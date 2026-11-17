
#!/usr/bin/env python3
"""
Koelner Gremienkalender

Entwicklungsstufe 1:
Sicherer, ausschliesslich lesender OParl-Datenabruf.

Keine Kalenderdateien werden erzeugt oder veroeffentlicht.
"""

import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qsl
from urllib.request import Request, build_opener, HTTPRedirectHandler


# --------------------------------------------------
# 1. Festgelegte Datenquellen
# --------------------------------------------------

BASE_URL = (
    "https://oparl.mandari.de/oparl/v1/body/"
    "277544fa-c7eb-598a-b8fa-458b979ccbec"
)

ENDPOINTS = {
    "organizations": BASE_URL + "/organizations",
    "meetings": BASE_URL + "/meetings",
    "papers": BASE_URL + "/papers",
}


# --------------------------------------------------
# 2. Sicherheitsgrenzen
# --------------------------------------------------

MAX_PAGES = 500
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
TIMEOUT_SECONDS = 20
MAX_TOTAL_RECORDS = 100000

USER_AGENT = "KoelnGremienkalender/0.1"


# --------------------------------------------------
# 3. Weiterleitungen verbieten
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
# 4. URLs kontrollieren
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

    if parsed.netloc != "oparl.mandari.de":
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
    )

    if parameters:
        if len(parameters) != 1:
            raise ValueError("Unerlaubte URL-Parameter.")

        key, value = parameters[0]

        if key != "page":
            raise ValueError("Nur page ist erlaubt.")

        if not value.isdigit() or int(value) < 1:
            raise ValueError("Ungueltige Seitennummer.")

    return url


# --------------------------------------------------
# 5. Eine Ergebnisseite abrufen
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

    try:

        with OPENER.open(
            request, timeout=TIMEOUT_SECONDS
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

    except (HTTPError, URLError, TimeoutError) as error:

        raise RuntimeError(
            "Datenabruf fehlgeschlagen."
        ) from error

    try:

        data = json.loads(raw_data.decode("utf-8"))

    except (UnicodeDecodeError, json.JSONDecodeError) as error:

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

    if pagination["totalPages"] < 1:
        raise RuntimeError("Ungueltige Seitenanzahl.")

    if pagination["currentPage"] > pagination["totalPages"]:
        raise RuntimeError("Seitennummer ausserhalb des Bereichs.")

    if len(records) > pagination["elementsPerPage"]:
        raise RuntimeError("Zu viele Datensaetze auf einer Seite.")

    links = data.get("links", {})

    if not isinstance(links, dict):
        raise RuntimeError("Ungueltige Navigationslinks.")

    next_url = links.get("next")

    if next_url is not None and not isinstance(next_url, str):
        raise RuntimeError("Ungueltiger Folgeseitenlink.")

    return records, next_url, pagination


# --------------------------------------------------
# 6. Alle Ergebnisseiten abrufen
# --------------------------------------------------

def fetch_collection(name, endpoint):

    print("Lese Datenquelle:", name)

    results = []
    visited_urls = set()
    seen_ids = set()

    current_url = endpoint
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
            current_url, endpoint
        )

        if pagination["currentPage"] != expected_page:
            raise RuntimeError("Falsche Seitennummer.")

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
            "  Seite",
            expected_page,
            "von",
            expected_pages,
            "- Datensaetze:",
            len(records)
        )

        if next_url is None:

            if expected_page != expected_pages:
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

        next_parameters = parse_qsl(
            urlsplit(next_url).query,
            keep_blank_values=True,
            strict_parsing=True
        )

        if next_parameters != [
            ("page", str(expected_page + 1))
        ]:
            raise RuntimeError(
                "Seitennavigation nicht fortlaufend."
            )

        current_url = next_url
        expected_page += 1

        time.sleep(0.2)

    raise RuntimeError(
        "Maximale Seitenanzahl ueberschritten."
    )


# --------------------------------------------------
# 7. Hauptprogramm
# --------------------------------------------------

def main():

    print("Koelner Gremienkalender")
    print("Sicherer OParl-Datenabruf")
    print("--------------------------------")

    collections = {}

    for name, endpoint in ENDPOINTS.items():

        collections[name] = fetch_collection(
            name, endpoint
        )

    print("--------------------------------")
    print("Abruf erfolgreich abgeschlossen.")

    for name, records in collections.items():

        print(
            name + ":",
            len(records),
            "Datensaetze"
        )

    print()
    print("Keine Dateien veroeffentlicht.")
    print("Keine Daten veraendert.")


if __name__ == "__main__":

    try:
        main()

    except Exception as error:

        print(
            "ABBRUCH:",
            str(error),
            file=sys.stderr
        )

        sys.exit(1)
