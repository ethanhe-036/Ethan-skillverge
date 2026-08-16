import {
  MAX_PROJECT_DIRECTORY_DEPTH,
  normalizeRelativePath,
} from "./lib.js";

export const MAX_PROJECT_ENTRIES = 10_000;
export const MAX_PROJECT_DEPTH = MAX_PROJECT_DIRECTORY_DEPTH;

const PROJECT_INDEX_METADATA = Symbol("projectIndexMetadata");

function assertBudget(value, label) {
  if (!Number.isInteger(value) || value < 1) {
    throw new TypeError(`${label} 必须是正整数`);
  }
}

function addIndexedFile(index, path, entry) {
  const normalized = normalizeRelativePath(path);
  if (index.has(normalized)) {
    throw new Error(`项目中存在规范化后重复的文件路径：${normalized}`);
  }
  index.set(normalized, entry);
}

function addProjectEntry(entries, path) {
  const normalized = normalizeRelativePath(path);
  if (entries.has(normalized)) {
    throw new Error(`项目中存在规范化后重复的文件系统条目：${normalized}`);
  }
  entries.add(normalized);
  return normalized;
}

function finishIndex(index, entries) {
  Object.defineProperty(index, PROJECT_INDEX_METADATA, {
    configurable: false,
    enumerable: false,
    writable: false,
    value: Object.freeze({
      entryCount: entries.size,
      directories: Object.freeze(
        [...entries].filter((path) => !index.has(path)),
      ),
    }),
  });
  return index;
}

function directoryPrefixes(path) {
  const parts = normalizeRelativePath(path).split("/");
  const prefixes = [];
  for (let index = 1; index < parts.length; index += 1) {
    prefixes.push(parts.slice(0, index).join("/"));
  }
  return prefixes;
}

export function projectMetadataFromPaths(paths) {
  const entries = new Set();
  const directories = new Set();
  for (const path of paths) {
    const normalized = normalizeRelativePath(path);
    entries.add(normalized);
    for (const directory of directoryPrefixes(normalized)) {
      entries.add(directory);
      directories.add(directory);
    }
  }
  return { entryCount: entries.size, directories };
}

export function projectIndexMetadata(index) {
  const metadata = index?.[PROJECT_INDEX_METADATA];
  if (metadata) {
    return {
      entryCount: metadata.entryCount,
      directories: new Set(metadata.directories),
    };
  }
  return projectMetadataFromPaths(index?.keys?.() ?? []);
}

/**
 * Index an explicitly authorized project without allowing an unbounded tree to
 * monopolize the browser's main thread. Directory entries, not just files, are
 * counted so a wide tree of empty folders cannot bypass the cap.
 */
export async function indexProjectDirectory(
  directoryHandle,
  {
    maxEntries = MAX_PROJECT_ENTRIES,
    maxDepth = MAX_PROJECT_DEPTH,
  } = {},
) {
  assertBudget(maxEntries, "项目条目上限");
  assertBudget(maxDepth, "项目目录深度上限");
  const index = new Map();
  const entries = new Set();
  let entryCount = 0;

  async function walk(handle, prefix = "", depth = 0) {
    for await (const [name, entry] of handle.entries()) {
      if (name === ".DS_Store") continue;
      entryCount += 1;
      if (entryCount > maxEntries) {
        throw new Error(`项目条目超过 ${maxEntries} 个安全上限`);
      }
      const path = prefix ? `${prefix}/${name}` : name;
      if (entry.kind === "directory") {
        if (depth >= maxDepth) {
          throw new Error(`项目目录深度超过 ${maxDepth} 层安全上限：${path}`);
        }
        const normalized = addProjectEntry(entries, path);
        await walk(entry, normalized, depth + 1);
      } else if (entry.kind === "file") {
        const normalized = addProjectEntry(entries, path);
        addIndexedFile(index, normalized, entry);
      } else {
        throw new Error(`项目包含不支持的文件系统条目：${path}`);
      }
    }
  }

  await walk(directoryHandle);
  return finishIndex(index, entries);
}

/** Index the read-only folder-upload fallback under the same resource policy. */
export function indexUploadedProjectFiles(
  fileList,
  {
    maxEntries = MAX_PROJECT_ENTRIES,
    maxDepth = MAX_PROJECT_DEPTH,
  } = {},
) {
  assertBudget(maxEntries, "项目条目上限");
  assertBudget(maxDepth, "项目目录深度上限");
  const fileCount = fileList?.length;
  if (!Number.isSafeInteger(fileCount) || fileCount < 0) {
    throw new TypeError("文件夹上传结果必须是可计数的 FileList");
  }
  // Every uploaded file is at least one project entry. Reject this lower
  // bound before Array.from so an oversized/malicious FileList cannot force an
  // unbounded materialization ahead of the directory-aware budget below.
  if (fileCount > maxEntries) {
    throw new Error(`项目文件超过 ${maxEntries} 个安全上限`);
  }
  const files = [];
  for (let index = 0; index < fileCount; index += 1) {
    const file = fileList[index] ?? fileList.item?.(index);
    if (!file || typeof file !== "object") {
      throw new Error(`文件夹上传结果缺少索引 ${index} 的文件`);
    }
    files.push(file);
  }
  const firstPath = files[0]?.webkitRelativePath || files[0]?.name || "";
  const rootName = firstPath.includes("/") ? firstPath.split("/")[0] : "";
  const index = new Map();
  const entries = new Set();
  const directories = new Set();
  for (const file of files) {
    let path = file.webkitRelativePath || file.name;
    if (rootName && path.startsWith(`${rootName}/`)) {
      path = path.slice(rootName.length + 1);
    }
    const normalized = normalizeRelativePath(path);
    const directoryDepth = Math.max(0, normalized.split("/").length - 1);
    if (directoryDepth > maxDepth) {
      throw new Error(`项目目录深度超过 ${maxDepth} 层安全上限：${normalized}`);
    }
    for (const directory of directoryPrefixes(normalized)) {
      if (index.has(directory)) {
        throw new Error(`项目中同一路径不能同时是文件和目录：${directory}`);
      }
      entries.add(directory);
      directories.add(directory);
    }
    if (directories.has(normalized)) {
      throw new Error(`项目中同一路径不能同时是文件和目录：${normalized}`);
    }
    entries.add(normalized);
    if (entries.size > maxEntries) {
      throw new Error(`项目条目超过 ${maxEntries} 个安全上限`);
    }
    addIndexedFile(index, normalized, {
      kind: "file",
      getFile: async () => file,
    });
  }
  return finishIndex(index, entries);
}
