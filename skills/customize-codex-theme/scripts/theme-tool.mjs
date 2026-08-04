import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { validateRuntimeContracts } from "./contract-tool.mjs";
import {
  AUTHORING_SCHEMA_VERSION,
  COMPOSITION_COMPILER_CONTRACT_VERSION,
  compileThemeAuthoring,
  migrateThemeDocument,
} from "./composition-compiler.mjs";
import { readImageMetadata } from "./image-metadata.mjs";
import {
  auditCatalogRegistry,
  createDetachedThemeSignature,
  evaluateCompatibility,
  loadGovernance,
  validateRegistryDeclaration,
  verifyDetachedThemeSignature,
} from "./theme-governance.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const COLOR_PATTERN = /^#[0-9a-f]{6}$/i;
const RADIUS_PATTERN = /^\d+(?:\.\d+)?(?:px|rem|em)$/i;
const FONT_PATTERN = /^[a-z0-9\s,'"_-]+$/i;
const WORKBENCH_POLICY_VERSION = 8;
const ART_VARIANT_PATTERN = /^[a-z][a-z0-9-]{0,31}$/;
const SUPPORTED_ART_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp"]);
const MAX_ART_VARIANTS = 4;
const COMPOSITION_MODES = new Set(["continuous", "portrait-zone"]);
const NARROW_MODES = new Set(["retain", "hide-art"]);
const PALETTE_STRATEGIES = new Set(["narrative", "restrained", "expressive"]);
const MATERIAL_FAMILY_PATTERN = /^[a-z][a-z0-9-]{1,39}$/;
const MATERIAL_ROLES = ["canvas", "navigation", "controls", "reading", "transient"];
const LIGHTING_DIRECTIONS = new Set(["top-left", "top", "top-right", "diffuse"]);
const DESIGN_FOCUS_AREAS = new Set([
  "workbench", "sidebar", "main", "header", "right-panel", "settings",
  "composer", "dialog", "menu", "terminal", "browser", "artwork",
  "active-task", "selected-navigation", "code", "diff", "approval",
]);
const VIEWPORT_INTENTS = new Set([
  "preserve-hierarchy", "rebalance-focus", "reduce-decoration",
  "protect-reading", "preserve-focal",
]);
const ART_CROP_BEHAVIORS = new Set([
  "preserve-focal", "rebalance-focal",
  "reduce-art-before-readability-loss", "hide-art",
]);
const REQUIRED_USABILITY = {
  typography: "native",
  geometry: "native",
  criticalSurfaces: "opaque",
  motion: "state-only",
};
const WORKBENCH_STRUCTURAL_PROPERTIES = new Set([
  "align-content", "align-items", "align-self", "animation", "animation-delay",
  "animation-direction", "animation-duration", "animation-fill-mode", "animation-iteration-count",
  "animation-name", "animation-play-state", "animation-timing-function", "aspect-ratio",
  "block-size", "border", "border-block", "border-block-end", "border-block-end-style",
  "border-block-end-width", "border-block-start", "border-block-start-style",
  "border-block-start-width", "border-block-style", "border-block-width", "border-bottom",
  "border-bottom-style", "border-bottom-width", "border-inline", "border-inline-end",
  "border-inline-end-style", "border-inline-end-width", "border-inline-start",
  "border-inline-start-style", "border-inline-start-width", "border-inline-style",
  "border-inline-width", "border-left", "border-left-style", "border-left-width", "border-right",
  "border-right-style", "border-right-width", "border-style", "border-top", "border-top-style",
  "border-top-width", "border-width", "bottom", "clear", "column-count", "column-gap", "columns", "contain",
  "content-visibility", "display", "flex", "flex-basis", "flex-direction", "flex-flow",
  "flex-grow", "flex-shrink", "flex-wrap", "float", "font-family", "font-size", "gap",
  "grid", "grid-area", "grid-auto-columns", "grid-auto-flow", "grid-auto-rows", "grid-column",
  "grid-row", "grid-template", "grid-template-areas", "grid-template-columns", "grid-template-rows",
  "height", "inline-size", "inset", "inset-block", "inset-inline", "justify-content",
  "justify-items", "justify-self", "left", "letter-spacing", "line-height", "margin",
  "margin-block", "margin-bottom", "margin-inline", "margin-left", "margin-right", "margin-top",
  "max-block-size", "max-height", "max-inline-size", "max-width", "min-block-size", "min-height",
  "min-inline-size", "min-width", "object-position", "order", "overflow", "overflow-x", "overflow-y",
  "padding", "padding-block", "padding-bottom", "padding-inline", "padding-left", "padding-right",
  "padding-top", "pointer-events", "position", "resize", "right", "rotate", "scale", "table-layout",
  "text-overflow", "top", "transform", "transform-origin", "translate", "visibility", "white-space",
  "width", "word-break", "writing-mode", "z-index",
]);
const DECORATION_LAYOUT_PROPERTIES = new Set([
  "border", "border-block", "border-block-end", "border-block-end-style", "border-block-end-width",
  "border-block-start", "border-block-start-style", "border-block-start-width", "border-block-style",
  "border-block-width", "border-bottom", "border-bottom-style", "border-bottom-width", "border-inline",
  "border-inline-end", "border-inline-end-style", "border-inline-end-width", "border-inline-start",
  "border-inline-start-style", "border-inline-start-width", "border-inline-style", "border-inline-width",
  "border-left", "border-left-style", "border-left-width", "border-right", "border-right-style",
  "border-right-width", "border-style", "border-top", "border-top-style", "border-top-width",
  "border-width", "bottom", "height", "inset", "left", "max-height", "max-width", "min-height", "min-width",
  "opacity", "pointer-events", "position", "right", "rotate", "scale", "top", "transform",
  "transform-origin", "translate", "width", "z-index",
]);
const NATIVE_CONTROL_PAINT_PROPERTIES = new Set([
  "background", "background-color", "border-color", "border-radius", "box-shadow", "color",
  "outline", "outline-color", "text-shadow",
]);
const THEME_PACK_NATIVE_SELECTOR_PATTERN =
  /(?:main\.main-surface|aside\.app-shell-left-panel|data-app-|data-browser-|data-settings-|bg-token-|\bwebview\b|scrollbar-stable|role\s*=|composer-surface-chrome|loading-shimmer)/i;
const THEME_PACK_BROWSER_SELECTOR_PATTERN =
  /\[\s*data-ct-part\s*~=\s*["']?browser["']?\s*\]/i;
const THEME_PACK_HEADER_SELECTOR_PATTERN =
  /\[\s*data-ct-part\s*~=\s*["']?header["']?\s*\]/i;
const THEME_PACK_SETTINGS_SELECTOR_PATTERN =
  /\[\s*data-ct-part\s*~=\s*["']?settings["']?\s*\]/i;
const RIGHT_PANEL_TAB_CLOSE_IDLE_SELECTOR =
  'html.codex-theme [data-app-shell-focus-area="right-panel"] ' +
  '[data-app-shell-tab-strip-controller="right"] [data-app-shell-tab-close-button]';
const APP_HEADER_TRANSPARENCY_SELECTOR =
  'html.codex-theme header[data-app-shell-application-menu-bar="true"]';
const SEMANTIC_HEADER_MATERIAL_SELECTOR =
  'html.codex-theme [data-ct-part~="header"]';
const HEADER_MATERIAL_TINT_BOUNDS = new Map([
  ["--ct-header-material-top-tint", [0, 70]],
  ["--ct-header-material-mid-tint", [0, 85]],
  ["--ct-header-material-bottom-tint", [0, 96]],
  ["--ct-header-material-control-tint", [0, 40]],
]);
const BROWSER_GUEST_PROTECTED_PROPERTIES = new Set([
  "display", "height", "inset", "left", "max-height", "max-width", "min-height", "min-width",
  "opacity", "overflow", "overflow-x", "overflow-y", "pointer-events", "position", "right",
  "transform", "visibility", "width", "z-index",
]);
const REQUIRED_TOKENS = [
  "canvas", "surface", "surfaceRaised", "text", "muted", "accent", "accentText",
  "border", "focus", "success", "warning", "danger", "info", "codeSurface",
  "terminalSurface", "terminalText", "diffAddSurface", "diffRemoveSurface", "approvalSurface",
  "radiusSmall", "radiusMedium", "radiusLarge", "shadowLow", "shadowHigh",
  "bodyFont", "displayFont",
];
const COLOR_TOKENS = new Set(REQUIRED_TOKENS.slice(0, 19));
const REQUIRED_NATIVE_PAINT_BRIDGE = new Map([
  ["--color-background-surface", "var(--ct-surface) !important"],
  ["--color-background-elevated-primary", "var(--ct-surface-raised) !important"],
  ["--color-background-elevated-secondary", "var(--ct-surface-raised) !important"],
  ["--color-background-panel", "var(--ct-surface) !important"],
  ["--color-background-control", "var(--ct-surface-raised) !important"],
  ["--color-text-foreground", "var(--ct-text) !important"],
  ["--color-icon-primary", "var(--ct-text) !important"],
  ["--color-border-heavy", "var(--ct-border) !important"],
  ["--color-token-foreground", "var(--ct-text) !important"],
  ["--color-token-text-primary", "var(--ct-text) !important"],
  ["--color-token-text-secondary", "var(--ct-muted) !important"],
  ["--color-token-text-tertiary", "var(--ct-muted) !important"],
  ["--color-token-conversation-body", "var(--ct-muted) !important"],
  ["--color-token-main-surface-primary", "var(--ct-surface) !important"],
  ["--color-token-input-background", "var(--ct-surface-raised) !important"],
  ["--color-token-input-foreground", "var(--ct-text) !important"],
  ["--color-token-icon-foreground", "var(--ct-text) !important"],
  ["--vscode-foreground", "var(--ct-text) !important"],
]);

function parseArgs(argv) {
  const [command = "help", ...rest] = argv;
  const options = {};
  for (let index = 0; index < rest.length; index += 1) {
    const key = rest[index];
    if (!key.startsWith("--")) throw new Error(`Unexpected argument: ${key}`);
    const name = key.slice(2);
    const value = rest[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`Missing value for ${key}`);
    options[name] = value;
    index += 1;
  }
  return { command, options };
}

function required(options, name) {
  const value = options[name];
  if (!value) throw new Error(`Missing --${name}`);
  return path.resolve(value);
}

function assertObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
}

function assertDesignText(value, label, minimumLength, maximumLength) {
  if (typeof value !== "string" || value.trim().length < minimumLength ||
      value.trim().length > maximumLength || /[\u0000-\u001f]/.test(value)) {
    throw new Error(
      `${label} must be a ${minimumLength}-${maximumLength} character single-line string`,
    );
  }
  return value.trim();
}

function assertUniqueStringList(
  value,
  label,
  { minimumItems, maximumItems, maximumLength, allowed = null },
) {
  if (!Array.isArray(value) || value.length < minimumItems || value.length > maximumItems) {
    throw new Error(`${label} must contain ${minimumItems}-${maximumItems} strings`);
  }
  const normalized = value.map((item, index) =>
    assertDesignText(item, `${label}[${index}]`, 1, maximumLength));
  const unique = new Set(normalized.map((item) => item.toLowerCase()));
  if (unique.size !== normalized.length) throw new Error(`${label} must not contain duplicates`);
  if (allowed) {
    for (const item of normalized) {
      if (!allowed.has(item)) throw new Error(`${label} contains an unsupported value: ${item}`);
    }
  }
  return normalized;
}

function assertPercentage(value, label, { minimum = 0, maximum = 100 } = {}) {
  if (typeof value !== "number" || !Number.isFinite(value) ||
      value < minimum || value > maximum) {
    throw new Error(`${label} must be a number from ${minimum} to ${maximum}`);
  }
  return value;
}

function assertId(value, label = "theme id") {
  if (typeof value !== "string" || !ID_PATTERN.test(value)) {
    throw new Error(`${label} must match ${ID_PATTERN}`);
  }
  return value;
}

function contrastRatio(first, second) {
  const luminance = (hex) => {
    const channels = hex.slice(1).match(/../g).map((part) => Number.parseInt(part, 16) / 255)
      .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  };
  const a = luminance(first);
  const b = luminance(second);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

function stripCssComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "");
}

function findUnquoted(source, needle, start = 0) {
  let quote = null;
  for (let index = start; index < source.length; index += 1) {
    const character = source[index];
    if (quote) {
      if (character === "\\") index += 1;
      else if (character === quote) quote = null;
      continue;
    }
    if (character === '"' || character === "'") quote = character;
    else if (character === needle) return index;
  }
  return -1;
}

function findClosingBrace(source, opening) {
  let depth = 1;
  let quote = null;
  for (let index = opening + 1; index < source.length; index += 1) {
    const character = source[index];
    if (quote) {
      if (character === "\\") index += 1;
      else if (character === quote) quote = null;
      continue;
    }
    if (character === '"' || character === "'") quote = character;
    else if (character === "{") depth += 1;
    else if (character === "}" && --depth === 0) return index;
  }
  throw new Error("Theme CSS has an unclosed block");
}

function splitCssTopLevel(source, delimiter) {
  const parts = [];
  let start = 0;
  let quote = null;
  let parentheses = 0;
  let brackets = 0;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (quote) {
      if (character === "\\") index += 1;
      else if (character === quote) quote = null;
      continue;
    }
    if (character === '"' || character === "'") quote = character;
    else if (character === "(") parentheses += 1;
    else if (character === ")") parentheses = Math.max(0, parentheses - 1);
    else if (character === "[") brackets += 1;
    else if (character === "]") brackets = Math.max(0, brackets - 1);
    else if (character === delimiter && parentheses === 0 && brackets === 0) {
      parts.push(source.slice(start, index));
      start = index + 1;
    }
  }
  parts.push(source.slice(start));
  return parts;
}

function parseDeclarations(body) {
  const declarations = [];
  for (const item of splitCssTopLevel(body, ";")) {
    const separator = item.indexOf(":");
    if (separator < 0) continue;
    const property = item.slice(0, separator).trim().toLowerCase();
    const value = item.slice(separator + 1).trim();
    if (/^(?:--[a-z]|-[a-z]|[a-z])[a-z0-9-]*$/i.test(property) && value) {
      declarations.push({ property, value });
    }
  }
  return declarations;
}

function collectCssRules(source, atRules = []) {
  const rules = [];
  let cursor = 0;
  while (cursor < source.length) {
    const opening = findUnquoted(source, "{", cursor);
    if (opening < 0) break;
    const prelude = source.slice(cursor, opening).trim().replace(/^;+/, "").trim();
    const closing = findClosingBrace(source, opening);
    const body = source.slice(opening + 1, closing);
    if (/^@(media|supports|container|layer)\b/i.test(prelude)) {
      rules.push(...collectCssRules(body, [...atRules, prelude]));
    } else if (prelude && !/^@(?:keyframes|-webkit-keyframes)\b/i.test(prelude)) {
      rules.push({ selector: prelude, declarations: parseDeclarations(body), atRules });
    }
    cursor = closing + 1;
  }
  return rules;
}

function findClosingParenthesis(source, opening) {
  let depth = 1;
  let quote = null;
  for (let index = opening + 1; index < source.length; index += 1) {
    const character = source[index];
    if (quote) {
      if (character === "\\") index += 1;
      else if (character === quote) quote = null;
      continue;
    }
    if (character === '"' || character === "'") quote = character;
    else if (character === "(") depth += 1;
    else if (character === ")" && --depth === 0) return index;
  }
  return -1;
}

function startsWithUnscopedNativeButton(selector) {
  const value = selector.trim();
  if (/^button(?:$|[\s>+~,:])/i.test(value)) return true;
  if (/^\[role\s*=\s*["']?button["']?\]/i.test(value)) return true;
  if (/^(?:aside|footer|form|header|main|nav|section)(?:\s+|>\s*)(?:button(?:$|[\s>+~,:])|\[role\s*=\s*["']?button["']?\])/i.test(value)) {
    return true;
  }

  const functional = value.match(/^:(?:where|is)\s*\(/i);
  if (!functional) return false;
  const opening = value.indexOf("(");
  const closing = findClosingParenthesis(value, opening);
  if (closing < 0) return false;
  return splitCssTopLevel(value.slice(opening + 1, closing), ",")
    .some((part) => startsWithUnscopedNativeButton(part));
}

function hasUnscopedNativeButtonSelector(rule) {
  return splitCssTopLevel(rule.selector, ",").some((selector) => {
    const withoutThemeRoot = selector.trim().replace(
      /^html\.codex-theme(?:\[[^\]]+\])*\s+/i,
      "",
    );
    return startsWithUnscopedNativeButton(withoutThemeRoot);
  });
}

function isAccessibilityControlOverride(rule) {
  return rule.atRules.some((atRule) =>
    /^@media\s*\((?:forced-colors\s*:|prefers-contrast\s*:)/i.test(atRule));
}

function isSettingsAccessibilityFallback(rule) {
  return rule.atRules.some((atRule) =>
    /^@media\b.*(?:forced-colors\s*:\s*active|prefers-contrast\s*:\s*more|prefers-reduced-transparency\s*:\s*reduce)/i
      .test(atRule));
}

function hasUnstableComposerOwnership(rule) {
  return /form\s*:has\s*\(/i.test(rule.selector);
}

function hasOnlyThemeOwnedDecorationSelectors(rule) {
  const selectors = splitCssTopLevel(rule.selector, ",").map((value) => value.trim()).filter(Boolean);
  return Boolean(selectors.length && selectors.every((value) =>
    /::(?:before|after)\s*$/i.test(value) ||
    /(?:#codex-theme-chrome|\[data-codex-theme-decoration\])\s*$/i.test(value)));
}

function isSafeDecorationRule(rule) {
  if (!hasOnlyThemeOwnedDecorationSelectors(rule)) return false;
  return rule.declarations.some(({ property, value }) =>
    property === "pointer-events" && /^none(?:\s*!important)?$/i.test(value));
}

function isRightPanelTabCloseIdleOpacityRule(id, rule, options) {
  if (id !== "base-css" || options.themePack || rule.atRules.length !== 0) return false;
  const selectors = splitCssTopLevel(rule.selector, ",")
    .map((value) => value.trim().replace(/\s+/g, " "))
    .filter(Boolean);
  return selectors.length === 1 &&
    selectors[0] === RIGHT_PANEL_TAB_CLOSE_IDLE_SELECTOR &&
    rule.declarations.length === 1 &&
    rule.declarations[0].property === "opacity" &&
    /^1\s*!important$/i.test(rule.declarations[0].value);
}

function validateThemeCssPolicy(id, css, options = {}) {
  const violations = [];
  for (const rule of collectCssRules(stripCssComments(css))) {
    const decoration = isSafeDecorationRule(rule);
    const themeOwnedDecoration = hasOnlyThemeOwnedDecorationSelectors(rule);
    const rightPanelTabCloseIdleOpacity = isRightPanelTabCloseIdleOpacityRule(id, rule, options);
    const unscopedNativeButton = hasUnscopedNativeButtonSelector(rule) &&
      !isAccessibilityControlOverride(rule);
    const settingsAccessibilityFallback = isSettingsAccessibilityFallback(rule);
    const themePackSelectorViolations = [];
    let themePackRootOnly = false;
    if (options.themePack) {
      const escapedId = id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const themeAnchor = new RegExp(
        `html\\.codex-theme\\[data-codex-theme=(?:["']${escapedId}["'])\\]`,
        "i",
      );
      const themeRootSelector = `html.codex-theme[data-codex-theme="${id}"]`;
      const themePackSelectors = splitCssTopLevel(rule.selector, ",")
        .map((value) => value.trim().replace(/\s+/g, " "))
        .filter(Boolean);
      themePackRootOnly = themePackSelectors.length === 1 &&
        themePackSelectors[0] === themeRootSelector;
      for (const selector of themePackSelectors) {
        if (!themeAnchor.test(selector)) {
          themePackSelectorViolations.push({ selector, property: "selector", reason: "unscoped-theme-pack" });
        }
        if (THEME_PACK_HEADER_SELECTOR_PATTERN.test(selector)) {
          themePackSelectorViolations.push({
            selector,
            property: "selector",
            reason: "semantic-header-runtime-owned",
          });
        }
        if (THEME_PACK_BROWSER_SELECTOR_PATTERN.test(selector)) {
          themePackSelectorViolations.push({
            selector,
            property: "selector",
            reason: "browser-guest-runtime-owned",
          });
        }
        if (THEME_PACK_NATIVE_SELECTOR_PATTERN.test(selector)) {
          themePackSelectorViolations.push({
            selector,
            property: "selector",
            reason: "native-topology-outside-runtime",
          });
        }
      }
    }
    if (hasUnstableComposerOwnership(rule)) {
      violations.push({ selector: rule.selector, property: "selector", reason: "unstable-composer-ownership" });
    }
    for (const { property, value } of rule.declarations) {
      const allowedDecorationLayout = decoration && DECORATION_LAYOUT_PROPERTIES.has(property);
      const disablesDecorationMotion = themeOwnedDecoration &&
        ["animation", "transition", "transform"].includes(property) &&
        /^none(?:\s*!important)?$/i.test(value);
      if (WORKBENCH_STRUCTURAL_PROPERTIES.has(property) && !allowedDecorationLayout && !disablesDecorationMotion) {
        violations.push({ selector: rule.selector, property, reason: "native-workbench-geometry" });
      }
      if (property === "opacity" && !decoration && !rightPanelTabCloseIdleOpacity) {
        violations.push({ selector: rule.selector, property, reason: "computed-contrast" });
      }
      if (property === "transition" && /(?:^|[,\s])(all|transform|translate|scale|rotate|width|height|margin|padding|font-size|line-height)(?:\s|,|$)/i.test(value)) {
        violations.push({ selector: rule.selector, property, reason: "layout-motion" });
      }
      if (unscopedNativeButton && NATIVE_CONTROL_PAINT_PROPERTIES.has(property)) {
        violations.push({ selector: rule.selector, property, reason: "native-control-state" });
      }
      if (options.themePack && /--ct-(?:user|workbench)-art\b/i.test(value)) {
        violations.push({ selector: rule.selector, property, reason: "theme-pack-raster-ownership" });
      }
      if (options.themePack && property === "--ct-settings-tint") {
        violations.push({
          selector: rule.selector,
          property,
          reason: "semantic-settings-material-runtime-owned",
        });
      }
      if (options.themePack && options.artworkTheme &&
          THEME_PACK_SETTINGS_SELECTOR_PATTERN.test(rule.selector) &&
          ["background", "background-color", "background-image"].includes(property) &&
          !settingsAccessibilityFallback) {
        violations.push({
          selector: rule.selector,
          property,
          reason: "semantic-settings-material-runtime-owned",
        });
      }
      if (options.themePack && property.startsWith("--ct-header-material-")) {
        if (!themePackRootOnly || /!important\s*$/i.test(value)) {
          violations.push({
            selector: rule.selector,
            property,
            reason: "semantic-header-tuning-scope",
          });
        } else if (property === "--ct-header-material-surface") {
          if (!/^var\(--ct-(?:canvas|surface|surface-raised)\)$/.test(value)) {
            violations.push({
              selector: rule.selector,
              property,
              reason: "semantic-header-tuning-value",
            });
          }
        } else if (HEADER_MATERIAL_TINT_BOUNDS.has(property)) {
          const match = /^(\d+(?:\.\d+)?)%$/.exec(value);
          const numeric = match ? Number(match[1]) : Number.NaN;
          const [minimum, maximum] = HEADER_MATERIAL_TINT_BOUNDS.get(property);
          if (!Number.isFinite(numeric) || numeric < minimum || numeric > maximum) {
            violations.push({
              selector: rule.selector,
              property,
              reason: "semantic-header-tuning-value",
            });
          }
        } else {
          violations.push({
            selector: rule.selector,
            property,
            reason: "semantic-header-tuning-property",
          });
        }
      }
    }
    violations.push(...themePackSelectorViolations);
  }
  if (violations.length) {
    const first = violations[0];
    throw new Error(`${id} workbench CSS policy rejects ${first.property} on ${first.selector} (${first.reason})`);
  }
  return { pass: true, violations };
}

function validateNativePaintBridge(css) {
  const declarations = new Map();
  const nestedDeclarations = new Map();
  const nestedSelectors = new Set([
    "html.codex-theme :where(.app-theme.electron-light, .app-theme.electron-dark)",
  ]);
  for (const rule of collectCssRules(stripCssComments(css))) {
    if (rule.atRules.length) continue;
    const selectors = splitCssTopLevel(rule.selector, ",").map((value) => value.trim());
    if (selectors.includes("html.codex-theme")) {
      for (const { property, value } of rule.declarations) declarations.set(property, value);
    }
    if (selectors.some((selector) => nestedSelectors.has(selector))) {
      for (const { property, value } of rule.declarations) nestedDeclarations.set(property, value);
    }
  }
  const missing = [];
  for (const [scope, scopedDeclarations] of [
    ["root", declarations],
    ["nested-app-theme", nestedDeclarations],
  ]) {
    for (const [property, expected] of REQUIRED_NATIVE_PAINT_BRIDGE) {
      if (scopedDeclarations.get(property) !== expected) {
        missing.push({ scope, property, expected, actual: scopedDeclarations.get(property) ?? null });
      }
    }
  }
  if (missing.length) {
    const first = missing[0];
    throw new Error(`base-css ${first.scope} native paint bridge requires ${first.property}: ${first.expected}`);
  }
  return {
    pass: true,
    requiredMappings: REQUIRED_NATIVE_PAINT_BRIDGE.size,
    scopes: ["root", "nested-app-theme"],
  };
}

function validateBrowserGuestIsolation(css) {
  const hostSelector = "html.codex-theme [data-browser-sidebar-webview]";
  const surfaceSelector = `${hostSelector} > webview`;
  const hostDeclarations = new Map();
  const surfaceDeclarations = new Map();
  const forbidden = [];
  for (const rule of collectCssRules(stripCssComments(css))) {
    const selectors = splitCssTopLevel(rule.selector, ",").map((value) => value.trim());
    const touchesBrowser = selectors.some((selector) =>
      selector.includes("[data-browser-sidebar-webview]") || /\bwebview\b/i.test(selector));
    if (touchesBrowser) {
      for (const { property } of rule.declarations) {
        if (BROWSER_GUEST_PROTECTED_PROPERTIES.has(property)) {
          forbidden.push({ selector: rule.selector, property });
        }
      }
    }
    if (selectors.includes(hostSelector)) {
      for (const { property, value } of rule.declarations) hostDeclarations.set(property, value);
    }
    if (selectors.includes(surfaceSelector)) {
      for (const { property, value } of rule.declarations) surfaceDeclarations.set(property, value);
    }
  }
  if (forbidden.length) {
    const first = forbidden[0];
    throw new Error(
      `base-css Browser guest isolation forbids ${first.property} on ${first.selector}`,
    );
  }
  const required = [
    [hostDeclarations, hostSelector, "color-scheme",
      "var(--ct-native-browser-color-scheme, normal) !important"],
    [surfaceDeclarations, surfaceSelector, "color-scheme",
      "var(--ct-native-browser-color-scheme, normal) !important"],
    [surfaceDeclarations, surfaceSelector, "color",
      "var(--ct-native-browser-color, CanvasText) !important"],
    [surfaceDeclarations, surfaceSelector, "background-color",
      "var(--ct-native-browser-background, Canvas) !important"],
  ];
  for (const [declarations, selector, property, expected] of required) {
    if (declarations.get(property) !== expected) {
      throw new Error(
        `base-css Browser guest isolation requires ${property}: ${expected} on ${selector}`,
      );
    }
  }
  return {
    pass: true,
    requiredMappings: required.length,
    protectedProperties: [...BROWSER_GUEST_PROTECTED_PROPERTIES],
  };
}

function validateSettingsContinuity(css) {
  const requiredSelectorSignals = [
    '[data-ct-part~="settings"]',
    ".main-surface",
  ];
  let structuralClear = null;
  let combinedRootTint = null;
  let defaultTint = false;
  const responsiveTints = new Set();
  let opaqueAccessibilityFallback = false;
  let forcedColorsFallback = false;
  for (const rule of collectCssRules(stripCssComments(css))) {
    const declarations = new Map(
      rule.declarations.map(({ property, value }) => [property, value]),
    );
    if (requiredSelectorSignals.every((signal) => rule.selector.includes(signal)) &&
        declarations.get("background-color") === "transparent !important" &&
        declarations.get("background-image") === "none !important") {
      structuralClear = rule.selector;
    }
    if (rule.selector.includes('[data-ct-part~="main"][data-ct-part~="settings"]') &&
        declarations.get("background-color") ===
          "color-mix(in srgb, var(--ct-surface) var(--ct-settings-tint, 52%), transparent) !important" &&
        declarations.get("background-image") === "none !important") {
      combinedRootTint = rule.selector;
    }
    if (rule.atRules.length === 0 && rule.selector === "html.codex-theme" &&
        declarations.get("--ct-settings-tint") === "52%") {
      defaultTint = true;
    }
    for (const atRule of rule.atRules) {
      if (/^@media\s*\(max-width:\s*900px\)$/i.test(atRule) &&
          declarations.get("--ct-settings-tint") === "60%") {
        responsiveTints.add(900);
      }
      if (/^@media\s*\(max-width:\s*620px\)$/i.test(atRule) &&
          declarations.get("--ct-settings-tint") === "68%") {
        responsiveTints.add(620);
      }
      if (/(?:prefers-reduced-transparency:\s*reduce|prefers-contrast:\s*more)/i.test(atRule) &&
          rule.selector.includes('[data-ct-part~="settings"]') &&
          declarations.get("background-color") === "var(--ct-surface) !important" &&
          declarations.get("background-image") === "none !important") {
        opaqueAccessibilityFallback = true;
      }
      if (/forced-colors:\s*active/i.test(atRule) &&
          rule.selector.includes('[data-ct-part~="settings"]') &&
          declarations.get("background") === "Canvas !important") {
        forcedColorsFallback = true;
      }
    }
  }
  if (structuralClear && combinedRootTint && defaultTint &&
      responsiveTints.size === 2 && opaqueAccessibilityFallback && forcedColorsFallback) {
    return {
      pass: true,
      semanticRoot: "settings",
      combinedMainSettingsTint: true,
      materialOwner: "shared-runtime",
      defaultTint: 52,
      responsiveTints: { split: 60, narrow: 68 },
      maximumNormalTint: 68,
      accessibilityFallback: "opaque",
      clearedStructuralLayers: [".main-surface"],
    };
  }
  throw new Error(
    "base-css settings continuity requires a bounded shared settings material, responsive/opaque fallbacks, and a transparent nested .main-surface",
  );
}

function validateAppHeaderTransparency(css) {
  let protectedRule = null;
  const conflicts = [];
  for (const rule of collectCssRules(stripCssComments(css))) {
    const selectors = splitCssTopLevel(rule.selector, ",").map((value) => value.trim());
    const targetsAppHeader = selectors.some((selector) =>
      selector.includes('[data-app-shell-application-menu-bar="true"]') &&
      !selector.includes(':not([data-app-shell-application-menu-bar="true"])'));
    if (!targetsAppHeader) continue;
    const declarations = new Map(
      rule.declarations.map(({ property, value }) => [property, value]),
    );
    const paintsBackground = ["background", "background-color", "background-image"]
      .some((property) => declarations.has(property));
    if (!paintsBackground) continue;
    const isProtectedRule = rule.atRules.length === 0 && selectors.length === 1 &&
      selectors[0] === APP_HEADER_TRANSPARENCY_SELECTOR &&
      !declarations.has("background") &&
      declarations.get("background-color") === "transparent !important" &&
      declarations.get("background-image") === "none !important";
    if (isProtectedRule) {
      protectedRule = rule.selector;
    } else {
      conflicts.push({ selector: rule.selector, atRules: rule.atRules });
    }
  }
  if (conflicts.length) {
    throw new Error(
      `base-css app header transparency rejects conflicting paint on ${conflicts[0].selector}`,
    );
  }
  if (protectedRule) {
    return {
      pass: true,
      selector: APP_HEADER_TRANSPARENCY_SELECTOR,
      preservesNativeGeometry: true,
      protectedProperties: ["background-color", "background-image"],
      conflictingRules: 0,
      scope: "shared-runtime",
    };
  }
  throw new Error(
    "base-css app header transparency requires the stable application-menu-bar outer surface to remain transparent",
  );
}

function validateSemanticHeaderMaterial(css) {
  let materialRule = null;
  let materialCandidate = null;
  let accessibleFallback = null;
  let forcedColorsFallback = null;
  const responsiveDefaults = new Map();
  const requiredVariables = [
    "--ct-header-material-surface",
    "--ct-header-material-top-tint",
    "--ct-header-material-mid-tint",
    "--ct-header-material-bottom-tint",
    "--ct-header-material-control-tint",
  ];
  for (const rule of collectCssRules(stripCssComments(css))) {
    const selectors = splitCssTopLevel(rule.selector, ",")
      .map((value) => value.trim().replace(/\s+/g, " "))
      .filter(Boolean);
    const declarations = new Map(
      rule.declarations.map(({ property, value }) => [property, value]),
    );
    const exactSemanticHeader = selectors.length === 1 &&
      selectors[0] === SEMANTIC_HEADER_MATERIAL_SELECTOR;
    if (exactSemanticHeader && rule.atRules.length === 0) {
      const image = declarations.get("background-image") ?? "";
      const gradientCount = image.toLowerCase().split("linear-gradient(").length - 1;
      const requiredImageVariables = requiredVariables;
      const checks = {
        transparentBackground:
          declarations.get("background-color") === "transparent !important",
        twoLayerGradient: gradientCount >= 2,
        horizontalLayer: image.includes("90deg"),
        verticalLayer: image.includes("180deg"),
        requiredVariables:
          requiredImageVariables.every((variable) => image.includes(`var(${variable}`)),
        noRepeat: declarations.get("background-repeat") === "no-repeat !important",
        transparentDivider:
          declarations.get("border-bottom-color") === "transparent !important",
        noShadow: declarations.get("box-shadow") === "none !important",
        noWebkitBlur:
          declarations.get("-webkit-backdrop-filter") === "none !important",
        noBlur: declarations.get("backdrop-filter") === "none !important",
      };
      materialCandidate = { checks, gradientCount };
      if (Object.values(checks).every(Boolean)) {
        materialRule = { selector: rule.selector, gradientCount };
      }
    }
    if (exactSemanticHeader && rule.atRules.some((atRule) =>
      /prefers-reduced-transparency\s*:\s*reduce/i.test(atRule) &&
      /prefers-contrast\s*:\s*more/i.test(atRule)) &&
        declarations.get("background-color") === "var(--ct-surface-raised) !important" &&
        declarations.get("background-image") === "none !important") {
      accessibleFallback = rule.selector;
    }
    if (exactSemanticHeader && rule.atRules.some((atRule) =>
      /forced-colors\s*:\s*active/i.test(atRule)) &&
        declarations.get("background") === "Canvas !important") {
      forcedColorsFallback = rule.selector;
    }
    if (selectors.length === 1 && selectors[0] === "html.codex-theme") {
      for (const width of [900, 620]) {
        if (!rule.atRules.some((atRule) =>
          new RegExp(`max-width\\s*:\\s*${width}px`, "i").test(atRule))) continue;
        if (requiredVariables.slice(1).every((variable) => declarations.has(variable))) {
          responsiveDefaults.set(width, true);
        }
      }
    }
  }
  if (!materialRule) {
    const failed = Object.entries(materialCandidate?.checks ?? {})
      .filter(([, pass]) => !pass)
      .map(([name]) => name)
      .join(", ");
    throw new Error(
      `base-css semantic header material requires the shared two-layer continuous-canvas recipe${
        failed ? `; failed checks: ${failed}` : ""
      }`,
    );
  }
  if (!accessibleFallback || !forcedColorsFallback) {
    throw new Error(
      "base-css semantic header material requires opaque reduced-transparency, increased-contrast, and forced-colors fallbacks",
    );
  }
  if (![900, 620].every((width) => responsiveDefaults.has(width))) {
    throw new Error(
      "base-css semantic header material requires shared split and narrow tint projections",
    );
  }
  return {
    pass: true,
    selector: SEMANTIC_HEADER_MATERIAL_SELECTOR,
    owner: "shared-runtime",
    layers: ["control-protection", "vertical-veil"],
    gradientCount: materialRule.gradientCount,
    responsiveWidths: [900, 620],
    accessibilityFallbacks: ["reduced-transparency", "increased-contrast", "forced-colors"],
    themePackPaintOwnership: "forbidden",
    themePackTuning: "bounded-root-custom-properties-only",
  };
}

function validatePalette(id, palette) {
  const requirements = [
    ["text", "canvas", 4.5], ["text", "surface", 4.5], ["text", "surfaceRaised", 4.5],
    ["muted", "canvas", 4.5], ["muted", "surface", 4.5], ["muted", "surfaceRaised", 4.5],
    ["accentText", "accent", 4.5], ["terminalText", "terminalSurface", 4.5],
    ["text", "codeSurface", 4.5],
    ["text", "diffAddSurface", 4.5], ["text", "diffRemoveSurface", 4.5],
    ["text", "approvalSurface", 4.5],
    ...["success", "warning", "danger", "info"].flatMap((foreground) =>
      ["canvas", "surface", "surfaceRaised"].map((background) => [foreground, background, 4.5])),
    ["focus", "canvas", 3], ["focus", "surface", 3], ["focus", "surfaceRaised", 3],
  ];
  const pairs = [];
  for (const [foreground, background, minimum] of requirements) {
    const ratio = contrastRatio(palette[foreground], palette[background]);
    if (ratio < minimum) {
      throw new Error(`${id} contrast ${foreground}/${background} is ${ratio.toFixed(2)}; expected ${minimum}`);
    }
    pairs.push({ foreground, background, ratio: Number(ratio.toFixed(2)), minimum });
  }
  return { pass: true, pairs };
}

function assertSafeToken(id, token, value) {
  if (COLOR_TOKENS.has(token)) {
    if (!COLOR_PATTERN.test(value)) throw new Error(`${id}.tokens.${token} must be a six-digit hex color`);
    return;
  }
  if (token.startsWith("radius")) {
    if (!RADIUS_PATTERN.test(value)) throw new Error(`${id}.tokens.${token} must be a simple CSS length`);
    return;
  }
  if (token.endsWith("Font")) {
    if (!FONT_PATTERN.test(value)) throw new Error(`${id}.tokens.${token} contains unsupported characters`);
    return;
  }
  if (/url\s*\(|var\s*\(|@|;|https?:/i.test(value) || value.length > 160) {
    throw new Error(`${id}.tokens.${token} contains unsafe CSS`);
  }
}

function assertRelativeDirectory(value, label) {
  if (typeof value !== "string" || value.length < 1 || path.isAbsolute(value)) {
    throw new Error(`${label} must be a relative directory`);
  }
  const normalized = path.normalize(value);
  if (normalized === ".." || normalized.startsWith(`..${path.sep}`)) {
    throw new Error(`${label} escapes the catalog`);
  }
  return normalized;
}

async function readJson(file) {
  let text;
  try {
    text = await fs.readFile(file, "utf8");
  } catch (error) {
    const wrapped = new Error(`Cannot read ${file}: ${error.message}`);
    wrapped.code = error.code;
    throw wrapped;
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`Invalid JSON in ${file}: ${error.message}`);
  }
}

async function containedDirectory(root, relative) {
  const resolvedRoot = await fs.realpath(root);
  const candidate = await fs.realpath(path.resolve(resolvedRoot, relative));
  const prefix = resolvedRoot.endsWith(path.sep) ? resolvedRoot : `${resolvedRoot}${path.sep}`;
  if (candidate !== resolvedRoot && !candidate.startsWith(prefix)) {
    throw new Error(`Theme path escapes the catalog: ${relative}`);
  }
  const stat = await fs.stat(candidate);
  if (!stat.isDirectory()) throw new Error(`Theme path is not a directory: ${relative}`);
  return candidate;
}

function validateComposition(theme, id, compiledAuthoring) {
  const composition = compiledAuthoring.runtimeComposition;
  if (theme.art === undefined) {
    if (composition !== null) {
      throw new Error(`${id} compiled a runtime composition without a declared art asset`);
    }
    return {
      mode: "none",
      asset: null,
      narrowMode: null,
      coordinateOwnership: compiledAuthoring.coordinateOwnership,
    };
  }
  assertObject(composition, `${id}.compiledComposition`);
  if (!COMPOSITION_MODES.has(composition.mode)) {
    throw new Error(`${id}.compiledComposition.mode must be continuous or portrait-zone`);
  }
  const availableAssets = new Set(["primary", ...Object.keys(theme.art?.variants ?? {})]);
  if (!availableAssets.has(composition.asset)) {
    throw new Error(`${id}.compiledComposition.asset must name primary or a declared art variant`);
  }
  for (const key of ["focusX", "focusY"]) {
    const value = composition[key];
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 100) {
      throw new Error(`${id}.compiledComposition.${key} must be a number from 0 to 100`);
    }
  }
  for (const key of ["workbenchScrim", "sidebarTint", "mainTint", "headerTint", "rightPanelTint"]) {
    const value = composition[key];
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 96) {
      throw new Error(`${id}.compiledComposition.${key} must be a number from 0 to 96`);
    }
  }
  if (!NARROW_MODES.has(composition.narrowMode)) {
    throw new Error(`${id}.compiledComposition.narrowMode must be retain or hide-art`);
  }
  assertObject(composition.safeArea, `${id}.compiledComposition.safeArea`);
  for (const key of ["left", "right", "top", "bottom"]) {
    const value = composition.safeArea[key];
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 80) {
      throw new Error(`${id}.compiledComposition.safeArea.${key} must be a number from 0 to 80`);
    }
  }
  if (composition.safeArea.left + composition.safeArea.right >= 95 ||
      composition.safeArea.top + composition.safeArea.bottom >= 95) {
    throw new Error(`${id}.compiledComposition.safeArea leaves no usable focal region`);
  }
  return {
    mode: composition.mode,
    asset: composition.asset,
    narrowMode: composition.narrowMode,
    coordinateOwnership: compiledAuthoring.coordinateOwnership,
  };
}

function validateAestheticProfile(theme, id, compiledAuthoring) {
  assertObject(theme.aestheticProfile, `${id}.aestheticProfile`);
  const profile = theme.aestheticProfile;
  const designThesis = assertDesignText(
    profile.designThesis,
    `${id}.aestheticProfile.designThesis`,
    20,
    240,
  );
  const moodKeywords = assertUniqueStringList(
    profile.moodKeywords,
    `${id}.aestheticProfile.moodKeywords`,
    { minimumItems: 3, maximumItems: 5, maximumLength: 40 },
  );
  const signatureMotif = assertDesignText(
    profile.signatureMotif,
    `${id}.aestheticProfile.signatureMotif`,
    5,
    120,
  );
  const antiGoals = assertUniqueStringList(
    profile.antiGoals,
    `${id}.aestheticProfile.antiGoals`,
    { minimumItems: 2, maximumItems: 5, maximumLength: 100 },
  );
  if (!PALETTE_STRATEGIES.has(profile.paletteStrategy)) {
    throw new Error(
      `${id}.aestheticProfile.paletteStrategy must be narrative, restrained, or expressive`,
    );
  }
  assertObject(profile.motifBudget, `${id}.aestheticProfile.motifBudget`);
  const dominantMinimum = compiledAuthoring.sourceSchemaVersion === 2 ? 0 : 1;
  if (!Number.isInteger(profile.motifBudget.dominantMotifs) ||
      profile.motifBudget.dominantMotifs < dominantMinimum ||
      profile.motifBudget.dominantMotifs > 1) {
    throw new Error(
      `${id}.aestheticProfile.motifBudget.dominantMotifs must be an integer ` +
      `from ${dominantMinimum} to 1`,
    );
  }
  if (!Number.isInteger(profile.motifBudget.secondaryMotifs) ||
      profile.motifBudget.secondaryMotifs < 0 ||
      profile.motifBudget.secondaryMotifs > 3) {
    throw new Error(
      `${id}.aestheticProfile.motifBudget.secondaryMotifs must be an integer from 0 to 3`,
    );
  }
  assertPercentage(
    profile.motifBudget.accentAreaPercent,
    `${id}.aestheticProfile.motifBudget.accentAreaPercent`,
    { minimum: 1, maximum: 15 },
  );
  return {
    designThesis,
    moodKeywords,
    signatureMotif,
    antiGoals,
    paletteStrategy: profile.paletteStrategy,
    motifBudget: { ...profile.motifBudget },
  };
}

function validateMaterialGrammar(theme, id) {
  assertObject(theme.materialGrammar, `${id}.materialGrammar`);
  const grammar = theme.materialGrammar;
  const families = {};
  for (const role of MATERIAL_ROLES) {
    const family = grammar[role];
    if (typeof family !== "string" || !MATERIAL_FAMILY_PATTERN.test(family)) {
      throw new Error(
        `${id}.materialGrammar.${role} must be a lowercase hyphenated material family`,
      );
    }
    families[role] = family;
  }
  const familyCount = new Set(Object.values(families)).size;
  if (familyCount > 4) {
    throw new Error(`${id}.materialGrammar may use at most four material families`);
  }
  if (!Number.isInteger(grammar.elevationLevels) ||
      grammar.elevationLevels < 1 || grammar.elevationLevels > 3) {
    throw new Error(`${id}.materialGrammar.elevationLevels must be an integer from 1 to 3`);
  }
  if (!LIGHTING_DIRECTIONS.has(grammar.lightingDirection)) {
    throw new Error(
      `${id}.materialGrammar.lightingDirection must be top-left, top, top-right, or diffuse`,
    );
  }
  return {
    families,
    familyCount,
    elevationLevels: grammar.elevationLevels,
    lightingDirection: grammar.lightingDirection,
  };
}

function validateCompositionProfile(theme, id, compiledAuthoring) {
  assertObject(theme.compositionProfile, `${id}.compositionProfile`);
  const profile = theme.compositionProfile;
  const primaryFocus = assertUniqueStringList(
    profile.primaryFocus,
    `${id}.compositionProfile.primaryFocus`,
    {
      minimumItems: 1,
      maximumItems: 4,
      maximumLength: 40,
      allowed: DESIGN_FOCUS_AREAS,
    },
  );
  const secondaryFocus = assertUniqueStringList(
    profile.secondaryFocus,
    `${id}.compositionProfile.secondaryFocus`,
    {
      minimumItems: 0,
      maximumItems: 4,
      maximumLength: 40,
      allowed: DESIGN_FOCUS_AREAS,
    },
  );
  const quietZones = assertUniqueStringList(
    profile.quietZones,
    `${id}.compositionProfile.quietZones`,
    {
      minimumItems: 1,
      maximumItems: 8,
      maximumLength: 40,
      allowed: DESIGN_FOCUS_AREAS,
    },
  );
  if (!primaryFocus.includes("composer") && !primaryFocus.includes("active-task")) {
    throw new Error(
      `${id}.compositionProfile.primaryFocus must keep composer or active-task authoritative`,
    );
  }
  const focusGroups = [
    ["primaryFocus", primaryFocus],
    ["secondaryFocus", secondaryFocus],
    ["quietZones", quietZones],
  ];
  for (let first = 0; first < focusGroups.length; first += 1) {
    for (let second = first + 1; second < focusGroups.length; second += 1) {
      const overlap = focusGroups[first][1].filter((item) => focusGroups[second][1].includes(item));
      if (overlap.length) {
        throw new Error(
          `${id}.compositionProfile ${focusGroups[first][0]} and ` +
          `${focusGroups[second][0]} overlap: ${overlap.join(", ")}`,
        );
      }
    }
  }
  assertObject(profile.viewportIntent, `${id}.compositionProfile.viewportIntent`);
  const viewportIntent = {};
  for (const viewport of ["wide", "split", "narrow"]) {
    const intent = profile.viewportIntent[viewport];
    if (!VIEWPORT_INTENTS.has(intent)) {
      throw new Error(
        `${id}.compositionProfile.viewportIntent.${viewport} has an unsupported intent`,
      );
    }
    viewportIntent[viewport] = intent;
  }

  if (!theme.art) {
    if (profile.artwork !== undefined) {
      throw new Error(`${id}.compositionProfile.artwork requires a declared art asset`);
    }
    return { primaryFocus, secondaryFocus, quietZones, viewportIntent, artwork: null };
  }

  assertObject(profile.artwork, `${id}.compositionProfile.artwork`);
  const artwork = profile.artwork;
  const narrativeAnchor = assertDesignText(
    artwork.narrativeAnchor,
    `${id}.compositionProfile.artwork.narrativeAnchor`,
    5,
    160,
  );
  assertObject(
    artwork.workspaceQuietZone,
    `${id}.compositionProfile.artwork.workspaceQuietZone`,
  );
  const workspaceQuietZone = {};
  for (const key of ["x", "y", "width", "height"]) {
    workspaceQuietZone[key] = assertPercentage(
      artwork.workspaceQuietZone[key],
      `${id}.compositionProfile.artwork.workspaceQuietZone.${key}`,
      { minimum: key === "width" || key === "height" ? 1 : 0, maximum: 100 },
    );
  }
  if (workspaceQuietZone.x + workspaceQuietZone.width > 100 ||
      workspaceQuietZone.y + workspaceQuietZone.height > 100) {
    throw new Error(`${id}.compositionProfile.artwork.workspaceQuietZone exceeds the raster`);
  }
  const focalRange = compiledAuthoring.sourceSchemaVersion === 2
    ? compiledAuthoring.layoutPlan.focalZoneRange
    : { minimum: 1, maximum: 4 };
  if (!Array.isArray(artwork.focalZones) ||
      artwork.focalZones.length < focalRange.minimum ||
      artwork.focalZones.length > focalRange.maximum) {
    throw new Error(
      `${id}.compositionProfile.artwork.focalZones must contain ` +
      `${focalRange.minimum}-${focalRange.maximum} zones for ` +
      `${compiledAuthoring.experience.artTopology}`,
    );
  }
  const focalZones = [];
  const focalNames = new Set();
  for (const [index, zone] of artwork.focalZones.entries()) {
    assertObject(zone, `${id}.compositionProfile.artwork.focalZones[${index}]`);
    if (typeof zone.name !== "string" || !MATERIAL_FAMILY_PATTERN.test(zone.name)) {
      throw new Error(
        `${id}.compositionProfile.artwork.focalZones[${index}].name must be a lowercase slug`,
      );
    }
    if (focalNames.has(zone.name)) {
      throw new Error(`${id}.compositionProfile.artwork.focalZones names must be unique`);
    }
    focalNames.add(zone.name);
    focalZones.push({
      name: zone.name,
      x: assertPercentage(
        zone.x,
        `${id}.compositionProfile.artwork.focalZones[${index}].x`,
      ),
      y: assertPercentage(
        zone.y,
        `${id}.compositionProfile.artwork.focalZones[${index}].y`,
      ),
      radius: assertPercentage(
        zone.radius,
        `${id}.compositionProfile.artwork.focalZones[${index}].radius`,
        { minimum: 2, maximum: 40 },
      ),
    });
  }
  assertObject(artwork.cropBehavior, `${id}.compositionProfile.artwork.cropBehavior`);
  const cropBehavior = {};
  for (const viewport of ["wide", "split", "narrow"]) {
    const behavior = artwork.cropBehavior[viewport];
    if (!ART_CROP_BEHAVIORS.has(behavior)) {
      throw new Error(
        `${id}.compositionProfile.artwork.cropBehavior.${viewport} is unsupported`,
      );
    }
    cropBehavior[viewport] = behavior;
  }
  return {
    primaryFocus,
    secondaryFocus,
    quietZones,
    viewportIntent,
    artwork: { narrativeAnchor, workspaceQuietZone, focalZones, cropBehavior },
  };
}

function validateThemeDocument(theme, expectedId, file) {
  assertObject(theme, file);
  const id = assertId(theme.id);
  if (id !== expectedId) throw new Error(`${file} id does not match manifest id ${expectedId}`);
  const compiledAuthoring = compileThemeAuthoring(theme, { expectedId: id, label: file });

  assertObject(theme.metadata, `${id}.metadata`);
  for (const key of ["name", "summary", "style", "license", "provenance"]) {
    if (typeof theme.metadata[key] !== "string" || !theme.metadata[key].trim()) {
      throw new Error(`${id}.metadata.${key} must be a non-empty string`);
    }
  }
  const aestheticProfile = validateAestheticProfile(theme, id, compiledAuthoring);
  const compositionProfile = validateCompositionProfile(theme, id, compiledAuthoring);
  const materialGrammar = validateMaterialGrammar(theme, id);

  assertObject(theme.tokens, `${id}.tokens`);
  for (const token of REQUIRED_TOKENS) {
    const value = theme.tokens[token];
    if (typeof value !== "string" || !value.trim()) {
      throw new Error(`${id}.tokens.${token} must be a non-empty string`);
    }
    assertSafeToken(id, token, value.trim());
  }
  const baseContrast = validatePalette(id, theme.tokens);
  const variants = { base: baseContrast };

  if (theme.darkTokens !== undefined) {
    assertObject(theme.darkTokens, `${id}.darkTokens`);
    for (const [token, value] of Object.entries(theme.darkTokens)) {
      if (!COLOR_TOKENS.has(token) || typeof value !== "string" || !COLOR_PATTERN.test(value)) {
        throw new Error(`${id}.darkTokens.${token} must be a supported six-digit hex color token`);
      }
    }
    variants.dark = validatePalette(`${id}.darkTokens`, { ...theme.tokens, ...theme.darkTokens });
  }

  assertObject(theme.capabilities, `${id}.capabilities`);
  if (!new Set(["light", "dark", "adaptive"]).has(theme.capabilities.mode)) {
    throw new Error(`${id}.capabilities.mode must be light, dark, or adaptive`);
  }
  for (const key of ["cssOnly", "userArt", "glassChrome", "tactileControls", "experimental"]) {
    if (typeof theme.capabilities[key] !== "boolean") {
      throw new Error(`${id}.capabilities.${key} must be boolean`);
    }
  }

  assertObject(theme.usability, `${id}.usability`);
  for (const [key, expected] of Object.entries(REQUIRED_USABILITY)) {
    if (theme.usability[key] !== expected) {
      throw new Error(`${id}.usability.${key} must be ${JSON.stringify(expected)}`);
    }
  }
  const composition = validateComposition(theme, id, compiledAuthoring);
  const contrast = { pass: true, pairs: baseContrast.pairs, variants };
  return {
    id,
    name: theme.metadata.name,
    summary: theme.metadata.summary,
    contrast,
    composition,
    authoringContract: compiledAuthoring,
    designContract: {
      aestheticProfile,
      compositionProfile,
      materialGrammar,
      creativeBrief: compiledAuthoring.creativeBrief,
      experience: compiledAuthoring.experience,
      layoutPlan: compiledAuthoring.layoutPlan,
      coordinateOwnership: compiledAuthoring.coordinateOwnership,
    },
  };
}

async function validateArtDescriptor(directory, descriptor, label, trustPolicy) {
  assertObject(descriptor, label);
  if (typeof descriptor.file !== "string" || path.basename(descriptor.file) !== descriptor.file) {
    throw new Error(`${label}.file must stay inside the theme directory`);
  }
  if (typeof descriptor.license !== "string" || !descriptor.license.trim() ||
      typeof descriptor.provenance !== "string" || !descriptor.provenance.trim()) {
    throw new Error(`${label} requires license and provenance`);
  }
  if (!SUPPORTED_ART_EXTENSIONS.has(path.extname(descriptor.file).toLowerCase())) {
    throw new Error(`${label} uses an unsupported image format`);
  }
  const extension = path.extname(descriptor.file).toLowerCase();
  if (!trustPolicy.assets.allowedExtensions.includes(extension)) {
    throw new Error(`${label} uses an image format blocked by asset governance`);
  }
  const file = path.join(directory, descriptor.file);
  const [art, bytes] = await Promise.all([fs.stat(file), fs.readFile(file)]);
  if (!art.isFile() || art.size < 1 || art.size > trustPolicy.assets.maximumFileBytes) {
    throw new Error(`${label} must be a non-empty image within the asset governance limit`);
  }
  const metadata = readImageMetadata(bytes, extension);
  if (!metadata ||
      metadata.width > trustPolicy.assets.maximumDimension ||
      metadata.height > trustPolicy.assets.maximumDimension ||
      metadata.width * metadata.height > trustPolicy.assets.maximumPixels) {
    throw new Error(`${label} has unsupported or oversized image dimensions`);
  }
  return {
    file: descriptor.file,
    bytes: art.size,
    width: metadata.width,
    height: metadata.height,
    aspect: metadata.aspect,
    sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
  };
}

async function validateThemeDirectory(directory, expectedId, trustPolicy) {
  const configFile = path.join(directory, "theme.json");
  const theme = await readJson(configFile);
  const summary = validateThemeDocument(theme, expectedId, configFile);
  const optionalCss = path.join(directory, "theme.css");
  let cssPolicy = { pass: true, violations: [] };
  try {
    const stat = await fs.stat(optionalCss);
    if (!stat.isFile() || stat.size > 256 * 1024) {
      throw new Error(`${optionalCss} must be a file no larger than 256 KiB`);
    }
    const css = await fs.readFile(optionalCss, "utf8");
    if (/@import\b|https?:\/\/|url\s*\(/i.test(css)) {
      throw new Error(`${optionalCss} cannot import or fetch resources`);
    }
    cssPolicy = validateThemeCssPolicy(expectedId, css, {
      themePack: true,
      artworkTheme: Boolean(theme.art),
    });
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }

  const assets = [];
  if (theme.art !== undefined) {
    assets.push(await validateArtDescriptor(
      directory,
      theme.art,
      `${expectedId}.art`,
      trustPolicy,
    ));
    if (theme.art.variants !== undefined) {
      assertObject(theme.art.variants, `${expectedId}.art.variants`);
      const entries = Object.entries(theme.art.variants);
      if (entries.length > MAX_ART_VARIANTS) {
        throw new Error(`${expectedId}.art.variants supports at most ${MAX_ART_VARIANTS} assets`);
      }
      for (const [name, descriptor] of entries) {
        if (!ART_VARIANT_PATTERN.test(name)) {
          throw new Error(`${expectedId}.art.variants contains an invalid name: ${name}`);
        }
        assets.push(await validateArtDescriptor(
          directory,
          descriptor,
          `${expectedId}.art.variants.${name}`,
          trustPolicy,
        ));
      }
    }
  }
  return { ...summary, directory, theme, cssPolicy, assets };
}

async function validateCatalog(catalogDirectory, baseCssFile = path.join(root, "assets", "base.css")) {
  const [runtimeContract, governance] = await Promise.all([
    validateRuntimeContracts(root),
    loadGovernance(root),
  ]);
  const baseCssStat = await fs.stat(baseCssFile);
  if (!baseCssStat.isFile() || baseCssStat.size > 256 * 1024) {
    throw new Error(`${baseCssFile} must be a file no larger than 256 KiB`);
  }
  const baseCss = await fs.readFile(baseCssFile, "utf8");
  if (/@import\b|https?:\/\/|url\s*\(/i.test(baseCss)) {
    throw new Error(`${baseCssFile} cannot import or fetch resources`);
  }
  const baseCssPolicy = {
    ...validateThemeCssPolicy("base-css", baseCss),
    nativePaintBridge: validateNativePaintBridge(baseCss),
    browserGuestIsolation: validateBrowserGuestIsolation(baseCss),
    settingsContinuity: validateSettingsContinuity(baseCss),
    appHeaderTransparency: validateAppHeaderTransparency(baseCss),
    semanticHeaderMaterial: validateSemanticHeaderMaterial(baseCss),
  };
  const manifestFile = path.join(catalogDirectory, "manifest.json");
  const manifest = await readJson(manifestFile);
  assertObject(manifest, manifestFile);
  if (manifest.version !== 2 || !Array.isArray(manifest.themes) || manifest.themes.length < 1) {
    throw new Error(`${manifestFile} must contain version 2 and a non-empty themes array`);
  }

  const collections = [];
  const collectionIds = new Set();
  if (manifest.collections !== undefined) {
    if (!Array.isArray(manifest.collections) || manifest.collections.length < 1 ||
        manifest.collections.length > 32) {
      throw new Error(`${manifestFile}.collections must contain 1-32 collection objects`);
    }
    for (const [index, item] of manifest.collections.entries()) {
      assertObject(item, `manifest collection ${index}`);
      const id = assertId(item.id, `manifest collection ${index} id`);
      if (collectionIds.has(id)) throw new Error(`Duplicate collection id: ${id}`);
      collectionIds.add(id);
      const order = item.order === undefined ? (index + 1) * 10 : item.order;
      if (!Number.isInteger(order) || order < 0 || order > 10000) {
        throw new Error(`${id}.order must be an integer from 0 to 10000`);
      }
      collections.push({
        id,
        name: assertDesignText(item.name, `${id}.name`, 1, 80),
        summary: assertDesignText(item.summary, `${id}.summary`, 1, 200),
        order,
      });
    }
    collections.sort((first, second) => first.order - second.order ||
      first.name.localeCompare(second.name));
  }

  const seen = new Set();
  const themes = [];
  const loadedThemes = [];
  for (const item of manifest.themes) {
    assertObject(item, "manifest theme");
    const id = assertId(item.id, "manifest theme id");
    if (seen.has(id)) throw new Error(`Duplicate theme id: ${id}`);
    seen.add(id);
    const collectionId = item.collectionId === undefined
      ? null
      : assertId(item.collectionId, `${id}.collectionId`);
    if (collectionId !== null && !collectionIds.has(collectionId)) {
      throw new Error(`${id}.collectionId refers to an unknown collection: ${collectionId}`);
    }
    const variantLabel = item.variantLabel === undefined
      ? null
      : assertDesignText(item.variantLabel, `${id}.variantLabel`, 1, 80);
    const trust = validateRegistryDeclaration(item);
    const relative = assertRelativeDirectory(item.path, `${id}.path`);
    const directory = await containedDirectory(catalogDirectory, relative);
    const loaded = await validateThemeDirectory(directory, id, governance.trustPolicy);
    loadedThemes.push(loaded);
    const primaryArt = loaded.assets.at(0) ?? null;
    themes.push({
      id: loaded.id,
      name: loaded.name,
      collectionId,
      variantLabel: variantLabel ?? loaded.name,
      summary: loaded.summary,
      mode: loaded.theme.capabilities.mode,
      experimental: loaded.theme.capabilities.experimental,
      usability: loaded.theme.usability,
      contrast: loaded.contrast,
      cssPolicy: loaded.cssPolicy,
      trust,
      authoringContract: loaded.authoringContract,
      designContract: loaded.designContract,
      presentation: {
        artPresence: loaded.authoringContract.creativeBrief.artPresence,
        artTopology: loaded.authoringContract.experience.artTopology,
        previewPath: primaryArt ? path.join(loaded.directory, primaryArt.file) : null,
        previewWidth: primaryArt?.width ?? null,
        previewHeight: primaryArt?.height ?? null,
      },
      colors: {
        canvas: loaded.theme.tokens.canvas,
        surface: loaded.theme.tokens.surface,
        accent: loaded.theme.tokens.accent,
        focus: loaded.theme.tokens.focus,
      },
    });
  }
  baseCssPolicy.appHeaderTransparency = {
    ...baseCssPolicy.appHeaderTransparency,
    catalogThemeCount: themes.length,
    catalogThemes: themes.map((theme) => theme.id),
    themePackNativeTopologyOverrides: "forbidden",
  };
  baseCssPolicy.semanticHeaderMaterial = {
    ...baseCssPolicy.semanticHeaderMaterial,
    catalogThemeCount: themes.length,
    catalogThemes: themes.map((theme) => theme.id),
  };
  const registry = await auditCatalogRegistry(
    catalogDirectory,
    manifest,
    loadedThemes,
    governance.trustPolicy,
    governance.trustStore,
  );
  return {
    pass: true,
    catalog: await fs.realpath(catalogDirectory),
    runtimeContract,
    compatibility: governance.compatibility,
    registry,
    baseCssPolicy,
    collections,
    themes,
  };
}

async function auditCatalog(catalogDirectory, baseCssFile) {
  const result = await validateCatalog(catalogDirectory, baseCssFile);
  return { ...result, policyVersion: WORKBENCH_POLICY_VERSION };
}

function emptyState() {
  return {
    version: 2,
    selectedTheme: null,
    nextLaunchTheme: null,
    loadedTheme: null,
    previousTheme: null,
    loadedHash: null,
    launchHistory: [],
    stagedRollback: null,
    migratedFromVersion: null,
    updatedAt: null,
  };
}

async function readState(file) {
  try {
    const value = await readJson(file);
    assertObject(value, file);
    if (![1, 2].includes(value.version)) throw new Error(`${file} has an unsupported version`);
    const state = {
      ...emptyState(),
      ...value,
      version: 2,
      migratedFromVersion: value.version === 1 ? 1 : value.migratedFromVersion ?? null,
      launchHistory: value.version === 1 ? [] : value.launchHistory ?? [],
      stagedRollback: value.version === 1 ? null : value.stagedRollback ?? null,
    };
    for (const key of ["selectedTheme", "nextLaunchTheme", "loadedTheme", "previousTheme"]) {
      if (state[key] !== null) assertId(state[key], key);
    }
    if (state.loadedHash !== null && (typeof state.loadedHash !== "string" || state.loadedHash.length > 128)) {
      throw new Error(`${file} has an invalid loadedHash`);
    }
    if (!Array.isArray(state.launchHistory) || state.launchHistory.length > 12) {
      throw new Error(`${file} has an invalid launchHistory`);
    }
    for (const [index, entry] of state.launchHistory.entries()) {
      assertObject(entry, `${file}.launchHistory[${index}]`);
      assertId(entry.theme, `${file}.launchHistory[${index}].theme`);
      if (entry.hash !== null &&
          (typeof entry.hash !== "string" || entry.hash.length > 128)) {
        throw new Error(`${file}.launchHistory[${index}].hash is invalid`);
      }
      if (typeof entry.recordedAt !== "string" || !Number.isFinite(Date.parse(entry.recordedAt))) {
        throw new Error(`${file}.launchHistory[${index}].recordedAt is invalid`);
      }
    }
    if (state.stagedRollback !== null) {
      assertObject(state.stagedRollback, `${file}.stagedRollback`);
      if (state.stagedRollback.from !== null) {
        assertId(state.stagedRollback.from, `${file}.stagedRollback.from`);
      }
      assertId(state.stagedRollback.to, `${file}.stagedRollback.to`);
      if (typeof state.stagedRollback.stagedAt !== "string" ||
          !Number.isFinite(Date.parse(state.stagedRollback.stagedAt))) {
        throw new Error(`${file}.stagedRollback.stagedAt is invalid`);
      }
    }
    return state;
  } catch (error) {
    if (error.code === "ENOENT") return emptyState();
    throw error;
  }
}

async function writeState(file, state) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const next = { ...state, version: 2, updatedAt: new Date().toISOString() };
  const temporary = `${file}.${process.pid}.${crypto.randomBytes(6).toString("hex")}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(next, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  await fs.rename(temporary, file);
  return next;
}

async function selectTheme(options) {
  const catalog = required(options, "catalog");
  const stateFile = required(options, "state");
  const theme = assertId(options.theme);
  const result = await validateCatalog(catalog);
  if (!result.themes.some((item) => item.id === theme)) throw new Error(`Unknown theme: ${theme}`);
  const state = await readState(stateFile);
  return writeState(stateFile, {
    ...state,
    selectedTheme: theme,
    nextLaunchTheme: theme,
    stagedRollback: null,
  });
}

async function markLoaded(options) {
  const stateFile = required(options, "state");
  const theme = assertId(options.theme);
  if (!options.hash || options.hash.length > 128 || !/^[a-zA-Z0-9._-]+$/.test(options.hash)) {
    throw new Error("--hash must be a short identifier without whitespace");
  }
  const state = await readState(stateFile);
  const previousTheme = state.loadedTheme && state.loadedTheme !== theme ? state.loadedTheme : state.previousTheme;
  const launchHistory = state.loadedTheme && state.loadedTheme !== theme
    ? [...state.launchHistory, {
      theme: state.loadedTheme,
      hash: state.loadedHash,
      recordedAt: state.updatedAt ?? new Date().toISOString(),
    }].slice(-12)
    : state.launchHistory;
  return writeState(stateFile, {
    ...state,
    selectedTheme: theme,
    nextLaunchTheme: theme,
    loadedTheme: theme,
    previousTheme,
    loadedHash: options.hash,
    launchHistory,
    stagedRollback: null,
  });
}

async function markRestored(options) {
  const stateFile = required(options, "state");
  const state = await readState(stateFile);
  return writeState(stateFile, {
    ...state,
    nextLaunchTheme: null,
    loadedTheme: null,
    previousTheme: state.loadedTheme ?? state.previousTheme,
    loadedHash: null,
    stagedRollback: null,
  });
}

async function stageRollback(options) {
  const catalog = required(options, "catalog");
  const stateFile = required(options, "state");
  const state = await readState(stateFile);
  const target = options.theme
    ? assertId(options.theme)
    : state.previousTheme ?? state.launchHistory.at(-1)?.theme ?? null;
  if (!target) throw new Error("No previous theme is available to stage for rollback");
  const rollbackCandidates = new Set([
    state.previousTheme,
    ...state.launchHistory.map((entry) => entry.theme),
  ].filter(Boolean));
  if (!rollbackCandidates.has(target) || target === state.loadedTheme) {
    throw new Error(`Rollback target is not present in trusted launch history: ${target}`);
  }
  const result = await validateCatalog(catalog);
  if (!result.themes.some((item) => item.id === target)) {
    throw new Error(`Rollback target is not in the trusted catalog: ${target}`);
  }
  return writeState(stateFile, {
    ...state,
    selectedTheme: target,
    nextLaunchTheme: target,
    stagedRollback: {
      from: state.loadedTheme,
      to: target,
      stagedAt: new Date().toISOString(),
    },
  });
}

async function resolveTheme(options) {
  const catalog = required(options, "catalog");
  const theme = assertId(options.theme);
  const result = await validateCatalog(catalog);
  if (!result.themes.some((item) => item.id === theme)) throw new Error(`Unknown theme: ${theme}`);
  const manifest = await readJson(path.join(catalog, "manifest.json"));
  const item = manifest.themes.find((entry) => entry.id === theme);
  const directory = await containedDirectory(catalog, assertRelativeDirectory(item.path, `${theme}.path`));
  const configFile = path.join(directory, "theme.json");
  const config = await readJson(configFile);
  const files = [configFile, path.join(directory, "theme.css")];
  if (config.art?.file) files.push(path.join(directory, config.art.file));
  for (const name of Object.keys(config.art?.variants ?? {}).sort()) {
    const file = config.art.variants[name]?.file;
    if (file) files.push(path.join(directory, file));
  }
  const hash = crypto.createHash("sha256");
  for (const file of files) {
    try {
      hash.update(await fs.readFile(file));
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  return { pass: true, id: theme, directory, hash: hash.digest("hex") };
}

async function resolveNextTheme(options) {
  const catalog = required(options, "catalog");
  const stateFile = required(options, "state");
  const state = await readState(stateFile);
  if (!state.nextLaunchTheme) {
    throw new Error("nextLaunchTheme is not set; select a theme before starting Codex Themes");
  }
  return resolveTheme({ catalog, theme: state.nextLaunchTheme });
}

async function writeJsonAtomic(file, value) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.${crypto.randomBytes(6).toString("hex")}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  await fs.rename(temporary, file);
}

async function compileTheme(options) {
  const themeFile = required(options, "theme-file");
  const theme = await readJson(themeFile);
  return {
    pass: true,
    themeFile,
    compiled: compileThemeAuthoring(theme, { label: themeFile }),
  };
}

async function migrateTheme(options) {
  if (options["theme-file"]) {
    const themeFile = required(options, "theme-file");
    const output = required(options, "output");
    if (path.resolve(themeFile) === path.resolve(output)) {
      throw new Error("Migration output must be a new file; in-place migration is not allowed");
    }
    const source = await readJson(themeFile);
    const migration = migrateThemeDocument(source, { label: themeFile });
    await writeJsonAtomic(output, migration.document);
    return {
      pass: true,
      status: migration.changed ? "MIGRATED" : "UNCHANGED",
      input: themeFile,
      output,
      sourceSchemaVersion: migration.sourceSchemaVersion,
      targetSchemaVersion: migration.targetSchemaVersion,
    };
  }
  const catalog = required(options, "catalog");
  const manifest = await readJson(path.join(catalog, "manifest.json"));
  if (!Array.isArray(manifest.themes)) throw new Error("Catalog manifest has no themes");
  const themes = [];
  for (const entry of manifest.themes) {
    const directory = await containedDirectory(
      catalog,
      assertRelativeDirectory(entry.path, `${entry.id}.path`),
    );
    const themeFile = path.join(directory, "theme.json");
    const source = await readJson(themeFile);
    const migration = migrateThemeDocument(source, {
      expectedId: entry.id,
      label: themeFile,
    });
    themes.push({
      id: entry.id,
      sourceSchemaVersion: migration.sourceSchemaVersion,
      targetSchemaVersion: migration.targetSchemaVersion,
      changed: migration.changed,
      outputRequired: migration.changed,
    });
  }
  return {
    pass: true,
    status: "DRY_RUN",
    catalog: await fs.realpath(catalog),
    mutationPerformed: false,
    themes,
  };
}

async function registryStatus(options) {
  const result = await validateCatalog(required(options, "catalog"));
  return result.registry;
}

async function compatibilityStatus(options) {
  const { compatibility } = await loadGovernance(root);
  const platform = options.platform;
  const version = options["codex-version"];
  if (!platform) throw new Error("Missing --platform");
  if (!version) throw new Error("Missing --codex-version");
  return {
    ...evaluateCompatibility(compatibility, platform, version),
    contracts: compatibility.contracts,
  };
}

async function signPackage(options) {
  const directory = required(options, "theme-dir");
  const privateKey = required(options, "private-key");
  const output = required(options, "output");
  if (!options.publisher) throw new Error("Missing --publisher");
  const { trustPolicy } = await loadGovernance(root);
  const descriptor = await createDetachedThemeSignature(
    directory,
    privateKey,
    options.publisher,
    trustPolicy,
  );
  await writeJsonAtomic(output, descriptor);
  return {
    pass: true,
    status: "SIGNED",
    output,
    publisherId: descriptor.publisherId,
    payloadHash: descriptor.payloadHash,
    algorithm: descriptor.algorithm,
  };
}

async function verifyPackage(options) {
  const directory = required(options, "theme-dir");
  const signature = options.signature
    ? path.resolve(options.signature)
    : path.join(directory, "theme.signature.json");
  const { trustPolicy, trustStore: defaultTrustStore } = await loadGovernance(root);
  const trustStore = options["trust-store"]
    ? await readJson(path.resolve(options["trust-store"]))
    : defaultTrustStore;
  return verifyDetachedThemeSignature(directory, signature, trustStore, trustPolicy);
}

async function diagnoseThemeSystem(options) {
  const catalog = required(options, "catalog");
  const validation = await validateCatalog(catalog);
  const theme = options.theme
    ? validation.themes.find((item) => item.id === assertId(options.theme))
    : null;
  if (options.theme && !theme) throw new Error(`Unknown theme: ${options.theme}`);
  if (Boolean(options.platform) !== Boolean(options["codex-version"])) {
    throw new Error("--platform and --codex-version must be provided together");
  }
  const compatibility = options.platform
    ? evaluateCompatibility(
      validation.compatibility,
      options.platform,
      options["codex-version"],
    )
    : null;
  const state = options.state ? await readState(path.resolve(options.state)) : null;
  const registryThemes = options.theme
    ? validation.registry.themes.filter((item) => item.id === options.theme)
    : validation.registry.themes;
  return {
    pass: true,
    status: "PARTIAL",
    mutationPerformed: false,
    contracts: {
      authoringSchemaVersion: AUTHORING_SCHEMA_VERSION,
      compositionCompilerContractVersion: COMPOSITION_COMPILER_CONTRACT_VERSION,
      workbenchPolicyVersion: WORKBENCH_POLICY_VERSION,
      runtime: validation.runtimeContract,
    },
    compatibility,
    state,
    theme: theme ?? null,
    registry: {
      pass: validation.registry.pass,
      themes: registryThemes,
      distribution: validation.registry.distribution,
    },
    note: "Diagnostics and static validation do not replace live renderer QA.",
  };
}

function help() {
  return {
    pass: true,
    usage: [
      "theme-tool.mjs validate --catalog PATH",
      "theme-tool.mjs audit --catalog PATH [--base-css PATH]",
      "theme-tool.mjs resolve --catalog PATH --theme ID",
      "theme-tool.mjs resolve-next --catalog PATH --state FILE",
      "theme-tool.mjs select --catalog PATH --state FILE --theme ID",
      "theme-tool.mjs mark-loaded --state FILE --theme ID --hash HASH",
      "theme-tool.mjs mark-restored --state FILE",
      "theme-tool.mjs rollback --catalog PATH --state FILE [--theme ID]",
      "theme-tool.mjs status --state FILE",
      "theme-tool.mjs compile --theme-file FILE",
      "theme-tool.mjs migrate --catalog PATH",
      "theme-tool.mjs migrate --theme-file FILE --output NEW_FILE",
      "theme-tool.mjs registry --catalog PATH",
      "theme-tool.mjs compatibility --platform windows|macos --codex-version VERSION",
      "theme-tool.mjs diagnose --catalog PATH [--theme ID] [--state FILE]",
      "theme-tool.mjs sign-package --theme-dir PATH --private-key FILE --publisher ID --output FILE",
      "theme-tool.mjs verify-package --theme-dir PATH [--signature FILE] [--trust-store FILE]",
    ],
  };
}

async function main() {
  const { command, options } = parseArgs(process.argv.slice(2));
  const baseCssFile = options["base-css"] ? path.resolve(options["base-css"]) : undefined;
  if (command === "validate") return validateCatalog(required(options, "catalog"), baseCssFile);
  if (command === "audit") return auditCatalog(required(options, "catalog"), baseCssFile);
  if (command === "resolve") return resolveTheme(options);
  if (command === "resolve-next") return resolveNextTheme(options);
  if (command === "select") return selectTheme(options);
  if (command === "mark-loaded") return markLoaded(options);
  if (command === "mark-restored") return markRestored(options);
  if (command === "rollback") return stageRollback(options);
  if (command === "status") return readState(required(options, "state"));
  if (command === "compile") return compileTheme(options);
  if (command === "migrate") return migrateTheme(options);
  if (command === "registry") return registryStatus(options);
  if (command === "compatibility") return compatibilityStatus(options);
  if (command === "diagnose") return diagnoseThemeSystem(options);
  if (command === "sign-package") return signPackage(options);
  if (command === "verify-package") return verifyPackage(options);
  if (command === "help" || command === "--help") return help();
  throw new Error(`Unknown command: ${command}`);
}

try {
  const result = await main();
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
} catch (error) {
  process.stderr.write(`${JSON.stringify({ pass: false, error: error.message })}\n`);
  process.exitCode = 1;
}
