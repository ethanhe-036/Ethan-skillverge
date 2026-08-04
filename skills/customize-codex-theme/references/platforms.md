# Platform operations

Read only the section for the detected operating system. Run the adapter from the skill directory and inspect `--help` before its first use.

## Shared action contract

Both adapters expose these actions except where a row is explicitly platform-specific:

| Action | Allowed effect |
| --- | --- |
| `doctor` | Read-only environment, process, endpoint, launcher, and state checks. |
| `prepare` | Validate a pack and set `selectedTheme` plus `nextLaunchTheme`; never launch or close Codex. |
| `start` | Launch the themed entry after explicit restart authorization; Windows may perform the documented exact-PID controlled restart only after a theme is confirmed. |
| `verify` | Probe renderer identity, required anchors, active theme marker, and critical controls. |
| `refresh` (Windows) | Replace the one verified recorded watcher and hot-reapply the canonical payload without restarting Codex. |
| `switch` | Select the next-launch theme; keep `loadedTheme` unchanged until verification. |
| `rollback` | Stage an explicit trusted or recorded previous theme; never restart or change `loadedTheme`. |
| `restore` | Remove verified live injection and return next launch to native Codex. |
| `status` | Print state, health, paths, and support status without mutation. |

Never mark a theme loaded until `verify` succeeds. Write state through `scripts/theme-tool.mjs`, which performs validation and atomic replacement.

Every `start` is serialized with a per-user lock or equivalent single-flight guard. Windows uses a cross-session named mutex scoped by the current user SID. macOS records PID, process start time, and script command; it reclaims a mismatched/dead or incomplete lock only after a 30-second initialization window. A duplicate launcher click or second launch must refuse; it must not create another Codex process or injector. If the configured port has an unknown or mismatched owner, report `NOT_READY` and refuse to reuse, stop, or automatically replace that listener. Do not select a different port unless the adapter atomically propagates it to state, runtime records, and launchers.

## Skill package deployment

For maintainer deployment, the validated repository package is the canonical
Skill source artifact; never choose files by timestamp or merge two copies.
The runtime authority is the single portable install at
`${CODEX_HOME:-<user-profile>/.codex}/skills/customize-codex-theme`; adapters
must reject another location and must never embed an employee username.
Validation means the contract test, preset validation, governance registry,
compatibility matrix, and platform-script syntax checks all pass.

Build the recursive SHA-256 manifest from every regular file with no exclusions: reject symlinks, normalize relative paths to forward slashes, sort paths by ordinal value, and hash file bytes. Install the whole package through a sibling staging directory: validate staging, rename the existing target to backup, atomically rename staging to the target, compare manifests, and restore the backup on any failure. Delete the backup only after the installed manifest matches. Any missing, extra, or changed file is `NOT_READY`; do not publish, run, or describe that installed copy as current.

## Windows

Call `scripts/windows-theme.ps1 -Action <doctor|prepare|start|verify|refresh|switch|rollback|restore|status>` with the required action-specific arguments. Pass `-AuthorizedRestart` to `start` only after current-turn authorization; the user clicking **Codex Themes** is itself a deliberate launch action. The managed desktop entry also passes `-STA -ShowThemeSelector`. Resolve Store/MSIX and installed Codex paths dynamically; do not hard-code a package version. Prefer the Codex-bundled Node runtime, fall back only to a validly Authenticode-signed Node.js 20+ executable on `PATH`, and verify the signature before executing version or WebSocket probes. Do not allow an environment variable to redirect the validator runtime, download software, or alter `PATH` silently.

The Windows-only `migrate` action retires the known legacy **Codex Dream Skin** shortcut after validating that `one-piece-paper-adventure` is in the current catalog and the managed **Codex Themes** and **Codex Original** launchers are valid. It moves the exact signed legacy shortcut into `%LOCALAPPDATA%/CodexThemeStudio/legacy-launchers`; an unknown same-name shortcut is refused and left untouched. It never launches, closes, injects into, or restarts Codex.

Capture Node JSON stdout directly as UTF-8, independent of the inherited Windows console code page. Do not route native JSON through `Out-String`: an Explorer-launched Simplified Chinese console may decode UTF-8 metadata as CP936 and corrupt a theme summary before `ConvertFrom-Json` sees it.

For a themed launch:

1. Let the user cancel the selector without closing Codex or mutating state. Once a validated theme is confirmed, capture every exact Store Codex PID, executable path, and start time.
2. Request graceful close, wait briefly, then re-verify each remaining captured PID immediately before force-stopping it. Never terminate a PID that was not captured, whose executable path changed, or whose start time changed.
3. Wait for every captured PID and the configured loopback endpoint to disappear. If a new or unknown process/listener appears, refuse to launch.
4. Launch Codex with remote debugging restricted to `127.0.0.1` and a per-user data directory only when the adapter requires isolation; then verify the port owner and renderer identity before injection.
5. Record the injector PID, executable, command line, start time, port, and theme hash.

Create shortcuts only on request:

- **Codex Themes** invokes the adapter's safe start action and opens the Windows visual selector. Acquire the adapter's per-user theme-state/start mutex before the dialog and retain it through selection and launch so only one selector/start workflow exists. Use the same mutex for Windows `prepare`, `switch`, `rollback`, and `restore`; a concurrent state mutation must be refused rather than replacing the confirmed choice. Populate the selector only from the validated manifest catalog, show `nextLaunchTheme` and `loadedTheme` as distinct states, revalidate the confirmed ID, perform the exact-PID controlled restart, select it, and then continue start. Cancel, Escape, or close is a no-op: state stays unchanged and Codex is not launched or closed.
- **Codex Original** invokes the stable AppsFolder application ID (`explorer.exe shell:AppsFolder/<AUMID>`) without debugging flags or injection.

Copy the current signed-package icon to a stable per-user file such as `%LOCALAPPDATA%/CodexThemeStudio/codex.ico`, then point both theme-owned shortcuts at that copy. Never store a versioned `WindowsApps` executable in a shortcut target or `IconLocation`; Store updates remove old package directories. The themed shortcut must invoke the adapter, which resolves the current package dynamically on every launch.

Use unique names. Before editing an existing `.lnk`, verify its path, owner, target, arguments, working directory, and description prove it is theme-owned; otherwise refuse. A known legacy **Codex Themes** entry may be transactionally upgraded to the selector signature only when every expected legacy field matches; back it up, replace through a temporary shortcut, read back the new fields, and restore the backup on any failure. Keep that backup until **Codex Original** creation/validation or byte-preservation checks also pass, then commit by deleting it. If rollback cannot prove the installed file is still the adapter's exact output, refuse deletion and preserve the legacy backup for manual recovery. Unknown, partial, or user-managed collisions must be refused. Treat **Codex Original** as an immutable rollback boundary during this migration: preflight its exact managed signature and confirm its bytes remain unchanged. After saving, read fields back and confirm only the intended values changed. Do not overwrite `Codex.lnk`, taskbar pins, Start-menu entries, Public Desktop entries, or user-managed shortcuts. The official/taskbar entry remains native and does not inherit the theme.

For a current-turn authorized Windows restart, prefer graceful close and condition-wait. An adapter may force-stop only the exact Store Codex PIDs it captured before closing, after re-verifying each executable path and process identity immediately before termination; then it must wait for those PIDs and their listener to disappear before launching. Never use `Stop-Process -Name ChatGPT`, `Stop-Process -Name Codex`, or a newly discovered PID. If the adapter does not implement this exact-PID guard, stop and request a manual exit instead of improvising.

## macOS

> **Support status:** Treat this adapter as Beta until doctor → prepare → authorized start → verify → restore passes on that actual Mac. The current release has not been live-tested on Intel macOS; fail closed rather than claiming support.

Call `scripts/macos-theme.sh <doctor|prepare|start|verify|switch|rollback|restore|status>` with the required action-specific arguments. Pass `--authorized-restart` to `start` only after current-turn authorization; the user clicking **Codex Themes** is itself a deliberate launch action. Discover Codex by bundle identifier and validate the app signature before trusting its executable or bundled runtime. The current macOS adapter does not expose Windows' verified hot-refresh action.

For a themed launch:

1. Refuse to proceed if Codex is running without a verified theme endpoint. Ask the user to finish work and quit normally.
2. Launch a new Codex instance with remote debugging restricted to `127.0.0.1` only after current-turn authorization.
3. Verify the endpoint belongs to the signed Codex process and that required renderer anchors exist.
4. Record the injector identity and theme hash before leaving it active.

Create desktop entries only on request:

- **Codex Themes** launches the selected next-launch theme.
- **Codex Original** uses `/usr/bin/open` on the signed Codex application with no debugging or injection arguments.

Prefer simple `.command` launchers unless the user explicitly asks for Dock-ready `.app` wrappers. Do not replace a user's existing launcher. If graceful quit fails, stop and ask the user to quit manually; never use `killall Codex` or `killall ChatGPT`.

Use the existing per-user start lock for macOS `prepare`, `switch`, `rollback`,
`start`, and `restore`, not only launch. An active lock whose PID, process start
time, script path, and mutating action still match must never be reclaimed as
stale; refuse the competing operation.

## Endpoint and process validation

Accept an endpoint only when all of these hold:

- it is loopback-only at `127.0.0.1`;
- the listening process resolves to the discovered Codex installation;
- the page URL and DOM probe identify a Codex renderer rather than an avatar, pet, browser, or unrelated Electron page;
- required sidebar/main/composer anchors are present;
- the selected pack validates and its hash matches the staged state.

Any failed condition is `UNSUPPORTED` or `NOT_READY`, not permission to broaden selectors.

For `restore`, if a Codex process is still running but its loopback endpoint cannot be verified, fail closed before stopping the recorded watcher or clearing `loadedTheme`, `nextLaunchTheme`, and runtime identity. Remove and verify live injection first; clear state only after successful removal, or when no Codex process remains.
