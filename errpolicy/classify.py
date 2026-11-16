"""Classify the outcome of a single request attempt into a reason code.

Classification is based on structured signals only: the transport-level
error kind, the HTTP status code, and (for 2xx responses) a structural
parse check.  The response body is never scanned for status-code-looking
digits, so a 200 page that merely mentions "404" in its text is still a
success.
"""

import re

TRANSIENT_ERRORS = frozenset({"timeout", "connection_reset"})
PERMANENT_ERRORS = frozenset({"dns_failure", "cert_error"})

TRANSIENT_STATUSES = frozenset({502, 503, 504})
PERMANENT_STATUSES = frozenset({404, 410})

REASON_OK = "ok"
REASON_PARSE_MISMATCH = "parse_mismatch"
REASON_403_CAPTCHA = "http_403_captcha"


def is_success_status(status):
    return status is not None and 200 <= status < 300


def has_captcha_signature(body, captcha_markers):
    text = (body or "").lower()
    return any(marker.lower() in text for marker in captcha_markers)


def parse_matches(body, parse_pattern):
    if not parse_pattern:
        return True
    return re.search(parse_pattern, body or "") is not None


def reason_for_attempt(attempt, parse_pattern=None, captcha_markers=()):
    """Return the reason code for one attempt result.

    An attempt is a mapping with either an 'error' key (transport-level
    failure) or a 'status' plus optional 'headers' and 'body'.
    """
    error = attempt.get("error")
    if error:
        return error

    status = attempt.get("status")
    body = attempt.get("body") or ""

    if status == 403:
        if has_captcha_signature(body, captcha_markers):
            return REASON_403_CAPTCHA
        return "http_403"

    if not is_success_status(status):
        return "http_%s" % status

    if not parse_matches(body, parse_pattern):
        return REASON_PARSE_MISMATCH

    return REASON_OK
