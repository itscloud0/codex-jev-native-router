# Measurement and Claude Code integration

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
