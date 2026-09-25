#!/usr/bin/env python3
"""Desktop app-server stdio adapter. Native Codex remains the only executor."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from core import ALIASES, Router, visible_roles

MAX_FRAME = 8 * 1024 * 1024
MAX_PENDING = 1024
MAX_THREADS = 500
MAX_AGE = 30 * 86400


def _key(thread_id: str) -> str:
    return hashlib.sha256(thread_id.encode("utf-8", "replace")).hexdigest()[:32]


def _request_id(message: dict) -> str | None:
    value = message.get("id")
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return json.dumps(value, separators=(",", ":"))[:256]
    return None


def _encode(message: dict) -> bytes:
    return (json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _decode(raw: bytes) -> dict | None:
    if len(raw) > MAX_FRAME:
        return None
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (ValueError, UnicodeError):
        return None


def _alias(model: Any) -> str | None:
    return model if isinstance(model, str) and model in ALIASES else None


def _custom_provider(params: dict) -> bool:
    config = params.get("config")
    if not isinstance(config, dict):
        config = {}
    for item in (params, config):
        provider = item.get("modelProvider") or item.get("model_provider") or item.get("model_provider_id")
        if isinstance(provider, str) and provider.lower() not in ("openai", ""):
            return True
        if any(item.get(key) for key in ("profile", "openai_base_url", "model_catalog_json")):
            return True
    return False


class IntentStore:
    """Bounded cross-process routing intent; never stores prompts or raw thread IDs."""

    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict:
        try:
            if self.path.stat().st_size > 256_000:
                return {}
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict):
                return {}
            now = time.time()
            return {key: value for key, value in data.items()
                    if re.fullmatch(r"[a-f0-9]{32}", key) and isinstance(value, dict)
                    and value.get("alias") in ALIASES
                    and (value.get("actual") is None or isinstance(value.get("actual"), str))
                    and (value.get("effort_override") is None or value.get("effort_override") in ("low", "medium", "high", "xhigh", "max", "ultra"))
                    and (value.get("context") is None or (isinstance(value.get("context"), int) and not isinstance(value.get("context"), bool) and 0 <= value["context"] <= 1_000_000_000))
                    and isinstance(value.get("updated"), (int, float))
                    and 0 <= now - value["updated"] <= MAX_AGE}
        except (OSError, ValueError):
            return {}

    def get(self, thread_id: str) -> dict | None:
        if not isinstance(thread_id, str) or not thread_id or len(thread_id) > 1024:
            return None
        return self._read().get(_key(thread_id))

    def update(self, thread_id: str, *, alias: str | None = None,
               clear: bool = False, context: int | None = None, failed: bool | None = None,
               actual: str | None = None, effort: str | None = None,
               conservative: bool | None = None, effort_override: str | None = None,
               clear_effort_override: bool = False) -> None:
        if not isinstance(thread_id, str) or not thread_id or len(thread_id) > 1024:
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        lock_path = self.path.with_suffix(".lock")
        with lock_path.open("a+") as lock:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self._read()
            key = _key(thread_id)
            if clear:
                data.pop(key, None)
            elif alias in ALIASES or key in data:
                entry = data.get(key, {"alias": alias})
                if alias in ALIASES:
                    if entry.get("alias") != alias:
                        entry.pop("effort_override", None)
                    entry["alias"] = alias
                if isinstance(context, int) and not isinstance(context, bool):
                    entry["context"] = max(0, min(context, 1_000_000_000))
                if failed is not None:
                    entry["failed"] = failed is True
                if isinstance(actual, str) and re.fullmatch(r"gpt-\d+(?:\.\d+)*-[a-z0-9]+", actual):
                    entry["actual"] = actual[:64]
                if effort in ("low", "medium", "high", "xhigh", "max", "ultra"):
                    entry["effort"] = effort
                if clear_effort_override:
                    entry.pop("effort_override", None)
                elif effort_override in ("low", "medium", "high", "xhigh", "max", "ultra"):
                    entry["effort_override"] = effort_override
                if conservative is not None:
                    entry["conservative"] = conservative is True
                entry["updated"] = time.time()
                data[key] = entry
            if len(data) > MAX_THREADS:
                data = dict(sorted(data.items(), key=lambda item: item[1]["updated"])[-MAX_THREADS:])
            tmp = self.path.with_name(self.path.name + "." + str(os.getpid()) + ".tmp")
            try:
                with tmp.open("w") as out:
                    os.chmod(tmp, 0o600)
                    json.dump(data, out, separators=(",", ":"))
                os.replace(tmp, self.path)
            finally:
                tmp.unlink(missing_ok=True)
            fcntl.flock(lock, fcntl.LOCK_UN)


class Adapter:
    def __init__(self, root: Path, router: Router | None = None, store: IntentStore | None = None):
        self.root = root
        self.router = router or Router(root / "config.json", root / "native-models.json",
                                       root / "state/leases.json", root / "state/telemetry.jsonl")
        self.store = store or IntentStore(root / "state/desktop-intent.json")
        self.pending: dict[str, dict] = {}
        self.active: set[str] = set()
        self.actual: dict[str, tuple[str, str]] = {}
        self.last_context: dict[str, int] = {}
        self.last_cache_pct: dict[str, tuple[int, float]] = {}
        self.turn_usage: dict[tuple[str, str], dict] = {}
        self.turn_routes: dict[str, str] = {}
        self.turn_signals: dict[str, dict] = {}
        self.pending_override: dict[str, bool] = {}

    @staticmethod
    def _remember(mapping: dict, key: str, value: Any) -> None:
        mapping.pop(key, None)
        mapping[key] = value
        if len(mapping) > MAX_THREADS:
            mapping.pop(next(iter(mapping)))

    def _sol(self) -> str:
        try:
            roles = visible_roles(self.router._catalog())
            if "sol" in roles:
                return roles["sol"]["slug"]
            config = self.router._config()
            value = config.get("fallback_model")
            if isinstance(value, str) and re.fullmatch(r"gpt-\d+(?:\.\d+)*-sol", value):
                return value
        except Exception:
            pass
        return "gpt-6-sol"

    def _mask_thread_model(self, thread: Any) -> bool:
        if not isinstance(thread, dict):
            return False
        thread_id = thread.get("id")
        entry = self.store.get(thread_id) if isinstance(thread_id, str) else None
        if not entry or entry.get("alias") not in ALIASES:
            return False
        if isinstance(thread.get("model"), str) and thread["model"].startswith("gpt-"):
            thread["model"] = entry["alias"]
            return True
        return False

    def _route(self, alias: str, params: dict, thread_id: str, saved: dict | None) -> tuple[str, str, dict | None]:
        # Only user text enters the policy. All other input types stay in the native request.
        parts = [item["text"] for item in params.get("input", [])
                 if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)] if isinstance(params.get("input"), list) else []
        text = "\n".join(parts)[:20_001]
        if saved and saved.get("failed"):
            text = "Previous turn failure. " + text
        payload = {"model": alias, "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}]}
        current = self.actual.get(thread_id)
        if current and isinstance(current[0], str):
            payload["current_model"] = current[0]
        context = self.last_context.get(thread_id)
        if context is None and saved:
            context = saved.get("context")
        if isinstance(context, int):
            payload["context_tokens"] = context
        cached_sample = self.last_cache_pct.get(thread_id)
        if isinstance(cached_sample, tuple) and len(cached_sample) == 2:
            cached_pct, observed_at = cached_sample
            age = time.monotonic() - observed_at
            if isinstance(cached_pct, int) and 0 <= age <= 600:
                payload["cached_input_pct"] = cached_pct
                payload["cache_state"] = "hot" if cached_pct > 0 else "warming"
                payload["cache_age_s"] = int(age)
        try:
            decision = self.router.decide(payload, client="desktop", session_id=thread_id,
                                          native_selection=True, mode_override="auto" if alias == "jev-auto" else "shadow")
            model, effort = decision.get("model"), decision.get("effort")
            if isinstance(model, str) and re.fullmatch(r"gpt-\d+(?:\.\d+)*-[a-z0-9]+", model) and isinstance(effort, str):
                configured_roles = self.router._config().get("auto_roles")
                astra_allowed = (isinstance(configured_roles, list) and "sol" in configured_roles
                                 and all(role in ("luna", "terra", "sol", "astra") for role in configured_roles)
                                 and "astra" in configured_roles)
                if model.endswith("-astra") and not astra_allowed:
                    model, effort = self._sol(), "high"
                if saved and saved.get("conservative") and context is None:
                    previous = saved.get("actual")
                    if model.endswith(("-luna", "-terra")) or (isinstance(previous, str) and previous.endswith("-astra")):
                        model, effort = self._sol(), "medium"
                    decision = {**decision, "model": model, "effort": effort, "reason": "lease_hysteresis"}
                decision = {**decision, "model": model, "effort": effort}
                decision["route_id"] = os.urandom(12).hex()
                self._remember(self.turn_routes, thread_id, decision["route_id"])
                self.router.record_usage(decision, None, "ok", event="route")
                return model, effort, decision
        except Exception:
            pass
        previous = self.actual.get(thread_id)
        if not previous and saved and isinstance(saved.get("actual"), str):
            previous = (saved["actual"], saved.get("effort") or "medium")
        if previous and previous[0].endswith("-sol"):
            return previous[0], "medium", None
        return self._sol(), "medium", None

    def _enabled(self) -> bool:
        try:
            if self.router._config().get("mode") == "off":
                return False
            manifest = self.root / "manifest.json"
            if manifest.exists() and json.loads(manifest.read_text()).get("config_state") != "enabled":
                return False
        except (OSError, ValueError, AttributeError):
            return False
        return True

    def client(self, raw: bytes) -> bytes:
        message = _decode(raw)
        if not message or not isinstance(message.get("method"), str) or not isinstance(message.get("params"), dict):
            return raw
        method, params = message["method"], message["params"]
        if method not in ("thread/start", "thread/resume", "thread/fork", "thread/settings/update",
                          "thread/read", "thread/list", "turn/start"):
            return raw
        if method in ("thread/read", "thread/list"):
            rid = _request_id(message)
            if rid and len(self.pending) < MAX_PENDING:
                self.pending[rid] = {"method": method, "thread": None, "alias": None}
            return raw
        rid = _request_id(message)
        thread_id = params.get("threadId") if isinstance(params.get("threadId"), str) else None
        saved = self.store.get(thread_id) if thread_id else None
        top_model = params.get("model")
        settings = params.get("collaborationMode", {}).get("settings") if isinstance(params.get("collaborationMode"), dict) else None
        collab_model = settings.get("model") if isinstance(settings, dict) else None
        config = params.get("config") if isinstance(params.get("config"), dict) else {}
        config_model = config.get("model") if isinstance(config.get("model"), str) else None
        # Collaboration settings win in native Codex. Any concrete selection is manual.
        explicit = config_model or (collab_model if isinstance(collab_model, str) else top_model if isinstance(top_model, str) else None)
        pending_manual = bool(self.pending_override.pop(thread_id, False)) if method == "turn/start" and thread_id else False
        manual_override = bool(method == "turn/start" and (pending_manual or
            (saved and saved.get("alias") in ALIASES and isinstance(explicit, str) and not _alias(explicit))))
        prior_failed = bool(saved and saved.get("failed"))
        if method == "thread/settings/update" and thread_id and saved and saved.get("alias") in ALIASES \
                and isinstance(explicit, str) and not _alias(explicit):
            self._remember(self.pending_override, thread_id, True)
        elif method == "thread/settings/update" and thread_id and _alias(explicit):
            self.pending_override.pop(thread_id, None)
        if method == "turn/start" and thread_id and isinstance(explicit, str) and not _alias(explicit):
            effort = (settings or {}).get("reasoning_effort") if isinstance(settings, dict) else None
            effort = effort if isinstance(effort, str) else params.get("effort")
            self._remember(self.actual, thread_id, (explicit, effort if isinstance(effort, str) else "medium"))
        alias = _alias(explicit) or (saved.get("alias") if saved and explicit is None else None)
        if _custom_provider(params):
            alias = None
            if thread_id and saved:
                self.store.update(thread_id, clear=True)
                saved = None
        if explicit and not _alias(explicit) and thread_id:
            if method == "thread/settings/update":
                chosen_effort = params.get("effort")
                self._remember(self.actual, thread_id,
                               (explicit, chosen_effort if isinstance(chosen_effort, str) else "medium"))
            self.store.update(thread_id, clear=True)
            saved = None
        if not self._enabled() and alias:
            previous = self.actual.get(thread_id) if thread_id else None
            model, effort = previous or ((saved or {}).get("actual") or self._sol(), (saved or {}).get("effort") or "medium")
            changed = copy.deepcopy(message)
            changed["params"]["model"] = model
            if method == "turn/start":
                changed["params"]["effort"] = effort
            if isinstance(settings, dict):
                changed["params"]["collaborationMode"]["settings"]["model"] = model
                if method == "turn/start":
                    changed["params"]["collaborationMode"]["settings"]["reasoning_effort"] = effort
            return _encode(changed)
        if method == "turn/start":
            if not thread_id or not alias:
                if thread_id and manual_override:
                    self._remember(self.turn_signals, thread_id,
                                   {"manual_override": True, "prior_failed": prior_failed,
                                    "command_failures": 0})
                if explicit and not _alias(explicit) and _alias(top_model):
                    changed = copy.deepcopy(message)
                    changed["params"]["model"] = explicit
                    return _encode(changed)
                return raw
            if thread_id in self.active:
                model, effort = self.actual.get(thread_id, (self._sol(), "medium"))
            else:
                self._remember(self.turn_signals, thread_id,
                               {"manual_override": False, "prior_failed": prior_failed,
                                "command_failures": 0})
                model, effort, _ = self._route(alias, params, thread_id, saved)
            changed = copy.deepcopy(message)
            changed["params"]["model"] = model
            changed["params"]["effort"] = effort
            collab = changed["params"].get("collaborationMode")
            if isinstance(collab, dict) and isinstance(collab.get("settings"), dict):
                collab["settings"]["model"] = model
                collab["settings"]["reasoning_effort"] = effort
            self._remember(self.actual, thread_id, (model, effort))
            self.active.add(thread_id)
            self.store.update(thread_id, alias=alias, failed=False, actual=model, effort=effort,
                              conservative=False, clear_effort_override=True)
            if rid and len(self.pending) < MAX_PENDING:
                self.pending[rid] = {"method": method, "thread": thread_id, "alias": alias}
            return _encode(changed)
        if not alias:
            if rid and len(self.pending) < MAX_PENDING and method in ("thread/start", "thread/resume", "thread/fork"):
                self.pending[rid] = {"method": method, "thread": thread_id, "alias": None}
            if explicit and not _alias(explicit) and _alias(top_model):
                changed = copy.deepcopy(message)
                changed["params"]["model"] = explicit
                return _encode(changed)
            return raw
        changed = copy.deepcopy(message)
        initial = (saved or {}).get("actual") if method in ("thread/resume", "thread/fork") else None
        if method == "thread/resume" and not initial:
            # Native resume restores the existing model. An unknown history must
            # not be overwritten with Sol before the response reveals it.
            changed["params"].pop("model", None)
        else:
            initial = initial if isinstance(initial, str) and re.fullmatch(r"gpt-\d+(?:\.\d+)*-[a-z0-9]+", initial) and not initial.endswith("-astra") else self._sol()
            changed["params"]["model"] = initial
        if isinstance(settings, dict):
            if initial:
                changed["params"]["collaborationMode"]["settings"]["model"] = initial
            else:
                changed["params"]["collaborationMode"]["settings"].pop("model", None)
        if rid and len(self.pending) < MAX_PENDING:
            self.pending[rid] = {"method": method, "thread": thread_id, "alias": alias}
        if thread_id and method == "thread/settings/update":
            requested = params.get("effort")
            self.store.update(thread_id, alias=alias, actual=initial,
                              effort=requested if isinstance(requested, str) else None,
                              clear_effort_override=True)
        return _encode(changed)

    def server(self, raw: bytes) -> bytes:
        message = _decode(raw)
        if not message:
            return raw
        method = message.get("method")
        params = message.get("params")
        if isinstance(method, str) and isinstance(params, dict):
            thread_id = params.get("threadId")
            if method == "item/completed" and isinstance(thread_id, str) and thread_id in self.active:
                item = params.get("item")
                if isinstance(item, dict) and item.get("type") == "commandExecution":
                    exit_code = item.get("exitCode")
                    if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
                        signals = self.turn_signals.setdefault(thread_id, {"command_failures": 0})
                        signals["command_failures"] = min(255, signals.get("command_failures", 0) + 1)
            if method == "thread/started" and isinstance(params.get("thread"), dict):
                changed = copy.deepcopy(message)
                if self._mask_thread_model(changed["params"]["thread"]):
                    return _encode(changed)
            if method == "thread/tokenUsage/updated" and isinstance(thread_id, str):
                last = (params.get("tokenUsage") or {}).get("last") if isinstance(params.get("tokenUsage"), dict) else None
                context = last.get("inputTokens") if isinstance(last, dict) else None
                if isinstance(context, int) and not isinstance(context, bool) and context >= 0:
                    self._remember(self.last_context, thread_id, min(context, 1_000_000_000))
                    self.store.update(thread_id, context=context)
                    cached = last.get("cachedInputTokens") if isinstance(last, dict) else None
                    if isinstance(cached, int) and not isinstance(cached, bool) and 0 <= cached <= context and context > 0:
                        self._remember(self.last_cache_pct, thread_id,
                                       (min(100, (cached * 100) // context), time.monotonic()))
                turn_id = params.get("turnId")
                if isinstance(turn_id, str) and isinstance(last, dict):
                    input_tokens, output_tokens = last.get("inputTokens"), last.get("outputTokens")
                    cached = last.get("cachedInputTokens")
                    if all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
                           for value in (input_tokens, output_tokens, cached)):
                        self._remember(self.turn_usage, (thread_id, turn_id), {
                            "input_tokens": input_tokens, "output_tokens": output_tokens,
                            "input_tokens_details": {"cached_tokens": cached},
                        })
            elif method == "turn/completed" and isinstance(thread_id, str):
                self.active.discard(thread_id)
                turn = params.get("turn")
                failed = isinstance(turn, dict) and turn.get("status") == "failed"
                self.store.update(thread_id, failed=failed)
                turn_id = turn.get("id") if isinstance(turn, dict) else None
                usage = self.turn_usage.pop((thread_id, turn_id), None) if isinstance(turn_id, str) else None
                saved = self.store.get(thread_id) or {}
                model, effort = self.actual.get(thread_id, (saved.get("actual"), saved.get("effort")))
                decision = {"model": model, "effort": effort, "client": "desktop",
                            "session": hashlib.sha256(thread_id.encode("utf-8", "replace")).hexdigest()[:24],
                            "turn_hash": hashlib.sha256(turn_id.encode("utf-8", "replace")).hexdigest()[:24]
                            if isinstance(turn_id, str) else "",
                            "mode": "auto" if saved.get("alias") == "jev-auto" else
                            "shadow" if saved.get("alias") == "jev-shadow" else "native",
                            "reason": "concrete_model" if not saved.get("alias") else "lease"}
                decision["route_id"] = self.turn_routes.pop(thread_id, "")
                decision.update(self.turn_signals.pop(thread_id, {}))
                status = "error" if failed else "cancelled" if isinstance(turn, dict) and turn.get("status") == "interrupted" else "ok"
                self.router.record_usage(decision, usage, status)
            elif method == "thread/settings/updated" and isinstance(thread_id, str):
                entry = self.store.get(thread_id)
                settings = params.get("threadSettings")
                if entry and isinstance(settings, dict) and isinstance(settings.get("model"), str):
                    actual = entry.get("actual")
                    if isinstance(actual, str) and settings["model"] != actual:
                        self.store.update(thread_id, clear=True)
                        return raw
                    # The response is UI state; native execution already uses the concrete model.
                    changed = copy.deepcopy(message)
                    changed["params"]["threadSettings"]["model"] = entry["alias"]
                    collab = changed["params"]["threadSettings"].get("collaborationMode")
                    if isinstance(collab, dict) and isinstance(collab.get("settings"), dict):
                        if collab["settings"].get("model") == actual:
                            collab["settings"]["model"] = entry["alias"]
                    return _encode(changed)
            return raw
        rid = _request_id(message)
        pending = self.pending.pop(rid, None) if rid else None
        if pending and pending["method"] == "turn/start" and "error" in message:
            self.active.discard(pending.get("thread"))
            self.turn_signals.pop(pending.get("thread"), None)
            self.turn_routes.pop(pending.get("thread"), None)
        if not pending or "result" not in message or not isinstance(message["result"], dict):
            return raw
        result = message["result"]
        if pending["method"] in ("thread/read", "thread/list"):
            changed = copy.deepcopy(message)
            updated = False
            if pending["method"] == "thread/read":
                updated = self._mask_thread_model(changed["result"].get("thread"))
            else:
                for thread in changed["result"].get("data", []):
                    updated = self._mask_thread_model(thread) or updated
            return _encode(changed) if updated else raw
        thread = result.get("thread")
        new_thread = thread.get("id") if isinstance(thread, dict) and isinstance(thread.get("id"), str) else pending.get("thread")
        alias = pending.get("alias")
        if isinstance(new_thread, str) and alias and pending["method"] in ("thread/start", "thread/resume", "thread/fork"):
            native_model = result.get("model")
            actual = native_model if isinstance(native_model, str) else None
            existing = self.store.get(new_thread)
            conservative = pending["method"] == "thread/resume" and not isinstance((existing or {}).get("context"), int)
            self.store.update(new_thread, alias=alias, actual=actual,
                              effort=result.get("reasoningEffort"), conservative=conservative,
                              clear_effort_override=True)
        if alias and pending["method"] in ("thread/start", "thread/resume", "thread/fork") and isinstance(result.get("model"), str):
            changed = copy.deepcopy(message)
            changed["result"]["model"] = alias
            self._mask_thread_model(changed["result"].get("thread"))
            return _encode(changed)
        return raw


def _relay(source, target, transform) -> None:
    try:
        while True:
            raw = source.readline(MAX_FRAME + 1)
            if not raw:
                break
            if len(raw) > MAX_FRAME and not raw.endswith(b"\n"):
                target.write(raw)
                while raw and not raw.endswith(b"\n"):
                    raw = source.readline(MAX_FRAME + 1)
                    target.write(raw)
                target.flush()
                continue
            try:
                rewritten = transform(raw)
            except Exception:
                # Keep the native protocol alive if policy or state code fails.
                rewritten = raw
            target.write(rewritten)
            target.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            target.flush()
        except (BrokenPipeError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    opts = parser.parse_args(argv)
    command = opts.command[1:] if opts.command[:1] == ["--"] else opts.command
    native = Path(opts.native)
    if not native.is_absolute() or not native.is_file() or not os.access(native, os.X_OK):
        return 127
    listen = next((command[i + 1] for i, arg in enumerate(command[:-1]) if arg == "--listen"), None)
    listen = next((arg.split("=", 1)[1] for arg in command if arg.startswith("--listen=")), listen)
    stdio = listen in (None, "stdio://") and "--listen-tcp" not in command
    # Desktop may prepend global `-c key=value` options before the subcommand.
    subcommand = 0
    while subcommand < len(command) and command[subcommand] in ("-c", "--config"):
        subcommand += 2
    if subcommand >= len(command) or command[subcommand] != "app-server" or not stdio:
        os.execv(str(native), [str(native), *command])
    adapter = Adapter(Path(opts.root))
    child = subprocess.Popen([str(native), *command], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=None, bufsize=65536)
    assert child.stdin is not None and child.stdout is not None
    def input_pump() -> None:
        _relay(sys.stdin.buffer, child.stdin, adapter.client)
        try:
            child.stdin.close()
        except OSError:
            pass

    incoming = threading.Thread(target=input_pump, daemon=True)
    incoming.start()
    _relay(child.stdout, sys.stdout.buffer, adapter.server)
    try:
        child.stdin.close()
    except OSError:
        pass
    try:
        return child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            return child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            return child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
