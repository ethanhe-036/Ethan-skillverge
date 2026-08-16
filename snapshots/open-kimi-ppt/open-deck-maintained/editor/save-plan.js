import {
  buildImagePathAliases,
  buildWritablePathAliases,
  dirname,
  extractPagePaths,
  extractReferencedImagePaths,
  MAX_PROJECT_PATH_CHARS,
  normalizeRelativePath,
  normalizeSaveChanges,
  validateImageReferencesGranted,
  validatePageClosure,
} from "./lib.js";
import { MAX_PROJECT_ENTRIES, projectMetadataFromPaths } from "./project-index.js";

export const MAX_SAVE_FROM_BYTES = 128;
export const MAX_SAVE_SLIDE_ID_BYTES = 256;
export const MAX_SAVE_TITLE_BYTES = 1024;
export const MAX_SAVE_PPTD_PATH_BYTES = 4096;
const MAX_SAVE_PAYLOAD_KEYS = 8;
const MAX_SAVE_PAYLOAD_KEY_CHARS = 64;
const MAX_SAVE_FILE_METADATA_KEYS = 2;
const SAVE_PAYLOAD_FIELDS = [
  "changes",
  "fileContent",
  "saveFrom",
  "slideId",
  "chatId",
  "file",
  "title",
];

function assertBoundedMetadataString(value, label, maxBytes) {
  if (typeof value !== "string") {
    throw new Error(`保存请求的 ${label} 必须是字符串`);
  }
  // Reject by code units first so TextEncoder never materializes an
  // attacker-controlled buffer larger than the declared metadata ceiling.
  if (value.length > maxBytes || new TextEncoder().encode(value).byteLength > maxBytes) {
    throw new Error(`保存请求的 ${label} 不能超过 ${maxBytes} UTF-8 字节`);
  }
}

function validateFileMetadata(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("保存请求的 file 必须是只含 pptdPath 的对象");
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    throw new Error("保存请求的 file 必须是普通对象");
  }

  const unknownFields = [];
  let ownKeyCount = 0;
  for (const key in value) {
    if (!Object.hasOwn(value, key)) continue;
    ownKeyCount += 1;
    if (ownKeyCount > MAX_SAVE_FILE_METADATA_KEYS) {
      throw new Error(`保存请求的 file 不能超过 ${MAX_SAVE_FILE_METADATA_KEYS} 个字段`);
    }
    if (key.length > MAX_SAVE_PAYLOAD_KEY_CHARS) {
      throw new Error(`保存请求的 file 字段名不能超过 ${MAX_SAVE_PAYLOAD_KEY_CHARS} 个字符`);
    }
    if (key !== "pptdPath") unknownFields.push(key);
  }
  if (unknownFields.length > 0) {
    throw new Error(`保存请求的 file 包含不支持的字段：${unknownFields.join(", ")}`);
  }
  if (!Object.hasOwn(value, "pptdPath")) {
    throw new Error("保存请求的 file 缺少 pptdPath");
  }
  assertBoundedMetadataString(value.pptdPath, "file.pptdPath", MAX_SAVE_PPTD_PATH_BYTES);
}

function sanitizeFileContent(value, { maxChangeCount }) {
  if (typeof value === "string") return value;
  if (!Array.isArray(value)) {
    throw new Error("保存请求的 fileContent 必须是字符串或文件内容数组");
  }
  if (value.length > maxChangeCount) {
    throw new Error(`保存请求的 fileContent 不能超过 ${maxChangeCount} 个文件`);
  }
  for (const key in value) {
    if (!Object.hasOwn(value, key)) continue;
    const index = Number(key);
    if (
      !Number.isSafeInteger(index)
      || index < 0
      || index >= value.length
      || String(index) !== key
    ) {
      throw new Error(`保存 fileContent 数组包含不支持的字段：${key}`);
    }
  }
  for (let index = 0; index < value.length; index += 1) {
    if (!Object.hasOwn(value, index)) {
      throw new Error(`保存 fileContent 数组不能包含空槽：${index}`);
    }
  }
  const fileContent = value.map((entry) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new Error("保存 fileContent 条目必须是对象");
    }
    const unknownFields = [];
    let ownKeyCount = 0;
    for (const key in entry) {
      if (!Object.hasOwn(entry, key)) continue;
      ownKeyCount += 1;
      if (ownKeyCount > MAX_SAVE_FILE_METADATA_KEYS) {
        throw new Error(`保存 fileContent 条目不能超过 ${MAX_SAVE_FILE_METADATA_KEYS} 个字段`);
      }
      if (key.length > MAX_SAVE_PAYLOAD_KEY_CHARS) {
        throw new Error(`保存 fileContent 字段名不能超过 ${MAX_SAVE_PAYLOAD_KEY_CHARS} 个字符`);
      }
      if (!["path", "content"].includes(key)) unknownFields.push(key);
    }
    if (unknownFields.length > 0) {
      throw new Error(`保存 fileContent 条目包含不支持的字段：${unknownFields.join(", ")}`);
    }
    if (typeof entry.path !== "string") {
      throw new Error("保存 fileContent 条目的 path 必须是字符串");
    }
    if (entry.path.length > MAX_PROJECT_PATH_CHARS) {
      throw new Error(`保存 fileContent 路径超过 ${MAX_PROJECT_PATH_CHARS} 个字符安全上限`);
    }
    if (typeof entry.content !== "string") {
      throw new Error("保存 fileContent 条目的 content 必须是字符串");
    }
    return Object.freeze({ path: entry.path, content: entry.content });
  });
  return Object.freeze(fileContent);
}

export function sanitizeSavePayload(payload, { maxChangeCount }) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("保存请求必须是对象");
  }
  const unknownFields = [];
  let ownKeyCount = 0;
  for (const key in payload) {
    if (!Object.hasOwn(payload, key)) continue;
    ownKeyCount += 1;
    if (ownKeyCount > MAX_SAVE_PAYLOAD_KEYS) {
      throw new Error(`保存请求不能超过 ${MAX_SAVE_PAYLOAD_KEYS} 个顶层字段`);
    }
    if (key.length > MAX_SAVE_PAYLOAD_KEY_CHARS) {
      throw new Error(`保存请求字段名不能超过 ${MAX_SAVE_PAYLOAD_KEY_CHARS} 个字符`);
    }
    if (!SAVE_PAYLOAD_FIELDS.includes(key)) unknownFields.push(key);
  }
  if (unknownFields.length > 0) {
    throw new Error(`保存请求包含不支持的字段：${unknownFields.join(", ")}`);
  }
  if (Object.hasOwn(payload, "saveFrom")) {
    assertBoundedMetadataString(payload.saveFrom, "saveFrom", MAX_SAVE_FROM_BYTES);
  }
  if (Object.hasOwn(payload, "slideId")) {
    assertBoundedMetadataString(payload.slideId, "slideId", MAX_SAVE_SLIDE_ID_BYTES);
  }
  if (Object.hasOwn(payload, "chatId") && payload.chatId !== undefined) {
    throw new Error("保存请求的 chatId 只允许当前协议中的 undefined 值");
  }
  if (Object.hasOwn(payload, "file")) {
    validateFileMetadata(payload.file);
  }
  if (Object.hasOwn(payload, "title")) {
    assertBoundedMetadataString(payload.title, "title", MAX_SAVE_TITLE_BYTES);
  }
  const rawChanges = payload.changes === undefined ? [] : payload.changes;
  if (!Array.isArray(rawChanges)) {
    throw new Error("保存请求的 changes 必须是数组");
  }
  if (rawChanges.length > maxChangeCount) {
    throw new Error(`一次保存不能超过 ${maxChangeCount} 个文件`);
  }
  for (const key in rawChanges) {
    if (!Object.hasOwn(rawChanges, key)) continue;
    const index = Number(key);
    if (
      !Number.isSafeInteger(index)
      || index < 0
      || index >= rawChanges.length
      || String(index) !== key
    ) {
      throw new Error(`保存 changes 数组包含不支持的字段：${key}`);
    }
  }
  for (let index = 0; index < rawChanges.length; index += 1) {
    if (!Object.hasOwn(rawChanges, index)) {
      throw new Error(`保存 changes 数组不能包含空槽：${index}`);
    }
  }
  const changes = rawChanges.map((change) => {
    if (!change || typeof change !== "object" || Array.isArray(change)) {
      throw new Error("保存变更必须是对象");
    }
    for (const key in change) {
      if (Object.hasOwn(change, key) && !["operate", "path", "content"].includes(key)) {
        throw new Error(`保存变更包含不支持的字段：${key}`);
      }
    }
    if (!["put", "create", "update", "delete"].includes(change.operate)) {
      throw new Error(`不支持的保存操作：${String(change.operate)}`);
    }
    if (typeof change.path !== "string") throw new Error("保存变更路径必须是字符串");
    if (change.operate === "delete") {
      if (change.content !== undefined && change.content !== null) {
        throw new Error("删除操作不能携带 content");
      }
    } else if (typeof change.content !== "string") {
      throw new Error("写入操作缺少字符串内容");
    }
    return Object.freeze({
      operate: change.operate,
      path: change.path,
      content: change.operate === "delete" ? null : change.content,
    });
  });
  const sanitized = {
    changes: Object.freeze(changes),
  };
  if (payload.fileContent !== undefined && payload.fileContent !== null) {
    sanitized.fileContent = sanitizeFileContent(
      payload.fileContent,
      { maxChangeCount },
    );
  }
  return Object.freeze(sanitized);
}

export function measureSavePayloadBytes(
  payload,
  { maxChangeCount, maxFileBytes, maxTotalBytes },
) {
  if (!Array.isArray(payload?.changes)) {
    throw new Error("保存请求缺少 changes 数组");
  }
  if (payload.changes.length > maxChangeCount) {
    throw new Error(`一次保存不能超过 ${maxChangeCount} 个文件`);
  }
  const encoder = new TextEncoder();
  let totalBytes = 0;
  const addString = (value, label, perValueLimit = maxFileBytes) => {
    if (typeof value !== "string") return;
    if (value.length > perValueLimit) {
      throw new Error(`${label}超过单项安全上限`);
    }
    if (value.length > maxTotalBytes - totalBytes) {
      throw new Error("保存请求文本总量超过安全上限");
    }
    const bytes = encoder.encode(value).byteLength;
    if (bytes > perValueLimit) throw new Error(`${label}超过单项安全上限`);
    totalBytes += bytes;
    if (totalBytes > maxTotalBytes) {
      throw new Error("保存请求文本总量超过安全上限");
    }
  };

  for (const change of payload.changes) {
    if (typeof change?.path === "string") {
      if (change.path.length > MAX_PROJECT_PATH_CHARS) {
        throw new Error(`文件路径超过 ${MAX_PROJECT_PATH_CHARS} 个字符安全上限`);
      }
      addString(change.path, "保存路径", MAX_PROJECT_PATH_CHARS * 4);
    }
    addString(change?.content, "保存内容");
  }
  if (typeof payload.fileContent === "string") {
    addString(payload.fileContent, "保存返回内容");
  } else if (Array.isArray(payload.fileContent)) {
    if (payload.fileContent.length > maxChangeCount) {
      throw new Error(`保存请求的 fileContent 不能超过 ${maxChangeCount} 个文件`);
    }
    for (const entry of payload.fileContent) {
      if (typeof entry?.path === "string") {
        if (entry.path.length > MAX_PROJECT_PATH_CHARS) {
          throw new Error(`保存 fileContent 路径超过 ${MAX_PROJECT_PATH_CHARS} 个字符安全上限`);
        }
        addString(entry.path, "保存返回路径", MAX_PROJECT_PATH_CHARS * 4);
      }
      addString(entry?.content, "保存返回内容");
    }
  } else if (payload.fileContent !== undefined && payload.fileContent !== null) {
    throw new Error("保存请求的 fileContent 必须是字符串或文件内容数组");
  }
  return totalBytes;
}

export function aliasesForSave(
  payload,
  context,
  { maxChangeCount, maxTextBytes, maxPageCount = 500 },
) {
  const currentAliases = context.writablePathAliases;
  let nextAliases = currentAliases;
  const changes = Array.isArray(payload?.changes) ? payload.changes : [];
  if (changes.length > maxChangeCount) {
    throw new Error(`一次保存不能超过 ${maxChangeCount} 个文件`);
  }
  for (const change of changes) {
    if (!change || !["put", "create", "update"].includes(change.operate)) continue;
    if (typeof change.path !== "string" || typeof change.content !== "string") continue;
    let requestedPath;
    try {
      requestedPath = normalizeRelativePath(change.path);
    } catch {
      continue;
    }
    if (currentAliases.get(requestedPath) !== context.manifestPath) continue;
    if (new TextEncoder().encode(change.content).byteLength > maxTextBytes) {
      throw new Error("PPTD 清单保存内容超过 20 MiB 安全上限");
    }
    const pagePaths = extractPagePaths(change.content, { maxPageCount });
    nextAliases = buildWritablePathAliases(context.manifestPath, pagePaths);
  }

  if (nextAliases === currentAliases) {
    return { aliases: currentAliases, nextAliases: currentAliases };
  }
  const aliases = new Map(currentAliases);
  for (const [alias, canonical] of nextAliases) {
    const previous = aliases.get(alias);
    if (previous && previous !== canonical) {
      throw new Error(`保存请求中的路径存在歧义：${alias}`);
    }
    aliases.set(alias, canonical);
  }
  return { aliases, nextAliases };
}

export function collectSaveAttemptPaths(payload, context, limits) {
  const paths = new Set();
  let unscoped = false;
  if (!Array.isArray(payload?.changes)) {
    return { paths, unscoped: true, pathAliases: context.writablePathAliases };
  }
  if (payload.changes.length > limits.maxChangeCount) {
    return { paths, unscoped: true, pathAliases: context.writablePathAliases };
  }
  let aliases = context.writablePathAliases;
  try {
    aliases = aliasesForSave(payload, context, limits).aliases;
  } catch {
    // Keep the best current identity. Validation will report the payload error
    // when the queued save executes.
  }
  for (const change of payload.changes) {
    if (typeof change?.path !== "string") {
      unscoped = true;
      continue;
    }
    try {
      const requested = normalizeRelativePath(change.path);
      paths.add(aliases.get(requested) ?? requested);
    } catch {
      unscoped = true;
    }
  }
  return { paths, unscoped, pathAliases: aliases };
}

function imageCapabilitiesForSave(context, changes, nextAliases) {
  const imageReferences = new Map(context.imageReferences);
  for (const change of changes) {
    if (change.path !== context.manifestPath && !change.path.toLowerCase().endsWith(".page")) continue;
    if (change.operation === "delete") imageReferences.delete(change.path);
    else imageReferences.set(change.path, extractReferencedImagePaths(change.content));
  }
  const declaredPages = new Set(
    [...nextAliases.values()].filter((path) => path.toLowerCase().endsWith(".page")),
  );
  for (const path of imageReferences.keys()) {
    if (path.toLowerCase().endsWith(".page") && !declaredPages.has(path)) {
      imageReferences.delete(path);
    }
  }
  const referencedImages = [...imageReferences.values()].flat();
  validateImageReferencesGranted(
    context.manifestPath,
    referencedImages,
    context.imageGrants,
  );
  return {
    imageReferences,
    imagePathAliases: buildImagePathAliases(
      context.manifestPath,
      referencedImages,
    ),
  };
}

function validateDeckClosure(context, changes, nextAliases) {
  const requiredPages = [...nextAliases.values()].filter(
    (path) => path.toLowerCase().endsWith(".page"),
  );
  const existingPaths = context.source === "demo"
    ? context.memoryFiles.keys()
    : context.fileIndex.keys();
  const declaredPages = [...context.writablePathAliases.values()].filter(
    (path) => path.toLowerCase().endsWith(".page"),
  );
  validatePageClosure(requiredPages, existingPaths, changes, { declaredPagePaths: declaredPages });
  const declared = new Set(declaredPages);
  return requiredPages.filter((path) => !declared.has(path));
}

function formatByteLimit(byteLimit) {
  const mebibyte = 1024 * 1024;
  return byteLimit % mebibyte === 0
    ? `${byteLimit / mebibyte} MiB`
    : `${byteLimit} 字节`;
}

function knownTextByteSize(context, path, encoder) {
  const recorded = context.textByteSizes?.get(path);
  if (Number.isSafeInteger(recorded) && recorded >= 0) return recorded;
  const memoryContent = context.memoryFiles?.get(path);
  if (typeof memoryContent === "string") return encoder.encode(memoryContent).byteLength;
  throw new Error(`无法确认声明文件的文本大小：${path}`);
}

function validateDeckTextBudget(
  context,
  changes,
  nextAliases,
  maxDeckTextBytes,
) {
  const encoder = new TextEncoder();
  const byPath = new Map(changes.map((change) => [change.path, change]));
  const declaredPaths = new Set([context.manifestPath]);
  for (const path of nextAliases.values()) {
    if (path.toLowerCase().endsWith(".page")) declaredPaths.add(path);
  }

  const nextTextByteSizes = new Map();
  let totalBytes = 0;
  for (const path of declaredPaths) {
    const change = byPath.get(path);
    if (change?.operation === "delete") {
      throw new Error(`保存会删除声明文件：${path}`);
    }
    const size = change?.operation === "put"
      ? encoder.encode(change.content).byteLength
      : knownTextByteSize(context, path, encoder);
    totalBytes += size;
    if (totalBytes > maxDeckTextBytes) {
      throw new Error(
        `文稿清单和页面总大小超过 ${formatByteLimit(maxDeckTextBytes)} 安全上限`,
      );
    }
    nextTextByteSizes.set(path, size);
  }
  return nextTextByteSizes;
}

function validateProjectEntryBudget(
  context,
  changes,
  newPagePaths,
  maxProjectEntries,
) {
  const knownPaths = context.source === "demo"
    ? context.memoryFiles.keys()
    : context.fileIndex.keys();
  const inferred = projectMetadataFromPaths(knownPaths);
  const directories = context.projectDirectories ?? inferred.directories;
  let nextProjectEntryCount = Number.isSafeInteger(context.projectEntryCount)
    ? context.projectEntryCount
    : inferred.entryCount;
  const newPages = new Set(newPagePaths);

  for (const path of newPages) {
    const parent = dirname(path);
    if (parent && !directories.has(parent)) {
      throw new Error(`新页面只能创建在载入时已存在的目录中：${path}`);
    }
  }

  for (const change of changes) {
    const existed = context.source === "demo"
      ? context.memoryFiles.has(change.path)
      : context.fileIndex.has(change.path);
    if (change.operation === "delete" && existed) nextProjectEntryCount -= 1;
    else if (change.operation === "put" && !existed) nextProjectEntryCount += 1;
  }
  if (nextProjectEntryCount > maxProjectEntries) {
    throw new Error(`保存会让项目条目超过 ${maxProjectEntries} 个安全上限`);
  }
  if (nextProjectEntryCount < 0) {
    throw new Error("项目条目计数无效，已拒绝保存");
  }
  return nextProjectEntryCount;
}

export function prepareSavePlan(
  payload,
  context,
  {
    maxChangeCount,
    maxTextBytes,
    maxSaveBytes,
    maxDeckTextBytes = maxSaveBytes,
    maxPageCount = 500,
    maxProjectEntries = MAX_PROJECT_ENTRIES,
  },
) {
  const { aliases, nextAliases } = aliasesForSave(
    payload,
    context,
    { maxChangeCount, maxTextBytes, maxPageCount },
  );
  const normalized = normalizeSaveChanges(payload, context.manifestDirectory, {
    maxChangeCount,
    maxFileBytes: maxTextBytes,
    maxTotalBytes: maxSaveBytes,
    pathAliases: aliases,
    requireKnownPaths: true,
  });
  if (normalized.length === 0) {
    return {
      normalized,
      newPagePaths: [],
      nextAliases,
      nextImageCapabilities: null,
      nextTextByteSizes: null,
      nextProjectEntryCount: null,
    };
  }
  if (normalized.some((change) => change.path === context.manifestPath && change.operation === "delete")) {
    throw new Error("不能删除当前文稿的 PPTD 清单");
  }
  const newPagePaths = validateDeckClosure(context, normalized, nextAliases);
  const nextImageCapabilities = imageCapabilitiesForSave(context, normalized, nextAliases);
  const nextTextByteSizes = validateDeckTextBudget(
    context,
    normalized,
    nextAliases,
    maxDeckTextBytes,
  );
  const nextProjectEntryCount = validateProjectEntryBudget(
    context,
    normalized,
    newPagePaths,
    maxProjectEntries,
  );
  return {
    normalized,
    newPagePaths,
    nextAliases,
    nextImageCapabilities,
    nextTextByteSizes,
    nextProjectEntryCount,
  };
}

/** Invoke mutation code only after every save/capability preflight succeeds. */
export function withSavePreflight(payload, context, limits, mutate) {
  const plan = prepareSavePlan(payload, context, limits);
  return mutate(plan);
}
