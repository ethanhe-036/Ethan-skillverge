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

Every `start` is serialized with a per-user lock or equivalent single-flight guard. Windows uses a cross-session named mutex scoped by the current user SID. macOS records PID, process start time, and script command; it reclaims a mismatched/dead or incomplete lock only after a 30-second initialization window. A duplicate launcher click or second launch must refuse; it must not create another Codex process or injector. If the configured port has an unknown or mismatched owner, report `NOT_READY` and refuse to reuse, stop, or automatically replace that listener. Do not select a different port unless the adapter atomically propagates it to state, runtime records, and launchers.

## Skill package deployment

For maintainer deployment, the validated repository package is the canonical Skill source; never choose files by timestamp or merge two copies. Validation means the contract test, preset validation, and platform-script syntax checks all pass.

Build the recursive SHA-256 manifest from every regular file with no exclusions: reject symlinks, normalize relative paths to forward slashes, sort paths by ordinal value, and hash file bytes. Install the whole package through a sibling staging directory: validate staging, rename the existing target to backup, atomically rename staging to the target, compare manifests, and restore the backup on any failure. Delete the backup only after the installed manifest matches. Any missing, extra, or changed file is `NOT_READY`; do not publish, run, or describe that installed copy as current.

## Windows

Call `scripts/windows-theme.ps1 -Action <doctor|prepare|start|verify|switch|restore|status>` with the required action-specific arguments. Pass `-AuthorizedRestart` to `start` only after current-turn authorization; the user clicking **Codex Themes** is itself a deliberate launch action. Resolve Store/MSIX and installed Codex paths dynamically; do not hard-code a package version. Discover a usable Node runtime without downloading software or altering `PATH` silently.

For a themed launch:

1. Refuse to proceed if Codex is running without a verified theme endpoint. Ask the user to finish work and exit normally.
2. Launch Codex with remote debugging restricted to `127.0.0.1` and a per-user data directory only when the adapter requires isolation.
3. Verify the port owner and renderer identity before injection.
4. Record the injector PID, executable, command line, start time, port, and theme hash.

Create shortcuts only on request:

- **Codex Themes** invokes the adapter's safe start action and uses `nextLaunchTheme`.
- **Codex Original** invokes the stable AppsFolder application ID (`explorer.exe shell:AppsFolder/<AUMID>`) without debugging flags or injection.

Copy the current signed-package icon to a stable per-user file such as `%LOCALAPPDATA%/CodexThemeStudio/codex.ico`, then point both theme-owned shortcuts at that copy. Never store a versioned `WindowsApps` executable in a shortcut target or `IconLocation`; Store updates remove old package directories. The themed shortcut must invoke the adapter, which resolves the current package dynamically on every launch.

Use unique names. Before editing an existing `.lnk`, verify its path, owner, target, arguments, working directory, and description prove it is theme-owned; otherwise refuse. After saving, read those fields back and confirm only the intended values changed. Do not overwrite `Codex.lnk`, taskbar pins, Start-menu entries, Public Desktop entries, or user-managed shortcuts. The official/taskbar entry remains native and does not inherit the theme.

For a current-turn authorized Windows restart, prefer graceful close and condition-wait. An adapter may force-stop only the exact Store Codex PIDs it captured before closing, after re-verifying each executable path and process identity immediately before termination; then it must wait for those PIDs and their listener to disappear before launching. Never use `Stop-Process -Name ChatGPT`, `Stop-Process -Name Codex`, or a newly discovered PID. If the adapter does not implement this exact-PID guard, stop and request a manual exit instead of improvising.

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
