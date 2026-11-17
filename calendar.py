
#!/usr/bin/env python3
"""
Kölner Gremienkalender
Erste Entwicklungsstufe: Sicherer, lesender OParl-Datenabruf.

Es werden keine Dateien veröffentlicht und keine Daten verändert.
"""

import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qsl
from urllib.request import (
    Request,
    build_opener,
    HTTPRedirectHandler,
)

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
# 3. Weiterleitungen grundsätzlich verbieten
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
    """Erlaubt nur den festgelegten Server und API-Pfad."""

    if not isinstance(url, str):
        raise ValueError("Ungültige URL.")

    if len(url) > 2048:
        raise ValueError("URL ist zu lang.")

    try:
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

        # Nur die Seitennavigation mit page=Zahl erlauben.
        parameters = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True
        )

        if parameters:
            if len(parameters) != 1:
                raise ValueError(
                    "Unerlaubte URL-Parameter."
                )

            key, value = parameters[0]

            if key != "page":
                raise ValueError(
                    "Nur der Parameter page ist erlaubt."
                )

            if not value.isdigit() or int(value) < 1:
                raise ValueError(
                    "Ungültige Seitennummer."
                )

    except ValueError as error:
        raise ValueError(
            "URL-Prüfung fehlgeschlagen: " + str(error)
        ) from error

    return url



# --------------------------------------------------
# 5. Eine Ergebnisseite sicher abrufen
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
                raise RuntimeError(
                    "Datenquelle lieferte keinen HTTP-Status 200."
                )

            content_type = response.headers.get(
                "Content-Type", ""
            ).lower()

            if not (
                "json" in content_type
                or content_type == ""
            ):
                raise RuntimeError(
                    "Unerwartetes Datenformat."
                )

            content_length = response.headers.get(
                "Content-Length"
            )

            if content_length:
                try:
                    if int(content_length) > MAX_RESPONSE_BYTES:
                        raise RuntimeError(
                            "Antwort überschreitet Größenlimit."
                        )
                except ValueError:
                    raise RuntimeError(
                        "Ungültige Größenangabe."
                    )

            raw_data = response.read(
                MAX_RESPONSE_BYTES + 1
            )

            if len(raw_data) > MAX_RESPONSE_BYTES:
                raise RuntimeError(
                    "Antwort überschreitet Größenlimit."
                )

    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(
            "Abruf der Datenquelle fehlgeschlagen."
        ) from error

    try:
        data = json.loads(raw_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "Datenquelle lieferte kein gültiges UTF-8-JSON."
        ) from error

    if not isinstance(data, dict):
        raise RuntimeError(
            "Unerwartete Datenstruktur."
        )

    records = data.get("data")

    if not isinstance(records, list):
        raise RuntimeError(
            "Die erwartete Datensatzliste fehlt."
        )

    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError(
                "Ungültiger Datensatz entdeckt."
            )

        if not isinstance(record.get("id"), str):
            raise RuntimeError(
                "Datensatz ohne gültige ID entdeckt."
            )

    links = data.get("links", {})

    if not isinstance(links, dict):
        raise RuntimeError(
            "Ungültige Navigationsinformationen."
        )

    next_url = links.get("next")

    if next_url is not None and not isinstance(next_url, str):
        raise RuntimeError(
            "Ungültiger Verweis auf die nächste Ergebnisseite."
        )

    return records, next_url


# --------------------------------------------------
# 6. Alle Ergebnisseiten eines Endpunkts abrufen
# --------------------------------------------------

def fetch_collection(name, endpoint):

    print("Lese Datenquelle:", name)

    results = []
    current_url = endpoint
    visited_urls = set()

    for page_number in range(1, MAX_PAGES + 1):

        validate_url(current_url, endpoint)

        if current_url in visited_urls:
            raise RuntimeError(
                "Endlosschleife bei der Seitennavigation erkannt."
            )

        visited_urls.add(current_url)

        records, next_url = fetch_page(
            current_url, endpoint
        )

        results.extend(records)

        if len(results) > MAX_TOTAL_RECORDS:
            raise RuntimeError(
                "Maximale Datensatzanzahl überschritten."
            )

        print(
            "  Seite", page_number,
            "- Datensätze:", len(records)
        )

        if next_url is None:
            return results

        current_url = next_url

        time.sleep(0.2)

    raise RuntimeError(
        "Maximale Seitenanzahl überschritten."
    )


# --------------------------------------------------
# 7. Hauptprogramm
# --------------------------------------------------

def main():

    print("Kölner Gremienkalender")
    print("Sicherer OParl-Datenabruf")
    print("--------------------------------")

    collections = {}

    for name, endpoint in ENDPOINTS.items():

        collections[name] = fetch_collection(
            name, endpoint
        )

    print("--------------------------------")
    print("Abruf erfolgreich abgeschlossen.")
    print()

    for name, records in collections.items():
        print(
            name + ":",
            len(records),
            "Datensätze"
        )

    print()
    print("Es wurden keine Dateien veröffentlicht.")
    print("Es wurden keine Daten verändert.")


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
