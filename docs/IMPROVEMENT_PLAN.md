# Measurement and Claude Code integration

## Independent routing under concurrency

Goal: a slow Jev request in one chat must not force an unrelated chat to Sol.
Reproduced on 2026-10-02 with a blocked synthetic Jev response: the second chat
returns `state_error` after the 250 ms global lock wait. A bounded sample of 160
recent route records contained no such errors, so this does not explain past
allowance consumption. The fix targets a demonstrated concurrency failure.

Files: `core.py`, focused concurrency tests, operation notes. Keep the existing
lease format and model policy. Use bounded session lock stripes, brief global
read/commit locks, re-read-and-merge commits, and reject stale same-session
decisions. Include mixed old/new process behavior in limitations. No new daemon,
dependency, auth change, or live-process restart.

Verification: observe the regression fail first; test parallel decisions, no lost
updates, same-session serialization, legacy-writer conflicts, and bounded lock
failure including separate processes. Then full tests, atomic local deployment
with backup, native status checks, commit and CI. Stop at verified publication.

Verification so far: all 257 tests passed, including nine concurrency regressions.
The blocked-request fixture failed before the fix and now allows the second chat
to select Luna while the first is still waiting; both leases survive. Tests also
cover a shared Router, separate processes, same-session reuse and timeouts,
stripe collisions, legacy writers, and retaining already-spent Jev token counts
when a stale decision cannot be committed. No live model request was needed.

The [TypeSafe state](https://docs.typesafe.ai/concepts/state) and
[fan-out](https://docs.typesafe.ai/patterns/fan-out) guidance supports the existing
bounded state and batched independent questions. Its
[confidence routing](https://docs.typesafe.ai/patterns/confidence-routing) examples
do not establish calibrated thresholds for our workload. This change therefore
preserves model policy rather than retuning it without quality evidence.

## Follow-up: prospective task evidence

Implemented and installed, 2026-10-02. Added an owner-local task ledger (`trials.py`) with
preregistered task characteristics, balanced assignment, explicit human outcomes,
and strict linkage to full-turn receipts. Commands register and evaluate a trial;
they do not change the user's model or switch an ongoing conversation. Reports
must expose missing coverage, nonadherence, manual overrides, and unfinished work.
Add measurement-generation diagnostics to `doctor`, and reject Claude paste
wrapper contents before they can reach Jev. No dependency/auth changes or live
process restarts. Stop after tests, safe local deployment, and a published update.

Files: `trials.py` and tests; CLI/doctor integration in `manage.py`; small
measurement status helper in `metrics.py`; Claude paste filter/tests; usage docs.
Tests: assignment/persistence/privacy, full versus partial receipt attribution,
outcomes and rework, old versus current instrumentation, Claude marked-paste
filtering, then the complete existing suite. Live Claude validation remains gated
on an installed authenticated native CLI.

Verified follow-up:

- 248 tests passed, including malformed telemetry, duplicate/conflicting receipts,
  cache coverage, manual overrides, client catalogs, bounded ledger writes,
  concurrent registration, and persistent rework. Runtime compilation and diff
  checks passed. No project/runtime dependencies were added.
- Existing installed modules matched the previous release before backups and
  atomic replacement. Router/Codex config, manifest, and Desktop wrapper hashes
  remained unchanged. Active sessions were not restarted.
- Installed `doctor` passed all eight static checks and detected mixed telemetry
  generations. At 19:26 UTC, two new full-turn receipts were observed alongside
  seven schema-3 missing-baseline and 26 legacy records in a bounded 15-minute
  scan. This is instrumentation evidence, not economic or quality evidence.
- Installed task and Claude reports ran successfully with zero enrolled tasks
  and zero Claude events. Native bypass reported Codex 0.160.0. Claude live
  execution remains untested because the native CLI is absent.

Next: reopen older clients after active work ends; preregister real comparable
tasks using `trial start`, record outcomes and follow-up corrections, and inspect
coverage with `trial report`. No model changes or experiments were enabled by
installation. Continue to report savings as unknown until actual comparison data
and quality evidence support a conclusion.

## Previous release: full-turn accounting and Claude Shadow

Status: implementation and local installation complete, 2026-10-02.
Claude Code live validation remains pending because the native CLI is not installed.

## Goal and boundary

Make observed consumption trustworthy before changing routing policy. Add a
supported, opt-in Claude Code integration without taking over native authentication,
tools, or subscription billing. Do not claim savings from unexecuted Shadow proposals.

## Work

1. Inspect native Codex usage semantics. Account for a complete turn only when a
   reliable cumulative baseline and end are available. Mark partial coverage;
   handle resume, compaction, duplicate notifications, and counter resets.
   Files: `rpc_adapter.py`, `core.py`, related tests.
2. Correct quota-window grouping for bounded reset timestamp jitter. Keep actual
   resets separate and surface stale/decreasing observations. Separate full-turn
   and last-call totals in reporting. Files: `metrics.py`, tests, `docs/METRICS.md`.
3. Research official Claude Code integration surfaces; implement the smallest
   supported Shadow adapter with bounded sanitized dossiers and native execution.
   Test failure paths without installing dependencies or invoking billed models.
4. Document a prospective task-level comparison: preassigned mode, comparable
   task characteristics, accepted work and subsequent rework, complete usage
   coverage, and account-wide subscription caveats. Update public evidence only
   from validated aggregates.

## Verification and completion

Run targeted regressions followed by the existing unittest suite. Review the diff
for privacy and compatibility. Install existing Codex runtime changes atomically
with backups only after checking ownership; leave active sessions alone. Commit
and publish the reviewed changes. Claude live validation requires an installed,
authenticated Claude Code and is explicitly separate from fixture tests.

Stop when these changes and their limitations are documented and tested. No new
daemon, model-policy retuning, automated claim of savings, or unrelated redesign.

## Verified result

- 214 unittest cases passed, including full-turn accounting, reset jitter,
  malformed metadata, resume/fallback, silent Claude hook timeouts, manual settings
  protection, and a real fixture executable receiving native CLI arguments.
- Native Codex 0.160.0 schema plus 67 local native usage events checked against
  cumulative-counter invariants. No raw history was copied into this repository.
- One synthetic Claude Shadow decision passed through the real TypeSafe endpoint:
  Haiku/default, 379 ms, 625 input and 91 output Jev tokens. No Claude/OpenAI coding
  request was made by that check. This validates the contract, not route quality.
- Existing runtime files matched the previous release before owner-only backup
  and atomic replacement. Codex config, router config, manifest, and Desktop
  wrapper content remained unchanged. Installed doctor checks passed; native CLI
  bypass reported 0.160.0. Active sessions were not restarted.
- Historical full-turn coverage remains zero in the pre-update report; new
  sessions/adapters must load schema 3 before improved coverage can accumulate.
  Claude proposal reports correctly contain zero events until Claude is used.

Next: reload adapters after active work ends, check full-turn coverage on real
turns, then follow `EVALUATION.md` for a prospective comparison. Use `effortlane
claude` only after installing and authenticating native Claude Code. No allowance
saving, quality improvement, or universal Desktop-tool compatibility is claimed
by these checks.
