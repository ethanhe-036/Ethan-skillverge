((cssText, themeConfig, version) => {
  "use strict";

  const STATE_KEY = "__CODEX_THEME_STATE__";
  const DISABLED_KEY = "__CODEX_THEME_DISABLED__";
  const STYLE_ID = "codex-theme-style";
  const THEME_ATTR = "data-codex-theme";
  const MODE_ATTR = "data-codex-theme-mode";
  const THEME = themeConfig && typeof themeConfig === "object" ? themeConfig : {};
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
  const REQUIRED = Object.keys(TOKEN_MAP);

  const fail = (reason) => ({ installed: false, reason });
  if (!document.documentElement || !document.head || !document.body) return fail("document-not-ready");
  if (!document.querySelector("main, aside, [role='main'], textarea, [contenteditable='true']")) {
    return fail("unsupported-renderer: stable anchor missing");
  }
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(String(THEME.id || ""))) return fail("invalid-theme-id");
  if (typeof cssText !== "string" || !cssText.trim()) return fail("missing-css");
  if (!THEME.tokens || REQUIRED.some((key) => typeof THEME.tokens[key] !== "string" || !THEME.tokens[key])) {
    return fail("invalid-theme-tokens");
  }

  const previous = window[STATE_KEY];
  if (previous?.cleanup) previous.cleanup();
  window[DISABLED_KEY] = false;

  const root = document.documentElement;
  const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
  const configuredMode = THEME.capabilities?.mode || "light";
  let artUrl = null;

  const detectShellMode = () => {
    if (configuredMode === "dark" || configuredMode === "light") return configuredMode;
    const marker = `${root.getAttribute("data-theme") || ""} ${root.getAttribute("data-appearance") || ""} ${root.className || ""}`.toLowerCase();
    if (/\b(dark|theme-dark|appearance-dark)\b/.test(marker)) return "dark";
    if (/\b(light|theme-light|appearance-light)\b/.test(marker)) return "light";
    return mediaQuery.matches ? "dark" : "light";
  };

  const buildArtUrl = () => {
    const data = THEME.capabilities?.userArt ? THEME.artDataUrl : null;
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
  artUrl = buildArtUrl();

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
    if (artUrl) root.style.setProperty("--ct-user-art", `url("${artUrl}")`);
    else root.style.removeProperty("--ct-user-art");
    root.setAttribute(MODE_ATTR, shellMode);
  };

  const ensure = () => {
    if (!active || window[DISABLED_KEY]) return;
    root.classList.add("codex-theme", "codex-theme-active");
    root.classList.toggle("codex-theme-has-art", Boolean(artUrl));
    root.setAttribute(THEME_ATTR, THEME.id);
    applyTokens();
    let style = document.getElementById(STYLE_ID);
    if (!style) {
      style = document.createElement("style");
      style.id = STYLE_ID;
      document.head.appendChild(style);
    }
    if (style.textContent !== cssText) style.textContent = cssText;
    style.dataset.codexThemeVersion = String(version ?? "1");
    style.dataset.codexThemeId = THEME.id;
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
    for (const variable of Object.values(TOKEN_MAP)) root.style.removeProperty(variable);
    root.style.removeProperty("--ct-color-scheme");
    root.style.removeProperty("--ct-user-art");
    if (artUrl) URL.revokeObjectURL(artUrl);
    if (window[STATE_KEY]?.cleanup === cleanup) delete window[STATE_KEY];
    return true;
  };

  window[STATE_KEY] = {
    themeId: THEME.id,
    version: String(version ?? "1"),
    ensure,
    pause,
    resume,
    cleanup,
    observer,
    detectShellMode,
  };
  ensure();
  resume();
  return { installed: true, themeId: THEME.id, version: String(version ?? "1"), mode: detectShellMode() };
})(__CODEX_THEME_CSS_JSON__, __CODEX_THEME_CONFIG_JSON__, __CODEX_THEME_VERSION_JSON__)
