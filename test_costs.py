import time
import unittest

from costs import cost_report


class CostReportTests(unittest.TestCase):
    def test_only_exact_auto_receipts_enter_auto_counterfactual(self):
        ts = int(time.time())
        route = {"event": "route", "ts": ts, "route_id": "a" * 24,
                 "mode": "auto", "session": "b" * 24, "client": "cli",
                 "model": "gpt-6-luna", "effort": "low",
                 "jev_input_tokens": 300, "jev_output_tokens": 20}
        usage = {"event": "usage", "ts": ts, "route_id": route["route_id"],
                 "session": route["session"], "client": "cli", "model": "gpt-6-luna",
                 "effort": "low", "input_tokens": 1_000_000,
                 "cached_input_tokens": 500_000, "output_tokens": 100_000}
        unrelated = {**usage, "route_id": "c" * 24, "model": "gpt-6-sol"}
        result = cost_report([route, usage, unrelated])
        self.assertEqual(result["auto"]["linked_calls"], 1)
        credits = result["auto"]["all_clients"]["codex_credit_equivalent"]
        self.assertEqual(credits["observed_mix"], 2.625)
        self.assertEqual(credits["all_sol_same_tokens"], 52.5)
        self.assertEqual(credits["vs_sol"], 49.875)
        self.assertEqual(result["jev"]["input_tokens"], 300)
        self.assertIsNone(result["jev"]["cost_usd"])
        self.assertEqual(result["observed_all_modes"]["calls"], 2)

    def test_missing_tokens_and_old_jev_usage_are_explicit(self):
        ts = int(time.time())
        rows = [{"event": "route", "ts": ts, "mode": "shadow"},
                {"event": "usage", "ts": ts, "model": "gpt-6-sol",
                 "input_tokens": None, "cached_input_tokens": None, "output_tokens": None}]
        result = cost_report(rows)
        self.assertEqual(result["jev"]["metered_requests"], 0)
        self.assertEqual(result["observed_all_modes"]["unpriced_calls"], 1)
        with self.assertRaises(ValueError):
            cost_report(rows, 0)


if __name__ == "__main__":
    unittest.main()
