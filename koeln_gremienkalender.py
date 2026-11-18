
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Koelner Gremienkalender
Version 0.5

- Direkter OParl-Datenabruf der Stadt Koeln
- Vollstaendiger Abruf von Organisationen und Sitzungen
- Pagination
- Behandlung von HTTP 429
- Validierung der Serverantworten
- Keine Mandari-Abhaengigkeiten
- Kein lokaler Cache
- Erzeugung einer abonnierbaren ICS-Datei
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
from urllib.parse import urlsplit, parse_qsl
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

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_RECORDS = 100000

TIMEOUT_SECONDS = 20
PAGE_DELAY_SECONDS = 1.0

USER_AGENT = "KoelnGremienkalender/0.5"

LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")

STANDARD_MEETING_DURATION = timedelta(hours=2)

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

    parameters = parse_qsl(
        parsed.query,
        keep_blank_values=True,
        strict_parsing=True
    ) if parsed.query else []

    seen = set()

    for key, value in parameters:

        if key != "page":
            raise ValueError("Unerlaubter URL-Parameter.")

        if key in seen:
            raise ValueError("Doppelter URL-Parameter.")

        seen.add(key)

        if not value.isdigit() or int(value) < 1:
            raise ValueError("Ungueltige Seitennummer.")

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

    links = data.get("links", {})

    if not isinstance(links, dict):
        raise RuntimeError("Ungueltige Navigationslinks.")

    next_url = links.get("next")

    if next_url is not None and not isinstance(next_url, str):
        raise RuntimeError("Ungueltiger Folgeseitenlink.")

    return records, next_url



# --------------------------------------------------
# 7. GESAMTE ERGEBNISLISTE ABRUFEN
# --------------------------------------------------

def fetch_collection(name, endpoint):

    print()
    print("Lese Datenquelle:", name)
    print("Abrufmodus: Vollstaendig")

    current_url = endpoint

    results = []
    visited_urls = set()
    seen_ids = set()

    page_number = 1

    while True:

        validate_url(current_url, endpoint)

        if current_url in visited_urls:

            raise RuntimeError(
                "Endlosschleife erkannt."
            )

        visited_urls.add(current_url)

        records, next_url, _pagination = fetch_page(
            current_url,
            endpoint
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
            page_number,
            "- Datensaetze:",
            len(records),
            "- Gesamt:",
            len(results)
        )

        # Keine weitere Seite vorhanden
        if not next_url:

            print("Keine weitere Seite vorhanden.")
            break

        # Folgeseitenlink pruefen
        validate_url(next_url, endpoint)

        current_url = next_url
        page_number += 1

        time.sleep(PAGE_DELAY_SECONDS)

    return results


# --------------------------------------------------
# 8. GELOESCHTE DATENSAETZE ERKENNEN
# --------------------------------------------------



# --------------------------------------------------
# 8. GELOESCHTE DATENSAETZE ERKENNEN
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
# 9. DATEN VOLLSTAENDIG ABRUFEN
# --------------------------------------------------

def synchronize_data():

    print()
    print("Starte vollstaendigen OParl-Datenabruf.")
    print("--------------------------------")

    organizations = fetch_collection(
        "Organisationen",
        ENDPOINTS["organizations"]
    )

    meetings = fetch_collection(
        "Sitzungen",
        ENDPOINTS["meetings"]
    )

    organization_data = {
        record["id"]: record
        for record in organizations
        if not is_deleted_record(record)
    }

    meeting_data = {
        record["id"]: record
        for record in meetings
        if not is_deleted_record(record)
    }

    print("--------------------------------")
    print("Datenabruf abgeschlossen.")
    print("Organisationen:", len(organization_data))
    print("Sitzungen:", len(meeting_data))

    return {
        "organizations": organization_data,
        "meetings": meeting_data,
    }

# --------------------------------------------------
# 10. ICALENDAR-TEXT SICHER AUFBEREITEN
# --------------------------------------------------

def escape_ical_text(value):

    if not isinstance(value, str):
        value = str(value)

    value = value.replace("\r\n", "\n")
    value = value.replace("\r", "\n")

    value = "".join(
        character
        for character in value
        if character == "\n"
        or ord(character) >= 32
        and ord(character) != 127
    )

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

        while (
            len(chunk) < len(encoded)
            and chunk
            and (encoded[len(chunk)] & 0xC0) == 0x80
        ):

            chunk = chunk[:-1]

        if not chunk:

            raise RuntimeError(
                "ICS-Zeilenumbruch fehlgeschlagen."
            )

        result.append(chunk.decode("utf-8"))

        encoded = encoded[len(chunk):]
        first_line = False

    if not result:
        return [""]

    return [
        result[0] if index == 0 else " " + part
        for index, part in enumerate(result)
    ]


# --------------------------------------------------
# 11. ORGANISATIONSNAMEN ERMITTELN
# --------------------------------------------------

def get_organization_names(meeting, organizations):

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
# 12. SITZUNGSORT ERMITTELN
# --------------------------------------------------

def get_meeting_location(meeting):

    parts = []

    location = meeting.get("location")

    if isinstance(location, dict):

        for field in (
            "name",
            "description",
            "room"
        ):

            value = location.get(field)

            if isinstance(value, str) and value.strip():

                value = value.strip()

                if value not in parts:
                    parts.append(value)

    return ", ".join(parts)


# --------------------------------------------------
# 13. SITZUNGSDATEN AUFBEREITEN
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

    event = {
        "uid": uid,
        "name": name,
        "start": start,
        "end": end,
        "location": location,
        "description": description,
        "standard_duration": used_standard_duration,
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
# 14. ICS-TERMIN ERZEUGEN
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

    last_modified = event.get("last_modified")

    if last_modified is None:
        last_modified = timestamp

    lines.append(
        "LAST-MODIFIED:"
        + last_modified.strftime(
            "%Y%m%dT%H%M%SZ"
        )
    )

    lines.append("SEQUENCE:0")
    lines.append("END:VEVENT")

    return lines

# --------------------------------------------------
# 15. GESAMTE ICS-DATEI ERZEUGEN
# --------------------------------------------------

def generate_calendar(data):

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

    organizations = data["organizations"]
    meetings = data["meetings"]

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

            temporary_file.write(ics_content)
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
# 16. HAUPTPROGRAMM
# --------------------------------------------------

def main():

    print()
    print("Kölner Gremienkalender")
    print("Version 0.5")
    print("--------------------------------")

    data = synchronize_data()

    print()
    print("Datenbestand erfolgreich abgerufen.")

    print(
        "Organisationen:",
        len(data["organizations"])
    )

    print(
        "Sitzungen:",
        len(data["meetings"])
    )

    generate_calendar(data)

    print()
    print("--------------------------------")
    print("Verarbeitung erfolgreich abgeschlossen.")
    print("Kalendereintraege wurden erstellt.")
    print("Kein Cache verwendet.")
    print("Keine automatische Veroeffentlichung.")
    print("--------------------------------")


# --------------------------------------------------
# 17. PROGRAMMSTART
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
