import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from core import Router, _floor, latest_user_text, sanitize_task, visible_roles


def catalog():
    models = []
    for role in ("luna", "terra", "sol", "astra"):
        models.append({
            "slug": "gpt-6-" + role, "visibility": "list", "supported_in_api": True,
            "supported_reasoning_levels": [{"effort": x} for x in ("low", "medium", "high")],
            "default_reasoning_level": "medium", "model_messages": {"identity": role},
            "input_modalities": ["text"], "tool_mode": "standard",
        })
    models.append({"slug": "gpt-reserve", "visibility": "hide", "supported_reasoning_levels": [{"effort": "medium"}]})
    models.append({"slug": "gpt-7-oracle", "visibility": "list", "supported_reasoning_levels": [{"effort": "medium"}]})
    return {"models": models}


def payload(text, model="jev-auto", context_tokens=0):
    return {"model": model, "context_tokens": context_tokens,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}]}


class RouterTest(unittest.TestCase):
    def test_russian_risk_floors(self):
        self.assertEqual(_floor("Исправь уязвимость авторизации"), "astra")
        self.assertEqual(_floor("Сделай миграцию схемы данных"), "astra")
        self.assertEqual(_floor("Найди сложную ошибку в интеграции"), "sol")
        self.assertEqual(_floor("Переименуй локальную переменную"), "luna")
        self.assertEqual(_floor("Во время изменений идут реальные заявки"), "astra")
        self.assertEqual(_floor("Real customer orders are arriving"), "astra")

    def test_jev_request_uses_explicit_client_user_agent(self):
        with tempfile.TemporaryDirectory() as temp:
            key_path = Path(temp) / "key"
            key_path.write_text("test-key")
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b"{}"
            opener = mock.MagicMock()
            opener.open.return_value = response
            with mock.patch("core.urllib.request.build_opener", return_value=opener):
                Router._call_jev({"model": "jev-latest", "state": {}, "questions": {}}, 2, key_path)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.get_header("User-agent"), "JevCodexRouter/1.0")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "catalog.json").write_text(json.dumps(catalog()))
        (self.root / "config.json").write_text(json.dumps({"mode": "auto", "auto_policy": "baseline",
                                                           "key_file": str(self.root / "missing-key")}))
        self.calls = []

        def jev(body, timeout, key_file):
            self.calls.append(body)
            return {"answers": {"capability": {"choice": "luna"}, "effort": {"choice": "low"}}}

        self.jev = jev
        self.router = self.new_router()

    def new_router(self, jev=None):
        return Router(self.root / "config.json", self.root / "catalog.json",
                      self.root / "leases.json", self.root / "telemetry.jsonl", jev or self.jev)

    def test_latency_does_not_clip_to_socket_timeout(self):
        with mock.patch("core.time.monotonic", return_value=0) as clock:
            def slow_jev(body, timeout, key_file):
                clock.return_value = 2.75
                return self.jev(body, timeout, key_file)
            decision = self.new_router(slow_jev).decide(
                payload("Change the button label text"), native_selection=True, session_id="slow-route")
        self.assertEqual(decision["jev_ms"], 2750)
        self.assertEqual(decision["router_ms"], 2750)

    def test_jev_timeout_is_distinct_and_default_budget_is_four_seconds(self):
        seen = []
        def timeout_jev(body, timeout, key_file):
            seen.append(timeout)
            raise TimeoutError("no response")
        router = self.new_router(timeout_jev)
        decision = router.decide(payload("Change the button label text"),
                                 native_selection=True, session_id="timeout-route")
        self.assertEqual(seen, [4.0])
        self.assertEqual(decision["reason"], "jev_timeout")
        self.assertEqual(decision["model"], "gpt-6-sol")
        router.record_usage(decision, None, "ok", event="route")
        self.assertEqual(json.loads((self.root / "telemetry.jsonl").read_text())["reason"], "jev_timeout")

    def test_only_latest_real_user_and_no_raw_private_content(self):
        raw = "Fix the small label. secret=abcdef1234567890 https://internal.example/a/b person@example.com /private/repo/file.py ```python\nprint('hidden')\n```"
        p = {"model": "jev-auto", "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "Old request"}]},
            {"role": "tool", "content": "secret tool output"},
            {"role": "user", "content": [{"type": "input_text", "text": raw}]},
        ]}
        self.assertEqual(latest_user_text(p), raw)
        clean, uncertain = sanitize_task(raw)
        self.assertTrue(uncertain)
        for secret in ("abcdef", "internal.example", "person@example", "/private", "print(", "Old request"):
            self.assertNotIn(secret, clean)
        decision = self.router.decide(p, native_selection=True, session_id="privacy")
        self.assertEqual(decision["reason"], "privacy_fallback")
        self.assertFalse(self.calls)

    def test_envelopes_and_high_entropy_are_stripped(self):
        text = "Fix button <environment_context>machine private detail</environment_context> token=abc123 " + "A" * 48
        clean, _ = sanitize_task(text)
        self.assertNotIn("machine private", clean)
        self.assertNotIn("abc123", clean)
        self.assertNotIn("A" * 20, clean)
        self.assertEqual(sanitize_task("Please review this:\n-----BEGIN PRIVATE KEY-----\nPRIVATEBODY\n-----END PRIVATE KEY-----"), ("", True))
        self.assertEqual(sanitize_task("Fix this code:\ndef handler(req):\n    return req"), ("", True))
        fenced, uncertain = sanitize_task("```text\nPrivate project instructions and source\n```\nPlease inspect this")
        self.assertTrue(uncertain)
        self.assertNotIn("Private project", fenced)
        self.assertEqual(latest_user_text({"input": [
            {"role": "user", "content": "Please fix the button"},
            {"role": "user", "content": "<environment_context>private host</environment_context>"},
        ]}), "Please fix the button")

    def test_concrete_model_passes_without_jev_or_normalization(self):
        p = payload("Fix parser", "gpt-5.6-sol")
        p["reasoning"] = {"effort": "ultra"}
        decision = self.router.decide(p)
        self.assertEqual((decision["model"], decision["effort"]), ("gpt-5.6-sol", "ultra"))
        self.assertFalse(self.calls)
        astra = self.router.decide(payload("Review architecture", "gpt-6-astra"))
        self.assertEqual(astra["model"], "gpt-6-astra")

    def test_auto_and_shadow_never_propose_astra(self):
        def pick_astra(body, timeout, key_file):
            self.assertNotIn("astra", body["questions"]["capability"]["criteria"])
            return {"answers": {"capability": {"choice": "astra"}, "effort": {"choice": "low"}}}
        router = self.new_router(pick_astra)
        for mode in ("auto", "shadow"):
            result = router.decide(payload("Plan a security migration", "jev-" + mode),
                                   session_id=mode, native_selection=True, mode_override=mode)
            self.assertEqual(result["proposed_model"], "gpt-6-sol")
            self.assertEqual(result["proposed_effort"], "high")
            self.assertNotEqual(result["model"], "gpt-6-astra")

    def test_astra_allowlist_and_fail_open(self):
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text())
        config["auto_roles"] = ["luna", "terra", "sol", "astra"]
        config_path.write_text(json.dumps(config))
        def pick_astra(body, timeout, key_file):
            self.assertIn("astra", body["questions"]["capability"]["criteria"])
            return {"answers": {"capability": {"choice": "astra"}, "effort": {"choice": "high"}}}
        router = self.new_router(pick_astra)
        auto = router.decide(payload("Review authentication architecture"), session_id="auto",
                             native_selection=True, mode_override="auto")
        self.assertEqual((auto["model"], auto["effort"]), ("gpt-6-astra", "high"))
        shadow = router.decide(payload("Review authentication architecture", "jev-shadow"), session_id="shadow",
                               native_selection=True, mode_override="shadow")
        self.assertEqual((shadow["model"], shadow["proposed_model"]), ("gpt-6-sol", "gpt-6-astra"))
        router.jev_client = lambda *args: (_ for _ in ()).throw(OSError("unavailable"))
        fault = router.decide(payload("Review billing architecture"), session_id="fault",
                              native_selection=True, mode_override="auto")
        self.assertEqual(fault["model"], "gpt-6-sol")
        next_turn = router.decide(payload("Review billing migration"), session_id="auto",
                                  native_selection=True, mode_override="auto")
        self.assertEqual(next_turn["model"], "gpt-6-sol")

    def test_configurable_auto_roles_and_fixed_effort(self):
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text())
        config.update({"auto_roles": ["terra", "sol"], "effort_policy": "fixed", "fixed_effort": "high"})
        config_path.write_text(json.dumps(config))
        def pick_terra(body, timeout, key_file):
            self.assertEqual(list(body["questions"]["capability"]["criteria"]), ["terra", "sol"])
            self.assertNotIn("effort", body["questions"])
            return {"answers": {"capability": {"choice": "terra"}}}
        result = self.new_router(pick_terra).decide(payload("Implement a routine feature"), native_selection=True)
        self.assertEqual((result["model"], result["effort"]), ("gpt-6-terra", "high"))

    def test_versioned_shadow_policy_isolated_from_auto(self):
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text())
        config["shadow_policy"] = "completion_v1"
        config_path.write_text(json.dumps(config))
        task = "Rename a local variable in the test"
        shadow = self.router.decide(payload(task, "jev-shadow"), session_id="shared", native_selection=True)
        self.assertEqual((shadow["model"], shadow["proposed_model"], shadow["policy"]),
                         ("gpt-6-sol", "gpt-6-luna", "completion_v1"))
        self.assertIn("retries, corrections", self.calls[-1]["questions"]["capability"]["instructions"])
        self.assertNotIn("astra", self.calls[-1]["questions"]["capability"]["criteria"])
        auto = self.router.decide(payload(task), session_id="shared", native_selection=True,
                                  mode_override="auto")
        self.assertEqual((auto["model"], auto["policy"], auto["reason"]),
                         ("gpt-6-luna", "baseline", "jev"))
        self.assertIn("least costly role", self.calls[-1]["questions"]["capability"]["instructions"])
        cached_payload = payload("Change another variable")
        cached_payload["cached_input_pct"] = 55
        cached_payload["cache_state"] = "hot"
        cached_payload["cache_age_s"] = 12
        self.router.decide(cached_payload, session_id="cache", native_selection=True,
                           mode_override="shadow")
        self.assertEqual(self.calls[-1]["state"]["cached_input_pct"], 55)
        self.assertEqual((self.calls[-1]["state"]["cache_state"],
                          self.calls[-1]["state"]["cache_age_s"]), ("hot", 12))
        self.router.record_usage(shadow, None, "ok", event="route")
        record = json.loads((self.root / "telemetry.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["policy"], "completion_v1")

    def test_joint_shadow_route_uses_only_supported_pairs_and_preserves_auto(self):
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text())
        config["shadow_policy"] = "completion_v2"
        config_path.write_text(json.dumps(config))
        seen = []
        def choose(body, timeout, key_file):
            seen.append(body)
            return {"answers": {"route": {"choice": "terra:high"}}}
        router = self.new_router(choose)
        request = payload("Implement a bounded feature", "jev-shadow")
        request["requested_effort"] = "high"
        shadow = router.decide(request, session_id="joint", native_selection=True)
        self.assertEqual((shadow["model"], shadow["proposed_model"], shadow["proposed_effort"], shadow["policy"]),
                         ("gpt-6-sol", "gpt-6-terra", "high", "completion_v2"))
        self.assertEqual(set(seen[0]["questions"]), {"route"})
        self.assertEqual(set(seen[0]["questions"]["route"]["criteria"]),
                         {"luna:high", "terra:high", "sol:high"})
        self.assertEqual(seen[0]["state"]["requested_effort"], "high")
        auto = self.router.decide(payload("Rename a test variable"), session_id="auto", native_selection=True)
        self.assertEqual((auto["model"], auto["policy"]), ("gpt-6-luna", "baseline"))

    def test_joint_choice_receipt_is_bounded_and_survives_decision_cache(self):
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text())
        config["shadow_policy"] = "completion_v2"
        config_path.write_text(json.dumps(config))
        calls = []
        def choose(body, timeout, key_file):
            calls.append(body)
            return {"model": "jev-1.13.0", "answers": {"route": {
                "choice": "terra:medium", "confidence": 0.83,
                "probabilities": {"terra:medium": 0.91, "sol:high": 0.09},
                "private": "must not be logged"}}}
        router = self.new_router(choose)
        request = payload("Implement a bounded feature", "jev-shadow")
        first = router.decide(request, session_id="receipt-a", native_selection=True)
        second = router.decide(request, session_id="receipt-b", native_selection=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["jev_confidence"], 0.83)
        self.assertEqual(second["jev_selected_probability"], 0.91)
        self.assertEqual(second["reason"], "shadow_decision_cache")
        router.record_usage(first, None, "ok", event="route")
        record = json.loads((self.root / "telemetry.jsonl").read_text())
        self.assertEqual((record["jev_confidence"], record["jev_selected_probability"], record["jev_model"]),
                         (0.83, 0.91, "jev-1.13.0"))
        self.assertNotIn("private", json.dumps(record))

    def test_malformed_choice_metrics_do_not_change_route_or_reach_telemetry(self):
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text())
        config["auto_policy"] = "completion_v2"
        config_path.write_text(json.dumps(config))
        router = self.new_router(lambda *args: {"model": "untrusted model value", "answers": {"route": {
            "choice": "terra:medium", "confidence": float("nan"),
            "probabilities": {"terra:medium": 5.0}}}})
        decision = router.decide(payload("Implement a bounded feature"), session_id="bad-metrics", native_selection=True)
        self.assertEqual((decision["model"], decision["effort"]), ("gpt-6-terra", "medium"))
        router.record_usage(decision, None, "ok", event="route")
        record = json.loads((self.root / "telemetry.jsonl").read_text())
        self.assertIsNone(record["jev_confidence"])
        self.assertIsNone(record["jev_selected_probability"])
        self.assertIsNone(record["jev_model"])

    def test_joint_shadow_rejects_unavailable_pair_without_execution_change(self):
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text())
        config["shadow_policy"] = "completion_v2"
        config_path.write_text(json.dumps(config))
        router = self.new_router(lambda *args: {"answers": {"route": {"choice": "astra:ultra"}}})
        decision = router.decide(payload("Rename a test variable", "jev-shadow"),
                                 session_id="invalid-joint", native_selection=True)
        self.assertEqual((decision["model"], decision["reason"]),
                         ("gpt-6-sol", "shadow_invalid_decision"))

    def test_joint_auto_keeps_sol_floor_for_large_context_and_selects_effort(self):
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text())
        config["auto_policy"] = "completion_v2"
        config_path.write_text(json.dumps(config))
        seen = []
        def choose(body, timeout, key_file):
            seen.append(body)
            criteria = body["questions"]["route"]["criteria"]
            return {"answers": {"route": {"choice": "sol:high" if "sol:high" in criteria and len(criteria) <= 3 else "luna:low"}}}
        router = self.new_router(choose)
        long_task = payload("Continue the implementation", context_tokens=100_312)
        long_result = router.decide(long_task, session_id="long-task", native_selection=True)
        self.assertEqual((long_result["model"], long_result["effort"], long_result["policy"]),
                         ("gpt-6-sol", "high", "completion_v2"))
        self.assertEqual(set(key.split(":")[0] for key in seen[0]["questions"]["route"]["criteria"]), {"sol"})
        short_result = router.decide(payload("Rename a test variable", context_tokens=500),
                                     session_id="short-task", native_selection=True)
        self.assertEqual((short_result["model"], short_result["effort"]), ("gpt-6-luna", "low"))

    def test_configured_context_floor_allows_medium_session_but_protects_long_session(self):
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text())
        config.update(auto_policy="completion_v2", large_context_sol_floor_tokens=96_000)
        config_path.write_text(json.dumps(config))
        seen = []
        def choose(body, timeout, key_file):
            criteria = body["questions"]["route"]["criteria"]
            seen.append(set(criteria))
            return {"answers": {"route": {"choice": "terra:medium" if "terra:medium" in criteria else "sol:medium"}}}
        router = self.new_router(choose)
        medium = router.decide(payload("Update a bounded parser", context_tokens=70_000),
                               session_id="medium-context", native_selection=True)
        long = router.decide(payload("Update a bounded parser", context_tokens=100_000),
                             session_id="long-context", native_selection=True)
        self.assertEqual((medium["model"], medium["effort"]), ("gpt-6-terra", "medium"))
        self.assertEqual((long["model"], long["effort"]), ("gpt-6-sol", "medium"))
        self.assertIn("terra:medium", seen[0])
        self.assertEqual({choice.split(":")[0] for choice in seen[1]}, {"sol"})

    def test_returning_from_manual_sol_does_not_reuse_stale_luna_lease(self):
        first = self.router.decide(payload("Rename a test variable"),
                                   session_id="manual-return", native_selection=True)
        self.assertEqual(first["model"], "gpt-6-luna")
        next_request = payload("Continue the task", context_tokens=500)
        next_request["current_model"] = "gpt-6-sol"
        after_manual = self.router.decide(next_request, session_id="manual-return", native_selection=True)
        self.assertEqual((after_manual["model"], after_manual["reason"]),
                         ("gpt-6-sol", "lease_hysteresis"))

    def test_live_orders_require_sol_and_high_effort_even_if_jev_underestimates(self):
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text())
        config["auto_policy"] = "completion_v2"
        config_path.write_text(json.dumps(config))
        router = self.new_router(lambda *args: {"answers": {"route": {"choice": "sol:low"}}})
        result = router.decide(payload("Нужно исправить защиту, пока идут реальные заявки"),
                               session_id="live-orders", native_selection=True)
        self.assertEqual((result["model"], result["effort"]), ("gpt-6-sol", "high"))

    def test_manual_effort_filters_auto_candidates_before_jev(self):
        catalog_path = self.root / "catalog.json"
        data = json.loads(catalog_path.read_text())
        for model in data["models"]:
            if model.get("slug") == "gpt-6-sol":
                model["supported_reasoning_levels"].append({"effort": "ultra"})
        catalog_path.write_text(json.dumps(data))
        seen = []
        def choose(body, timeout, key_file):
            seen.append(body)
            return {"answers": {"capability": {"choice": "sol"}}}
        request = payload("Implement a bounded feature")
        request["requested_effort"] = "ultra"
        result = self.new_router(choose).decide(request, session_id="manual-ultra", native_selection=True)
        self.assertEqual(set(seen[0]["questions"]["capability"]["criteria"]), {"sol"})
        self.assertEqual((result["model"], result["effort"]), ("gpt-6-sol", "ultra"))

    def test_shadow_circuit_failure_does_not_disable_auto(self):
        config_path = self.root / "config.json"
        config = json.loads(config_path.read_text())
        config["shadow_policy"] = "completion_v1"
        config_path.write_text(json.dumps(config))
        router = self.new_router(lambda *args: (_ for _ in ()).throw(OSError("down")))
        for index in range(3):
            router.decide(payload(f"Rename a test variable {index}", "jev-shadow"),
                          session_id=f"shadow-{index}", native_selection=True)
        self.assertGreater(router._open_until["completion_v1"], 0)
        self.assertEqual(router._open_until["baseline"], 0)
        router.jev_client = self.jev
        auto = router.decide(payload("Rename another test variable"), session_id="auto",
                             native_selection=True, mode_override="auto")
        self.assertEqual((auto["reason"], auto["model"]), ("jev", "gpt-6-luna"))

    def test_native_selection_and_proxy_gate(self):
        p = payload("Write a short greeting")
        native = self.router.decide(p, session_id="native", native_selection=True)
        self.assertEqual((native["model"], native["effort"]), ("gpt-6-luna", "low"))
        proxy = self.router.decide(p, session_id="proxy")
        self.assertEqual(proxy["model"], "gpt-6-sol")
        self.assertEqual(proxy["proposed_model"], "gpt-6-luna")
        self.assertEqual(proxy["reason"], "requires_native_model_selection")

    def test_capability_floor_and_effort_clamp(self):
        for high_risk in ("Review vulnerabilities", "Plan database migrations", "Check authentication", "Design architecture"):
            self.assertEqual(_floor(high_risk), "astra")
        result = self.router.decide(payload("Review a security vulnerability in authentication"), native_selection=True)
        self.assertEqual(result["model"], "gpt-6-sol")
        self.assertEqual(result["effort"], "high")
        self.assertEqual(result["reason"], "invalid_decision")
        self.assertEqual(list(self.calls[0]["questions"]["capability"]["criteria"]), ["sol"])
        (self.root / "catalog.json").write_text(json.dumps({"models": [dict(x, supported_reasoning_levels=[{"effort": "medium"}]) for x in catalog()["models"]]}))
        self.router = self.new_router()
        result = self.router.decide(payload("Write a short greeting"), session_id="effort", native_selection=True)
        self.assertEqual(result["effort"], "medium")

    def test_lease_same_turn_and_hysteresis(self):
        def pick_sol(body, timeout, key_file):
            return {"answers": {"capability": {"choice": "sol"}, "effort": {"choice": "high"}}}
        r = self.new_router(pick_sol)
        first = r.decide(payload("Investigate failing build"), session_id="s", native_selection=True)
        self.assertEqual(first["model"], "gpt-6-sol")
        same = r.decide({"model": "jev-auto", "input": [{"role": "tool", "content": "some result"}]}, session_id="s", native_selection=True)
        self.assertEqual(same["reason"], "lease")
        r.jev_client = self.jev
        second = r.decide(payload("Write a short greeting 2"), session_id="s", native_selection=True)
        self.assertEqual((second["model"], second["reason"]), ("gpt-6-sol", "lease_hysteresis"))
        third = r.decide(payload("Write a short greeting 3"), session_id="s", native_selection=True)
        self.assertEqual(third["model"], "gpt-6-luna")

    def test_shadow_off_and_circuit_fail_open(self):
        p = payload("Write a short greeting", "jev-shadow")
        shadow = self.router.decide(p, session_id="shadow", native_selection=True)
        self.assertEqual(shadow["model"], "gpt-6-sol")
        self.assertEqual(shadow["proposed_model"], "gpt-6-luna")
        off = self.router.decide(payload("Write a short greeting"), mode_override="off", session_id="off")
        self.assertEqual(off["model"], "gpt-6-sol")
        self.assertEqual(off["reason"], "off")
        self.assertEqual(len(self.calls), 1)

        def fail(body, timeout, key_file):
            raise RuntimeError("secret response payload")
        r = self.new_router(fail)
        for i in range(3):
            d = r.decide(payload("Fix parser error " + str(i)), session_id=str(i), native_selection=True)
            self.assertEqual(d["model"], "gpt-6-sol")
            self.assertEqual(d["reason"], "jev_error")
        d = r.decide(payload("Fix parser error 4"), session_id="4", native_selection=True)
        self.assertEqual(d["reason"], "circuit_open")

    def test_missing_catalog_and_no_cross_session_lease(self):
        (self.root / "catalog.json").write_text("{}")
        manual = self.router.decide({"model": "gpt-5.6-sol", "reasoning": {"effort": "ultra"}})
        self.assertEqual((manual["model"], manual["effort"]), ("gpt-5.6-sol", "ultra"))
        alias = self.router.decide(payload("Fix parser"))
        self.assertIsNone(alias["model"])
        self.assertEqual(alias["reason"], "cannot_route")
        (self.root / "config.json").write_text(json.dumps({"mode": "auto", "fallback_model": "gpt-6-sol"}))
        self.assertEqual(self.router.decide(payload("Fix parser"))["model"], "gpt-6-sol")
        (self.root / "catalog.json").write_text(json.dumps(catalog()))
        first = self.router.decide(payload("Write a short greeting"), native_selection=True)
        second = self.router.decide(payload("Write a short greeting"), native_selection=True)
        self.assertNotEqual(first["session"], second["session"])
        self.assertNotEqual(second["reason"], "lease")

    def test_stronger_lease_survives_jev_fault(self):
        def pick_sol(body, timeout, key_file):
            return {"answers": {"capability": {"choice": "sol"}, "effort": {"choice": "high"}}}
        r = self.new_router(pick_sol)
        d = r.decide(payload("Investigate failing build"), session_id="s", native_selection=True)
        self.assertEqual((d["model"], d["effort"]), ("gpt-6-sol", "high"))

        def fail(body, timeout, key_file):
            raise RuntimeError("private remote response")
        r.jev_client = fail
        for i in range(1, 4):
            d = r.decide(payload("Write a short greeting " + str(i)), session_id="s", native_selection=True)
            self.assertEqual((d["model"], d["effort"]), ("gpt-6-sol", "high"))

    def test_unknown_context_suppresses_downgrade_and_missing_usage(self):
        def pick_sol(body, timeout, key_file):
            return {"answers": {"capability": {"choice": "sol"}, "effort": {"choice": "high"}}}
        r = self.new_router(pick_sol)
        r.decide(payload("Investigate failing build"), session_id="s", native_selection=True)
        r.jev_client = self.jev
        for i in range(1, 4):
            p = payload("Write a short greeting " + str(i))
            p.pop("context_tokens")
            d = r.decide(p, session_id="s", native_selection=True)
            self.assertEqual(d["model"], "gpt-6-sol")
        r.record_usage(d, None, "ok")
        line = json.loads((self.root / "telemetry.jsonl").read_text())
        self.assertTrue(line["usage_missing"])
        self.assertIsNone(line["input_tokens"])
        self.assertIsNone(line["cached_input_tokens"])
        self.assertIsNone(line["context_tokens"])
        self.assertIn("router_ms", line)

    def test_catalog_visibility_and_telemetry_allowlist(self):
        roles = visible_roles(catalog())
        self.assertEqual(set(roles), {"luna", "terra", "sol", "astra"})
        d = self.router.decide(payload("Write a short greeting"), native_selection=True)
        d["secret"] = "never-log-this"
        d.update({"prior_failed": True, "manual_override": True, "command_failures": 2,
                  "command_output": "private source"})
        self.router.record_usage(d, {"input_tokens": 100, "output_tokens": 20,
                                     "input_tokens_details": {"cached_tokens": 40}, "raw": "private"}, "ok")
        line = (self.root / "telemetry.jsonl").read_text()
        self.assertNotIn("never-log-this", line)
        self.assertNotIn("private", line)
        self.assertEqual(json.loads(line)["cached_input_tokens"], 40)
        self.assertEqual(json.loads(line)["event"], "usage")
        self.assertEqual(json.loads(line)["command_failures"], 2)
        self.assertEqual(oct(os.stat(self.root / "telemetry.jsonl").st_mode & 0o777), "0o600")

    def test_route_event_is_allowlisted_and_distinct(self):
        d = self.router.decide(payload("Write a short greeting"), native_selection=True)
        d.update({"client": "cli", "secret": "private-prompt"})
        self.router.record_usage(d, None, "ok", event="route")
        line = (self.root / "telemetry.jsonl").read_text()
        self.assertNotIn("private-prompt", line)
        parsed = json.loads(line)
        self.assertEqual((parsed["event"], parsed["client"]), ("route", "cli"))


if __name__ == "__main__":
    unittest.main()
