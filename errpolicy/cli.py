"""Command line entry point.

Usage:
    python -m errpolicy.cli --outcomes demo/outcomes.json \
        --policy demo/policy.json --out out/report.json
"""

import argparse
import json
import os
import sys

from . import report as report_mod
from . import retry as retry_mod
from . import rules as rules_mod


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="errpolicy",
        description="Policy-driven error classification and retry engine.",
    )
    parser.add_argument("--outcomes", required=True, help="path to outcomes JSON")
    parser.add_argument("--policy", required=True, help="path to policy JSON")
    parser.add_argument("--out", required=True, help="path to write the report JSON")
    parser.add_argument(
        "--sleep-scale",
        type=float,
        default=None,
        help="override the policy sleep_scale (recorded waits are never scaled)",
    )
    args = parser.parse_args(argv)

    with open(args.outcomes, "r", encoding="utf-8") as fh:
        outcomes = json.load(fh)
    rules = rules_mod.Rules.load(args.policy)

    runner = retry_mod.Runner(rules, sleep_scale=args.sleep_scale)
    records = runner.run(outcomes)
    result = report_mod.build_report(records)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    print(report_mod.summary_line(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
