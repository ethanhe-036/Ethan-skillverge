# Launch transaction

Use this reference whenever Windows **Codex Themes** starts, switches, repairs, or recovers a theme. It prevents a selection dialog, a process restart, and a renderer injection from drifting into unrelated partial states.

## Commit order

1. Run `doctor`, validate the manifest catalog, and acquire the per-user start mutex.
2. Open the selector. `Cancel`, Escape, or window close must not close Codex, write state, start a watcher, or change `nextLaunchTheme`.
3. After the selected ID is validated but **before `select`**, capture the exact official Codex PIDs, executable path, and start time. Request graceful close, wait, and only then force-stop a remaining captured PID after rechecking its executable path and start time. Never terminate a newly discovered, recycled, or unverified PID.
4. Wait for every captured PID and the configured loopback listener to disappear. An unknown listener or a new Codex process is a fail-closed result, not a reason to reuse the port.
5. Write `selectedTheme` and `nextLaunchTheme`, launch the verified renderer, and wait for the exact main-renderer anchors—not merely the first `app://` endpoint. A cold start may expose an avatar, webview, or incomplete shell before the task renderer is ready; keep the readiness wait bounded, then inject and run adapter `verify`.
6. Start exactly one recorded watcher. Require one `payloadHash` across offline `check`, one-shot injection, renderer `verify`, watcher runtime, and `loadedTheme`; the full payload hash includes artwork bytes. Separately compare the expected CSS hash with the live injected style hash. Mark `loadedTheme` only after both checks succeed.

## State truthfulness

- `nextLaunchTheme` is a request for the next verified themed launch. It is not proof of a visible switch.
- `loadedTheme` is the most recently verified injection, not proof that Codex is currently open. Label it as historical when the CDP endpoint or watcher is absent.
- The disk pack, renderer style, runtime record, and watcher are four separate artifacts. A mismatch is `PARTIAL`; do not overwrite the record, start a second watcher, or claim success.
- If the recorded watcher has stopped, replace it without restarting Codex only after the runtime record still identifies the canonical pack and an exact live renderer verification proves the current disk `payloadHash`. A native, missing, stale, or mismatched renderer must fail closed.
- A failed controlled restart before `select` must leave `selectedTheme`, `nextLaunchTheme`, and `loadedTheme` unchanged. A failure after `select` must report the exact persisted fields and keep the app native unless injection verifies.
- Native helper failures must emit privacy-safe structured JSON and the adapter must preserve its concrete reason. Do not collapse target-readiness, hash, contrast, composition, or transport failures into a generic `injection failed` message.

## Readability gate

Token contrast validation is necessary but insufficient. When a dark theme places a dark surface over native light-mode controls, inspect computed foreground, background, opacity-bearing ancestors, pseudo-elements, and explicit component styles. Add an opaque, stable-root fallback for the affected component class; never use a blanket `* { color: ... }` rule. Validate the home action cards, empty-state text, composer, menus, permission controls, code, diff, and terminal before reporting `PASS`.

## Evidence to retain

Record Codex version, target URL/ID, selected and next themes, `loadedTheme`, disk hash, watcher PID/start time/command, port owner, and privacy-safe screenshots. For a CP936 Explorer launch, capture Node JSON as UTF-8 before parsing; localized metadata must survive unchanged.
