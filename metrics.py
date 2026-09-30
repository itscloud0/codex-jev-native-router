"""Privacy-safe, aggregate-only telemetry metrics.

This module intentionally reports observations and coverage, never prompts,
responses, identifiers, API prices, or claims of subscription savings.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from typing import Any


_ROUTE_ID = re.compile(r"[a-f0-9]{24}")
_SESSION = re.compile(r"[a-f0-9]{16,64}")
_MODEL = re.compile(r"gpt-\d+(?:\.\d+)*(?:-[a-z][a-z0-9]*)?")
_EFFORT = {"low", "medium", "high", "xhigh", "max", "ultra"}
_SAFE_CLIENTS = {"cli", "desktop", "app-server", "proxy", "unknown", "other"}
_SAFE_REASONS = {
    "concrete_model", "catalog_unavailable", "sol_catalog_unavailable", "cannot_route",
    "state_error", "off", "lease", "continuation_lease", "fallback", "decision_cache",
    "jev", "jev_error", "jev_timeout", "invalid_decision", "privacy_fallback",
    "circuit_open", "lease_hysteresis", "cache_hysteresis", "requires_native_model_selection",
    "shadow_fallback", "shadow_decision_cache", "shadow_jev", "shadow_jev_error",
    "shadow_jev_timeout", "shadow_invalid_decision", "shadow_privacy_fallback",
    "shadow_circuit_open", "shadow_lease_hysteresis", "shadow_cache_hysteresis",
}
_FALLBACK_REASONS = {
    "catalog_unavailable", "sol_catalog_unavailable", "cannot_route", "state_error", "fallback",
    "jev_error", "jev_timeout", "invalid_decision", "privacy_fallback", "circuit_open",
    "shadow_fallback", "shadow_jev_error", "shadow_jev_timeout", "shadow_invalid_decision",
    "shadow_privacy_fallback", "shadow_circuit_open",
}
_SAFE_LIMIT_ID = re.compile(r"(?:codex(?:_[a-z0-9]+)*|quota-[a-f0-9]{24}|gpt-\d+(?:\.\d+)*(?:-[a-z][a-z0-9]*)?)")
_SAFE_PLAN_TYPES = {"free", "plus", "pro", "team", "business", "enterprise", "edu"}
_NONNEGATIVE_FIELDS = (
    "turn_duration_ms", "first_response_ms", "tool_calls", "command_failures",
    "compactions", "reasoning_output_tokens",
)


def _number(value: Any, *, minimum: float = 0) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        return None
    return float(value)


def _count(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _timestamp(value: Any) -> float | None:
    return _number(value)


def _pair(row: dict, model_key: str, effort_key: str) -> str:
    model = row.get(model_key)
    effort = row.get(effort_key)
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        model = "unknown"
    if not isinstance(effort, str) or effort not in _EFFORT:
        effort = "unknown"
    return f"{model}/{effort}"


def _percentile(values: list[float], percentile: float) -> float | int | None:
    if not values:
        return None
    ordered = sorted(values)
    value = ordered[math.ceil(len(ordered) * percentile) - 1]
    return int(value) if value.is_integer() else round(value, 3)


def _latency(values: list[float]) -> dict:
    return {"observations": len(values), "p50": _percentile(values, 0.50), "p95": _percentile(values, 0.95)}


def _safe_client(value: Any) -> str:
    return value if isinstance(value, str) and value in _SAFE_CLIENTS else "unknown"


def _link_key(row: dict) -> tuple[str, str, str, str, str] | None:
    route_id, session, client, model, effort = (row.get(key) for key in ("route_id", "session", "client", "model", "effort"))
    if (not isinstance(route_id, str) or not _ROUTE_ID.fullmatch(route_id)
            or not isinstance(session, str) or not _SESSION.fullmatch(session)
            or not isinstance(client, str) or client not in _SAFE_CLIENTS - {"unknown"}
            or not isinstance(model, str) or not _MODEL.fullmatch(model)
            or not isinstance(effort, str) or effort not in _EFFORT):
        return None
    return route_id, session, client, model, effort


def _subscription_component(value: Any) -> tuple[float, int, int] | None:
    if not isinstance(value, dict):
        return None
    used = _number(value.get("used_percent", value.get("usedPercent")))
    window = _count(value.get("window_minutes", value.get("windowDurationMins")))
    reset = _count(value.get("resets_at", value.get("resetsAt")))
    if used is None or used > 100 or window is None or window <= 0 or reset is None:
        return None
    return used, window, reset


def metrics_report(rows: list[dict], hours: int = 168, since: float | None = None) -> dict:
    """Return a bounded aggregate report for already privacy-filtered telemetry."""
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 720:
        raise ValueError("hours must be between 1 and 720")
    if since is not None and (_timestamp(since) is None):
        raise ValueError("since must be a nonnegative Unix timestamp")
    cutoff = max(time.time() - hours * 3600, float(since) if since is not None else 0.0)
    valid_rows: list[dict] = []
    malformed = 0
    for row in rows:
        if not isinstance(row, dict) or _timestamp(row.get("ts")) is None:
            malformed += 1
        elif float(row["ts"]) >= cutoff:
            valid_rows.append(row)

    routes = [row for row in valid_rows if row.get("event") == "route"]
    usage = [row for row in valid_rows if row.get("event") == "usage"]
    subscriptions = [row for row in valid_rows if row.get("event") == "subscription"]

    route_keys: set[tuple[str, str, str, str, str]] = set()
    route_pairs: Counter[str] = Counter()
    proposed_pairs: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    fallbacks: Counter[str] = Counter()
    clients: Counter[str] = Counter()
    sessions: set[str] = set()
    jev_latency: list[float] = []
    router_latency: list[float] = []
    valid_route_rows = 0
    for row in routes:
        session = row.get("session")
        client = _safe_client(row.get("client"))
        key = _link_key(row)
        if key is not None:
            route_keys.add(key)
            valid_route_rows += 1
        route_pairs[_pair(row, "model", "effort")] += 1
        proposed_pairs[_pair(row, "proposed_model", "proposed_effort")] += 1
        clients[client] += 1
        if isinstance(session, str) and _SESSION.fullmatch(session):
            sessions.add(session)
        reason = row.get("reason")
        if isinstance(reason, str) and reason in _SAFE_REASONS:
            reasons[reason] += 1
            if reason in _FALLBACK_REASONS:
                fallbacks[reason] += 1
        for field, target in (("jev_ms", jev_latency), ("router_ms", router_latency)):
            value = _number(row.get(field))
            if value is not None:
                target.append(value)

    linked_usage: list[dict] = []
    unlinked_native = 0
    for row in usage:
        key = _link_key(row)
        if key is not None and key in route_keys:
            linked_usage.append(row)
        elif row.get("mode") == "native":
            unlinked_native += 1

    statuses: Counter[str] = Counter()
    metric_totals = {field: 0 for field in _NONNEGATIVE_FIELDS}
    metric_counts = {field: 0 for field in _NONNEGATIVE_FIELDS}
    turn_duration: list[float] = []
    first_response: list[float] = []
    scopes: Counter[str] = Counter()
    token_totals = {field: 0 for field in ("input_tokens", "cached_input_tokens", "output_tokens")}
    token_counts = {field: 0 for field in token_totals}
    quality_signals: Counter[str] = Counter()
    cache_numerator = 0.0
    cache_denominator = 0.0
    cache_observed = 0
    cache_missing = 0
    for row in linked_usage:
        for field in token_totals:
            value = _count(row.get(field))
            if value is not None and (field != "cached_input_tokens" or row.get("cached_input_observed") is True or row.get("cache_observed") is True):
                token_totals[field] += value
                token_counts[field] += 1
        for field in ("prior_failed", "manual_override"):
            if row.get(field) is True:
                quality_signals[field] += 1
        status = row.get("status")
        statuses[status if status in {"ok", "error", "cancelled"} else "unknown"] += 1
        scope = row.get("usage_scope")
        scopes[scope if scope in {"last_model_call", "model_call"} else "unknown"] += 1
        observed = row.get("cache_observed") is True or row.get("cached_input_observed") is True
        input_tokens = _number(row.get("input_tokens"))
        cached = _number(row.get("cached_input_tokens"))
        if observed and input_tokens is not None and cached is not None and cached <= input_tokens:
            cache_observed += 1
            cache_numerator += cached
            cache_denominator += input_tokens
        else:
            cache_missing += 1
        for field in _NONNEGATIVE_FIELDS:
            value = _number(row.get(field))
            if value is not None:
                metric_totals[field] += int(value) if value.is_integer() else value
                metric_counts[field] += 1
                if field == "turn_duration_ms":
                    turn_duration.append(value)
                elif field == "first_response_ms":
                    first_response.append(value)

    subscription_groups: dict[tuple[str, str, int, int], list[tuple[float, float]]] = defaultdict(list)
    plan_types: Counter[str] = Counter()
    for row in subscriptions:
        limit_id = row.get("limit_id")
        if not isinstance(limit_id, str) or not _SAFE_LIMIT_ID.fullmatch(limit_id):
            continue
        plan = row.get("plan_type")
        if isinstance(plan, str) and plan in _SAFE_PLAN_TYPES:
            plan_types[plan] += 1
        ts = float(row["ts"])
        for component in ("primary", "secondary"):
            parsed = _subscription_component(row.get(component))
            if parsed is not None:
                used, window, reset = parsed
                subscription_groups[(limit_id, component, window, reset)].append((ts, used))
    subscription_view = []
    for (limit_id, component, window, reset), samples in sorted(subscription_groups.items()):
        # The same snapshot may be emitted by more than one observer. It is one
        # account-wide observation, not independent per-client consumption.
        samples = sorted(set(samples))
        first_ts, first_used = samples[0]
        last_ts, last_used = samples[-1]
        subscription_view.append({
            "limit_id": limit_id, "component": component, "window_minutes": window, "resets_at": reset,
            "observations": len(samples), "first": {"ts": first_ts, "used_percent": first_used},
            "last": {"ts": last_ts, "used_percent": last_used},
            "delta_percentage_points": round(last_used - first_used, 3),
            "reset_or_correction_observed": any(
                current_ts > previous_ts and current_used < previous_used
                for (previous_ts, previous_used), (current_ts, current_used) in zip(samples, samples[1:])
            ),
        })

    omitted_subscription_groups = max(0, len(subscription_view) - 200)
    subscription_view = subscription_view[:200]

    linked_route_ids = {row.get("route_id") for row in linked_usage}
    metric_totals["reasoning_output_tokens"] = (
        metric_totals["reasoning_output_tokens"] if metric_counts["reasoning_output_tokens"] else None
    )
    return {
        "window": {"hours": hours, "cutoff_ts": cutoff, "rows_in_window": len(valid_rows), "malformed_rows_skipped": malformed},
        "subscriptions": {"groups": subscription_view, "omitted_groups": omitted_subscription_groups,
                          "plan_type_observations": dict(sorted(plan_types.items())),
                          "note": "Negative usage deltas indicate a reset or correction; they are not savings claims."},
        "routes": {"decisions": len(routes), "valid_linkable_decisions": valid_route_rows,
                   "executed_pairs": dict(sorted(route_pairs.items())), "proposed_pairs": dict(sorted(proposed_pairs.items())),
                   "reasons": dict(sorted(reasons.items())), "fallbacks": dict(sorted(fallbacks.items())),
                   "clients": dict(sorted(clients.items())), "unique_pseudonymous_sessions": len(sessions),
                   "model_switches": sum(row.get("switched") is True for row in routes),
                   "latency_ms": {"jev": _latency(jev_latency), "router": _latency(router_latency)}},
        "usage": {"observed_calls": len(usage), "linked_calls": len(linked_usage),
                  "linked_route_decisions": len(linked_route_ids), "unlinked_calls": len(usage) - len(linked_usage),
                  "unlinked_native_calls_excluded_from_routed_claims": unlinked_native, "statuses": dict(sorted(statuses.items())),
                  "usage_scope": dict(sorted(scopes.items())),
                  "token_totals": token_totals, "token_observation_counts": token_counts,
                  "weak_quality_signals": dict(sorted(quality_signals.items())),
                  "cache": {"observed_calls": cache_observed, "missing_coverage_calls": cache_missing,
                            "cached_input_ratio": round(cache_numerator / cache_denominator, 6) if cache_denominator else None},
                  "turn_metrics": {"totals": metric_totals, "observation_counts": metric_counts,
                                   "latency_ms": {"turn_duration": _latency(turn_duration), "first_response": _latency(first_response)}}},
        "quality_equivalent_savings": None,
        "subscription_allowance_savings": None,
        "api_price_savings": None,
        "limits": "Routes may represent instructions rather than user tasks. Exact route_id, session, client, model, and effort linkage is required for routed usage. Aggregates do not establish quality or allowance savings.",
    }
