import {
  assertJsonResourceLimits,
  extractRestrictedYamlManifestPages,
  JsonPreflightSyntaxError,
} from "./restricted-parser.js";

export const MAX_PROJECT_PATH_CHARS = 4096;
export const MAX_PROJECT_DIRECTORY_DEPTH = 64;
export const MAX_SAVE_FAILURE_PATHS = 600;
export const MAX_SAVE_FAILURE_ALIASES = 2048;

export function normalizeRelativePath(value) {
  if (typeof value !== "string") {
    throw new TypeError("文件路径必须是字符串");
  }

  const path = value.trim().replaceAll("\\", "/");
  if (path.length > MAX_PROJECT_PATH_CHARS) {
    throw new Error(`文件路径超过 ${MAX_PROJECT_PATH_CHARS} 个字符安全上限`);
  }
  // Filesystem names are not URL paths. URI-decoding here can turn a literal
  // "%2F" filename into a separator and make two distinct files collide.
  if (
    !path
    || path.includes("\0")
    || path.startsWith("/")
    || /^[A-Za-z]:\//.test(path)
    || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(path)
  ) {
    throw new Error(`不允许的绝对路径：${value}`);
  }

  const parts = [];
  for (const part of path.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") throw new Error(`不允许越过项目目录：${value}`);
    parts.push(part);
  }

  if (parts.length === 0) throw new Error(`无效文件路径：${value}`);
  if (parts.length - 1 > MAX_PROJECT_DIRECTORY_DEPTH) {
    throw new Error(
      `项目目录深度超过 ${MAX_PROJECT_DIRECTORY_DEPTH} 层安全上限：${value}`,
    );
  }
  return parts.join("/");
}

export function dirname(path) {
  const normalized = normalizeRelativePath(path);
  const index = normalized.lastIndexOf("/");
  return index === -1 ? "" : normalized.slice(0, index);
}

export function basename(path) {
  const normalized = normalizeRelativePath(path);
  return normalized.slice(normalized.lastIndexOf("/") + 1);
}

export function joinDeckPath(base, path) {
  const child = normalizeRelativePath(path);
  if (!base) return child;
  const normalizedBase = normalizeRelativePath(base);
  if (child === normalizedBase || child.startsWith(`${normalizedBase}/`)) return child;
  // Both halves are already normalized. Keep local filenames byte-for-byte
  // rather than treating percent sequences as URL encoding.
  return normalizeRelativePath(`${normalizedBase}/${child}`);
}

export function joinManifestPath(base, path) {
  const child = normalizeRelativePath(path);
  if (!base) return child;
  // Manifest entries are always relative to the manifest directory. Do not use
  // joinDeckPath here: a legitimate child whose first segment equals `base`
  // still needs that base prepended.
  return normalizeRelativePath(`${normalizeRelativePath(base)}/${child}`);
}

function unquoteYamlScalar(value) {
  const text = value.trim();
  if (text.startsWith('"')) {
    const match = text.match(/^"(?:\\.|[^"\\])*"/);
    if (match) {
      try {
        return JSON.parse(match[0]);
      } catch {
        return match[0].slice(1, -1);
      }
    }
  }
  if (text.startsWith("'")) {
    let result = "";
    for (let index = 1; index < text.length; index += 1) {
      if (text[index] !== "'") {
        result += text[index];
        continue;
      }
      if (text[index + 1] === "'") {
        result += "'";
        index += 1;
        continue;
      }
      return result;
    }
  }
  return text.replace(/\s+#.*$/, "").trim();
}

const LOCAL_IMAGE_EXTENSION = /\.(?:gif|jpe?g|png|svg|webp)$/i;

const DEFAULT_MAX_JSON_SOURCE_NODES = 100_000;
const DEFAULT_MAX_JSON_SOURCE_DEPTH = 128;

function collectJsonSources(
  value,
  sources,
  {
    maxNodes = DEFAULT_MAX_JSON_SOURCE_NODES,
    maxDepth = DEFAULT_MAX_JSON_SOURCE_DEPTH,
  } = {},
) {
  function* childEntries(parent) {
    if (Array.isArray(parent)) {
      for (let index = 0; index < parent.length; index += 1) {
        yield [String(index), parent[index]];
      }
      return;
    }
    for (const key in parent) {
      if (Object.hasOwn(parent, key)) yield [key, parent[key]];
    }
  }

  const stack = [{ value, depth: 0, iterator: null }];
  let visitedNodes = 0;
  while (stack.length > 0) {
    const current = stack[stack.length - 1];
    if (current.iterator === null) {
      visitedNodes += 1;
      if (visitedNodes > maxNodes) {
        throw new Error(`页面 JSON 节点数超过 ${maxNodes} 安全上限`);
      }
      if (!current.value || typeof current.value !== "object") {
        stack.pop();
        continue;
      }
      if (current.depth >= maxDepth) {
        throw new Error(`页面 JSON 嵌套深度超过 ${maxDepth} 安全上限`);
      }
      current.iterator = childEntries(current.value);
    }

    const next = current.iterator.next();
    if (next.done) {
      stack.pop();
      continue;
    }
    const [key, child] = next.value;
    if (key === "src" && typeof child === "string") {
      visitedNodes += 1;
      if (visitedNodes > maxNodes) {
        throw new Error(`页面 JSON 节点数超过 ${maxNodes} 安全上限`);
      }
      sources.push(child);
    } else {
      stack.push({ value: child, depth: current.depth + 1, iterator: null });
    }
  }
}

/**
 * Return local image paths explicitly referenced by a page.
 *
 * This intentionally supports only the JSON form and the documented `src:`
 * YAML scalar form. It is a capability allow-list, not a general YAML parser:
 * ambiguous or unsafe paths are omitted instead of guessed.
 */
export function extractReferencedImagePaths(
  pageText,
  {
    maxJsonNodes = DEFAULT_MAX_JSON_SOURCE_NODES,
    maxJsonDepth = DEFAULT_MAX_JSON_SOURCE_DEPTH,
    maxJsonBytes,
    maxJsonStringChars,
    maxJsonTotalStringChars,
  } = {},
) {
  if (typeof pageText !== "string") throw new TypeError("页面内容必须是字符串");
  const sources = [];
  const trimmed = pageText.trim();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      assertJsonResourceLimits(pageText, {
        label: "页面 JSON",
        maxBytes: maxJsonBytes,
        maxNodes: maxJsonNodes,
        maxDepth: maxJsonDepth,
        maxStringChars: maxJsonStringChars,
        maxTotalStringChars: maxJsonTotalStringChars,
      });
    } catch (error) {
      // Malformed page JSON historically grants no image capabilities. Budget
      // and duplicate-key failures remain hard errors because silently falling
      // back would let ambiguous documents bypass a security policy.
      if (error instanceof JsonPreflightSyntaxError) return [];
      throw error;
    }
    let parsed;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      return [];
    }
    collectJsonSources(parsed, sources, {
      maxNodes: maxJsonNodes,
      maxDepth: maxJsonDepth,
    });
  } else {
    for (const line of pageText.replaceAll("\r\n", "\n").split("\n")) {
      const match = line.match(/^\s*(?:-\s*)?src\s*:\s*(.+?)\s*$/);
      if (match) sources.push(unquoteYamlScalar(match[1]));
    }
  }

  const paths = new Set();
  for (const source of sources) {
    if (/^(?:data:|https?:\/\/|blob:)/i.test(source)) continue;
    try {
      const normalized = normalizeRelativePath(source);
      if (LOCAL_IMAGE_EXTENSION.test(normalized)) paths.add(normalized);
    } catch {
      // A local asset must be manifest-relative. Absolute paths and traversal
      // are deliberately not repaired by suffix matching.
    }
  }
  return [...paths];
}

function assertPageLimit(maxPageCount) {
  if (!Number.isSafeInteger(maxPageCount) || maxPageCount < 1) {
    throw new TypeError("maxPageCount 必须是正安全整数");
  }
}

export function extractPagePaths(
  manifestText,
  {
    maxPageCount = 500,
    maxJsonNodes = DEFAULT_MAX_JSON_SOURCE_NODES,
    maxJsonDepth = DEFAULT_MAX_JSON_SOURCE_DEPTH,
    maxJsonBytes,
    maxJsonStringChars,
    maxJsonTotalStringChars,
    maxYamlNodes = DEFAULT_MAX_JSON_SOURCE_NODES,
    maxYamlDepth = DEFAULT_MAX_JSON_SOURCE_DEPTH,
    maxYamlBytes,
    maxYamlLines,
  } = {},
) {
  assertPageLimit(maxPageCount);
  if (typeof manifestText !== "string") throw new TypeError("PPTD manifest 内容必须是字符串");
  const trimmed = manifestText.trim();
  if (!trimmed) throw new Error("PPTD manifest 是空文件");

  if (trimmed.startsWith("{")) {
    assertJsonResourceLimits(manifestText, {
      label: "PPTD manifest JSON",
      maxBytes: maxJsonBytes,
      maxNodes: maxJsonNodes,
      maxDepth: maxJsonDepth,
      maxStringChars: maxJsonStringChars,
      maxTotalStringChars: maxJsonTotalStringChars,
    });
    const parsed = JSON.parse(trimmed);
    if (!Array.isArray(parsed.pages)) throw new Error("PPTD manifest 缺少 pages 数组");
    if (parsed.pages.length > maxPageCount) {
      throw new Error(`文稿包含 ${parsed.pages.length} 页，超过 ${maxPageCount} 页安全上限`);
    }
    if (parsed.pages.length === 0) throw new Error("PPTD manifest 的 pages 数组不能为空");
    return parsed.pages.map((path, index) => {
      if (typeof path !== "string") {
        throw new Error(`PPTD manifest 的 pages[${index}] 必须是字符串路径`);
      }
      return normalizeRelativePath(path);
    });
  }

  const pages = extractRestrictedYamlManifestPages(manifestText, {
    maxPageCount,
    maxBytes: maxYamlBytes,
    maxNodes: maxYamlNodes,
    maxDepth: maxYamlDepth,
    maxLines: maxYamlLines,
    normalizePagePath: normalizeRelativePath,
  });
  if (pages.length === 0) throw new Error("PPTD manifest 的 pages 数组不能为空");
  return pages;
}

export function titleFromManifest(manifestText, fallback) {
  const trimmed = manifestText.trim();
  if (trimmed.startsWith("{")) {
    try {
      assertJsonResourceLimits(manifestText, { label: "PPTD manifest JSON" });
      return JSON.parse(trimmed).title || fallback;
    } catch {
      return fallback;
    }
  }
  const match = manifestText.match(/^title\s*:\s*(.+?)\s*$/m);
  return match ? unquoteYamlScalar(match[1]) : fallback;
}

export function assertWritableChangePath(base, requestedPath) {
  const path = joinDeckPath(base, requestedPath);
  if (!/\.(?:pptd|page)$/i.test(path)) {
    throw new Error(`为安全起见，仅允许编辑 .pptd/.page：${requestedPath}`);
  }
  return path;
}

function addPathAlias(aliases, alias, canonical) {
  const previous = aliases.get(alias);
  if (previous && previous !== canonical) {
    throw new Error(`PPTD 路径存在歧义：${alias} 同时指向 ${previous} 和 ${canonical}`);
  }
  aliases.set(alias, canonical);
}

/**
 * Build an explicit protocol-path → project-path table for one manifest.
 * Both the manifest-relative form sent in `pages` and the canonical form sent
 * by some editor versions are accepted only when they resolve unambiguously.
 */
export function buildWritablePathAliases(manifestPath, pagePaths) {
  const canonicalManifest = normalizeRelativePath(manifestPath);
  if (!canonicalManifest.toLowerCase().endsWith(".pptd")) {
    throw new Error(`无效 PPTD 清单路径：${manifestPath}`);
  }
  if (!Array.isArray(pagePaths)) throw new Error("PPTD 页面路径必须是数组");

  const manifestDirectory = dirname(canonicalManifest);
  const aliases = new Map();
  addPathAlias(aliases, canonicalManifest, canonicalManifest);
  addPathAlias(aliases, basename(canonicalManifest), canonicalManifest);
  const canonicalPages = new Set();
  for (const pagePath of pagePaths) {
    const relative = normalizeRelativePath(pagePath);
    if (!relative.toLowerCase().endsWith(".page")) {
      throw new Error(`PPTD 页面必须使用 .page 扩展名：${pagePath}`);
    }
    const canonical = joinManifestPath(manifestDirectory, relative);
    if (canonicalPages.has(canonical)) throw new Error(`PPTD 清单包含重复页面：${pagePath}`);
    canonicalPages.add(canonical);
    addPathAlias(aliases, relative, canonical);
    addPathAlias(aliases, canonical, canonical);
  }
  return aliases;
}

/** Build a fail-closed image request map; ambiguous aliases resolve to null. */
export function buildImagePathAliases(manifestPath, imagePaths) {
  const manifestDirectory = dirname(manifestPath);
  const aliases = new Map();
  const addAlias = (alias, canonical) => {
    if (!aliases.has(alias)) aliases.set(alias, canonical);
    else if (aliases.get(alias) !== canonical) aliases.set(alias, null);
  };
  for (const imagePath of imagePaths) {
    const relative = normalizeRelativePath(imagePath);
    if (!LOCAL_IMAGE_EXTENSION.test(relative)) continue;
    const canonical = joinManifestPath(manifestDirectory, relative);
    addAlias(relative, canonical);
    addAlias(canonical, canonical);
  }
  return aliases;
}

export function collectImageProjectPaths(manifestPath, imagePaths) {
  const manifestDirectory = dirname(manifestPath);
  const paths = new Set();
  for (const imagePath of imagePaths) {
    const relative = normalizeRelativePath(imagePath);
    if (LOCAL_IMAGE_EXTENSION.test(relative)) {
      paths.add(joinManifestPath(manifestDirectory, relative));
    }
  }
  return paths;
}

export function validateImageReferencesGranted(manifestPath, imagePaths, grantedPaths) {
  const granted = new Set(grantedPaths);
  for (const path of collectImageProjectPaths(manifestPath, imagePaths)) {
    if (!granted.has(path)) {
      throw new Error(`保存请求尝试读取载入时未授权的本地图片：${path}`);
    }
  }
}

export function validateNewPageBackups(newPagePaths, backups) {
  for (const path of newPagePaths) {
    if (backups.get(path)?.existed) {
      throw new Error(`不能用新页面写入覆盖当前文稿未授权的现存文件：${path}`);
    }
  }
}

export function validatePageClosure(
  requiredPagePaths,
  existingPaths,
  changes,
  { declaredPagePaths = requiredPagePaths } = {},
) {
  const requiredPages = new Set(requiredPagePaths);
  const existing = new Set(existingPaths);
  const declared = new Set(declaredPagePaths);
  const byPath = new Map(changes.map((change) => [change.path, change]));
  for (const change of changes) {
    if (
      change.operation === "put"
      && change.path.toLowerCase().endsWith(".page")
      && !requiredPages.has(change.path)
    ) {
      throw new Error(`不能写入新清单未引用的页面：${change.path}`);
    }
  }
  for (const pagePath of requiredPages) {
    if (existing.has(pagePath) && !declared.has(pagePath)) {
      throw new Error(`不能把当前文稿未授权的现存页面加入清单：${pagePath}`);
    }
    const change = byPath.get(pagePath);
    const existsAfterSave = change ? change.operation === "put" : existing.has(pagePath);
    if (!existsAfterSave) {
      throw new Error(`保存会让 PPTD 清单引用缺失页面：${pagePath}`);
    }
  }
}

/** Keep failed save paths sticky until successful writes cover those paths. */
export function recordSaveFailure(
  previous,
  revision,
  error,
  attemptPaths,
  { unscoped = false, pathAliases = null } = {},
) {
  const sameRevision = previous?.revision === revision ? previous : null;
  const aliases = new Map();
  let evidenceOverflow = false;
  for (const [alias, canonical] of sameRevision?.pathAliases ?? []) {
    if (aliases.size >= MAX_SAVE_FAILURE_ALIASES) {
      evidenceOverflow = true;
      break;
    }
    aliases.set(alias, canonical);
  }
  let aliasConflict = false;
  if (pathAliases) {
    for (const [alias, canonical] of pathAliases) {
      const previousCanonical = aliases.get(alias);
      if (previousCanonical && previousCanonical !== canonical) aliasConflict = true;
      else if (!aliases.has(alias)) {
        if (aliases.size >= MAX_SAVE_FAILURE_ALIASES) evidenceOverflow = true;
        else aliases.set(alias, canonical);
      }
    }
  }
  const attempted = new Set();
  for (const path of attemptPaths) {
    const canonical = aliases.get(path) ?? path;
    if (attempted.has(canonical)) continue;
    if (attempted.size >= MAX_SAVE_FAILURE_PATHS) evidenceOverflow = true;
    else attempted.add(canonical);
  }
  const paths = new Set();
  for (const path of sameRevision?.paths ?? []) {
    if (paths.size >= MAX_SAVE_FAILURE_PATHS) {
      evidenceOverflow = true;
      break;
    }
    paths.add(path);
  }
  for (const path of attempted) {
    if (paths.has(path)) continue;
    if (paths.size >= MAX_SAVE_FAILURE_PATHS) evidenceOverflow = true;
    else paths.add(path);
  }
  return {
    revision,
    error,
    paths,
    pathAliases: aliases,
    unscoped: Boolean(sameRevision?.unscoped)
      || unscoped
      || aliasConflict
      || evidenceOverflow
      || attempted.size === 0,
  };
}

export function resolveSaveFailure(
  failure,
  revision,
  successfulPaths,
  { pathAliases = null } = {},
) {
  if (!failure || failure.revision !== revision || failure.unscoped) return failure;
  const aliases = new Map(failure.pathAliases ?? []);
  if (pathAliases) {
    for (const [alias, canonical] of pathAliases) {
      if (!aliases.has(alias) && aliases.size < MAX_SAVE_FAILURE_ALIASES) {
        aliases.set(alias, canonical);
      }
    }
  }
  const remaining = new Set(failure.paths);
  for (const path of successfulPaths) remaining.delete(aliases.get(path) ?? path);
  return remaining.size === 0 ? null : { ...failure, paths: remaining, pathAliases: aliases };
}

/**
 * Validate an untrusted editor save payload before any filesystem operation.
 * Unknown operations are rejected instead of being coerced into a write.
 */
export function normalizeSaveChanges(
  payload,
  manifestDirectory,
  {
    maxChangeCount = 600,
    maxFileBytes = 20 * 1024 * 1024,
    maxTotalBytes = 100 * 1024 * 1024,
    pathAliases,
    requireKnownPaths = false,
  } = {},
) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("保存请求必须是对象");
  }
  if (payload.changes === undefined) return [];
  if (!Array.isArray(payload.changes)) throw new Error("保存请求的 changes 必须是数组");
  if (payload.changes.length > maxChangeCount) {
    throw new Error(`一次保存不能超过 ${maxChangeCount} 个文件`);
  }

  const encoder = new TextEncoder();
  const seen = new Set();
  let totalBytes = 0;
  return payload.changes.map((change) => {
    if (!change || typeof change !== "object" || Array.isArray(change)) {
      throw new Error("保存变更必须是对象");
    }
    const requestedPath = normalizeRelativePath(change.path);
    const path = pathAliases?.get(requestedPath)
      ?? (requireKnownPaths ? null : assertWritableChangePath(manifestDirectory, requestedPath));
    if (!path) throw new Error(`保存请求包含当前文稿未声明的路径：${requestedPath}`);
    if (seen.has(path)) throw new Error(`一次保存中存在重复路径：${path}`);
    seen.add(path);

    let operation;
    if (change.operate === "delete") operation = "delete";
    else if (["put", "create", "update"].includes(change.operate)) operation = "put";
    else throw new Error(`不支持的保存操作：${String(change.operate)}`);

    if (operation === "put" && typeof change.content !== "string") {
      throw new Error(`写入操作缺少字符串内容：${path}`);
    }
    const content = operation === "put" ? change.content : null;
    const size = content === null ? 0 : encoder.encode(content).byteLength;
    if (size > maxFileBytes) throw new Error(`保存内容超过单文件上限：${path}`);
    totalBytes += size;
    if (totalBytes > maxTotalBytes) throw new Error("单次保存总大小超过安全上限");
    return { path, operation, content };
  });
}
