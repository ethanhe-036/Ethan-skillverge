((cssText, themeConfig, selectorContract, version, styleRevision, payloadRevision) => {
  "use strict";

  const STATE_KEY = "__CODEX_THEME_STATE__";
  const DISABLED_KEY = "__CODEX_THEME_DISABLED__";
  const STYLE_ID = "codex-theme-style";
  const THEME_ATTR = "data-codex-theme";
  const MODE_ATTR = "data-codex-theme-mode";
  const PART_ATTR = "data-ct-part";
  const PART_OWNER_ATTR = "data-ct-part-owned";
  const SCOPE_ATTR = "data-ct-scope";
  const CONTRACT_ATTR = "data-ct-contract-version";
  const COMPOSITION_ATTR = "data-ct-composition";
  const NARROW_ATTR = "data-ct-narrow-art";
  const THEME = themeConfig && typeof themeConfig === "object" ? themeConfig : {};
  const SELECTOR_CONTRACT = selectorContract && typeof selectorContract === "object"
    ? selectorContract
    : {};
  const TOKEN_MAP = {
    canvas: "--ct-canvas",
    surface: "--ct-surface",
    surfaceRaised: "--ct-surface-raised",
    text: "--ct-text",
    muted: "--ct-muted",
    accent: "--ct-accent",
    accentText: "--ct-accent-text",
    border: "--ct-border",
    focus: "--ct-focus",
    success: "--ct-success",
    warning: "--ct-warning",
    danger: "--ct-danger",
    info: "--ct-info",
    codeSurface: "--ct-code-surface",
    terminalSurface: "--ct-terminal-surface",
    terminalText: "--ct-terminal-text",
    diffAddSurface: "--ct-diff-add-surface",
    diffRemoveSurface: "--ct-diff-remove-surface",
    approvalSurface: "--ct-approval-surface",
    radiusSmall: "--ct-radius-small",
    radiusMedium: "--ct-radius-medium",
    radiusLarge: "--ct-radius-large",
    shadowLow: "--ct-shadow-low",
    shadowHigh: "--ct-shadow-high",
    bodyFont: "--ct-body-font",
    displayFont: "--ct-display-font",
  };
  const COMPOSITION_VARIABLES = [
    "--ct-art-focus-x", "--ct-art-focus-y", "--ct-workbench-scrim",
    "--ct-sidebar-tint", "--ct-main-tint", "--ct-header-tint", "--ct-right-panel-tint",
    "--ct-settings-tint",
    "--ct-header-material-top-tint", "--ct-header-material-mid-tint",
    "--ct-header-material-bottom-tint", "--ct-header-material-control-tint",
    "--ct-safe-left", "--ct-safe-right", "--ct-safe-top", "--ct-safe-bottom",
  ];
  const REQUIRED = Object.keys(TOKEN_MAP);

  const fail = (reason) => ({ installed: false, reason });
  if (!document.documentElement || !document.head || !document.body) return fail("document-not-ready");
  if (!document.querySelector("main, aside, [role='main'], textarea, [contenteditable='true']")) {
    return fail("unsupported-renderer: stable anchor missing");
  }
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(String(THEME.id || ""))) return fail("invalid-theme-id");
  if (typeof cssText !== "string" || !cssText.trim()) return fail("missing-css");
  if (!/^[0-9a-f]{64}$/.test(String(styleRevision || "")) ||
      !/^[0-9a-f]{64}$/.test(String(payloadRevision || ""))) {
    return fail("invalid-payload-revision");
  }
  if (!THEME.tokens || REQUIRED.some((key) => typeof THEME.tokens[key] !== "string" || !THEME.tokens[key])) {
    return fail("invalid-theme-tokens");
  }
  if (SELECTOR_CONTRACT.schemaVersion !== 1 ||
      !SELECTOR_CONTRACT.parts || typeof SELECTOR_CONTRACT.parts !== "object") {
    return fail("invalid-selector-contract");
  }
  const composition = THEME.composition && typeof THEME.composition === "object"
    ? THEME.composition
    : null;

  const previous = window[STATE_KEY];
  if (previous?.cleanup) previous.cleanup();
  window[DISABLED_KEY] = false;

  const root = document.documentElement;
  const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
  const configuredMode = THEME.capabilities?.mode || "light";
  const nativeBrowserSurface = document.querySelector(
    "[data-browser-sidebar-webview]" +
    "[data-app-shell-focus-area='right-panel'] > webview",
  );
  const nativeBrowserComputed = getComputedStyle(nativeBrowserSurface || root);
  const nativeBrowserColorScheme = (() => {
    const value = String(nativeBrowserComputed.colorScheme || "").toLowerCase();
    if (value.includes("light")) return "light";
    if (value.includes("dark")) return "dark";
    return "normal";
  })();
  const nativeBrowserPaint = {
    backgroundColor: nativeBrowserSurface
      ? nativeBrowserComputed.backgroundColor
      : "Canvas",
    color: nativeBrowserSurface ? nativeBrowserComputed.color : "CanvasText",
    colorScheme: nativeBrowserColorScheme,
  };
  let artUrl = null;
  const artVariantUrls = new Map();

  const detectShellMode = () => {
    if (configuredMode === "dark" || configuredMode === "light") return configuredMode;
    const marker = `${root.getAttribute("data-theme") || ""} ${root.getAttribute("data-appearance") || ""} ${root.className || ""}`.toLowerCase();
    if (/\b(dark|theme-dark|appearance-dark)\b/.test(marker)) return "dark";
    if (/\b(light|theme-light|appearance-light)\b/.test(marker)) return "light";
    return mediaQuery.matches ? "dark" : "light";
  };

  const buildArtUrl = (data) => {
    if (typeof data !== "string" || data.length > 20 * 1024 * 1024) return null;
    const match = /^data:(image\/(?:png|jpeg|webp|gif));base64,([a-z0-9+/=\s]+)$/i.exec(data);
    if (!match) return null;
    try {
      const binary = atob(match[2].replace(/\s/g, ""));
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
      return URL.createObjectURL(new Blob([bytes], { type: match[1] }));
    } catch {
      return null;
    }
  };
  if (THEME.capabilities?.userArt) {
    artUrl = buildArtUrl(THEME.artDataUrl);
    if (THEME.artVariantDataUrls && typeof THEME.artVariantDataUrls === "object" &&
        !Array.isArray(THEME.artVariantDataUrls)) {
      for (const [name, data] of Object.entries(THEME.artVariantDataUrls)) {
        if (!/^[a-z][a-z0-9-]{0,31}$/.test(name)) continue;
        const url = buildArtUrl(data);
        if (url) artVariantUrls.set(name, url);
      }
    }
  }

  let active = true;
  let paused = true;
  let scheduled = null;
  let observer;

  const applyTokens = () => {
    const shellMode = detectShellMode();
    const palette = shellMode === "dark" && THEME.darkTokens
      ? { ...THEME.tokens, ...THEME.darkTokens }
      : THEME.tokens;
    for (const [key, variable] of Object.entries(TOKEN_MAP)) root.style.setProperty(variable, palette[key]);
    root.style.setProperty("--ct-color-scheme", shellMode);
    root.style.setProperty("--ct-native-browser-background", nativeBrowserPaint.backgroundColor);
    root.style.setProperty("--ct-native-browser-color", nativeBrowserPaint.color);
    root.style.setProperty("--ct-native-browser-color-scheme", nativeBrowserPaint.colorScheme);
    if (artUrl) root.style.setProperty("--ct-user-art", `url("${artUrl}")`);
    else root.style.removeProperty("--ct-user-art");
    for (const [name, url] of artVariantUrls) {
      root.style.setProperty(`--ct-user-art-${name}`, `url("${url}")`);
    }
    const selectedArt = composition?.asset && composition.asset !== "primary"
      ? artVariantUrls.get(composition.asset)
      : artUrl;
    if (selectedArt || artUrl) {
      root.style.setProperty("--ct-workbench-art", `url("${selectedArt || artUrl}")`);
    } else {
      root.style.removeProperty("--ct-workbench-art");
    }
    // Composition defaults live in the generated stylesheet, before pack CSS.
    // Removing legacy inline values lets responsive pack media queries win.
    for (const variable of COMPOSITION_VARIABLES) root.style.removeProperty(variable);
    root.setAttribute(MODE_ATTR, shellMode);
  };

  const detectScope = () => {
    if (document.querySelector(SELECTOR_CONTRACT.scopes?.settings || "[data-settings-panel-slug]")) {
      return "settings";
    }
    if (document.querySelector(SELECTOR_CONTRACT.scopes?.thread || "[data-message-author-role]")) {
      return "thread";
    }
    return "home";
  };

  const mapSemanticParts = () => {
    for (const element of document.querySelectorAll(`[${PART_OWNER_ATTR}="1"]`)) {
      element.removeAttribute(PART_ATTR);
      element.removeAttribute(PART_OWNER_ATTR);
      element.removeAttribute("data-ct-part-tier");
    }
    const assignments = new Map();
    const tiersUsed = {};
    const counts = {};
    const add = (element, part, tier) => {
      if (!assignments.has(element)) assignments.set(element, new Set());
      assignments.get(element).add(part);
      tiersUsed[part] = tier;
    };
    for (const [part, definition] of Object.entries(SELECTOR_CONTRACT.parts)) {
      let resolved = [];
      let resolvedTier = null;
      for (const tier of ["L1", "L2"]) {
        const unique = new Set();
        for (const selector of definition.tiers?.[tier] || []) {
          try {
            for (const element of document.querySelectorAll(selector)) unique.add(element);
          } catch {}
        }
        if (unique.size) {
          resolved = [...unique];
          resolvedTier = tier;
          break;
        }
      }
      if (definition.cardinality === "one") resolved = resolved.slice(0, 1);
      else resolved = resolved.slice(0, Number(definition.maximum) || 16);
      for (const element of resolved) add(element, part, resolvedTier);
      counts[part] = resolved.length;
    }
    for (const [element, parts] of assignments) {
      element.setAttribute(PART_ATTR, [...parts].sort().join(" "));
      element.setAttribute(PART_OWNER_ATTR, "1");
      element.setAttribute("data-ct-part-tier", [...parts]
        .map((part) => `${part}:${tiersUsed[part] || "unknown"}`).join(" "));
    }
    root.setAttribute(CONTRACT_ATTR, String(SELECTOR_CONTRACT.schemaVersion));
    root.setAttribute(SCOPE_ATTR, detectScope());
    return { counts, tiersUsed, scope: root.getAttribute(SCOPE_ATTR) };
  };

  const ensure = () => {
    if (!active || window[DISABLED_KEY]) return;
    root.classList.add("codex-theme", "codex-theme-active");
    root.classList.toggle("codex-theme-has-art", Boolean(artUrl || artVariantUrls.size));
    root.setAttribute(THEME_ATTR, THEME.id);
    root.setAttribute(COMPOSITION_ATTR, composition?.mode || "none");
    root.setAttribute(NARROW_ATTR, composition?.narrowMode || "hide-art");
    applyTokens();
    const semanticMap = mapSemanticParts();
    let style = document.getElementById(STYLE_ID);
    if (!style) {
      style = document.createElement("style");
      style.id = STYLE_ID;
      document.head.appendChild(style);
    }
    if (style.textContent !== cssText) style.textContent = cssText;
    style.dataset.codexThemeVersion = String(version ?? "1");
    style.dataset.codexThemeId = THEME.id;
    if (window[STATE_KEY]) window[STATE_KEY].semanticMap = semanticMap;
  };

  const scheduleEnsure = () => {
    if (!active || paused || scheduled) return;
    scheduled = setTimeout(() => {
      scheduled = null;
      ensure();
    }, 80);
  };

  observer = new MutationObserver(scheduleEnsure);
  const mediaHandler = scheduleEnsure;

  const pause = () => {
    if (paused) return;
    paused = true;
    observer.disconnect();
    try { mediaQuery.removeEventListener("change", mediaHandler); } catch {}
  };

  const resume = () => {
    if (!active || !paused) return;
    paused = false;
    observer.observe(root, {
      attributes: true,
      attributeFilter: ["class", "data-theme", "data-appearance", "data-color-mode"],
    });
    observer.observe(document.head, { childList: true });
    observer.observe(document.body, { childList: true, subtree: true });
    try { mediaQuery.addEventListener("change", mediaHandler); } catch {}
  };

  const cleanup = () => {
    if (!active) return true;
    window[DISABLED_KEY] = true;
    active = false;
    pause();
    if (scheduled) clearTimeout(scheduled);
    scheduled = null;
    document.getElementById(STYLE_ID)?.remove();
    root.classList.remove("codex-theme", "codex-theme-active", "codex-theme-has-art");
    root.removeAttribute(THEME_ATTR);
    root.removeAttribute(MODE_ATTR);
    root.removeAttribute(PART_ATTR);
    root.removeAttribute(SCOPE_ATTR);
    root.removeAttribute(CONTRACT_ATTR);
    root.removeAttribute(COMPOSITION_ATTR);
    root.removeAttribute(NARROW_ATTR);
    for (const element of document.querySelectorAll(`[${PART_OWNER_ATTR}="1"]`)) {
      element.removeAttribute(PART_ATTR);
      element.removeAttribute(PART_OWNER_ATTR);
      element.removeAttribute("data-ct-part-tier");
    }
    for (const variable of Object.values(TOKEN_MAP)) root.style.removeProperty(variable);
    root.style.removeProperty("--ct-color-scheme");
    root.style.removeProperty("--ct-native-browser-background");
    root.style.removeProperty("--ct-native-browser-color");
    root.style.removeProperty("--ct-native-browser-color-scheme");
    root.style.removeProperty("--ct-user-art");
    root.style.removeProperty("--ct-workbench-art");
    for (const variable of COMPOSITION_VARIABLES) root.style.removeProperty(variable);
    for (const name of artVariantUrls.keys()) root.style.removeProperty(`--ct-user-art-${name}`);
    if (artUrl) URL.revokeObjectURL(artUrl);
    for (const url of artVariantUrls.values()) URL.revokeObjectURL(url);
    artVariantUrls.clear();
    if (window[STATE_KEY]?.cleanup === cleanup) delete window[STATE_KEY];
    return true;
  };

  window[STATE_KEY] = {
    themeId: THEME.id,
    version: String(version ?? "1"),
    styleRevision,
    payloadRevision,
    ensure,
    pause,
    resume,
    cleanup,
    observer,
    detectShellMode,
    nativeBrowserPaint,
    compositionMode: composition?.mode || "none",
    selectorContractVersion: SELECTOR_CONTRACT.schemaVersion,
    semanticMap: null,
  };
  ensure();
  resume();
  return {
    installed: true,
    themeId: THEME.id,
    version: String(version ?? "1"),
    styleRevision,
    payloadRevision,
    mode: detectShellMode(),
  };
})(
  __CODEX_THEME_CSS_JSON__,
  __CODEX_THEME_CONFIG_JSON__,
  __CODEX_THEME_SELECTORS_JSON__,
  __CODEX_THEME_VERSION_JSON__,
  __CODEX_THEME_STYLE_REVISION_JSON__,
  __CODEX_THEME_PAYLOAD_REVISION_JSON__
)
