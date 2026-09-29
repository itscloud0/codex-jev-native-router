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

## Observed usage audit — 2026-09-30

The local telemetry spans only about 50 hours, not a full subscription week. In a 72-hour query it contains 5,202 Auto-attributed model calls, but only 151 calls link to 155 pre-turn route decisions; 5,051 calls are unlinked and cannot prove what Jev chose for them. The old CLI gateway accounts for most of these. Of the 156 Auto route events seen in the raw 72-hour window, 129 selected GPT-6 Sol, 27 Luna, and none Terra. Work shape was `unknown` 70 times. Only two events recorded a model switch. This is evidence that the router is conservative, not evidence of an optimal policy.

On the 5,048 calls with a published model rate and recorded token counts, the observed mix is 4,220.6946 Codex credit-equivalent units versus 4,239.9610 for hypothetical all-GPT-6-Sol at the **same token counts**: about 19.27 units, 0.45%, before Jev. Legacy records did not mark whether cached-input details were actually present, so this is a sensitivity estimate. The 12-hour recent window is better linked: 26 Auto-attributed calls, all linked to pre-turn routing, 18 priced, observed mix 19.2118 versus all-GPT-6-Sol 22.3875 (14.2% same-token difference). These samples do not measure correction cost, task quality, actual Pro allowance debits, or TypeSafe billing. The currently published GPT-6.1-Sol cached-input credit rate is half GPT-6-Sol's; all-6.1-Sol is a lower-cost comparison for new work, but not a historically available baseline for these older calls.

The 72-hour Auto-attributed token records contain about 636.5M input tokens, 622.3M marked cached (97.8%), and 1.64M output tokens. This argues against a general cache-collapse explanation for the user's fast weekly usage depletion. The large volume and long-lived sessions are more plausible contributors, but these records do not prove causation. Public Codex credits are a proxy rather than Pro allowance accounting; TypeSafe publishes a [$0.042/M input-token rate with free output](https://typesafe.ai/blog/introducing-system-one-models-and-jev), so 80,088 metered Jev input tokens imply roughly $0.00336 at the published rate; actual account billing and promotional credits are unknown. `jev-codex savings --hours 12` and `jev-codex savings --hours 168` now expose the link gap and cache-detail provenance. New records explicitly mark when native cached-input detail is absent so they cannot be priced as if all input were uncached.

The routing policy was not changed from v4 in this audit. A chat-wide permanent model floor would be speculative: the measured cached-input ratio is already high and switching is rare. The actionable next measurement is a comparable task cohort with corrections, retries, elapsed time, and all model calls linked to pre-turn routes, then compare Auto to fixed GPT-6.1 Sol at equivalent quality. Jev receives no full thread or source to improve its decisions; it already receives bounded task text plus context size and recent cache/switch state. More raw history would increase privacy risk and Jev latency without established benefit.
