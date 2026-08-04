import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { loadSelectorContract } from "./selector-contract.mjs";

const ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const PUBLISHER_PATTERN = /^[a-z0-9][a-z0-9._-]{1,63}$/;
const HASH_PATTERN = /^[0-9a-f]{64}$/;
const TRUST_TIERS = new Set(["builtin", "local-private", "external"]);
const DISTRIBUTIONS = new Set(["redistributable", "excluded"]);
const COMPATIBILITY_STATUSES = new Set(["PASS", "PARTIAL", "UNSUPPORTED"]);
const COMPATIBILITY_EVIDENCE_KIND = "native-structure-probe";
const COMPATIBILITY_EVIDENCE_CONTRACT_ID = "codex-theme-compatibility-evidence";
const SIGNATURE_FILE = "theme.signature.json";

async function readJson(file) {
  const value = JSON.parse(await fs.readFile(file, "utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${file} must contain a JSON object`);
  }
  return value;
}

function assertObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value;
}

function assertStringArray(value, label, allowed = null) {
  if (!Array.isArray(value) || !value.length ||
      value.some((item) => typeof item !== "string" || !item)) {
    throw new Error(`${label} must be a non-empty string array`);
  }
  if (new Set(value).size !== value.length) throw new Error(`${label} must be unique`);
  if (allowed && value.some((item) => !allowed.has(item))) {
    throw new Error(`${label} contains an unsupported value`);
  }
  return value;
}

export function validateTrustPolicy(policy) {
  assertObject(policy, "theme trust policy");
  if (policy.schemaVersion !== 1 || policy.contractId !== "codex-theme-trust-policy") {
    throw new Error("theme trust policy has an unsupported identity");
  }
  assertObject(policy.registry, "theme trust policy.registry");
  assertStringArray(policy.registry.allowedTiers, "theme trust policy.registry.allowedTiers", TRUST_TIERS);
  assertStringArray(
    policy.registry.allowedDistribution,
    "theme trust policy.registry.allowedDistribution",
    DISTRIBUTIONS,
  );
  assertObject(policy.externalPackages, "theme trust policy.externalPackages");
  if (policy.externalPackages.signatureRequired !== true ||
      policy.externalPackages.algorithm !== "ed25519" ||
      policy.externalPackages.signatureFile !== SIGNATURE_FILE) {
    throw new Error("external theme packages must require Ed25519 signatures");
  }
  assertObject(policy.assets, "theme trust policy.assets");
  if (policy.assets.remoteResources !== "forbidden" ||
      !Number.isInteger(policy.assets.maximumFileBytes) ||
      !Number.isInteger(policy.assets.maximumPackageBytes) ||
      !Number.isInteger(policy.assets.maximumFiles) ||
      !Number.isInteger(policy.assets.maximumDepth) ||
      !Number.isInteger(policy.assets.maximumDimension) ||
      !Number.isInteger(policy.assets.maximumPixels)) {
    throw new Error("theme trust policy asset limits are invalid");
  }
  assertStringArray(policy.assets.allowedExtensions, "theme trust policy.assets.allowedExtensions");
  if (new Set(policy.assets.allowedExtensions).size !== policy.assets.allowedExtensions.length ||
      policy.assets.allowedExtensions.some((extension) =>
        !/^\.[a-z0-9]+$/.test(extension))) {
    throw new Error("theme trust policy allowed extensions must be unique lowercase suffixes");
  }
  if (policy.assets.licenseRequired !== true || policy.assets.provenanceRequired !== true) {
    throw new Error("theme trust policy must require license and provenance");
  }
  return policy;
}

function versionParts(value, label) {
  if (typeof value !== "string" || !/^\d+(?:\.\d+){1,3}$/.test(value)) {
    throw new Error(`${label} must be a dotted numeric version`);
  }
  return value.split(".").map(Number).concat([0, 0, 0, 0]).slice(0, 4);
}

function compareVersions(first, second) {
  const a = versionParts(first, "version");
  const b = versionParts(second, "version");
  for (let index = 0; index < 4; index += 1) {
    if (a[index] !== b[index]) return a[index] < b[index] ? -1 : 1;
  }
  return 0;
}

export function validateCompatibilityMatrix(matrix) {
  assertObject(matrix, "compatibility matrix");
  if (matrix.schemaVersion !== 1 ||
      matrix.contractId !== "codex-theme-platform-compatibility") {
    throw new Error("compatibility matrix has an unsupported identity");
  }
  assertObject(matrix.contracts, "compatibility matrix.contracts");
  if (matrix.contracts.compatibilityProbe !== 1) {
    throw new Error("compatibility matrix requires compatibilityProbe contract 1");
  }
  assertObject(matrix.platforms, "compatibility matrix.platforms");
  for (const platform of ["windows", "macos"]) {
    const definition = assertObject(
      matrix.platforms[platform],
      `compatibility matrix.platforms.${platform}`,
    );
    if (!Array.isArray(definition.builds) || !Array.isArray(definition.families)) {
      throw new Error(`compatibility matrix ${platform} requires builds and families`);
    }
    const buildVersions = new Set();
    for (const build of definition.builds) {
      assertObject(build, `compatibility matrix ${platform} build`);
      versionParts(build.version, `compatibility matrix ${platform} build.version`);
      if (buildVersions.has(build.version)) {
        throw new Error(`compatibility matrix ${platform} has duplicate build ${build.version}`);
      }
      buildVersions.add(build.version);
      if (!COMPATIBILITY_STATUSES.has(build.status) || build.status === "UNSUPPORTED" ||
          typeof build.evidence !== "string" || !build.evidence) {
        throw new Error(`compatibility matrix ${platform} build is invalid`);
      }
      const hasProbeEvidence = build.evidenceKind !== undefined ||
        build.evidenceFile !== undefined || build.evidenceSha256 !== undefined;
      if (hasProbeEvidence && (build.status !== "PARTIAL" ||
          build.evidenceKind !== COMPATIBILITY_EVIDENCE_KIND ||
          typeof build.evidenceFile !== "string" ||
          !/^compatibility-evidence\/[a-z0-9.-]+\.json$/.test(build.evidenceFile) ||
          typeof build.evidenceSha256 !== "string" ||
          !HASH_PATTERN.test(build.evidenceSha256))) {
        throw new Error(`compatibility matrix ${platform} probe evidence is invalid`);
      }
    }
    for (const family of definition.families) {
      assertObject(family, `compatibility matrix ${platform} family`);
      versionParts(family.minimum, `compatibility matrix ${platform} family.minimum`);
      versionParts(
        family.maximumExclusive,
        `compatibility matrix ${platform} family.maximumExclusive`,
      );
      if (compareVersions(family.minimum, family.maximumExclusive) >= 0 ||
          !COMPATIBILITY_STATUSES.has(family.status) ||
          family.status === "PASS") {
        throw new Error(`compatibility matrix ${platform} family is invalid`);
      }
    }
    if (!COMPATIBILITY_STATUSES.has(definition.unknownBuildStatus)) {
      throw new Error(`compatibility matrix ${platform} unknownBuildStatus is invalid`);
    }
  }
  return matrix;
}

export function evaluateCompatibility(matrix, platform, version) {
  validateCompatibilityMatrix(matrix);
  const key = String(platform).toLowerCase();
  const definition = matrix.platforms[key];
  if (!definition) {
    return {
      pass: false,
      status: "UNSUPPORTED",
      platform: key,
      version,
      reason: "unknown-platform",
    };
  }
  versionParts(version, "Codex version");
  const exact = definition.builds.find((build) => build.version === version);
  if (exact) {
    return {
      pass: exact.status !== "UNSUPPORTED",
      status: exact.status,
      platform: key,
      version,
      evidence: exact.evidence,
      evidenceKind: exact.evidenceKind ?? null,
      evidenceFile: exact.evidenceFile ?? null,
      evidenceSha256: exact.evidenceSha256 ?? null,
      matchedBy: "exact-build",
    };
  }
  const family = definition.families.find((item) =>
    compareVersions(version, item.minimum) >= 0 &&
    compareVersions(version, item.maximumExclusive) < 0);
  if (family) {
    return {
      pass: family.status !== "UNSUPPORTED",
      status: family.status,
      platform: key,
      version,
      evidence: family.evidence,
      matchedBy: "version-family",
      range: {
        minimum: family.minimum,
        maximumExclusive: family.maximumExclusive,
      },
    };
  }
  return {
    pass: definition.unknownBuildStatus !== "UNSUPPORTED",
    status: definition.unknownBuildStatus,
    platform: key,
    version,
    reason: "outside-supported-families",
    matchedBy: "fallback-policy",
  };
}

function assertAllowedKeys(value, allowed, label) {
  for (const key of Object.keys(assertObject(value, label))) {
    if (!allowed.has(key)) throw new Error(`${label} contains forbidden field: ${key}`);
  }
}

function assertBoolean(value, label) {
  if (typeof value !== "boolean") throw new Error(`${label} must be boolean`);
}

function assertNonnegativeInteger(value, label) {
  if (!Number.isInteger(value) || value < 0) throw new Error(`${label} must be a nonnegative integer`);
}

function validateCompatibilityEvidenceDocument(
  evidence,
  platform,
  build,
  selectorContract,
  selectorContractHash,
  probeContractVersion,
) {
  const topLevelKeys = new Set([
    "schemaVersion", "contractId", "probeContractVersion", "pass", "compatible", "status",
    "mutationPerformed", "platform", "codexVersion", "capturedAt", "engineVersion", "port",
    "privacy", "selectorContract", "appTargetCount", "qualifiedTargetCount",
    "failedTargetCount", "targets",
  ]);
  assertAllowedKeys(evidence, topLevelKeys, "compatibility evidence");
  if (evidence.schemaVersion !== 1 ||
      evidence.contractId !== COMPATIBILITY_EVIDENCE_CONTRACT_ID ||
      evidence.probeContractVersion !== probeContractVersion ||
      evidence.pass !== true || evidence.compatible !== true ||
      evidence.status !== "PARTIAL" || evidence.mutationPerformed !== false ||
      evidence.platform !== platform || evidence.codexVersion !== build.version ||
      typeof evidence.capturedAt !== "string" || !Number.isFinite(Date.parse(evidence.capturedAt)) ||
      typeof evidence.engineVersion !== "string" || !/^\d+\.\d+\.\d+$/.test(evidence.engineVersion) ||
      !Number.isInteger(evidence.port) || evidence.port < 1024 || evidence.port > 65535) {
    throw new Error(`compatibility evidence identity is invalid for ${platform} ${build.version}`);
  }

  const privacyKeys = new Set([
    "structureOnly", "textCaptured", "titlesCaptured", "urlsCaptured", "inputValuesCaptured",
    "filePathsCaptured", "htmlCaptured",
  ]);
  assertAllowedKeys(evidence.privacy, privacyKeys, "compatibility evidence.privacy");
  if (evidence.privacy.structureOnly !== true ||
      ["textCaptured", "titlesCaptured", "urlsCaptured", "inputValuesCaptured",
        "filePathsCaptured", "htmlCaptured"].some((key) => evidence.privacy[key] !== false)) {
    throw new Error("compatibility evidence violates the structure-only privacy boundary");
  }

  const selectorKeys = new Set(["contractId", "schemaVersion", "hash", "verifiedAgainst"]);
  assertAllowedKeys(evidence.selectorContract, selectorKeys, "compatibility evidence.selectorContract");
  if (evidence.selectorContract.contractId !== selectorContract.contractId ||
      evidence.selectorContract.schemaVersion !== selectorContract.schemaVersion ||
      evidence.selectorContract.hash !== selectorContractHash) {
    throw new Error("compatibility evidence was captured against a stale selector contract");
  }
  assertAllowedKeys(
    evidence.selectorContract.verifiedAgainst,
    new Set(["platform", "codexVersion", "verifiedAt"]),
    "compatibility evidence.selectorContract.verifiedAgainst",
  );
  if (evidence.selectorContract.verifiedAgainst.platform !== platform ||
      evidence.selectorContract.verifiedAgainst.codexVersion !== build.version ||
      typeof evidence.selectorContract.verifiedAgainst.verifiedAt !== "string" ||
      !/^\d{4}-\d{2}-\d{2}$/.test(evidence.selectorContract.verifiedAgainst.verifiedAt)) {
    throw new Error("compatibility evidence selector provenance is invalid");
  }

  for (const key of ["appTargetCount", "qualifiedTargetCount", "failedTargetCount"]) {
    assertNonnegativeInteger(evidence[key], `compatibility evidence.${key}`);
  }
  if (evidence.qualifiedTargetCount < 1 ||
      evidence.qualifiedTargetCount > evidence.appTargetCount ||
      evidence.failedTargetCount > evidence.appTargetCount ||
      !Array.isArray(evidence.targets) ||
      evidence.targets.length !== evidence.qualifiedTargetCount) {
    throw new Error("compatibility evidence target counts are invalid");
  }

  const targetKeys = new Set([
    "documentReady", "rendererCandidate", "requiredPass", "requiredParts", "avatarSignals",
    "themeMarkerPresent", "scope", "viewport", "overflowX", "preferences", "parts",
  ]);
  const partKeys = new Set([
    "tier", "matchedCount", "resolvedCount", "visibleCount", "required", "pass",
  ]);
  const expectedRequiredParts = Object.entries(selectorContract.parts)
    .filter(([, definition]) => definition.required === true)
    .map(([part]) => part)
    .sort();
  for (const [targetIndex, target] of evidence.targets.entries()) {
    const label = `compatibility evidence.targets[${targetIndex}]`;
    assertAllowedKeys(target, targetKeys, label);
    for (const key of ["documentReady", "rendererCandidate", "requiredPass", "avatarSignals",
      "themeMarkerPresent", "overflowX"]) assertBoolean(target[key], `${label}.${key}`);
    if (!target.documentReady || !target.rendererCandidate || !target.requiredPass || target.avatarSignals ||
        !new Set(["home", "thread", "settings"]).has(target.scope) ||
        !Array.isArray(target.requiredParts) ||
        JSON.stringify([...target.requiredParts].sort()) !== JSON.stringify(expectedRequiredParts)) {
      throw new Error(`${label} does not prove a supported Codex renderer`);
    }
    assertAllowedKeys(target.viewport, new Set(["width", "height", "deviceScaleFactor"]), `${label}.viewport`);
    if (!Number.isInteger(target.viewport.width) || target.viewport.width <= 0 ||
        !Number.isInteger(target.viewport.height) || target.viewport.height <= 0 ||
        typeof target.viewport.deviceScaleFactor !== "number" ||
        !Number.isFinite(target.viewport.deviceScaleFactor) || target.viewport.deviceScaleFactor <= 0) {
      throw new Error(`${label}.viewport is invalid`);
    }
    assertAllowedKeys(
      target.preferences,
      new Set(["reducedMotion", "increasedContrast", "forcedColors"]),
      `${label}.preferences`,
    );
    for (const key of ["reducedMotion", "increasedContrast", "forcedColors"]) {
      assertBoolean(target.preferences[key], `${label}.preferences.${key}`);
    }
    assertObject(target.parts, `${label}.parts`);
    if (JSON.stringify(Object.keys(target.parts).sort()) !==
        JSON.stringify(Object.keys(selectorContract.parts).sort())) {
      throw new Error(`${label}.parts does not match the selector contract`);
    }
    for (const [part, result] of Object.entries(target.parts)) {
      const partLabel = `${label}.parts.${part}`;
      assertAllowedKeys(result, partKeys, partLabel);
      if (![null, "L1", "L2"].includes(result.tier)) throw new Error(`${partLabel}.tier is invalid`);
      for (const key of ["matchedCount", "resolvedCount", "visibleCount"]) {
        assertNonnegativeInteger(result[key], `${partLabel}.${key}`);
      }
      assertBoolean(result.required, `${partLabel}.required`);
      assertBoolean(result.pass, `${partLabel}.pass`);
      if (result.required !== (selectorContract.parts[part].required === true) ||
          (result.required && (result.tier === null || result.resolvedCount !== 1 ||
            result.visibleCount !== 1 || result.pass !== true))) {
        throw new Error(`${partLabel} does not satisfy the required selector contract`);
      }
    }
  }
  return evidence;
}

export async function validateCompatibilityEvidence(root, matrix, selectorContract, selectorContractHash) {
  const probeContractVersion = matrix.contracts.compatibilityProbe;
  const evidence = [];
  const evidenceRoot = path.resolve(root, "assets", "compatibility-evidence");
  for (const platform of ["windows", "macos"]) {
    for (const build of matrix.platforms[platform].builds) {
      if (!build.evidenceFile) continue;
      const file = path.resolve(root, "assets", build.evidenceFile);
      const relative = path.relative(evidenceRoot, file);
      if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
        throw new Error(`compatibility evidence must stay inside ${evidenceRoot}`);
      }
      const bytes = await fs.readFile(file);
      const digest = crypto.createHash("sha256").update(bytes).digest("hex");
      if (digest !== build.evidenceSha256) {
        throw new Error(`compatibility evidence hash mismatch: ${build.evidenceFile}`);
      }
      const document = JSON.parse(bytes.toString("utf8"));
      validateCompatibilityEvidenceDocument(
        document,
        platform,
        build,
        selectorContract,
        selectorContractHash,
        probeContractVersion,
      );
      evidence.push({ platform, version: build.version, file, digest, document });
    }
  }
  return evidence;
}

async function collectPackageFiles(
  directory,
  policy,
  base = directory,
  depth = 0,
  budget = { files: 0, bytes: 0 },
) {
  if (depth > policy.assets.maximumDepth) {
    throw new Error(`Theme package exceeds the maximum directory depth: ${directory}`);
  }
  const files = [];
  const allowedExtensions = new Set(
    policy.assets.allowedExtensions.map((extension) => extension.toLowerCase()),
  );
  for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
    if (entry.name === SIGNATURE_FILE && path.resolve(directory) === path.resolve(base)) continue;
    const absolute = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) {
      throw new Error(`Theme package cannot contain symbolic links: ${absolute}`);
    }
    if (entry.isDirectory()) {
      files.push(...await collectPackageFiles(absolute, policy, base, depth + 1, budget));
    } else if (entry.isFile()) {
      const extension = path.extname(entry.name).toLowerCase();
      if (!allowedExtensions.has(extension)) {
        throw new Error(`Theme package file extension is not allowed: ${absolute}`);
      }
      const stat = await fs.stat(absolute);
      if (stat.size > policy.assets.maximumFileBytes) {
        throw new Error(`Theme package file exceeds the governance limit: ${absolute}`);
      }
      budget.files += 1;
      budget.bytes += stat.size;
      if (budget.files > policy.assets.maximumFiles) {
        throw new Error(`Theme package exceeds the ${policy.assets.maximumFiles}-file limit`);
      }
      if (budget.bytes > policy.assets.maximumPackageBytes) {
        throw new Error("Theme package exceeds the governance package-size limit");
      }
      files.push({
        absolute,
        relative: path.relative(base, absolute).split(path.sep).join("/"),
        size: stat.size,
      });
    } else {
      throw new Error(`Theme package contains an unsupported filesystem entry: ${absolute}`);
    }
  }
  return files;
}

export async function computeThemePackageHash(directory, policy) {
  validateTrustPolicy(policy);
  const root = await fs.realpath(directory);
  const files = await collectPackageFiles(root, policy);
  if (!files.length || files.length > policy.assets.maximumFiles) {
    throw new Error(`Theme package must contain 1-${policy.assets.maximumFiles} files`);
  }
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  if (totalBytes > policy.assets.maximumPackageBytes) {
    throw new Error("Theme package exceeds the governance package-size limit");
  }
  const hash = crypto.createHash("sha256");
  for (const file of files.sort((first, second) =>
    first.relative < second.relative ? -1 : first.relative > second.relative ? 1 : 0)) {
    const content = await fs.readFile(file.absolute);
    hash.update(file.relative);
    hash.update("\0");
    hash.update(String(content.length));
    hash.update("\0");
    hash.update(content);
    hash.update("\0");
  }
  return {
    algorithm: "sha256",
    hash: hash.digest("hex"),
    fileCount: files.length,
    totalBytes,
    files: files.map(({ relative, size }) => ({ relative, size })),
  };
}

export function validateTrustedPublisherStore(trustStore) {
  assertObject(trustStore, "trusted publisher store");
  if (trustStore.schemaVersion !== 1 || !Array.isArray(trustStore.publishers) ||
      trustStore.publishers.length > 128) {
    throw new Error("trusted publisher store has an unsupported schema");
  }
  const publisherIds = new Set();
  for (const [index, publisher] of trustStore.publishers.entries()) {
    assertObject(publisher, `trusted publisher store.publishers[${index}]`);
    if (!PUBLISHER_PATTERN.test(String(publisher.id ?? "")) ||
        publisher.algorithm !== "ed25519" ||
        typeof publisher.publicKeyPem !== "string") {
      throw new Error(`trusted publisher store publisher ${index} is invalid`);
    }
    if (publisherIds.has(publisher.id)) {
      throw new Error(`trusted publisher store has duplicate publisher: ${publisher.id}`);
    }
    publisherIds.add(publisher.id);
    let publicKey;
    try {
      publicKey = crypto.createPublicKey(publisher.publicKeyPem);
    } catch {
      throw new Error(`trusted publisher has an invalid public key: ${publisher.id}`);
    }
    if (publicKey.type !== "public" || publicKey.asymmetricKeyType !== "ed25519") {
      throw new Error(`trusted publisher key must be Ed25519 public material: ${publisher.id}`);
    }
  }
  return trustStore;
}

export function validateRegistryDeclaration(entry) {
  assertObject(entry, "theme registry entry");
  if (typeof entry.id !== "string" || !ID_PATTERN.test(entry.id)) {
    throw new Error("theme registry entry id is invalid");
  }
  const trust = assertObject(entry.trust, `${entry.id}.trust`);
  if (!TRUST_TIERS.has(trust.tier)) {
    throw new Error(`${entry.id}.trust.tier is invalid`);
  }
  if (!DISTRIBUTIONS.has(trust.distribution)) {
    throw new Error(`${entry.id}.trust.distribution is invalid`);
  }
  if (trust.tier === "local-private" && trust.distribution !== "excluded") {
    throw new Error(`${entry.id} local-private themes must be excluded from distribution`);
  }
  if (typeof trust.reason !== "string" || trust.reason.trim().length < 8) {
    throw new Error(`${entry.id}.trust.reason must explain the trust boundary`);
  }
  return {
    tier: trust.tier,
    distribution: trust.distribution,
    reason: trust.reason.trim(),
  };
}

function redistributionBlocked(loaded) {
  const declarations = [
    loaded.theme?.metadata?.license,
    ...Object.values(loaded.theme?.art?.variants ?? {}).map((asset) => asset?.license),
    loaded.theme?.art?.license,
  ].filter(Boolean);
  return declarations.some((value) =>
    /personal\s+local\s+use|redistribution\s+is\s+not\s+authorized|all\s+rights\s+reserved/i
      .test(String(value)));
}

export async function verifyDetachedThemeSignature(
  directory,
  signatureFile,
  trustStore,
  policy,
) {
  validateTrustPolicy(policy);
  validateTrustedPublisherStore(trustStore);
  const descriptor = await readJson(signatureFile);
  if (descriptor.schemaVersion !== 1 ||
      descriptor.algorithm !== policy.externalPackages.algorithm ||
      !PUBLISHER_PATTERN.test(String(descriptor.publisherId ?? "")) ||
      !HASH_PATTERN.test(String(descriptor.payloadHash ?? "")) ||
      typeof descriptor.signature !== "string" ||
      typeof descriptor.createdAt !== "string" ||
      !Number.isFinite(Date.parse(descriptor.createdAt))) {
    throw new Error("theme signature descriptor is invalid");
  }
  const publisher = trustStore.publishers.find((item) => item.id === descriptor.publisherId);
  if (!publisher || publisher.algorithm !== "ed25519" ||
      typeof publisher.publicKeyPem !== "string") {
    throw new Error(`Theme publisher is not trusted: ${descriptor.publisherId}`);
  }
  const packageEvidence = await computeThemePackageHash(directory, policy);
  if (packageEvidence.hash !== descriptor.payloadHash) {
    throw new Error("theme signature payload hash does not match the package");
  }
  const encodedSignature = descriptor.signature.trim();
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/
    .test(encodedSignature)) {
    throw new Error("theme signature is not valid base64");
  }
  const signature = Buffer.from(encodedSignature, "base64");
  if (signature.length !== 64 || signature.toString("base64") !== encodedSignature) {
    throw new Error("theme signature is not canonical base64");
  }
  const verified = crypto.verify(
    null,
    Buffer.from(packageEvidence.hash, "hex"),
    publisher.publicKeyPem,
    signature,
  );
  if (!verified) throw new Error("theme package signature verification failed");
  return {
    pass: true,
    status: "trusted-signed",
    publisherId: descriptor.publisherId,
    payloadHash: packageEvidence.hash,
    algorithm: descriptor.algorithm,
    packageEvidence,
  };
}

export async function createDetachedThemeSignature(
  directory,
  privateKeyFile,
  publisherId,
  policy,
) {
  validateTrustPolicy(policy);
  if (!PUBLISHER_PATTERN.test(String(publisherId ?? ""))) {
    throw new Error("publisher id is invalid");
  }
  const [packageEvidence, privateKeyPem] = await Promise.all([
    computeThemePackageHash(directory, policy),
    fs.readFile(privateKeyFile, "utf8"),
  ]);
  const key = crypto.createPrivateKey(privateKeyPem);
  if (key.asymmetricKeyType !== "ed25519") {
    throw new Error("theme signing requires an Ed25519 private key");
  }
  const signature = crypto.sign(
    null,
    Buffer.from(packageEvidence.hash, "hex"),
    key,
  ).toString("base64");
  return {
    schemaVersion: 1,
    algorithm: "ed25519",
    publisherId,
    payloadHash: packageEvidence.hash,
    signature,
    createdAt: new Date().toISOString(),
  };
}

export async function auditCatalogRegistry(catalog, manifest, loadedThemes, policy, trustStore) {
  validateTrustPolicy(policy);
  if (manifest.version !== 2 || !Array.isArray(manifest.themes)) {
    throw new Error("theme registry requires catalog manifest version 2");
  }
  const loadedById = new Map(loadedThemes.map((theme) => [theme.id, theme]));
  const themes = [];
  for (const entry of manifest.themes) {
    const declaration = validateRegistryDeclaration(entry);
    const loaded = loadedById.get(entry.id);
    if (!loaded) throw new Error(`Registry theme was not validated: ${entry.id}`);
    if (declaration.distribution === "redistributable" && redistributionBlocked(loaded)) {
      throw new Error(`${entry.id} cannot be marked redistributable under its declared licenses`);
    }
    const packageEvidence = await computeThemePackageHash(loaded.directory, policy);
    let trustEvidence;
    if (declaration.tier === "external") {
      const signatureFile = path.join(loaded.directory, SIGNATURE_FILE);
      trustEvidence = await verifyDetachedThemeSignature(
        loaded.directory,
        signatureFile,
        trustStore,
        policy,
      );
    } else {
      trustEvidence = {
        pass: true,
        status: declaration.tier === "builtin"
          ? "trusted-canonical-registry"
          : "trusted-local-only",
        payloadHash: packageEvidence.hash,
      };
    }
    themes.push({
      id: entry.id,
      trust: declaration,
      trustEvidence,
      package: {
        algorithm: packageEvidence.algorithm,
        hash: packageEvidence.hash,
        fileCount: packageEvidence.fileCount,
        totalBytes: packageEvidence.totalBytes,
      },
      assets: loaded.assets ?? [],
    });
  }
  return {
    pass: true,
    contractVersion: 1,
    catalog: await fs.realpath(catalog),
    signatureRequiredForExternal: policy.externalPackages.signatureRequired,
    themes,
    distribution: {
      redistributable: themes
        .filter((theme) => theme.trust.distribution === "redistributable")
        .map((theme) => theme.id),
      excluded: themes
        .filter((theme) => theme.trust.distribution === "excluded")
        .map((theme) => theme.id),
    },
  };
}

export async function loadGovernance(root) {
  const [trustPolicy, trustStore, compatibility, selectorSource] = await Promise.all([
    readJson(path.join(root, "assets", "theme-trust-policy.json")),
    readJson(path.join(root, "assets", "trusted-publishers.json")),
    readJson(path.join(root, "assets", "compatibility.json")),
    loadSelectorContract(root),
  ]);
  validateTrustPolicy(trustPolicy);
  validateCompatibilityMatrix(compatibility);
  validateTrustedPublisherStore(trustStore);
  const compatibilityEvidence = await validateCompatibilityEvidence(
    root,
    compatibility,
    selectorSource.contract,
    selectorSource.compatibilityHash,
  );
  return { trustPolicy, trustStore, compatibility, compatibilityEvidence };
}
