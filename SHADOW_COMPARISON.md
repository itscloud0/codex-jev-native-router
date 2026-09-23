# Versioned Shadow policy comparison — 2026-09-23

The `completion_v1` policy adapts useful ideas from `0xNatoshi/jev-codex-router`: account for retries, corrections, context rebuilding, model capability, and cache-switch cost. It does not copy their per-call architecture, hardcoded model identifiers, or mandatory Astra rule. It uses our discovered model catalog and configured allowlist. Auto remains on `baseline`; Shadow can run `completion_v1`.

The read-only replay used 48 stratified historical first-user requests from the local Codex index. Each request was sanitized and clipped before transmission. Jev saw only the bounded task dossier. Output records contain pseudonymous IDs and decision metadata, not task text, source, tool output, keys or full conversations. Both policies ran on the same cases with Luna/Terra/Sol allowed and Astra excluded.

| Proposal | Baseline | completion_v1 |
|---|---:|---:|
| Luna | 29 | 3 |
| Terra | 15 | 19 |
| Sol | 4 | 26 |
| Median Jev latency | 423 ms | 416 ms |

Models agreed on 9/48 cases, efforts on 37/48, and the exact model/effort pair on 8/48. Each policy returned 47 direct Jev decisions and one decision-cache reuse. The candidate strongly favors Sol, which may improve hard-task completion or may waste Pro allowance on easy tasks. Historical manual model choice and first-turn text do not identify the correct executor. This replay **does not measure quality, total completion usage, or savings**; it is a disagreement and regression signal.

Small low-overhead additions: the Desktop adapter now passes only the previous turn's integer cached-input percentage when native Codex reports it; the candidate treats this as a switching-cost hint, never a capability ceiling. Route telemetry records the policy version, and `jev-codex report` groups proposals by policy. Neither feature stores prompts. The candidate stays in Shadow until outcome-labeled task comparisons support promotion.
