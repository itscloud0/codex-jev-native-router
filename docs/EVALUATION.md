# Measuring value on a subscription

The target is accepted engineering work per allowance window, with no material
increase in defects, rework, or completion time. The monthly fee is fixed: a 10
percentage-point meter change is not a $20 charge or saving.

## What Shadow establishes

Shadow records proposed model/effort while a fixed native executor does the work.
It establishes proposal frequency, latency, failure rate, and observed executor
usage. It cannot establish the output length, correctness, cache behavior, or
allowance consumption of a proposal that never ran. Lower effort is a hypothesis,
not a measured percentage saving. Choice confidence is not a quality score.

## Prospective comparison

1. Keep the Codex version, policy, model catalog, tools, and starting effort fixed
   within a comparison period. Record upgrades as a new period. First verify that
   new usage records have `turn_total` coverage; incomplete records must remain
   visible and cannot support full-task totals.
2. Before starting a task, record coarse characteristics locally: repository,
   work type (mechanical/routine/debugging/design), scope (single component or
   cross-component), uncertainty (known approach or investigation), risk, initial
   context-size band, and concrete acceptance checks. Do not use the observed
   route, elapsed time, or final success to decide which tasks are comparable.
3. Within comparable buckets, preassign task-level Auto versus a fixed model and
   effort. Shuffle small balanced blocks before seeing results. Keep that
   assignment for the task and its follow-up corrections; do not randomize each
   tool call or switch models inside a fixed baseline. Shadow with the fixed
   executor is an instrumented baseline, with its Jev overhead reported
   separately. It is not a zero-overhead native control.
4. Record accepted/rework/failed plus evidence from the predefined checks. Include
   follow-up fixes and unfinished/abandoned tasks. Keep collecting defects for a
   predefined follow-up interval; an immediate native `completed` status is not
   acceptance. Existing `evaluate --labels` accepts local route-level
   `{"route_id":"<24 hex characters>","outcome":"accepted|rework|failed"}`
   JSONL. Those labels alone are not a task ledger: relate all relevant turns and
   child work to the task in your local project record.
5. Compare completion time, acceptance/rework rates, full-coverage input/cache/
   output/reasoning tokens, model switches, and Jev overhead per task. Separate
   client, version, task bucket, and missing coverage. Report distributions and
   uncertainty; do not let one large task or many trivial turns dominate a claim.

Account quota snapshots are rounded, account-wide observations. Concurrent chats,
other clients, or modes prevent attribution to one task or route. For an
allowance-level comparison, use preassigned periods with one mode across all
participating clients, record other account use, and keep reset windows separate.
That compares whole workflows and is more confounded by changing work than
task-level assignment; report that limitation. Never sum overlapping quota groups.

Collect across multiple working weeks/reset windows, with comparable work in each
arm. More elapsed days do not fix biased assignment or missing outcomes. Until
there is enough coverage and uncertainty excludes a material quality regression,
the honest conclusion remains **inconclusive**, even if a token-price simulation
looks favorable. Report TypeSafe charges from actual billing records separately;
do not mix simulated OpenAI API prices with the ChatGPT subscription meter.

## Local task ledger

Register before starting the work, from its repository directory. Use the thread
UUID from Codex. The UUID and repository path are hashed locally; neither raw
value, task text, source code, nor check output is stored in the ledger.

```sh
effortlane trial start THREAD_UUID --kind routine --scope component \
  --risk low --uncertainty known --effort medium --check tests
```

This returns a task ID and a balanced randomized assignment: **Effortlane Auto**
or **Effortlane Shadow** with the pinned native Sol and effort. The default client
is CLI; add `--client desktop` for a Desktop task. Its native model catalog is
resolved separately. Select that mode
yourself before the first task turn. The command returns `applied: false`: it
does not change any model, config, or running conversation. `--arm auto` or
`--arm baseline` records an explicitly chosen, observational assignment instead.
Keep observational and randomized results separate.

The registered effort pins the Shadow baseline. Auto can select its own model
and effort. Manual model choices always win and are reported as nonadherence,
not silently counted as the assigned treatment. Keep one task per thread;
registering another task while one is open fails. A materially different task
belongs in a fresh session, per the normal context workflow.

Record the outcome only after the predefined check. These are human labels, not
automatically verified quality claims:

```sh
effortlane trial finish TASK_ID --outcome accepted --checks passed
effortlane trial reopen TASK_ID   # include a follow-up correction in the same task
effortlane trial report --hours 168
```

Use `rework` or `failed` when appropriate, and `failed` or `not_run` for the check
result. Reopening preserves the original assignment and records rework after an
accepted outcome. It is refused if a later task already occupies that thread.
Abandoned tasks must be marked failed; open tasks remain visible in reports.

Reports use exact route/session/client/model/effort links and validated full-turn
receipts. Missing, partial, conflicting, mismatched, out-of-window, and manual
override data cannot establish a comparable complete observation. Medians include
eligible failed tasks as well as successes. Cohorts separate repositories,
registered characteristics, client, baseline, policy fingerprint, and assignment method.
The fingerprint includes the effective client catalog, routing code, and selected
non-secret routing settings; changes make prior task observations ineligible for
the current comparison. Integer-second events on task boundaries and events with
unusable timestamps remain explicit ambiguities instead of disappearing.

This first ledger covers the registered native thread, not automatically all
subagents or other chats. It cannot discover completely absent telemetry or
prove the outcome labels. Record external work, initial context band, concrete
check evidence, and the follow-up interval in the project record. Changes to tools,
Codex versions, and an intervening policy change that was later reverted cannot
all be reconstructed from telemetry. Keep comparison periods stable. Token
observations and elapsed time still do **not** identify per-task subscription
allowance or establish a percentage saving.

State is owner-only `state/trials.json` under the installation directory, bounded
to 1,000 tasks / 1 MB with locking and atomic writes. Malformed or unsafe files
are rejected. Nothing is uploaded or silently removed when the bound is reached.

## Local inspection

```sh
effortlane metrics --hours 168
effortlane doctor
effortlane trial report --hours 168
effortlane evaluate --hours 168 --labels /absolute/path/to/private-outcomes.jsonl
effortlane claude-report --hours 168
```

No experiment is automatically enabled by these commands. Claude Shadow currently
records proposals only; it does not measure Claude executor tokens or subscription
quota. No statistically supported subscription saving has been established yet.
