"""Published rate-card sensitivity estimates for privacy-safe router telemetry.

These rates are not a statement of ChatGPT subscription debits or Jev billing.
"""
from __future__ import annotations

from collections import defaultdict
import datetime as dt
import re


CODEX_CREDITS_URL = "https://learn.chatgpt.com/docs/pricing"
API_PRICES_URL = "https://developers.openai.com/api/docs/pricing"
# Standard, short-context published rates per million tokens, 2026-09-28.
# Tuple order: uncached input, cached input, output.
CODEX_CREDITS = {
    "gpt-6-luna": (2.5, 0.25, 12.5),
    "gpt-6-sol": (50, 5, 250),
    "gpt-6-astra": (250, 25, 1250),
    "gpt-5.6-luna": (5, 0.5, 30),
    "gpt-5.6-terra": (50, 5, 300),
    "gpt-5.6-sol": (100, 10, 500),
}
API_USD = {
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6-sol": (2, 0.20, 10),
    "gpt-6-astra": (10, 1, 50),
}


def _tokens(row: dict) -> tuple[int, int, int] | None:
    values = [row.get(key) for key in ("input_tokens", "cached_input_tokens", "output_tokens")]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        return None
    input_tokens, cached, output = values
    if cached > input_tokens:
        return None
    return input_tokens - cached, cached, output


def _charge(tokens: tuple[int, int, int], rates: tuple[float, float, float]) -> float:
    return sum(amount * rate for amount, rate in zip(tokens, rates)) / 1_000_000


def _view(rows: list[dict]) -> dict:
    known = [row for row in rows if _tokens(row) is not None and row.get("model") in CODEX_CREDITS]
    totals = tuple(sum(_tokens(row)[i] for row in known) for i in range(3))
    actual_credits = sum(_charge(_tokens(row), CODEX_CREDITS[row["model"]]) for row in known)
    sol_credits = _charge(totals, CODEX_CREDITS["gpt-6-sol"])
    astra_credits = _charge(totals, CODEX_CREDITS["gpt-6-astra"])
    api_known = [row for row in known if row["model"] in API_USD]
    api_tokens = tuple(sum(_tokens(row)[i] for row in api_known) for i in range(3))
    api_actual = sum(_charge(_tokens(row), API_USD[row["model"]]) for row in api_known)
    return {
        "calls": len(rows), "priced_calls": len(known), "unpriced_calls": len(rows) - len(known),
        "tokens": {"uncached_input": totals[0], "cached_input": totals[1], "output": totals[2]},
        "codex_credit_equivalent": {
            "observed_mix": round(actual_credits, 4),
            "all_sol_same_tokens": round(sol_credits, 4),
            "all_astra_same_tokens": round(astra_credits, 4),
            "vs_sol": round(sol_credits - actual_credits, 4),
            "vs_astra": round(astra_credits - actual_credits, 4),
        },
        "api_usd_equivalent": {
            "priced_calls": len(api_known), "unpriced_calls": len(rows) - len(api_known),
            "observed_mix": round(api_actual, 4),
            "all_sol_same_tokens": round(_charge(api_tokens, API_USD["gpt-6-sol"]), 4),
            "all_astra_same_tokens": round(_charge(api_tokens, API_USD["gpt-6-astra"]), 4),
        },
    }


def cost_report(rows: list[dict], hours: int = 168) -> dict:
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 720:
        raise ValueError("hours must be between 1 and 720")
    cutoff = dt.datetime.now(dt.timezone.utc).timestamp() - hours * 3600
    rows = [row for row in rows if isinstance(row.get("ts"), (int, float)) and row["ts"] >= cutoff]
    routes = {row["route_id"]: row for row in rows
              if row.get("event") == "route" and row.get("mode") == "auto"
              and isinstance(row.get("route_id"), str) and re.fullmatch(r"[a-f0-9]{24}", row["route_id"])}
    usage = [row for row in rows if row.get("event") == "usage"]
    linked = []
    clients = defaultdict(list)
    for row in usage:
        route = routes.get(row.get("route_id"))
        if (route and all(route.get(key) == row.get(key) for key in ("session", "client", "model", "effort"))):
            linked.append(row)
            clients[str(row.get("client"))].append(row)
    jev_routes = [row for row in rows if row.get("event") == "route" and row.get("mode") in ("auto", "shadow")]
    metered = [row for row in jev_routes if isinstance(row.get("jev_input_tokens"), int) and isinstance(row.get("jev_output_tokens"), int)]
    return {
        "window_hours": hours,
        "auto": {"route_decisions": len(routes), "linked_calls": len(linked),
                 "unlinked_decisions": len(routes) - len({row.get("route_id") for row in linked}),
                 "all_clients": _view(linked), "by_client": {key: _view(value) for key, value in sorted(clients.items())}},
        "observed_all_modes": _view(usage),
        "jev": {"route_decisions": len(jev_routes), "metered_requests": len(metered),
                "input_tokens": sum(row["jev_input_tokens"] for row in metered),
                "output_tokens": sum(row["jev_output_tokens"] for row in metered),
                "cost_usd": None, "cost_reason": "No public TypeSafe Jev tariff or billing export verified; old decisions did not record Jev tokens."},
        "sources": {"codex_credits": CODEX_CREDITS_URL, "api_usd": API_PRICES_URL,
                    "rate_card_checked": "2026-09-28", "tier": "standard_short_context"},
        "limits": "Same-token counterfactual, not quality-equivalent savings. Desktop may record only the last model call of a turn. Pro included usage is not a dollar charge; API rates are comparison units only. Fast/long-context rates are not applied.",
    }
