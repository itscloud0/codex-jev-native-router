# Effortlane metrics

```sh
effortlane metrics --hours 168
effortlane evaluate --hours 168
effortlane savings --hours 168
```

`metrics` reports local observations, not a savings estimate. The report separates:

- Executed and proposed model/effort pairs, routing reasons, fallbacks, clients, and pseudonymous session counts.
- Measured Jev and router latency (p50/p95 with observation counts).
- Exact-linked usage: route ID, session, client, model, and effort must all match. Unlinked native calls cannot support routed-usage claims.
- Cached-input ratio only where native cache detail exists; missing coverage is explicit.
- Native quota snapshots grouped by limit, window duration, and reset time, tolerating up to 10 seconds of reset timestamp jitter within an anchored group. Groups do not grow through a chain of near timestamps and are split across a clear reset boundary. These are account-wide observations; other chats consume the same allowance. Decreasing values flag corrections. Conflicting endpoint observations produce a null delta, not an invented consumption figure.
- Optional reasoning-output tokens, turn duration, first assistant text latency, tool-item counts, compactions, command failures, and status. First assistant text can be commentary; it is not necessarily a completed answer.

The RPC adapter passively observes existing native notifications. It adds no polling service and does not change routing policy. New optional fields require a newly started adapter; ongoing Desktop/CLI processes retain their loaded code. HTTP execution does not expose the same turn-level signals, so its fields can be absent.

Since telemetry schema 3, Desktop and the CLI app-server adapter record a
**native-thread turn total** when they observe both a reliable cumulative baseline
and end. `usage_scope=turn_total` is the difference of native `tokenUsage.total`
counters. Repeated notifications are not added again. Context size and routing
cache affinity still use the last call, not accumulated input.

The first turn after attaching/resuming may have no baseline. Missing or invalid
totals, a counter decrease, or compaction make coverage incomplete. These cases
fall back to `last_model_call` with a fixed `usage_coverage_reason` such as
`baseline_missing`, `total_missing`, `invalid_total`, `counter_reset`, or
`compacted`. No usage notification means missing data, not zero consumption.
Future turns can recover coverage from subsequent valid counters. Notifications
pass through unchanged, and no additional model request or polling is introduced.

`complete_turns`, `token_totals_by_scope`, `cache_by_scope`, and coverage reasons
make these groups independently reviewable. Historical unknown-scope records stay
unknown. The legacy overall totals can mix scopes; use the per-scope totals for
analysis. A native-thread total is **not** a complete software task or a sum of all
subagent threads. A successful command or completed turn is only a weak quality
signal. Use outcome labels with `evaluate` for accepted work, rework, and failures.

The contract was checked against Codex 0.160.0's generated app-server schema,
[OpenAI's cumulative token implementation](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs),
and a bounded local sample of 67 native usage events (all counter invariants held;
64 adjacent comparisons were monotonic). Regression tests cover two-call turns,
duplicates, resume without a baseline, cancellation, compaction, and resets.
This does not certify every future Codex release or fill historical gaps.

## Privacy and retention

Events are allowlisted metadata. No full prompts, repository content, tool results, auth tokens, credit balances, or raw unknown quota IDs are persisted. Unknown quota IDs are hashed; model and effort fields are validated. Files are owner-only, and the existing locked log rotation remains in use. There is no automatic upload to GitHub or an analytics service. Publish code and aggregate evidence only after checking it for personal information.

## What this can establish

Over comparable periods, compare account allowance percentage points against accepted work, elapsed time, and rework. Keep reset windows separate. Snapshot deltas cannot attribute account consumption to an individual chat or isolate effort's causal effect. Shadow measures candidate decisions while the fixed executor runs; it cannot demonstrate their quality or allowance savings. `savings` uses token-rate counterfactuals and must not be treated as the economics of a $200 subscription.

Historical records lack newly added fields. Missing observations stay missing; the system does not fabricate zero consumption, complete task quality, or retroactive latency.

See [the prospective comparison protocol](EVALUATION.md) before making savings claims.
