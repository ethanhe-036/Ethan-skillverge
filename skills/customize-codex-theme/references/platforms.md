# Platform operations

Read only the section for the detected operating system. Run the adapter from the skill directory and inspect `--help` before its first use.

## Shared action contract

Both adapters expose these actions:

| Action | Allowed effect |
| --- | --- |
| `doctor` | Read-only environment, process, endpoint, launcher, and state checks. |
| `prepare` | Validate a pack and set `selectedTheme` plus `nextLaunchTheme`; never launch or close Codex. |
| `start` | Launch the themed entry after explicit restart authorization; never close an existing Codex implicitly. |
| `verify` | Probe renderer identity, required anchors, active theme marker, and critical controls. |
| `switch` | Select the next-launch theme; keep `loadedTheme` unchanged until verification. |
| `restore` | Remove verified live injection and return next launch to native Codex. |
| `status` | Print state, health, paths, and support status without mutation. |

Never mark a theme loaded until `verify` succeeds. Write state through `scripts/theme-tool.mjs`, which performs validation and atomic replacement.

## Windows

Call `scripts/windows-theme.ps1 -Action <doctor|prepare|start|verify|switch|restore|status>` with the required action-specific arguments. Pass `-AuthorizedRestart` to `start` only after current-turn authorization; the user clicking **Codex Themes** is itself a deliberate launch action. Resolve Store/MSIX and installed Codex paths dynamically; do not hard-code a package version. Discover a usable Node runtime without downloading software or altering `PATH` silently.

For a themed launch:

1. Refuse to proceed if Codex is running without a verified theme endpoint. Ask the user to finish work and exit normally.
2. Launch Codex with remote debugging restricted to `127.0.0.1` and a per-user data directory only when the adapter requires isolation.
3. Verify the port owner and renderer identity before injection.
4. Record the injector PID, executable, command line, start time, port, and theme hash.

Create shortcuts only on request:

- **Codex Themes** invokes the adapter's safe start action and uses `nextLaunchTheme`.
- **Codex Original** invokes the discovered official Codex entry without debugging flags or injection.

Use unique names. Do not overwrite `Codex.lnk`, taskbar pins, Start-menu entries, or user-managed shortcuts. If Windows blocks graceful closure, stop and request a manual exit; do not use `Stop-Process -Name ChatGPT` or `Stop-Process -Name Codex`.

## macOS

> **Support status:** Treat this adapter as Beta until doctor → prepare → authorized start → verify → restore passes on that actual Mac. The current release has not been live-tested on Intel macOS; fail closed rather than claiming support.

Call `scripts/macos-theme.sh <doctor|prepare|start|verify|switch|restore|status>` with the required action-specific arguments. Pass `--authorized-restart` to `start` only after current-turn authorization; the user clicking **Codex Themes** is itself a deliberate launch action. Discover Codex by bundle identifier and validate the app signature before trusting its executable or bundled runtime.

For a themed launch:

1. Refuse to proceed if Codex is running without a verified theme endpoint. Ask the user to finish work and quit normally.
2. Launch a new Codex instance with remote debugging restricted to `127.0.0.1` only after current-turn authorization.
3. Verify the endpoint belongs to the signed Codex process and that required renderer anchors exist.
4. Record the injector identity and theme hash before leaving it active.

Create desktop entries only on request:

- **Codex Themes** launches the selected next-launch theme.
- **Codex Original** uses `/usr/bin/open` on the signed Codex application with no debugging or injection arguments.

Prefer simple `.command` launchers unless the user explicitly asks for Dock-ready `.app` wrappers. Do not replace a user's existing launcher. If graceful quit fails, stop and ask the user to quit manually; never use `killall Codex` or `killall ChatGPT`.

## Endpoint and process validation

Accept an endpoint only when all of these hold:

- it is loopback-only at `127.0.0.1`;
- the listening process resolves to the discovered Codex installation;
- the page URL and DOM probe identify a Codex renderer rather than an avatar, pet, browser, or unrelated Electron page;
- required sidebar/main/composer anchors are present;
- the selected pack validates and its hash matches the staged state.

Any failed condition is `UNSUPPORTED` or `NOT_READY`, not permission to broaden selectors.
