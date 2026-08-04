import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import { compileThemeAuthoring } from "./composition-compiler.mjs";
import { readImageMetadata } from "./image-metadata.mjs";
import { loadSelectorContract as loadSelectorContractSource } from "./selector-contract.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const ENGINE_VERSION = "2.8.0";
const QA_CONTRACT_VERSION = 14;
const COMPATIBILITY_PROBE_CONTRACT_VERSION = 1;
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "[::1]"]);
const MAX_ART_BYTES = 16 * 1024 * 1024;
const MAX_ART_VARIANTS = 4;
const ART_VARIANT_PATTERN = /^[a-z][a-z0-9-]{0,31}$/;

function parseArgs(argv) {
  const legacyModes = new Map([
    ["--check-payload", "check"], ["--once", "once"], ["--watch", "watch"],
    ["--verify", "verify"], ["--remove", "remove"],
  ]);
  const options = {
    mode: "watch",
    port: 9341,
    timeoutMs: 30000,
    themeDir: null,
    screenshot: null,
    statusFile: null,
    platform: null,
    codexVersion: null,
  };
  if (argv[0] && !argv[0].startsWith("--")) options.mode = argv.shift();
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (legacyModes.has(argument)) options.mode = legacyModes.get(argument);
    else if (argument === "--port") options.port = Number(argv[++index]);
    else if (argument === "--timeout-ms") options.timeoutMs = Number(argv[++index]);
    else if (argument === "--theme-dir") options.themeDir = path.resolve(argv[++index]);
    else if (argument === "--screenshot") options.screenshot = path.resolve(argv[++index]);
    else if (argument === "--status-file") options.statusFile = path.resolve(argv[++index]);
    else if (argument === "--platform") options.platform = String(argv[++index]).toLowerCase();
    else if (argument === "--codex-version") options.codexVersion = String(argv[++index]);
    else throw new Error(`Unknown argument: ${argument}`);
  }
  if (!["check", "once", "watch", "verify", "remove", "compatibility-probe"].includes(options.mode)) {
    throw new Error(`Unknown action: ${options.mode}`);
  }
  if (!Number.isInteger(options.port) || options.port < 1024 || options.port > 65535) {
    throw new Error(`Invalid port: ${options.port}`);
  }
  if (!Number.isFinite(options.timeoutMs) || options.timeoutMs < 250 || options.timeoutMs > 120000) {
    throw new Error(`Invalid timeout: ${options.timeoutMs}`);
  }
  if (["check", "once", "watch", "verify"].includes(options.mode) && !options.themeDir) {
    throw new Error(`${options.mode} requires --theme-dir`);
  }
  if (options.mode === "compatibility-probe") {
    if (!new Set(["windows", "macos"]).has(options.platform)) {
      throw new Error("compatibility-probe requires --platform windows|macos");
    }
    if (!/^\d+(?:\.\d+){1,3}$/.test(options.codexVersion ?? "")) {
      throw new Error("compatibility-probe requires --codex-version VERSION");
    }
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
    const shell = document.querySelector('[data-app-shell-main-surface], main.main-surface');
    const sidebar = document.querySelector('aside.app-shell-left-panel');
    const composer = document.querySelector('.composer-surface-chrome');
    const main = document.querySelector('[role="main"]');
    const settings = document.querySelector('[data-settings-panel-slug]');
    const avatarSignals = document.querySelector('[data-pet-window], [data-avatar-window], .pet-window');
    return {
      anchors: {
        shell: Boolean(shell),
        sidebar: Boolean(sidebar),
        composer: Boolean(composer),
        main: Boolean(main),
        settings: Boolean(settings),
      },
      codex: Boolean(shell && sidebar && (composer || main || settings) && !avatarSignals),
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

async function probeCompatibilitySession(session, selectorContract) {
  return session.evaluate(`(() => {
    const contract = ${JSON.stringify(selectorContract)};
    const isVisible = (element) => {
      if (!element) return false;
      const computed = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return computed.display !== 'none' && computed.visibility !== 'hidden' &&
        Number(computed.opacity) > 0 && rect.width > 0 && rect.height > 0;
    };
    const parts = {};
    for (const [part, definition] of Object.entries(contract.parts || {})) {
      let resolved = [];
      let resolvedTier = null;
      let matchedCount = 0;
      for (const tier of ['L1', 'L2']) {
        const unique = new Set();
        for (const selector of definition.tiers?.[tier] || []) {
          try {
            for (const element of document.querySelectorAll(selector)) unique.add(element);
          } catch {}
        }
        if (unique.size) {
          matchedCount = unique.size;
          resolved = [...unique];
          resolvedTier = tier;
          break;
        }
      }
      if (definition.cardinality === 'one') resolved = resolved.slice(0, 1);
      else resolved = resolved.slice(0, Number(definition.maximum) || 16);
      const visibleCount = resolved.filter(isVisible).length;
      const required = definition.required === true;
      parts[part] = {
        tier: resolvedTier,
        matchedCount,
        resolvedCount: resolved.length,
        visibleCount,
        required,
        pass: !required || (resolved.length === 1 && visibleCount === 1),
      };
    }
    const requiredParts = Object.entries(parts)
      .filter(([, result]) => result.required)
      .map(([part]) => part);
    const requiredPass = requiredParts.length > 0 &&
      requiredParts.every((part) => parts[part].pass);
    const avatarSignals = Boolean(document.querySelector(
      '[data-pet-window], [data-avatar-window], .pet-window'
    ));
    const queryScope = (name) => {
      const selector = contract.scopes?.[name];
      if (!selector) return false;
      try { return Boolean(document.querySelector(selector)); } catch { return false; }
    };
    const scope = queryScope('settings') ? 'settings' :
      queryScope('thread') ? 'thread' : 'home';
    const root = document.documentElement;
    const fingerprint = (element) => element ? {
      tag: element.tagName.toLowerCase(),
      classes: [...element.classList].slice(0, 16),
      role: element.getAttribute('role'),
      dataAttributeNames: element.getAttributeNames()
        .filter((name) => name.startsWith('data-')).slice(0, 16),
      childElementCount: element.childElementCount,
    } : null;
    const ancestorFingerprints = (element) => {
      const values = [];
      let current = element;
      while (current && current !== document.body && values.length < 30) {
        values.push(fingerprint(current));
        current = current.parentElement;
      }
      if (current === document.body) values.push(fingerprint(current));
      return values;
    };
    const composerAnchor = document.querySelector(
      '.composer-surface-chrome, [data-testid*="composer" i], [data-testid*="prompt-input" i]'
    );
    return {
      documentReady: document.readyState !== 'loading',
      rendererCandidate: requiredPass && !avatarSignals,
      requiredPass,
      requiredParts,
      avatarSignals,
      themeMarkerPresent: Boolean(
        root.classList.contains('codex-theme') || root.hasAttribute('data-codex-theme')
      ),
      scope,
      viewport: {
        width: Math.max(0, Math.round(window.innerWidth || root.clientWidth || 0)),
        height: Math.max(0, Math.round(window.innerHeight || root.clientHeight || 0)),
        deviceScaleFactor: Number((window.devicePixelRatio || 1).toFixed(2)),
      },
      overflowX: root.scrollWidth > root.clientWidth + 1,
      preferences: {
        reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
        increasedContrast: matchMedia('(prefers-contrast: more)').matches,
        forcedColors: matchMedia('(forced-colors: active)').matches,
      },
      parts,
      diagnostics: requiredPass ? null : {
        composerAncestors: ancestorFingerprints(composerAnchor),
        bodyChildren: [...document.body.children].slice(0, 12).map(fingerprint),
      },
    };
  })()`);
}

async function runCompatibilityProbe(options) {
  ensureWebSocket();
  const selectorSource = await loadSelectorContractSource(root);
  const capturedAt = new Date().toISOString();
  const deadline = Date.now() + options.timeoutMs;
  let appTargetCount = 0;
  let failedTargetCount = 0;
  let results = [];
  while (Date.now() < deadline) {
    let targets = [];
    try { targets = await listAppTargets(options.port); } catch {}
    appTargetCount = targets.length;
    failedTargetCount = 0;
    results = [];
    for (const target of targets) {
      let session;
      try {
        session = await connectTarget(target, options.port);
        results.push(await probeCompatibilitySession(session, selectorSource.contract));
      } catch {
        failedTargetCount += 1;
      } finally {
        session?.close();
      }
    }
    if (results.some((result) => result.rendererCandidate) ||
        results.some((result) => result.documentReady)) break;
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  const qualified = results.filter((result) => result.rendererCandidate);
  const qualifiedTargetCount = qualified.length;
  const compatible = qualifiedTargetCount > 0;
  const reportedTargets = compatible
    ? qualified.map(({ diagnostics: _diagnostics, ...result }) => result)
    : results;
  return {
    schemaVersion: 1,
    contractId: "codex-theme-compatibility-evidence",
    probeContractVersion: COMPATIBILITY_PROBE_CONTRACT_VERSION,
    pass: compatible,
    compatible,
    status: compatible ? "PARTIAL" : "UNSUPPORTED",
    mutationPerformed: false,
    platform: options.platform,
    codexVersion: options.codexVersion,
    capturedAt,
    engineVersion: ENGINE_VERSION,
    port: options.port,
    privacy: {
      structureOnly: true,
      textCaptured: false,
      titlesCaptured: false,
      urlsCaptured: false,
      inputValuesCaptured: false,
      filePathsCaptured: false,
      htmlCaptured: false,
    },
    selectorContract: {
      contractId: selectorSource.contract.contractId,
      schemaVersion: selectorSource.contract.schemaVersion,
      hash: selectorSource.compatibilityHash,
      verifiedAgainst: {
        platform: options.platform,
        codexVersion: options.codexVersion,
        verifiedAt: capturedAt.slice(0, 10),
      },
    },
    appTargetCount,
    qualifiedTargetCount,
    failedTargetCount,
    targets: reportedTargets,
  };
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

async function loadArtAsset(themeDir, descriptor, label) {
  if (!descriptor || typeof descriptor !== "object" || Array.isArray(descriptor) ||
      typeof descriptor.file !== "string") {
    throw new Error(`${label} must contain a file name`);
  }
  if (path.basename(descriptor.file) !== descriptor.file) {
    throw new Error(`${label} must stay inside the theme directory`);
  }
  if (!descriptor.license || !descriptor.provenance) {
    throw new Error(`${label} requires license and provenance metadata`);
  }
  const extension = path.extname(descriptor.file).toLowerCase();
  const mime = new Map([[".png", "image/png"], [".jpg", "image/jpeg"], [".jpeg", "image/jpeg"], [".webp", "image/webp"]]).get(extension);
  if (!mime) throw new Error(`${label} uses an unsupported image format: ${extension || "missing"}`);
  const bytes = await optionalFile(path.join(themeDir, descriptor.file), MAX_ART_BYTES);
  if (!bytes?.length) throw new Error(`${label} is missing or empty`);
  const metadata = readImageMetadata(bytes, extension);
  if (!metadata) {
    throw new Error(`${label} has invalid image metadata or exceeds the 16384px / 50MP safety limit`);
  }
  return {
    dataUrl: `data:${mime};base64,${bytes.toString("base64")}`,
    digest: crypto.createHash("sha256").update(bytes).digest("hex"),
    metadata,
  };
}

async function loadArt(themeDir, config) {
  if (!config.art) return null;
  const primary = await loadArtAsset(themeDir, config.art, "theme.art");
  const variants = {};
  if (config.art.variants !== undefined) {
    if (!config.art.variants || typeof config.art.variants !== "object" || Array.isArray(config.art.variants)) {
      throw new Error("theme.art.variants must be an object");
    }
    const entries = Object.entries(config.art.variants);
    if (entries.length > MAX_ART_VARIANTS) {
      throw new Error(`theme.art.variants supports at most ${MAX_ART_VARIANTS} assets`);
    }
    for (const [name, descriptor] of entries) {
      if (!ART_VARIANT_PATTERN.test(name)) {
        throw new Error(`Invalid theme art variant name: ${name}`);
      }
      variants[name] = await loadArtAsset(themeDir, descriptor, `theme.art.variants.${name}`);
    }
  }
  return { primary, variants };
}

export function assertPayloadIntegrity(payload) {
  if (/__CODEX_THEME_[A-Z0-9_]+_JSON__/.test(payload)) {
    throw new Error("Renderer payload has unresolved placeholders");
  }
  try {
    new vm.Script(payload, { filename: "codex-theme-renderer-payload.js" });
  } catch (error) {
    throw new Error(`Renderer payload is not parsable: ${error.message}`);
  }
  return true;
}

function percentage(value, fallback) {
  const numeric = Number.isFinite(Number(value)) ? Number(value) : fallback;
  return `${Math.max(0, Math.min(100, numeric))}%`;
}

function headerMaterialProjection(headerTint) {
  const tint = Number.isFinite(Number(headerTint)) ? Number(headerTint) : 72;
  const delta = tint - 72;
  return {
    topTint: 22 + delta * (5 / 3),
    midTint: 38 + delta * (5 / 3),
    bottomTint: 52 + delta * (5 / 3),
    controlTint: 14 + delta * (2 / 3),
  };
}

function headerMaterialVariableLines(composition, indentation = "  ") {
  const material = headerMaterialProjection(composition.headerTint);
  return [
    `${indentation}--ct-header-material-top-tint: ${percentage(material.topTint, 22)};`,
    `${indentation}--ct-header-material-mid-tint: ${percentage(material.midTint, 38)};`,
    `${indentation}--ct-header-material-bottom-tint: ${percentage(material.bottomTint, 52)};`,
    `${indentation}--ct-header-material-control-tint: ${percentage(material.controlTint, 14)};`,
  ];
}

function settingsMaterialProjection(viewportBand = "wide") {
  return ({ wide: 52, split: 60, narrow: 68 })[viewportBand] ?? 52;
}

function projectedComposition(composition, behavior) {
  const projection = {
    preserve: { focus: 1, scrim: 0, tint: 0 },
    rebalance: { focus: 0.7, scrim: 4, tint: 4 },
    reduce: { focus: 0.45, scrim: 10, tint: 12 },
    hide: { focus: 0, scrim: 61, tint: 24 },
  }[behavior] ?? { focus: 1, scrim: 0, tint: 0 };
  const towardCenter = (value, fallback) => {
    const numeric = Number.isFinite(Number(value)) ? Number(value) : fallback;
    return 50 + (numeric - 50) * projection.focus;
  };
  const add = (value, fallback, amount) => {
    const numeric = Number.isFinite(Number(value)) ? Number(value) : fallback;
    return Math.max(0, Math.min(96, numeric + amount));
  };
  return {
    ...composition,
    focusX: towardCenter(composition.focusX, 50),
    focusY: towardCenter(composition.focusY, 50),
    workbenchScrim: add(composition.workbenchScrim, 35, projection.scrim),
    sidebarTint: add(composition.sidebarTint, 72, projection.tint),
    mainTint: add(composition.mainTint, 78, projection.tint),
    headerTint: add(composition.headerTint, 76, projection.tint),
    rightPanelTint: add(composition.rightPanelTint, 72, projection.tint),
  };
}

function compositionVariableLines(
  composition,
  safeArea,
  indentation = "  ",
  settingsViewportBand = "wide",
) {
  const lines = [
    `${indentation}--ct-art-focus-x: ${percentage(composition.focusX, 50)};`,
    `${indentation}--ct-art-focus-y: ${percentage(composition.focusY, 50)};`,
    `${indentation}--ct-workbench-scrim: ${percentage(composition.workbenchScrim, 35)};`,
    `${indentation}--ct-sidebar-tint: ${percentage(composition.sidebarTint, 72)};`,
    `${indentation}--ct-main-tint: ${percentage(composition.mainTint, 78)};`,
    `${indentation}--ct-header-tint: ${percentage(composition.headerTint, 76)};`,
    ...headerMaterialVariableLines(composition, indentation),
    `${indentation}--ct-right-panel-tint: ${percentage(composition.rightPanelTint, 72)};`,
  ];
  if (settingsViewportBand) {
    lines.push(
      `${indentation}--ct-settings-tint: ${percentage(
        settingsMaterialProjection(settingsViewportBand),
        52,
      )};`);
  }
  lines.push(
    `${indentation}--ct-safe-left: ${percentage(safeArea.left, 0)};`,
    `${indentation}--ct-safe-right: ${percentage(safeArea.right, 0)};`,
    `${indentation}--ct-safe-top: ${percentage(safeArea.top, 0)};`,
    `${indentation}--ct-safe-bottom: ${percentage(safeArea.bottom, 0)};`,
  );
  return lines;
}

export function buildCompositionCss(config) {
  const composition = config?.composition;
  if (!composition || typeof composition !== "object") return "";
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(String(config.id ?? ""))) {
    throw new Error("Theme has an invalid id for composition CSS");
  }
  const safeArea = composition.safeArea && typeof composition.safeArea === "object"
    ? composition.safeArea
    : {};
  const authoring = config.compiledAuthoring;
  const selector = `html.codex-theme[data-codex-theme="${config.id}"]`;
  const compilerOwnedProjection = authoring?.sourceSchemaVersion === 2 &&
    authoring?.projection?.preservesDeclaredComposition === false;
  const wideBehavior = compilerOwnedProjection
    ? authoring.experience.responsivePolicy.wide
    : "preserve";
  const wideComposition = projectedComposition(composition, wideBehavior);
  const css = [
    `${selector} {`,
    ...compositionVariableLines(wideComposition, safeArea, "  ", "wide"),
  ];
  if (authoring?.layoutPlan?.topology) {
    css.push(`  --ct-authoring-art-topology: ${authoring.layoutPlan.topology};`);
  }
  css.push("}");

  if (composition.mode === "portrait-zone") {
    const portraitRoot =
      `${selector}.codex-theme-has-art[data-ct-composition="portrait-zone"]`;
    css.push(
      `${portraitRoot} body[data-ct-part~="workbench"] {`,
      "  background-color: var(--ct-canvas) !important;",
      "  background-image:",
      "    linear-gradient(",
      "      color-mix(in srgb, var(--ct-canvas) var(--ct-workbench-scrim, 35%), transparent),",
      "      color-mix(in srgb, var(--ct-canvas) var(--ct-workbench-scrim, 35%), transparent)",
      "    ),",
      "    var(--ct-workbench-art, var(--ct-user-art, none)) !important;",
      "  background-position: center, var(--ct-art-focus-x, 50%) var(--ct-art-focus-y, 50%) !important;",
      "  background-size: cover, auto 100% !important;",
      "  background-repeat: no-repeat, no-repeat !important;",
      "  background-attachment: fixed, fixed !important;",
      "}",
      `${portraitRoot} [data-ct-part~="sidebar"] {`,
      "  background-color: color-mix(in srgb, var(--ct-surface) var(--ct-sidebar-tint, 72%), transparent) !important;",
      "  background-image: none !important;",
      "}",
      `${portraitRoot} [data-ct-part~="main"] {`,
      "  background-color: color-mix(in srgb, var(--ct-surface) var(--ct-main-tint, 78%), transparent) !important;",
      "  background-image: none !important;",
      "}",
      `${portraitRoot} [data-ct-part~="right-panel"] {`,
      "  background-color: color-mix(in srgb, var(--ct-surface) var(--ct-right-panel-tint, 72%), transparent) !important;",
      "  background-image: none !important;",
      "}",
      `${portraitRoot} [data-ct-part~="settings"] {`,
      "  background-color: color-mix(in srgb, var(--ct-surface) var(--ct-settings-tint, 52%), transparent) !important;",
      "  background-image: none !important;",
      "}",
      `${portraitRoot} [data-ct-part~="sidebar"] :where(`,
      "  nav, [role=\"navigation\"], [class~=\"bg-token-main-surface-primary\"]",
      "),",
      `${portraitRoot} [data-ct-part~="main"] :where(`,
      "  [role=\"main\"], [data-app-action-timeline-scroll],",
      "  [class~=\"bg-token-main-surface-primary\"], .scrollbar-stable",
      "),",
      `${portraitRoot} [data-ct-part~="right-panel"] :where(`,
      "  [role=\"tabpanel\"], [data-app-shell-tabs=\"true\"],",
      "  [class~=\"bg-token-main-surface-primary\"]",
      "),",
      `${portraitRoot} [data-ct-part~="settings"] :where(`,
      "  .main-surface, [role=\"main\"],",
      "  [class~=\"bg-token-main-surface-primary\"], .scrollbar-stable",
      ") {",
      "  background-color: transparent !important;",
      "  background-image: none !important;",
      "}",
    );
  }

  if (authoring?.sourceSchemaVersion === 2) {
    const declaredScope = new Set(authoring.experience.backgroundScope);
    for (const route of ["home", "thread", "settings"]) {
      if (declaredScope.has(route)) continue;
      css.push(
        `${selector}[data-ct-scope="${route}"] body[data-ct-part~="workbench"] {`,
        "  background-color: var(--ct-canvas) !important;",
        "  background-image: none !important;",
        "}",
      );
    }
    if (!declaredScope.has("right-panel")) {
      css.push(
        `${selector} [data-ct-part~="right-panel"] {`,
        "  background-color: var(--ct-surface) !important;",
        "  background-image: none !important;",
        "}",
      );
    }
  }

  if (compilerOwnedProjection) {
    const responsiveRules = [
      {
        maximum: 1200,
        behavior: authoring.experience.responsivePolicy.split,
        settingsViewportBand: null,
      },
      {
        maximum: 720,
        behavior: authoring.experience.responsivePolicy.narrow,
        settingsViewportBand: null,
      },
    ];
    for (const responsive of responsiveRules) {
      const projected = projectedComposition(composition, responsive.behavior);
      css.push(
        `@media (max-width: ${responsive.maximum}px) {`,
        `  ${selector} {`,
        ...compositionVariableLines(
          projected,
          safeArea,
          "    ",
          responsive.settingsViewportBand,
        ),
        "  }",
      );
      if (responsive.behavior === "hide") {
        css.push(
          `  ${selector} body[data-ct-part~="workbench"] {`,
          "    background-color: var(--ct-canvas) !important;",
          "    background-image: none !important;",
          "  }",
        );
      }
      css.push("}");
    }
    for (const settingsResponsive of [
      { maximum: 900, settingsViewportBand: "split" },
      { maximum: 620, settingsViewportBand: "narrow" },
    ]) {
      css.push(
        `@media (max-width: ${settingsResponsive.maximum}px) {`,
        `  ${selector} {`,
        `    --ct-settings-tint: ${percentage(
          settingsMaterialProjection(settingsResponsive.settingsViewportBand),
          52,
        )};`,
        "  }",
        "}",
      );
    }
  } else {
    for (const responsive of [
      { maximum: 900, behavior: "rebalance", settingsViewportBand: "split" },
      { maximum: 620, behavior: "reduce", settingsViewportBand: "narrow" },
    ]) {
      const projected = projectedComposition(composition, responsive.behavior);
      css.push(
        `@media (max-width: ${responsive.maximum}px) {`,
        `  ${selector} {`,
        ...headerMaterialVariableLines(projected, "    "),
        `    --ct-settings-tint: ${percentage(
          settingsMaterialProjection(responsive.settingsViewportBand),
          52,
        )};`,
        "  }",
        "}",
      );
    }
  }
  return css.join("\n");
}

export async function loadPayload(themeDir) {
  const resolvedTheme = await fs.realpath(themeDir);
  const baseCssFile = path.join(root, "assets", "base.css");
  const templateFile = path.join(root, "assets", "renderer-inject.js");
  const compilerFile = path.join(root, "scripts", "composition-compiler.mjs");
  const configFile = path.join(resolvedTheme, "theme.json");
  const themeCssFile = path.join(resolvedTheme, "theme.css");
  const [baseCss, template, selectorSource, configText] = await Promise.all([
    fs.readFile(baseCssFile, "utf8"),
    fs.readFile(templateFile, "utf8"),
    loadSelectorContractSource(root),
    fs.readFile(configFile, "utf8"),
  ]);
  const { contract: selectorContract, text: selectorsText } = selectorSource;
  const sourceConfig = JSON.parse(configText);
  if (!sourceConfig?.id || typeof sourceConfig.id !== "string" ||
      !sourceConfig.tokens || !sourceConfig.capabilities) {
    throw new Error("Theme has an unsupported schema");
  }
  const compiledAuthoring = compileThemeAuthoring(sourceConfig, {
    expectedId: sourceConfig.id,
    label: configFile,
  });
  const config = {
    ...sourceConfig,
    composition: compiledAuthoring.runtimeComposition,
    compiledAuthoring: {
      compilerContractVersion: compiledAuthoring.compilerContractVersion,
      sourceSchemaVersion: compiledAuthoring.sourceSchemaVersion,
      targetSchemaVersion: compiledAuthoring.targetSchemaVersion,
      authoringMode: compiledAuthoring.authoringMode,
      creativeBrief: compiledAuthoring.creativeBrief,
      experience: compiledAuthoring.experience,
      layoutPlan: compiledAuthoring.layoutPlan,
      coordinateOwnership: compiledAuthoring.coordinateOwnership,
      projection: compiledAuthoring.projection,
    },
  };
  const themeCss = await optionalFile(themeCssFile, 256 * 1024);
  const themeCssText = themeCss?.toString("utf8") ?? "";
  if (/@import\b|https?:\/\/|url\s*\(/i.test(themeCssText)) {
    throw new Error("theme.css cannot import or fetch remote/local resources; use declared theme art instead");
  }
  const themeCssPolicySource = themeCssText.replace(/\/\*[\s\S]*?\*\//g, "");
  if (/\[\s*data-ct-part\s*~=\s*["']?browser["']?\s*\]|data-browser-sidebar-webview|\bwebview\b/i
    .test(themeCssPolicySource)) {
    throw new Error(
      "theme.css cannot target the Browser guest surface; native Browser paint is runtime-owned",
    );
  }
  const art = await loadArt(resolvedTheme, config);
  const compositionCss = buildCompositionCss(config);
  const css = `${baseCss}\n${compositionCss}\n${themeCssText}`;
  const cssHash = crypto.createHash("sha256").update(css).digest("hex");
  const themeConfig = {
    ...config,
    artDataUrl: art?.primary?.dataUrl ?? null,
    artMetadata: art?.primary?.metadata ?? null,
    artVariantDataUrls: Object.fromEntries(
      Object.entries(art?.variants ?? {}).map(([name, asset]) => [name, asset.dataUrl]),
    ),
    artVariantMetadata: Object.fromEntries(
      Object.entries(art?.variants ?? {}).map(([name, asset]) => [name, asset.metadata]),
    ),
  };
  const payloadRevision = crypto.createHash("sha256")
    .update(ENGINE_VERSION).update("\0")
    .update(css).update("\0")
    .update(template).update("\0")
    .update(selectorsText).update("\0")
    .update(JSON.stringify(themeConfig))
    .digest("hex");
  // Function replacements insert JSON verbatim. Plain replacement strings interpret
  // $$, $&, $`, and $', which can corrupt otherwise valid theme metadata.
  const payload = template
    .replace("__CODEX_THEME_CSS_JSON__", () => JSON.stringify(css))
    .replace("__CODEX_THEME_CONFIG_JSON__", () => JSON.stringify(themeConfig))
    .replace("__CODEX_THEME_SELECTORS_JSON__", () => JSON.stringify(selectorContract))
    .replace("__CODEX_THEME_VERSION_JSON__", () => JSON.stringify(ENGINE_VERSION))
    .replace("__CODEX_THEME_STYLE_REVISION_JSON__", () => JSON.stringify(cssHash))
    .replace("__CODEX_THEME_PAYLOAD_REVISION_JSON__", () => JSON.stringify(payloadRevision));
  assertPayloadIntegrity(payload);
  const payloadHash = crypto.createHash("sha256").update(payload).digest("hex");
  const watchFiles = [
    baseCssFile,
    templateFile,
    selectorSource.file,
    compilerFile,
    configFile,
    themeCssFile,
  ];
  if (config.art?.file) watchFiles.push(path.join(resolvedTheme, config.art.file));
  for (const descriptor of Object.values(config.art?.variants ?? {})) {
    if (descriptor?.file) watchFiles.push(path.join(resolvedTheme, descriptor.file));
  }
  return {
    payload,
    payloadHash,
    payloadRevision,
    cssHash,
    styleRevision: cssHash,
    selectorContractHash: selectorSource.payloadHash,
    watchFiles,
    theme: config,
  };
}

async function watchStamp(files) {
  const hash = crypto.createHash("sha256");
  for (const file of [...new Set(files)].sort()) {
    hash.update(file);
    try {
      const stat = await fs.stat(file);
      hash.update(`${stat.size}:${stat.mtimeMs}`);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      hash.update("missing");
    }
  }
  return hash.digest("hex");
}

async function writeWatchStatus(statusFile, event, loaded, generation, extra = {}) {
  if (!statusFile) return;
  const status = {
    schemaVersion: 1,
    event,
    pid: process.pid,
    port: extra.port ?? null,
    themeId: loaded?.theme?.id ?? null,
    themeDir: extra.themeDir ?? null,
    generation,
    payloadHash: loaded?.payloadHash ?? null,
    payloadRevision: loaded?.payloadRevision ?? null,
    cssHash: loaded?.cssHash ?? null,
    styleRevision: loaded?.styleRevision ?? null,
    selectorContractHash: loaded?.selectorContractHash ?? null,
    targetCount: extra.targetCount ?? null,
    error: extra.error ?? null,
    updatedAt: new Date().toISOString(),
  };
  await fs.mkdir(path.dirname(statusFile), { recursive: true });
  const temporary = `${statusFile}.${process.pid}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(status, null, 2)}\n`, "utf8");
  await fs.rename(temporary, statusFile);
}

async function removeFromSession(session) {
  return session.evaluate(`(() => {
    window.__CODEX_THEME_DISABLED__ = true;
    const state = window.__CODEX_THEME_STATE__;
    if (state?.cleanup) return state.cleanup();
    document.documentElement?.classList.remove('codex-theme', 'codex-theme-active', 'codex-theme-has-art');
    document.documentElement?.removeAttribute('data-codex-theme');
    document.documentElement?.removeAttribute('data-codex-theme-mode');
    document.documentElement?.removeAttribute('data-ct-scope');
    document.documentElement?.removeAttribute('data-ct-contract-version');
    document.documentElement?.removeAttribute('data-ct-composition');
    document.documentElement?.removeAttribute('data-ct-narrow-art');
    for (const element of document.querySelectorAll('[data-ct-part-owned="1"]')) {
      element.removeAttribute('data-ct-part');
      element.removeAttribute('data-ct-part-owned');
      element.removeAttribute('data-ct-part-tier');
    }
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

async function verifySession(session, expectedCssHash, expectedPayloadRevision) {
  return session.evaluate(`(async () => {
    const state = window.__CODEX_THEME_STATE__;
    const byPart = (part) => document.querySelector('[data-ct-part~="' + part + '"]');
    const shell = byPart('main') || document.querySelector(
      '[data-app-shell-main-surface], main.main-surface, div.main-surface'
    );
    const workbench = byPart('workbench') || document.body;
    const sidebar = byPart('sidebar') || document.querySelector('aside.app-shell-left-panel');
    const composer = byPart('composer') || document.querySelector('.composer-surface-chrome');
    const main = document.querySelector('[role="main"]') || shell;
    const settings = byPart('settings');
    const rightPanel = byPart('right-panel') || document.querySelector('[data-app-shell-focus-area="right-panel"]');
    const appHeader = document.querySelector(
      'header[data-app-shell-application-menu-bar="true"]',
    );
    const browserHost = document.querySelector(
      '[data-browser-sidebar-webview][data-app-shell-focus-area="right-panel"]',
    );
    const browserSurface = browserHost?.querySelector('webview') ?? null;
    const style = document.getElementById('codex-theme-style');
    const styleBytes = new TextEncoder().encode(style?.textContent ?? '');
    const styleDigest = await crypto.subtle.digest('SHA-256', styleBytes);
    const liveCssHash = [...new Uint8Array(styleDigest)]
      .map((value) => value.toString(16).padStart(2, '0')).join('');
    const cssHashMatches = liveCssHash === ${JSON.stringify(expectedCssHash)};
    const styleRevisionMatches = state?.styleRevision === ${JSON.stringify(expectedCssHash)};
    const payloadRevisionMatches =
      state?.payloadRevision === ${JSON.stringify(expectedPayloadRevision)};
    const overflowX = document.documentElement.scrollWidth > document.documentElement.clientWidth + 1;
    const isVisible = (element, computed, rect) => Boolean(element && computed.display !== 'none' &&
      computed.visibility !== 'hidden' && Number(computed.opacity) > 0 && rect.width > 0 && rect.height > 0);
    const colorCanvas = document.createElement('canvas');
    colorCanvas.width = 1;
    colorCanvas.height = 1;
    const colorContext = colorCanvas.getContext('2d', { willReadFrequently: true });
    const parseComputedColor = (value) => {
      if (typeof value !== 'string' || !value || !colorContext) return null;
      colorContext.clearRect(0, 0, 1, 1);
      colorContext.fillStyle = '#000000';
      colorContext.fillStyle = value;
      colorContext.fillRect(0, 0, 1, 1);
      const [red, green, blue, alpha] = colorContext.getImageData(0, 0, 1, 1).data;
      return {
        red,
        green,
        blue,
        alpha: alpha / 255,
      };
    };
    const relativeLuminance = ({ red, green, blue }) => {
      const channels = [red, green, blue].map((channel) => {
        const value = Math.max(0, Math.min(255, channel)) / 255;
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    };
    const compositeColor = (foreground, background, opacity = 1) => {
      const alpha = Math.max(0, Math.min(1, foreground.alpha * opacity));
      return {
        red: foreground.red * alpha + background.red * (1 - alpha),
        green: foreground.green * alpha + background.green * (1 - alpha),
        blue: foreground.blue * alpha + background.blue * (1 - alpha),
        alpha: 1,
      };
    };
    const computedContrast = (foreground, background, opacity = 1) => {
      if (!foreground || !background || background.alpha < 0.99) return null;
      const first = relativeLuminance(compositeColor(foreground, background, opacity));
      const second = relativeLuminance(background);
      return Math.round(((Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)) * 100) / 100;
    };
    const effectiveOpacity = (element) => {
      let opacity = 1;
      for (let current = element; current; current = current.parentElement) {
        opacity *= Number(getComputedStyle(current).opacity);
        if (opacity <= 0.01) return 0;
      }
      return opacity;
    };
    const opaqueBackground = (element) => {
      for (let current = element; current; current = current.parentElement) {
        const computed = getComputedStyle(current);
        const color = parseComputedColor(computed.backgroundColor);
        if (color?.alpha >= 0.99) {
          return {
            color,
            css: computed.backgroundColor,
            backgroundImage: computed.backgroundImage !== 'none',
          };
        }
      }
      return null;
    };
    const inspectSemanticTokenContrast = () => {
      const definitions = [
        ['native-surface', '--color-text-foreground', '--color-background-surface'],
        ['native-panel', '--color-text-foreground', '--color-background-panel'],
        ['native-control', '--color-text-foreground', '--color-background-control'],
        ['native-elevated-primary', '--color-text-foreground', '--color-background-elevated-primary'],
        ['native-elevated-secondary', '--color-text-foreground', '--color-background-elevated-secondary'],
        ['token-main-surface', '--color-token-foreground', '--color-token-main-surface-primary'],
        ['token-input', '--color-token-input-foreground', '--color-token-input-background'],
        ['vscode-editor', '--vscode-foreground', '--vscode-editor-background'],
        ['vscode-panel', '--vscode-foreground', '--vscode-panel-background'],
        ['vscode-widget', '--vscode-editorWidget-foreground', '--vscode-editorWidget-background'],
        ['vscode-input', '--vscode-input-foreground', '--vscode-input-background'],
        ['vscode-menu', '--vscode-menu-foreground', '--vscode-menu-background'],
        ['vscode-terminal', '--vscode-terminal-foreground', '--vscode-terminal-background'],
      ];
      const scopes = [
        ['root', document.documentElement],
        ...[...document.querySelectorAll('.app-theme.electron-light, .app-theme.electron-dark')]
          .map((element, index) => ['nested-app-theme-' + index, element]),
      ];
      const pairs = scopes.flatMap(([scope, element]) => {
        const computed = getComputedStyle(element);
        return definitions.map(([name, foregroundToken, backgroundToken]) => {
          const foregroundCss = computed.getPropertyValue(foregroundToken).trim();
          const backgroundCss = computed.getPropertyValue(backgroundToken).trim();
          const contrastRatio = computedContrast(
            parseComputedColor(foregroundCss),
            parseComputedColor(backgroundCss),
          );
          return {
            scope,
            name,
            foregroundToken,
            backgroundToken,
            foreground: foregroundCss || null,
            background: backgroundCss || null,
            contrastRatio,
            minimum: 4.5,
            pass: contrastRatio !== null && contrastRatio >= 4.5,
          };
        });
      });
      return {
        pairCount: pairs.length,
        riskCount: pairs.filter((pair) => !pair.pass).length,
        pairs,
      };
    };
    const inspectSurface = (selector) => {
      const element = document.querySelector(selector);
      if (!element) return { present: false };
      const computed = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const foreground = parseComputedColor(computed.color);
      const ownBackground = parseComputedColor(computed.backgroundColor);
      const background = opaqueBackground(element);
      return {
        present: true,
        visible: isVisible(element, computed, rect),
        width: Math.round(rect.width * 10) / 10,
        height: Math.round(rect.height * 10) / 10,
        color: computed.color,
        backgroundColor: background?.css ?? computed.backgroundColor,
        ownBackgroundAlpha: ownBackground?.alpha ?? null,
        backgroundImage: computed.backgroundImage !== 'none' || Boolean(background?.backgroundImage),
        opaqueBackground: Boolean(background),
        contrastRatio: computedContrast(foreground, background?.color),
        opacity: computed.opacity,
        pointerEvents: computed.pointerEvents,
        fontSize: computed.fontSize,
        lineHeight: computed.lineHeight,
      };
    };
    const surfaceSelectors = {
      workbench: '[data-ct-part~="workbench"]',
      shell: '[data-ct-part~="main"]',
      sidebar: '[data-ct-part~="sidebar"]',
      header: '[data-ct-part~="header"]',
      main: '[data-ct-part~="main"]',
      composer: '[data-ct-part~="composer"]',
      code: 'pre, code',
      terminal: '[data-ct-part~="terminal"]',
      browser: '[data-ct-part~="browser"]',
      panel: '[data-ct-part~="right-panel"]',
      settings: '[data-ct-part~="settings"]',
      diff: '[data-testid*="diff" i], [class*="diff-view" i], [class*="patch-view" i]',
      approval: '[data-testid*="approval" i], [data-testid*="permission" i]',
      dialog: '[role="dialog"]',
      menu: '[role="menu"]',
    };
    const surfaces = Object.fromEntries(Object.entries(surfaceSelectors)
      .map(([name, selector]) => [name, inspectSurface(selector)]));
    const inspectTextContrast = () => {
      const roots = [
        ['settings', '[data-ct-part~="settings"]'],
        ['sidebar', '[data-ct-part~="sidebar"]'],
        ['composer', '[data-ct-part~="composer"]'],
        ['panel', '[data-ct-part~="right-panel"]'],
        ['main', '[data-ct-part~="main"]'],
        ['shell', '[data-ct-part~="workbench"]'],
      ];
      const seen = new Set();
      const risks = [];
      let inspectedCount = 0;
      let passingCount = 0;
      for (const [surface, selector] of roots) {
        const root = document.querySelector(selector);
        if (!root) continue;
        for (const element of [root, ...root.querySelectorAll('*')]) {
          if (seen.has(element) || element.matches('script, style, noscript')) continue;
          const hasDirectText = [...element.childNodes].some((node) =>
            node.nodeType === Node.TEXT_NODE && Boolean(node.nodeValue?.trim()));
          if (!hasDirectText || element.closest('[aria-hidden="true"], [hidden], [inert]')) continue;
          const computed = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          const opacity = effectiveOpacity(element);
          if (!isVisible(element, computed, rect) || opacity <= 0.01 ||
              rect.width < 2 || rect.height < 2 || rect.bottom <= 0 || rect.right <= 0 ||
              rect.top >= innerHeight || rect.left >= innerWidth) {
            continue;
          }
          seen.add(element);
          if (inspectedCount >= 800) continue;
          inspectedCount += 1;
          const foreground = parseComputedColor(computed.color);
          const background = opaqueBackground(element);
          const contrastRatio = computedContrast(foreground, background?.color, opacity);
          const fontSize = Number.parseFloat(computed.fontSize);
          const fontWeight = Number.parseInt(computed.fontWeight, 10);
          const minimum = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700) ? 3 : 4.5;
          if (contrastRatio !== null && contrastRatio >= minimum) {
            passingCount += 1;
            continue;
          }
          risks.push({
            surface,
            reason: contrastRatio === null ? 'unresolved-contrast' : 'contrast-below-minimum',
            contrastRatio,
            minimum,
            color: computed.color,
            backgroundColor: background?.css ?? null,
            backgroundImage: background?.backgroundImage ?? false,
            effectiveOpacity: Math.round(opacity * 1000) / 1000,
            tag: element.tagName.toLowerCase(),
            role: element.getAttribute('role'),
            testId: element.getAttribute('data-testid'),
            classSignals: [...element.classList]
              .filter((name) => /(?:token|foreground|text|markdown|modelpicker)/i.test(name))
              .slice(0, 4),
          });
        }
      }
      return {
        inspectedCount,
        passingCount,
        riskCount: risks.length,
        risks: risks.slice(0, 40),
      };
    };
    const textContrast = inspectTextContrast();
    const semanticTokenContrast = inspectSemanticTokenContrast();
    const semanticParts = Object.fromEntries([
      'workbench', 'sidebar', 'header', 'main', 'right-panel', 'composer',
      'settings', 'terminal', 'browser', 'dialog', 'menu', 'overlay',
    ].map((part) => [part, document.querySelectorAll('[data-ct-part~="' + part + '"]').length]));
    const scope = document.documentElement.getAttribute('data-ct-scope');
    const contractVersion = document.documentElement.getAttribute('data-ct-contract-version');
    const reducedTransparencyActive = matchMedia(
      '(prefers-reduced-transparency: reduce)',
    ).matches;
    const increasedContrastActive = matchMedia('(prefers-contrast: more)').matches;
    const forcedColorsActive = matchMedia('(forced-colors: active)').matches;
    const materialFallbackActive =
      reducedTransparencyActive || increasedContrastActive || forcedColorsActive;
    const settingsRect = settings?.getBoundingClientRect() ?? null;
    const settingsContinuityPass = scope !== 'settings' || Boolean(
      settings && !settings.hasAttribute('data-settings-panel-slug') &&
      settingsRect.width >= innerWidth * 0.35 &&
      settingsRect.height >= innerHeight * 0.5
    );
    const settingsContinuityEvidence = {
      required: scope === 'settings',
      surfacePresent: Boolean(settings),
      navigationMarkerMisowned: Boolean(settings?.hasAttribute('data-settings-panel-slug')),
      width: settingsRect ? Math.round(settingsRect.width) : null,
      height: settingsRect ? Math.round(settingsRect.height) : null,
      pass: settingsContinuityPass,
    };
    const settingsComputed = settings ? getComputedStyle(settings) : null;
    const settingsBackground = settingsComputed
      ? parseComputedColor(settingsComputed.backgroundColor)
      : null;
    const settingsBackdropFilter = settingsComputed
      ? (settingsComputed.backdropFilter || settingsComputed.webkitBackdropFilter || 'none')
      : null;
    const settingsArtMaterialRequired = scope === 'settings' && Boolean(
      document.documentElement.classList.contains('codex-theme-has-art') &&
      state?.compositionMode && state.compositionMode !== 'none'
    );
    const configuredSettingsTint = getComputedStyle(document.documentElement)
      .getPropertyValue('--ct-settings-tint').trim();
    const configuredSettingsTintMatch = /^(52|60|68)%$/.exec(configuredSettingsTint);
    const configuredSettingsAlpha = configuredSettingsTintMatch
      ? Number(configuredSettingsTintMatch[1]) / 100
      : null;
    const settingsIntegratedMaterialPass = Boolean(
      settingsBackground && configuredSettingsAlpha !== null &&
      Math.abs(settingsBackground.alpha - configuredSettingsAlpha) <= 0.015 &&
      settingsComputed?.backgroundImage === 'none' && settingsBackdropFilter === 'none'
    );
    const settingsFallbackMaterialPass = Boolean(
      settingsBackground && settingsBackground.alpha >= 0.99 &&
      settingsComputed?.backgroundImage === 'none' && settingsBackdropFilter === 'none'
    );
    const settingsMaterialPass = !settingsArtMaterialRequired ||
      (materialFallbackActive ? settingsFallbackMaterialPass : settingsIntegratedMaterialPass);
    const settingsMaterialEvidence = {
      required: settingsArtMaterialRequired,
      fallbackActive: materialFallbackActive,
      backgroundColor: settingsComputed?.backgroundColor ?? null,
      backgroundAlpha: settingsBackground
        ? Math.round(settingsBackground.alpha * 1000) / 1000
        : null,
      backgroundImage: settingsComputed?.backgroundImage ?? null,
      backdropFilter: settingsBackdropFilter,
      configuredTint: configuredSettingsTint || null,
      pass: settingsMaterialPass,
    };
    const appHeaderComputed = appHeader ? getComputedStyle(appHeader) : null;
    const appHeaderBackground = appHeaderComputed
      ? parseComputedColor(appHeaderComputed.backgroundColor)
      : null;
    const appHeaderTransparencyPass = !appHeader || Boolean(
      appHeaderBackground && appHeaderBackground.alpha <= 0.01 &&
      appHeaderComputed.backgroundImage === 'none'
    );
    const appHeaderTransparencyEvidence = {
      present: Boolean(appHeader),
      backgroundColor: appHeaderComputed?.backgroundColor ?? null,
      backgroundAlpha: appHeaderBackground
        ? Math.round(appHeaderBackground.alpha * 1000) / 1000
        : null,
      backgroundImage: appHeaderComputed?.backgroundImage ?? null,
      pass: appHeaderTransparencyPass,
    };
    const headerFallbackActive = materialFallbackActive;
    const semanticHeaderMaterialSamples = [
      ...document.querySelectorAll('[data-ct-part~="header"]'),
    ].map((element) => {
      const computed = getComputedStyle(element);
      const background = parseComputedColor(computed.backgroundColor);
      const borderBottom = parseComputedColor(computed.borderBottomColor);
      const backgroundImage = computed.backgroundImage;
      const gradientLayers = backgroundImage.toLowerCase().split('linear-gradient(').length - 1;
      const backdropFilter = computed.backdropFilter ||
        computed.webkitBackdropFilter || 'none';
      const integratedPass = Boolean(
        background && background.alpha <= 0.01 && gradientLayers >= 2 &&
        borderBottom && borderBottom.alpha <= 0.01 &&
        computed.boxShadow === 'none' && backdropFilter === 'none'
      );
      const fallbackPass = Boolean(
        background && background.alpha >= 0.99 && backgroundImage === 'none' &&
        computed.boxShadow === 'none' && backdropFilter === 'none'
      );
      return {
        backgroundColor: computed.backgroundColor,
        backgroundAlpha: background
          ? Math.round(background.alpha * 1000) / 1000
          : null,
        backgroundImage,
        gradientLayers,
        borderBottomColor: computed.borderBottomColor,
        boxShadow: computed.boxShadow,
        backdropFilter,
        pass: headerFallbackActive ? fallbackPass : integratedPass,
      };
    });
    const semanticHeaderMaterialPass = semanticHeaderMaterialSamples.every(
      (sample) => sample.pass,
    );
    const semanticHeaderMaterialEvidence = {
      present: semanticHeaderMaterialSamples.length > 0,
      count: semanticHeaderMaterialSamples.length,
      fallbackActive: headerFallbackActive,
      samples: semanticHeaderMaterialSamples,
      pass: semanticHeaderMaterialPass,
    };
    const semanticMapPass = contractVersion === '1' &&
      semanticParts.workbench === 1 && semanticParts.sidebar === 1 && semanticParts.main === 1 &&
      (!rightPanel || semanticParts['right-panel'] === 1) &&
      (!composer || semanticParts.composer === 1) &&
      (scope !== 'settings' || semanticParts.settings === 1) &&
      (!browserHost || semanticParts.browser === 1);
    const normalizeColorScheme = (value) => {
      const normalized = String(value ?? '').toLowerCase();
      if (normalized.includes('light')) return 'light';
      if (normalized.includes('dark')) return 'dark';
      return 'normal';
    };
    const browserComputed = browserSurface ? getComputedStyle(browserSurface) : null;
    const browserPaint = state?.nativeBrowserPaint;
    const browserBackgroundMatches = !browserHost || Boolean(
      browserComputed && browserPaint &&
      browserComputed.backgroundColor === browserPaint.backgroundColor
    );
    const browserColorSchemeMatches = !browserHost || Boolean(
      browserComputed && browserPaint &&
      normalizeColorScheme(browserComputed.colorScheme) === browserPaint.colorScheme
    );
    const browserPaintIsolationPass =
      browserBackgroundMatches && browserColorSchemeMatches;
    const browserIsolationEvidence = {
      hostPresent: Boolean(browserHost),
      surfacePresent: Boolean(browserSurface),
      semanticCount: semanticParts.browser,
      backgroundMatches: browserBackgroundMatches,
      colorSchemeMatches: browserColorSchemeMatches,
      pass: browserPaintIsolationPass,
    };
    const matchingBackgroundRules = (element) => {
      const matches = [];
      const visit = (rules) => {
        for (const rule of rules ?? []) {
          const style = rule.style;
          if (rule.selectorText && style &&
              (style.background || style.backgroundColor || style.backgroundImage)) {
            try {
              if (element.matches(rule.selectorText)) {
                matches.push({
                  selector: rule.selectorText,
                  background: style.background || null,
                  backgroundColor: style.backgroundColor || null,
                  important: style.getPropertyPriority('background') === 'important' ||
                    style.getPropertyPriority('background-color') === 'important',
                });
              }
            } catch {}
          }
          if (rule.cssRules?.length) {
            try { visit(rule.cssRules); } catch {}
          }
        }
      };
      for (const sheet of document.styleSheets) {
        try { visit(sheet.cssRules); } catch {}
      }
      return matches.slice(-12);
    };
    const coveringOpaqueDescendant = (element) => {
      if (!element) return null;
      const rootRect = element.getBoundingClientRect();
      const rootArea = Math.max(1, rootRect.width * rootRect.height);
      let maximum = null;
      let inspected = 0;
      for (const candidate of element.querySelectorAll('*')) {
        if (inspected++ >= 600) break;
        const computed = getComputedStyle(candidate);
        const rect = candidate.getBoundingClientRect();
        if (!isVisible(candidate, computed, rect)) continue;
        const color = parseComputedColor(computed.backgroundColor);
        if (!color || color.alpha < 0.96) continue;
        const overlapWidth = Math.max(0, Math.min(rootRect.right, rect.right) - Math.max(rootRect.left, rect.left));
        const overlapHeight = Math.max(0, Math.min(rootRect.bottom, rect.bottom) - Math.max(rootRect.top, rect.top));
        const coverage = Math.round((overlapWidth * overlapHeight / rootArea) * 1000) / 1000;
        if (!maximum || coverage > maximum.coverage) {
          maximum = {
            coverage,
            tag: candidate.tagName.toLowerCase(),
            role: candidate.getAttribute('role'),
            testId: candidate.getAttribute('data-testid'),
            classes: [...candidate.classList].slice(0, 8),
            parentTag: candidate.parentElement?.tagName.toLowerCase() ?? null,
            parentClasses: candidate.parentElement ? [...candidate.parentElement.classList].slice(0, 8) : [],
            themeClearMatch: candidate.matches(
              'html.codex-theme[data-ct-composition="continuous"] ' +
              '[data-ct-part~="right-panel"] [class~="bg-token-main-surface-primary"]',
            ),
            inlineBackground: candidate.style.background || null,
            inlineBackgroundColor: candidate.style.backgroundColor || null,
            inlineBackgroundImportant: candidate.style.getPropertyPriority('background') === 'important' ||
              candidate.style.getPropertyPriority('background-color') === 'important',
            matchingBackgroundRules: matchingBackgroundRules(candidate),
            backgroundColor: computed.backgroundColor,
          };
        }
      }
      return maximum;
    };
    const continuousComposition = state?.compositionMode === 'continuous';
    const compositionEnforced = continuousComposition &&
      !matchMedia('(prefers-reduced-transparency: reduce)').matches &&
      !matchMedia('(prefers-contrast: more)').matches &&
      !matchMedia('(forced-colors: active)').matches;
    const compositionPartNames = ['workbench', 'sidebar', 'header', 'main', 'right-panel', 'settings'];
    const compositionParts = compositionPartNames.map((part) => {
      const element = byPart(part);
      if (!element) return { part, present: false };
      const computed = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const background = parseComputedColor(computed.backgroundColor);
      const directArt = /url\\s*\\(/i.test(computed.backgroundImage);
      const opaqueCover = part === 'workbench' ? null : coveringOpaqueDescendant(element);
      return {
        part,
        present: true,
        visible: isVisible(element, computed, rect),
        directArt,
        backgroundImage: computed.backgroundImage,
        backgroundAlpha: background?.alpha ?? null,
        opaqueCover,
      };
    });
    const visibleCompositionParts = compositionParts.filter((part) => part.present && part.visible);
    const artOwners = visibleCompositionParts.filter((part) => part.directArt).map((part) => part.part);
    const repeatedArt = artOwners.filter((part) => part !== 'workbench');
    const opaqueBlockers = visibleCompositionParts
      .filter((part) => part.part !== 'workbench' &&
        ((part.backgroundAlpha ?? 1) >= 0.97 || (part.opaqueCover?.coverage ?? 0) > 0.92))
      .map((part) => ({
        part: part.part,
        backgroundAlpha: part.backgroundAlpha,
        opaqueCover: part.opaqueCover,
      }));
    const compositionPass = !compositionEnforced ||
      (artOwners.length === 1 && artOwners[0] === 'workbench' &&
        repeatedArt.length === 0 && opaqueBlockers.length === 0);
    const compositionEvidence = {
      mode: state?.compositionMode ?? 'none',
      enforced: compositionEnforced,
      pass: compositionPass,
      artOwners,
      repeatedArt,
      opaqueBlockers,
      parts: compositionParts,
    };
    const contrastRisks = ['main', 'composer', 'code', 'terminal', 'panel', 'settings', 'diff', 'approval', 'dialog', 'menu']
      .flatMap((name) => {
        const surface = surfaces[name];
        if (!surface?.present || !surface.visible) return [];
        const reasons = [];
        if (!surface.opaqueBackground) reasons.push('transparent-background');
        if (surface.contrastRatio === null) reasons.push('unresolved-contrast');
        else if (surface.contrastRatio < 4.5) reasons.push('contrast-below-4.5');
        return reasons.length ? [{ surface: name, reasons }] : [];
      });
    if (textContrast.riskCount) {
      contrastRisks.push({
        surface: 'visible-descendant-text',
        reasons: ['contrast-below-required-minimum'],
        count: textContrast.riskCount,
      });
    }
    if (semanticTokenContrast.riskCount) {
      contrastRisks.push({
        surface: 'semantic-paint-tokens',
        reasons: ['contrast-below-required-minimum'],
        count: semanticTokenContrast.riskCount,
      });
    }
    const controls = [...document.querySelectorAll('button, input, textarea, select, [role="button"]')]
      .map((element) => {
        const computed = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return { computed, rect, visible: isVisible(element, computed, rect) };
      })
      .filter((item) => item.visible);
    const activeAnimationCount = typeof document.getAnimations === 'function'
      ? document.getAnimations().filter((animation) => animation.playState === 'running').length
      : null;
    const usabilityEvidence = {
      contractVersion: ${QA_CONTRACT_VERSION},
      privacy: 'style-and-geometry-only',
      classification: 'PARTIAL',
      viewport: {
        width: document.documentElement.clientWidth,
        height: document.documentElement.clientHeight,
        devicePixelRatio: window.devicePixelRatio,
        visualScale: window.visualViewport?.scale ?? 1,
      },
      preferences: {
        reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
        reducedTransparency: reducedTransparencyActive,
        increasedContrast: increasedContrastActive,
        forcedColors: forcedColorsActive,
      },
      overflowX,
      semanticContract: {
        pass: semanticMapPass,
        version: contractVersion,
        scope,
        parts: semanticParts,
      },
      settingsContinuity: settingsContinuityEvidence,
      settingsMaterial: settingsMaterialEvidence,
      appHeaderTransparency: appHeaderTransparencyEvidence,
      semanticHeaderMaterial: semanticHeaderMaterialEvidence,
      browserIsolation: browserIsolationEvidence,
      composition: compositionEvidence,
      surfaces,
      contrastRisks,
      textContrast,
      semanticTokenContrast,
      controls: {
        visibleCount: controls.length,
        below24CssPxCount: controls.filter(({ rect }) => rect.width < 24 || rect.height < 24).length,
      },
      activeAnimationCount,
    };
    const pass = Boolean(state?.version === ${JSON.stringify(ENGINE_VERSION)} &&
      document.documentElement.classList.contains('codex-theme-active') && style &&
      shell && sidebar && (composer || main || settings) && !overflowX && cssHashMatches &&
      styleRevisionMatches && payloadRevisionMatches &&
      semanticMapPass && settingsContinuityPass && settingsMaterialPass &&
      appHeaderTransparencyPass &&
      semanticHeaderMaterialPass &&
      browserPaintIsolationPass && compositionPass &&
      textContrast.riskCount === 0 && semanticTokenContrast.riskCount === 0);
    return { pass, version: state?.version ?? null, themeId: state?.themeId ?? null,
      styleRevision: state?.styleRevision ?? null,
      payloadRevision: state?.payloadRevision ?? null,
      anchors: {
        shell: Boolean(shell),
        sidebar: Boolean(sidebar),
        composer: Boolean(composer),
        main: Boolean(main),
        settings: Boolean(settings),
        rightPanel: Boolean(rightPanel),
        browser: Boolean(browserHost),
      },
      overflowX, liveCssHash, cssHashMatches, styleRevisionMatches, payloadRevisionMatches,
      semanticMapPass, settingsContinuityPass, settingsMaterialPass,
      appHeaderTransparencyPass,
      semanticHeaderMaterialPass,
      browserPaintIsolationPass, compositionPass,
      usabilityEvidence };
  })()`);
}

async function waitForVerification(session, timeoutMs, expectedCssHash, expectedPayloadRevision) {
  const deadline = Date.now() + timeoutMs;
  let result;
  while (Date.now() < deadline) {
    result = await verifySession(session, expectedCssHash, expectedPayloadRevision);
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
  const loaded = ["once", "verify"].includes(options.mode) ? await loadPayload(options.themeDir) : null;
  const results = [];
  let captured = false;
  for (const { target, session, probe } of connected) {
    try {
      if (options.mode === "remove") await removeFromSession(session);
      if (options.mode === "once") await session.evaluate(loaded.payload);
      const result = options.mode === "remove"
        ? await verifyRemovedSession(session)
        : await waitForVerification(
          session,
          options.timeoutMs,
          loaded.cssHash,
          loaded.payloadRevision,
        );
      results.push({ targetId: target.id, probe, result });
      if (options.screenshot && !captured) { await capture(session, options.screenshot); captured = true; }
    } finally {
      session.close();
    }
  }
  const pass = results.length > 0 && results.every((item) => options.mode === "remove" ? item.result === true : item.result?.pass);
  console.log(JSON.stringify({ pass, mode: options.mode, version: ENGINE_VERSION, port: options.port,
    themeId: loaded?.theme.id ?? null, payloadHash: loaded?.payloadHash ?? null,
    payloadRevision: loaded?.payloadRevision ?? null,
    cssHash: loaded?.cssHash ?? null, styleRevision: loaded?.styleRevision ?? null,
    targets: results }, null, 2));
  if (!pass) process.exitCode = 2;
}

async function runWatch(options) {
  ensureWebSocket();
  let loaded = await loadPayload(options.themeDir);
  let activeStamp = await watchStamp(loaded.watchFiles);
  let observedStamp = activeStamp;
  let pendingSince = 0;
  let rejectedStamp = null;
  let generation = 1;
  const sessions = new Map();
  let stopping = false;
  let connectedOnce = false;
  let lastVerifiedAt = Date.now();
  const stop = () => { stopping = true; };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
  await writeWatchStatus(options.statusFile, "watching", loaded, generation, {
    port: options.port,
    themeDir: options.themeDir,
    targetCount: 0,
  });

  const refreshPayloadIfChanged = async () => {
    const currentStamp = await watchStamp(loaded.watchFiles);
    if (currentStamp !== observedStamp) {
      observedStamp = currentStamp;
      pendingSince = Date.now();
      return;
    }
    if (currentStamp === activeStamp || currentStamp === rejectedStamp ||
        Date.now() - pendingSince < 450) {
      return;
    }
    const previous = loaded;
    try {
      const candidate = await loadPayload(options.themeDir);
      if (candidate.payloadHash === previous.payloadHash) {
        loaded = candidate;
        activeStamp = await watchStamp(candidate.watchFiles);
        observedStamp = activeStamp;
        rejectedStamp = null;
        return;
      }
      const applied = [];
      for (const [targetId, session] of sessions) {
        if (session.closed) continue;
        await session.evaluate(candidate.payload);
        const verified = await waitForVerification(
          session,
          Math.min(options.timeoutMs, 5000),
          candidate.cssHash,
          candidate.payloadRevision,
        );
        if (!verified?.pass) throw new Error(`target ${targetId} rejected the refreshed payload`);
        applied.push(session);
      }
      loaded = candidate;
      generation += 1;
      activeStamp = await watchStamp(candidate.watchFiles);
      observedStamp = activeStamp;
      rejectedStamp = null;
      console.log(JSON.stringify({
        event: "reloaded",
        generation,
        themeId: loaded.theme.id,
        payloadHash: loaded.payloadHash,
        payloadRevision: loaded.payloadRevision,
        cssHash: loaded.cssHash,
        styleRevision: loaded.styleRevision,
        selectorContractHash: loaded.selectorContractHash,
        targetCount: applied.length,
      }));
      await writeWatchStatus(options.statusFile, "reloaded", loaded, generation, {
        port: options.port,
        themeDir: options.themeDir,
        targetCount: applied.length,
      });
    } catch (error) {
      rejectedStamp = currentStamp;
      for (const session of sessions.values()) {
        if (session.closed) continue;
        try {
          await session.evaluate(previous.payload);
          await waitForVerification(
            session,
            Math.min(options.timeoutMs, 5000),
            previous.cssHash,
            previous.payloadRevision,
          );
        } catch {}
      }
      console.error(`[codex-theme] refresh rejected; last-known-good retained: ${error.message}`);
      await writeWatchStatus(options.statusFile, "rejected", previous, generation, {
        port: options.port,
        themeDir: options.themeDir,
        targetCount: sessions.size,
        error: error.message,
      });
    }
  };

  while (!stopping) {
    try { await refreshPayloadIfChanged(); }
    catch (error) { console.error(`[codex-theme] watch probe failed: ${error.message}`); }
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
        const verified = await waitForVerification(
          session,
          Math.min(options.timeoutMs, 5000),
          loaded.cssHash,
          loaded.payloadRevision,
        );
        if (!verified?.pass) throw new Error("Theme payload did not verify");
        sessions.set(target.id, session);
        connectedOnce = true;
        lastVerifiedAt = Date.now();
        console.log(JSON.stringify({ event: "loaded", targetId: target.id, themeId: loaded.theme.id,
          generation, payloadHash: loaded.payloadHash, payloadRevision: loaded.payloadRevision,
          cssHash: loaded.cssHash, styleRevision: loaded.styleRevision,
          selectorContractHash: loaded.selectorContractHash }));
        await writeWatchStatus(options.statusFile, "loaded", loaded, generation, {
          port: options.port,
          themeDir: options.themeDir,
          targetCount: sessions.size,
        });
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
  await writeWatchStatus(options.statusFile, "stopped", loaded, generation, {
    port: options.port,
    themeDir: options.themeDir,
    targetCount: 0,
  });
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.mode === "check") {
    const loaded = await loadPayload(options.themeDir);
    console.log(JSON.stringify({ pass: true, version: ENGINE_VERSION, themeId: loaded.theme.id,
      payloadHash: loaded.payloadHash, payloadRevision: loaded.payloadRevision,
      cssHash: loaded.cssHash, styleRevision: loaded.styleRevision,
      payloadBytes: Buffer.byteLength(loaded.payload),
      selectorContractHash: loaded.selectorContractHash,
      qaContractVersion: QA_CONTRACT_VERSION, verificationScope: "engine-and-composition", qaStatus: "PARTIAL" }, null, 2));
  } else if (options.mode === "compatibility-probe") {
    console.log(JSON.stringify(await runCompatibilityProbe(options), null, 2));
  } else if (options.mode === "watch") await runWatch(options);
  else await runOneShot(options);
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.log(JSON.stringify({
      pass: false,
      version: ENGINE_VERSION,
      error: error?.message || String(error),
    }, null, 2));
    console.error(`[codex-theme] ${error.stack || error.message}`);
    process.exitCode = 1;
  });
}
