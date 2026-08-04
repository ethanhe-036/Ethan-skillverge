import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  FIXTURE_ALLOWED_ATTRIBUTES,
  FIXTURE_PRIVACY,
  FIXTURE_SCHEMA_VERSION,
  FIXTURE_STABLE_CLASS_SOURCE,
  loadFixtureContracts,
  validateFixtureObject,
} from "./fixture-tool.mjs";
import { loadPayload } from "./injector.mjs";

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "[::1]"]);
const here = path.dirname(fileURLToPath(import.meta.url));
const skillRoot = path.resolve(here, "..");

function parseArgs(argv) {
  const options = { port: 9341, output: null, state: null, viewportId: null };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--port") options.port = Number(argv[++index]);
    else if (argument === "--output") options.output = path.resolve(argv[++index]);
    else if (argument === "--state") options.state = String(argv[++index]);
    else if (argument === "--viewport") options.viewportId = String(argv[++index]);
    else throw new Error(`Unknown argument: ${argument}`);
  }
  if (!Number.isInteger(options.port) || options.port < 1024 || options.port > 65535) {
    throw new Error("port must be an integer from 1024 to 65535");
  }
  if (!options.output) throw new Error("--output is required");
  if (!options.state || !/^[a-z0-9][a-z0-9-]{0,63}$/.test(options.state)) {
    throw new Error("--state must be a stable lowercase id");
  }
  if (!options.viewportId || !/^[a-z0-9][a-z0-9-]{0,63}$/.test(options.viewportId)) {
    throw new Error("--viewport must be a stable lowercase id");
  }
  return options;
}

function verifiedWebSocketUrl(target, port) {
  const url = new URL(target.webSocketDebuggerUrl);
  if (url.protocol !== "ws:" || !LOOPBACK_HOSTS.has(url.hostname) || Number(url.port) !== port) {
    throw new Error("Rejected non-loopback CDP target");
  }
  return url.href;
}

class Session {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
  }

  async open() {
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("CDP connection timed out")), 5000);
      this.socket.addEventListener("open", () => {
        clearTimeout(timeout);
        resolve();
      }, { once: true });
      this.socket.addEventListener("error", () => {
        clearTimeout(timeout);
        reject(new Error("CDP connection failed"));
      }, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      clearTimeout(waiter.timeout);
      if (message.error) waiter.reject(new Error(message.error.message));
      else waiter.resolve(message.result);
    });
    await this.send("Runtime.enable");
    return this;
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} timed out`));
      }, 10000);
      this.pending.set(id, { resolve, reject, timeout });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails) throw new Error("Fixture evaluation failed");
    return result.result?.value;
  }

  close() {
    try { this.socket.close(); } catch {}
  }
}

const FIXTURE_EXPRESSION = `(() => {
  const stableClass = new RegExp(${JSON.stringify(FIXTURE_STABLE_CLASS_SOURCE)});
  const allowedAttributes = new Set(${JSON.stringify(FIXTURE_ALLOWED_ATTRIBUTES)});
  const nodes = [];
  const queue = [{ element: document.documentElement, parent: null, depth: 0 }];
  while (queue.length && nodes.length < 2500) {
    const current = queue.shift();
    if (!current?.element || current.depth > 14) continue;
    const element = current.element;
    const attributes = {};
    for (const attribute of element.attributes || []) {
      if (!allowedAttributes.has(attribute.name)) continue;
      const value = String(attribute.value || '');
      attributes[attribute.name] = value.slice(0, 120);
    }
    const classes = [...element.classList].filter((name) => stableClass.test(name)).slice(0, 12);
    const index = nodes.length;
    nodes.push({
      parent: current.parent,
      depth: current.depth,
      tag: element.tagName.toLowerCase(),
      attributes,
      classes,
    });
    for (const child of element.children || []) {
      queue.push({ element: child, parent: index, depth: current.depth + 1 });
    }
  }
  const root = document.documentElement;
  const themeState = window.__CODEX_THEME_STATE__;
  return {
    schemaVersion: ${FIXTURE_SCHEMA_VERSION},
    privacy: ${JSON.stringify(FIXTURE_PRIVACY)},
    scope: root.getAttribute('data-ct-scope') || (
      document.querySelector('[data-settings-panel-slug]') ? 'settings' :
      document.querySelector('[data-message-author-role]') ? 'thread' : 'home'
    ),
    contractVersion: root.getAttribute('data-ct-contract-version'),
    themeId: root.getAttribute('data-codex-theme') || undefined,
    engineVersion: document.getElementById('codex-theme-style')?.dataset.codexThemeVersion || undefined,
    payloadRevision: themeState?.payloadRevision,
    styleRevision: themeState?.styleRevision,
    viewport: {
      width: root.clientWidth,
      height: root.clientHeight,
      devicePixelRatio: window.devicePixelRatio,
      visualScale: window.visualViewport?.scale || 1,
    },
    nodeCount: nodes.length,
    truncated: queue.length > 0,
    nodes,
  };
})()`;

async function main() {
  if (typeof globalThis.WebSocket !== "function") {
    throw new Error("Node.js 20+ with built-in WebSocket is required");
  }
  const options = parseArgs(process.argv.slice(2));
  const contracts = await loadFixtureContracts(skillRoot);
  if (!contracts.matrix.states.some((state) => state.id === options.state)) {
    throw new Error(`Unknown surface-matrix state: ${options.state}`);
  }
  if (!contracts.matrix.viewports.some((viewport) => viewport.id === options.viewportId)) {
    throw new Error(`Unknown surface-matrix viewport: ${options.viewportId}`);
  }
  const response = await fetch(`http://127.0.0.1:${options.port}/json/list`);
  if (!response.ok) throw new Error(`CDP target listing failed with HTTP ${response.status}`);
  const targets = (await response.json()).filter((target) => target.type === "page" && target.webSocketDebuggerUrl);
  let fixture = null;
  for (const target of targets) {
    const session = await new Session(verifiedWebSocketUrl(target, options.port)).open();
    try {
      const probe = await session.evaluate(`(() => {
        const root = document.documentElement;
        const style = document.getElementById('codex-theme-style');
        const state = window.__CODEX_THEME_STATE__;
        const workbench = document.querySelector('[data-ct-part~="workbench"]');
        const sidebar = document.querySelector('[data-ct-part~="sidebar"]');
        const main = document.querySelector('[data-ct-part~="main"]');
        const themed = root.classList.contains('codex-theme-active') && Boolean(style && state);
        return {
          codex: Boolean(themed && workbench && sidebar && main),
          themed,
          themeId: root.getAttribute('data-codex-theme'),
        };
      })()`);
      if (!probe?.codex || !probe.themed || !probe.themeId) continue;
      if (options.state === "browser") {
        const browserVisible = await session.evaluate(`(() => {
          const host = document.querySelector(
            '[data-browser-sidebar-webview][data-app-shell-focus-area="right-panel"]'
          );
          const surface = host?.querySelector('webview');
          if (!surface) return false;
          const computed = getComputedStyle(surface);
          const rect = surface.getBoundingClientRect();
          return computed.display !== 'none' && computed.visibility !== 'hidden' &&
            Number(computed.opacity) > 0 && rect.width > 0 && rect.height > 0;
        })()`);
        if (!browserVisible) {
          throw new Error("Requested Browser state is not visibly active");
        }
      }
      if (options.state === "settings") {
        const settingsVisible = await session.evaluate(`(() => {
          const surface = document.querySelector('[data-ct-part~="settings"]');
          if (!surface || surface.hasAttribute('data-settings-panel-slug') ||
              document.documentElement.getAttribute('data-ct-scope') !== 'settings') {
            return false;
          }
          const computed = getComputedStyle(surface);
          const rect = surface.getBoundingClientRect();
          return computed.display !== 'none' && computed.visibility !== 'hidden' &&
            Number(computed.opacity) > 0 &&
            rect.width >= innerWidth * 0.35 && rect.height >= innerHeight * 0.5;
        })()`);
        if (!settingsVisible) {
          throw new Error("Requested settings state does not own the large settings content root");
        }
      }
      fixture = await session.evaluate(FIXTURE_EXPRESSION);
      fixture.state = options.state;
      fixture.viewportId = options.viewportId;
      fixture.verifiedCodexVersion = contracts.selectors.verifiedAgainst?.codexVersion;
      fixture.themed = Boolean(probe.themed);
      break;
    } finally {
      session.close();
    }
  }
  if (!fixture) throw new Error("No exact themed Codex main renderer was found");
  const themeDirectory = contracts.themeDirectories.get(fixture.themeId);
  if (!themeDirectory) throw new Error(`Live theme ${fixture.themeId} is not in the preset catalog`);
  const expected = await loadPayload(themeDirectory);
  if (fixture.payloadRevision !== expected.payloadRevision ||
      fixture.styleRevision !== expected.styleRevision) {
    throw new Error("Live renderer revisions do not match the canonical theme payload");
  }
  const validation = validateFixtureObject(fixture, contracts, path.basename(options.output));
  await fs.mkdir(path.dirname(options.output), { recursive: true });
  const temporary = `${options.output}.${process.pid}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(fixture, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  await fs.rename(temporary, options.output);
  process.stdout.write(`${JSON.stringify({
    pass: true,
    output: path.basename(options.output),
    state: fixture.state,
    viewportId: fixture.viewportId,
    scope: fixture.scope,
    nodeCount: fixture.nodeCount,
    privacy: fixture.privacy,
    eligibleForCoverage: validation.eligibleForCoverage,
    reasons: validation.reasons,
  }, null, 2)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${JSON.stringify({ pass: false, error: error.message })}\n`);
  process.exitCode = 1;
});
