import copy
import json
from pathlib import Path
import tempfile
import unittest

import trials


class TrialEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = {"models": [{"slug": "gpt-6.1-sol", "visibility": "list",
                                    "supported_reasoning_levels": [{"effort": "medium"}]}]}
        (self.root / "native-models.json").write_text(json.dumps(self.catalog))
        self.thread = "123e4567-e89b-12d3-a456-426614174000"
        task = trials.start(self.root, self.thread, kind="routine", scope="component",
                            risk="low", uncertainty="known", effort="medium", arm="baseline", now=10.1)
        self.task_id = task["task_id"]
        trials.finish(self.root, self.task_id, outcome="accepted", checks="passed", now=20.5)
        self.route = {"event": "route", "ts": 12, "session": trials._thread(self.thread),
                      "route_id": "a" * 24, "client": "cli", "mode": "shadow",
                      "model": "gpt-6.1-sol", "effort": "medium"}
        self.usage = {**self.route, "event": "usage", "ts": 18, "turn_hash": "b" * 24,
                      "schema_version": 3, "usage_scope": "turn_total", "usage_coverage_reason": "cumulative_delta",
                      "input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 40,
                      "cached_input_observed": True, "turn_duration_ms": 6000}

    def result(self, *rows):
        return trials.report(self.root, list(rows) if rows else [self.route, self.usage], now=25)["tasks"][0]

    def test_exact_duplicates_do_not_double_count_and_conflicts_are_incomplete(self):
        report = self.result(self.route, self.route.copy(), self.usage, self.usage.copy())
        self.assertTrue(report["comparable"])
        self.assertEqual((report["routes"], report["full"], report["input_tokens"]), (1, 1, 100))
        self.assertFalse(self.result(self.route, self.usage, {**self.usage, "input_tokens": 101})["comparable"])

    def test_unplaceable_same_session_event_cannot_disappear(self):
        for value in (None, "broken", True, 10**400, float("nan")):
            with self.subTest(value=str(value)[:12]):
                result = self.result(self.route, self.usage, {**self.usage, "ts": value})
                self.assertFalse(result["comparable"])
                self.assertEqual(result["unknown_time_records"], 1)
        other = {**self.usage, "session": "c" * 24, "ts": "broken"}
        self.assertTrue(self.result(self.route, self.usage, other)["comparable"])

    def test_malformed_fields_fail_closed_without_type_errors(self):
        for key in ("usage_scope", "usage_coverage_reason", "status", "turn_hash", "cached_input_tokens"):
            with self.subTest(key=key):
                self.assertFalse(self.result(self.route, {**self.usage, key: []})["comparable"])
        for key in ("client", "effort", "mode", "route_id"):
            with self.subTest(key=key):
                result = self.result(self.route, self.usage, {**self.route, key: []})
                self.assertFalse(result["comparable"])
                self.assertEqual(result["invalid_routes"], 1)

    def test_client_model_and_mode_mismatches_do_not_link(self):
        for key, value in (("client", "desktop"), ("model", "gpt-6-luna"), ("mode", "auto")):
            with self.subTest(key=key):
                result = self.result(self.route, {**self.usage, key: value})
                self.assertFalse(result["comparable"])
                self.assertEqual(result["link_mismatches"], 1)
        self.assertFalse(self.result({**self.route, "client": "desktop"},
                                     {**self.usage, "client": "desktop"})["adherent"])

    def test_malformed_manual_metadata_is_not_interpreted_as_false(self):
        for patch in ({"manual_override": []}, {"manual_override": "true"},
                      {"manual_override": 1}, {"reason": []}, {"reason": "unknown-new-reason"}):
            with self.subTest(patch=patch):
                self.assertFalse(self.result({**self.route, **patch}, self.usage)["comparable"])
                self.assertFalse(self.result(self.route, {**self.usage, **patch})["comparable"])
        known = {"manual_override": False, "reason": "shadow_jev"}
        self.assertTrue(self.result({**self.route, **known}, {**self.usage, **known})["comparable"])

    def test_partial_unknown_scope_cache_and_duplicate_turn_are_excluded(self):
        for update in ({"schema_version": 2}, {"usage_scope": "last_model_call"},
                       {"cached_input_observed": False}, {"cached_input_tokens": 101}):
            self.assertFalse(self.result(self.route, {**self.usage, **update})["comparable"])
        second_route = {**self.route, "route_id": "c" * 24}
        second_usage = {**self.usage, "route_id": "c" * 24}
        self.assertFalse(self.result(self.route, self.usage, second_route, second_usage)["comparable"])

    def test_boundary_record_remains_visible(self):
        for stamp in (10, 20):
            report = self.result(self.route, self.usage, {**self.usage, "ts": stamp})
            self.assertFalse(report["comparable"])
            self.assertEqual(report["boundary_ambiguous_count"], 1)

    def test_rework_survives_acceptance_and_is_aggregated(self):
        trials.reopen(self.root, self.task_id, now=26)
        trials.finish(self.root, self.task_id, outcome="rework", checks="failed", now=27)
        trials.reopen(self.root, self.task_id, now=28)
        trials.finish(self.root, self.task_id, outcome="accepted", checks="passed", now=29)
        report = trials.report(self.root, [self.route, self.usage], now=30)
        self.assertTrue(report["tasks"][0]["had_rework"])
        self.assertEqual(next(iter(report["cohorts"].values()))["had_rework_tasks"], 1)

    def test_malformed_ledger_labels_and_overlaps_are_rejected(self):
        path = self.root / "state/trials.json"
        original = json.loads(path.read_text())
        for key in ("kind", "outcome", "checks", "assignment_method", "client"):
            data = copy.deepcopy(original)
            data["tasks"][0][key] = ["private-field"]
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "malformed trial ledger"):
                trials.report(self.root, [], now=25)
        data = copy.deepcopy(original)
        data["tasks"].append({**data["tasks"][0], "id": "d" * 24})
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "overlapping"):
            trials.report(self.root, [], now=25)

    def test_unsafe_ledger_or_lock_never_overwrites_target(self):
        ledger = self.root / "state/trials.json"
        contents = ledger.read_bytes()
        foreign = self.root / "unowned-file"
        foreign.write_bytes(contents)
        ledger.unlink()
        ledger.symlink_to(foreign)
        with self.assertRaisesRegex(ValueError, "unsafe trial ledger"):
            trials.report(self.root, [], now=25)
        self.assertEqual(foreign.read_bytes(), contents)
        ledger.unlink()
        ledger.write_bytes(contents)
        ledger.chmod(0o600)
        lock = self.root / "state/trials.lock"
        lock.unlink()
        lock.symlink_to(foreign)
        with self.assertRaisesRegex(ValueError, "unsafe trial ledger"):
            trials.reopen(self.root, self.task_id, now=25)
        self.assertEqual(foreign.read_bytes(), contents)

    def test_task_limit_rejects_next_registration_without_deleting_history(self):
        path = self.root / "state/trials.json"
        ledger = json.loads(path.read_text())
        template = ledger["tasks"][0]
        ledger["tasks"] = [{**template, "id": f"{index:024x}", "session": f"{index:024x}"}
                           for index in range(1000)]
        path.write_text(json.dumps(ledger))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "task limit"):
            trials.start(self.root, self.thread, kind="routine", scope="component", risk="low",
                         uncertainty="known", effort="medium", now=30)
        self.assertEqual(path.read_bytes(), before)

    def test_cli_generation_changes_baseline_and_fingerprint(self):
        before = trials._fingerprint(self.root, "cli")
        generation = "cli-20261002T120000Z-aaaaaaaa"
        directory = self.root / "catalogs" / generation
        directory.mkdir(parents=True)
        newer = copy.deepcopy(self.catalog)
        newer["models"][0]["slug"] = "gpt-6.2-sol"
        for name in ("native-models.json", "models.json"):
            (directory / name).write_text(json.dumps(newer))
        (self.root / "manifest.json").write_text(json.dumps({"cli_catalog_generation": generation}))
        self.assertNotEqual(before, trials._fingerprint(self.root, "cli"))
        self.assertEqual(trials._baseline(self.root, "medium", "cli"), "gpt-6.2-sol")
        self.assertEqual(trials._baseline(self.root, "medium", "desktop"), "gpt-6.1-sol")
        self.assertTrue(self.result()["policy_drift"])

    def test_effective_effort_config_changes_are_detected_and_huge_values_do_not_crash(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({"effort_policy": "fixed", "fixed_effort": "medium", "key_file": "private"}))
        before = trials._fingerprint(self.root)
        path.write_text(json.dumps({"effort_policy": "fixed", "fixed_effort": "high", "key_file": "private"}))
        self.assertNotEqual(before, trials._fingerprint(self.root))
        self.assertEqual(trials._safe_config({"timeout_seconds": 10**400}), {"timeout_seconds": "invalid"})
        self.assertNotIn("private", json.dumps(trials.report(self.root, [], now=25)))


if __name__ == "__main__":
    unittest.main()
