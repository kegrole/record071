"""Build the final report from engine records."""

from . import retry as retry_mod
from . import rules as rules_mod


def build_report(records):
    per_class = {cls: 0 for cls in rules_mod.ALL_CLASSES}
    successes = []
    failures = []
    manual = []
    details = []

    for rec in records:
        per_class[rec.cls] += 1
        details.append(rec.to_dict())
        if rec.result == retry_mod.RESULT_SUCCESS:
            successes.append({"url": rec.url, "attempts": rec.attempts})
        elif rec.result == retry_mod.RESULT_MANUAL:
            manual.append(
                {
                    "url": rec.url,
                    "class": rec.cls,
                    "attempts": rec.attempts,
                    "action": rec.action,
                }
            )
        else:
            failures.append(
                {
                    "url": rec.url,
                    "class": rec.cls,
                    "attempts": rec.attempts,
                    "action": rec.action,
                    "waits": list(rec.waits),
                }
            )

    return {
        "total": len(records),
        "per_class": per_class,
        "successes": successes,
        "failures": failures,
        "manual": manual,
        "details": details,
        "counts": {
            "success": len(successes),
            "failure": len(failures),
            "manual": len(manual),
        },
    }


def summary_line(report):
    dist = ", ".join(
        "%s=%d" % (cls, report["per_class"][cls]) for cls in rules_mod.ALL_CLASSES
    )
    return (
        "requests=%(total)d success=%(success)d failure=%(failure)d "
        "manual=%(manual)d per_class: "
        % {"total": report["total"], **report["counts"]}
    ) + dist
