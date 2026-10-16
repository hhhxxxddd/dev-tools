"""Application readiness uses direct HTTP and an optional JSON subset contract."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

MAX_READINESS_BYTES = 65536
READINESS_TIMEOUT = 1


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def _matches(expected, actual) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _matches(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(_matches(left, right) for left, right in zip(expected, actual, strict=True))
        )
    return type(expected) is type(actual) and expected == actual


def http_ready(checks: dict) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    deadline = time.monotonic() + READINESS_TIMEOUT
    try:
        with opener.open(checks["http"], timeout=READINESS_TIMEOUT) as response:
            if not 200 <= response.status < 300:
                return False
            if "json" not in checks:
                return True
            content = bytearray()
            while len(content) <= MAX_READINESS_BYTES and time.monotonic() < deadline:
                # read1 performs at most one buffered read, so a trickling body
                # cannot keep a full-size read alive indefinitely.
                block = response.read1(min(8192, MAX_READINESS_BYTES + 1 - len(content)))
                if not block:
                    return _matches(checks["json"], json.loads(content))
                content.extend(block)
            return False
    except urllib.error.HTTPError as exc:
        exc.close()
        return False
    except OSError, ValueError, RecursionError:
        return False
