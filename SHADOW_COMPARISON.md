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

## Work-shape policy — 2026-09-25

`completion_v3` replaces one large model/effort Choice with two independent questions in a single Jev call: work shape (`mechanical`, `routine`, `substantive`, `unknown`) and reasoning depth. Code maps shape to the catalog/allowlist, preserves risk and large-context floors, and sends `unknown` to Sol. No numeric confidence gate is enabled without outcome calibration.

Seven synthetic, privacy-safe probes checked obvious boundaries in English and Russian. V3 chose Luna/low for a variable rename, Terra/medium for a bounded parser, Sol/high for integration debugging and security architecture, Sol/medium for an evidence-poor "Continue the task", Luna/low for a README edit, and Sol/high for a Russian integration bug. V2 chose Luna/low for that evidence-poor continuation and Sol/medium for the debugging probe. The remaining role choices matched. These examples justified the initial v3 rollout but were **not** a SWE quality benchmark or a Pro savings measurement. Shadow retains v2 for comparison. Review real corrections, retries, cache behavior, and task outcomes before claiming an improvement.

## Long-context switch guard — 2026-09-25

The installed v3 policy had an absolute 48k-token Sol floor and a second lease rule that allowed downgrades only in short sessions. In one six-hour sample, 22 of 24 Desktop Auto routes were above the threshold and all 24 executed Sol. This was a policy failure for the goal of evaluating cheaper models, despite healthy Jev and native execution.

V4 leaves Luna and Terra eligible at any measured context size. It keeps the deterministic risk floor and `unknown`→Sol behavior. When a previous stronger model has a context above the configurable threshold or at least 80% measured cache reads, v4 waits for three distinct valid Jev recommendations for the same cheaper role before switching. An invalid answer, Jev outage, or Sol recommendation resets the streak. Upgrades remain immediate. The streak is metadata only; no prompt or source is persisted. This is a heuristic to limit cache thrashing, not a calibrated confidence gate or proof of Pro allowance savings.

Unit tests cover the long-context switch, reset, hot-cache guard, manual choice, and timeout fallback. A live Jev probe on three simple turns proposed Luna each time and selected it on the third; a native `codex exec --jev-auto` smoke chose Luna/low and completed correctly. A vague CLI request chose Sol/medium. These are function tests, not a quality or savings benchmark. Existing Desktop app-server processes must restart before using v4.

After v4 promotion, the original Shadow v2 setting was not comparable with Auto v4. New installs and the reference configuration now run Shadow v4: Sol remains the executor and the proposal follows the same policy as Auto. Earlier v2 receipts remain in historical reports under their own policy version.
