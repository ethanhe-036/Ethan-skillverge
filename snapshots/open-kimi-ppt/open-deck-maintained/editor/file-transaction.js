const DEFAULT_STABILITY_ATTEMPTS = 3;

function isNotFound(error) {
  return error?.name === "NotFoundError";
}

function notFoundError(path) {
  const error = new Error(`找不到文件：${path}`);
  error.name = "NotFoundError";
  return error;
}

async function getFileHandle(root, path, create = false) {
  const parts = path.split("/");
  const fileName = parts.pop();
  let directory = root;
  for (const part of parts) {
    // Save-plan authorizes new files only in directories that existed at
    // project load. Never create intermediate directories here: a failed
    // write would otherwise leave untracked empty entries and could make the
    // project exceed its own reload budget.
    directory = await directory.getDirectoryHandle(part, { create: false });
  }
  if (!fileName) throw notFoundError(path);
  return {
    directory,
    fileName,
    handle: await directory.getFileHandle(fileName, { create }),
  };
}

function bytesToHex(bytes) {
  return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
}

async function digestBytes(bytes) {
  if (!globalThis.crypto?.subtle) {
    throw new Error("当前浏览器缺少文件并发校验所需的 SHA-256 支持");
  }
  return bytesToHex(new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes)));
}

async function contentBytes(content) {
  if (typeof content === "string") return new TextEncoder().encode(content).buffer;
  if (content instanceof ArrayBuffer) return content.slice(0);
  if (ArrayBuffer.isView(content)) {
    return content.buffer.slice(content.byteOffset, content.byteOffset + content.byteLength);
  }
  if (content && typeof content.arrayBuffer === "function") return content.arrayBuffer();
  throw new TypeError("文件内容必须是字符串、Blob、ArrayBuffer 或 TypedArray");
}

export async function versionForContent(content) {
  const bytes = await contentBytes(content);
  return Object.freeze({
    kind: "file",
    size: bytes.byteLength,
    digest: await digestBytes(bytes),
  });
}

export function fileVersionsEqual(left, right) {
  if (left.kind !== right.kind) return false;
  if (left.kind === "missing") return true;
  return left.size === right.size
    && left.lastModified === right.lastModified
    && left.digest === right.digest;
}

function sameContent(version, bytes, digest) {
  return version.kind === "file"
    && version.size === bytes.byteLength
    && version.digest === digest;
}

async function readOnce(root, path, maxBytes) {
  try {
    const resolved = await getFileHandle(root, path, false);
    const file = await resolved.handle.getFile();
    if (file.size > maxBytes) {
      const error = new Error(`无法安全检查超过 ${Math.floor(maxBytes / 1024 / 1024)} MiB 的文件：${path}`);
      error.code = "FILE_SNAPSHOT_TOO_LARGE";
      throw error;
    }
    const bytes = await file.arrayBuffer();
    if (bytes.byteLength > maxBytes) {
      const error = new Error(`无法安全检查超过 ${Math.floor(maxBytes / 1024 / 1024)} MiB 的文件：${path}`);
      error.code = "FILE_SNAPSHOT_TOO_LARGE";
      throw error;
    }
    return {
      existed: true,
      bytes,
      handle: resolved.handle,
      version: Object.freeze({
        kind: "file",
        size: bytes.byteLength,
        lastModified: file.lastModified,
        digest: await digestBytes(bytes),
      }),
    };
  } catch (error) {
    if (!isNotFound(error)) throw error;
    return {
      existed: false,
      bytes: null,
      handle: null,
      version: Object.freeze({ kind: "missing" }),
    };
  }
}

async function readStableSnapshot(root, path, maxBytes) {
  for (let attempt = 0; attempt < DEFAULT_STABILITY_ATTEMPTS; attempt += 1) {
    const first = await readOnce(root, path, maxBytes);
    const second = await readOnce(root, path, maxBytes);
    if (fileVersionsEqual(first.version, second.version)) return second;
  }
  const error = new Error(`文件在读取期间持续变化，无法取得稳定版本：${path}`);
  error.code = "FILE_SNAPSHOT_UNSTABLE";
  throw error;
}

function syncIndex(context, path, snapshot) {
  if (snapshot.existed && snapshot.handle) context.fileIndex.set(path, snapshot.handle);
  else context.fileIndex.delete(path);
}

export class ExternalFileConflictError extends Error {
  constructor(path, operation, detail, options = {}) {
    const rollback = operation === "回滚";
    const phase = options.phase === "after" ? "后" : "前";
    super(
      rollback
        ? `回滚${phase}检测到外部文件已变更，已保留外部版本并跳过该路径：${path}${detail ? `（${detail}）` : ""}`
        : `${operation}${phase}检测到外部文件已变更，已取消后续操作且未覆盖外部版本：${path}${detail ? `（${detail}）` : ""}`,
      options,
    );
    this.name = "ExternalFileConflictError";
    this.code = "EXTERNAL_FILE_CONFLICT";
    this.path = path;
    this.operation = operation;
    this.preservedExternalVersion = true;
    this.saveScopeUncertain = true;
    this.mayHaveApplied = options.phase === "after";
  }
}

function uncertainMutationError(path, operation, detail, cause) {
  const error = new Error(
    `${operation}可能已经修改磁盘，但无法确认最终文件版本：${path}`
      + `${detail ? `（${detail}）` : ""}`,
    cause ? { cause } : undefined,
  );
  error.name = "FileMutationUncertainError";
  error.code = "FILE_MUTATION_UNCERTAIN";
  error.path = path;
  error.operation = operation;
  error.mayHaveApplied = true;
  error.saveScopeUncertain = true;
  return error;
}

async function assertVersion(context, path, expectedVersion, maxBytes, operation) {
  let current;
  try {
    current = await readStableSnapshot(context.directoryHandle, path, maxBytes);
  } catch (error) {
    if (error?.code !== "FILE_SNAPSHOT_TOO_LARGE" && error?.code !== "FILE_SNAPSHOT_UNSTABLE") {
      throw error;
    }
    throw new ExternalFileConflictError(path, operation, error.message, { cause: error });
  }
  syncIndex(context, path, current);
  if (!fileVersionsEqual(current.version, expectedVersion)) {
    throw new ExternalFileConflictError(path, operation, "版本与事务快照不一致");
  }
  return current;
}

export function assertBackupMatchesBaseline(path, backup, baseline, operation = "保存") {
  if (!baseline || !fileVersionsEqual(backup.version, baseline)) {
    throw new ExternalFileConflictError(
      path,
      operation,
      baseline ? "文件在文稿载入后已被外部修改" : "缺少文稿载入时的文件版本基线",
    );
  }
  return backup;
}

export async function readVersionedBackup(context, path, { maxBytes }) {
  const snapshot = await readStableSnapshot(context.directoryHandle, path, maxBytes);
  syncIndex(context, path, snapshot);
  return Object.freeze({
    existed: snapshot.existed,
    bytes: snapshot.bytes,
    version: snapshot.version,
  });
}

export async function writeVersionedFile(
  context,
  path,
  content,
  expectedVersion,
  { maxBytes, operation = "写入" },
) {
  const intendedBytes = await contentBytes(content);
  if (intendedBytes.byteLength > maxBytes) {
    throw new Error(`无法安全写入超过 ${Math.floor(maxBytes / 1024 / 1024)} MiB 的文件：${path}`);
  }
  const intendedDigest = await digestBytes(intendedBytes);
  const current = await assertVersion(context, path, expectedVersion, maxBytes, operation);
  const resolved = current.existed
    ? await getFileHandle(context.directoryHandle, path, false)
    : await getFileHandle(context.directoryHandle, path, true);
  let preCloseVersion = expectedVersion;
  let createdPlaceholder = false;
  if (!current.existed) {
    let created;
    try {
      created = await readStableSnapshot(context.directoryHandle, path, maxBytes);
    } catch (error) {
      throw uncertainMutationError(
        path,
        operation,
        "创建新文件后无法确认初始版本",
        error,
      );
    }
    syncIndex(context, path, created);
    if (!created.existed || created.version.size !== 0) {
      throw new ExternalFileConflictError(
        path,
        operation,
        "新文件路径在创建期间被其他程序占用",
      );
    }
    createdPlaceholder = true;
    preCloseVersion = created.version;
  }

  let writable;
  try {
    writable = await resolved.handle.createWritable();
  } catch (error) {
    if (createdPlaceholder) {
      try {
        await deleteVersionedFile(
          context,
          path,
          preCloseVersion,
          { maxBytes, operation: "清理未完成的新文件" },
        );
      } catch (cleanupError) {
        if (cleanupError?.preservedExternalVersion) throw cleanupError;
        throw uncertainMutationError(
          path,
          operation,
          "创建写入器失败，且无法安全清理新文件",
          new AggregateError([error, cleanupError]),
        );
      }
    }
    throw error;
  }
  let closeStarted = false;
  try {
    await writable.write(content);
    // FileSystemWritableFileStream commits its private swap file on close.
    // Re-check the visible file after staging bytes and immediately before
    // that commit to narrow the unavoidable non-atomic CAS window.
    await assertVersion(context, path, preCloseVersion, maxBytes, operation);
    closeStarted = true;
    await writable.close();
  } catch (error) {
    await writable.abort?.().catch(() => undefined);
    if (closeStarted) {
      throw uncertainMutationError(path, operation, "关闭写入器时提交结果未知", error);
    }
    if (createdPlaceholder) {
      try {
        await deleteVersionedFile(
          context,
          path,
          preCloseVersion,
          { maxBytes, operation: "清理未完成的新文件" },
        );
      } catch (cleanupError) {
        if (cleanupError?.preservedExternalVersion) throw error;
        throw uncertainMutationError(
          path,
          operation,
          "写入提交前失败，且无法安全清理新文件",
          new AggregateError([error, cleanupError]),
        );
      }
    }
    throw error;
  }

  let written;
  try {
    written = await readStableSnapshot(context.directoryHandle, path, maxBytes);
  } catch (error) {
    throw uncertainMutationError(path, operation, "写入后读取验证失败", error);
  }
  syncIndex(context, path, written);
  if (!sameContent(written.version, intendedBytes, intendedDigest)) {
    throw new ExternalFileConflictError(
      path,
      operation,
      "文件内容被再次更改，已保留当前版本",
      { phase: "after" },
    );
  }
  return written.version;
}

export async function deleteVersionedFile(
  context,
  path,
  expectedVersion,
  { maxBytes, operation = "删除" },
) {
  const current = await assertVersion(context, path, expectedVersion, maxBytes, operation);
  if (current.existed) {
    const resolved = await getFileHandle(context.directoryHandle, path, false);
    await assertVersion(context, path, expectedVersion, maxBytes, operation);
    try {
      await resolved.directory.removeEntry(resolved.fileName);
    } catch (error) {
      throw uncertainMutationError(path, operation, "删除调用的提交结果未知", error);
    }
  }

  let deleted;
  try {
    deleted = await readStableSnapshot(context.directoryHandle, path, maxBytes);
  } catch (error) {
    throw uncertainMutationError(path, operation, "删除后读取验证失败", error);
  }
  syncIndex(context, path, deleted);
  if (deleted.existed) {
    throw new ExternalFileConflictError(
      path,
      operation,
      "路径被再次创建，已保留当前版本",
      { phase: "after" },
    );
  }
  return deleted.version;
}

export async function rollbackVersionedChanges(context, applied, { maxBytes }) {
  const entries = [...applied.entries()].reverse();
  const errors = [];
  let preservedExternalVersions = 0;
  const restoredVersions = new Map();
  for (const [path, record] of entries) {
    try {
      if (record.backup.existed) {
        const restoredVersion = await writeVersionedFile(
          context,
          path,
          record.backup.bytes,
          record.writtenVersion,
          { maxBytes, operation: "回滚" },
        );
        restoredVersions.set(path, restoredVersion);
      } else {
        const restoredVersion = await deleteVersionedFile(
          context,
          path,
          record.writtenVersion,
          { maxBytes, operation: "回滚" },
        );
        restoredVersions.set(path, restoredVersion);
      }
    } catch (error) {
      if (error?.preservedExternalVersion) preservedExternalVersions += 1;
      errors.push(new Error(`${path}: ${error.message}`, { cause: error }));
    }
  }
  if (errors.length) {
    const preservation = preservedExternalVersions
      ? `；其中 ${preservedExternalVersions} 个路径检测到外部修改，已保留外部版本而未覆盖`
      : "";
    const aggregate = new AggregateError(
      errors,
      `${errors.length} 个文件未能回滚${preservation}`,
    );
    aggregate.restoredVersions = restoredVersions;
    aggregate.saveScopeUncertain = true;
    throw aggregate;
  }
  return restoredVersions;
}

export const __test = Object.freeze({ fileVersionsEqual });
