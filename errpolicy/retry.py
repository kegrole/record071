"""Retry engine: executes request outcomes according to the policy.

Each outcome carries a scripted list of attempt results (this keeps the
engine deterministic and testable without real network access).  The
engine walks the script, classifies every failure, waits between retries
according to the policy, and finally routes the request into the success
set, the failure list, or the manual-review list.
"""

import time

from . import backoff as backoff_mod
from . import classify
from . import rules as rules_mod

RESULT_SUCCESS = "success"
RESULT_FAILURE = "failure"
RESULT_MANUAL = "manual"


def parse_retry_after(headers):
    """Return the Retry-After value in integer seconds, or None."""
    if not headers:
        return None
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                seconds = int(str(value).strip())
            except (TypeError, ValueError):
                return None
            return max(seconds, 0)
    return None


class AttemptRecord:
    def __init__(self, url):
        self.url = url
        self.cls = None
        self.attempts = 0
        self.waits = []
        self.action = None
        self.result = None

    def to_dict(self):
        return {
            "url": self.url,
            "class": self.cls,
            "attempts": self.attempts,
            "waits": list(self.waits),
            "action": self.action,
            "result": self.result,
        }


class Runner:
    def __init__(self, rules, sleep_scale=None, sleeper=None):
        self.rules = rules
        self.sleep_scale = rules.sleep_scale if sleep_scale is None else sleep_scale
        self.sleeper = time.sleep if sleeper is None else sleeper

    def run(self, outcomes):
        return [self.run_one(outcome) for outcome in outcomes]

    def run_one(self, outcome):
        url = outcome["url"]
        script = outcome["script"]
        record = AttemptRecord(url)
        first_error_class = None

        while True:
            attempt = script[min(record.attempts, len(script) - 1)]
            record.attempts += 1

            reason = classify.reason_for_attempt(
                attempt,
                parse_pattern=self.rules.parse_pattern,
                captcha_markers=self.rules.captcha_markers,
            )
            cls = self.rules.class_for_reason(reason)
            if cls != rules_mod.CLASS_OK and first_error_class is None:
                first_error_class = cls

            if cls == rules_mod.CLASS_OK:
                record.cls = first_error_class or rules_mod.CLASS_OK
                record.action = "none"
                record.result = RESULT_SUCCESS
                return record

            rule = self.rules.action_for_class(cls)
            action = rule["action"]
            max_attempts = self.rules.max_attempts_for(cls)

            if action == "retry" and record.attempts < max_attempts:
                wait = self._wait_seconds(cls, attempt, record.attempts)
                record.waits.append(wait)
                self._sleep(wait)
                continue

            terminal = action if action != "retry" else self.rules.on_exhaust(cls)
            record.cls = first_error_class
            record.action = terminal
            record.result = (
                RESULT_MANUAL if terminal == "report_manual" else RESULT_FAILURE
            )
            return record

    def _wait_seconds(self, cls, attempt, attempt_number):
        if self.rules.respect_retry_after(cls):
            retry_after = parse_retry_after(attempt.get("headers"))
            if retry_after is not None:
                return retry_after
        base, multiplier = self.rules.backoff_for(cls)
        return backoff_mod.wait_at(attempt_number - 1, base, multiplier)

    def _sleep(self, wait_seconds):
        # The recorded wait is always the real, unscaled value; the scale
        # only accelerates the actual sleep (e.g. in tests or demos).
        delay = wait_seconds * self.sleep_scale
        if delay > 0:
            self.sleeper(delay)
