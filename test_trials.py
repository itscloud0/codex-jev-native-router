import json
import tempfile
import threading
import unittest
from pathlib import Path

import trials

CAT = {
    "models": [
        {
            "slug": "gpt-6.1-sol",
            "visibility": "list",
            "supported_reasoning_levels": [{"effort": "high"}],
            "default_reasoning_level": "high",
        }
    ]
}


class TrialsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = Path(self.d.name)
        (self.root / "native-models.json").write_text(json.dumps(CAT))
        (self.root / "core.py").write_text("x")
        self.thread = "123e4567-e89b-12d3-a456-426614174000"

    def tearDown(self):
        self.d.cleanup()

    def start(self, **kw):
        now = kw.pop("now", 1.1)
        return trials.start(
            self.root,
            self.thread,
            kind="routine",
            scope="component",
            risk="low",
            uncertainty="known",
            effort="high",
            now=now,
            **kw,
        )

    def test_privacy_and_balanced(self):
        a = self.start()
        trials.finish(
            self.root, a["task_id"], outcome="failed", checks="not_run", now=2
        )
        b = self.start(now=2.1)
        self.assertNotEqual(a["arm"], b["arm"])
        text = (self.root / "state/trials.json").read_text()
        self.assertNotIn(self.thread, text)
        self.assertNotIn(str(self.root), text)

    def test_invalid_labels_and_open(self):
        a = self.start()
        with self.assertRaises(ValueError):
            trials.finish(self.root, a["task_id"], outcome="accepted", checks="failed")
        with self.assertRaises(ValueError):
            self.start()
        trials.finish(
            self.root, a["task_id"], outcome="failed", checks="not_run", now=2
        )
        with self.assertRaises(ValueError):
            trials.start(
                self.root,
                self.thread,
                kind="routine",
                scope="component",
                risk="low",
                uncertainty="known",
                effort="high",
                now=1.5,
            )

    def test_usage_complete_partial_and_override(self):
        a = self.start(arm="auto")
        trials.finish(
            self.root, a["task_id"], outcome="accepted", checks="passed", now=5
        )
        ses = trials._thread(self.thread)
        route = {
            "event": "route",
            "ts": 2,
            "route_id": "a" * 24,
            "session": ses,
            "client": "cli",
            "model": "gpt-6.1-sol",
            "effort": "high",
            "mode": "auto",
        }
        use = {
            **route,
            "event": "usage",
            "turn_hash": "b" * 24,
            "schema_version": 3,
            "usage_scope": "turn_total",
            "usage_coverage_reason": "cumulative_delta",
            "input_tokens": 10,
            "output_tokens": 2,
            "cached_input_tokens": 1,
            "cached_input_observed": True,
            "turn_duration_ms": 3,
        }
        rep = trials.report(self.root, [route, use, use], now=6)
        self.assertTrue(rep["tasks"][0]["comparable"])
        self.assertEqual(rep["tasks"][0]["input_tokens"], 10)
        use["manual_override"] = True
        self.assertFalse(
            trials.report(self.root, [route, use], now=6)["tasks"][0]["comparable"]
        )
        use["manual_override"] = False
        use.pop("cached_input_observed")
        self.assertFalse(
            trials.report(self.root, [route, use], now=6)["tasks"][0]["comparable"]
        )

    def test_auto_allows_chosen_model_but_baseline_is_pinned(self):
        a = self.start(arm="auto")
        trials.finish(
            self.root, a["task_id"], outcome="accepted", checks="passed", now=5
        )
        ses = trials._thread(self.thread)
        route = {
            "event": "route",
            "ts": 2,
            "route_id": "a" * 24,
            "session": ses,
            "client": "cli",
            "model": "gpt-6-astra",
            "effort": "ultra",
            "mode": "auto",
        }
        use = {
            **route,
            "event": "usage",
            "turn_hash": "b" * 24,
            "schema_version": 3,
            "usage_scope": "turn_total",
            "usage_coverage_reason": "cumulative_delta",
            "input_tokens": 10,
            "output_tokens": 2,
            "cached_input_tokens": 1,
            "cached_input_observed": True,
        }
        self.assertTrue(
            trials.report(self.root, [route, use], now=6)["tasks"][0]["comparable"]
        )
        trials.reopen(self.root, a["task_id"], now=6)
        trials.finish(
            self.root, a["task_id"], outcome="accepted", checks="passed", now=7
        )
        data = json.loads((self.root / "state/trials.json").read_text())
        data["tasks"][0]["arm"] = "baseline"
        (self.root / "state/trials.json").write_text(json.dumps(data))
        self.assertFalse(
            trials.report(self.root, [route, use], now=8)["tasks"][0]["adherent"]
        )

    def test_hours_cohorts_drift_and_link_mismatch(self):
        a = self.start(arm="auto")
        trials.finish(
            self.root, a["task_id"], outcome="failed", checks="not_run", now=7200
        )
        ses = trials._thread(self.thread)
        route = {
            "event": "route",
            "ts": 7199,
            "route_id": "a" * 24,
            "session": ses,
            "client": "cli",
            "model": "gpt-6.1-sol",
            "effort": "high",
            "mode": "auto",
        }
        use = {
            **route,
            "event": "usage",
            "turn_hash": "b" * 24,
            "schema_version": 3,
            "usage_scope": "turn_total",
            "usage_coverage_reason": "cumulative_delta",
            "input_tokens": 10,
            "output_tokens": 2,
            "cached_input_tokens": 1,
            "cached_input_observed": True,
        }
        report = trials.report(self.root, [route, use], hours=1, now=7200)
        self.assertTrue(report["tasks"][0]["lookback_truncated"])
        self.assertFalse(report["tasks"][0]["comparable"])
        self.assertEqual(trials.report(self.root, [], hours=1, now=12000)["tasks"], [])
        bad = {**use, "model": "gpt-6-astra"}
        report = trials.report(self.root, [route, use, bad], now=7200)
        self.assertEqual(report["tasks"][0]["link_mismatches"], 1)
        self.assertTrue(report["tasks"][0]["coverage_gap"])
        (self.root / "config.json").write_text(
            '{"mode":"auto","token":"never fingerprint this"}'
        )
        self.assertTrue(
            trials.report(self.root, [route, use], now=7200)["tasks"][0]["policy_drift"]
        )

    def test_integer_second_start_excludes_ambiguous_route(self):
        a = trials.start(
            self.root,
            self.thread,
            kind="routine",
            scope="component",
            risk="low",
            uncertainty="known",
            effort="high",
            arm="auto",
            now=1.0,
        )
        trials.finish(
            self.root, a["task_id"], outcome="failed", checks="not_run", now=2
        )
        route = {
            "event": "route",
            "ts": 1,
            "route_id": "a" * 24,
            "session": trials._thread(self.thread),
            "client": "cli",
            "model": "gpt-6.1-sol",
            "effort": "high",
            "mode": "auto",
        }
        task = trials.report(self.root, [route], now=3)["tasks"][0]
        self.assertEqual(task["routes"], 0)
        self.assertEqual(task["boundary_ambiguous_count"], 1)
        self.assertFalse(task["comparable"])

    def test_cohorts_ledger_validation_and_limit(self):
        a = self.start(arm="auto", project=self.root / "one")
        trials.finish(
            self.root, a["task_id"], outcome="failed", checks="not_run", now=2
        )
        b = self.start(arm="auto", project=self.root / "two", now=2.1)
        trials.finish(
            self.root, b["task_id"], outcome="failed", checks="not_run", now=3
        )
        self.assertEqual(len(trials.report(self.root, [], now=4)["cohorts"]), 2)
        state = self.root / "state/trials.json"
        data = json.loads(state.read_text())
        data["tasks"][0]["private"] = "secret"
        state.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            trials.report(self.root, [], now=4)
        data["tasks"][0].pop("private")
        template = data["tasks"][0]
        data["tasks"] = [{**template, "id": f"{i:024x}"} for i in range(1000)]
        state.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.start()

    def test_reopen_and_malformed_ledger(self):
        a = self.start()
        trials.finish(
            self.root, a["task_id"], outcome="accepted", checks="passed", now=2
        )
        trials.reopen(self.root, a["task_id"], now=3)
        state = self.root / "state/trials.json"
        state.write_text("{bad")
        with self.assertRaises(ValueError):
            trials.report(self.root, [], now=4)

    def test_reject_symlink_and_bad_fields(self):
        a = self.start()
        trials.finish(
            self.root, a["task_id"], outcome="failed", checks="not_run", now=2
        )
        self.assertEqual(
            trials.report(
                self.root, [{"event": "usage", "session": [], "ts": "bad"}], now=3
            )["tasks"][0]["routes"],
            0,
        )
        with self.assertRaises(ValueError):
            trials.start(
                self.root,
                "secret",
                kind="oops",
                scope="x",
                risk="x",
                uncertainty="x",
                effort="x",
            )

    def test_concurrent_open_claim_is_serialized(self):
        results = []

        def call():
            try:
                results.append(self.start())
            except ValueError:
                results.append(None)

        workers = [threading.Thread(target=call) for _ in range(4)]
        [w.start() for w in workers]
        [w.join() for w in workers]
        self.assertEqual(sum(x is not None for x in results), 1)
        state = json.loads((self.root / "state/trials.json").read_text())
        self.assertEqual(len(state["tasks"]), 1)
