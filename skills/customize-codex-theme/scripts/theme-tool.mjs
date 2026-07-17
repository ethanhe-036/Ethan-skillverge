import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

const ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const COLOR_PATTERN = /^#[0-9a-f]{6}$/i;
const RADIUS_PATTERN = /^\d+(?:\.\d+)?(?:px|rem|em)$/i;
const FONT_PATTERN = /^[a-z0-9\s,'"_-]+$/i;
const REQUIRED_TOKENS = [
  "canvas", "surface", "surfaceRaised", "text", "muted", "accent", "accentText",
  "border", "focus", "success", "warning", "danger", "info", "codeSurface",
  "terminalSurface", "diffAddSurface", "diffRemoveSurface", "approvalSurface",
  "radiusSmall", "radiusMedium", "radiusLarge", "shadowLow", "shadowHigh",
  "bodyFont", "displayFont",
];
const COLOR_TOKENS = new Set(REQUIRED_TOKENS.slice(0, 18));

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

function validatePalette(id, palette) {
  const pairs = [
    ["text", "surface", 4.5], ["muted", "surface", 4.5],
    ["accentText", "accent", 4.5], ["text", "codeSurface", 4.5],
    ["text", "diffAddSurface", 4.5], ["text", "diffRemoveSurface", 4.5],
    ["text", "approvalSurface", 4.5], ["focus", "surface", 3],
  ];
  for (const [foreground, background, minimum] of pairs) {
    const ratio = contrastRatio(palette[foreground], palette[background]);
    if (ratio < minimum) {
      throw new Error(`${id} contrast ${foreground}/${background} is ${ratio.toFixed(2)}; expected ${minimum}`);
    }
  }
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

function validateThemeDocument(theme, expectedId, file) {
  assertObject(theme, file);
  const id = assertId(theme.id);
  if (id !== expectedId) throw new Error(`${file} id does not match manifest id ${expectedId}`);

  assertObject(theme.metadata, `${id}.metadata`);
  for (const key of ["name", "summary", "style", "license", "provenance"]) {
    if (typeof theme.metadata[key] !== "string" || !theme.metadata[key].trim()) {
      throw new Error(`${id}.metadata.${key} must be a non-empty string`);
    }
  }

  assertObject(theme.tokens, `${id}.tokens`);
  for (const token of REQUIRED_TOKENS) {
    const value = theme.tokens[token];
    if (typeof value !== "string" || !value.trim()) {
      throw new Error(`${id}.tokens.${token} must be a non-empty string`);
    }
    assertSafeToken(id, token, value.trim());
  }
  validatePalette(id, theme.tokens);

  if (theme.darkTokens !== undefined) {
    assertObject(theme.darkTokens, `${id}.darkTokens`);
    for (const [token, value] of Object.entries(theme.darkTokens)) {
      if (!COLOR_TOKENS.has(token) || typeof value !== "string" || !COLOR_PATTERN.test(value)) {
        throw new Error(`${id}.darkTokens.${token} must be a supported six-digit hex color token`);
      }
    }
    validatePalette(`${id}.darkTokens`, { ...theme.tokens, ...theme.darkTokens });
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
  return { id, name: theme.metadata.name, summary: theme.metadata.summary };
}

async function validateThemeDirectory(directory, expectedId) {
  const configFile = path.join(directory, "theme.json");
  const theme = await readJson(configFile);
  const summary = validateThemeDocument(theme, expectedId, configFile);
  const optionalCss = path.join(directory, "theme.css");
  try {
    const stat = await fs.stat(optionalCss);
    if (!stat.isFile() || stat.size > 256 * 1024) {
      throw new Error(`${optionalCss} must be a file no larger than 256 KiB`);
    }
    const css = await fs.readFile(optionalCss, "utf8");
    if (/@import\b|https?:\/\/|url\s*\(/i.test(css)) {
      throw new Error(`${optionalCss} cannot import or fetch resources`);
    }
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }

  if (theme.art !== undefined) {
    assertObject(theme.art, `${expectedId}.art`);
    if (typeof theme.art.file !== "string" || path.basename(theme.art.file) !== theme.art.file) {
      throw new Error(`${expectedId}.art.file must stay inside the theme directory`);
    }
    if (typeof theme.art.license !== "string" || !theme.art.license.trim() ||
        typeof theme.art.provenance !== "string" || !theme.art.provenance.trim()) {
      throw new Error(`${expectedId}.art requires license and provenance`);
    }
    if (![".png", ".jpg", ".jpeg", ".webp"].includes(path.extname(theme.art.file).toLowerCase())) {
      throw new Error(`${expectedId}.art uses an unsupported image format`);
    }
    const art = await fs.stat(path.join(directory, theme.art.file));
    if (!art.isFile() || art.size < 1 || art.size > 16 * 1024 * 1024) {
      throw new Error(`${expectedId}.art must be a non-empty image no larger than 16 MiB`);
    }
  }
  return { ...summary, directory, theme };
}

async function validateCatalog(catalogDirectory) {
  const manifestFile = path.join(catalogDirectory, "manifest.json");
  const manifest = await readJson(manifestFile);
  assertObject(manifest, manifestFile);
  if (manifest.version !== 1 || !Array.isArray(manifest.themes) || manifest.themes.length < 1) {
    throw new Error(`${manifestFile} must contain version 1 and a non-empty themes array`);
  }

  const seen = new Set();
  const themes = [];
  for (const item of manifest.themes) {
    assertObject(item, "manifest theme");
    const id = assertId(item.id, "manifest theme id");
    if (seen.has(id)) throw new Error(`Duplicate theme id: ${id}`);
    seen.add(id);
    const relative = assertRelativeDirectory(item.path, `${id}.path`);
    const directory = await containedDirectory(catalogDirectory, relative);
    const loaded = await validateThemeDirectory(directory, id);
    themes.push({ id: loaded.id, name: loaded.name, summary: loaded.summary });
  }
  return { pass: true, catalog: await fs.realpath(catalogDirectory), themes };
}

function emptyState() {
  return {
    version: 1,
    selectedTheme: null,
    nextLaunchTheme: null,
    loadedTheme: null,
    previousTheme: null,
    loadedHash: null,
    updatedAt: null,
  };
}

async function readState(file) {
  try {
    const value = await readJson(file);
    assertObject(value, file);
    const state = { ...emptyState(), ...value };
    if (state.version !== 1) throw new Error(`${file} has an unsupported version`);
    for (const key of ["selectedTheme", "nextLaunchTheme", "loadedTheme", "previousTheme"]) {
      if (state[key] !== null) assertId(state[key], key);
    }
    if (state.loadedHash !== null && (typeof state.loadedHash !== "string" || state.loadedHash.length > 128)) {
      throw new Error(`${file} has an invalid loadedHash`);
    }
    return state;
  } catch (error) {
    if (error.code === "ENOENT") return emptyState();
    throw error;
  }
}

async function writeState(file, state) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const next = { ...state, version: 1, updatedAt: new Date().toISOString() };
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
  return writeState(stateFile, { ...state, selectedTheme: theme, nextLaunchTheme: theme });
}

async function markLoaded(options) {
  const stateFile = required(options, "state");
  const theme = assertId(options.theme);
  if (!options.hash || options.hash.length > 128 || !/^[a-zA-Z0-9._-]+$/.test(options.hash)) {
    throw new Error("--hash must be a short identifier without whitespace");
  }
  const state = await readState(stateFile);
  const previousTheme = state.loadedTheme && state.loadedTheme !== theme ? state.loadedTheme : state.previousTheme;
  return writeState(stateFile, {
    ...state,
    selectedTheme: theme,
    nextLaunchTheme: theme,
    loadedTheme: theme,
    previousTheme,
    loadedHash: options.hash,
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

function help() {
  return {
    pass: true,
    usage: [
      "theme-tool.mjs validate --catalog PATH",
      "theme-tool.mjs resolve --catalog PATH --theme ID",
      "theme-tool.mjs select --catalog PATH --state FILE --theme ID",
      "theme-tool.mjs mark-loaded --state FILE --theme ID --hash HASH",
      "theme-tool.mjs mark-restored --state FILE",
      "theme-tool.mjs status --state FILE",
    ],
  };
}

async function main() {
  const { command, options } = parseArgs(process.argv.slice(2));
  if (command === "validate") return validateCatalog(required(options, "catalog"));
  if (command === "resolve") return resolveTheme(options);
  if (command === "select") return selectTheme(options);
  if (command === "mark-loaded") return markLoaded(options);
  if (command === "mark-restored") return markRestored(options);
  if (command === "status") return readState(required(options, "state"));
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
