# Claude Code Shadow (experimental)

`effortlane claude [native Claude arguments...]` is an experimental Claude Code
adapter. It starts the installed `claude` binary with its existing native login
and tools. It does not modify `~/.claude/settings.json`, aliases, PATH,
environment variables, or authentication.

The adapter generates an owner-only `claude-shadow-settings.json` fragment in
the Effortlane root. The fragment registers an asynchronous `UserPromptSubmit`
hook. Claude Code continues to choose and execute its own model. The hook sends
only a bounded, sanitized task dossier to Jev, then records an offline proposal
among `haiku`, `sonnet`, and `opus`; it never applies that proposal.

This is Shadow-only: there is no model override, no account-availability claim,
and no Claude quota or savings telemetry. Network errors, timeouts,
malformed events, and unavailable Jev fail open. The adapter does not
read transcripts. Prompts containing Claude's `<pasted_content ...>` wrapper,
including malformed or incomplete wrappers, are recorded only as
`privacy_filtered` and never route remotely or resolve the local key path. It
accepts no `-p`/`--print` mode in this first release.
The Jev socket timeout is two seconds and the hook process has a separate,
enforced three-second POSIX wall-time limit; the Claude async-hook timeout field
is not relied on for that deadline.

The native CLI arguments, including `--model` and `--resume`, are passed through
unchanged. A supplied `--settings` is refused so the generated fragment cannot
silently overwrite native settings. If the owned fragment is edited, launch
refuses to replace it. Claude Code must already be installed and signed in.

The generated hook format follows Claude Code's documented
[hooks](https://code.claude.com/docs/en/hooks), and native argument behavior
follows the [CLI reference](https://code.claude.com/docs/en/cli-reference).
Authentication remains subject to Claude Code's documented
[environment-variable precedence](https://code.claude.com/docs/en/env-vars); the
adapter leaves the native authentication and billing mode to Claude Code. Print
mode is unsupported in this first release.
Live Claude Code integration has not been validated; fixture tests cover the
adapter only.

## Use with an existing Effortlane installation

```sh
effortlane claude --model sonnet
effortlane claude --resume
effortlane claude-report --hours 168
```

The existing TypeSafe key is reused locally. No Claude package or account is
installed by these commands. To run without the hook, launch ordinary `claude`.
No persistent Claude setting needs to be undone. An already-open Effortlane
Claude session keeps its hook until that session exits.

Optional `claude_shadow_candidates` in the existing Effortlane `config.json`
restricts proposals, for example `{"haiku":["default"],"sonnet":["medium","high"]}`.
An explicitly empty/invalid allowlist disables proposals. Haiku `default` means
no effort override; other defaults use low/medium/high. These are semantic
families, not a discovered account catalog. Actual executor model/effort and
history are not read and remain unknown. Follow-up prompts without enough task
context may produce weak proposals: do not promote this adapter to Auto based
on agreement counts alone.

Reports show allowlisted proposal pairs, latency, failures, filtered-input counts,
and whether the bounded local log scan was truncated. Hook process deadlines may
end before a receipt is written; event count is not proof of complete coverage.
No raw prompts, paths, source, transcript, or auth data are logged. This release
requires the existing POSIX Python runtime; Windows support remains future work.
