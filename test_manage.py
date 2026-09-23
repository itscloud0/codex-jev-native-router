import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import manage


class ConfigSurgeryTests(unittest.TestCase):
    def test_round_trip_preserves_unrelated_changes(self):
        original = 'model = "gpt-6-astra"\n# comment\n[features]\nsearch = true\n'
        before = {key: manage.root_fields(original).get(key) for key in manage.MANAGED_KEYS}
        managed = {"model": 'model = "jev-shadow"\n', "openai_base_url": 'openai_base_url = "http://127.0.0.1"\n'}
        changed = manage.edit_root(original, before, managed)
        changed = changed.replace("search = true", "search = false")
        restored = manage.edit_root(changed, managed, before)
        self.assertEqual(restored, original.replace("search = true", "search = false"))

    def test_conflicting_managed_value_refused(self):
        original = 'model = "gpt-6-astra"\n[features]\nsearch = true\n'
        with self.assertRaisesRegex(ValueError, "config changed at model"):
            manage.edit_root(original, {"model": 'model = "jev-shadow"\n'}, {"model": 'model = "gpt-6-astra"\n'})


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "router"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.codex = self.bin / "codex"
        self.real = self.base / "real-codex"
        self.real.write_text("binary")
        self.real.chmod(0o700)
        self.codex.symlink_to(self.real)
        self.config = self.base / "config.toml"
        self.config.write_text('model = "gpt-6-astra"\n# owner text\n[features]\nsearch = true\n')
        self.cache = self.base / "models_cache.json"
        sol = {"slug": "gpt-6-sol", "display_name": "Sol", "visibility": "list", "supported_reasoning_levels": [{"effort": "medium"}]}
        self.cache.write_text(json.dumps({"models": [sol, {"slug": "gpt-6-astra", "visibility": "list", "supported_reasoning_levels": [{"effort": "medium"}]}]}))
        self.agent = self.base / "LaunchAgent.plist"
        self.key = self.base / "typesafe-key"
        self.key.write_text("test-only")
        self.key.chmod(0o600)

    def install(self):
        manage.install(self.root, self.config, self.bin, self.cache, self.agent, start=False, key_path=self.key)

    def test_install_disable_enable_rollback(self):
        self.install()
        self.assertEqual(os.readlink(self.codex), str(self.root / "jev-codex"))
        self.assertEqual(os.readlink(self.bin / "codex-native"), str(self.root / "codex-native"))
        self.assertEqual((self.root / "config.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / "capability").stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list((self.root / "backups").glob("*.toml"))), 1)
        self.assertIn('model = "jev-shadow"', self.config.read_text())
        self.assertEqual(manage.load_json(self.root / "config.json")["fallback_model"], "gpt-6-sol")
        self.assertEqual(manage.load_json(self.root / "config.json")["auto_roles"], ["luna", "terra", "sol"])
        manage.disable(self.root, stop=False)
        self.assertNotIn("openai_base_url", self.config.read_text())
        self.assertIn('model = "gpt-6-astra"', self.config.read_text())
        manage.enable(self.root, start=False)
        self.config.write_text(self.config.read_text().replace("search = true", "search = false"))
        manage.rollback(self.root, stop=False)
        self.assertEqual(os.readlink(self.codex), str(self.real))
        self.assertIn("search = false", self.config.read_text())
        self.assertNotIn("openai_base_url", self.config.read_text())

    def test_desktop_runtime_distinguishes_direct_and_adapted_app_server(self):
        sample = """10 1 /Applications/ChatGPT.app/Contents/MacOS/ChatGPT
11 10 /Applications/ChatGPT.app/Contents/Resources/codex -c x=y app-server
12 10 /opt/homebrew/bin/python3.14 /tmp/rpc_adapter.py --native /Applications/ChatGPT.app/Contents/Resources/codex --root /tmp -- -c x=y app-server
13 12 /Applications/ChatGPT.app/Contents/Resources/codex -c x=y app-server
14 13 /opt/homebrew/bin/node_repl
15 14 /opt/homebrew/bin/python3.14 /tmp/rpc_adapter.py --native /Applications/ChatGPT.app/Contents/Resources/codex --root /tmp -- app-server
"""
        with mock.patch.object(manage.subprocess, "run", return_value=mock.Mock(stdout=sample)):
            self.assertEqual(manage.desktop_runtime(), {"app_running": True,
                                                       "adapter_active": True,
                                                       "direct_native_app_server": True})
        sample = "\n".join(line for line in sample.splitlines() if not line.startswith("11 "))
        with mock.patch.object(manage.subprocess, "run", return_value=mock.Mock(stdout=sample)):
            self.assertFalse(manage.desktop_runtime()["direct_native_app_server"])
        sample = "\n".join(line for line in sample.splitlines() if not line.startswith("12 "))
        with mock.patch.object(manage.subprocess, "run", return_value=mock.Mock(stdout=sample)):
            self.assertFalse(manage.desktop_runtime()["adapter_active"])

    def test_rollback_preserves_manual_model(self):
        self.install()
        self.config.write_text(self.config.read_text().replace('model = "jev-shadow"', 'model = "custom"'))
        manage.rollback(self.root, stop=False)
        self.assertIn('model = "custom"', self.config.read_text())
        self.assertNotIn("openai_base_url", self.config.read_text())
        self.assertEqual(manage.load_json(self.root / "manifest.json")["preserved_user_changes"], ["model"])

    def test_cli_astra_requires_allowlist(self):
        self.install()
        import core
        decision = {"model": "gpt-6-astra", "effort": "high", "mode": "auto", "reason": "jev"}
        with mock.patch.object(manage, "health", return_value=True), mock.patch.object(core.Router, "decide", side_effect=lambda *args, **kwargs: dict(decision)):
            disabled = manage.cli_args(["exec", "Review architecture"], self.root)
            self.assertEqual(disabled[disabled.index("-m") + 1], "gpt-6-sol")
            config_path = self.root / "config.json"
            config = json.loads(config_path.read_text())
            config["auto_roles"].append("astra")
            config_path.write_text(json.dumps(config))
            enabled = manage.cli_args(["exec", "Review architecture"], self.root)
            self.assertEqual(enabled[enabled.index("-m") + 1], "gpt-6-astra")

    def test_wrapper_routes_only_initial_prompt_and_preserves_overrides(self):
        self.install()
        with mock.patch.object(manage, "health", return_value=True):
            import core
            with mock.patch.object(core.Router, "decide", return_value={"model": "gpt-6-astra", "effort": "high"}) as decide:
                args = manage.cli_args(["exec", "--json", "Fix tests"], self.root, stdin_tty=False)
                self.assertEqual(args[0], str(self.real))
                self.assertEqual(args[1], "-c")
                self.assertTrue(args[2].startswith('openai_base_url="http://127.0.0.1:43191/'))
                self.assertTrue(args[2].endswith('/cli"'))
                self.assertEqual(args[3:7], ["-m", "gpt-6-sol", "-c", 'model_reasoning_effort="high"'])
                self.assertIn("Fix tests", args)
                self.assertEqual(decide.call_args.kwargs["mode_override"], "auto")
                self.assertEqual(decide.call_args.kwargs["native_selection"], True)
            with mock.patch.object(core.Router, "decide", return_value={"model": "gpt-6-sol", "effort": "low"}) as decide:
                args = manage.cli_args(["--jev-auto", "-c", "model_reasoning_effort=high", "exec", "Fix tests"], self.root)
                self.assertEqual(args.count("model_reasoning_effort=high"), 1)
                self.assertIn("gpt-6-sol", args)
                decide.assert_called_once()
            self.assertEqual(manage.cli_args(["-m", "gpt-6-sol", "exec", "Fix"], self.root)[-4:], ["-m", "gpt-6-sol", "exec", "Fix"])
            self.assertEqual(manage.cli_args(["-c", 'model="gpt-6-sol"', "exec", "Fix"], self.root)[-4:], ["-c", 'model="gpt-6-sol"', "exec", "Fix"])
            self.assertEqual(manage.cli_args(["exec", "resume", "id", "continue"], self.root)[-4:], ["exec", "resume", "id", "continue"])
            self.assertEqual(manage.cli_args(["login"], self.root), [str(self.real), "login"])
            self.assertEqual(manage.cli_args(["--help"], self.root), [str(self.real), "--help"])
            self.assertEqual(manage.cli_args(["-c", 'openai_base_url="https://custom.example"', "exec", "Fix"], self.root),
                             [str(self.real), "-c", 'openai_base_url="https://custom.example"', "exec", "Fix"])

    def test_stdin_and_daemon_failure(self):
        self.install()
        bypass = str(self.bin / "codex-native")
        with mock.patch.object(manage, "health", return_value=False):
            args = manage.cli_args(["exec", "-"], self.root, stdin_tty=False)
            self.assertEqual(args, [bypass, "-m", "gpt-6-sol", "exec", "-"])
            args = manage.cli_args(["exec", "Fix"], self.root)
            self.assertEqual(args[:3], [bypass, "-m", "gpt-6-sol"])
            self.assertEqual(manage.cli_args(["--jev-off", "exec", "Fix"], self.root), [bypass, "exec", "Fix"])
            self.assertEqual(manage.cli_args(["-c", 'openai_base_url="https://custom.example"', "exec", "Fix"], self.root),
                             [str(self.real), "-c", 'openai_base_url="https://custom.example"', "exec", "Fix"])
        native = manage.native_args(["exec", "Fix"], self.root)
        self.assertEqual(native[0], str(self.real))
        self.assertIn('openai_base_url="https://chatgpt.com/backend-api/codex"', native)
        self.assertEqual(native[-4:], ["-m", "gpt-6-sol", "exec", "Fix"])
        native = manage.native_args(["-c", "model_reasoning_effort=high", "exec", "Fix"], self.root)
        self.assertEqual(native[5:7], ["-m", "gpt-6-sol"])

    def test_startup_failure_restores_config_and_symlink(self):
        with mock.patch.object(manage, "start_agent", side_effect=RuntimeError("launch failed")), mock.patch.object(manage, "stop_agent"):
            with self.assertRaisesRegex(RuntimeError, "launch failed"):
                manage.install(self.root, self.config, self.bin, self.cache, self.agent, start=True, key_path=self.key)
        self.assertEqual(os.readlink(self.codex), str(self.real))
        self.assertNotIn("openai_base_url", self.config.read_text())
        self.assertFalse(self.agent.exists())
        self.assertEqual(manage.load_json(self.root / "manifest.json")["config_state"], "failed")

    def test_separate_execution_binary_preserves_original_link(self):
        bundled = self.base / "bundled-codex"
        bundled.write_text("binary")
        bundled.chmod(0o700)
        manage.install(self.root, self.config, self.bin, self.cache, self.agent, start=False,
                       key_path=self.key, execution_binary=bundled)
        manifest = manage.load_json(self.root / "manifest.json")
        self.assertEqual(manifest["native_target"], str(bundled))
        self.assertEqual(manifest["original_codex_link"], str(self.real))
        self.assertEqual(manage.native_args(["exec", "Fix"], self.root)[0], str(bundled))
        manage.rollback(self.root, stop=False)
        self.assertEqual(os.readlink(self.codex), str(self.real))

    def mock_desktop_launchctl(self, initial):
        current = {"value": initial}
        calls = []

        def launchctl(*args):
            calls.append(args)
            if args[:2] == ("setenv", "CODEX_CLI_PATH"):
                current["value"] = args[2]
            elif args == ("unsetenv", "CODEX_CLI_PATH"):
                current["value"] = None

        self.enterContext(mock.patch.object(manage, "desktop_env", side_effect=lambda: current["value"]))
        self.enterContext(mock.patch.object(manage, "launchctl", side_effect=launchctl))
        start = self.enterContext(mock.patch.object(manage, "start_desktop_agent"))
        stop = self.enterContext(mock.patch.object(manage, "stop_desktop_agent"))
        return current, calls, start, stop

    def test_desktop_opt_in_disable_enable_rollback(self):
        self.install()
        installed_config = self.config.read_bytes()
        desktop_agent = self.base / "desktop-env.plist"
        prior = "/user/other-codex"
        current, calls, start, stop = self.mock_desktop_launchctl(prior)
        manage.desktop_enable(self.root, desktop_agent, self.real)
        wrapper = self.root / "app-server-wrapper"
        manifest = manage.load_json(self.root / "manifest.json")
        self.assertEqual(current["value"], str(wrapper))
        self.assertEqual(manifest["desktop"]["previous_env"], prior)
        self.assertTrue(desktop_agent.exists())
        self.assertTrue(wrapper.stat().st_mode & 0o100)
        self.assertIn("rpc_adapter.py", wrapper.read_text())
        self.assertIn("--native", wrapper.read_text())
        self.assertIn("--root", wrapper.read_text())
        self.assertIn('"$@"', wrapper.read_text())
        start.assert_called_once_with(desktop_agent)

        manage.disable(self.root, stop=False)
        self.assertEqual(current["value"], prior)
        self.assertFalse(desktop_agent.exists())
        self.assertEqual(stop.call_count, 1)
        self.assertFalse(manage.load_json(self.root / "manifest.json")["desktop"]["enabled"])

        manage.enable(self.root, start=False)
        self.assertEqual(self.config.read_bytes(), installed_config)
        self.assertEqual(current["value"], str(wrapper))
        self.assertTrue(desktop_agent.exists())
        self.assertTrue(manage.load_json(self.root / "manifest.json")["desktop"]["enabled"])
        manage.rollback(self.root, stop=False)
        self.assertEqual(current["value"], prior)
        self.assertFalse(wrapper.exists())
        self.assertFalse(desktop_agent.exists())
        self.assertEqual(os.readlink(self.codex), str(self.real))
        self.assertEqual([call[0] for call in calls if call[0] in ("setenv", "unsetenv")],
                         ["setenv", "setenv", "setenv", "setenv"])

    def test_desktop_wrapper_falls_back_when_adapter_runtime_missing(self):
        native = self.base / "native-fallback"
        native.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        native.chmod(0o700)
        wrapper = self.base / "desktop-wrapper"
        wrapper.write_bytes(manage.desktop_wrapper_content(self.root, native, self.base / "missing-python"))
        wrapper.chmod(0o700)
        result = subprocess.run([str(wrapper), "-c", "features.code_mode_host=true", "app-server"],
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ["-c", 'openai_base_url="https://chatgpt.com/backend-api/codex"',
                                                       "-c", "features.code_mode_host=true", "app-server"])

    def test_desktop_enable_failure_recovers_prior_env_from_journal(self):
        self.install()
        desktop_agent = self.base / "desktop-env.plist"
        prior = "/user/custom-codex  "
        current, _, start, _ = self.mock_desktop_launchctl(prior)

        def partial_start(_):
            current["value"] = str(self.root / "app-server-wrapper")
            prepared = manage.load_json(self.root / "manifest.json")["desktop"]
            self.assertEqual(prepared["phase"], "prepared")
            self.assertEqual(prepared["previous_env"], prior)
            raise RuntimeError("partial bootstrap")

        start.side_effect = partial_start
        with self.assertRaisesRegex(RuntimeError, "partial bootstrap"):
            manage.desktop_enable(self.root, desktop_agent, self.real)
        self.assertEqual(current["value"], prior)
        self.assertFalse(desktop_agent.exists())
        self.assertFalse((self.root / "app-server-wrapper").exists())
        desktop = manage.load_json(self.root / "manifest.json")["desktop"]
        self.assertEqual(desktop["phase"], "disabled")
        self.assertFalse(desktop["opted_in"])

    def test_desktop_disable_preserves_externally_changed_env(self):
        self.install()
        desktop_agent = self.base / "desktop-env.plist"
        current, calls, _, _ = self.mock_desktop_launchctl(None)
        manage.desktop_enable(self.root, desktop_agent, self.real)
        current["value"] = "/user/new-codex"
        manage.disable(self.root, stop=False)
        self.assertEqual(current["value"], "/user/new-codex")
        self.assertFalse(desktop_agent.exists())
        self.assertTrue(manage.load_json(self.root / "manifest.json")["desktop"]["env_changed_externally"])
        self.assertNotIn(("unsetenv", "CODEX_CLI_PATH"), calls)
        manage.enable(self.root, start=False)
        self.assertEqual(current["value"], "/user/new-codex")
        self.assertFalse(desktop_agent.exists())
        manage.rollback(self.root, stop=False)
        self.assertEqual(current["value"], "/user/new-codex")

    def test_desktop_disable_keeps_router_enabled(self):
        self.install()
        desktop_agent = self.base / "desktop-env.plist"
        current, _, _, _ = self.mock_desktop_launchctl(None)
        manage.desktop_enable(self.root, desktop_agent, self.real)
        manage.desktop_disable(self.root)
        self.assertIsNone(current["value"])
        self.assertEqual(manage.load_json(self.root / "manifest.json")["config_state"], "enabled")
        self.assertIn('model = "jev-shadow"', self.config.read_text())

    def test_desktop_enable_refuses_existing_setter(self):
        self.install()
        desktop_agent = self.base / "desktop-env.plist"
        desktop_agent.write_text("user data")
        current, calls, _, _ = self.mock_desktop_launchctl("/user/other-codex")
        with self.assertRaisesRegex(ValueError, "already exists"):
            manage.desktop_enable(self.root, desktop_agent, self.real)
        self.assertEqual(desktop_agent.read_text(), "user data")
        self.assertEqual(current["value"], "/user/other-codex")
        self.assertEqual(calls, [])

    def test_report_uses_supplied_weights_only(self):
        self.install()
        telemetry = self.root / "state/telemetry.jsonl"
        telemetry.write_text(json.dumps({"event": "route", "client": "cli", "model": "gpt-6-sol",
                                         "proposed_model": "gpt-6-astra", "jev_ms": 25,
                                         "switched": True, "status": "ok"}) + "\n" +
                             json.dumps({"event": "usage", "client": "cli", "model": "gpt-6-sol",
                                         "effort": "medium", "input_tokens": 100,
                                         "cached_input_tokens": 20, "output_tokens": 10,
                                         "usage_missing": False, "status": "ok"}) + "\n")
        no_weights = manage.report(self.root)
        self.assertIsNone(no_weights["counterfactual"]["actual_units"])
        self.assertEqual(no_weights["observed"]["calls"], 1)
        self.assertEqual(no_weights["observed"]["by_client"]["cli"]["input_tokens"], 100)
        self.assertEqual(no_weights["routes"], {"decisions": 1, "switches": 1, "jev_ms": 25,
                                                 "proposed_models": {"gpt-6-astra": 1},
                                                 "by_policy": {"unknown": {"decisions": 1,
                                                                            "proposed_models": {"gpt-6-astra": 1}}}})
        weights = {"gpt-6-sol": {"input": 1, "cached_input": 0.5, "output": 2},
                   "gpt-6-astra": {"input": 2, "cached_input": 1, "output": 4}}
        result = manage.report(self.root, weights)
        self.assertEqual(result["counterfactual"]["actual_units"], 110)
        self.assertEqual(result["counterfactual"]["all_astra_units"], 220)


if __name__ == "__main__":
    unittest.main()
