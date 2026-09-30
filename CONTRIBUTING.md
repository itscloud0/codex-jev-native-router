# Contributing to Effortlane

Effortlane currently integrates native Codex on macOS. Claude Code and Windows adapters are future work. Small, testable changes and evidence of completed-task outcomes are welcome.

## Development

Use Python 3.11+; the runtime uses the standard library. No dependency install is needed for the unit suite.

```sh
python3 -m unittest discover -s . -p 'test_*.py' -q
python3 -m py_compile core.py manage.py rpc_adapter.py desktop_bootstrap.py cli_bridge.py transport.py
```

Socket tests need loopback access. Report sandbox failures separately from product failures. Do not alter your real Codex configuration just to run tests.

## Useful reports

Include OS and native Codex version, client (Desktop / interactive CLI / exec / resume), mode, sanitized `doctor` findings, expected behavior, actual behavior, and a minimal reproduction. Remove personal paths and identifiers. Never attach keys, auth.json, complete prompts, source, or full session rollouts.

## Measuring value

Primary objective: less subscription allowance per correctly completed task. Record dated allowance snapshots within the same reset window, active model/effort, completion and rework, elapsed time, and concurrent account activity. Shadow proposals are not executed savings. API-price counterfactuals are not Pro allowance measurements. Aggregate anonymous metadata before sharing; measurements are opt-in.

## Pull requests

Explain the concrete problem and resulting behavior, add tests for runtime behavior changes, and list validation and limits. Preserve native authentication, manual override, bounded Jev input, and clean rollback. Avoid new dependencies and unrelated refactors. New adapters must document their authentication, billing, privacy, and fail-open behavior.

Ideas and routing comparisons belong in issues; private vulnerabilities follow [SECURITY.md](SECURITY.md).
