
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Koelner Gremienkalender

Version 0.4

Teil 1:
- Sicherer OParl-Datenabruf
- Vollstaendiger Erstabruf
- Inkrementelle Aktualisierung
- Pagination
- Behandlung von HTTP 429
- Validierung der Serverantworten
"""

import json
import sys
import time
import os
import tempfile
import uuid

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import (
    urlsplit,
    parse_qsl,
    urlencode
)
from urllib.request import (
    Request,
    build_opener,
    HTTPRedirectHandler
)
from zoneinfo import ZoneInfo



# --------------------------------------------------
# 1. DATENQUELLEN
# --------------------------------------------------

BASE_URL = (
    "https://buergerinfo.stadt-koeln.de/oparl/"
    "bodies/stadtverwaltung_koeln"
)

ENDPOINTS = {
    "organizations": BASE_URL + "/organizations",
    "meetings": BASE_URL + "/meetings",
}


# --------------------------------------------------
# 2. EINSTELLUNGEN
# --------------------------------------------------

MAX_PAGES = 500
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_RECORDS = 100000

TIMEOUT_SECONDS = 20
PAGE_DELAY_SECONDS = 1.0

USER_AGENT = "KoelnGremienkalender/0.4"

LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")

STANDARD_MEETING_DURATION = timedelta(hours=2)

SYNC_OVERLAP = timedelta(minutes=5)

CACHE_VERSION = 1

CACHE_FILE = Path("meetings_cache.json")

OUTPUT_FILE = Path("Koelner_Gremienkalender.ics")


# --------------------------------------------------
# 3. WEITERLEITUNGEN VERBIETEN
# --------------------------------------------------

class NoRedirectHandler(HTTPRedirectHandler):

    def redirect_request(
        self, req, fp, code, msg, headers, newurl
    ):

        raise RuntimeError(
            "Unerwartete HTTP-Weiterleitung abgebrochen."
        )


OPENER = build_opener(NoRedirectHandler)


# --------------------------------------------------
# 4. ZEITSTEMPEL
# --------------------------------------------------

def utc_now():

    return datetime.now(timezone.utc)


def format_utc(value):

    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def parse_timestamp(value):

    if not isinstance(value, str):
        raise ValueError("Ungueltiger Zeitstempel.")

    result = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if result.tzinfo is None:
        raise ValueError("Zeitzone fehlt.")

    return result.astimezone(timezone.utc)



# --------------------------------------------------
# 5. URL-VALIDIERUNG
# --------------------------------------------------

def validate_url(url, expected_endpoint):

    if not isinstance(url, str):
        raise ValueError("Ungueltige URL.")

    if len(url) > 2048:
        raise ValueError("URL ist zu lang.")

    parsed = urlsplit(url)
    expected = urlsplit(expected_endpoint)

    if parsed.scheme != "https":
        raise ValueError("Nur HTTPS ist erlaubt.")

    if parsed.netloc != "buergerinfo.stadt-koeln.de":
        raise ValueError("Nicht erlaubter Server.")

    if parsed.path != expected.path:
        raise ValueError("Nicht erlaubter API-Pfad.")

    if parsed.fragment:
        raise ValueError("URL-Fragmente sind verboten.")

    if parsed.username or parsed.password:
        raise ValueError("Zugangsdaten sind verboten.")

    if parsed.query:

        parameters = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True
        )

    else:

        parameters = []

    seen = set()

    for key, value in parameters:

        if key not in ("page", "modified_since"):
            raise ValueError("Unerlaubter URL-Parameter.")

        if key in seen:
            raise ValueError("Doppelter URL-Parameter.")

        seen.add(key)

        if key == "page":

            if not value.isdigit() or int(value) < 1:
                raise ValueError("Ungueltige Seitennummer.")

        if key == "modified_since":

            parse_timestamp(value)

    return url


# --------------------------------------------------
# 6. EINE ERGEBNISSEITE ABRUFEN
# --------------------------------------------------

def fetch_page(url, expected_endpoint):

    validate_url(url, expected_endpoint)

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )

    raw_data = None

    for attempt in range(4):

        try:

            with OPENER.open(
                request,
                timeout=TIMEOUT_SECONDS
            ) as response:

                if response.status != 200:
                    raise RuntimeError("HTTP-Fehler.")

                content_type = response.headers.get(
                    "Content-Type", ""
                ).lower()

                if "json" not in content_type:

                    raise RuntimeError(
                        "Unerwartetes Datenformat."
                    )

                raw_data = response.read(
                    MAX_RESPONSE_BYTES + 1
                )

                if len(raw_data) > MAX_RESPONSE_BYTES:

                    raise RuntimeError(
                        "Antwort ist zu gross."
                    )

            break

        except HTTPError as error:

            if error.code != 429:

                error.close()

                raise RuntimeError(
                    "HTTP-Fehler: " + str(error.code)
                ) from error

            if attempt >= 3:

                error.close()

                raise RuntimeError(
                    "HTTP 429: Wiederholungen ausgeschoepft."
                ) from error

            retry_after = (
                error.headers.get("Retry-After")
                if error.headers else None
            )

            error.close()

            if retry_after is None:

                delay = 2 ** attempt

            else:

                try:

                    delay = float(retry_after)

                except ValueError:

                    try:

                        retry_date = parsedate_to_datetime(
                            retry_after
                        )

                        if retry_date.tzinfo is None:

                            retry_date = retry_date.replace(
                                tzinfo=timezone.utc
                            )

                        delay = (
                            retry_date - utc_now()
                        ).total_seconds()

                        delay = max(0, delay)

                    except (
                        TypeError,
                        ValueError,
                        OverflowError
                    ):

                        raise RuntimeError(
                            "Ungueltiger Retry-After-Header."
                        ) from error

            if not 0 <= delay <= 60:

                raise RuntimeError(
                    "Retry-After ausserhalb des Limits."
                )

            print(
                "HTTP 429. Warte",
                delay,
                "Sekunden."
            )

            time.sleep(delay)

        except (URLError, TimeoutError) as error:

            raise RuntimeError(
                "Datenabruf fehlgeschlagen."
            ) from error

    else:

        raise RuntimeError(
            "Datenabruf nach Wiederholungen fehlgeschlagen."
        )

    try:

        data = json.loads(
            raw_data.decode("utf-8")
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError
    ) as error:

        raise RuntimeError(
            "Ungueltiges JSON."
        ) from error

    if not isinstance(data, dict):
        raise RuntimeError("Ungueltige Datenstruktur.")

    records = data.get("data")

    if not isinstance(records, list):
        raise RuntimeError("Datensatzliste fehlt.")

    for record in records:

        if not isinstance(record, dict):
            raise RuntimeError("Ungueltiger Datensatz.")

        if not isinstance(record.get("id"), str):
            raise RuntimeError("Datensatz-ID fehlt.")

    pagination = data.get("pagination")

    if not isinstance(pagination, dict):
        raise RuntimeError("Seitendaten fehlen.")

    required = (
        "totalElements",
        "elementsPerPage",
        "currentPage",
        "totalPages",
    )

    for field in required:

        value = pagination.get(field)

        if type(value) is not int or value < 0:

            raise RuntimeError(
                "Ungueltige Seitenangabe: " + field
            )

    if pagination["elementsPerPage"] < 1:
        raise RuntimeError("Ungueltige Seitengroesse.")

    if pagination["currentPage"] < 1:
        raise RuntimeError("Ungueltige Seitennummer.")

    if pagination["currentPage"] > max(
        1, pagination["totalPages"]
    ):

        raise RuntimeError(
            "Seitennummer ausserhalb des Bereichs."
        )

    if len(records) > pagination["elementsPerPage"]:

        raise RuntimeError(
            "Zu viele Datensaetze auf einer Seite."
        )

    if pagination["totalPages"] == 0:

        if pagination["totalElements"] != 0 or records:
            raise RuntimeError("Ungueltige leere Liste.")

    links = data.get("links", {})

    if not isinstance(links, dict):
        raise RuntimeError("Ungueltige Navigationslinks.")

    next_url = links.get("next")

    if next_url is not None and not isinstance(next_url, str):

        raise RuntimeError(
            "Ungueltiger Folgeseitenlink."
        )

    return records, next_url, pagination


# --------------------------------------------------
# 7. GESAMTE ERGEBNISLISTE ABRUFEN
# --------------------------------------------------

def fetch_collection(
    name,
    endpoint,
    modified_since=None
):

    print()
    print("Lese Datenquelle:", name)

    if modified_since is None:

        current_url = endpoint

        print("Abrufmodus: Vollstaendig")

    else:

        current_url = (
            endpoint
            + "?"
            + urlencode({
                "modified_since": modified_since
            })
        )

        print("Abrufmodus: Nur Aenderungen")
        print("Seit:", modified_since)

    results = []
    visited_urls = set()
    seen_ids = set()

    expected_page = 1
    expected_total = None
    expected_pages = None

    for page_number in range(1, MAX_PAGES + 1):

        validate_url(current_url, endpoint)

        if current_url in visited_urls:

            raise RuntimeError(
                "Endlosschleife erkannt."
            )

        visited_urls.add(current_url)

        records, next_url, pagination = fetch_page(
            current_url,
            endpoint
        )

        if pagination["currentPage"] != expected_page:

            raise RuntimeError(
                "Falsche Seitennummer."
            )

        if expected_total is None:

            expected_total = pagination["totalElements"]
            expected_pages = pagination["totalPages"]

        if pagination["totalElements"] != expected_total:

            raise RuntimeError(
                "Gesamtzahl hat sich veraendert."
            )

        if pagination["totalPages"] != expected_pages:

            raise RuntimeError(
                "Seitenanzahl hat sich veraendert."
            )

        for record in records:

            record_id = record["id"]

            if record_id in seen_ids:

                raise RuntimeError(
                    "Doppelter Datensatz entdeckt."
                )

            seen_ids.add(record_id)

        results.extend(records)

        if len(results) > MAX_TOTAL_RECORDS:

            raise RuntimeError(
                "Datensatzlimit ueberschritten."
            )

        print(
            "Seite",
            expected_page,
            "von",
            expected_pages,
            "- Datensaetze:",
            len(records)
        )

        if next_url is None:

            if expected_page != max(1, expected_pages):

                raise RuntimeError(
                    "Ergebnisseiten fehlen."
                )

            if len(results) != expected_total:

                raise RuntimeError(
                    "Datensatzanzahl stimmt nicht."
                )

            return results

        if expected_page >= expected_pages:

            raise RuntimeError(
                "Unerwartete zusaetzliche Seite."
            )

        validate_url(next_url, endpoint)

        next_parameters = dict(
            parse_qsl(
                urlsplit(next_url).query,
                keep_blank_values=True,
                strict_parsing=True
            )
        )

        expected_parameters = {
            "page": str(expected_page + 1)
        }

        if modified_since is not None:

            expected_parameters["modified_since"] = (
                modified_since
            )

        if next_parameters != expected_parameters:

            raise RuntimeError(
                "Seitennavigation nicht fortlaufend."
            )

        current_url = next_url
        expected_page += 1

        time.sleep(PAGE_DELAY_SECONDS)

    raise RuntimeError(
        "Maximale Seitenanzahl ueberschritten."
    )



# --------------------------------------------------
# 8. CACHE LADEN
# --------------------------------------------------

def load_cache():

    if not CACHE_FILE.exists():

        print()
        print("Kein lokaler Cache vorhanden.")
        print("Vollstaendiger Erstabruf erforderlich.")

        return None

    print()
    print("Lade lokalen Cache:", CACHE_FILE)

    try:

        with CACHE_FILE.open(
            "r",
            encoding="utf-8"
        ) as file:

            cache = json.load(file)

    except (
        OSError,
        json.JSONDecodeError
    ) as error:

        raise RuntimeError(
            "Cache konnte nicht gelesen werden. "
            "Die vorhandene Datei bleibt unveraendert."
        ) from error

    if not isinstance(cache, dict):
        raise RuntimeError("Ungueltige Cache-Struktur.")

    if cache.get("version") != CACHE_VERSION:

        raise RuntimeError(
            "Unbekannte Cache-Version."
        )

    if not isinstance(cache.get("last_sync_utc"), str):

        raise RuntimeError(
            "Letzter Synchronisationszeitpunkt fehlt."
        )

    parse_timestamp(cache["last_sync_utc"])

    for collection_name in (
        "organizations",
        "meetings"
    ):

        collection = cache.get(collection_name)

        if not isinstance(collection, dict):

            raise RuntimeError(
                "Cache-Bestand fehlt: "
                + collection_name
            )

        for record_id, record in collection.items():

            if (
                not isinstance(record_id, str)
                or not isinstance(record, dict)
                or record.get("id") != record_id
            ):

                raise RuntimeError(
                    "Ungueltiger Cache-Datensatz."
                )

    return cache


# --------------------------------------------------
# 9. TOMBSTONES ERKENNEN
# --------------------------------------------------

def is_deleted_record(record):

    if not isinstance(record, dict):
        return False

    if record.get("deleted") is True:
        return True

    if record.get("type") == "https://schema.oparl.org/1.1/Deleted":

        return True

    return False



# --------------------------------------------------
# 10. AENDERUNGEN AUF DEN CACHE ANWENDEN
# --------------------------------------------------

def apply_changes(
    existing_records,
    changed_records,
    preserve_deleted=False
):

    updated = dict(existing_records)

    added = 0
    modified = 0
    deleted = 0

    for record in changed_records:

        record_id = record.get("id")

        if not isinstance(record_id, str):

            raise RuntimeError(
                "Aenderungsdatensatz ohne ID."
            )

        old_record = updated.get(record_id)

        if is_deleted_record(record):

            if preserve_deleted:

                old_sequence = 0

                if old_record is not None:

                    old_sequence = old_record.get(
                        "_calendar_sequence",
                        0
                    )

                    if (
                        type(old_sequence) is not int
                        or old_sequence < 0
                    ):

                        raise RuntimeError(
                            "Ungueltige Kalender-Versionsnummer."
                        )

                    old_comparison = {
                        key: value
                        for key, value in old_record.items()
                        if key != "_calendar_sequence"
                    }

                    if old_comparison == record:

                        continue

                tombstone = dict(record)

                tombstone["_calendar_sequence"] = (
                    old_sequence + 1
                    if old_record is not None
                    else 0
                )

                updated[record_id] = tombstone

            else:

                if record_id in updated:

                    del updated[record_id]

            deleted += 1
            continue

        if old_record is None:

            new_record = dict(record)
            new_record["_calendar_sequence"] = 0

            updated[record_id] = new_record

            added += 1
            continue

        old_sequence = old_record.get(
            "_calendar_sequence",
            0
        )

        if (
            type(old_sequence) is not int
            or old_sequence < 0
        ):

            raise RuntimeError(
                "Ungueltige Kalender-Versionsnummer."
            )

        old_comparison = {
            key: value
            for key, value in old_record.items()
            if key != "_calendar_sequence"
        }

        new_comparison = dict(record)

        if old_comparison == new_comparison:
            continue

        new_record = dict(record)

        new_record["_calendar_sequence"] = (
            old_sequence + 1
        )

        updated[record_id] = new_record

        modified += 1

    return (
        updated,
        added,
        modified,
        deleted
    )


# --------------------------------------------------
# 11. VOLLSTAENDIGEN CACHE AUFBAUEN
# --------------------------------------------------

def create_initial_cache():

    print()
    print("Starte erstmaligen vollstaendigen Abruf.")
    print("--------------------------------")

    # Synchronisationszeitpunkt vor dem Abruf festlegen.
    # Dadurch gehen Aenderungen waehrend des Abrufs
    # bei der naechsten Aktualisierung nicht verloren.

    initial_sync_time = utc_now()

    organizations = fetch_collection(
        "organizations",
        ENDPOINTS["organizations"]
    )

    meetings = fetch_collection(
        "meetings",
        ENDPOINTS["meetings"]
    )

    organization_data = {
        record["id"]: record
        for record in organizations
        if not is_deleted_record(record)
    }

    meeting_data = {}

    for record in meetings:

        if is_deleted_record(record):
            continue

        meeting_record = dict(record)

        meeting_record["_calendar_sequence"] = 0

        meeting_data[record["id"]] = meeting_record

    cache = {
        "version": CACHE_VERSION,
        "last_sync_utc": format_utc(initial_sync_time),
        "organizations": organization_data,
        "meetings": meeting_data,
    }

    print("--------------------------------")
    print("Erstabruf abgeschlossen.")
    print("Organisationen:", len(organization_data))
    print("Sitzungen:", len(meeting_data))

    return cache



# --------------------------------------------------
# 12. INKREMENTELLE AKTUALISIERUNG
# --------------------------------------------------

def update_cache(cache):

    print()
    print("Starte inkrementelle Aktualisierung.")
    print("--------------------------------")

    previous_sync = parse_timestamp(
        cache["last_sync_utc"]
    )

    modified_since = format_utc(
        previous_sync - SYNC_OVERLAP
    )

    # Zeitpunkt vor dem Abruf festlegen.

    new_sync_time = utc_now()

    organization_changes = fetch_collection(
        "organizations",
        ENDPOINTS["organizations"],
        modified_since
    )

    meeting_changes = fetch_collection(
        "meetings",
        ENDPOINTS["meetings"],
        modified_since
    )

    (
        updated_organizations,
        org_added,
        org_modified,
        org_deleted
    ) = apply_changes(
        cache["organizations"],
        organization_changes
    )

    (
        updated_meetings,
        meeting_added,
        meeting_modified,
        meeting_deleted
    ) = apply_changes(
        cache["meetings"],
        meeting_changes,
        preserve_deleted=True
    )

    updated_cache = {
        "version": CACHE_VERSION,
        "last_sync_utc": format_utc(new_sync_time),
        "organizations": updated_organizations,
        "meetings": updated_meetings,
    }

    print("--------------------------------")
    print("Aktualisierung abgeschlossen.")
    print()
    print("Organisationen:")
    print("  Neu:", org_added)
    print("  Geaendert:", org_modified)
    print("  Geloescht:", org_deleted)
    print()
    print("Sitzungen:")
    print("  Neu:", meeting_added)
    print("  Geaendert:", meeting_modified)
    print("  Geloescht:", meeting_deleted)

    return updated_cache



# --------------------------------------------------
# 13. CACHE SICHER SPEICHERN
# --------------------------------------------------

def save_cache(cache):

    cache_path = CACHE_FILE.resolve()

    temporary_path = None

    try:

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=cache_path.parent,
            prefix=".meetings_cache_",
            suffix=".tmp",
            delete=False
        ) as temporary_file:

            temporary_path = Path(
                temporary_file.name
            )

            json.dump(
                cache,
                temporary_file,
                ensure_ascii=False,
                separators=(",", ":")
            )

            temporary_file.write("\n")

            temporary_file.flush()

            os.fsync(
                temporary_file.fileno()
            )

        os.replace(
            temporary_path,
            cache_path
        )

        print()
        print("Cache erfolgreich gespeichert.")

    except OSError as error:

        if temporary_path is not None:

            try:

                temporary_path.unlink(
                    missing_ok=True
                )

            except OSError:
                pass

        raise RuntimeError(
            "Cache konnte nicht sicher gespeichert werden."
        ) from error




# --------------------------------------------------
# 14. CACHE AKTUALISIEREN ODER ERSTELLEN
# --------------------------------------------------

def synchronize_data():

    cache = create_initial_cache()

    return cache



# --------------------------------------------------
# 15. ICALENDAR-TEXT SICHER AUFBEREITEN
# --------------------------------------------------

def escape_ical_text(value):

    if value is None:
        return ""

    value = str(value)

    value = value.replace("\\", "\\\\")
    value = value.replace(";", "\\;")
    value = value.replace(",", "\\,")
    value = value.replace("\r\n", "\\n")
    value = value.replace("\n", "\\n")
    value = value.replace("\r", "\\n")

    return value



# --------------------------------------------------
# 16. ICALENDAR-TEXT UND ZEILEN UMBRECHEN
# --------------------------------------------------

def escape_ical_text(value):

    if not isinstance(value, str):
        value = str(value)

    # Zeilenumbrueche vereinheitlichen

    value = value.replace("\r\n", "\n")
    value = value.replace("\r", "\n")

    # Nicht erlaubte Steuerzeichen entfernen

    value = "".join(
        character
        for character in value
        if character == "\n"
        or ord(character) >= 32
        and ord(character) != 127
    )

    # Sonderzeichen nach ICS-Regeln maskieren

    value = value.replace("\\", "\\\\")
    value = value.replace("\n", "\\n")
    value = value.replace(";", "\\;")
    value = value.replace(",", "\\,")

    return value


def fold_ical_line(line):

    encoded = line.encode("utf-8")

    result = []
    first_line = True

    while encoded:

        limit = 75 if first_line else 74

        chunk = encoded[:limit]

        if len(encoded) > limit:

            while (
                chunk
                and len(chunk) < len(encoded)
                and (encoded[len(chunk)] & 0xC0) == 0x80
            ):

                chunk = chunk[:-1]

        if not chunk:

            raise RuntimeError(
                "ICS-Zeilenumbruch fehlgeschlagen."
            )

        result.append(
            chunk.decode("utf-8")
        )

        encoded = encoded[len(chunk):]

        first_line = False

    if not result:
        return [""]

    return [
        result[0]
        if index == 0
        else " " + part

        for index, part in enumerate(result)
    ]


# --------------------------------------------------
# 17. ORGANISATIONSNAMEN ERMITTELN
# --------------------------------------------------

def get_organization_names(
    meeting,
    organizations
):

    names = []

    organization_ids = meeting.get(
        "organization",
        []
    )

    if not isinstance(organization_ids, list):
        return names

    for organization_id in organization_ids:

        if not isinstance(organization_id, str):
            continue

        organization = organizations.get(
            organization_id
        )

        if not isinstance(organization, dict):
            continue

        name = organization.get("name")

        if isinstance(name, str) and name.strip():

            names.append(name.strip())

    return names



# --------------------------------------------------
# 18. SITZUNGSORT ERMITTELN
# --------------------------------------------------

def get_meeting_location(meeting):

    parts = []

    # Ausschliesslich standardisierte OParl-Ortsangaben

    location = meeting.get("location")

    if isinstance(location, dict):

        for field in (
            "name",
            "description",
            "room",
            "streetAddress",
            "postalCode",
            "locality",
            "subLocality"
        ):

            value = location.get(field)

            if isinstance(value, str) and value.strip():

                value = value.strip()

                if value not in parts:
                    parts.append(value)

    return ", ".join(parts)



# --------------------------------------------------
# 19. SITZUNGSDATEN AUFBEREITEN
# --------------------------------------------------

def prepare_meeting(
    meeting,
    organizations,
    today_utc
):

    meeting_id = meeting.get("id")

    if not isinstance(meeting_id, str):
        return None, "invalid"

    if is_deleted_record(meeting):
        return None, "deleted"

    if meeting.get("cancelled") is True:
        return None, "cancelled"

    try:

        start = parse_timestamp(
            meeting.get("start")
        )

    except (
        ValueError,
        TypeError,
        OverflowError
    ):

        return None, "invalid"

    if start < today_utc:
        return None, "past"

    used_standard_duration = False

    try:

        end = parse_timestamp(
            meeting.get("end")
        )

        if end <= start:

            raise ValueError(
                "Ende liegt vor Beginn."
            )

    except (
        ValueError,
        TypeError,
        OverflowError
    ):

        end = start + STANDARD_MEETING_DURATION

        used_standard_duration = True

    name = meeting.get("name")

    if not isinstance(name, str) or not name.strip():

        name = "Sitzung eines Kölner Gremiums"

    else:

        name = name.strip()

    organization_names = get_organization_names(
        meeting,
        organizations
    )

    location = get_meeting_location(meeting)

    description_parts = []

    if organization_names:

        description_parts.append(
            "Gremium: " + ", ".join(organization_names)
        )

    if used_standard_duration:

        description_parts.append(
            "Hinweis: Die tatsächliche Sitzungsdauer "
            "kann abweichen."
        )

    description = "\n\n".join(description_parts)

    stable_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        meeting_id
    )

    uid = (
        str(stable_id)
        + "@koeln-gremienkalender"
    )

    sequence = meeting.get(
        "_calendar_sequence",
        0
    )

    if type(sequence) is not int or sequence < 0:

        raise RuntimeError(
            "Ungueltige Kalender-Versionsnummer."
        )

    event = {
        "uid": uid,
        "name": name,
        "start": start,
        "end": end,
        "location": location,
        "description": description,
        "url": meeting_id,
        "standard_duration": used_standard_duration,
        "sequence": sequence,
    }

    modified = meeting.get("modified")

    if isinstance(modified, str):

        try:

            event["last_modified"] = parse_timestamp(
                modified
            )

        except (
            ValueError,
            TypeError,
            OverflowError
        ):

            pass

    return event, "valid"



# --------------------------------------------------
# 20. ICS-TERMIN ERZEUGEN
# --------------------------------------------------

def create_ical_event(event, timestamp):

    lines = [
        "BEGIN:VEVENT",
        "UID:" + escape_ical_text(event["uid"]),
        "DTSTAMP:" + timestamp.strftime(
            "%Y%m%dT%H%M%SZ"
        ),
        "DTSTART:" + event["start"].strftime(
            "%Y%m%dT%H%M%SZ"
        ),
        "DTEND:" + event["end"].strftime(
            "%Y%m%dT%H%M%SZ"
        ),
        "SUMMARY:" + escape_ical_text(event["name"]),
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
    ]

    if event.get("location"):

        lines.append(
            "LOCATION:"
            + escape_ical_text(event["location"])
        )

    if event.get("description"):

        lines.append(
            "DESCRIPTION:"
            + escape_ical_text(event["description"])
        )

    if event.get("url"):

        lines.append(
            "URL:" + event["url"]
        )

    last_modified = event.get("last_modified")

    if last_modified is None:
        last_modified = timestamp

    lines.append(
        "LAST-MODIFIED:"
        + last_modified.strftime(
            "%Y%m%dT%H%M%SZ"
        )
    )

    sequence = event.get("sequence", 0)

    if type(sequence) is not int or sequence < 0:

        raise RuntimeError(
            "Ungueltige ICS-Versionsnummer."
        )

    lines.append(
        "SEQUENCE:" + str(sequence)
    )

    lines.append("END:VEVENT")

    return lines



# --------------------------------------------------
# 21. GESAMTE ICS-DATEI ERZEUGEN
# --------------------------------------------------

def generate_calendar(cache):

    print()
    print("Erzeuge Kalenderdatei")
    print("--------------------------------")

    now = utc_now()

    local_today = datetime.now(
        LOCAL_TIMEZONE
    ).date()

    today_local_midnight = datetime.combine(
        local_today,
        datetime.min.time(),
        tzinfo=LOCAL_TIMEZONE
    )

    today_utc = today_local_midnight.astimezone(
        timezone.utc
    )

    organizations = cache["organizations"]
    meetings = cache["meetings"]

    events = []

    cancelled_count = 0
    deleted_count = 0
    past_count = 0
    invalid_count = 0
    standard_duration_count = 0

    for meeting in meetings.values():

        event, status = prepare_meeting(
            meeting,
            organizations,
            today_utc
        )

        if status == "deleted":

            deleted_count += 1
            continue

        if status == "cancelled":

            cancelled_count += 1
            continue

        if status == "past":

            past_count += 1
            continue

        if status == "invalid":

            invalid_count += 1
            continue

        if event is None:
            continue

        if event["standard_duration"]:

            standard_duration_count += 1

        events.append(event)

    events.sort(
        key=lambda item: (
            item["start"],
            item["name"]
        )
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Koelner Gremienkalender//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Kölner Gremienkalender",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]

    for event in events:

        lines.extend(
            create_ical_event(
                event,
                now
            )
        )

    lines.append("END:VCALENDAR")

    folded_lines = []

    for line in lines:

        folded_lines.extend(
            fold_ical_line(line)
        )

    ics_content = (
        "\r\n".join(folded_lines)
        + "\r\n"
    )

    output_path = OUTPUT_FILE.resolve()

    temporary_path = None

    try:

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=output_path.parent,
            prefix=".koeln_calendar_",
            suffix=".tmp",
            delete=False
        ) as temporary_file:

            temporary_path = Path(
                temporary_file.name
            )

            temporary_file.write(
                ics_content
            )

            temporary_file.flush()

            os.fsync(
                temporary_file.fileno()
            )

        os.replace(
            temporary_path,
            output_path
        )

    except OSError as error:

        if temporary_path is not None:

            try:

                temporary_path.unlink(
                    missing_ok=True
                )

            except OSError:
                pass

        raise RuntimeError(
            "ICS-Datei konnte nicht sicher erstellt werden."
        ) from error

    print()
    print("Uebernommene Sitzungen:", len(events))
    print("Abgesagte Sitzungen:", cancelled_count)
    print("Geloeschte Sitzungen:", deleted_count)
    print("Vergangene Sitzungen:", past_count)
    print("Ungueltiger Sitzungsbeginn:", invalid_count)

    print(
        "Verwendete 2-Stunden-Standarddauer:",
        standard_duration_count
    )

    print("--------------------------------")
    print("ICS-Datei erfolgreich erstellt:")
    print(output_path)
    print("--------------------------------")

    return len(events)


# --------------------------------------------------
# 22. HAUPTPROGRAMM
# --------------------------------------------------

def main():

    print()
    print("Kölner Gremienkalender")
    print("Version 0.4")
    print("--------------------------------")

    cache = synchronize_data()

    print()
    print("Datenbestand erfolgreich synchronisiert.")

    print(
        "Organisationen:",
        len(cache["organizations"])
    )

    print(
        "Sitzungen im Cache:",
        len(cache["meetings"])
    )

    # Zuerst ICS-Datei erfolgreich erzeugen.

    generate_calendar(cache)

    # Erst danach den Cache dauerhaft speichern.

    save_cache(cache)

    print()
    print("--------------------------------")
    print("Verarbeitung erfolgreich abgeschlossen.")
    print("Kalendereintraege wurden erstellt.")
    print("Cache wurde gespeichert.")
    print("Keine automatische Veroeffentlichung.")
    print("--------------------------------")


# --------------------------------------------------
# 23. PROGRAMMSTART
# --------------------------------------------------

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\nABBRUCH durch Benutzer.",
            file=sys.stderr
        )

        sys.exit(1)

    except Exception as error:

        print(
            "\nABBRUCH:",
            str(error),
            file=sys.stderr
        )

        sys.exit(1)


