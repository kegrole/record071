"""Load and query the policy (rules) file.

The policy is a JSON document that drives every decision: how reason
codes map to error classes, what each class does (retry / switch_source /
skip / report_manual), and the backoff parameters.  Nothing about the
disposition strategy is hard-coded in the engine.
"""

import json

from . import backoff as backoff_mod
from . import classify

DEFAULT_MAX_ATTEMPTS = 3

VALID_ACTIONS = ("retry", "switch_source", "skip", "report_manual")

CLASS_TRANSIENT = "transient"
CLASS_PERMANENT = "permanent"
CLASS_RATE_LIMITED = "rate_limited"
CLASS_BLOCKED = "blocked"
CLASS_PARSE_ERROR = "parse_error"
CLASS_OK = "ok"

ALL_CLASSES = (
    CLASS_TRANSIENT,
    CLASS_PERMANENT,
    CLASS_RATE_LIMITED,
    CLASS_BLOCKED,
    CLASS_PARSE_ERROR,
    CLASS_OK,
)

DEFAULT_CLASSIFICATION = {
    "timeout": CLASS_TRANSIENT,
    "connection_reset": CLASS_TRANSIENT,
    "http_502": CLASS_TRANSIENT,
    "http_503": CLASS_TRANSIENT,
    "http_504": CLASS_TRANSIENT,
    "http_404": CLASS_PERMANENT,
    "http_410": CLASS_PERMANENT,
    "dns_failure": CLASS_PERMANENT,
    "cert_error": CLASS_PERMANENT,
    "http_429": CLASS_RATE_LIMITED,
    "http_403_captcha": CLASS_BLOCKED,
    "parse_mismatch": CLASS_PARSE_ERROR,
}


class PolicyError(ValueError):
    """Raised when a policy file is missing required structure."""


class Rules:
    def __init__(self, data):
        if not isinstance(data, dict):
            raise PolicyError("policy must be a JSON object")
        defaults = data.get("defaults", {})
        self.max_attempts = int(defaults.get("max_attempts", DEFAULT_MAX_ATTEMPTS))
        backoff_cfg = defaults.get("backoff", {})
        self.backoff_base = backoff_cfg.get("base", backoff_mod.DEFAULT_BASE)
        self.backoff_multiplier = backoff_cfg.get(
            "multiplier", backoff_mod.DEFAULT_MULTIPLIER
        )
        self.sleep_scale = float(data.get("sleep_scale", 1.0))
        self.parse_pattern = data.get("parse_pattern")
        self.captcha_markers = list(data.get("captcha_markers", ["captcha"]))

        self.classification = dict(DEFAULT_CLASSIFICATION)
        self.classification.update(data.get("classification", {}))

        self.actions = dict(data.get("actions", {}))
        for cls, rule in self.actions.items():
            action = rule.get("action")
            if action not in VALID_ACTIONS:
                raise PolicyError(
                    "invalid action %r for class %r (valid: %s)"
                    % (action, cls, ", ".join(VALID_ACTIONS))
                )

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    def class_for_reason(self, reason):
        if reason == classify.REASON_OK:
            return CLASS_OK
        if reason in self.classification:
            return self.classification[reason]
        if reason.startswith("http_"):
            status = reason[len("http_"):]
            if status.isdigit() and int(status) in classify.TRANSIENT_STATUSES:
                return CLASS_TRANSIENT
            return CLASS_PERMANENT
        if reason in classify.TRANSIENT_ERRORS:
            return CLASS_TRANSIENT
        return CLASS_PERMANENT

    def action_for_class(self, cls):
        """Return the action rule dict for a class (defaults to skip)."""
        return self.actions.get(cls, {"action": "skip"})

    def max_attempts_for(self, cls):
        rule = self.action_for_class(cls)
        return int(rule.get("max_attempts", self.max_attempts))

    def backoff_for(self, cls):
        rule = self.action_for_class(cls)
        cfg = rule.get("backoff", {})
        base = cfg.get("base", self.backoff_base)
        multiplier = cfg.get("multiplier", self.backoff_multiplier)
        return base, multiplier

    def respect_retry_after(self, cls):
        rule = self.action_for_class(cls)
        return bool(rule.get("respect_retry_after", False))

    def on_exhaust(self, cls):
        rule = self.action_for_class(cls)
        return rule.get("on_exhaust", "skip")
