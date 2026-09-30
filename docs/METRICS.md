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
- Native quota snapshots grouped by limit, window duration, and reset time. These are account-wide observations; other chats consume the same allowance. Negative deltas flag a reset or correction, not savings.
- Optional reasoning-output tokens, turn duration, first assistant text latency, tool-item counts, compactions, command failures, and status. First assistant text can be commentary; it is not necessarily a completed answer.

The RPC adapter passively observes existing native notifications. It adds no polling service and does not change routing policy. New optional fields require a newly started adapter; ongoing Desktop/CLI processes retain their loaded code. HTTP execution does not expose the same turn-level signals, so its fields can be absent.

Desktop and the CLI app-server adapter currently record only the **last model call** reported at turn completion. They do not provide all internal calls for a long agent turn. `usage_scope` makes this explicit; never interpret the report as complete turn consumption. A successful command or completed native turn is a weak signal, not an accepted software task. Use outcome labels with `evaluate` for accepted work, rework, and failures.

## Privacy and retention

Events are allowlisted metadata. No full prompts, repository content, tool results, auth tokens, credit balances, or raw unknown quota IDs are persisted. Unknown quota IDs are hashed; model and effort fields are validated. Files are owner-only, and the existing locked log rotation remains in use. There is no automatic upload to GitHub or an analytics service. Publish code and aggregate evidence only after checking it for personal information.

## What this can establish

Over comparable periods, compare account allowance percentage points against accepted work, elapsed time, and rework. Keep reset windows separate. Snapshot deltas cannot attribute account consumption to an individual chat or isolate effort's causal effect. Shadow measures candidate decisions while the fixed executor runs; it cannot demonstrate their quality or allowance savings. `savings` uses token-rate counterfactuals and must not be treated as the economics of a $200 subscription.

Historical records lack newly added fields. Missing observations stay missing; the system does not fabricate zero consumption, complete task quality, or retroactive latency.
