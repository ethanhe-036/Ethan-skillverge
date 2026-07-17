#!/bin/bash

set -euo pipefail

ACTION="${1:-status}"
[ "$#" -gt 0 ] && shift
THEME=""
PORT=9341
CREATE_LAUNCHERS="false"
AUTHORIZED_RESTART="false"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --theme) THEME="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --create-launchers) CREATE_LAUNCHERS="true"; shift ;;
    --authorized-restart) AUTHORIZED_RESTART="true"; shift ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done
case "$ACTION" in doctor|prepare|start|verify|switch|restore|status) ;; *) printf 'Unknown action: %s\n' "$ACTION" >&2; exit 2 ;; esac
case "$PORT" in ''|*[!0-9]*) printf 'Invalid port: %s\n' "$PORT" >&2; exit 2 ;; esac
[ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || { printf 'Port must be between 1024 and 65535.\n' >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
CATALOG="$SKILL_ROOT/assets/presets"
THEME_TOOL="$SCRIPT_DIR/theme-tool.mjs"
INJECTOR="$SCRIPT_DIR/injector.mjs"
STATE_ROOT="$HOME/Library/Application Support/CodexThemeStudio"
STATE_PATH="$STATE_ROOT/theme-state.json"
RUNTIME_PATH="$STATE_ROOT/runtime.json"
INJECTOR_LOG="$STATE_ROOT/injector.log"
INJECTOR_ERROR_LOG="$STATE_ROOT/injector-error.log"
EXPECTED_TEAM_ID="2DC432GLL2"

fail() { printf 'Codex Theme Studio: %s\n' "$*" >&2; exit 1; }

discover_codex() {
  local candidate identifier executable
  CODEX_BUNDLE=""
  for candidate in \
    "${CODEX_APP_BUNDLE:-}" \
    "/Applications/ChatGPT.app" \
    "/Applications/Codex.app" \
    "$HOME/Applications/ChatGPT.app" \
    "$HOME/Applications/Codex.app"
  do
    [ -n "$candidate" ] || continue
    [ -f "$candidate/Contents/Info.plist" ] || continue
    identifier="$(/usr/bin/plutil -extract CFBundleIdentifier raw -o - "$candidate/Contents/Info.plist" 2>/dev/null || true)"
    if [ "$identifier" = "com.openai.codex" ]; then CODEX_BUNDLE="$candidate"; break; fi
  done
  [ -n "$CODEX_BUNDLE" ] || fail 'Could not find the official Codex app (com.openai.codex).'

  /usr/bin/codesign --verify --deep --strict "$CODEX_BUNDLE" >/dev/null 2>&1 ||
    fail 'The Codex app signature is invalid; reinstall the official app.'
  CODEX_TEAM_ID="$(/usr/bin/codesign -dv --verbose=4 "$CODEX_BUNDLE" 2>&1 | /usr/bin/awk -F= '/^TeamIdentifier=/{print $2; exit}')"
  [ "$CODEX_TEAM_ID" = "$EXPECTED_TEAM_ID" ] || fail "Unexpected Codex signing team: ${CODEX_TEAM_ID:-missing}."

  executable="$(/usr/bin/plutil -extract CFBundleExecutable raw -o - "$CODEX_BUNDLE/Contents/Info.plist")"
  CODEX_EXE="$CODEX_BUNDLE/Contents/MacOS/$executable"
  CODEX_VERSION="$(/usr/bin/plutil -extract CFBundleShortVersionString raw -o - "$CODEX_BUNDLE/Contents/Info.plist")"
  [ -x "$CODEX_EXE" ] || fail "Codex executable is missing: $CODEX_EXE"
}

resolve_node() {
  local major node_team
  NODE="$CODEX_BUNDLE/Contents/Resources/cua_node/bin/node"
  [ -x "$NODE" ] || fail "The signed Node.js runtime bundled with Codex was not found: $NODE"
  /usr/bin/codesign --verify --strict "$NODE" >/dev/null 2>&1 || fail 'The bundled Node.js signature is invalid.'
  node_team="$(/usr/bin/codesign -dv --verbose=4 "$NODE" 2>&1 | /usr/bin/awk -F= '/^TeamIdentifier=/{print $2; exit}')"
  [ "$node_team" = "$CODEX_TEAM_ID" ] || fail 'The bundled Node.js signer does not match Codex.'
  NODE_VERSION="$($NODE --version 2>/dev/null || true)"
  major="${NODE_VERSION#v}"; major="${major%%.*}"
  case "$major" in ''|*[!0-9]*) fail "Could not parse Node.js version: $NODE_VERSION" ;; esac
  [ "$major" -ge 20 ] || fail "Node.js 20+ is required; bundled version is $NODE_VERSION."
  "$NODE" -e 'if(typeof globalThis.WebSocket!=="function")process.exit(1)' >/dev/null 2>&1 ||
    fail 'The signed bundled Node.js runtime lacks the WebSocket API required by the injector.'
}

ensure_runtime() { discover_codex; resolve_node; }
ensure_state_root() { /bin/mkdir -p "$STATE_ROOT"; /bin/chmod 700 "$STATE_ROOT"; }

json_field() {
  printf '%s' "$1" | "$NODE" -e '
    let text=""; process.stdin.setEncoding("utf8");
    process.stdin.on("data", chunk => text += chunk);
    process.stdin.on("end", () => { const value=JSON.parse(text)[process.argv[1]]; if(value!=null) process.stdout.write(String(value)); });
  ' "$2"
}

resolve_selected_theme() {
  local prefer_loaded="${1:-false}" status
  status="$($NODE "$THEME_TOOL" status --state "$STATE_PATH")"
  THEME_ID=""
  if [ "$prefer_loaded" = "true" ]; then THEME_ID="$(json_field "$status" loadedTheme)"; fi
  [ -n "$THEME_ID" ] || THEME_ID="$(json_field "$status" nextLaunchTheme)"
  [ -n "$THEME_ID" ] || THEME_ID="$(json_field "$status" selectedTheme)"
  [ -n "$THEME_ID" ] || fail 'No theme is selected. Run prepare or switch with --theme first.'
  RESOLVED_THEME="$($NODE "$THEME_TOOL" resolve --catalog "$CATALOG" --theme "$THEME_ID")"
  THEME_DIR="$(json_field "$RESOLVED_THEME" directory)"
  THEME_HASH="$(json_field "$RESOLVED_THEME" hash)"
  [ -n "$THEME_DIR" ] && [ -n "$THEME_HASH" ] || fail 'Theme resolution returned incomplete data.'
}

codex_pids() {
  local pid command
  while read -r pid command; do
    [ -n "$pid" ] || continue
    case "$command" in "$CODEX_EXE"*) printf '%s\n' "$pid" ;; esac
  done < <(/bin/ps -axo pid=,command=)
}

codex_is_running() { [ -n "$(codex_pids)" ]; }
listener_pids() { /usr/sbin/lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | /usr/bin/sort -u || true; }
loopback_listener_pids() { /usr/sbin/lsof -nP -iTCP@127.0.0.1:"$1" -sTCP:LISTEN -t 2>/dev/null | /usr/bin/sort -u || true; }

pid_is_codex_descendant() {
  local current="$1" parent command depth=0
  while [ "$current" -gt 1 ] 2>/dev/null && [ "$depth" -lt 32 ]; do
    command="$(/bin/ps -p "$current" -o command= 2>/dev/null || true)"
    case "$command" in "$CODEX_EXE"*) return 0 ;; esac
    parent="$(/bin/ps -p "$current" -o ppid= 2>/dev/null | /usr/bin/awk '{$1=$1; print}')"
    case "$parent" in ''|*[!0-9]*) return 1 ;; esac
    [ "$parent" -ne "$current" ] || return 1
    current="$parent"; depth=$((depth + 1))
  done
  return 1
}

verified_cdp() {
  local port="$1" all loop pid targets
  all="$(listener_pids "$port")"; loop="$(loopback_listener_pids "$port")"
  [ -n "$all" ] && [ "$all" = "$loop" ] || return 1
  while IFS= read -r pid; do [ -n "$pid" ] && pid_is_codex_descendant "$pid" || return 1; done <<< "$all"
  targets="$(/usr/bin/curl --noproxy '*' --silent --fail --max-time 1 "http://127.0.0.1:$port/json/list" 2>/dev/null || true)"
  printf '%s' "$targets" | /usr/bin/grep -Eq '"url"[[:space:]]*:[[:space:]]*"app://'
}

wait_for_cdp() {
  local deadline=$((SECONDS + 35))
  while [ "$SECONDS" -lt "$deadline" ]; do verified_cdp "$1" && return 0; /bin/sleep 0.4; done
  return 1
}

runtime_field() {
  "$NODE" -e '
    const fs=require("node:fs"); const value=JSON.parse(fs.readFileSync(process.argv[1],"utf8"))[process.argv[2]];
    if(value!=null) process.stdout.write(String(value));
  ' "$RUNTIME_PATH" "$1"
}

process_started_at() { /bin/ps -p "$1" -o lstart= 2>/dev/null | /usr/bin/awk '{$1=$1; print}'; }

stop_recorded_injector() {
  [ -f "$RUNTIME_PATH" ] || return 0
  local pid saved_node saved_injector saved_start actual_start command deadline
  pid="$(runtime_field injectorPid 2>/dev/null || true)"
  case "$pid" in ''|*[!0-9]*) return 0 ;; esac
  /bin/kill -0 "$pid" 2>/dev/null || return 0
  saved_node="$(runtime_field nodePath 2>/dev/null || true)"
  saved_injector="$(runtime_field injectorPath 2>/dev/null || true)"
  saved_start="$(runtime_field injectorStartedAt 2>/dev/null || true)"
  actual_start="$(process_started_at "$pid")"
  command="$(/bin/ps -p "$pid" -o command= 2>/dev/null || true)"
  [ "$saved_node" = "$NODE" ] && [ "$saved_injector" = "$INJECTOR" ] && [ "$saved_start" = "$actual_start" ] ||
    fail 'Recorded injector identity no longer matches; refusing to stop that process.'
  case "$command" in *"$saved_node"*"$saved_injector"*' watch '*) ;; *) fail 'Recorded PID is not the theme watcher; refusing to stop it.' ;; esac
  /bin/kill -TERM "$pid"
  deadline=$((SECONDS + 4))
  while /bin/kill -0 "$pid" 2>/dev/null && [ "$SECONDS" -lt "$deadline" ]; do /bin/sleep 0.2; done
  /bin/kill -0 "$pid" 2>/dev/null && fail 'The recorded theme watcher did not stop; no other process was touched.'
}

write_runtime() {
  local pid="$1" started="$2"
  "$NODE" -e '
    const fs=require("node:fs");
    const [file,port,pid,started,injector,node,themeId,themeDir,themeHash]=process.argv.slice(1);
    const state={schemaVersion:1,port:Number(port),injectorPid:Number(pid),injectorStartedAt:started,injectorPath:injector,nodePath:node,themeId,themeDir,themeHash};
    const temporary=`${file}.${process.pid}.tmp`; fs.writeFileSync(temporary,`${JSON.stringify(state,null,2)}\n`,{mode:0o600}); fs.renameSync(temporary,file);
  ' "$RUNTIME_PATH" "$PORT" "$pid" "$started" "$INJECTOR" "$NODE" "$THEME_ID" "$THEME_DIR" "$THEME_HASH"
}

create_launchers() {
  local desktop="$HOME/Desktop" themes="$HOME/Desktop/Codex Themes.command" original="$HOME/Desktop/Codex Original.command"
  /bin/mkdir -p "$desktop"
  [ ! -e "$themes" ] && [ ! -e "$original" ] || fail 'A Codex Themes or Codex Original launcher already exists; refusing to overwrite it.'
  { printf '#!/bin/bash\nexec /bin/bash '; printf '%q' "$SCRIPT_DIR/macos-theme.sh"; printf ' start --port %q --authorized-restart\n' "$PORT"; } > "$themes"
  {
    printf '#!/bin/bash\n'
    printf 'if /usr/bin/pgrep -f %q >/dev/null 2>&1; then printf "Fully exit Codex before using Codex Original.\\n" >&2; exit 1; fi\n' "$CODEX_EXE"
    printf 'exec /usr/bin/open %q\n' "$CODEX_BUNDLE"
  } > "$original"
  /bin/chmod 700 "$themes" "$original"
  printf 'Created Codex Themes.command and Codex Original.command. Fully exit Codex before using Original.\n'
}

ensure_runtime

case "$ACTION" in
  doctor)
    "$NODE" "$THEME_TOOL" validate --catalog "$CATALOG" >/dev/null
    "$NODE" -e 'console.log(JSON.stringify({pass:true,platform:"macOS",codexVersion:process.argv[1],codexPath:process.argv[2],nodeVersion:process.argv[3],stateRoot:process.argv[4]},null,2))' \
      "$CODEX_VERSION" "$CODEX_EXE" "$NODE_VERSION" "$STATE_ROOT"
    ;;

  prepare|switch)
    [ -n "$THEME" ] || fail "$ACTION requires --theme <id>."
    ensure_state_root
    "$NODE" "$THEME_TOOL" select --catalog "$CATALOG" --state "$STATE_PATH" --theme "$THEME"
    [ "$CREATE_LAUNCHERS" = "true" ] && create_launchers
    ;;

  start)
    [ "$AUTHORIZED_RESTART" = "true" ] || fail 'start requires --authorized-restart after explicit current-turn authorization, or a deliberate Codex Themes launcher click.'
    resolve_selected_theme false
    "$NODE" "$INJECTOR" check --theme-dir "$THEME_DIR"
    if codex_is_running; then
      fail 'Codex is already running. Theme changes are next-launch only; fully exit Codex manually, then run start again.'
    fi
    if ! verified_cdp "$PORT"; then
      [ -z "$(listener_pids "$PORT")" ] || fail "Port $PORT belongs to an unknown listener."
      /usr/bin/open -na "$CODEX_BUNDLE" --args --remote-debugging-address=127.0.0.1 --remote-debugging-port="$PORT"
      wait_for_cdp "$PORT" || fail "Codex did not expose a verified loopback endpoint on port $PORT."
    fi

    stop_recorded_injector
    "$NODE" "$INJECTOR" once --theme-dir "$THEME_DIR" --port "$PORT" || fail 'Theme injection failed; the native interface was kept.'
    if ! "$NODE" "$INJECTOR" verify --theme-dir "$THEME_DIR" --port "$PORT"; then
      "$NODE" "$INJECTOR" remove --theme-dir "$THEME_DIR" --port "$PORT" >/dev/null 2>&1 || true
      fail 'Theme verification failed; the native interface was restored.'
    fi

    ensure_state_root
    : > "$INJECTOR_LOG"; : > "$INJECTOR_ERROR_LOG"
    /usr/bin/nohup "$NODE" "$INJECTOR" watch --theme-dir "$THEME_DIR" --port "$PORT" >>"$INJECTOR_LOG" 2>>"$INJECTOR_ERROR_LOG" &
    watcher_pid="$!"; /bin/sleep 0.35
    /bin/kill -0 "$watcher_pid" 2>/dev/null || fail "Theme watcher exited during startup. See $INJECTOR_ERROR_LOG"
    watcher_start="$(process_started_at "$watcher_pid")"
    [ -n "$watcher_start" ] || fail 'Could not record the theme watcher identity.'
    write_runtime "$watcher_pid" "$watcher_start"
    if ! "$NODE" "$THEME_TOOL" mark-loaded --state "$STATE_PATH" --theme "$THEME_ID" --hash "$THEME_HASH"; then
      stop_recorded_injector
      fail 'Could not record the loaded theme.'
    fi
    ;;

  verify)
    verified_cdp "$PORT" || fail 'No verified Codex loopback endpoint is available.'
    resolve_selected_theme true
    "$NODE" "$INJECTOR" verify --theme-dir "$THEME_DIR" --port "$PORT"
    ;;

  restore)
    stop_recorded_injector
    if verified_cdp "$PORT"; then "$NODE" "$INJECTOR" remove --port "$PORT" || fail 'Could not remove the injected theme safely.'; fi
    "$NODE" "$THEME_TOOL" mark-restored --state "$STATE_PATH"
    /bin/rm -f "$RUNTIME_PATH"
    ;;

  status)
    "$NODE" "$THEME_TOOL" status --state "$STATE_PATH"
    ;;
esac
