const THEME_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;

export const AUTHORING_SCHEMA_VERSION = 2;
export const COMPOSITION_COMPILER_CONTRACT_VERSION = 1;

export const EXPERIENCE_MODES = new Set([
  "background",
  "immersive",
  "bounded-showcase",
  "palette-only",
]);

export const ART_TOPOLOGIES = new Set([
  "ambient-full-canvas",
  "edge-focal",
  "dual-anchor",
  "centered-emblem",
  "pattern-texture",
  "portrait-zone",
  "bounded-hero",
  "none",
]);

export const ART_PRESENCE_LEVELS = new Set([
  "immersive-workbench",
  "home-and-empty-space",
  "edge-accent",
  "palette-only",
]);

export const BACKGROUND_SCOPES = new Set([
  "home",
  "thread",
  "right-panel",
  "settings",
]);

export const READING_STRATEGIES = new Set([
  "adaptive-scrim",
  "opaque-reading-zones",
  "minimal-art",
  "protected-surfaces",
]);

export const RESPONSIVE_BEHAVIORS = new Set([
  "preserve",
  "rebalance",
  "reduce",
  "hide",
]);

const DENSITIES = new Set(["low", "medium", "high"]);
const RESPONSIVE_RANK = new Map([
  ["preserve", 0],
  ["rebalance", 1],
  ["reduce", 2],
  ["hide", 3],
]);

const TOPOLOGY_LAYOUT_PLANS = Object.freeze({
  "ambient-full-canvas": Object.freeze({
    anchorStrategy: "freeform-distributed",
    focalZoneRange: Object.freeze({ minimum: 0, maximum: 4 }),
    quietZoneRelationship: "designer-declared",
    rasterization: "compose-into-workbench-canvas",
  }),
  "edge-focal": Object.freeze({
    anchorStrategy: "designer-chosen-edge",
    focalZoneRange: Object.freeze({ minimum: 1, maximum: 2 }),
    quietZoneRelationship: "opposes-primary-edge",
    rasterization: "compose-into-workbench-canvas",
  }),
  "dual-anchor": Object.freeze({
    anchorStrategy: "separated-paired-anchors",
    focalZoneRange: Object.freeze({ minimum: 2, maximum: 4 }),
    quietZoneRelationship: "between-or-around-anchors",
    rasterization: "compose-into-workbench-canvas",
  }),
  "centered-emblem": Object.freeze({
    anchorStrategy: "single-centered-symbol",
    focalZoneRange: Object.freeze({ minimum: 1, maximum: 1 }),
    quietZoneRelationship: "surrounds-emblem",
    rasterization: "compose-into-workbench-canvas",
  }),
  "pattern-texture": Object.freeze({
    anchorStrategy: "non-focal-field",
    focalZoneRange: Object.freeze({ minimum: 0, maximum: 0 }),
    quietZoneRelationship: "uniform-low-salience",
    rasterization: "bake-pattern-into-workbench-canvas",
  }),
  "portrait-zone": Object.freeze({
    anchorStrategy: "bounded-portrait-region",
    focalZoneRange: Object.freeze({ minimum: 1, maximum: 2 }),
    quietZoneRelationship: "adjacent-to-portrait-region",
    rasterization: "compose-source-portrait-into-workbench-canvas",
  }),
  "bounded-hero": Object.freeze({
    anchorStrategy: "bounded-hero-region",
    focalZoneRange: Object.freeze({ minimum: 1, maximum: 2 }),
    quietZoneRelationship: "outside-hero-region",
    rasterization: "compose-into-workbench-canvas",
  }),
  none: Object.freeze({
    anchorStrategy: "none",
    focalZoneRange: Object.freeze({ minimum: 0, maximum: 0 }),
    quietZoneRelationship: "entire-workbench",
    rasterization: "none",
  }),
});

function assertObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value;
}

function designText(value, label, minimum = 3, maximum = 180) {
  if (typeof value !== "string" || value.trim().length < minimum ||
      value.trim().length > maximum || /[\u0000-\u001f]/.test(value)) {
    throw new Error(`${label} must be a ${minimum}-${maximum} character single-line string`);
  }
  return value.trim();
}

function uniqueEnumList(value, label, allowed, minimum = 0, maximum = 8) {
  if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {
    throw new Error(`${label} must contain ${minimum}-${maximum} values`);
  }
  const normalized = value.map((item) => {
    if (typeof item !== "string" || !allowed.has(item)) {
      throw new Error(`${label} contains an unsupported value: ${String(item)}`);
    }
    return item;
  });
  if (new Set(normalized).size !== normalized.length) {
    throw new Error(`${label} must not contain duplicates`);
  }
  return normalized;
}

function boundedNumber(value, label, minimum, maximum) {
  if (typeof value !== "number" || !Number.isFinite(value) ||
      value < minimum || value > maximum) {
    throw new Error(`${label} must be a number from ${minimum} to ${maximum}`);
  }
  return value;
}

function boundedInteger(value, label, minimum, maximum) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label} must be an integer from ${minimum} to ${maximum}`);
  }
  return value;
}

function focalPoint(descriptor) {
  const match = /^(\d+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%$/.exec(
    String(descriptor?.focalPoint ?? ""),
  );
  if (!match) return { x: 50, y: 50 };
  return {
    x: Math.max(0, Math.min(100, Number(match[1]))),
    y: Math.max(0, Math.min(100, Number(match[2]))),
  };
}

function inferArtPresence(theme) {
  if (!theme.art) return "palette-only";
  const averageTint = [
    theme.composition?.sidebarTint,
    theme.composition?.mainTint,
    theme.composition?.headerTint,
    theme.composition?.rightPanelTint,
  ].filter(Number.isFinite).reduce((sum, value, _index, values) =>
    sum + value / values.length, 0);
  if (averageTint && averageTint >= 84) return "edge-accent";
  if (averageTint && averageTint >= 76) return "home-and-empty-space";
  return "immersive-workbench";
}

function inferCreativeBrief(theme) {
  return {
    mood: (theme.aestheticProfile?.moodKeywords ?? ["focused", "coherent", "calm"])
      .slice(0, 5).join(", "),
    visualSignature: theme.aestheticProfile?.signatureMotif ??
      theme.metadata?.style ?? "A restrained visual signature",
    artPresence: inferArtPresence(theme),
  };
}

function responsiveFromCrop(value, fallback) {
  if (value === "hide-art") return "hide";
  if (value === "rebalance-focal") return "rebalance";
  if (value === "reduce-art-before-readability-loss") return "reduce";
  if (value === "preserve-focal") return "preserve";
  return fallback;
}

function densityFromBudget(budget) {
  const accent = Number(budget?.accentAreaPercent ?? 6);
  const secondary = Number(budget?.secondaryMotifs ?? 1);
  if (accent <= 4 && secondary <= 1) return "low";
  if (accent >= 10 || secondary >= 3) return "high";
  return "medium";
}

function normalizedResponsivePolicy(policy) {
  const behaviors = [...RESPONSIVE_RANK.entries()]
    .sort((first, second) => first[1] - second[1])
    .map(([behavior]) => behavior);
  let minimumRank = 0;
  return Object.fromEntries(["wide", "split", "narrow"].map((viewport) => {
    const rank = Math.max(minimumRank, RESPONSIVE_RANK.get(policy[viewport]) ?? 0);
    minimumRank = rank;
    return [viewport, behaviors[rank]];
  }));
}

function inferExperience(theme) {
  const hasArt = Boolean(theme.art);
  const crop = theme.compositionProfile?.artwork?.cropBehavior ?? {};
  const artPresence = inferArtPresence(theme);
  const motifBudget = theme.aestheticProfile?.motifBudget ?? {};
  const responsivePolicy = hasArt ? {
    wide: responsiveFromCrop(crop.wide, "preserve") === "hide"
      ? "preserve"
      : responsiveFromCrop(crop.wide, "preserve"),
    split: responsiveFromCrop(crop.split, "rebalance"),
    narrow: responsiveFromCrop(
      crop.narrow,
      theme.composition?.narrowMode === "hide-art" ? "hide" : "reduce",
    ),
  } : { wide: "hide", split: "hide", narrow: "hide" };
  return {
    mode: !hasArt ? "palette-only"
      : theme.composition?.mode === "portrait-zone" ? "bounded-showcase"
        : artPresence === "immersive-workbench" ? "immersive" : "background",
    artTopology: !hasArt ? "none"
      : theme.composition?.mode === "portrait-zone" ? "portrait-zone"
        : "ambient-full-canvas",
    backgroundScope: hasArt ? ["home", "thread", "right-panel", "settings"] : [],
    readingStrategy: hasArt ? "protected-surfaces" : "minimal-art",
    responsivePolicy: normalizedResponsivePolicy(responsivePolicy),
    decorBudget: {
      density: densityFromBudget(motifBudget),
      dominantMotifs: Number.isInteger(motifBudget.dominantMotifs)
        ? motifBudget.dominantMotifs : 1,
      secondaryMotifs: Number.isInteger(motifBudget.secondaryMotifs)
        ? motifBudget.secondaryMotifs : 1,
      accentAreaPercent: Number.isFinite(motifBudget.accentAreaPercent)
        ? motifBudget.accentAreaPercent : 6,
    },
    interactionAuthority: "native-only",
  };
}

function validateCreativeBrief(value, label) {
  const brief = assertObject(value, label);
  const result = {
    mood: designText(brief.mood, `${label}.mood`, 3, 180),
    visualSignature: designText(
      brief.visualSignature,
      `${label}.visualSignature`,
      3,
      180,
    ),
    artPresence: brief.artPresence,
  };
  if (!ART_PRESENCE_LEVELS.has(result.artPresence)) {
    throw new Error(`${label}.artPresence is unsupported`);
  }
  return result;
}

function validateExperience(value, label) {
  const experience = assertObject(value, label);
  if (!EXPERIENCE_MODES.has(experience.mode)) {
    throw new Error(`${label}.mode is unsupported`);
  }
  if (!ART_TOPOLOGIES.has(experience.artTopology)) {
    throw new Error(`${label}.artTopology is unsupported`);
  }
  if (!READING_STRATEGIES.has(experience.readingStrategy)) {
    throw new Error(`${label}.readingStrategy is unsupported`);
  }
  if (experience.interactionAuthority !== "native-only") {
    throw new Error(`${label}.interactionAuthority must be native-only`);
  }
  const backgroundScope = uniqueEnumList(
    experience.backgroundScope,
    `${label}.backgroundScope`,
    BACKGROUND_SCOPES,
    0,
    4,
  );
  const responsivePolicySource = assertObject(
    experience.responsivePolicy,
    `${label}.responsivePolicy`,
  );
  const responsivePolicy = {};
  for (const viewport of ["wide", "split", "narrow"]) {
    const behavior = responsivePolicySource[viewport];
    if (!RESPONSIVE_BEHAVIORS.has(behavior)) {
      throw new Error(`${label}.responsivePolicy.${viewport} is unsupported`);
    }
    responsivePolicy[viewport] = behavior;
  }
  if (RESPONSIVE_RANK.get(responsivePolicy.wide) >
      RESPONSIVE_RANK.get(responsivePolicy.split) ||
      RESPONSIVE_RANK.get(responsivePolicy.split) >
      RESPONSIVE_RANK.get(responsivePolicy.narrow)) {
    throw new Error(
      `${label}.responsivePolicy must not increase artwork as the viewport narrows`,
    );
  }
  const budget = assertObject(experience.decorBudget, `${label}.decorBudget`);
  if (!DENSITIES.has(budget.density)) {
    throw new Error(`${label}.decorBudget.density must be low, medium, or high`);
  }
  const decorBudget = {
    density: budget.density,
    dominantMotifs: boundedInteger(
      budget.dominantMotifs,
      `${label}.decorBudget.dominantMotifs`,
      0,
      1,
    ),
    secondaryMotifs: boundedInteger(
      budget.secondaryMotifs,
      `${label}.decorBudget.secondaryMotifs`,
      0,
      3,
    ),
    accentAreaPercent: boundedNumber(
      budget.accentAreaPercent,
      `${label}.decorBudget.accentAreaPercent`,
      0,
      20,
    ),
  };
  return {
    mode: experience.mode,
    artTopology: experience.artTopology,
    backgroundScope,
    readingStrategy: experience.readingStrategy,
    responsivePolicy,
    decorBudget,
    interactionAuthority: "native-only",
  };
}

function validateCoherence(theme, creativeBrief, experience) {
  const hasArt = Boolean(theme.art);
  if (!hasArt && (
    creativeBrief.artPresence !== "palette-only" ||
    experience.mode !== "palette-only" ||
    experience.artTopology !== "none" ||
    experience.backgroundScope.length
  )) {
    throw new Error(
      `${theme.id} has no art, so creativeBrief/experience must use palette-only, none, and no background scopes`,
    );
  }
  if (hasArt && (
    creativeBrief.artPresence === "palette-only" ||
    experience.mode === "palette-only" ||
    experience.artTopology === "none" ||
    !experience.backgroundScope.length
  )) {
    throw new Error(
      `${theme.id} declares art, so creativeBrief/experience must declare a visible art intent and scope`,
    );
  }
  if (hasArt && experience.responsivePolicy.wide === "hide") {
    throw new Error(`${theme.id} declares art but hides it at every supported viewport`);
  }
  if (hasArt && !experience.backgroundScope.some((scope) =>
    scope === "home" || scope === "thread" || scope === "settings")) {
    throw new Error(
      `${theme.id}.experience.backgroundScope must include a workbench route; ` +
      "right-panel cannot become an independent raster owner",
    );
  }
  if (!hasArt && Object.values(experience.responsivePolicy).some((value) => value !== "hide")) {
    throw new Error(`${theme.id} has no art, so every responsive policy must be hide`);
  }
}

function readingPaintAdjustment(readingStrategy) {
  if (readingStrategy === "opaque-reading-zones") {
    return { scrim: 8, tint: 10 };
  }
  if (readingStrategy === "minimal-art") {
    return { scrim: 12, tint: 14 };
  }
  if (readingStrategy === "adaptive-scrim") {
    return { scrim: 4, tint: 2 };
  }
  return { scrim: 0, tint: 0 };
}

function clampPaint(value) {
  return Math.max(0, Math.min(96, value));
}

function generatedRuntimeComposition(theme, experience) {
  if (!theme.art) return null;
  const selectedAsset = Object.keys(theme.art.variants ?? {}).includes("landscape")
    ? "landscape"
    : "primary";
  const descriptor = selectedAsset === "primary"
    ? theme.art
    : theme.art.variants[selectedAsset];
  const focal = focalPoint(descriptor);
  const profiles = {
    immersive: {
      workbenchScrim: 32,
      sidebarTint: 68,
      mainTint: 74,
      headerTint: 72,
      rightPanelTint: 72,
    },
    background: {
      workbenchScrim: 42,
      sidebarTint: 78,
      mainTint: 82,
      headerTint: 80,
      rightPanelTint: 80,
    },
    "bounded-showcase": {
      workbenchScrim: 48,
      sidebarTint: 84,
      mainTint: 86,
      headerTint: 84,
      rightPanelTint: 84,
    },
  };
  const paint = profiles[experience.mode] ?? profiles.background;
  const readingAdjustment = readingPaintAdjustment(experience.readingStrategy);
  return {
    mode: "continuous",
    asset: selectedAsset,
    focusX: focal.x,
    focusY: focal.y,
    safeArea: { left: 0, right: 0, top: 0, bottom: 0 },
    workbenchScrim: clampPaint(paint.workbenchScrim + readingAdjustment.scrim),
    sidebarTint: clampPaint(paint.sidebarTint + readingAdjustment.tint),
    mainTint: clampPaint(paint.mainTint + readingAdjustment.tint),
    headerTint: clampPaint(paint.headerTint + readingAdjustment.tint),
    rightPanelTint: clampPaint(paint.rightPanelTint + readingAdjustment.tint),
    narrowMode: experience.responsivePolicy.narrow === "hide" ? "hide-art" : "retain",
  };
}

function buildLayoutPlan(experience, hasArt) {
  const topology = TOPOLOGY_LAYOUT_PLANS[experience.artTopology];
  return {
    contractVersion: 1,
    topology: experience.artTopology,
    coordinateSpace: "semantic-workbench-viewport",
    coordinateOwner: hasArt ? "workbench" : "none",
    assetRole: hasArt ? "single-workbench-raster" : "none",
    sourceAssetFlexibility: hasArt ? "any-local-raster-source" : "none",
    compiledCanvas: hasArt ? "semantic-workbench" : "none",
    anchorStrategy: topology.anchorStrategy,
    focalZoneRange: { ...topology.focalZoneRange },
    quietZoneRelationship: topology.quietZoneRelationship,
    rasterization: topology.rasterization,
    backgroundScope: [...experience.backgroundScope],
    responsivePolicy: { ...experience.responsivePolicy },
    readingStrategy: experience.readingStrategy,
    panelRasterCopies: "forbidden",
  };
}

export function compileThemeAuthoring(theme, options = {}) {
  assertObject(theme, options.label ?? "theme");
  if (typeof theme.id !== "string" || !THEME_ID_PATTERN.test(theme.id)) {
    throw new Error("theme.id must be a lowercase hyphenated id");
  }
  if (options.expectedId && options.expectedId !== theme.id) {
    throw new Error(`theme.id does not match expected id ${options.expectedId}`);
  }
  const sourceSchemaVersion = theme.schemaVersion ?? 1;
  if (![1, AUTHORING_SCHEMA_VERSION].includes(sourceSchemaVersion)) {
    throw new Error(`${theme.id}.schemaVersion is unsupported`);
  }
  const declaredAuthoring = sourceSchemaVersion === AUTHORING_SCHEMA_VERSION;
  if (sourceSchemaVersion === 1 &&
      (theme.creativeBrief !== undefined || theme.experience !== undefined)) {
    throw new Error(
      `${theme.id} must set schemaVersion ${AUTHORING_SCHEMA_VERSION} when creativeBrief or experience is declared`,
    );
  }
  const creativeBrief = validateCreativeBrief(
    declaredAuthoring ? theme.creativeBrief : inferCreativeBrief(theme),
    `${theme.id}.creativeBrief`,
  );
  const experience = validateExperience(
    declaredAuthoring ? theme.experience : inferExperience(theme),
    `${theme.id}.experience`,
  );
  validateCoherence(theme, creativeBrief, experience);
  const runtimeComposition = theme.composition
    ? structuredClone(theme.composition)
    : generatedRuntimeComposition(theme, experience);
  const hasArt = Boolean(theme.art);
  const layoutPlan = buildLayoutPlan(experience, hasArt);
  return {
    compilerContractVersion: COMPOSITION_COMPILER_CONTRACT_VERSION,
    sourceSchemaVersion,
    targetSchemaVersion: AUTHORING_SCHEMA_VERSION,
    authoringMode: declaredAuthoring ? "declared" : "legacy-inferred",
    creativeBrief,
    experience,
    layoutPlan,
    coordinateOwnership: {
      contractVersion: 1,
      coordinateSpace: "semantic-workbench-viewport",
      owner: hasArt ? "workbench" : "none",
      directRasterOwners: hasArt ? ["workbench"] : [],
      panelRasterCopies: "forbidden",
      panelCropAuthority: "forbidden",
      geometryAuthority: "native",
      interactionAuthority: "native-only",
    },
    runtimeComposition,
    projection: {
      mode: runtimeComposition?.mode ?? "none",
      preservesDeclaredComposition: Boolean(theme.composition),
      topologyIsDescriptive: true,
      topologyCompiledToLayoutPlan: true,
      authoringScopeRequiresRuntimeProjection: declaredAuthoring && hasArt,
      responsivePolicyRequiresRuntimeProjection: declaredAuthoring && hasArt,
      backgroundScopeIsDesignIntent: true,
    },
    diagnostics: declaredAuthoring ? [] : [{
      code: "LEGACY_AUTHORING_INFERRED",
      severity: "info",
      message: "Schema v1 theme was compiled in memory without modifying its source file.",
    }],
  };
}

export function migrateThemeDocument(theme, options = {}) {
  const compiled = compileThemeAuthoring(theme, options);
  if (compiled.sourceSchemaVersion === AUTHORING_SCHEMA_VERSION) {
    return {
      changed: false,
      sourceSchemaVersion: AUTHORING_SCHEMA_VERSION,
      targetSchemaVersion: AUTHORING_SCHEMA_VERSION,
      document: structuredClone(theme),
    };
  }
  return {
    changed: true,
    sourceSchemaVersion: compiled.sourceSchemaVersion,
    targetSchemaVersion: AUTHORING_SCHEMA_VERSION,
    document: {
      ...structuredClone(theme),
      schemaVersion: AUTHORING_SCHEMA_VERSION,
      creativeBrief: compiled.creativeBrief,
      experience: compiled.experience,
    },
  };
}
