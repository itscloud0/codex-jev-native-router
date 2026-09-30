import time
import unittest

from metrics import metrics_report


class MetricsReportTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.route = {"event": "route", "ts": self.now, "route_id": "a" * 24,
                      "session": "b" * 24, "client": "desktop", "model": "gpt-6-sol",
                      "effort": "high", "proposed_model": "gpt-6-luna", "proposed_effort": "medium",
                      "reason": "fallback", "jev_ms": 3, "router_ms": 5}

    def test_linked_tokens_and_switches_have_explicit_coverage(self):
        route = {**self.route, "switched": True}
        usage = {**route, "event": "usage", "input_tokens": 100, "cached_input_tokens": 75,
                 "output_tokens": 10, "cached_input_observed": True, "manual_override": True}
        report = metrics_report([route, usage])
        self.assertEqual(report["routes"]["model_switches"], 1)
        self.assertEqual(report["usage"]["token_totals"],
                         {"input_tokens": 100, "cached_input_tokens": 75, "output_tokens": 10})
        self.assertEqual(report["usage"]["token_observation_counts"]["cached_input_tokens"], 1)
        self.assertEqual(report["usage"]["weak_quality_signals"], {"manual_override": 1})

    def test_subscription_windows_are_isolated_and_negative_delta_is_not_savings(self):
        rows = [
            {"event": "subscription", "ts": self.now - 20, "limit_id": "codex", "plan_type": "pro",
             "primary": {"used_percent": 70, "window_minutes": 300, "resets_at": 1000}, "secondary": None},
            {"event": "subscription", "ts": self.now - 10, "limit_id": "codex", "plan_type": "pro",
             "primary": {"used_percent": 10, "window_minutes": 300, "resets_at": 1000}, "secondary": None},
            {"event": "subscription", "ts": self.now, "limit_id": "codex", "plan_type": "pro",
             "primary": {"used_percent": 20, "window_minutes": 10080, "resets_at": 2000}, "secondary": None},
        ]
        report = metrics_report(rows)
        groups = report["subscriptions"]["groups"]
        self.assertEqual(len(groups), 2)
        reset = groups[0]
        self.assertEqual((reset["window_minutes"], reset["observations"], reset["delta_percentage_points"]), (300, 2, -60))
        self.assertTrue(reset["reset_or_correction_observed"])
        self.assertIsNone(report["subscription_allowance_savings"])

    def test_subscription_deduplicates_account_snapshot_and_accepts_hashed_quota(self):
        snapshot = {"event": "subscription", "ts": self.now, "limit_id": "quota-" + "c" * 24,
                    "primary": {"usedPercent": 30, "windowDurationMins": 300, "resetsAt": 1000}}
        report = metrics_report([snapshot, {**snapshot, "client": "cli"}])
        group = report["subscriptions"]["groups"][0]
        self.assertEqual((group["limit_id"], group["observations"]), ("quota-" + "c" * 24, 1))

    def test_subscription_accepts_native_model_limit_and_detects_intermediate_reset(self):
        rows = [
            {"event": "subscription", "ts": self.now - 3, "limit_id": "gpt-6.1-sol", "plan_type": "enterprise",
             "primary": {"used_percent": 10, "window_minutes": 60, "resets_at": 42}},
            {"event": "subscription", "ts": self.now - 2, "limit_id": "gpt-6.1-sol", "plan_type": "private-plan",
             "primary": {"used_percent": 5, "window_minutes": 60, "resets_at": 42}},
            {"event": "subscription", "ts": self.now - 1, "limit_id": "gpt-6.1-sol", "plan_type": "enterprise",
             "primary": {"used_percent": 12, "window_minutes": 60, "resets_at": 42}},
        ]
        report = metrics_report(rows)
        self.assertTrue(report["subscriptions"]["groups"][0]["reset_or_correction_observed"])
        self.assertEqual(report["subscriptions"]["plan_type_observations"], {"enterprise": 2})

    def test_malformed_link_values_do_not_crash_or_link(self):
        bad_route = {**self.route, "session": {"private": "value"}}
        bad_usage = {"event": "usage", "ts": self.now, "route_id": "a" * 24, "session": [],
                     "client": "desktop", "model": "gpt-6-sol", "effort": "high", "mode": "native"}
        report = metrics_report([bad_route, bad_usage])
        self.assertEqual(report["usage"]["linked_calls"], 0)
        self.assertEqual(report["usage"]["unlinked_native_calls_excluded_from_routed_claims"], 1)
        self.assertEqual(report["routes"]["executed_pairs"], {"gpt-6-sol/high": 1})

    def test_subscription_groups_are_capped_and_omission_is_disclosed(self):
        rows = [
            {"event": "subscription", "ts": self.now, "limit_id": "codex", "plan_type": "pro",
             "primary": {"used_percent": 1, "window_minutes": 1, "resets_at": reset}}
            for reset in range(201)
        ]
        subscriptions = metrics_report(rows)["subscriptions"]
        self.assertEqual((len(subscriptions["groups"]), subscriptions["omitted_groups"]), (200, 1))

    def test_only_exactly_linked_usage_contributes_metrics_and_cache_coverage(self):
        linked = {"event": "usage", "ts": self.now, "route_id": "a" * 24, "session": "b" * 24,
                  "client": "desktop", "model": "gpt-6-sol", "effort": "high", "status": "error",
                  "cache_observed": True, "input_tokens": 100, "cached_input_tokens": 40,
                  "turn_duration_ms": 20, "first_response_ms": 5, "tool_calls": 2, "command_failures": 1,
                  "compactions": 1, "reasoning_output_tokens": 9}
        native = {**linked, "route_id": "c" * 24, "mode": "native", "turn_duration_ms": 999}
        report = metrics_report([self.route, linked, native])
        usage = report["usage"]
        self.assertEqual((usage["linked_calls"], usage["unlinked_native_calls_excluded_from_routed_claims"]), (1, 1))
        self.assertEqual(usage["statuses"], {"error": 1})
        self.assertEqual(usage["cache"], {"observed_calls": 1, "missing_coverage_calls": 0, "cached_input_ratio": 0.4})
        self.assertEqual(usage["turn_metrics"]["totals"]["turn_duration_ms"], 20)
        self.assertEqual(usage["turn_metrics"]["totals"]["reasoning_output_tokens"], 9)
        self.assertEqual(report["routes"]["latency_ms"]["jev"], {"observations": 1, "p50": 3, "p95": 3})

    def test_malformed_input_is_skipped_without_leaking_or_counting_it(self):
        malformed_subscription = {"event": "subscription", "ts": self.now, "limit_id": "not-safe",
                                  "primary": {"used_percent": 101, "window_minutes": 1, "resets_at": 1}}
        report = metrics_report([None, {"event": "route"}, malformed_subscription, self.route])
        self.assertEqual(report["window"]["malformed_rows_skipped"], 2)
        self.assertEqual(report["subscriptions"]["groups"], [])
        self.assertEqual(report["routes"]["reasons"], {"fallback": 1})
        self.assertNotIn("not-safe", str(report))
        with self.assertRaises(ValueError):
            metrics_report({}, hours=1)
        with self.assertRaises(ValueError):
            metrics_report([], hours=0)


if __name__ == "__main__":
    unittest.main()
