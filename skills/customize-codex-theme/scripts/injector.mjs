import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const ENGINE_VERSION = "1.0.0";
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "[::1]"]);
const MAX_ART_BYTES = 16 * 1024 * 1024;

function parseArgs(argv) {
  const legacyModes = new Map([
    ["--check-payload", "check"], ["--once", "once"], ["--watch", "watch"],
    ["--verify", "verify"], ["--remove", "remove"],
  ]);
  const options = { mode: "watch", port: 9341, timeoutMs: 30000, themeDir: null, screenshot: null };
  if (argv[0] && !argv[0].startsWith("--")) options.mode = argv.shift();
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (legacyModes.has(argument)) options.mode = legacyModes.get(argument);
    else if (argument === "--port") options.port = Number(argv[++index]);
    else if (argument === "--timeout-ms") options.timeoutMs = Number(argv[++index]);
    else if (argument === "--theme-dir") options.themeDir = path.resolve(argv[++index]);
    else if (argument === "--screenshot") options.screenshot = path.resolve(argv[++index]);
    else throw new Error(`Unknown argument: ${argument}`);
  }
  if (!["check", "once", "watch", "verify", "remove"].includes(options.mode)) {
    throw new Error(`Unknown action: ${options.mode}`);
  }
  if (!Number.isInteger(options.port) || options.port < 1024 || options.port > 65535) {
    throw new Error(`Invalid port: ${options.port}`);
  }
  if (!Number.isFinite(options.timeoutMs) || options.timeoutMs < 250 || options.timeoutMs > 120000) {
    throw new Error(`Invalid timeout: ${options.timeoutMs}`);
  }
  if (["check", "once", "watch"].includes(options.mode) && !options.themeDir) {
    throw new Error(`${options.mode} requires --theme-dir`);
  }
  return options;
}

function validatedDebuggerUrl(target, port) {
  const url = new URL(target.webSocketDebuggerUrl);
  if (url.protocol !== "ws:" || !LOOPBACK_HOSTS.has(url.hostname) || Number(url.port) !== port) {
    throw new Error(`Rejected non-loopback CDP WebSocket URL: ${url.href}`);
  }
  return url.href;
}

function ensureWebSocket() {
  if (typeof globalThis.WebSocket !== "function") {
    throw new Error("This injector needs a Node runtime with the built-in WebSocket API");
  }
}

class CdpSession {
  constructor(target, port) {
    this.target = target;
    this.ws = new WebSocket(validatedDebuggerUrl(target, port));
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    this.closed = false;
  }

  async open() {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("CDP WebSocket open timed out")), 5000);
      this.ws.addEventListener("open", () => { clearTimeout(timeout); resolve(); }, { once: true });
      this.ws.addEventListener("error", () => { clearTimeout(timeout); reject(new Error("CDP WebSocket open failed")); }, { once: true });
    });
    this.ws.addEventListener("message", (event) => this.onMessage(event));
    this.ws.addEventListener("close", () => {
      this.closed = true;
      for (const waiter of this.pending.values()) {
        clearTimeout(waiter.timeout);
        waiter.reject(new Error("CDP socket closed"));
      }
      this.pending.clear();
    });
    await this.send("Runtime.enable");
    await this.send("Page.enable");
    return this;
  }

  onMessage(event) {
    const message = JSON.parse(String(event.data));
    if (message.id) {
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      clearTimeout(waiter.timeout);
      this.pending.delete(message.id);
      if (message.error) waiter.reject(new Error(`${message.error.message} (${message.error.code})`));
      else waiter.resolve(message.result);
      return;
    }
    for (const listener of this.listeners.get(message.method) ?? []) listener(message.params ?? {});
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) ?? [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }

  send(method, params = {}) {
    if (this.closed) return Promise.reject(new Error("CDP session is closed"));
    return new Promise((resolve, reject) => {
      const id = this.nextId++;
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP command timed out: ${method}`));
      }, 10000);
      this.pending.set(id, { resolve, reject, timeout });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression, awaitPromise: true, returnByValue: true, userGesture: false,
    });
    if (result.exceptionDetails) {
      const detail = result.exceptionDetails.exception?.description ?? result.exceptionDetails.text;
      throw new Error(`Renderer evaluation failed: ${detail}`);
    }
    return result.result?.value;
  }

  close() {
    if (!this.closed) this.ws.close();
    this.closed = true;
  }
}

async function listAppTargets(port) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2000);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: controller.signal });
    if (!response.ok) throw new Error(`CDP endpoint returned HTTP ${response.status}`);
    const targets = await response.json();
    return targets.filter((target) => {
      if (target.type !== "page" || !target.url?.startsWith("app://") || !target.webSocketDebuggerUrl) return false;
      try { validatedDebuggerUrl(target, port); return true; } catch { return false; }
    });
  } finally {
    clearTimeout(timeout);
  }
}

async function probeSession(session) {
  return session.evaluate(`(() => {
    const shell = document.querySelector('main.main-surface');
    const sidebar = document.querySelector('aside.app-shell-left-panel');
    const composer = document.querySelector('.composer-surface-chrome');
    const main = document.querySelector('[role="main"]');
    const avatarSignals = document.querySelector('[data-pet-window], [data-avatar-window], .pet-window');
    return {
      href: location.href,
      title: document.title,
      anchors: { shell: Boolean(shell), sidebar: Boolean(sidebar), composer: Boolean(composer), main: Boolean(main) },
      codex: Boolean(shell && sidebar && (composer || main) && !avatarSignals),
    };
  })()`);
}

async function connectTarget(target, port) {
  return new CdpSession(target, port).open();
}

async function connectCodexTargets(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = new Error("No targets found");
  while (Date.now() < deadline) {
    const connected = [];
    try {
      for (const target of await listAppTargets(port)) {
        let session;
        try {
          session = await connectTarget(target, port);
          const probe = await probeSession(session);
          if (probe?.codex) connected.push({ target, session, probe });
          else session.close();
        } catch (error) {
          session?.close();
          lastError = error;
        }
      }
      if (connected.length) return connected;
      lastError = new Error("unknown DOM or missing required anchor; keeping the native interface");
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  throw new Error(`No verified Codex renderer on 127.0.0.1:${port}: ${lastError.message}`);
}

async function optionalFile(file, maximumBytes) {
  try {
    const stat = await fs.stat(file);
    if (!stat.isFile() || stat.size > maximumBytes) throw new Error(`${file} is too large or not a file`);
    return fs.readFile(file);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

async function loadArt(themeDir, config) {
  if (!config.art) return null;
  if (!config.art || typeof config.art !== "object" || typeof config.art.file !== "string") {
    throw new Error("theme.art must contain a file name");
  }
  if (path.basename(config.art.file) !== config.art.file) throw new Error("Theme art must stay inside the theme directory");
  if (!config.art.license || !config.art.provenance) throw new Error("Theme art requires license and provenance metadata");
  const extension = path.extname(config.art.file).toLowerCase();
  const mime = new Map([[".png", "image/png"], [".jpg", "image/jpeg"], [".jpeg", "image/jpeg"], [".webp", "image/webp"]]).get(extension);
  if (!mime) throw new Error(`Unsupported theme art format: ${extension || "missing"}`);
  const bytes = await optionalFile(path.join(themeDir, config.art.file), MAX_ART_BYTES);
  if (!bytes?.length) throw new Error("Theme art is missing or empty");
  return `data:${mime};base64,${bytes.toString("base64")}`;
}

async function loadPayload(themeDir) {
  const resolvedTheme = await fs.realpath(themeDir);
  const [baseCss, template, configText] = await Promise.all([
    fs.readFile(path.join(root, "assets", "base.css"), "utf8"),
    fs.readFile(path.join(root, "assets", "renderer-inject.js"), "utf8"),
    fs.readFile(path.join(resolvedTheme, "theme.json"), "utf8"),
  ]);
  const config = JSON.parse(configText);
  if (!config?.id || typeof config.id !== "string" || !config.tokens || !config.capabilities) {
    throw new Error("Theme has an unsupported schema");
  }
  const themeCss = await optionalFile(path.join(resolvedTheme, "theme.css"), 256 * 1024);
  const themeCssText = themeCss?.toString("utf8") ?? "";
  if (/@import\b|https?:\/\/|url\s*\(/i.test(themeCssText)) {
    throw new Error("theme.css cannot import or fetch remote/local resources; use declared theme art instead");
  }
  const artDataUrl = await loadArt(resolvedTheme, config);
  const css = `${baseCss}\n${themeCssText}`;
  const themeConfig = { ...config, artDataUrl };
  const payloadHash = crypto.createHash("sha256").update(configText).update(css).digest("hex");
  const payload = template
    .replace("__CODEX_THEME_CSS_JSON__", JSON.stringify(css))
    .replace("__CODEX_THEME_CONFIG_JSON__", JSON.stringify(themeConfig))
    .replace("__CODEX_THEME_VERSION_JSON__", JSON.stringify(ENGINE_VERSION));
  if (/__CODEX_THEME_(?:CSS|CONFIG|VERSION)_JSON__/.test(payload)) throw new Error("Renderer payload has unresolved placeholders");
  return { payload, payloadHash, theme: config };
}

async function removeFromSession(session) {
  return session.evaluate(`(() => {
    window.__CODEX_THEME_DISABLED__ = true;
    const state = window.__CODEX_THEME_STATE__;
    if (state?.cleanup) return state.cleanup();
    document.documentElement?.classList.remove('codex-theme', 'codex-theme-active', 'codex-theme-has-art');
    document.documentElement?.removeAttribute('data-codex-theme');
    document.documentElement?.removeAttribute('data-codex-theme-mode');
    for (const name of [...document.documentElement.style]) {
      if (name.startsWith('--ct-')) document.documentElement.style.removeProperty(name);
    }
    document.getElementById('codex-theme-style')?.remove();
    document.getElementById('codex-theme-chrome')?.remove();
    delete window.__CODEX_THEME_STATE__;
    return true;
  })()`);
}

async function verifyRemovedSession(session) {
  return session.evaluate(`(() => !document.documentElement.classList.contains('codex-theme') &&
    !document.documentElement.classList.contains('codex-theme-active') &&
    !document.getElementById('codex-theme-style') && !window.__CODEX_THEME_STATE__)()`);
}

async function verifySession(session) {
  return session.evaluate(`(() => {
    const state = window.__CODEX_THEME_STATE__;
    const shell = document.querySelector('main.main-surface');
    const sidebar = document.querySelector('aside.app-shell-left-panel');
    const composer = document.querySelector('.composer-surface-chrome');
    const main = document.querySelector('[role="main"]');
    const style = document.getElementById('codex-theme-style');
    const overflowX = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1;
    const pass = Boolean(state?.version === ${JSON.stringify(ENGINE_VERSION)} &&
      document.documentElement.classList.contains('codex-theme-active') && style &&
      shell && sidebar && (composer || main) && !overflowX);
    return { pass, version: state?.version ?? null, themeId: state?.themeId ?? null,
      anchors: { shell: Boolean(shell), sidebar: Boolean(sidebar), composer: Boolean(composer), main: Boolean(main) },
      overflowX };
  })()`);
}

async function waitForVerification(session, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let result;
  while (Date.now() < deadline) {
    result = await verifySession(session);
    if (result.pass) return result;
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  return result;
}

async function capture(session, output) {
  await fs.mkdir(path.dirname(output), { recursive: true });
  const result = await session.send("Page.captureScreenshot", { format: "png", fromSurface: true, captureBeyondViewport: false });
  await fs.writeFile(output, Buffer.from(result.data, "base64"));
}

async function runOneShot(options) {
  ensureWebSocket();
  const connected = await connectCodexTargets(options.port, options.timeoutMs);
  const loaded = options.mode === "once" ? await loadPayload(options.themeDir) : null;
  const results = [];
  let captured = false;
  for (const { target, session, probe } of connected) {
    try {
      if (options.mode === "remove") await removeFromSession(session);
      if (options.mode === "once") await session.evaluate(loaded.payload);
      const result = options.mode === "remove" ? await verifyRemovedSession(session) : await waitForVerification(session, options.timeoutMs);
      results.push({ targetId: target.id, title: target.title, url: target.url, probe, result });
      if (options.screenshot && !captured) { await capture(session, options.screenshot); captured = true; }
    } finally {
      session.close();
    }
  }
  const pass = results.length > 0 && results.every((item) => options.mode === "remove" ? item.result === true : item.result?.pass);
  console.log(JSON.stringify({ pass, mode: options.mode, version: ENGINE_VERSION, port: options.port,
    themeId: loaded?.theme.id ?? null, payloadHash: loaded?.payloadHash ?? null, targets: results }, null, 2));
  if (!pass) process.exitCode = 2;
}

async function runWatch(options) {
  ensureWebSocket();
  const loaded = await loadPayload(options.themeDir);
  const sessions = new Map();
  let stopping = false;
  let connectedOnce = false;
  let lastVerifiedAt = Date.now();
  const stop = () => { stopping = true; };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);

  while (!stopping) {
    let targets = [];
    try { targets = await listAppTargets(options.port); }
    catch (error) {
      if (connectedOnce && Date.now() - lastVerifiedAt > 30000) break;
      console.error(`[codex-theme] ${error.message}`);
      await new Promise((resolve) => setTimeout(resolve, 900));
      continue;
    }

    const active = new Set(targets.map((target) => target.id));
    for (const [id, session] of sessions) {
      if (!active.has(id) || session.closed) { session.close(); sessions.delete(id); }
    }
    for (const target of targets) {
      if (sessions.has(target.id)) continue;
      let session;
      try {
        session = await connectTarget(target, options.port);
        const probe = await probeSession(session);
        if (!probe.codex) { session.close(); continue; }
        session.on("Page.loadEventFired", () => setTimeout(() => session.evaluate(loaded.payload).catch(() => {}), 250));
        await session.evaluate(loaded.payload);
        const verified = await waitForVerification(session, Math.min(options.timeoutMs, 5000));
        if (!verified?.pass) throw new Error("Theme payload did not verify");
        sessions.set(target.id, session);
        connectedOnce = true;
        lastVerifiedAt = Date.now();
        console.log(JSON.stringify({ event: "loaded", targetId: target.id, themeId: loaded.theme.id, payloadHash: loaded.payloadHash }));
      } catch (error) {
        session?.close();
        console.error(`[codex-theme] rejected target ${target.id}: ${error.message}`);
      }
    }
    if (sessions.size) lastVerifiedAt = Date.now();
    if (connectedOnce && sessions.size === 0 && Date.now() - lastVerifiedAt > 30000) break;
    await new Promise((resolve) => setTimeout(resolve, 900));
  }
  for (const session of sessions.values()) session.close();
}

try {
  const options = parseArgs(process.argv.slice(2));
  if (options.mode === "check") {
    const loaded = await loadPayload(options.themeDir);
    console.log(JSON.stringify({ pass: true, version: ENGINE_VERSION, themeId: loaded.theme.id,
      payloadHash: loaded.payloadHash, payloadBytes: Buffer.byteLength(loaded.payload) }, null, 2));
  } else if (options.mode === "watch") await runWatch(options);
  else await runOneShot(options);
} catch (error) {
  console.error(`[codex-theme] ${error.stack || error.message}`);
  process.exitCode = 1;
}
