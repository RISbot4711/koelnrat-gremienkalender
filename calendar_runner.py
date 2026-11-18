#!/usr/bin/env python3
"""Run the calendar generator with slower paging and network retries."""

import sys
import time
from urllib.error import HTTPError, URLError

import koeln_gremienkalender as calendar


# Be gentler to the OParl server between paginated requests.
calendar.PAGE_DELAY_SECONDS = 3.0

_original_fetch_page = calendar.fetch_page
_RETRY_DELAYS_SECONDS = (5, 15, 30)


def fetch_page_with_retries(url, expected_endpoint):
    """Retry temporary URL/timeout failures, but not HTTP/API errors."""
    for attempt in range(len(_RETRY_DELAYS_SECONDS) + 1):
        try:
            return _original_fetch_page(url, expected_endpoint)
        except RuntimeError as error:
            cause = error.__cause__

            # HTTP errors (including HTTP 429) have their own handling in
            # fetch_page and should not be retried here as connection errors.
            if isinstance(cause, HTTPError) or not isinstance(
                cause, (URLError, TimeoutError)
            ):
                raise

            if attempt >= len(_RETRY_DELAYS_SECONDS):
                raise

            delay = _RETRY_DELAYS_SECONDS[attempt]
            print(
                "Temporärer Netzwerkfehler. Wiederholung",
                str(attempt + 1) + "/" + str(len(_RETRY_DELAYS_SECONDS)),
                "in",
                str(delay) + " Sekunden:",
                str(cause),
                flush=True,
            )
            time.sleep(delay)


# fetch_collection resolves fetch_page from the imported module at runtime.
calendar.fetch_page = fetch_page_with_retries


if __name__ == "__main__":
    try:
        calendar.main()
    except KeyboardInterrupt:
        print("\nABBRUCH durch Benutzer.", file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        print("\nABBRUCH:", str(error), file=sys.stderr)
        sys.exit(1)
