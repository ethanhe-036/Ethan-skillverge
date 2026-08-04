import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { inflateSync } from "node:zlib";
import { compileThemeAuthoring } from "./composition-compiler.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const QUALITY_CONTRACT_VERSION = 4;
const TEXT_TOKENS = ["text", "muted", "success", "warning", "danger", "info"];
const COMPOSITION_VARIABLES = [
  "--ct-art-focus-x", "--ct-art-focus-y", "--ct-workbench-scrim",
  "--ct-sidebar-tint", "--ct-main-tint", "--ct-header-tint", "--ct-right-panel-tint",
  "--ct-settings-tint",
  "--ct-header-material-top-tint", "--ct-header-material-mid-tint",
  "--ct-header-material-bottom-tint", "--ct-header-material-control-tint",
  "--ct-safe-left", "--ct-safe-right", "--ct-safe-top", "--ct-safe-bottom",
];
const CRITICAL_PARTS = ["composer", "dialog", "menu", "terminal", "browser"];

function parseArgs(argv) {
  const options = {
    command: "audit",
    catalog: null,
    theme: null,
    strict: false,
    summary: false,
  };
  if (argv[0] && !argv[0].startsWith("--")) options.command = argv.shift();
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--catalog") options.catalog = path.resolve(argv[++index]);
    else if (argument === "--theme") options.theme = argv[++index];
    else if (argument === "--strict") options.strict = true;
    else if (argument === "--summary") options.summary = true;
    else throw new Error(`Unknown argument: ${argument}`);
  }
  if (!["audit", "help", "--help"].includes(options.command)) {
    throw new Error(`Unknown action: ${options.command}`);
  }
  if (options.command === "audit" && !options.catalog) throw new Error("audit requires --catalog");
  if (options.theme && !/^[a-z0-9][a-z0-9-]{0,63}$/.test(options.theme)) {
    throw new Error("--theme must be a lowercase theme id");
  }
  return options;
}

function runJson(file, args) {
  return new Promise((resolve, reject) => {
    execFile(process.execPath, [file, ...args], {
      windowsHide: true,
      maxBuffer: 16 * 1024 * 1024,
    }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(stderr.trim() || stdout.trim() || error.message));
        return;
      }
      try {
        resolve(JSON.parse(stdout));
      } catch (parseError) {
        reject(new Error(`Could not parse ${path.basename(file)} output: ${parseError.message}`));
      }
    });
  });
}

async function optionalText(file) {
  try {
    return await fs.readFile(file, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return "";
    throw error;
  }
}

function finding(code, severity, message, evidence = null) {
  return { code, severity, message, evidence };
}

function rgb(hex) {
  return hex.slice(1).match(/../g).map((value) => Number.parseInt(value, 16));
}

function linear(channel) {
  const value = channel / 255;
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function luminance(color) {
  return 0.2126 * linear(color[0]) + 0.7152 * linear(color[1]) + 0.0722 * linear(color[2]);
}

function ratio(first, second) {
  const a = luminance(first);
  const b = luminance(second);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

function composite(top, bottom, alpha) {
  return top.map((value, index) => value * alpha + bottom[index] * (1 - alpha));
}

function settingsMaterialTint() {
  return 52;
}

function oklab(color) {
  const [red, green, blue] = color.map(linear);
  const l = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue;
  const m = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue;
  const s = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue;
  const lRoot = Math.cbrt(l);
  const mRoot = Math.cbrt(m);
  const sRoot = Math.cbrt(s);
  return {
    l: 0.2104542553 * lRoot + 0.793617785 * mRoot - 0.0040720468 * sRoot,
    a: 1.9779984951 * lRoot - 2.428592205 * mRoot + 0.4505937099 * sRoot,
    b: 0.0259040371 * lRoot + 0.7827717662 * mRoot - 0.808675766 * sRoot,
  };
}

function oklch(color) {
  const lab = oklab(color);
  const chroma = Math.sqrt(lab.a ** 2 + lab.b ** 2);
  let hue = Math.atan2(lab.b, lab.a) * 180 / Math.PI;
  if (hue < 0) hue += 360;
  return { l: lab.l, c: chroma, h: hue };
}

function oklabDistance(first, second) {
  const firstLab = oklab(first);
  const secondLab = oklab(second);
  return Math.sqrt(
    (firstLab.l - secondLab.l) ** 2 +
    (firstLab.a - secondLab.a) ** 2 +
    (firstLab.b - secondLab.b) ** 2,
  );
}

function perceptualPaletteSignals(tokens) {
  const surfaces = Object.fromEntries(
    ["canvas", "surface", "surfaceRaised"].map((token) => [token, oklch(rgb(tokens[token]))]),
  );
  const surfaceSteps = {
    canvasToSurface: surfaces.surface.l - surfaces.canvas.l,
    surfaceToRaised: surfaces.surfaceRaised.l - surfaces.surface.l,
  };
  const semanticTokens = ["success", "warning", "danger", "info"];
  const semanticPairs = [];
  const accentSemanticPairs = [];
  for (let first = 0; first < semanticTokens.length; first += 1) {
    accentSemanticPairs.push({
      pair: `accent/${semanticTokens[first]}`,
      distance: oklabDistance(
        rgb(tokens.accent),
        rgb(tokens[semanticTokens[first]]),
      ),
    });
    for (let second = first + 1; second < semanticTokens.length; second += 1) {
      semanticPairs.push({
        pair: `${semanticTokens[first]}/${semanticTokens[second]}`,
        distance: oklabDistance(
          rgb(tokens[semanticTokens[first]]),
          rgb(tokens[semanticTokens[second]]),
        ),
      });
    }
  }
  const closestSemanticPair = semanticPairs
    .sort((first, second) => first.distance - second.distance)[0];
  const closestAccentSemanticPair = accentSemanticPairs
    .sort((first, second) => first.distance - second.distance)[0];
  return {
    surfaceLightness: Object.fromEntries(
      Object.entries(surfaces).map(([token, value]) => [token, Number(value.l.toFixed(4))]),
    ),
    surfaceChroma: Object.fromEntries(
      Object.entries(surfaces).map(([token, value]) => [token, Number(value.c.toFixed(4))]),
    ),
    surfaceSteps: Object.fromEntries(
      Object.entries(surfaceSteps).map(([name, value]) => [name, Number(value.toFixed(4))]),
    ),
    lightnessOrdered: Object.values(surfaceSteps).every((value) => value > 0),
    minimumLightnessStep: Number(Math.min(...Object.values(surfaceSteps)).toFixed(4)),
    maximumSurfaceChroma: Number(
      Math.max(...Object.values(surfaces).map((value) => value.c)).toFixed(4),
    ),
    accentChroma: Number(oklch(rgb(tokens.accent)).c.toFixed(4)),
    closestSemanticPair: {
      pair: closestSemanticPair.pair,
      distance: Number(closestSemanticPair.distance.toFixed(4)),
    },
    closestAccentSemanticPair: {
      pair: closestAccentSemanticPair.pair,
      distance: Number(closestAccentSemanticPair.distance.toFixed(4)),
    },
  };
}

function semanticAccentParts(rules) {
  const parts = new Set();
  for (const rule of rules) {
    if (!/var\(--ct-(?:accent|focus)\b/i.test(rule.declarations)) continue;
    for (const match of rule.selector.matchAll(
      /\[\s*data-ct-part\s*~=\s*["']([^"']+)["']\s*\]/gi,
    )) {
      parts.add(match[1]);
    }
  }
  return [...parts].sort();
}

function pointInsideRectangle(point, rectangle) {
  return point.x >= rectangle.x &&
    point.x <= rectangle.x + rectangle.width &&
    point.y >= rectangle.y &&
    point.y <= rectangle.y + rectangle.height;
}

function paeth(first, second, third) {
  const estimate = first + second - third;
  const firstDistance = Math.abs(estimate - first);
  const secondDistance = Math.abs(estimate - second);
  const thirdDistance = Math.abs(estimate - third);
  if (firstDistance <= secondDistance && firstDistance <= thirdDistance) return first;
  if (secondDistance <= thirdDistance) return second;
  return third;
}

function decodePng(bytes) {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (bytes.length < 33 || !bytes.subarray(0, 8).equals(signature)) return null;
  let offset = 8;
  let width;
  let height;
  let bitDepth;
  let colorType;
  let interlace;
  const compressed = [];
  while (offset + 12 <= bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const type = bytes.toString("ascii", offset + 4, offset + 8);
    const start = offset + 8;
    const end = start + length;
    if (end + 4 > bytes.length) return null;
    if (type === "IHDR") {
      width = bytes.readUInt32BE(start);
      height = bytes.readUInt32BE(start + 4);
      bitDepth = bytes[start + 8];
      colorType = bytes[start + 9];
      interlace = bytes[start + 12];
    } else if (type === "IDAT") {
      compressed.push(bytes.subarray(start, end));
    } else if (type === "IEND") {
      break;
    }
    offset = end + 4;
  }
  const channels = colorType === 2 ? 3 : colorType === 6 ? 4 : 0;
  if (!width || !height || bitDepth !== 8 || !channels || interlace !== 0 || !compressed.length) {
    return null;
  }
  const stride = width * channels;
  const inflated = inflateSync(Buffer.concat(compressed));
  if (inflated.length !== (stride + 1) * height) return null;
  const pixels = Buffer.alloc(stride * height);
  let input = 0;
  for (let row = 0; row < height; row += 1) {
    const filter = inflated[input++];
    const rowStart = row * stride;
    const previousStart = rowStart - stride;
    for (let column = 0; column < stride; column += 1) {
      const raw = inflated[input++];
      const left = column >= channels ? pixels[rowStart + column - channels] : 0;
      const above = row > 0 ? pixels[previousStart + column] : 0;
      const upperLeft = row > 0 && column >= channels
        ? pixels[previousStart + column - channels]
        : 0;
      let value;
      if (filter === 0) value = raw;
      else if (filter === 1) value = raw + left;
      else if (filter === 2) value = raw + above;
      else if (filter === 3) value = raw + Math.floor((left + above) / 2);
      else if (filter === 4) value = raw + paeth(left, above, upperLeft);
      else return null;
      pixels[rowStart + column] = value & 0xff;
    }
  }
  return { width, height, channels, pixels };
}

function selectedArtDescriptor(theme) {
  if (!theme.art) return null;
  const selected = theme.composition?.asset;
  if (!selected || selected === "primary") return theme.art;
  return theme.art.variants?.[selected] ?? null;
}

function decodedPixel(decoded, x, y, fallback) {
  const boundedX = Math.max(0, Math.min(decoded.width - 1, x));
  const boundedY = Math.max(0, Math.min(decoded.height - 1, y));
  const index = (boundedY * decoded.width + boundedX) * decoded.channels;
  const alpha = decoded.channels === 4 ? decoded.pixels[index + 3] / 255 : 1;
  return composite([
    decoded.pixels[index],
    decoded.pixels[index + 1],
    decoded.pixels[index + 2],
  ], fallback, alpha);
}

function quietZoneEdgeDensity(decoded, quietZone, fallback, sampleStep) {
  if (!quietZone) return null;
  const startX = Math.floor(decoded.width * quietZone.x / 100);
  const endX = Math.ceil(decoded.width * (quietZone.x + quietZone.width) / 100);
  const startY = Math.floor(decoded.height * quietZone.y / 100);
  const endY = Math.ceil(decoded.height * (quietZone.y + quietZone.height) / 100);
  let edgeSum = 0;
  let edgeSamples = 0;
  for (let y = startY; y < endY; y += sampleStep) {
    for (let x = startX; x < endX; x += sampleStep) {
      const current = decodedPixel(decoded, x, y, fallback);
      const currentLuminance = luminance(current);
      if (x + sampleStep < endX) {
        edgeSum += Math.abs(
          currentLuminance - luminance(decodedPixel(decoded, x + sampleStep, y, fallback)),
        );
        edgeSamples += 1;
      }
      if (y + sampleStep < endY) {
        edgeSum += Math.abs(
          currentLuminance - luminance(decodedPixel(decoded, x, y + sampleStep, fallback)),
        );
        edgeSamples += 1;
      }
    }
  }
  return edgeSamples
    ? Number((edgeSum / edgeSamples * 100).toFixed(2))
    : null;
}

async function analyzeArtwork(directory, theme) {
  const descriptor = selectedArtDescriptor(theme);
  if (!descriptor?.file) return null;
  const extension = path.extname(descriptor.file).toLowerCase();
  const bytes = await fs.readFile(path.join(directory, descriptor.file));
  if (extension !== ".png") {
    return {
      supported: false,
      file: descriptor.file,
      reason: "pixel-composite audit currently supports 8-bit RGB/RGBA non-interlaced PNG",
    };
  }
  const decoded = decodePng(bytes);
  if (!decoded) {
    return { supported: false, file: descriptor.file, reason: "unsupported PNG encoding" };
  }
  const canvas = rgb(theme.tokens.canvas);
  const surface = rgb(theme.tokens.surface);
  const raised = rgb(theme.tokens.surfaceRaised);
  const scrim = theme.composition.workbenchScrim / 100;
  const surfaces = {
    sidebar: {
      color: surface,
      tint: theme.composition.sidebarTint / 100,
      requiredContrastTokens: TEXT_TOKENS,
    },
    main: {
      color: surface,
      tint: theme.composition.mainTint / 100,
      requiredContrastTokens: TEXT_TOKENS,
    },
    header: {
      color: raised,
      tint: theme.composition.headerTint / 100,
      requiredContrastTokens: TEXT_TOKENS,
    },
    "right-panel": {
      color: surface,
      tint: theme.composition.rightPanelTint / 100,
      requiredContrastTokens: TEXT_TOKENS,
    },
    settings: {
      color: surface,
      tint: settingsMaterialTint() / 100,
      requiredContrastTokens: ["text"],
      protectedContrastTokens: TEXT_TOKENS,
    },
  };
  const tokenColors = Object.fromEntries(TEXT_TOKENS.map((token) => [token, rgb(theme.tokens[token])]));
  const results = Object.fromEntries(Object.entries(surfaces).map(([name, value]) => [
    name,
    {
      artSignalPercent: Number(((1 - scrim) * (1 - value.tint) * 100).toFixed(2)),
      contrastModel: name === "settings"
        ? "ambient-primary-text-plus-opaque-protected-descendants"
        : "direct-semantic-text",
      requiredContrastTokens: value.requiredContrastTokens,
      protectedContrastTokens: value.protectedContrastTokens ?? [],
      minimumContrast: Object.fromEntries(TEXT_TOKENS.map((token) => [token, Number.POSITIVE_INFINITY])),
      protectedMinimumContrast: value.protectedContrastTokens
        ? Object.fromEntries(value.protectedContrastTokens.map((token) => [
          token,
          Number(ratio(tokenColors[token], raised).toFixed(2)),
        ]))
        : {},
    },
  ]));
  const pixelCount = decoded.width * decoded.height;
  const sampleStep = Math.max(1, Math.ceil(Math.sqrt(pixelCount / 120000)));
  for (let y = 0; y < decoded.height; y += sampleStep) {
    for (let x = 0; x < decoded.width; x += sampleStep) {
      const index = (y * decoded.width + x) * decoded.channels;
      const alpha = decoded.channels === 4 ? decoded.pixels[index + 3] / 255 : 1;
      const source = [
        decoded.pixels[index],
        decoded.pixels[index + 1],
        decoded.pixels[index + 2],
      ];
      const art = composite(source, canvas, alpha);
      const workbench = composite(canvas, art, scrim);
      for (const [name, surfaceProfile] of Object.entries(surfaces)) {
        const background = composite(surfaceProfile.color, workbench, surfaceProfile.tint);
        for (const token of TEXT_TOKENS) {
          results[name].minimumContrast[token] = Math.min(
            results[name].minimumContrast[token],
            ratio(tokenColors[token], background),
          );
        }
      }
    }
  }
  for (const surface of Object.values(results)) {
    for (const token of TEXT_TOKENS) {
      surface.minimumContrast[token] = Number(surface.minimumContrast[token].toFixed(2));
    }
  }
  return {
    supported: true,
    file: descriptor.file,
    width: decoded.width,
    height: decoded.height,
    sampleStep,
    quietZoneEdgeDensityPercent: quietZoneEdgeDensity(
      decoded,
      theme.compositionProfile?.artwork?.workspaceQuietZone,
      canvas,
      sampleStep,
    ),
    surfaces: results,
  };
}

function inspectFlatRules(css) {
  const rules = [];
  const pattern = /([^{}]+)\{([^{}]*)\}/g;
  for (const match of css.matchAll(pattern)) {
    rules.push({ selector: match[1].trim(), declarations: match[2].trim() });
  }
  return rules;
}

function declarationValues(declarations, propertyPattern) {
  const values = [];
  const pattern = new RegExp(
    `(?:^|;)\\s*(?:${propertyPattern})\\s*:\\s*([^;]+)`,
    "gi",
  );
  for (const match of declarations.matchAll(pattern)) values.push(match[1].trim());
  return values;
}

function minimumPaletteHeadroom(validationTheme) {
  const variants = validationTheme.contrast?.variants ?? {};
  let minimum = Number.POSITIVE_INFINITY;
  let pair = null;
  let variantName = null;
  for (const [name, variant] of Object.entries(variants)) {
    for (const candidate of variant.pairs ?? []) {
      if (candidate.minimum !== 4.5 || candidate.ratio >= minimum) continue;
      minimum = candidate.ratio;
      pair = `${candidate.foreground}/${candidate.background}`;
      variantName = name;
    }
  }
  return Number.isFinite(minimum) ? { ratio: minimum, pair, variant: variantName } : null;
}

function buildAestheticAudit(theme, rules, artwork, compiledAuthoring) {
  const paletteVariants = {
    base: perceptualPaletteSignals(theme.tokens),
  };
  if (theme.darkTokens) {
    paletteVariants.dark = perceptualPaletteSignals({ ...theme.tokens, ...theme.darkTokens });
  }
  const materialFamilies = Object.fromEntries(
    ["canvas", "navigation", "controls", "reading", "transient"]
      .map((role) => [role, theme.materialGrammar[role]]),
  );
  const artworkProfile = theme.compositionProfile.artwork;
  const focalQuietConflicts = artworkProfile
    ? artworkProfile.focalZones
      .filter((zone) => pointInsideRectangle(zone, artworkProfile.workspaceQuietZone))
      .map((zone) => zone.name)
    : [];
  const quietZoneAreaPercent = artworkProfile
    ? Number((
      artworkProfile.workspaceQuietZone.width *
      artworkProfile.workspaceQuietZone.height / 100
    ).toFixed(2))
    : null;
  return {
    creativeBrief: compiledAuthoring.creativeBrief,
    experience: compiledAuthoring.experience,
    layoutPlan: compiledAuthoring.layoutPlan,
    coordinateOwnership: compiledAuthoring.coordinateOwnership,
    authoringMode: compiledAuthoring.authoringMode,
    designThesis: theme.aestheticProfile.designThesis,
    paletteStrategy: theme.aestheticProfile.paletteStrategy,
    identity: {
      signatureMotif: theme.aestheticProfile.signatureMotif,
      moodKeywords: theme.aestheticProfile.moodKeywords,
      antiGoals: theme.aestheticProfile.antiGoals,
      motifBudget: theme.aestheticProfile.motifBudget,
    },
    hierarchy: {
      primaryFocus: theme.compositionProfile.primaryFocus,
      secondaryFocus: theme.compositionProfile.secondaryFocus,
      quietZones: theme.compositionProfile.quietZones,
      viewportIntent: theme.compositionProfile.viewportIntent,
    },
    material: {
      families: materialFamilies,
      familyCount: new Set(Object.values(materialFamilies)).size,
      elevationLevels: theme.materialGrammar.elevationLevels,
      lightingDirection: theme.materialGrammar.lightingDirection,
    },
    composition: artworkProfile ? {
      narrativeAnchor: artworkProfile.narrativeAnchor,
      quietZoneAreaPercent,
      focalZoneCount: artworkProfile.focalZones.length,
      focalQuietConflicts,
      cropBehavior: artworkProfile.cropBehavior,
      quietZoneEdgeDensityPercent: artwork?.quietZoneEdgeDensityPercent ?? null,
    } : null,
    palette: paletteVariants,
    cssSignals: {
      accentSemanticParts: semanticAccentParts(rules),
    },
  };
}

async function listPowerShellEncodingFindings() {
  const findings = [];
  const directory = path.join(root, "scripts");
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    if (!entry.isFile() || path.extname(entry.name).toLowerCase() !== ".ps1") continue;
    const bytes = await fs.readFile(path.join(directory, entry.name));
    const containsNonAscii = bytes.some((value) => value >= 0x80);
    const hasUtf8Bom = bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf;
    if (containsNonAscii && !hasUtf8Bom) {
      findings.push(finding(
        "WINDOWS_POWERSHELL_ENCODING",
        "error",
        `${entry.name} contains non-ASCII text without a UTF-8 BOM`,
      ));
    }
  }
  return findings;
}

async function auditQuality(options) {
  const themeTool = path.join(here, "theme-tool.mjs");
  const validation = await runJson(themeTool, ["validate", "--catalog", options.catalog]);
  const [manifest, skillManifest, surfaceMatrix] = await Promise.all([
    fs.readFile(path.join(options.catalog, "manifest.json"), "utf8").then(JSON.parse),
    fs.readFile(path.join(root, "skill.manifest.json"), "utf8").then(JSON.parse),
    fs.readFile(path.join(root, "fixtures", "surface-matrix.json"), "utf8").then(JSON.parse),
  ]);
  const baseCss = await fs.readFile(path.join(root, "assets", "base.css"), "utf8");
  const renderer = await fs.readFile(path.join(root, "assets", "renderer-inject.js"), "utf8");
  const injector = await fs.readFile(path.join(root, "scripts", "injector.mjs"), "utf8");
  const engineFindings = await listPowerShellEncodingFindings();

  for (const variable of COMPOSITION_VARIABLES) {
    if (renderer.includes(`root.style.setProperty("${variable}"`)) {
      engineFindings.push(finding(
        "INLINE_COMPOSITION_VARIABLE",
        "error",
        `${variable} is written inline and would suppress responsive pack CSS`,
      ));
    }
  }
  if (!injector.includes("buildCompositionCss(config)") ||
      !injector.includes("${compositionCss}") ||
      !injector.includes("compileThemeAuthoring(sourceConfig")) {
    engineFindings.push(finding(
      "MISSING_COMPOSITION_STYLESHEET",
      "error",
      "The injector does not compile authoring intent and composition defaults before pack CSS",
    ));
  }
  if (!injector.includes("authoring.experience.backgroundScope") ||
      !injector.includes('data-ct-scope="${route}"')) {
    engineFindings.push(finding(
      "AUTHORING_SCOPE_PROJECTION_MISSING",
      "error",
      "Schema-v2 background scope is not projected onto the semantic workbench",
    ));
  }
  if (!injector.includes("authoring.experience.responsivePolicy.split") ||
      !injector.includes("projectedComposition(composition")) {
    engineFindings.push(finding(
      "AUTHORING_RESPONSIVE_PROJECTION_MISSING",
      "error",
      "Schema-v2 responsive intent is not projected into deterministic viewport rules",
    ));
  }
  if (!injector.includes('data-ct-composition="portrait-zone"') ||
      !injector.includes("background-size: cover, auto 100%")) {
    engineFindings.push(finding(
      "PORTRAIT_COMPATIBILITY_PROJECTION_MISSING",
      "error",
      "The declared portrait-zone compatibility mode has no single-owner runtime projection",
    ));
  }

  const selectedEntries = manifest.themes.filter((entry) => !options.theme || entry.id === options.theme);
  if (options.theme && !selectedEntries.length) throw new Error(`Unknown theme: ${options.theme}`);
  const coverageByTheme = new Map(
    (validation.runtimeContract?.fixtureCoverage?.themeCoverage ?? [])
      .map((entry) => [entry.themeId, entry]),
  );
  const coveredCells = new Set(
    validation.runtimeContract?.fixtureCoverage?.coveredCells ?? [],
  );
  const validationByTheme = new Map(validation.themes.map((theme) => [theme.id, theme]));
  const themes = [];

  for (const entry of selectedEntries) {
    const directory = await fs.realpath(path.join(options.catalog, entry.path));
    const theme = JSON.parse(await fs.readFile(path.join(directory, "theme.json"), "utf8"));
    const compiledAuthoring = compileThemeAuthoring(theme, { expectedId: entry.id });
    const runtimeTheme = {
      ...theme,
      composition: compiledAuthoring.runtimeComposition,
    };
    const css = await optionalText(path.join(directory, "theme.css"));
    const rules = inspectFlatRules(css);
    const findings = [];

    if (new RegExp(`data-codex-theme=[\"']${entry.id}[\"']`).test(baseCss)) {
      findings.push(finding(
        "PACK_RULE_IN_SHARED_CSS",
        "error",
        `${entry.id} has theme-specific selectors in assets/base.css`,
      ));
    }
    if (theme.capabilities.cssOnly && theme.capabilities.userArt) {
      findings.push(finding(
        "CAPABILITY_CONTRADICTION",
        "error",
        "cssOnly and userArt cannot both be true",
      ));
    }
    if (css.includes('[data-ct-part~="browser"]')) {
      findings.push(finding(
        "BROWSER_GUEST_PAINT",
        "error",
        "Theme pack CSS must not paint the Browser guest surface",
      ));
    }
    const ownership = compiledAuthoring.coordinateOwnership;
    const expectedOwners = theme.art ? ["workbench"] : [];
    if (ownership.coordinateSpace !== "semantic-workbench-viewport" ||
        ownership.owner !== (theme.art ? "workbench" : "none") ||
        JSON.stringify(ownership.directRasterOwners) !== JSON.stringify(expectedOwners) ||
        ownership.panelRasterCopies !== "forbidden" ||
        ownership.panelCropAuthority !== "forbidden") {
      findings.push(finding(
        "COORDINATE_OWNERSHIP_INVALID",
        "error",
        "Compiled artwork must have one semantic coordinate owner and no independent panel crops",
        ownership,
      ));
    }

    for (const rule of rules) {
      if (/::(?:before|after)\b/i.test(rule.selector) &&
          /(?:^|;)\s*content\s*:\s*(?!none|normal|["']{2})/i.test(rule.declarations) &&
          !/(?:^|;)\s*pointer-events\s*:\s*none\b/i.test(rule.declarations)) {
        findings.push(finding(
          "DECORATION_INTERACTION_RISK",
          "error",
          `Decorative generated content must not imitate or intercept native controls: ${rule.selector}`,
        ));
      }
      if (CRITICAL_PARTS.some((part) =>
        rule.selector.includes(`[data-ct-part~="${part}"]`)) &&
        /background(?:-color)?\s*:\s*(?:transparent|[^;]*(?:--ct-user-art|--ct-workbench-art))/i
          .test(rule.declarations)) {
        findings.push(finding(
          "CRITICAL_READING_PROTECTION_MISSING",
          "error",
          `Critical reading surface cannot be transparent or own artwork: ${rule.selector}`,
        ));
      }
    }

    const nonNoneBlurRules = rules.filter((rule) =>
      declarationValues(rule.declarations, "(?:-webkit-)?backdrop-filter")
        .some((value) => !/^none(?:\s*!important)?$/i.test(value)));
    const semanticHeaderBlur = nonNoneBlurRules.some((rule) =>
      rule.selector.includes('[data-ct-part~="header"]'));
    const criticalBlur = nonNoneBlurRules.filter((rule) =>
      CRITICAL_PARTS.some((part) => rule.selector.includes(`[data-ct-part~="${part}"]`)));

    if (theme.capabilities.glassChrome) {
      if (!css || !semanticHeaderBlur) {
        findings.push(finding(
          "GLASS_CAPABILITY_MISSING",
          "error",
          "glassChrome requires a semantic header rule with real backdrop blur",
        ));
      }
      if (!/@media[^{]*prefers-reduced-transparency\s*:\s*reduce/i.test(css) ||
          !/@media[^{]*max-width\s*:\s*720px/i.test(css) ||
          !/backdrop-filter\s*:\s*none/i.test(css)) {
        findings.push(finding(
          "GLASS_FALLBACK_MISSING",
          "error",
          "glassChrome requires reduced-transparency and narrow opaque fallbacks",
        ));
      }
    } else if (nonNoneBlurRules.length) {
      findings.push(finding(
        "GLASS_CAPABILITY_DRIFT",
        "error",
        "Theme uses backdrop blur but glassChrome is false",
      ));
    }
    for (const rule of criticalBlur) {
      findings.push(finding(
        "CRITICAL_SURFACE_BLUR",
        "error",
        `Critical semantic surface uses backdrop blur: ${rule.selector}`,
      ));
    }

    if (theme.capabilities.tactileControls &&
        !rules.some((rule) =>
          rule.selector.includes("[data-ct-part~=") &&
          rule.selector.includes(":active") &&
          /box-shadow\s*:|background(?:-color)?\s*:/i.test(rule.declarations))) {
      findings.push(finding(
        "TACTILE_CAPABILITY_MISSING",
        "error",
        "tactileControls requires a semantic paint-only active-state rule",
      ));
    }

    const headroom = minimumPaletteHeadroom(validationByTheme.get(entry.id));
    if (headroom && headroom.ratio < 5.5) {
      findings.push(finding(
        "PALETTE_HEADROOM_LOW",
        "warning",
        `Lowest 4.5:1 token pair has only ${headroom.ratio}:1 headroom`,
        headroom,
      ));
    }

    let artwork = null;
    if (runtimeTheme.art && runtimeTheme.composition) {
      const declaredNarrowCrop =
        runtimeTheme.compositionProfile?.artwork?.cropBehavior?.narrow ?? null;
      if (
        runtimeTheme.composition.narrowMode === "retain" &&
        declaredNarrowCrop === "hide-art"
      ) {
        findings.push(finding(
          "NARROW_ART_INTENT_MISMATCH",
          "warning",
          `Declared narrow crop behavior is ${declaredNarrowCrop}, but the runtime composition retains artwork`,
          {
            viewportIntent: runtimeTheme.compositionProfile?.viewportIntent?.narrow ?? null,
            cropBehavior: declaredNarrowCrop,
            narrowMode: runtimeTheme.composition.narrowMode,
          },
        ));
      } else if (
        runtimeTheme.composition.narrowMode === "hide-art" &&
        declaredNarrowCrop === "preserve-focal"
      ) {
        findings.push(finding(
          "NARROW_ART_INTENT_MISMATCH",
          "warning",
          "Declared narrow crop behavior preserves the focal artwork, but the runtime composition hides it",
          {
            viewportIntent: runtimeTheme.compositionProfile?.viewportIntent?.narrow ?? null,
            cropBehavior: declaredNarrowCrop,
            narrowMode: runtimeTheme.composition.narrowMode,
          },
        ));
      }
      const compilerOwnsResponsiveProjection =
        compiledAuthoring.sourceSchemaVersion === 2 &&
        compiledAuthoring.projection.preservesDeclaredComposition === false;
      if (runtimeTheme.composition.narrowMode === "retain" &&
          !compilerOwnsResponsiveProjection &&
          (!/@media\s*\([^)]*max-width:/i.test(css) ||
           !/--ct-(?:sidebar|main|header|right-panel)-tint\s*:/i.test(css))) {
        findings.push(finding(
          "RESPONSIVE_ART_PROFILE_MISSING",
          "error",
          "retain artwork requires responsive semantic tint overrides",
        ));
      }
      artwork = await analyzeArtwork(directory, runtimeTheme);
      if (!artwork?.supported) {
        findings.push(finding(
          "ART_PIXEL_AUDIT_UNAVAILABLE",
          "warning",
          artwork?.reason ?? "Artwork could not be sampled",
          artwork,
        ));
      } else {
        for (const [surfaceName, surface] of Object.entries(artwork.surfaces)) {
          if (surface.artSignalPercent < 12) {
            findings.push(finding(
              "ART_SIGNAL_LOW",
              "warning",
              `${surfaceName} retains only ${surface.artSignalPercent}% of the raster signal`,
              { surface: surfaceName, artSignalPercent: surface.artSignalPercent },
            ));
          }
          const failures = surface.requiredContrastTokens
            .map((token) => ({ token, ratio: surface.minimumContrast[token] }))
            .filter(({ ratio: contrastRatio }) => contrastRatio < 4.5);
          const protectedFailures = surface.protectedContrastTokens
            .map((token) => ({ token, ratio: surface.protectedMinimumContrast[token] }))
            .filter(({ ratio: contrastRatio }) => contrastRatio < 4.5);
          if (failures.length) {
            findings.push(finding(
              "COMPOSITE_CONTRAST_RISK",
              "warning",
              `${surfaceName} has image-composited text risks below 4.5:1`,
              { surface: surfaceName, contrastModel: surface.contrastModel, failures },
            ));
          }
          if (protectedFailures.length) {
            findings.push(finding(
              "PROTECTED_SURFACE_CONTRAST_RISK",
              "warning",
              `${surfaceName} has protected descendant text risks below 4.5:1`,
              {
                surface: surfaceName,
                contrastModel: surface.contrastModel,
                failures: protectedFailures,
              },
            ));
          }
        }
      }
    }

    const aestheticAudit = buildAestheticAudit(
      runtimeTheme,
      rules,
      artwork,
      compiledAuthoring,
    );
    for (const [variantName, palette] of Object.entries(aestheticAudit.palette)) {
      if (!palette.lightnessOrdered) {
        findings.push(finding(
          "SURFACE_LIGHTNESS_ORDER",
          "warning",
          `${variantName} canvas/surface/surfaceRaised OKLCH lightness is not ordered`,
          { variant: variantName, ...palette },
        ));
      } else if (palette.minimumLightnessStep < 0.008) {
        findings.push(finding(
          "SURFACE_LIGHTNESS_STEP_LOW",
          "warning",
          `${variantName} surface ladder has only ${palette.minimumLightnessStep} minimum OKLCH lightness separation`,
          { variant: variantName, ...palette },
        ));
      }
      if (palette.maximumSurfaceChroma > 0.09) {
        findings.push(finding(
          "SURFACE_CHROMA_HIGH",
          "warning",
          `${variantName} neutral surfaces reach ${palette.maximumSurfaceChroma} OKLCH chroma`,
          { variant: variantName, ...palette },
        ));
      }
      if (palette.closestSemanticPair.distance < 0.08) {
        findings.push(finding(
          "SEMANTIC_COLOR_DISTANCE_LOW",
          "warning",
          `${variantName} ${palette.closestSemanticPair.pair} has only ` +
          `${palette.closestSemanticPair.distance} OKLab separation`,
          { variant: variantName, ...palette },
        ));
      }
      if (palette.closestAccentSemanticPair.distance < 0.06) {
        findings.push(finding(
          "ACCENT_SEMANTIC_DISTANCE_LOW",
          "warning",
          `${variantName} ${palette.closestAccentSemanticPair.pair} has only ` +
          `${palette.closestAccentSemanticPair.distance} OKLab separation`,
          { variant: variantName, ...palette },
        ));
      }
    }
    if (aestheticAudit.cssSignals.accentSemanticParts.length > 4) {
      findings.push(finding(
        "ACCENT_SPREAD_WIDE",
        "warning",
        `Accent/focus paint reaches ${aestheticAudit.cssSignals.accentSemanticParts.length} semantic surfaces`,
        aestheticAudit.cssSignals,
      ));
    }
    if (aestheticAudit.composition) {
      if (aestheticAudit.composition.quietZoneAreaPercent < 25) {
        findings.push(finding(
          "QUIET_ZONE_SMALL",
          "warning",
          `Declared artwork quiet zone covers only ${aestheticAudit.composition.quietZoneAreaPercent}% of the raster`,
          aestheticAudit.composition,
        ));
      }
      if (aestheticAudit.composition.focalQuietConflicts.length) {
        findings.push(finding(
          "FOCAL_QUIET_ZONE_CONFLICT",
          "warning",
          "Declared focal zones place their center inside the reading quiet zone",
          aestheticAudit.composition,
        ));
      }
      if (aestheticAudit.composition.quietZoneEdgeDensityPercent !== null &&
          aestheticAudit.composition.quietZoneEdgeDensityPercent > 10) {
        findings.push(finding(
          "QUIET_ZONE_VISUAL_NOISE",
          "warning",
          `Artwork quiet-zone edge density is ${aestheticAudit.composition.quietZoneEdgeDensityPercent}%`,
          aestheticAudit.composition,
        ));
      }
    }

    const coverage = coverageByTheme.get(entry.id) ?? { covered: 0, required: 0 };
    const expectedCells = surfaceMatrix.states.flatMap((state) =>
      surfaceMatrix.viewports.map((viewport) => `${state.id}/${viewport.id}`));
    const missingCells = expectedCells.filter(
      (cell) => !coveredCells.has(`${entry.id}/${cell}`),
    );
    const firstMissing = missingCells[0]?.split("/") ?? null;
    const verifiedCodexVersion =
      validation.runtimeContract?.verifiedCodexVersion ?? "current";
    const runtimeEvidence = {
      ...coverage,
      missingCells: options.theme ? missingCells : missingCells.slice(0, 3),
      omittedMissingCellCount: options.theme ? 0 : Math.max(0, missingCells.length - 3),
      captureCommand: firstMissing
        ? `node scripts/capture-dom-fixture.mjs --state ${firstMissing[0]} ` +
          `--viewport ${firstMissing[1]} --output ` +
          `fixtures/codex-${verifiedCodexVersion}-${entry.id}-${firstMissing[0]}-${firstMissing[1]}.json`
        : null,
      auditCommand: `node scripts/fixture-tool.mjs audit --theme ${entry.id}`,
    };
    if (coverage.covered < coverage.required) {
      findings.push(finding(
        "RUNTIME_MATRIX_INCOMPLETE",
        "warning",
        `Runtime evidence covers ${coverage.covered}/${coverage.required} matrix cells`,
        runtimeEvidence,
      ));
    }

    themes.push({
      id: entry.id,
      pass: !findings.some((item) => item.severity === "error"),
      headroom,
      artwork,
      aestheticAudit,
      runtimeCoverage: runtimeEvidence,
      findings,
    });
  }

  const allFindings = [...engineFindings, ...themes.flatMap((theme) => theme.findings)];
  const errorCount = allFindings.filter((item) => item.severity === "error").length;
  const warningCount = allFindings.filter((item) => item.severity === "warning").length;
  const pass = errorCount === 0 && (!options.strict || warningCount === 0);
  return {
    pass,
    qaStatus: "PARTIAL",
    qualityContractVersion: QUALITY_CONTRACT_VERSION,
    runtimeQaContractVersion: skillManifest.qaContractVersion,
    contractRelationship:
      "qualityContractVersion governs deterministic preset checks; " +
      "runtimeQaContractVersion governs live renderer evidence",
    catalog: await fs.realpath(options.catalog),
    filterTheme: options.theme,
    strict: options.strict,
    engine: {
      pass: !engineFindings.some((item) => item.severity === "error"),
      findings: engineFindings,
    },
    summary: { themeCount: themes.length, errorCount, warningCount },
    themes,
    note: "Static quality evidence cannot produce full runtime PASS.",
  };
}

function summarizeQuality(result) {
  const themes = result.themes.map((theme) => {
    const surfaces = Object.values(theme.artwork?.surfaces ?? {});
    const artSignal = surfaces.map((surface) => surface.artSignalPercent);
    const compositeContrast = surfaces.flatMap((surface) =>
      (surface.requiredContrastTokens ?? []).map((token) =>
        surface.minimumContrast?.[token]).filter(Number.isFinite));
    const protectedContrast = surfaces.flatMap((surface) =>
      Object.values(surface.protectedMinimumContrast ?? {}).filter(Number.isFinite));
    return {
      id: theme.id,
      pass: theme.pass,
      headroom: theme.headroom,
      artwork: theme.artwork ? {
        file: theme.artwork.file,
        width: theme.artwork.width,
        height: theme.artwork.height,
        minimumArtSignalPercent: artSignal.length ? Math.min(...artSignal) : null,
        minimumCompositeContrast: compositeContrast.length
          ? Math.min(...compositeContrast)
          : null,
        minimumProtectedContrast: protectedContrast.length
          ? Math.min(...protectedContrast)
          : null,
        quietZoneEdgeDensityPercent: theme.artwork.quietZoneEdgeDensityPercent ?? null,
      } : null,
      aestheticAudit: theme.aestheticAudit,
      runtimeCoverage: {
        covered: theme.runtimeCoverage.covered,
        required: theme.runtimeCoverage.required,
        firstMissingCell: theme.runtimeCoverage.missingCells[0] ?? null,
        captureCommand: theme.runtimeCoverage.captureCommand,
        auditCommand: theme.runtimeCoverage.auditCommand,
      },
      findings: theme.findings.map(({ code, severity, message }) => ({
        code,
        severity,
        message,
      })),
    };
  });
  return {
    pass: result.pass,
    qaStatus: result.qaStatus,
    qualityContractVersion: result.qualityContractVersion,
    runtimeQaContractVersion: result.runtimeQaContractVersion,
    contractRelationship: result.contractRelationship,
    strict: result.strict,
    summary: result.summary,
    engine: result.engine,
    themes,
    note: result.note,
  };
}

function help() {
  return {
    pass: true,
    usage: [
      "theme-quality.mjs audit --catalog PATH",
      "theme-quality.mjs audit --catalog PATH --theme ID",
      "theme-quality.mjs audit --catalog PATH --theme ID --summary",
      "theme-quality.mjs audit --catalog PATH --theme ID --strict",
    ],
  };
}

try {
  const options = parseArgs(process.argv.slice(2));
  const result = options.command === "audit" ? await auditQuality(options) : help();
  const output = options.summary && options.command === "audit"
    ? summarizeQuality(result)
    : result;
  process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
  if (!result.pass) process.exitCode = 1;
} catch (error) {
  process.stderr.write(`${JSON.stringify({ pass: false, error: error.message })}\n`);
  process.exitCode = 1;
}
