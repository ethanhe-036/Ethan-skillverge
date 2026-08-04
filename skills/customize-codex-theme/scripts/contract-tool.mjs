import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { auditFixtureCoverage } from "./fixture-tool.mjs";
import { loadGovernance } from "./theme-governance.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const skillRoot = path.resolve(here, "..");
const PART_PATTERN = /^[a-z][a-z0-9-]{0,31}$/;
const ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const ALLOWED_CARDINALITY = new Set(["one", "many"]);
const ALLOWED_TIERS = new Set(["L1", "L2"]);
const HASH_EXCLUSIONS = new Set(["skill.manifest.json"]);

function isHashExcluded(relative) {
  return HASH_EXCLUSIONS.has(relative) || /^fixtures\/codex-.*\.json$/i.test(relative);
}

async function readJson(file) {
  const value = JSON.parse(await fs.readFile(file, "utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${file} must contain a JSON object`);
  }
  return value;
}

function validateSelector(selector, label) {
  if (typeof selector !== "string" || !selector.trim() || selector.length > 300) {
    throw new Error(`${label} must be a non-empty selector no longer than 300 characters`);
  }
  if (/:nth-(?:child|of-type)\s*\(|\[style\*?=|body\s*>\s*div\s*>\s*div/i.test(selector)) {
    throw new Error(`${label} uses a forbidden brittle selector`);
  }
}

async function collectFiles(directory, base = directory) {
  const files = [];
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    const relative = path.relative(base, absolute).split(path.sep).join("/");
    if (isHashExcluded(relative)) continue;
    if (entry.isDirectory()) files.push(...await collectFiles(absolute, base));
    else if (entry.isFile()) files.push({ absolute, relative });
  }
  return files;
}

export async function computeRuntimeHash(root = skillRoot) {
  const resolvedRoot = await fs.realpath(root);
  const hash = crypto.createHash("sha256");
  const files = await collectFiles(resolvedRoot);
  for (const file of files.sort((first, second) => first.relative.localeCompare(second.relative))) {
    const content = await fs.readFile(file.absolute);
    hash.update(file.relative);
    hash.update("\0");
    hash.update(String(content.length));
    hash.update("\0");
    hash.update(content);
    hash.update("\0");
  }
  return hash.digest("hex");
}

export async function validateRuntimeContracts(root = skillRoot) {
  const selectorFile = path.join(root, "assets", "selectors.json");
  const policyFile = path.join(root, "assets", "safe-css-policy.json");
  const matrixFile = path.join(root, "fixtures", "surface-matrix.json");
  const [selectors, policy, matrix, governance] = await Promise.all([
    readJson(selectorFile),
    readJson(policyFile),
    readJson(matrixFile),
    loadGovernance(root),
  ]);

  if (selectors.schemaVersion !== 1 || selectors.contractId !== "codex-theme-runtime-surfaces") {
    throw new Error("selectors.json has an unsupported contract identity");
  }
  if (!selectors.parts || typeof selectors.parts !== "object" || Array.isArray(selectors.parts)) {
    throw new Error("selectors.json parts must be an object");
  }

  const parts = new Set();
  let selectorCount = 0;
  for (const [part, definition] of Object.entries(selectors.parts)) {
    if (!PART_PATTERN.test(part)) throw new Error(`Invalid semantic part name: ${part}`);
    if (!definition || typeof definition !== "object" || Array.isArray(definition)) {
      throw new Error(`selectors.parts.${part} must be an object`);
    }
    if (!ALLOWED_CARDINALITY.has(definition.cardinality)) {
      throw new Error(`selectors.parts.${part}.cardinality must be one or many`);
    }
    if (typeof definition.required !== "boolean") {
      throw new Error(`selectors.parts.${part}.required must be boolean`);
    }
    if (!definition.tiers || typeof definition.tiers !== "object" || Array.isArray(definition.tiers)) {
      throw new Error(`selectors.parts.${part}.tiers must be an object`);
    }
    for (const [tier, tierSelectors] of Object.entries(definition.tiers)) {
      if (!ALLOWED_TIERS.has(tier) || !Array.isArray(tierSelectors) || !tierSelectors.length) {
        throw new Error(`selectors.parts.${part}.tiers.${tier} is invalid`);
      }
      for (const [index, selector] of tierSelectors.entries()) {
        validateSelector(selector, `selectors.parts.${part}.tiers.${tier}[${index}]`);
        selectorCount += 1;
      }
    }
    if (definition.maximum !== undefined &&
        (!Number.isInteger(definition.maximum) || definition.maximum < 1 || definition.maximum > 32)) {
      throw new Error(`selectors.parts.${part}.maximum must be an integer from 1 to 32`);
    }
    parts.add(part);
  }
  const settingsSelectors = Object.values(selectors.parts.settings?.tiers ?? {}).flat();
  if (settingsSelectors.includes("[data-settings-panel-slug]") ||
      !settingsSelectors.includes(
        "aside.app-shell-left-panel:has([data-settings-panel-slug]) ~ [data-app-shell-main-surface]",
      )) {
    throw new Error(
      "settings must map the large content root from its stable navigation markers",
    );
  }

  if (policy.schemaVersion !== 1 || !Array.isArray(policy.semanticParts)) {
    throw new Error("safe-css-policy.json has an unsupported schema");
  }
  const policyParts = new Set(policy.semanticParts);
  if (policyParts.size !== parts.size || [...parts].some((part) => !policyParts.has(part))) {
    throw new Error("safe-css-policy semanticParts must exactly match selectors.json");
  }
  if (policy.continuousComposition?.singleArtOwner !== "workbench") {
    throw new Error("continuous composition must use workbench as its single art owner");
  }
  if (policy.coordinateOwnership?.contractVersion !== 1 ||
      policy.coordinateOwnership?.coordinateSpace !== "semantic-workbench-viewport" ||
      policy.coordinateOwnership?.singleRasterOwner !== "workbench" ||
      policy.coordinateOwnership?.independentPanelRasterOwners !== "forbidden" ||
      policy.coordinateOwnership?.independentPanelCrops !== "forbidden" ||
      policy.coordinateOwnership?.geometryAuthority !== "native" ||
      policy.coordinateOwnership?.interactionAuthority !== "native-only") {
    throw new Error("safe-css-policy coordinate ownership contract is incomplete");
  }

  const requiredHeaderTuning = [
    "--ct-header-material-surface",
    "--ct-header-material-top-tint",
    "--ct-header-material-mid-tint",
    "--ct-header-material-bottom-tint",
    "--ct-header-material-control-tint",
  ];
  if (policy.headerMaterial?.contractVersion !== 1 ||
      policy.headerMaterial?.owner !== "shared-runtime" ||
      policy.headerMaterial?.outerDragSurface !== "transparent" ||
      policy.headerMaterial?.semanticSurface !== "continuous-material-veil" ||
      policy.headerMaterial?.themePackPaintOwnership !== "forbidden" ||
      policy.headerMaterial?.themePackTuning !== "bounded-root-custom-properties-only" ||
      policy.headerMaterial?.accessibilityFallback !== "opaque-native-safe" ||
      JSON.stringify(policy.headerMaterial?.layers) !==
        JSON.stringify(["control-protection", "vertical-veil"]) ||
      JSON.stringify(policy.headerMaterial?.tuningProperties) !==
        JSON.stringify(requiredHeaderTuning)) {
    throw new Error("safe-css-policy shared header material contract is incomplete");
  }

  if (policy.settingsMaterial?.contractVersion !== 1 ||
      policy.settingsMaterial?.owner !== "shared-runtime" ||
      policy.settingsMaterial?.semanticSurface !== "continuous-canvas-material" ||
      policy.settingsMaterial?.defaultTintPercent !== 52 ||
      policy.settingsMaterial?.splitFallbackTintPercent !== 60 ||
      policy.settingsMaterial?.narrowFallbackTintPercent !== 68 ||
      policy.settingsMaterial?.maximumNormalTintPercent !== 68 ||
      policy.settingsMaterial?.themePackMaterialOwnership !==
        "forbidden-for-artwork-themes" ||
      policy.settingsMaterial?.accessibilityFallback !== "opaque-native-safe" ||
      JSON.stringify(policy.settingsMaterial?.protectedOpaqueDescendants) !==
        JSON.stringify(["cards", "fields", "controls", "menus", "dialogs"])) {
    throw new Error("safe-css-policy shared settings material contract is incomplete");
  }

  if (matrix.schemaVersion !== 1 || matrix.contractVersion !== selectors.schemaVersion ||
      !Array.isArray(matrix.states) || !matrix.states.length ||
      !Array.isArray(matrix.viewports) || !matrix.viewports.length) {
    throw new Error("surface-matrix.json has an unsupported schema");
  }
  const stateIds = new Set();
  for (const state of matrix.states) {
    if (!ID_PATTERN.test(String(state?.id ?? "")) ||
        stateIds.has(state.id) ||
        !Array.isArray(state.requiredParts) ||
        !state.requiredParts.length ||
        new Set(state.requiredParts).size !== state.requiredParts.length) {
      throw new Error("surface-matrix states require unique ids and requiredParts");
    }
    for (const part of state.requiredParts) {
      if (!parts.has(part)) throw new Error(`surface-matrix state ${state.id} references unknown part ${part}`);
    }
    stateIds.add(state.id);
  }
  const viewportIds = new Set();
  for (const viewport of matrix.viewports) {
    if (!ID_PATTERN.test(String(viewport?.id ?? "")) ||
        viewportIds.has(viewport.id) ||
        !Number.isInteger(viewport.width) || !Number.isInteger(viewport.height) ||
        viewport.width < 640 || viewport.height < 480 ||
        typeof viewport.scale !== "number" || viewport.scale < 1 || viewport.scale > 4) {
      throw new Error(`surface-matrix viewport ${viewport?.id ?? "unknown"} is invalid`);
    }
    viewportIds.add(viewport.id);
  }

  const manifest = await readJson(path.join(root, "skill.manifest.json"));
  if (manifest.schemaVersion !== 2 ||
      manifest.runtimeContractVersion !== 2 ||
      manifest.authoringContractVersion !== 2 ||
      manifest.compositionCompilerContractVersion !== 1 ||
      manifest.governanceContractVersion !== 1 ||
      manifest.selectorContractVersion !== selectors.schemaVersion ||
      manifest.compatibilityProbeContractVersion !== 1 ||
      manifest.fixtureContractVersion !== 2 ||
      manifest.qaContractVersion !== 14) {
    throw new Error("skill.manifest.json does not match the active runtime contracts");
  }
  if (manifest.canonicalInstall?.strategy !== "codex-home-skill" ||
      manifest.canonicalInstall?.skillName !== "customize-codex-theme" ||
      manifest.canonicalInstall?.environmentOverride !== "CODEX_HOME") {
    throw new Error("skill.manifest.json canonical install policy is not portable");
  }
  const compatibilityContracts = governance.compatibility.contracts;
  const expectedContracts = {
    runtime: manifest.runtimeContractVersion,
    authoring: manifest.authoringContractVersion,
    compositionCompiler: manifest.compositionCompilerContractVersion,
    governance: manifest.governanceContractVersion,
    selector: manifest.selectorContractVersion,
    compatibilityProbe: manifest.compatibilityProbeContractVersion,
    fixture: manifest.fixtureContractVersion,
    runtimeQa: manifest.qaContractVersion,
  };
  for (const [name, expected] of Object.entries(expectedContracts)) {
    if (compatibilityContracts[name] !== expected) {
      throw new Error(`compatibility matrix ${name} contract does not match skill.manifest.json`);
    }
  }
  if (compatibilityContracts.staticQuality !== 4) {
    throw new Error("compatibility matrix static quality contract is unsupported");
  }
  const runtimeHash = await computeRuntimeHash(root);
  if (!/^[0-9a-f]{64}$/.test(manifest.runtimeHash ?? "") || manifest.runtimeHash !== runtimeHash) {
    throw new Error("skill.manifest.json runtimeHash does not match the canonical Skill package");
  }

  const fixtureCoverage = await auditFixtureCoverage(root);
  return {
    pass: true,
    qaStatus: fixtureCoverage.qaStatus,
    contractVersion: selectors.schemaVersion,
    verifiedCodexVersion: selectors.verifiedAgainst?.codexVersion ?? null,
    partCount: parts.size,
    selectorCount,
    runtimeHash,
    states: [...stateIds],
    viewports: matrix.viewports.map(({ id, width, height, scale }) => ({ id, width, height, scale })),
    fixtureCoverage,
    governance: {
      contractVersion: manifest.governanceContractVersion,
      signatureAlgorithm: governance.trustPolicy.externalPackages.algorithm,
      externalSignatureRequired: governance.trustPolicy.externalPackages.signatureRequired,
      compatibilityContractId: governance.compatibility.contractId,
      compatibilityEvidenceCount: governance.compatibilityEvidence.length,
    },
  };
}

async function main() {
  if (process.argv[2] === "--hash") {
    const rootArgument = process.argv[3] ? path.resolve(process.argv[3]) : skillRoot;
    process.stdout.write(`${JSON.stringify({ runtimeHash: await computeRuntimeHash(rootArgument) }, null, 2)}\n`);
    return;
  }
  const rootArgument = process.argv[2] ? path.resolve(process.argv[2]) : skillRoot;
  process.stdout.write(`${JSON.stringify(await validateRuntimeContracts(rootArgument), null, 2)}\n`);
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${JSON.stringify({ pass: false, error: error.message })}\n`);
    process.exitCode = 1;
  });
}
