# Effortlane compatibility

The project, public management command, CLI flags, and picker display names use **Effortlane**. Jev is the external decision service; native Codex executes coding requests with existing ChatGPT authentication. This does not use OpenRouter's hosted Jev Router to execute coding models.

Public commands:

```sh
effortlane status
effortlane doctor
codex --effortlane-auto
codex --effortlane-shadow
codex --effortlane-off
```

`--model effortlane-auto` and `--model effortlane-shadow` are accepted by the CLI wrapper. The native picker displays Effortlane Auto and Effortlane Shadow.

Existing `jev-codex`, `--jev-auto`, `--jev-shadow`, and `--jev-off` remain compatible. Internal model IDs `jev-auto` and `jev-shadow` are preserved to resume existing threads. Installed files, credential locations, and LaunchAgent labels keep their legacy names (`~/.local/share/jev-codex-router`, `~/.config/jev-codex-router`, `com.local.jev-codex-router`). This avoids disruptive state or credential migration. No logout or session reset is required. These identifiers are compatibility details, not the public brand.

New installations create both public and legacy management links. Rollback removes only links owned by the installer and preserves foreign replacements.
