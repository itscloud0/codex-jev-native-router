# Codex Jev Native Router

[![tests](https://github.com/itscloud0/codex-jev-native-router/actions/workflows/tests.yml/badge.svg)](https://github.com/itscloud0/codex-jev-native-router/actions/workflows/tests.yml)

**Experimental, macOS-only.** This project routes authenticated Codex work by
model and reasoning effort. It has passed local unit and limited live smoke tests;
it has **not** demonstrated a particular ChatGPT Pro allowance saving or quality
equivalence across broad software-engineering tasks. Desktop integration depends
on an undocumented Codex launch override and should be rechecked after updates.

Local, owner-only Codex model router. Desktop uses a native app-server stdio adapter for model selection; its executor connects directly to the official ChatGPT Codex endpoint. The CLI wrapper chooses a concrete native model and effort before Codex builds its session and uses a loopback Responses gateway for usage telemetry. Authentication stays with the built-in Codex OpenAI provider. Jev receives only a bounded, sanitized task excerpt; its key is read from an owner-only file passed at install time or `~/.config/jev-codex-router/typesafe-api-key`. The gateway does not forward Codex bearer headers to Jev.

## Install and controls

Run from this directory after reviewing `manage.py`, `core.py`, and `transport.py`:

```sh
/opt/homebrew/bin/python3 manage.py install --execution-binary /Applications/ChatGPT.app/Contents/Resources/codex --key-file /absolute/path/to/owner-only-typesafe-key
jev-codex status
jev-codex report
jev-codex desktop-enable
jev-codex desktop-disable
jev-codex disable
jev-codex enable
jev-codex rollback
```

Install backs up `~/.codex/config.toml`, writes four managed root keys (`model`, `model_reasoning_effort`, `openai_base_url`, `model_catalog_json`), adds a user LaunchAgent on `127.0.0.1:43191`, and replaces `~/.local/bin/codex` with a symlink to the wrapper. The wrapper uses the selected Desktop bundled Codex binary, whose current model catalog was verified with the Desktop client. `~/.local/bin/codex-native` is an explicit bypass shim. It calls that same binary with the native endpoint and catalog, avoiding the global loopback URL. The prior `codex` symlink remains recorded for rollback. Owner-only files live in `~/.local/share/jev-codex-router`. No app bundle is modified. The initial global model is `jev-shadow`; it executes Sol and records proposals. Selecting `jev-auto` with the native Desktop adapter loaded enables real per-turn model selection.

## Policy configuration

Edit `~/.local/share/jev-codex-router/config.json` (owner-only), then restart the relevant Codex process to load code changes; ordinary policy values are read at each new route. `jev-codex status` shows the effective policy. Example values:

```json
{
  "auto_roles": ["luna", "terra", "sol"],
  "shadow_policy": "completion_v1",
  "effort_policy": "jev",
  "fixed_effort": "medium"
}
```

`auto_roles` is the executor allowlist and must include Sol. Add `"astra"` to let Jev choose Astra automatically, without a confirmation window; the default excludes it. Jev chooses only from roles available in the authenticated Codex model catalog and permitted by this list. High-risk work has a Sol floor and at least high effort; Jev may upgrade it to Astra when Astra is allowed. If Jev fails, the route falls back to Sol rather than spending Astra. `effort_policy` is `jev` or `fixed`; `fixed_effort` accepts `low`, `medium`, `high`, `xhigh`, `max`, or `ultra`. A manual effort choice in Desktop or `codex --jev-auto -c model_reasoning_effort=high "task"` overrides automatic effort. A concrete model choice overrides routing and the allowlist.

`shadow_policy` selects the experimental proposal policy for Jev Shadow: `baseline` or `completion_v1`. Jev Auto deliberately stays on `baseline`. `completion_v1` asks Jev to account for retries, corrections, context rebuilding, model capability, and measured prior-turn cached-input percentage where available. It does not send prompts, source or tool output. On a 48-case privacy-bounded historical replay, the policies agreed on only 9 model proposals; `completion_v1` proposed Sol on 26 cases versus 4 for `baseline`. Those first-turn cases have no trustworthy outcome labels, so the candidate has **not** been promoted to Auto. The route report groups proposals by policy. This policy was adapted from ideas in [0xNatoshi/jev-codex-router](https://github.com/0xNatoshi/jev-codex-router) under its MIT license; no source module was copied.

CLI calls with a visible initial prompt run in auto mode by default. `--jev-shadow` forces a Sol execution and records the proposed route. `--jev-auto` explicitly routes. `--jev-off` uses the native bypass. Manual `-m`/`--model`, `-c model=...`, and profile choices take precedence. A bare interactive `codex` starts on Sol; use `/model` for later manual changes. `codex exec resume` preserves its saved model, without reconstructing or reclassifying the old thread. A prompt supplied as `-` passes stdin untouched and starts on Sol. The wrapper does not reroute turns inside the native TUI. CLI calls use the native bypass if the local gateway is unavailable.

Healthy CLI calls use a capability protected `/cli` gateway path so telemetry identifies the client even when Codex sends Desktop-style headers. The wrapper leaves explicit custom base URLs, providers, and profiles untouched. Route decisions and executor usage are separate telemetry events; `report` counts requests and tokens only from usage events and shows Jev latency and proposals from route events.

`jev-codex disable` restores owned config keys and stops the gateway. `jev-codex rollback` additionally restores the original `codex` symlink and removes only the installed links and LaunchAgent. Backups, routing state, and telemetry remain for inspection. Manual edits to managed keys are preserved; `status` lists their names. Re-enabling may require resolving such conflicts. Both operations preserve unrelated config edits.

`jev-codex update` accepts only a native `~/.codex/models_cache.json` refreshed within ten minutes. It does not refresh authentication or use a temporary Codex home. If Sol alias metadata changed, first disable the router, update the catalog, enable, then fully restart Desktop. This keeps the running Desktop harness from using stale alias instructions. Check the compatibility gate after a Codex update.

## Report

`jev-codex report` aggregates recorded model, effort, token counts, cache tokens, route switches, failures, and Jev latency. To compare the observed mix against fixed all-Sol and all-Astra choices, supply your own relative weights:

```json
{
  "gpt-6-sol": {"input": 1, "cached_input": 0.1, "output": 2},
  "gpt-6-astra": {"input": 2, "cached_input": 0.2, "output": 4}
}
```

Run `jev-codex report --weights /absolute/path/weights.json`. Include weights for every observed model. The comparison holds token counts constant and is a sensitivity estimate. It does not measure Pro quota debits, quality equivalence, or money saved.

## Limits

The catalog alias is cloned from Sol only for picker compatibility. The native Desktop adapter resolves the concrete model before native turn construction, including collaboration-mode precedence, so the executor uses its own model instructions and tools. For older clients without the adapter, the HTTP gateway retains a compatibility gate and executes Sol when target metadata differs. CLI selects a native model at launch.

Desktop integration uses the bundled app's implemented but undocumented `CODEX_CLI_PATH` override. `desktop-enable` journals the previous value, installs an owner-only wrapper and a one-shot user LaunchAgent, and sets the GUI launch environment. Fully quit/reopen Desktop afterward. A configured `hostConfig.codex_cli_command` takes precedence. Presence of picker aliases or an environment setting alone does not prove the running app adopted the wrapper. Login launch ordering can also require reopening an already-started app.

The Desktop wrapper adds a command-line override for the official ChatGPT Codex URL, so native app-server execution bypasses the local Responses relay. The adapter records allowlisted token counts from native `thread/tokenUsage/updated` notifications at turn completion. If Python or the adapter file is missing at startup, the wrapper starts bundled native Codex with the same direct URL and original Desktop arguments; concrete models remain usable. This does not cover an adapter failure after it starts. The CLI still uses the relay for telemetry and can be interrupted if that relay dies mid-request.

`jev-codex status` includes `desktop.runtime.adapter_active` and `direct_native_app_server`, determined from the primary Desktop process's direct children without printing process arguments. This excludes standalone probes launched inside a Codex task. An installed override with `adapter_active=false` means the running Desktop backend has not adopted the adapter. Desktop may pass global `-c` flags before `app-server`; the adapter recognizes this argument order.

`desktop-disable` restores the owned environment and leaves CLI/relay enabled. Global `disable` and `rollback` also remove the Desktop override. A Desktop restart is required for environment changes to affect a running app. User changes to the environment or wrapper are preserved rather than silently overwritten.

Routing happens between user turns. Tool chains and active-turn steering keep their executor. Explicit concrete choices disable automatic intent. Resume with unknown context preserves a conservative native model. Unexpected transform errors and frames over 8 MiB pass unchanged to native Codex; the legacy HTTP compatibility gate remains the fallback. Prompt contents, source, tool output and auth are never written to the adapter sidecar.

The Desktop adapter keeps the logical Jev Auto/Shadow model in thread start/read/list metadata and settings notifications, while native execution receives the selected concrete model. This matters when the Desktop effort picker refreshes thread metadata. An explicit effort change on an alias is remembered for later turns; if the chosen executor lacks that effort (for example, Luna/Ultra), policy selects an available Sol model. A concrete model choice clears the alias and takes precedence.

The relay terminates a stream after a complete response.completed SSE frame instead of waiting for upstream EOF. It allows up to 16 simultaneous requests, remains loopback-only, and preserves native bearer forwarding only to the fixed ChatGPT endpoint. WebSocket requests still use native HTTP fallback. Total daemon failure can interrupt Desktop; global disable plus restart restores native operation. Revalidate model availability, wrapper adoption and protocol behavior after Codex updates.

Observed latency is recorded without clipping to the configured socket timeout; that timeout is not a strict whole-operation wall-clock deadline. No Pro savings or broad engineering-quality parity is established by the small smoke/coding tests.

## Related work

[0xNatoshi/jev-codex-router](https://github.com/0xNatoshi/jev-codex-router)
has a broader per-call policy and backtesting setup.
[gargpratyush/jev-router](https://github.com/gargpratyush/jev-router)
supports Claude Code as well as Codex. This implementation focuses on a direct
Codex Desktop app-server adapter, one shared local policy for Desktop and CLI,
and an explicit model allowlist. These are different tradeoffs, not a measured
claim of better completion quality or quota savings.

MIT licensed. Contributions and reproducible outcome measurements are welcome.
