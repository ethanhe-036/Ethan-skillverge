import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadPayload } from "./injector.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const defaultSkillRoot = path.resolve(here, "..");

export const FIXTURE_PRIVACY = "structure-only-no-text-no-input-values-no-urls";
export const FIXTURE_ALLOWED_ATTRIBUTES = Object.freeze([
  "role",
  "data-testid",
  "data-app-shell-focus-area",
  "data-app-shell-tabs",
  "data-app-shell-tab-controller",
  "data-app-shell-tab-panel-controller",
  "data-browser-sidebar-webview",
  "data-settings-panel-slug",
  "data-ct-part",
]);
export const FIXTURE_STABLE_CLASS_SOURCE =
  "^(?:app-|main-surface$|composer-|electron-|bg-token-|text-token-|xterm|scrollbar-|draggable$|group/application-menu-top-bar$)";
export const FIXTURE_SCHEMA_VERSION = 2;

const ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const PART_PATTERN = /^[a-z][a-z0-9-]{0,31}$/;
const TAG_PATTERN = /^[a-z][a-z0-9-]*$/;
const VERSION_PATTERN = /^[0-9]+(?:\.[0-9]+){1,4}$/;
const HASH_PATTERN = /^[0-9a-f]{64}$/;
const TOP_LEVEL_KEYS = new Set([
  "schemaVersion",
  "privacy",
  "scope",
  "contractVersion",
  "verifiedCodexVersion",
  "themeId",
  "engineVersion",
  "payloadRevision",
  "styleRevision",
  "viewportId",
  "viewport",
  "nodeCount",
  "truncated",
  "nodes",
  "state",
  "themed",
]);
const VIEWPORT_KEYS = new Set(["width", "height", "devicePixelRatio", "visualScale"]);
const NODE_KEYS = new Set(["parent", "depth", "tag", "attributes", "classes"]);
const ALLOWED_ATTRIBUTE_SET = new Set(FIXTURE_ALLOWED_ATTRIBUTES);
const STABLE_CLASS_PATTERN = new RegExp(FIXTURE_STABLE_CLASS_SOURCE);

function assertObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be a JSON object`);
  }
}

function rejectUnknownKeys(value, allowed, label) {
  const unknown = Object.keys(value).filter((key) => !allowed.has(key));
  if (unknown.length) throw new Error(`${label} contains forbidden fields: ${unknown.join(", ")}`);
}

async function readJson(file) {
  const value = JSON.parse(await fs.readFile(file, "utf8"));
  assertObject(value, file);
  return value;
}

export async function loadFixtureContracts(root = defaultSkillRoot) {
  const [selectors, matrix, catalog] = await Promise.all([
    readJson(path.join(root, "assets", "selectors.json")),
    readJson(path.join(root, "fixtures", "surface-matrix.json")),
    readJson(path.join(root, "assets", "presets", "manifest.json")),
  ]);
  if (selectors.schemaVersion !== 1 || !selectors.parts || typeof selectors.parts !== "object") {
    throw new Error("selectors.json has an unsupported fixture contract");
  }
  if (matrix.schemaVersion !== 1 || matrix.contractVersion !== selectors.schemaVersion ||
      !Array.isArray(matrix.states) || !matrix.states.length ||
      !Array.isArray(matrix.viewports) || !matrix.viewports.length) {
    throw new Error("surface-matrix.json has an unsupported fixture contract");
  }
  const evidence = matrix.evidencePolicy;
  if (!evidence || evidence.coverage !== "state-viewport-cross-product" ||
      evidence.privacy !== FIXTURE_PRIVACY ||
      !Number.isInteger(evidence.maximumNodes) || evidence.maximumNodes < 100 ||
      !evidence.viewportTolerance || typeof evidence.viewportTolerance !== "object") {
    throw new Error("surface-matrix.json evidencePolicy is missing or invalid");
  }
  if (!Array.isArray(catalog.themes) || !catalog.themes.length) {
    throw new Error("preset manifest must declare at least one theme");
  }
  const themeIds = catalog.themes.map((theme) => theme?.id);
  if (themeIds.some((themeId) => !ID_PATTERN.test(String(themeId ?? ""))) ||
      new Set(themeIds).size !== themeIds.length) {
    throw new Error("preset manifest contains invalid or duplicate theme ids");
  }
  const presetRoot = path.join(root, "assets", "presets");
  const themeDirectories = new Map();
  for (const theme of catalog.themes) {
    if (typeof theme.path !== "string" || path.basename(theme.path) !== theme.path) {
      throw new Error(`preset manifest contains an unsafe path for ${theme.id}`);
    }
    themeDirectories.set(theme.id, path.join(presetRoot, theme.path));
  }
  return { selectors, matrix, themeIds, themeDirectories };
}

function validateViewport(viewport, viewportDefinition, evidencePolicy, label) {
  assertObject(viewport, `${label}.viewport`);
  rejectUnknownKeys(viewport, VIEWPORT_KEYS, `${label}.viewport`);
  if (!Number.isInteger(viewport.width) || viewport.width < 320 ||
      !Number.isInteger(viewport.height) || viewport.height < 240 ||
      typeof viewport.devicePixelRatio !== "number" || viewport.devicePixelRatio < 0.5 ||
      viewport.devicePixelRatio > 4 ||
      typeof viewport.visualScale !== "number" || viewport.visualScale < 0.25 ||
      viewport.visualScale > 4) {
    throw new Error(`${label}.viewport contains invalid geometry`);
  }
  const tolerance = evidencePolicy.viewportTolerance;
  for (const key of ["width", "height", "scale"]) {
    if (typeof tolerance[key] !== "number" || tolerance[key] < 0) {
      throw new Error(`surface-matrix evidencePolicy.viewportTolerance.${key} is invalid`);
    }
  }
  if (Math.abs(viewport.width - viewportDefinition.width) > tolerance.width ||
      Math.abs(viewport.height - viewportDefinition.height) > tolerance.height ||
      Math.abs(viewport.devicePixelRatio - viewportDefinition.scale) > tolerance.scale) {
    throw new Error(
      `${label}.viewport does not match ${viewportDefinition.id} within the declared tolerance`,
    );
  }
}

function validateNode(node, index, nodes, selectors, partCounts, label) {
  assertObject(node, `${label}.nodes[${index}]`);
  rejectUnknownKeys(node, NODE_KEYS, `${label}.nodes[${index}]`);
  if (!Number.isInteger(node.depth) || node.depth < 0 || node.depth > 32) {
    throw new Error(`${label}.nodes[${index}].depth is invalid`);
  }
  if (index === 0) {
    if (node.parent !== null || node.depth !== 0 || node.tag !== "html") {
      throw new Error(`${label}.nodes[0] must be the root html node`);
    }
  } else {
    if (!Number.isInteger(node.parent) || node.parent < 0 || node.parent >= index) {
      throw new Error(`${label}.nodes[${index}].parent must reference an earlier node`);
    }
    if (node.depth !== nodes[node.parent].depth + 1) {
      throw new Error(`${label}.nodes[${index}].depth does not match its parent`);
    }
  }
  if (typeof node.tag !== "string" || !TAG_PATTERN.test(node.tag)) {
    throw new Error(`${label}.nodes[${index}].tag is invalid`);
  }
  assertObject(node.attributes, `${label}.nodes[${index}].attributes`);
  for (const [name, value] of Object.entries(node.attributes)) {
    if (!ALLOWED_ATTRIBUTE_SET.has(name)) {
      throw new Error(`${label}.nodes[${index}] contains forbidden attribute ${name}`);
    }
    if (typeof value !== "string" || value.length > 120 || /[\u0000-\u001f\u007f]/.test(value)) {
      throw new Error(`${label}.nodes[${index}].attributes.${name} is invalid`);
    }
  }
  if (!Array.isArray(node.classes) || node.classes.length > 12) {
    throw new Error(`${label}.nodes[${index}].classes is invalid`);
  }
  for (const className of node.classes) {
    if (typeof className !== "string" || className.length > 120 ||
        !STABLE_CLASS_PATTERN.test(className)) {
      throw new Error(`${label}.nodes[${index}] contains a non-contract class`);
    }
  }
  for (const part of String(node.attributes["data-ct-part"] ?? "").split(/\s+/).filter(Boolean)) {
    if (!PART_PATTERN.test(part) || !selectors.parts[part]) {
      throw new Error(`${label}.nodes[${index}] contains unknown semantic part ${part}`);
    }
    partCounts.set(part, (partCounts.get(part) ?? 0) + 1);
  }
}

export function validateFixtureObject(fixture, contracts, label = "fixture") {
  assertObject(fixture, label);
  rejectUnknownKeys(fixture, TOP_LEVEL_KEYS, label);
  const { selectors, matrix } = contracts;
  if (fixture.schemaVersion !== FIXTURE_SCHEMA_VERSION || fixture.privacy !== FIXTURE_PRIVACY) {
    throw new Error(`${label} has an unsupported schema or privacy boundary`);
  }
  if (!ID_PATTERN.test(String(fixture.state ?? "")) ||
      !ID_PATTERN.test(String(fixture.viewportId ?? ""))) {
    throw new Error(`${label} requires stable state and viewportId values`);
  }
  const state = matrix.states.find((candidate) => candidate.id === fixture.state);
  const viewportDefinition = matrix.viewports.find(
    (candidate) => candidate.id === fixture.viewportId,
  );
  if (!state) throw new Error(`${label} references unknown state ${fixture.state}`);
  if (!viewportDefinition) {
    throw new Error(`${label} references unknown viewport ${fixture.viewportId}`);
  }
  if (String(fixture.contractVersion) !== String(matrix.contractVersion)) {
    throw new Error(`${label} uses a stale selector contract`);
  }
  const verifiedVersion = selectors.verifiedAgainst?.codexVersion;
  if (!VERSION_PATTERN.test(String(fixture.verifiedCodexVersion ?? "")) ||
      fixture.verifiedCodexVersion !== verifiedVersion) {
    throw new Error(`${label} uses a stale or invalid verified Codex version`);
  }
  if (!ID_PATTERN.test(String(fixture.scope ?? ""))) {
    throw new Error(`${label}.scope is invalid`);
  }
  if (!ID_PATTERN.test(String(fixture.themeId ?? "")) ||
      !contracts.themeIds.includes(fixture.themeId)) {
    throw new Error(`${label}.themeId is missing from the validated preset catalog`);
  }
  if (!VERSION_PATTERN.test(String(fixture.engineVersion ?? ""))) {
    throw new Error(`${label}.engineVersion is missing or invalid`);
  }
  if (!HASH_PATTERN.test(String(fixture.payloadRevision ?? "")) ||
      !HASH_PATTERN.test(String(fixture.styleRevision ?? ""))) {
    throw new Error(`${label} requires exact payload and style revisions`);
  }
  if (typeof fixture.themed !== "boolean" || typeof fixture.truncated !== "boolean") {
    throw new Error(`${label} requires boolean themed and truncated fields`);
  }
  validateViewport(fixture.viewport, viewportDefinition, matrix.evidencePolicy, label);
  if (!Array.isArray(fixture.nodes) ||
      !Number.isInteger(fixture.nodeCount) ||
      fixture.nodeCount !== fixture.nodes.length ||
      fixture.nodeCount < 1 ||
      fixture.nodeCount > matrix.evidencePolicy.maximumNodes) {
    throw new Error(`${label}.nodeCount does not match a bounded nodes array`);
  }

  const partCounts = new Map();
  fixture.nodes.forEach((node, index) => {
    validateNode(node, index, fixture.nodes, selectors, partCounts, label);
  });

  const reasons = [];
  if (!fixture.themed) reasons.push("theme-not-active");
  if (fixture.truncated) reasons.push("structure-truncated");
  if (state.id === "settings") {
    const settingsNodes = fixture.nodes.filter((node) =>
      String(node.attributes["data-ct-part"] ?? "").split(/\s+/).includes("settings")
    );
    if (fixture.scope !== "settings") reasons.push("settings-route-not-active");
    for (const node of settingsNodes) {
      if (Object.hasOwn(node.attributes, "data-settings-panel-slug")) {
        reasons.push("settings-part-owned-by-navigation-marker");
      }
      if (node.attributes.role !== "main" && !node.classes.includes("main-surface")) {
        reasons.push("settings-part-is-not-large-content-root");
      }
    }
  }
  for (const part of state.requiredParts) {
    if ((partCounts.get(part) ?? 0) < 1) reasons.push(`missing-part:${part}`);
  }
  for (const [part, count] of partCounts) {
    const definition = selectors.parts[part];
    if (definition.cardinality === "one" && count !== 1) {
      reasons.push(`cardinality:${part}:${count}`);
    }
    if (Number.isInteger(definition.maximum) && count > definition.maximum) {
      reasons.push(`maximum:${part}:${count}`);
    }
  }

  return {
    pass: true,
    eligibleForCoverage: reasons.length === 0,
    themeId: fixture.themeId,
    state: fixture.state,
    viewportId: fixture.viewportId,
    nodeCount: fixture.nodeCount,
    parts: Object.fromEntries([...partCounts].sort(([first], [second]) => first.localeCompare(second))),
    reasons,
  };
}

export async function validateFixtureFile(file, root = defaultSkillRoot) {
  const contracts = await loadFixtureContracts(root);
  const fixture = await readJson(file);
  return validateFixtureObject(fixture, contracts, path.basename(file));
}

export async function auditFixtureCoverage(root = defaultSkillRoot, options = {}) {
  const contracts = options.contracts ?? await loadFixtureContracts(root);
  const themeFilter = options.themeId ?? null;
  if (themeFilter !== null && !contracts.themeIds.includes(themeFilter)) {
    throw new Error(`Unknown preset theme: ${themeFilter}`);
  }
  const fixtureDirectory = path.join(root, "fixtures");
  const fixtureFiles = (await fs.readdir(fixtureDirectory, { withFileTypes: true }))
    .filter((entry) => entry.isFile() && /^codex-.*\.json$/i.test(entry.name))
    .map((entry) => entry.name)
    .sort((first, second) => first.localeCompare(second));
  const covered = new Map();
  const rejectedFixtures = [];
  const evidenceHash = crypto.createHash("sha256");
  const selectedThemes = themeFilter ? [themeFilter] : contracts.themeIds;
  const expectedRevisions = new Map();
  let selectedFixtureCount = 0;
  for (const fileName of fixtureFiles) {
    const fixturePath = path.join(fixtureDirectory, fileName);
    const fixtureBytes = await fs.readFile(fixturePath);
    const fixture = JSON.parse(fixtureBytes.toString("utf8"));
    assertObject(fixture, fixturePath);
    if (themeFilter && fixture.themeId !== themeFilter) continue;
    if (selectedThemes.includes(fixture.themeId)) {
      selectedFixtureCount += 1;
      evidenceHash.update(fileName);
      evidenceHash.update("\0");
      evidenceHash.update(String(fixtureBytes.length));
      evidenceHash.update("\0");
      evidenceHash.update(fixtureBytes);
      evidenceHash.update("\0");
    }
    let result;
    try {
      result = validateFixtureObject(fixture, contracts, fileName);
    } catch (error) {
      const knownTheme = contracts.themeIds.includes(fixture.themeId);
      const knownState = contracts.matrix.states.some((state) => state.id === fixture.state);
      const knownViewport = contracts.matrix.viewports.some(
        (viewport) => viewport.id === fixture.viewportId,
      );
      if (!knownTheme || !knownState || !knownViewport) throw error;
      const cell = `${fixture.themeId}/${fixture.state}/${fixture.viewportId}`;
      if (covered.has(cell) || rejectedFixtures.some((entry) => entry.cell === cell)) {
        throw new Error(`Duplicate fixture evidence for ${cell}`);
      }
      rejectedFixtures.push({
        file: fileName,
        cell,
        reasons: [`invalid-fixture:${error.message}`],
      });
      continue;
    }
    let expected = expectedRevisions.get(result.themeId);
    if (!expected) {
      const themeDirectory = contracts.themeDirectories.get(result.themeId);
      const loaded = await loadPayload(themeDirectory);
      expected = {
        payloadRevision: loaded.payloadRevision,
        styleRevision: loaded.styleRevision,
      };
      expectedRevisions.set(result.themeId, expected);
    }
    const reasons = [...result.reasons];
    if (fixture.payloadRevision !== expected.payloadRevision) {
      reasons.push("stale-payload-revision");
    }
    if (fixture.styleRevision !== expected.styleRevision) {
      reasons.push("stale-style-revision");
    }
    const cell = `${result.themeId}/${result.state}/${result.viewportId}`;
    if (covered.has(cell) || rejectedFixtures.some((entry) => entry.cell === cell)) {
      throw new Error(`Duplicate fixture evidence for ${cell}`);
    }
    if (result.eligibleForCoverage && reasons.length === 0) covered.set(cell, fileName);
    else rejectedFixtures.push({ file: fileName, cell, reasons });
  }

  const cellsPerTheme = contracts.matrix.states.length * contracts.matrix.viewports.length;
  const themeCoverage = selectedThemes.map((themeId) => {
    const expectedCells = contracts.matrix.states.flatMap((state) =>
      contracts.matrix.viewports.map((viewport) => `${themeId}/${state.id}/${viewport.id}`));
    const missingCells = expectedCells.filter((cell) => !covered.has(cell));
    const themeRejected = rejectedFixtures.filter((entry) => entry.cell.startsWith(`${themeId}/`));
    return {
      themeId,
      qaStatus: missingCells.length === 0 && themeRejected.length === 0 ? "PASS" : "PARTIAL",
      covered: expectedCells.length - missingCells.length,
      required: expectedCells.length,
      missingCellCount: missingCells.length,
      rejectedFixtureCount: themeRejected.length,
      missingCells: missingCells.map((cell) => cell.split("/").slice(1).join("/")),
      stateCoverage: contracts.matrix.states.map((state) => {
        const stateCells = contracts.matrix.viewports.map(
          (viewport) => `${themeId}/${state.id}/${viewport.id}`,
        );
        return {
          state: state.id,
          covered: stateCells.filter((cell) => covered.has(cell)).length,
          required: stateCells.length,
        };
      }),
    };
  });
  const complete = themeCoverage.every((theme) => theme.qaStatus === "PASS");
  const selectedCoveredCells = [...covered.keys()]
    .filter((cell) => selectedThemes.some((themeId) => cell.startsWith(`${themeId}/`)))
    .sort();
  const filteredRejected = rejectedFixtures.filter(
    (entry) => selectedThemes.some((themeId) => entry.cell.startsWith(`${themeId}/`)),
  );
  return {
    pass: true,
    qaStatus: complete ? "PASS" : "PARTIAL",
    privacy: FIXTURE_PRIVACY,
    evidenceHash: evidenceHash.digest("hex"),
    fixtureCount: selectedFixtureCount,
    acceptedFixtureCount: selectedCoveredCells.length,
    requiredCellCount: selectedThemes.length * cellsPerTheme,
    coveredCells: selectedCoveredCells.map((cell) =>
      themeFilter ? cell.split("/").slice(1).join("/") : cell),
    missingCellCount: themeCoverage.reduce((total, theme) => total + theme.missingCellCount, 0),
    rejectedFixtures: filteredRejected,
    unverifiedThemes: themeCoverage
      .filter((theme) => theme.qaStatus !== "PASS")
      .map((theme) => theme.themeId),
    themeCoverage: themeCoverage.map((theme) => ({
      themeId: theme.themeId,
      qaStatus: theme.qaStatus,
      covered: theme.covered,
      required: theme.required,
      missingCellCount: theme.missingCellCount,
      rejectedFixtureCount: theme.rejectedFixtureCount,
    })),
    ...(themeFilter ? {
      themeId: themeFilter,
      missingCells: themeCoverage[0].missingCells,
      stateCoverage: themeCoverage[0].stateCoverage,
    } : {}),
  };
}

function parseCli(argv) {
  const options = { action: "audit", root: defaultSkillRoot, fixture: null, themeId: null };
  if (argv[0] && !argv[0].startsWith("--")) options.action = argv.shift();
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--root") options.root = path.resolve(argv[++index]);
    else if (argument === "--fixture") options.fixture = path.resolve(argv[++index]);
    else if (argument === "--theme") options.themeId = String(argv[++index]);
    else throw new Error(`Unknown argument: ${argument}`);
  }
  if (!["audit", "validate"].includes(options.action)) {
    throw new Error(`Unknown action: ${options.action}`);
  }
  if (options.action === "validate" && !options.fixture) {
    throw new Error("validate requires --fixture");
  }
  return options;
}

async function main() {
  const options = parseCli(process.argv.slice(2));
  const result = options.action === "validate"
    ? await validateFixtureFile(options.fixture, options.root)
    : await auditFixtureCoverage(options.root, { themeId: options.themeId });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${JSON.stringify({ pass: false, error: error.message })}\n`);
    process.exitCode = 1;
  });
}
