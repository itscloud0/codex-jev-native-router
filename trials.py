"""Small, local, privacy-safe prospective task ledger for Effortlane."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
import time
import uuid
from collections import defaultdict
from pathlib import Path
from statistics import median

from metrics import _SAFE_REASONS

_ID = re.compile(r"[a-f0-9]{24}$")
_UUID = re.compile(
    r"(?:codex://threads/)?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
_MODEL = re.compile(r"gpt-\d+(?:\.\d+)*-[a-z][a-z0-9]*$")
_EFFORT = {"low", "medium", "high", "xhigh", "max", "ultra"}
_ENUMS = {
    "kind": {"mechanical", "routine", "debugging", "design"},
    "scope": {"component", "cross_component"},
    "risk": {"low", "high"},
    "uncertainty": {"known", "investigation"},
    "effort": _EFFORT,
    "check": {"tests", "build", "review"},
    "arm": {"auto", "baseline"},
    "client": {"cli", "desktop"},
}
_OUTCOMES = {"accepted", "rework", "failed"}
_CHECKS = {"passed", "failed", "not_run"}
_METHODS = {"randomized", "observational"}
_POLICIES = {
    "baseline",
    "completion_v1",
    "completion_v2",
    "completion_v3",
    "completion_v4",
}
_CLIENTS = {"cli", "desktop", "app-server", "proxy"}


def _enum(value, allowed) -> bool:
    return isinstance(value, str) and value in allowed


def _hash(value: str | bytes) -> str:
    return hashlib.sha256(
        value if isinstance(value, bytes) else value.encode()
    ).hexdigest()[:24]


def _now(now: float | None) -> float:
    value = time.time() if now is None else now
    if not _valid_time(value):
        raise ValueError("invalid timestamp")
    return float(value)


def _thread(value: str) -> str:
    m = _UUID.fullmatch(value) if isinstance(value, str) else None
    if not m:
        raise ValueError("thread must be UUID or codex://threads/UUID")
    return _hash(str(uuid.UUID(m.group(1))))


def _safe_file(path: Path, *, missing: bool = False) -> None:
    try:
        st = path.lstat()
    except FileNotFoundError:
        if missing:
            return
        raise
    if (
        path.is_symlink()
        or not path.is_file()
        or st.st_uid != os.getuid()
        or st.st_mode & 0o077
    ):
        raise ValueError("unsafe trial ledger")


def _catalog_path(root: Path, client: str) -> Path:
    if client == "cli" and (root / "manifest.json").exists():
        from manage import cli_catalog_path

        try:
            return cli_catalog_path(root, "native-models.json")
        except (OSError, ValueError, AttributeError):
            raise ValueError("native catalog manifest unavailable") from None
    return root / "native-models.json"


def _fingerprint(root: Path, client: str = "cli") -> str:
    parts = []
    for name in ("core.py", "costs.py", "rpc_adapter.py"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            parts.append(
                name.encode() + b":" + hashlib.sha256(path.read_bytes()).digest()
            )
    catalog = _catalog_path(root, client)
    if catalog.is_file() and not catalog.is_symlink():
        parts.append(
            client.encode()
            + b":catalog:"
            + hashlib.sha256(catalog.read_bytes()).digest()
        )
    config = root / "config.json"
    if config.is_file() and not config.is_symlink():
        try:
            raw = json.loads(config.read_text())
            safe = _safe_config(raw)
            parts.append(
                b"config:"
                + hashlib.sha256(
                    json.dumps(safe, sort_keys=True, separators=(",", ":")).encode()
                ).digest()
            )
        except (OSError, ValueError, TypeError):
            parts.append(b"config:invalid")
    return _hash(b"|".join(parts))


def _safe_config(raw) -> dict:
    """Only routing settings with constrained, non-secret value domains enter the hash."""
    if not isinstance(raw, dict):
        return {}
    result = {}
    if _enum(raw.get("mode"), {"auto", "shadow", "off"}):
        result["mode"] = raw["mode"]
    if isinstance(raw.get("auto_roles"), list) and all(
        _enum(role, {"luna", "terra", "sol", "astra"}) for role in raw["auto_roles"]
    ):
        result["auto_roles"] = raw["auto_roles"]
    if isinstance(raw.get("allow_dominated_roles"), bool):
        result["allow_dominated_roles"] = raw["allow_dominated_roles"]
    for key in ("auto_policy", "shadow_policy"):
        if _enum(raw.get(key), _POLICIES):
            result[key] = raw[key]
    if _enum(raw.get("effort_policy"), {"fixed", "adaptive"}):
        result["effort_policy"] = raw["effort_policy"]
    if _enum(raw.get("fixed_effort"), _EFFORT):
        result["fixed_effort"] = raw["fixed_effort"]
    for key in ("large_context_sol_floor_tokens", "timeout_seconds"):
        value = raw.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and -1_000_000_000 <= value <= 1_000_000_000
        ):
            result[key] = value
        elif key in raw:
            result[key] = "invalid"
    if isinstance(raw.get("fallback_model"), str) and re.fullmatch(
        r"gpt-\d+(?:\.\d+)*-sol", raw["fallback_model"]
    ):
        result["fallback_model"] = raw["fallback_model"]
    return result


def _baseline(root: Path, effort: str, client: str) -> str:
    from core import visible_roles

    path = _catalog_path(root, client)
    if not path.is_file() or path.is_symlink():
        raise ValueError("native catalog unavailable")
    try:
        sol = visible_roles(json.loads(path.read_text()))["sol"]
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("native Sol unavailable")
    if effort not in sol.get("efforts", []):
        raise ValueError("requested effort unavailable for native Sol")
    return sol["slug"]


def _paths(root: Path) -> tuple[Path, Path]:
    state = root / "state"
    state.mkdir(mode=0o700, exist_ok=True)
    if (
        state.is_symlink()
        or not state.is_dir()
        or state.stat().st_uid != os.getuid()
        or state.stat().st_mode & 0o077
    ):
        raise ValueError("unsafe trial state directory")
    return state / "trials.json", state / "trials.lock"


def _load(path: Path) -> dict:
    _safe_file(path, missing=True)
    if not path.exists():
        return {"version": 1, "tasks": []}
    if path.stat().st_size > 1_000_000:
        raise ValueError("trial ledger too large")
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("malformed trial ledger") from None
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "tasks"}
        or type(data.get("version")) is not int
        or data["version"] != 1
        or not isinstance(data.get("tasks"), list)
        or len(data["tasks"]) > 1000
    ):
        raise ValueError("malformed trial ledger")
    ids = set()
    sessions = defaultdict(list)
    for task in data["tasks"]:
        if not _valid_task(task) or task["id"] in ids:
            raise ValueError("malformed trial ledger")
        ids.add(task["id"])
        sessions[task["session"]].append(task)
    for tasks in sessions.values():
        ordered = sorted(tasks, key=lambda task: task["start"])
        for previous, following in zip(ordered, ordered[1:]):
            if previous["end"] is None or following["start"] < previous["end"]:
                raise ValueError("overlapping tasks in trial ledger")
    return data


def _valid_time(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 4_000_000_000
    )


def _valid_task(task) -> bool:
    if not isinstance(task, dict):
        return False
    required = {
        "id",
        "session",
        "project",
        "kind",
        "scope",
        "risk",
        "uncertainty",
        "effort",
        "check",
        "client",
        "baseline",
        "policy",
        "start",
        "end",
        "arm",
        "assignment_method",
        "had_rework",
    }
    closed = task.get("end") is not None
    if set(task) != required | ({"outcome", "checks"} if closed else set()):
        return False
    if not all(
        isinstance(task.get(key), str) and _ID.fullmatch(task[key])
        for key in ("id", "session", "project", "policy")
    ):
        return False
    if not isinstance(task.get("baseline"), str) or not _MODEL.fullmatch(
        task["baseline"]
    ):
        return False
    if any(not _enum(task.get(key), _ENUMS[key]) for key in _ENUMS):
        return False
    if (
        not _enum(task.get("assignment_method"), _METHODS)
        or not isinstance(task.get("had_rework"), bool)
        or not _valid_time(task.get("start"))
    ):
        return False
    if not closed:
        return True
    return (
        _valid_time(task["end"])
        and task["end"] >= task["start"]
        and _enum(task.get("outcome"), _OUTCOMES)
        and _enum(task.get("checks"), _CHECKS)
        and not (task["outcome"] == "accepted" and task["checks"] == "failed")
    )


def _save(path: Path, data: dict) -> None:
    encoded = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    if len(encoded) > 1_000_000:
        raise ValueError("trial ledger too large")
    fd, name = tempfile.mkstemp(prefix=".trials-", dir=path.parent)
    os.fchmod(fd, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def _locked(root: Path, fn):
    path, lock = _paths(root)
    _safe_file(lock, missing=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_uid != os.getuid() or os.fstat(fd).st_mode & 0o077:
            raise ValueError("unsafe trial lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = _load(path)
        result = fn(data)
        _save(path, data)
        return result
    finally:
        os.close(fd)


def start(
    root,
    thread,
    *,
    kind,
    scope,
    risk,
    uncertainty,
    effort,
    check="review",
    client="cli",
    arm=None,
    project=None,
    now=None,
) -> dict:
    root = Path(root)
    ts = _now(now)
    cohort = {
        k: v
        for k, v in {
            "kind": kind,
            "scope": scope,
            "risk": risk,
            "uncertainty": uncertainty,
            "effort": effort,
            "check": check,
            "client": client,
        }.items()
    }
    if any(not _enum(v, _ENUMS[k]) for k, v in cohort.items()) or arm not in (
        None,
        "auto",
        "baseline",
    ):
        raise ValueError("invalid trial cohort")
    session = _thread(thread)
    projecthash = _hash(str(Path(project or Path.cwd()).resolve()))
    baseline = _baseline(root, effort, client)
    policy = _fingerprint(root, client)

    def add(data):
        if len(data["tasks"]) >= 1000:
            raise ValueError("trial ledger task limit reached")
        if any(
            t.get("session") == session and t.get("end") is None for t in data["tasks"]
        ):
            raise ValueError("thread already has an open task")
        if any(
            t.get("session") == session and t.get("end") is not None and ts < t["end"]
            for t in data["tasks"]
        ):
            raise ValueError("task would overlap prior thread work")
        key = (projecthash, *cohort.values(), baseline, policy)
        prior = [
            t
            for t in data["tasks"]
            if tuple(
                t.get(k)
                for k in (
                    "project",
                    "kind",
                    "scope",
                    "risk",
                    "uncertainty",
                    "effort",
                    "check",
                    "client",
                    "baseline",
                    "policy",
                )
            )
            == key
            and t.get("assignment_method") == "randomized"
        ]
        if arm is None:
            # Each pair contains both arms; randomize which arm starts it.
            arm2 = (
                ("auto" if os.urandom(1)[0] & 1 else "baseline")
                if len(prior) % 2 == 0
                else ("baseline" if prior[-1]["arm"] == "auto" else "auto")
            )
            method = "randomized"
        else:
            arm2, method = arm, "observational"
        task = {
            "id": os.urandom(12).hex(),
            "session": session,
            "project": projecthash,
            **cohort,
            "baseline": baseline,
            "policy": policy,
            "start": ts,
            "end": None,
            "arm": arm2,
            "assignment_method": method,
            "had_rework": False,
        }
        data["tasks"].append(task)
        return {
            "task_id": task["id"],
            "arm": arm2,
            "assignment_method": method,
            "suggested_picker": (
                "Effortlane Auto" if arm2 == "auto" else "Effortlane Shadow"
            ),
            "baseline_executor": {"model": baseline, "effort": effort},
            "applied": False,
        }

    return _locked(root, add)


def _find(data, task_id):
    if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
        raise ValueError("invalid task id")
    for task in data["tasks"]:
        if task.get("id") == task_id:
            return task
    raise ValueError("unknown task")


def finish(root, task_id, *, outcome, checks, now=None) -> dict:
    if (
        not _enum(outcome, _OUTCOMES)
        or not _enum(checks, _CHECKS)
        or (outcome == "accepted" and checks == "failed")
    ):
        raise ValueError("invalid outcome/checks")
    ts = _now(now)

    def close(data):
        task = _find(data, task_id)
        if task.get("end") is not None or ts < task["start"]:
            raise ValueError("task is not open")
        task.update(end=ts, outcome=outcome, checks=checks)
        task["had_rework"] |= outcome == "rework"
        return {"task_id": task_id, "outcome": outcome, "checks": checks}

    return _locked(Path(root), close)


def reopen(root, task_id, now=None) -> dict:
    ts = _now(now)

    def reopen_task(data):
        task = _find(data, task_id)
        if task.get("end") is None or ts < task["end"]:
            raise ValueError("task is not closed")
        if any(
            t is not task
            and t.get("session") == task["session"]
            and (t.get("end") is None or t.get("start", 0) >= task["end"])
            for t in data["tasks"]
        ):
            raise ValueError("newer or overlapping task exists")
        task["had_rework"] = task.get("had_rework") or task.get("outcome") == "accepted"
        task.pop("outcome", None)
        task.pop("checks", None)
        task["end"] = None
        return {"task_id": task_id, "reopened": True}

    return _locked(Path(root), reopen_task)


def _int(row, key):
    value = row.get(key)
    return (
        value
        if isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 2**63 - 1
        else None
    )


def _valid_routing_metadata(row):
    return ("manual_override" not in row or isinstance(row["manual_override"], bool)) and (
        row.get("reason") is None or _enum(row["reason"], _SAFE_REASONS)
    )


def _valid_route(row):
    return (
        isinstance(row, dict)
        and row.get("event") == "route"
        and isinstance(row.get("route_id"), str)
        and _ID.fullmatch(row["route_id"])
        and isinstance(row.get("session"), str)
        and _ID.fullmatch(row["session"])
        and _enum(row.get("client"), _CLIENTS)
        and isinstance(row.get("model"), str)
        and _MODEL.fullmatch(row["model"])
        and _enum(row.get("effort"), _EFFORT)
        and _enum(row.get("mode"), {"auto", "shadow", "off", "native"})
        and _valid_routing_metadata(row)
    )


def _receipt(row):
    """A hashable receipt only; malformed telemetry becomes incomplete coverage."""
    if not _valid_routing_metadata(row):
        return None
    values = tuple(
        row.get(k)
        for k in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "turn_duration_ms",
        )
    )
    if (
        type(row.get("schema_version")) is not int
        or row["schema_version"] != 3
        or not isinstance(row.get("turn_hash"), str)
        or not _ID.fullmatch(row["turn_hash"])
        or any(
            v is not None and (not isinstance(v, int) or isinstance(v, bool))
            for v in values
        )
        or not _enum(
            row.get("usage_scope"), {"turn_total", "last_model_call", "model_call"}
        )
        or not _enum(
            row.get("usage_coverage_reason"),
            {
                "cumulative_delta",
                "baseline_missing",
                "counter_reset",
                "invalid_total",
                "compacted",
                "no_usage",
                "total_missing",
            },
        )
    ):
        return None
    status = row.get("status")
    if status is not None and not _enum(
        status, {"ok", "error", "cancelled", "unknown"}
    ):
        return None
    return (
        row["turn_hash"],
        *values,
        row["usage_scope"],
        row["usage_coverage_reason"],
        status,
        row.get("cached_input_observed") is True,
        row.get("manual_override") is True,
        row.get("reason") == "concrete_model",
    )


def report(root, rows, hours=168, now=None) -> dict:
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 720:
        raise ValueError("hours must be 1..720")
    ts = _now(now)
    cutoff = ts - hours * 3600
    root = Path(root)
    path, _ = _paths(root)
    data = _load(path)
    selected = [
        task
        for task in data["tasks"]
        if task["start"] <= ts and (task["end"] is None or task["end"] >= cutoff)
    ]
    fingerprints = {
        client: _fingerprint(root, client)
        for client in {task["client"] for task in selected}
    }
    rows = rows if isinstance(rows, list) else []
    tasks_out = []
    buckets = defaultdict(list)
    for task in selected:
        end = task["end"] if task["end"] is not None else ts
        observation_start = max(task["start"], cutoff)
        lookback_truncated = cutoff > task["start"]
        session_rows = [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("session") == task["session"]
            and _enum(row.get("event"), {"route", "usage"})
        ]
        unknown_time = sum(not _valid_time(row.get("ts")) for row in session_rows)
        timed = [row for row in session_rows if _valid_time(row.get("ts"))]
        # Integer-second events cannot be placed precisely at fractional task boundaries.
        boundary_seconds = {math.floor(task["start"]), math.floor(end)}
        boundary = [
            row
            for row in timed
            if float(row["ts"]).is_integer()
            and int(row["ts"]) in boundary_seconds
            and cutoff <= row["ts"] <= end
        ]
        boundary_ids = {id(row) for row in boundary}
        window = [
            row
            for row in timed
            if observation_start <= row["ts"] <= end and id(row) not in boundary_ids
        ]
        routes = [row for row in window if _valid_route(row)]
        invalid_routes = sum(
            row.get("event") == "route" and not _valid_route(row) for row in window
        )
        by_id = defaultdict(list)
        for row in routes:
            by_id[row["route_id"]].append(row)
        complete = partial = conflicts = mismatches = 0
        input_total = output_total = cache_total = 0
        durations, linked_turns, linked_usage_ids = [], set(), set()
        for route_id, copies in by_id.items():
            route = copies[0]
            if (
                len(
                    {
                        (row["client"], row["model"], row["effort"], row["mode"])
                        for row in copies
                    }
                )
                != 1
            ):
                conflicts += 1
                partial += 1
                continue
            same_route = [
                row
                for row in window
                if row.get("event") == "usage" and row.get("route_id") == route_id
            ]
            uses = [
                row
                for row in same_route
                if all(
                    row.get(key) == route[key]
                    for key in ("session", "client", "model", "effort", "mode")
                )
            ]
            mismatches += len(same_route) - len(uses)
            linked_usage_ids.update(id(row) for row in uses)
            receipts = {_receipt(row) for row in uses}
            if len(receipts) != 1 or None in receipts:
                partial += 1
                continue
            usage = uses[0]
            counts = [
                _int(usage, key)
                for key in ("input_tokens", "output_tokens", "cached_input_tokens")
            ]
            valid = (
                usage.get("usage_scope") == "turn_total"
                and usage.get("usage_coverage_reason") == "cumulative_delta"
                and usage.get("cached_input_observed") is True
                and all(value is not None for value in counts)
                and counts[2] <= counts[0]
            )
            if not valid or usage["turn_hash"] in linked_turns:
                partial += 1
                continue
            linked_turns.add(usage["turn_hash"])
            complete += 1
            input_total += counts[0]
            output_total += counts[1]
            cache_total += counts[2]
            duration = _int(usage, "turn_duration_ms")
            if duration is not None:
                durations.append(duration)
        usages = [row for row in window if row.get("event") == "usage"]
        unlinked = sum(id(row) not in linked_usage_ids for row in usages)
        manual = any(
            row.get("manual_override") is True or row.get("reason") == "concrete_model"
            for row in routes + usages
        )
        expected_mode = "auto" if task["arm"] == "auto" else "shadow"
        adherent = (
            bool(routes)
            and not manual
            and all(row["mode"] == expected_mode for row in routes)
            and all(row.get("client") == task["client"] for row in routes + usages)
        )
        if task["arm"] == "baseline":
            adherent = adherent and all(
                row["model"] == task["baseline"] and row["effort"] == task["effort"]
                for row in routes
            )
        closed = task["end"] is not None
        drift = task["policy"] != fingerprints[task["client"]]
        coverage_gap = bool(
            not routes
            or partial
            or unlinked
            or invalid_routes
            or boundary
            or unknown_time
            or lookback_truncated
        )
        comparable = (
            closed and complete > 0 and not coverage_gap and adherent and not drift
        )
        observed = {
            "task_id": task["id"],
            "arm": task["arm"],
            "client": task["client"],
            "assignment_method": task["assignment_method"],
            "outcome": task.get("outcome"),
            "checks": task.get("checks"),
            "had_rework": task["had_rework"],
            "open": not closed,
            "elapsed_ms": int((end - task["start"]) * 1000),
            "policy_drift": drift,
            "lookback_truncated": lookback_truncated,
            "boundary_ambiguous_count": len(boundary),
            "unknown_time_records": unknown_time,
            "invalid_routes": invalid_routes,
            "routes": len(by_id),
            "duplicate_route_records": len(routes) - len(by_id),
            "linked": complete,
            "full": complete,
            "partial": partial,
            "conflicting_routes": conflicts,
            "link_mismatches": mismatches,
            "unlinked": unlinked,
            "coverage_gap": coverage_gap,
            "manual_override_observed": manual,
            "actual_mode_counts": {
                mode: sum(row["mode"] == mode for row in routes)
                for mode in ("auto", "shadow", "off", "native")
            },
            "adherent": adherent,
            "comparable": comparable,
            "input_tokens": input_total if complete else None,
            "output_tokens": output_total if complete else None,
            "cached_input_tokens": cache_total if complete else None,
            "turn_duration_ms": durations,
        }
        tasks_out.append(observed)
        cohort = tuple(
            task[key]
            for key in (
                "project",
                "kind",
                "scope",
                "risk",
                "uncertainty",
                "effort",
                "check",
                "client",
                "baseline",
                "policy",
                "assignment_method",
                "arm",
            )
        )
        buckets[cohort].append(observed)
    groups = {}
    for key, group in buckets.items():
        comparable = [task for task in group if task["comparable"]]
        groups["/".join(key)] = {
            "registered": len(group),
            "open": sum(task["open"] for task in group),
            **{
                label: sum(task["outcome"] == label for task in group)
                for label in ("accepted", "rework", "failed")
            },
            "had_rework_tasks": sum(task["had_rework"] for task in group),
            "comparable_tasks": len(comparable),
            **{
                "median_"
                + field: (
                    median([task[field] for task in comparable]) if comparable else None
                )
                for field in (
                    "input_tokens",
                    "output_tokens",
                    "cached_input_tokens",
                    "elapsed_ms",
                )
            },
        }
    return {
        "tasks": tasks_out,
        "cohorts": groups,
        "hours": hours,
        "subscription_allowance_savings": None,
        "quality_equivalent_savings": None,
        "limits": "Registered-thread observations only; human outcomes are not correctness proof; complete absence of telemetry cannot be detected; no subscription savings inferred.",
    }
