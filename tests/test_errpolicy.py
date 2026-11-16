"""Unittests covering the acceptance criteria for the errpolicy package.

Runnable from the repository root via:
    python -m unittest tests.test_errpolicy -v
or from the tests/ directory via:
    python -m unittest test_errpolicy -v
"""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from errpolicy import backoff, classify, report, retry, rules

OUTCOMES_PATH = os.path.join(REPO_ROOT, "demo", "outcomes.json")
POLICY_PATH = os.path.join(REPO_ROOT, "demo", "policy.json")

URL_RATE_LIMITED = "http://example.com/rate-limited"
URL_FLAKY = "http://example.com/flaky-503"
URL_MISSING = "http://example.com/missing-404"
URL_DNS = "http://example.com/dns-failure"
URL_BLOCKED = "http://example.com/blocked-captcha"
URL_PARSE = "http://example.com/structure-changed"
URL_TIMEOUT = "http://example.com/always-timeout"
URL_OK_404_BODY = "http://example.com/ok-mentions-404"


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def run_engine(policy_data=None):
    outcomes = load_json(OUTCOMES_PATH)
    data = policy_data if policy_data is not None else load_json(POLICY_PATH)
    rule_set = rules.Rules(data)
    runner = retry.Runner(rule_set, sleep_scale=0)
    records = runner.run(outcomes)
    return report.build_report(records), records


def record_by_url(records, url):
    for rec in records:
        if rec.url == url:
            return rec
    raise AssertionError("no record for %s" % url)


class FixtureTest(unittest.TestCase):
    """Criterion 1: the fixture itself matches the required shape."""

    def setUp(self):
        self.outcomes = load_json(OUTCOMES_PATH)
        self.by_url = {o["url"]: o for o in self.outcomes}

    def test_twelve_outcomes(self):
        self.assertEqual(len(self.outcomes), 12)
        self.assertEqual(len(self.by_url), 12)

    def test_required_shapes(self):
        rate = self.by_url[URL_RATE_LIMITED]["script"]
        self.assertEqual(rate[0]["status"], 429)
        self.assertEqual(rate[0]["headers"]["Retry-After"], "7")

        flaky = self.by_url[URL_FLAKY]["script"]
        self.assertEqual([a["status"] for a in flaky], [503, 503, 200])

        self.assertEqual(self.by_url[URL_MISSING]["script"][0]["status"], 404)
        self.assertEqual(self.by_url[URL_DNS]["script"][0]["error"], "dns_failure")

        blocked = self.by_url[URL_BLOCKED]["script"][0]
        self.assertEqual(blocked["status"], 403)
        self.assertIn("captcha", blocked["body"].lower())

        parse_fail = self.by_url[URL_PARSE]["script"][0]
        self.assertEqual(parse_fail["status"], 200)

        timeout = self.by_url[URL_TIMEOUT]["script"]
        self.assertEqual([a["error"] for a in timeout], ["timeout"] * 3)

        ok_body = self.by_url[URL_OK_404_BODY]["script"][0]
        self.assertEqual(ok_body["status"], 200)
        self.assertIn("404", ok_body["body"])


class ClassificationTest(unittest.TestCase):
    """Criterion 2: per_class counts and first-error classification."""

    @classmethod
    def setUpClass(cls):
        cls.report, cls.records = run_engine()

    def test_per_class_counts(self):
        expected = {
            "transient": 2,
            "permanent": 2,
            "rate_limited": 1,
            "blocked": 1,
            "parse_error": 1,
            "ok": 5,
        }
        self.assertEqual(self.report["per_class"], expected)
        self.assertEqual(sum(self.report["per_class"].values()), 12)

    def test_body_mentioning_404_is_ok(self):
        rec = record_by_url(self.records, URL_OK_404_BODY)
        self.assertEqual(rec.cls, "ok")
        self.assertEqual(rec.result, "success")

    def test_rate_limited_counted_despite_success(self):
        rec = record_by_url(self.records, URL_RATE_LIMITED)
        self.assertEqual(rec.cls, "rate_limited")
        self.assertEqual(rec.result, "success")

    def test_classifier_ignores_body_digits(self):
        reason = classify.reason_for_attempt(
            {"status": 200, "body": "error 404 page not found <title>x</title>"},
            parse_pattern="<title>[^<]+</title>",
        )
        self.assertEqual(reason, classify.REASON_OK)


class BackoffTest(unittest.TestCase):
    """Criterion 3: 503 retried twice, waits strictly increasing and recorded."""

    @classmethod
    def setUpClass(cls):
        cls.report, cls.records = run_engine()

    def test_default_sequence(self):
        self.assertEqual(backoff.sequence(4), [1, 2, 4, 8])

    def test_flaky_503_attempts_and_waits(self):
        rec = record_by_url(self.records, URL_FLAKY)
        self.assertEqual(rec.attempts, 3)
        self.assertEqual(rec.result, "success")
        self.assertEqual(len(rec.waits), 2)
        self.assertEqual(rec.waits, [1, 2])
        self.assertLess(rec.waits[0], rec.waits[1])

    def test_waits_recorded_in_report_details(self):
        detail = {d["url"]: d for d in self.report["details"]}[URL_FLAKY]
        self.assertEqual(detail["waits"], [1, 2])

    def test_backoff_is_configurable(self):
        self.assertEqual(backoff.sequence(3, base=2, multiplier=3), [2, 6, 18])


class RetryAfterTest(unittest.TestCase):
    """Criterion 4: 429 waits exactly the Retry-After seconds."""

    @classmethod
    def setUpClass(cls):
        cls.report, cls.records = run_engine()

    def test_retry_after_wait_recorded(self):
        rec = record_by_url(self.records, URL_RATE_LIMITED)
        self.assertEqual(rec.waits, [7])
        self.assertEqual(rec.attempts, 2)
        self.assertEqual(rec.result, "success")

    def test_retry_after_not_from_backoff_sequence(self):
        rec = record_by_url(self.records, URL_RATE_LIMITED)
        self.assertNotIn(rec.waits[0], backoff.sequence(4))

    def test_recorded_wait_not_scaled(self):
        outcomes = load_json(OUTCOMES_PATH)
        rule_set = rules.Rules(load_json(POLICY_PATH))
        slept = []
        runner = retry.Runner(
            rule_set, sleep_scale=0.01, sleeper=lambda s: slept.append(s)
        )
        records = runner.run(outcomes)
        rec = record_by_url(records, URL_RATE_LIMITED)
        self.assertEqual(rec.waits, [7])
        self.assertAlmostEqual(slept[0], 0.07)


class PermanentNoRetryTest(unittest.TestCase):
    """Criterion 5: permanent errors are never retried."""

    @classmethod
    def setUpClass(cls):
        cls.report, cls.records = run_engine()

    def test_404_single_attempt(self):
        rec = record_by_url(self.records, URL_MISSING)
        self.assertEqual(rec.attempts, 1)
        self.assertEqual(rec.cls, "permanent")

    def test_dns_single_attempt(self):
        rec = record_by_url(self.records, URL_DNS)
        self.assertEqual(rec.attempts, 1)
        self.assertEqual(rec.cls, "permanent")


class AlternatePolicyTest(unittest.TestCase):
    """Criterion 6: a different rules file can make http_404 retryable."""

    def test_404_retried_with_alternate_policy(self):
        data = load_json(POLICY_PATH)
        data["classification"]["http_404"] = "transient"
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as fh:
            json.dump(data, fh)
            alt_path = fh.name
        try:
            rule_set = rules.Rules.load(alt_path)
            self.assertEqual(rule_set.max_attempts_for("transient"), 3)
            outcomes = load_json(OUTCOMES_PATH)
            runner = retry.Runner(rule_set, sleep_scale=0)
            records = runner.run(outcomes)
        finally:
            os.unlink(alt_path)
        rec = record_by_url(records, URL_MISSING)
        self.assertEqual(rec.attempts, 3)
        self.assertEqual(rec.result, "failure")

    def test_default_policy_keeps_404_permanent(self):
        _, records = run_engine()
        rec = record_by_url(records, URL_MISSING)
        self.assertEqual(rec.cls, "permanent")
        self.assertEqual(rec.attempts, 1)


class FinalListsTest(unittest.TestCase):
    """Criterion 7: success/failure/manual lists partition all 12 requests."""

    @classmethod
    def setUpClass(cls):
        cls.report, cls.records = run_engine()

    def test_counts(self):
        self.assertEqual(self.report["counts"]["success"], 7)
        self.assertEqual(self.report["counts"]["failure"], 4)
        self.assertEqual(self.report["counts"]["manual"], 1)
        self.assertEqual(len(self.report["successes"]), 7)
        self.assertEqual(len(self.report["failures"]), 4)
        self.assertEqual(len(self.report["manual"]), 1)

    def test_lists_partition_all_requests(self):
        total = (
            len(self.report["successes"])
            + len(self.report["failures"])
            + len(self.report["manual"])
        )
        self.assertEqual(total, 12)

    def test_failure_entries_have_class_and_attempts(self):
        expected_urls = {URL_MISSING, URL_DNS, URL_PARSE, URL_TIMEOUT}
        seen = set()
        for failure in self.report["failures"]:
            self.assertIn("url", failure)
            self.assertIn("class", failure)
            self.assertIn("attempts", failure)
            self.assertIn("action", failure)
            seen.add(failure["url"])
        self.assertEqual(seen, expected_urls)

    def test_blocked_goes_to_manual_not_failures(self):
        manual_urls = [m["url"] for m in self.report["manual"]]
        self.assertEqual(manual_urls, [URL_BLOCKED])
        failure_urls = [f["url"] for f in self.report["failures"]]
        self.assertNotIn(URL_BLOCKED, failure_urls)
        rec = record_by_url(self.records, URL_BLOCKED)
        self.assertEqual(rec.attempts, 1)
        self.assertEqual(rec.action, "report_manual")

    def test_success_set_members(self):
        success_urls = {s["url"] for s in self.report["successes"]}
        self.assertIn(URL_FLAKY, success_urls)
        self.assertIn(URL_RATE_LIMITED, success_urls)
        self.assertIn(URL_OK_404_BODY, success_urls)


if __name__ == "__main__":
    unittest.main()
