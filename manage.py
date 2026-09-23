#!/usr/bin/env python3
"""Owner-local Jev/Codex installation and conservative CLI launcher."""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tomllib
import urllib.request


ROOT = Path.home() / ".local/share/jev-codex-router"
BIN = Path.home() / ".local/bin"
CODEX_CONFIG = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
AGENT = Path.home() / "Library/LaunchAgents/com.local.jev-codex-router.plist"
LABEL = "com.local.jev-codex-router"
DESKTOP_AGENT = Path.home() / "Library/LaunchAgents/com.local.jev-codex-router.desktop-env.plist"
DESKTOP_LABEL = "com.local.jev-codex-router.desktop-env"
DESKTOP_PYTHON = Path("/opt/homebrew/bin/python3.14")
MANAGED_KEYS = ("model", "model_reasoning_effort", "openai_base_url", "model_catalog_json")
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z_0-9-]*)\s*=")
NATIVE_URL = "https://chatgpt.com/backend-api/codex"
SOL = "gpt-6-sol"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_json(path: Path, value: dict) -> None:
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def toml_string(value: str) -> str:
    return json.dumps(value)


def split_root(text: str) -> tuple[list[str], list[str]]:
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.lstrip().startswith("["):
            return lines[:i], lines[i:]
    return lines, []


def root_fields(text: str) -> dict[str, str]:
    root, _ = split_root(text)
    fields: dict[str, str] = {}
    for line in root:
        match = KEY_RE.match(line)
        if match and match.group(1) in MANAGED_KEYS:
            key = match.group(1)
            if key in fields:
                raise ValueError("duplicate root key: " + key)
            fields[key] = line
    return fields


def edit_root(text: str, expected: dict[str, str | None], replacement: dict[str, str | None]) -> str:
    root, suffix = split_root(text)
    found = root_fields(text)
    for key, before in expected.items():
        if found.get(key) != before:
            raise ValueError(f"config changed at {key}; refusing to overwrite")
    kept: list[str] = []
    for line in root:
        match = KEY_RE.match(line)
        if match and match.group(1) in replacement:
            new = replacement[match.group(1)]
            if new is not None:
                kept.append(new)
        else:
            kept.append(line)
    for key in MANAGED_KEYS:
        if key not in found and replacement.get(key) is not None:
            kept.append(replacement[key])
    result = "".join(kept + suffix)
    tomllib.loads(result)
    return result


def restore_owned_root(text: str, owned: dict[str, str], original: dict[str, str | None]) -> tuple[str, list[str]]:
    current = root_fields(text)
    safe = {key: value for key, value in owned.items() if current.get(key) == value}
    conflicts = [key for key, value in owned.items() if current.get(key) != value]
    # Do not leave a live router URL while stopping its daemon.
    if "openai_base_url" in conflicts and "127.0.0.1:43191" in (current.get("openai_base_url") or ""):
        raise ValueError("openai_base_url still targets router; refusing to stop")
    return edit_root(text, safe, {key: original.get(key) for key in safe}), conflicts


def save_config(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    atomic_write(path, text.encode(), mode)


def native_catalog(cache_path: Path) -> dict:
    catalog = load_json(cache_path)
    models = catalog.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("native catalog has no models")
    slugs = [x.get("slug") for x in models if isinstance(x, dict)]
    if not any(re.fullmatch(r"gpt-\d+(?:\.\d+)*-sol", slug or "") for slug in slugs):
        raise ValueError("Sol is absent from native catalog")
    return {"models": models}


def select_sol(catalog: dict) -> str:
    candidates = [x["slug"] for x in catalog["models"] if isinstance(x, dict) and x.get("visibility") == "list"
                  and isinstance(x.get("slug"), str) and re.fullmatch(r"gpt-\d+(?:\.\d+)*-sol", x["slug"])]
    if not candidates:
        raise ValueError("no visible Sol model")
    return max(candidates, key=lambda slug: tuple(int(n) for n in slug.split("-")[1].split(".")))


def managed_catalog(native: dict) -> dict:
    models = copy.deepcopy(native["models"])
    sol = next(x for x in models if x.get("slug") == select_sol(native))
    for slug, name in (("jev-auto", "Jev Auto"), ("jev-shadow", "Jev Shadow")):
        if any(x.get("slug") == slug for x in models):
            raise ValueError("native catalog already owns " + slug)
        alias = copy.deepcopy(sol)
        alias["slug"] = slug
        alias["display_name"] = name
        models.append(alias)
    return {"models": models}


def plist_content(root: Path, python: Path) -> bytes:
    import plistlib

    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(python), str(root / "transport.py"), "--root", str(root)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(root / "state/daemon.log"),
        "StandardErrorPath": str(root / "state/daemon.log"),
    })


def desktop_plist_content(wrapper: Path) -> bytes:
    import plistlib

    return plistlib.dumps({
        "Label": DESKTOP_LABEL,
        "ProgramArguments": ["/bin/launchctl", "setenv", "CODEX_CLI_PATH", str(wrapper)],
        "RunAtLoad": True,
    })


def desktop_wrapper_content(root: Path, native: Path, python: Path) -> bytes:
    argv = [str(python), str(root / "rpc_adapter.py"), "--native", str(native), "--root", str(root), "--"]
    adapter = root / "rpc_adapter.py"
    direct = "openai_base_url=" + toml_string(NATIVE_URL)
    return ("#!/bin/sh\n"
            + f"if [ -x {shlex.quote(str(python))} ] && [ -r {shlex.quote(str(adapter))} ]; then\n"
            + "  exec " + " ".join(map(shlex.quote, [*argv, "-c", direct])) + ' "$@"\n'
            + "fi\n"
            + "exec " + " ".join(map(shlex.quote, [str(native), "-c", direct])) + ' "$@"\n').encode()


def desktop_env() -> str | None:
    result = subprocess.run(["launchctl", "getenv", "CODEX_CLI_PATH"], capture_output=True, text=True, timeout=2)
    if result.returncode:
        return None
    return result.stdout.removesuffix("\n")


def start_desktop_agent(agent_path: Path) -> None:
    domain = f"gui/{os.getuid()}"
    launchctl("bootstrap", domain, str(agent_path))
    launchctl("enable", domain + "/" + DESKTOP_LABEL)
    launchctl("kickstart", "-k", domain + "/" + DESKTOP_LABEL)


def stop_desktop_agent(agent_path: Path) -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(agent_path)], capture_output=True, timeout=2)


def restore_desktop_env(previous: str | None) -> None:
    if previous is None:
        launchctl("unsetenv", "CODEX_CLI_PATH")
    else:
        launchctl("setenv", "CODEX_CLI_PATH", previous)


def python_executable() -> Path:
    stable = Path("/opt/homebrew/bin/python3")
    return stable if stable.exists() else Path(sys.executable)


def launchctl(*args: str) -> None:
    result = subprocess.run(["launchctl", *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"launchctl {args[0]} failed: {result.stderr.strip()[:300]}")


def start_agent() -> None:
    domain = f"gui/{os.getuid()}"
    # A disabled or absent agent can both be bootstrapped safely.
    subprocess.run(["launchctl", "bootout", domain, str(AGENT)], capture_output=True)
    launchctl("bootstrap", domain, str(AGENT))
    launchctl("enable", domain + "/" + LABEL)
    launchctl("kickstart", "-k", domain + "/" + LABEL)


def stop_agent() -> None:
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(AGENT)], capture_output=True)


def source_dir() -> Path:
    return Path(__file__).resolve().parent


def install(root: Path = ROOT, config_path: Path = CODEX_CONFIG, bin_dir: Path = BIN,
            cache_path: Path | None = None, agent_path: Path = AGENT,
            start: bool = True, key_path: Path | None = None,
            execution_binary: Path | None = None) -> None:
    if (root / "manifest.json").exists():
        raise ValueError("already installed; use status/update or rollback")
    cache_path = cache_path or config_path.parent / "models_cache.json"
    codex = bin_dir / "codex"
    if not codex.is_symlink():
        raise ValueError("expected existing codex symlink; refusing to replace it")
    original_link = os.readlink(codex)
    native_target = execution_binary or (Path(original_link) if os.path.isabs(original_link) else codex.parent / original_link)
    if not native_target.resolve(strict=True).is_file() or not os.access(native_target, os.X_OK):
        raise ValueError("native codex is not executable")
    if (bin_dir / "codex-native").exists() or (bin_dir / "jev-codex").exists():
        raise ValueError("codex-native or jev-codex already exists")
    if not config_path.exists():
        raise ValueError("Codex config missing")
    key_file = key_path or Path.home() / ".config/jev-codex-router/typesafe-api-key"
    if not key_file.is_file() or (key_file.stat().st_mode & 0o077):
        raise ValueError("TypeSafe key file missing or not owner-only")
    if agent_path.exists():
        raise ValueError("LaunchAgent already exists: " + str(agent_path))
    for filename in ("manage.py", "core.py", "transport.py", "rpc_adapter.py"):
        if not (source_dir() / filename).exists():
            raise ValueError("missing source: " + filename)
    original_text = config_path.read_text()
    tomllib.loads(original_text)
    before = {key: root_fields(original_text).get(key) for key in MANAGED_KEYS}
    native = native_catalog(cache_path)
    sol = select_sol(native)
    generated = managed_catalog(native)
    root.mkdir(parents=True, exist_ok=False)
    os.chmod(root, 0o700)
    (root / "backups").mkdir(mode=0o700)
    (root / "state").mkdir(mode=0o700)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = root / "backups" / ("config-" + timestamp + ".toml")
    atomic_write(backup, original_text.encode())
    for filename in ("manage.py", "core.py", "transport.py", "rpc_adapter.py"):
        source = source_dir() / filename
        if not source.exists():
            raise ValueError("missing source: " + filename)
        shutil.copy2(source, root / filename)
        os.chmod(root / filename, 0o600)
    write_json(root / "native-models.json", native)
    write_json(root / "models.json", generated)
    capability = secrets.token_urlsafe(32)
    atomic_write(root / "capability", (capability + "\n").encode())
    port = 43191
    config = {
        "mode": "shadow",
        "auto_roles": ["luna", "terra", "sol"],
        "effort_policy": "jev",
        "fixed_effort": "medium",
        "fallback_model": sol,
        "port": port,
        "capability_file": str(root / "capability"),
        "key_file": str(key_file),
        "native_catalog_path": str(root / "native-models.json"),
        "telemetry_file": str(root / "state/telemetry.jsonl"),
    }
    write_json(root / "config.json", config)
    managed = {
        "model": 'model = "jev-shadow"\n',
        "model_reasoning_effort": 'model_reasoning_effort = "medium"\n',
        "openai_base_url": f'openai_base_url = {toml_string(f"http://127.0.0.1:{port}/{capability}")}\n',
        "model_catalog_json": f'model_catalog_json = {toml_string(str(root / "models.json"))}\n',
    }
    updated = edit_root(original_text, before, managed)
    python = python_executable()
    wrapper = root / "jev-codex"
    atomic_write(wrapper, (f"#!{python}\nimport sys\nsys.path.insert(0, {str(root)!r})\nfrom manage import main\nif __name__ == '__main__': main()\n").encode(), 0o700)
    os.chmod(wrapper, 0o700)
    native_wrapper = root / "codex-native"
    atomic_write(native_wrapper, (f"#!{python}\nimport sys\nsys.path.insert(0, {str(root)!r})\nfrom manage import native_main\nif __name__ == '__main__': native_main()\n").encode(), 0o700)
    os.chmod(native_wrapper, 0o700)
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "original_codex_link": original_link,
        "native_target": str(native_target),
        "original_root": before,
        "managed_root": managed,
        "config_state": "enabled",
        "config_path": str(config_path),
        "backup": str(backup),
        "agent_path": str(agent_path),
        "bin_dir": str(bin_dir),
    }
    # All preflight work is complete. Keep the recoverable backup and manifest before global edits.
    write_json(root / "manifest.json", manifest)
    try:
        atomic_write(agent_path, plist_content(root, python), 0o600)
        save_config(config_path, updated)
        (bin_dir / "codex-native").symlink_to(native_wrapper)
        (bin_dir / "jev-codex").symlink_to(wrapper)
        codex.unlink()
        codex.symlink_to(wrapper)
        if start:
            start_agent()
    except Exception:
        if codex.is_symlink() and os.readlink(codex) == str(wrapper):
            codex.unlink()
        if not codex.exists() and not codex.is_symlink():
            codex.symlink_to(original_link)
        for name, target in (("codex-native", native_wrapper), ("jev-codex", wrapper)):
            path = bin_dir / name
            if path.is_symlink() and os.readlink(path) == str(target):
                path.unlink()
        try:
            if config_path.exists() and root_fields(config_path.read_text()) == managed:
                save_config(config_path, original_text)
        except (OSError, ValueError):
            pass
        if agent_path.exists() and agent_path.read_bytes() == plist_content(root, python):
            agent_path.unlink()
        if start:
            stop_agent()
        manifest["config_state"] = "failed"
        write_json(root / "manifest.json", manifest)
        raise


def _restore_root(root: Path, manifest: dict) -> None:
    path = Path(manifest["config_path"])
    current = path.read_text()
    if manifest["config_state"] == "enabled":
        updated, conflicts = restore_owned_root(current, manifest["managed_root"], manifest["original_root"])
        if conflicts:
            manifest["preserved_user_changes"] = conflicts
            write_json(root / "manifest.json", manifest)
    else:
        updated = current
    if updated != current:
        save_config(path, updated)


def desktop_enable(root: Path = ROOT, agent_path: Path | None = None,
                   python: Path = DESKTOP_PYTHON) -> None:
    manifest_path = root / "manifest.json"
    manifest = load_json(manifest_path)
    if manifest["config_state"] != "enabled":
        raise ValueError("enable router before Desktop adapter")
    desktop = manifest.get("desktop", {})
    if desktop.get("phase") == "prepared":
        desktop_disable(root)
        manifest = load_json(manifest_path)
        desktop = manifest.get("desktop", {})
    wrapper = root / "app-server-wrapper"
    agent_path = agent_path or Path(desktop.get("agent_path", DESKTOP_AGENT))
    content = desktop_wrapper_content(root, Path(manifest["native_target"]), python)
    if desktop.get("enabled"):
        if desktop_env() != str(wrapper):
            raise ValueError("CODEX_CLI_PATH changed outside router; refusing to overwrite")
        return
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Python 3.14 executable missing: " + str(python))
    source_adapter = source_dir() / "rpc_adapter.py"
    if not source_adapter.is_file():
        raise ValueError("missing source: rpc_adapter.py")
    installed_adapter = root / "rpc_adapter.py"
    if installed_adapter.exists() and installed_adapter.read_bytes() != source_adapter.read_bytes():
        raise ValueError("installed rpc_adapter.py differs from source; refusing to overwrite")
    if wrapper.exists() and wrapper.read_bytes() != content:
        raise ValueError("Desktop wrapper changed; refusing to overwrite")
    if agent_path.exists():
        raise ValueError("Desktop environment LaunchAgent already exists: " + str(agent_path))
    previous = desktop_env()
    if previous == str(wrapper):
        raise ValueError("CODEX_CLI_PATH already points to an unowned wrapper")
    copied_adapter = not installed_adapter.exists()
    created_wrapper = not wrapper.exists()
    # Journal the old value before the LaunchAgent or launchctl can change it.
    manifest["desktop"] = {
        "opted_in": bool(desktop.get("opted_in")),
        "enabled": False,
        "phase": "prepared",
        "previous_env": previous,
        "agent_path": str(agent_path),
        "wrapper_path": str(wrapper),
        "python": str(python),
        "env_changed_externally": False,
    }
    write_json(manifest_path, manifest)
    try:
        if copied_adapter:
            shutil.copy2(source_adapter, installed_adapter)
            os.chmod(installed_adapter, 0o600)
        if created_wrapper:
            atomic_write(wrapper, content, 0o700)
        agent_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(agent_path, desktop_plist_content(wrapper), 0o600)
        start_desktop_agent(agent_path)
        launchctl("setenv", "CODEX_CLI_PATH", str(wrapper))
        manifest["desktop"] = {
            "opted_in": True,
            "enabled": True,
            "phase": "enabled",
            "previous_env": previous,
            "agent_path": str(agent_path),
            "wrapper_path": str(wrapper),
            "python": str(python),
            "env_changed_externally": False,
        }
        write_json(manifest_path, manifest)
    except Exception:
        # If cleanup fails, the prepared manifest remains for a later disable/rollback.
        desktop_disable(root)
        if created_wrapper and wrapper.exists() and wrapper.read_bytes() == content:
            wrapper.unlink()
        if copied_adapter and installed_adapter.exists() and installed_adapter.read_bytes() == source_adapter.read_bytes():
            installed_adapter.unlink()
        raise


def desktop_disable(root: Path = ROOT) -> None:
    manifest_path = root / "manifest.json"
    manifest = load_json(manifest_path)
    desktop = manifest.get("desktop", {})
    if not desktop.get("enabled") and desktop.get("phase") != "prepared":
        return
    wrapper = Path(desktop["wrapper_path"])
    agent_path = Path(desktop["agent_path"])
    expected = desktop_plist_content(wrapper)
    if agent_path.exists() and agent_path.read_bytes() != expected:
        raise ValueError("Desktop environment LaunchAgent changed; refusing to unload")
    stop_desktop_agent(agent_path)
    current = desktop_env()
    changed = current != str(wrapper) and (desktop.get("phase") != "prepared" or current != desktop.get("previous_env"))
    if not changed:
        if current == str(wrapper):
            restore_desktop_env(desktop.get("previous_env"))
    if agent_path.exists() and agent_path.read_bytes() == expected:
        agent_path.unlink()
    desktop["enabled"] = False
    desktop["phase"] = "disabled"
    desktop["env_changed_externally"] = changed
    manifest["desktop"] = desktop
    write_json(manifest_path, manifest)


def _remove_desktop_wrapper(root: Path, manifest: dict) -> None:
    desktop = manifest.get("desktop", {})
    if not desktop.get("wrapper_path"):
        return
    wrapper = Path(desktop["wrapper_path"])
    expected = desktop_wrapper_content(root, Path(manifest["native_target"]), Path(desktop["python"]))
    if wrapper.exists():
        if wrapper.read_bytes() != expected:
            manifest.setdefault("preserved_user_changes", []).append("app-server-wrapper")
        else:
            wrapper.unlink()


def disable(root: Path = ROOT, stop: bool = True) -> None:
    desktop_disable(root)
    manifest = load_json(root / "manifest.json")
    if manifest["config_state"] == "enabled":
        _restore_root(root, manifest)
        manifest["config_state"] = "disabled"
        write_json(root / "manifest.json", manifest)
    config = load_json(root / "config.json")
    config["mode"] = "off"
    write_json(root / "config.json", config)
    if stop:
        stop_agent()


def enable(root: Path = ROOT, start: bool = True) -> None:
    manifest = load_json(root / "manifest.json")
    if manifest["config_state"] == "disabled":
        path = Path(manifest["config_path"])
        updated = edit_root(path.read_text(), manifest["original_root"], manifest["managed_root"])
        save_config(path, updated)
        manifest["config_state"] = "enabled"
        write_json(root / "manifest.json", manifest)
    config = load_json(root / "config.json")
    config["mode"] = "shadow"
    write_json(root / "config.json", config)
    if start:
        start_agent()
    if manifest.get("desktop", {}).get("opted_in") and not manifest["desktop"].get("env_changed_externally"):
        desktop = manifest["desktop"]
        desktop_enable(root, Path(desktop["agent_path"]), Path(desktop["python"]))


def rollback(root: Path = ROOT, stop: bool = True) -> None:
    desktop_disable(root)
    manifest = load_json(root / "manifest.json")
    bin_dir = Path(manifest["bin_dir"])
    codex = bin_dir / "codex"
    wrapper = root / "jev-codex"
    if not codex.is_symlink() or Path(os.readlink(codex)) != wrapper:
        raise ValueError("codex symlink changed; refusing rollback")
    native = bin_dir / "codex-native"
    if not native.is_symlink() or Path(os.readlink(native)) != root / "codex-native":
        raise ValueError("codex-native symlink changed; refusing rollback")
    _restore_root(root, manifest)
    if stop:
        stop_agent()
    codex.unlink()
    codex.symlink_to(manifest["original_codex_link"])
    native.unlink()
    jev = bin_dir / "jev-codex"
    if jev.is_symlink() and Path(os.readlink(jev)) == wrapper:
        jev.unlink()
    agent = Path(manifest["agent_path"])
    if agent.exists() and agent.read_bytes() == plist_content(root, python_executable()):
        agent.unlink()
    _remove_desktop_wrapper(root, manifest)
    # Preserve installation snapshot, backup, and telemetry for inspection.
    manifest["config_state"] = "rolled_back"
    write_json(root / "manifest.json", manifest)


def update_catalog(root: Path = ROOT) -> None:
    manifest = load_json(root / "manifest.json")
    cache = Path(manifest["config_path"]).parent / "models_cache.json"
    data = load_json(cache)
    fetched_at = data.get("fetched_at")
    try:
        fetched = dt.datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        age = (dt.datetime.now(dt.timezone.utc) - fetched).total_seconds()
    except (AttributeError, ValueError):
        raise ValueError("native catalog cache has no valid fetch time") from None
    if age < 0 or age > 600:
        raise ValueError(f"native catalog cache is stale ({int(age)}s); refresh it through native Codex before update")
    native = native_catalog(cache)
    generated = managed_catalog(native)
    old_alias = next((x for x in load_json(root / "models.json")["models"] if x.get("slug") == "jev-auto"), None)
    new_alias = next(x for x in generated["models"] if x.get("slug") == "jev-auto")
    if manifest["config_state"] == "enabled" and old_alias != new_alias:
        raise ValueError("Sol alias metadata changed; disable router, update catalog, then restart Desktop before enable")
    write_json(root / "native-models.json", native)
    write_json(root / "models.json", generated)
    config = load_json(root / "config.json")
    config["fallback_model"] = select_sol(native)
    write_json(root / "config.json", config)


def native_catalog_from_response(data: dict) -> dict:
    models = data.get("models")
    if not isinstance(models, list) or not models or not any(isinstance(x, dict) and x.get("slug", "").endswith("-sol") for x in models):
        raise ValueError("native debug models returned no Sol")
    return {"models": models}


def health(root: Path) -> bool:
    try:
        config = load_json(root / "config.json")
        with urllib.request.urlopen(f"http://127.0.0.1:{int(config['port'])}/health", timeout=0.3) as response:
            return response.status == 200 and json.load(response).get("ok") is True
    except (OSError, ValueError, KeyError):
        return False


def desktop_runtime() -> dict:
    """Report active local Desktop transport without logging process arguments."""
    result = {"app_running": False, "adapter_active": False, "direct_native_app_server": False}
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True,
                            text=True, timeout=2, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return result
    processes = {}
    for line in ps.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid, parent = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        processes[pid] = (parent, parts[2])
    apps = {pid for pid, (_, command) in processes.items()
            if command.split()[0].endswith("/ChatGPT.app/Contents/MacOS/ChatGPT")}
    result["app_running"] = bool(apps)
    # The primary app-server is spawned by the Desktop main process. Test probes
    # launched from a Codex task also descend from it, but are not its transport.
    adapters = {pid for pid, (parent, command) in processes.items()
                if "rpc_adapter.py --native " in command and parent in apps}
    result["adapter_active"] = bool(adapters)
    for pid, (parent, command) in processes.items():
        if not command.split()[0].endswith("/ChatGPT.app/Contents/Resources/codex") or "app-server" not in command:
            continue
        if parent not in apps and parent not in adapters:
            continue
        if parent in apps:
            result["direct_native_app_server"] = True
    return result


def status(root: Path = ROOT) -> dict:
    manifest = load_json(root / "manifest.json")
    config = load_json(root / "config.json")
    auto_roles = config.get("auto_roles")
    if not (isinstance(auto_roles, list) and "sol" in auto_roles and
            all(isinstance(role, str) and role in ("luna", "terra", "sol", "astra") for role in auto_roles)):
        auto_roles = ["luna", "terra", "sol"]
    effort_policy = config.get("effort_policy") if config.get("effort_policy") in ("jev", "fixed") else "jev"
    fixed_effort = config.get("fixed_effort") if config.get("fixed_effort") in ("low", "medium", "high", "xhigh", "max", "ultra") else "medium"
    catalog = load_json(root / "models.json")
    process: dict = {"pid": None, "rss_kib": None, "elapsed": None}
    try:
        out = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True, timeout=2)
        match = re.search(r"\bpid\s*=\s*(\d+)", out.stdout)
        if match:
            process["pid"] = int(match.group(1))
            ps = subprocess.run(["ps", "-o", "rss=,etime=", "-p", match.group(1)], capture_output=True, text=True, timeout=2)
            values = ps.stdout.split()
            if len(values) >= 2:
                process["rss_kib"] = int(values[0])
                process["elapsed"] = values[1]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    desktop = manifest.get("desktop", {})
    wrapper = desktop.get("wrapper_path", str(root / "app-server-wrapper"))
    try:
        current_env = desktop_env()
    except (OSError, subprocess.TimeoutExpired):
        current_env = None
    desktop_status = {
        "opted_in": bool(desktop.get("opted_in")),
        "enabled": bool(desktop.get("enabled")),
        "env_points_to_wrapper": current_env == wrapper,
        "env_present": current_env is not None,
        "env_changed_externally": bool(desktop.get("env_changed_externally")),
        "wrapper_present": Path(wrapper).exists(),
        "launch_agent_present": Path(desktop.get("agent_path", DESKTOP_AGENT)).exists(),
        "limitation": "Desktop hostConfig.codex_cli_command overrides CODEX_CLI_PATH when set",
        "runtime": desktop_runtime(),
    }
    return {
        "mode": config["mode"], "config_state": manifest["config_state"], "health": health(root),
        "policy": {"config_file": str(root / "config.json"), "auto_roles": auto_roles,
                   "effort_policy": effort_policy,
                   "fixed_effort": fixed_effort,
                   "astra_auto_allowed": "astra" in auto_roles},
        "port": config["port"], "catalog_models": len(catalog["models"]),
        "models": [x.get("slug") for x in catalog["models"] if x.get("slug", "").startswith("jev-")],
        "native_binary": manifest["native_target"],
        "daemon": process,
        "desktop": desktop_status,
        "preserved_user_changes": manifest.get("preserved_user_changes", []),
        "telemetry_bytes": (root / "state/telemetry.jsonl").stat().st_size if (root / "state/telemetry.jsonl").exists() else 0,
    }


def report(root: Path = ROOT, weights: dict | None = None) -> dict:
    path = root / "state/telemetry.jsonl"
    rows = []
    if path.exists():
        with path.open() as source:
            for line in source:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except json.JSONDecodeError:
                    continue
    usage_rows = [row for row in rows if row.get("event", "usage") == "usage"]
    route_rows = [row for row in rows if row.get("event") == "route"]
    by_model: dict[str, dict] = {}
    by_client: dict[str, dict] = {}
    failures = 0
    for row in usage_rows:
        model = str(row.get("model") or "unknown")
        client = str(row.get("client") or "unknown")
        client_bucket = by_client.setdefault(client, {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0})
        client_bucket["calls"] += 1
        bucket = by_model.setdefault(model, {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0,
                                             "output_tokens": 0, "jev_ms": 0, "efforts": {}})
        bucket["calls"] += 1
        for field in ("input_tokens", "cached_input_tokens", "output_tokens"):
            value = row.get(field)
            if isinstance(value, int) and value >= 0:
                bucket[field] += value
                client_bucket[field] += value
        effort = str(row.get("effort") or "unknown")
        bucket["efforts"][effort] = bucket["efforts"].get(effort, 0) + 1
        failures += row.get("status") in ("failed", "error")
    proposals: dict[str, int] = {}
    jev_ms = 0
    for row in route_rows:
        proposed = row.get("proposed_model")
        if isinstance(proposed, str):
            proposals[proposed] = proposals.get(proposed, 0) + 1
        value = row.get("jev_ms")
        if isinstance(value, (int, float)) and value >= 0:
            jev_ms += value
    totals = {field: sum(row[field] for row in by_model.values()) for field in ("input_tokens", "cached_input_tokens", "output_tokens")}
    comparison: dict = {"token_hold_constant": totals, "weights_source": "none", "actual_units": None,
                        "all_sol_units": None, "all_astra_units": None}
    if weights:
        try:
            models = load_json(root / "native-models.json")
            from core import visible_roles
            roles = visible_roles(models)
            def units(tokens: dict, rate: dict) -> float:
                if not all(isinstance(rate.get(key), (int, float)) and rate[key] >= 0 for key in ("input", "cached_input", "output")):
                    raise ValueError("weights require nonnegative input/cached_input/output")
                uncached = max(0, tokens["input_tokens"] - tokens["cached_input_tokens"])
                return round(uncached * rate["input"] + tokens["cached_input_tokens"] * rate["cached_input"] + tokens["output_tokens"] * rate["output"], 4)
            actual = sum(units(tokens, weights[model]) for model, tokens in by_model.items())
            comparison.update({"weights_source": "user_supplied", "actual_units": round(actual, 4),
                               "all_sol_units": units(totals, weights[roles["sol"]["slug"]]),
                               "all_astra_units": units(totals, weights[roles["astra"]["slug"]])})
        except (KeyError, TypeError, ValueError, ImportError):
            comparison["weights_source"] = "incomplete_user_weights"
    return {
        "observed": {"calls": len(usage_rows), "failures": failures,
                     "usage_missing_count": sum(row.get("usage_missing") is True for row in usage_rows),
                     "by_model": by_model, "by_client": by_client},
        "routes": {"decisions": len(route_rows), "switches": sum(row.get("switched") is True for row in route_rows),
                   "jev_ms": jev_ms, "proposed_models": proposals},
        "counterfactual": comparison,
        "note": "Token-hold-constant comparisons are sensitivity estimates, not Pro cost or quality-equivalent savings.",
    }


def _parse_cli(argv: list[str]) -> tuple[str, str | None, bool]:
    """Return supported command, initial prompt, and explicit override flag."""
    command = "interactive"
    positional: list[str] = []
    explicit = False
    value_options = {"-c", "--config", "-m", "--model", "-p", "--profile", "-i", "--image", "-C", "--cd",
                     "--add-dir", "-s", "--sandbox", "-a", "--ask-for-approval", "--output-schema", "-o",
                     "--output-last-message", "--color", "--thread-source", "--enable", "--disable", "--remote",
                     "--remote-auth-token-env", "--local-provider"}
    safe_switches = {"--json", "--search", "--no-alt-screen", "--skip-git-repo-check", "--ephemeral",
                     "--ignore-rules", "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust",
                     "--approve-for-me", "--strict-config", "--all", "--last"}
    native_commands = {"agents", "review", "login", "logout", "mcp", "plugin", "mcp-server", "app-server",
                       "remote-control", "app", "completion", "update", "doctor", "sandbox", "debug", "apply",
                       "a", "resume", "queue", "archive", "delete", "migrate-rollouts", "unarchive", "fork",
                       "cloud", "exec-server", "features", "help"}
    i = 0
    while i < len(argv):
        item = argv[i]
        if item in ("exec", "e") and command == "interactive" and not positional:
            command = "exec"
        elif item == "resume" and command == "exec" and not positional:
            command = "exec-resume"
        elif item == "--":
            positional.extend(argv[i + 1:]); break
        elif item in value_options:
            if i + 1 >= len(argv):
                return "passthrough", None, True
            value = argv[i + 1]
            if item in ("-m", "--model", "-p", "--profile", "--remote", "--local-provider"):
                explicit = True
            if item in ("-c", "--config") and value.split("=", 1)[0] in ("model", "model_provider", "model_provider_id", "openai_base_url", "model_catalog_json"):
                explicit = True
            i += 1
        elif item.startswith("--model=") or item.startswith("--profile=") or item.startswith("--remote="):
            explicit = True
        elif item.startswith("--config=") and item.partition("=")[2].split("=", 1)[0] in ("model", "model_provider", "model_provider_id", "openai_base_url", "model_catalog_json"):
            explicit = True
        elif item in ("--oss", "--ignore-user-config"):
            explicit = True
        elif item == "-":
            positional.append(item)
        elif item.startswith("-"):
            if item not in safe_switches:
                return "passthrough", None, True
        else:
            if command == "interactive" and not positional and item in native_commands:
                return "passthrough", None, True
            if command == "exec" and not positional and item in ("review", "fork", "help"):
                return "passthrough", None, True
            positional.append(item)
        i += 1
    if command == "exec-resume":
        # Two positional args: session then prompt. --last permits a prompt alone.
        if "--last" in argv:
            prompt = positional[-1] if positional else None
        else:
            prompt = positional[1] if len(positional) == 2 else None
        return command, prompt, explicit
    if command in ("interactive", "exec"):
        if len(positional) > 1:
            return "passthrough", None, True
        return command, positional[0] if positional else None, explicit
    return "passthrough", None, True


def _custom_transport(argv: list[str]) -> bool:
    for i, arg in enumerate(argv):
        if arg in ("-p", "--profile", "--oss", "--local-provider", "--remote", "--ignore-user-config") or arg.startswith(("--profile=", "--local-provider=", "--remote=")):
            return True
        value = argv[i + 1] if arg in ("-c", "--config") and i + 1 < len(argv) else arg.partition("=")[2] if arg.startswith("--config=") else ""
        if value.split("=", 1)[0] in ("openai_base_url", "model_provider", "model_provider_id"):
            return True
    return False


def _cli_endpoint_args(root: Path, config: dict) -> list[str]:
    capability = Path(config["capability_file"]).read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", capability):
        raise ValueError("invalid local capability")
    port = int(config["port"])
    return ["-c", "openai_base_url=" + toml_string(f"http://127.0.0.1:{port}/{capability}/cli")]


def _cli_effort_override(argv: list[str]) -> str | None:
    for index, arg in enumerate(argv):
        value = argv[index + 1] if arg in ("-c", "--config") and index + 1 < len(argv) else arg.partition("=")[2] if arg.startswith("--config=") else ""
        name, separator, raw = value.partition("=")
        if separator and name == "model_reasoning_effort":
            effort = raw.strip().strip("\"'")
            if effort in ("low", "medium", "high", "xhigh", "max", "ultra"):
                return effort
    return None


def cli_args(argv: list[str], root: Path = ROOT, stdin_tty: bool = True) -> list[str]:
    manifest = load_json(root / "manifest.json")
    native = manifest["native_target"]
    bypass = str(Path(manifest["bin_dir"]) / "codex-native")
    mode_flag = next((x for x in argv if x in ("--jev-auto", "--jev-shadow", "--jev-off")), None)
    clean = [x for x in argv if x not in ("--jev-auto", "--jev-shadow", "--jev-off")]
    command, prompt, explicit = _parse_cli(clean)
    config = load_json(root / "config.json")
    sol = config.get("fallback_model", SOL)
    if mode_flag == "--jev-off":
        return [native if _custom_transport(clean) else bypass, *clean]
    if _custom_transport(clean):
        return [native, *clean]
    enabled = config.get("mode") != "off" and manifest["config_state"] == "enabled"
    healthy = health(root) if enabled else False
    endpoint = _cli_endpoint_args(root, config) if enabled and healthy and command != "passthrough" else []
    if command == "passthrough":
        return [native if healthy else bypass, *clean]
    if explicit:
        return [native if healthy else bypass, *endpoint, *clean]
    if prompt == "-":
        return [native if healthy else bypass, *endpoint, "-m", sol, *clean]
    if command == "interactive" and prompt is None:
        return [native if healthy else bypass, *endpoint, "-m", sol, *clean]
    if prompt is None:
        return [native if healthy else bypass, *endpoint, "-m", sol, *clean]
    if not enabled:
        return [bypass, *clean]
    if not healthy:
        return [bypass, "-m", sol, *clean]
    if command == "exec-resume":
        return [native, *endpoint, *clean]
    from core import Router
    payload = {"model": "jev-auto", "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt[:12000]}]}]}
    try:
        router = Router(config_path=root / "config.json", catalog_path=root / "native-models.json",
                        state_path=root / "state/leases.json", telemetry_path=root / "state/telemetry.jsonl")
        requested_mode = "shadow" if mode_flag == "--jev-shadow" else "auto"
        decision = router.decide(payload, client="cli", session_id="cli-" + secrets.token_hex(8),
                                 native_selection=True, mode_override=requested_mode)
        model = decision["model"]
        effort = decision.get("effort")
        if not isinstance(model, str) or not model or model.startswith("jev-"):
            raise ValueError("invalid native selection")
        configured_roles = config.get("auto_roles")
        astra_allowed = (isinstance(configured_roles, list) and "sol" in configured_roles
                         and all(role in ("luna", "terra", "sol", "astra") for role in configured_roles)
                         and "astra" in configured_roles)
        if model.endswith("-astra") and not astra_allowed:
            model, effort = sol, "high"
        requested_effort = _cli_effort_override(clean)
        if requested_effort:
            from core import visible_roles
            roles = visible_roles(router._catalog())
            selected = next((item for item in roles.values() if item["slug"] == model), None)
            if selected and requested_effort not in selected["efforts"]:
                model = sol
            effort = requested_effort
        decision["model"], decision["effort"] = model, effort
        decision["client"] = "cli"
        router.record_usage(decision, None, "ok", event="route")
        extras = ["-m", model]
        if effort and not requested_effort:
            extras.extend(["-c", "model_reasoning_effort=" + toml_string(effort)])
        return [native, *endpoint, *extras, *clean]
    except Exception:
        return [native, *endpoint, "-m", sol, *clean]


def native_overrides(root: Path) -> list[str]:
    return ["-c", "openai_base_url=" + toml_string(NATIVE_URL),
            "-c", "model_catalog_json=" + toml_string(str(root / "native-models.json"))]


def native_args(argv: list[str], root: Path = ROOT) -> list[str]:
    manifest = load_json(root / "manifest.json")
    real = manifest["native_target"]
    config = load_json(root / "config.json")
    command, _, _ = _parse_cli(argv)
    explicit_model = False
    for i, arg in enumerate(argv):
        if arg in ("-m", "--model", "-p", "--profile") or arg.startswith(("--model=", "--profile=")):
            explicit_model = True
        if arg in ("-c", "--config") and i + 1 < len(argv) and argv[i + 1].split("=", 1)[0] == "model":
            explicit_model = True
        if arg.startswith("--config=model="):
            explicit_model = True
    # The native shim never inherits a synthetic default alias from global config.
    model = [] if explicit_model or command in ("passthrough", "exec-resume") else ["-m", config.get("fallback_model", SOL)]
    return [real, *native_overrides(root), *model, *argv]


def native_main() -> None:
    args = native_args(sys.argv[1:])
    os.execv(args[0], args)


def main() -> None:
    argv = sys.argv[1:]
    commands = {"install", "status", "report", "disable", "enable", "rollback", "update",
                "desktop-enable", "desktop-disable"}
    # Only jev-codex subcommands manage installation. The transparent codex link always passes native commands.
    invoked = Path(sys.argv[0]).name
    if invoked in ("jev-codex", "manage.py") and argv and argv[0] in commands:
        cmd = argv[0]
        try:
            if cmd == "install":
                options = argv[1:]
                if len(options) % 2 or any(options[i] not in ("--execution-binary", "--key-file") for i in range(0, len(options), 2)):
                    raise ValueError("usage: manage.py install [--execution-binary /absolute/path/to/codex] [--key-file /absolute/path/to/key]")
                settings = dict(zip(options[::2], options[1::2]))
                install(execution_binary=Path(settings["--execution-binary"]) if "--execution-binary" in settings else None,
                        key_path=Path(settings["--key-file"]) if "--key-file" in settings else None)
            elif cmd == "disable": disable()
            elif cmd == "enable": enable()
            elif cmd == "desktop-enable": desktop_enable()
            elif cmd == "desktop-disable": desktop_disable()
            elif cmd == "rollback": rollback()
            elif cmd == "update": update_catalog()
            elif cmd == "status": print(json.dumps(status(), indent=2))
            elif cmd == "report":
                if len(argv) not in (1, 3) or (len(argv) == 3 and argv[1] != "--weights"):
                    raise ValueError("usage: jev-codex report [--weights path.json]")
                weights = load_json(Path(argv[2])) if len(argv) == 3 else None
                print(json.dumps(report(weights=weights), indent=2))
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"jev-codex {cmd}: {exc}", file=sys.stderr)
            raise SystemExit(1)
        return
    if not (ROOT / "manifest.json").exists():
        print("jev-codex is not installed", file=sys.stderr)
        raise SystemExit(1)
    args = cli_args(argv, stdin_tty=sys.stdin.isatty())
    os.execv(args[0], args)


if __name__ == "__main__":
    main()
