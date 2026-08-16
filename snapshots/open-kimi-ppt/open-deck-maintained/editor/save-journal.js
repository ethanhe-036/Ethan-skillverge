import { normalizeRelativePath } from "./lib.js";
import {
  deleteVersionedFile,
  fileVersionsEqual,
  readVersionedBackup,
  versionForContent,
  writeVersionedFile,
} from "./file-transaction.js";
import { assertJsonResourceLimits } from "./restricted-parser.js";
import {
  journalAuthorizationMatches,
  saveJournalAuthorizationError,
} from "./journal-authorization.js";

export const SAVE_JOURNAL_PATH = ".open-kimi-ppt-save-journal.json";
export const MAX_SAVE_JOURNAL_BYTES = 160 * 1024 * 1024;
const JOURNAL_VERSION = 1;
const MAX_JOURNAL_ENTRIES = 600;
const BASE64_CHUNK_BYTES = 768 * 1024;
const MAX_BASE64_CHUNK_CHARS = 4 * Math.ceil(BASE64_CHUNK_BYTES / 3);
const BASE64_PATTERN = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;
const TRANSACTION_ID_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/;

export function saveJournalPathAliasKey(path) {
  // JS has no direct Unicode case-fold primitive. Upper-then-lower performs
  // the multi-character folds (for example sharp-s) that lowercasing alone
  // misses; NFC on both sides also collapses canonically equivalent names.
  return normalizeRelativePath(path)
    .normalize("NFC")
    .toLocaleUpperCase("und")
    .toLocaleLowerCase("und")
    .normalize("NFC");
}

export function isReservedSaveJournalPath(path) {
  return saveJournalPathAliasKey(path) === saveJournalPathAliasKey(SAVE_JOURNAL_PATH);
}

export function assertNoReservedSaveJournalPathAliases(paths) {
  for (const path of paths) {
    if (isReservedSaveJournalPath(path)) {
      throw new Error(`项目包含保留的保存恢复日志路径或其大小写/Unicode 别名：${path}`);
    }
  }
}

function assertAuthorizationStore(store) {
  if (
    !store
    || typeof store.read !== "function"
    || typeof store.prepare !== "function"
    || typeof store.arm !== "function"
    || typeof store.commit !== "function"
    || typeof store.clear !== "function"
    || typeof store.projectScopeId !== "string"
  ) {
    throw saveJournalAuthorizationError("缺少项目目录之外的保存恢复授权存储；已拒绝修改项目");
  }
  return store;
}

function assertManifestPath(path) {
  const normalized = normalizeRelativePath(path);
  if (isReservedSaveJournalPath(normalized) || !normalized.toLocaleLowerCase("und").endsWith(".pptd")) {
    throw new Error(`保存恢复日志的文稿清单必须是非保留的 .pptd 文件：${normalized}`);
  }
  return normalized;
}

function assertProtocolEntryPath(path, manifestPath) {
  const normalized = normalizeRelativePath(path);
  const alias = saveJournalPathAliasKey(normalized);
  const extension = normalized.toLocaleLowerCase("und");
  if (isReservedSaveJournalPath(normalized)) {
    throw new Error(`保存恢复日志包含保留路径：${normalized}`);
  }
  if (extension.endsWith(".pptd")) {
    if (alias !== saveJournalPathAliasKey(manifestPath)) {
      throw new Error(`保存恢复日志只能修改当前 .pptd 清单：${normalized}`);
    }
  } else if (!extension.endsWith(".page")) {
    throw new Error(`保存恢复日志只能包含 .pptd 或 .page 文件：${normalized}`);
  }
  return normalized;
}

function assertPositiveSafeLimit(value, name) {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new TypeError(`${name} 必须是正安全整数`);
  }
}

function bytesToBase64Chunks(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunks = [];
  for (let offset = 0; offset < bytes.byteLength; offset += BASE64_CHUNK_BYTES) {
    const slice = bytes.subarray(offset, Math.min(offset + BASE64_CHUNK_BYTES, bytes.byteLength));
    let binary = "";
    for (let cursor = 0; cursor < slice.byteLength; cursor += 0x8000) {
      binary += String.fromCharCode(...slice.subarray(cursor, cursor + 0x8000));
    }
    chunks.push(btoa(binary));
  }
  return chunks;
}

function base64ChunksToBytes(chunks, expectedSize, maxBytes) {
  if (!Array.isArray(chunks)) throw new Error("保存恢复日志的备份分块格式无效");
  if (!Number.isSafeInteger(expectedSize) || expectedSize < 0 || expectedSize > maxBytes) {
    throw new Error("保存恢复日志声明的备份大小超过安全上限");
  }
  const expectedChunkCount = Math.ceil(expectedSize / BASE64_CHUNK_BYTES);
  if (chunks.length !== expectedChunkCount) {
    throw new Error("保存恢复日志的备份分块数量不一致");
  }
  const output = new Uint8Array(expectedSize);
  let offset = 0;
  for (let chunkIndex = 0; chunkIndex < chunks.length; chunkIndex += 1) {
    const encoded = chunks[chunkIndex];
    const expectedChunkBytes = Math.min(BASE64_CHUNK_BYTES, expectedSize - offset);
    const expectedEncodedChars = 4 * Math.ceil(expectedChunkBytes / 3);
    if (
      typeof encoded !== "string"
      || encoded.length !== expectedEncodedChars
      || encoded.length > MAX_BASE64_CHUNK_CHARS
      || !BASE64_PATTERN.test(encoded)
    ) {
      throw new Error("保存恢复日志包含无效的 base64 备份");
    }
    const binary = atob(encoded);
    if (binary.length !== expectedChunkBytes || offset + binary.length > output.byteLength) {
      throw new Error("保存恢复日志的备份大小不一致");
    }
    for (let index = 0; index < binary.length; index += 1) {
      output[offset + index] = binary.charCodeAt(index);
    }
    offset += binary.length;
  }
  if (offset !== expectedSize) throw new Error("保存恢复日志的备份长度不完整");
  return output.buffer;
}

function assertVersionShape(
  version,
  { allowMissing = true, intended = false, maxFileBytes } = {},
) {
  if (!version || typeof version !== "object") throw new Error("保存恢复日志缺少文件版本");
  if (version.kind === "missing") {
    if (!allowMissing) throw new Error("保存恢复日志中的文件版本不能缺失");
    return;
  }
  if (
    version.kind !== "file"
    || !Number.isSafeInteger(version.size)
    || version.size < 0
    || version.size > maxFileBytes
    || typeof version.digest !== "string"
    || !/^[0-9a-f]{64}$/.test(version.digest)
    || (!intended && (!Number.isFinite(version.lastModified) || version.lastModified < 0))
  ) {
    throw new Error("保存恢复日志包含无效的文件版本");
  }
}

function normalizeJournalEntry(raw, seen, limits, manifestPath) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("保存恢复日志包含无效的事务条目");
  }
  const path = assertProtocolEntryPath(raw.path, manifestPath);
  const alias = saveJournalPathAliasKey(path);
  if (seen.has(alias)) {
    throw new Error(`保存恢复日志包含重复或保留路径：${path}`);
  }
  seen.add(alias);
  if (raw.operation !== "put" && raw.operation !== "delete") {
    throw new Error(`保存恢复日志包含未知操作：${path}`);
  }
  if (alias === saveJournalPathAliasKey(manifestPath) && raw.operation === "delete") {
    throw new Error(`保存恢复日志不能删除当前 .pptd 清单：${path}`);
  }
  assertVersionShape(raw.beforeVersion, { maxFileBytes: limits.maxFileBytes });
  assertVersionShape(raw.intendedVersion, {
    intended: true,
    maxFileBytes: limits.maxFileBytes,
  });
  if (raw.operation === "delete" && raw.intendedVersion.kind !== "missing") {
    throw new Error(`删除事务的目标版本必须是 missing：${path}`);
  }
  if (raw.operation === "put" && raw.intendedVersion.kind !== "file") {
    throw new Error(`写入事务的目标版本必须是 file：${path}`);
  }
  const backupChunks = raw.beforeVersion.kind === "file" ? raw.backupChunks : [];
  if (raw.beforeVersion.kind === "file" && !Array.isArray(backupChunks)) {
    throw new Error(`保存恢复日志缺少原文件备份：${path}`);
  }
  if (
    raw.beforeVersion.kind === "missing"
    && raw.backupChunks !== undefined
    && (!Array.isArray(raw.backupChunks) || raw.backupChunks.length > 0)
  ) {
    throw new Error(`保存恢复日志为不存在的文件携带了备份：${path}`);
  }
  if (raw.beforeVersion.kind === "file") {
    limits.backupBytes += raw.beforeVersion.size;
    if (limits.backupBytes > limits.maxBackupBytes) {
      throw new Error("保存恢复日志声明的备份总量超过安全上限");
    }
    const expectedChunkCount = Math.ceil(raw.beforeVersion.size / BASE64_CHUNK_BYTES);
    if (backupChunks.length !== expectedChunkCount) {
      throw new Error(`保存恢复日志的备份分块数量不一致：${path}`);
    }
    for (const encoded of backupChunks) {
      if (typeof encoded !== "string" || encoded.length > MAX_BASE64_CHUNK_CHARS) {
        throw new Error(`保存恢复日志的备份分块超过安全上限：${path}`);
      }
    }
  }
  return Object.freeze({
    path,
    operation: raw.operation,
    beforeVersion: Object.freeze({ ...raw.beforeVersion }),
    intendedVersion: Object.freeze({ ...raw.intendedVersion }),
    backupChunks: Object.freeze([...(backupChunks ?? [])]),
  });
}

function parseJournal(text, { maxJournalBytes, maxFileBytes, maxBackupBytes }) {
  assertJsonResourceLimits(text, {
    label: "保存恢复日志",
    maxBytes: maxJournalBytes,
    maxNodes: MAX_JOURNAL_ENTRIES * 128,
    maxDepth: 16,
    maxStringChars: Math.min(maxJournalBytes, MAX_BASE64_CHUNK_CHARS),
    maxTotalStringChars: maxJournalBytes,
  });
  let raw;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    throw new Error(`保存恢复日志不是有效 JSON：${error.message}`, { cause: error });
  }
  if (
    !raw
    || typeof raw !== "object"
    || raw.version !== JOURNAL_VERSION
    || !Array.isArray(raw.entries)
    || raw.entries.length > MAX_JOURNAL_ENTRIES
  ) {
    throw new Error("保存恢复日志的版本或条目数量无效");
  }
  if (
    typeof raw.transactionId !== "string"
    || !TRANSACTION_ID_PATTERN.test(raw.transactionId)
  ) {
    throw new Error("保存恢复日志缺少有效事务 ID");
  }
  const manifestPath = assertManifestPath(raw.manifestPath);
  const seen = new Set();
  const limits = { maxFileBytes, maxBackupBytes, backupBytes: 0 };
  return Object.freeze({
    version: JOURNAL_VERSION,
    transactionId: raw.transactionId,
    manifestPath,
    entries: Object.freeze(
      raw.entries.map((entry) => normalizeJournalEntry(entry, seen, limits, manifestPath)),
    ),
  });
}

function matchesIntended(currentVersion, intendedVersion) {
  if (intendedVersion.kind === "missing") return currentVersion.kind === "missing";
  return currentVersion.kind === "file"
    && currentVersion.size === intendedVersion.size
    && currentVersion.digest === intendedVersion.digest;
}

export function orderChangesForCrashConsistency(changes, manifestPath) {
  const manifest = [];
  const pageWrites = [];
  const otherWrites = [];
  const deletes = [];
  for (const change of changes) {
    if (change.operation === "delete") deletes.push(change);
    else if (change.path === manifestPath) manifest.push(change);
    else if (change.path.toLowerCase().endsWith(".page")) pageWrites.push(change);
    else otherWrites.push(change);
  }
  return [...pageWrites, ...otherWrites, ...manifest, ...deletes];
}

export async function hasPendingSaveJournal(directoryHandle) {
  try {
    const handle = await directoryHandle.getFileHandle(SAVE_JOURNAL_PATH, { create: false });
    const file = await handle.getFile();
    return file.size >= 0;
  } catch (error) {
    if (error?.name === "NotFoundError") return false;
    throw error;
  }
}

export async function beginSaveJournal(
  context,
  changes,
  backups,
  {
    manifestPath,
    maxBytes = MAX_SAVE_JOURNAL_BYTES,
    maxBackupBytes = maxBytes,
    authorizationStore,
  } = {},
) {
  assertPositiveSafeLimit(maxBytes, "maxBytes");
  assertPositiveSafeLimit(maxBackupBytes, "maxBackupBytes");
  if (!Array.isArray(changes) || changes.length > MAX_JOURNAL_ENTRIES) {
    throw new Error(`保存恢复日志的事务条目不能超过 ${MAX_JOURNAL_ENTRIES}`);
  }
  const trustedAuthorization = assertAuthorizationStore(
    authorizationStore ?? context?.authorizationStore,
  );
  const canonicalManifestPath = assertManifestPath(manifestPath);
  const existing = await readVersionedBackup(context, SAVE_JOURNAL_PATH, { maxBytes });
  if (existing.existed) {
    throw new Error("检测到上次未完成的保存恢复日志；请重新载入项目后再保存");
  }

  const entries = [];
  let backupBytes = 0;
  const seenPaths = new Set();
  for (const change of changes) {
    const canonicalPath = assertProtocolEntryPath(change?.path, canonicalManifestPath);
    const pathAlias = saveJournalPathAliasKey(canonicalPath);
    if (seenPaths.has(pathAlias)) {
      throw new Error(`保存恢复日志包含重复或保留路径：${canonicalPath}`);
    }
    seenPaths.add(pathAlias);
    if (change.operation !== "put" && change.operation !== "delete") {
      throw new Error(`保存恢复日志包含未知操作：${canonicalPath}`);
    }
    if (
      pathAlias === saveJournalPathAliasKey(canonicalManifestPath)
      && change.operation === "delete"
    ) {
      throw new Error(`保存恢复日志不能删除当前 .pptd 清单：${canonicalPath}`);
    }
    const backup = backups.get(change.path);
    if (!backup) throw new Error(`保存恢复日志缺少事务备份：${change.path}`);
    const intendedVersion = change.operation === "delete"
      ? Object.freeze({ kind: "missing" })
      : await versionForContent(change.content);
    if (backup.existed) {
      backupBytes += backup.bytes.byteLength;
      if (backupBytes > maxBackupBytes) {
        throw new Error("保存恢复日志需要的原文件备份总量超过安全上限");
      }
      const backupContentVersion = await versionForContent(backup.bytes);
      if (
        backupContentVersion.size !== backup.version.size
        || backupContentVersion.digest !== backup.version.digest
      ) {
        throw new Error(`保存恢复日志的原文件备份版本不一致：${change.path}`);
      }
    }
    entries.push({
      path: canonicalPath,
      operation: change.operation,
      beforeVersion: backup.version,
      intendedVersion,
      backupChunks: backup.existed ? bytesToBase64Chunks(backup.bytes) : [],
    });
  }

  const transactionId = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
  const serialized = JSON.stringify({
    version: JOURNAL_VERSION,
    transactionId,
    manifestPath: canonicalManifestPath,
    entries,
  });
  const journalContentVersion = await versionForContent(serialized);
  const byteLength = journalContentVersion.size;
  if (byteLength > maxBytes) {
    throw new Error(`保存恢复日志超过 ${Math.floor(maxBytes / 1024 / 1024)} MiB 安全上限`);
  }
  const authorization = Object.freeze({
    projectScopeId: trustedAuthorization.projectScopeId,
    transactionId,
    journalDigest: journalContentVersion.digest,
    journalBytes: byteLength,
  });
  // "preparing" is durable before the project gets even a zero-byte journal.
  // beginSaveJournal cannot return until the complete journal is verified and
  // the evidence advances to "armed", so preparing proves deck mutations did
  // not start and is the only state in which an incomplete journal is cleanable.
  trustedAuthorization.prepare(authorization);
  const version = await writeVersionedFile(
    context,
    SAVE_JOURNAL_PATH,
    serialized,
    existing.version,
    { maxBytes, operation: "创建保存恢复日志" },
  );
  if (
    version.size !== journalContentVersion.size
    || version.digest !== journalContentVersion.digest
  ) {
    throw saveJournalAuthorizationError("保存恢复日志写入后的摘要不匹配；已拒绝修改文稿");
  }
  trustedAuthorization.arm(authorization);
  return Object.freeze({
    path: SAVE_JOURNAL_PATH,
    version,
    byteLength,
    ...authorization,
  });
}

export async function finishSaveJournal(
  context,
  journal,
  {
    maxBytes = MAX_SAVE_JOURNAL_BYTES,
    authorizationStore,
  } = {},
) {
  const trustedAuthorization = assertAuthorizationStore(
    authorizationStore ?? context?.authorizationStore,
  );
  const authorization = {
    projectScopeId: journal?.projectScopeId,
    transactionId: journal?.transactionId,
    journalDigest: journal?.journalDigest,
    journalBytes: journal?.byteLength,
  };
  const currentAuthorization = trustedAuthorization.read();
  if (
    !journalAuthorizationMatches(currentAuthorization, authorization)
    || (currentAuthorization.state !== "armed" && currentAuthorization.state !== "committed")
  ) {
    throw saveJournalAuthorizationError("完成保存时缺少匹配的宿主授权；恢复日志与项目均保持冻结");
  }
  const snapshot = await readVersionedBackup(context, SAVE_JOURNAL_PATH, { maxBytes });
  if (snapshot.existed) {
    if (
      snapshot.version.size !== journal.byteLength
      || snapshot.version.digest !== journal.journalDigest
    ) {
      throw saveJournalAuthorizationError("完成保存时恢复日志已被替换或篡改；项目保持冻结");
    }
  } else if (currentAuthorization.state === "armed") {
    throw saveJournalAuthorizationError(
      "保存恢复日志在 committed 前缺失；无法证明文稿已完整提交，项目保持冻结",
    );
  }
  let durableCommitReached = currentAuthorization.state === "committed";
  try {
    if (currentAuthorization.state === "armed") {
      // The caller reaches finish only after every deck mutation and directory
      // invariant is confirmed (or after a complete in-process rollback).
      // Persist that fact before deleting the journal so a crash in the cleanup
      // window cannot make a coherent deck look like an interrupted save.
      trustedAuthorization.commit(authorization);
      durableCommitReached = true;
    }
    if (snapshot.existed) {
      await deleteVersionedFile(
        context,
        SAVE_JOURNAL_PATH,
        snapshot.version,
        { maxBytes, operation: "完成保存恢复日志" },
      );
    }
    trustedAuthorization.clear(authorization, { states: ["committed"] });
  } catch (error) {
    // Finalization starts only after every deck mutation (or every in-process
    // rollback) is a verified coherent version. Even the host authorization
    // transition has a write-then-confirm ambiguity: a confirmation read may
    // fail after `committed` was durably written. Never start another deck
    // rollback from this point. Scoped recovery will either roll an `armed`
    // transaction back or finish cleanup for a `committed` transaction.
    const cleanupError = new Error(
      durableCommitReached
        ? "文稿已完整提交，但恢复日志清理未完成；已冻结后续保存，请重新载入完成收口"
        : "文稿写入已完成，但恢复事务最终状态未确认；已冻结后续保存，请重新载入恢复",
      { cause: error },
    );
    cleanupError.code = "SAVE_JOURNAL_CLEANUP_PENDING";
    cleanupError.journalFinalizationStarted = true;
    cleanupError.durableCommitReached = durableCommitReached;
    cleanupError.mayHaveApplied = true;
    cleanupError.saveScopeUncertain = true;
    throw cleanupError;
  }
}

export async function recoverPendingSaveJournal(
  directoryHandle,
  {
    maxJournalBytes = MAX_SAVE_JOURNAL_BYTES,
    maxFileBytes,
    maxBackupBytes = maxJournalBytes,
    authorizationStore,
  },
) {
  assertPositiveSafeLimit(maxJournalBytes, "maxJournalBytes");
  assertPositiveSafeLimit(maxFileBytes, "maxFileBytes");
  assertPositiveSafeLimit(maxBackupBytes, "maxBackupBytes");
  const trustedAuthorization = assertAuthorizationStore(authorizationStore);
  const context = { directoryHandle, fileIndex: new Map() };
  const journalSnapshot = await readVersionedBackup(
    context,
    SAVE_JOURNAL_PATH,
    { maxBytes: maxJournalBytes },
  );
  const authorization = trustedAuthorization.read();
  if (!journalSnapshot.existed) {
    if (!authorization) {
      return Object.freeze({ recovered: false, restoredVersions: new Map() });
    }
    if (authorization.state === "preparing") {
      trustedAuthorization.clear(authorization, { states: ["preparing"] });
      return Object.freeze({
        recovered: false,
        cleanedPreparation: true,
        restoredVersions: new Map(),
      });
    }
    if (authorization.state === "committed") {
      trustedAuthorization.clear(authorization, { states: ["committed"] });
      return Object.freeze({
        recovered: false,
        cleanedCommitted: true,
        restoredVersions: new Map(),
      });
    }
    throw saveJournalAuthorizationError(
      "检测到已授权保存但项目恢复日志缺失；无法证明文稿未被修改，项目已保持冻结",
    );
  }

  if (!authorization) {
    throw saveJournalAuthorizationError(
      "项目内的保存恢复日志没有宿主侧授权证据；可能是伪造文件，未修改任何项目内容",
    );
  }

  if (authorization.state === "preparing") {
    // A preparing record is written before journal creation and begin cannot
    // return until it becomes armed. Therefore no deck mutation can have
    // started, even if journal creation stopped at zero bytes or partial data.
    await deleteVersionedFile(
      context,
      SAVE_JOURNAL_PATH,
      journalSnapshot.version,
      { maxBytes: maxJournalBytes, operation: "清理未完成创建的保存恢复日志" },
    );
    trustedAuthorization.clear(authorization, { states: ["preparing"] });
    return Object.freeze({
      recovered: false,
      cleanedPreparation: true,
      restoredVersions: new Map(),
    });
  }

  if (
    journalSnapshot.version.size !== authorization.journalBytes
    || journalSnapshot.version.digest !== authorization.journalDigest
  ) {
    throw saveJournalAuthorizationError(
      "保存恢复日志与宿主授权摘要不匹配（可能被截断或篡改）；未写入任何项目文件",
    );
  }

  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(journalSnapshot.bytes);
  } catch (error) {
    throw new Error("保存恢复日志不是有效 UTF-8；为避免覆盖文件，项目已保持冻结", { cause: error });
  }
  const journal = parseJournal(text, {
    maxJournalBytes,
    maxFileBytes,
    maxBackupBytes,
  });
  if (!journalAuthorizationMatches(authorization, {
    projectScopeId: trustedAuthorization.projectScopeId,
    transactionId: journal.transactionId,
    journalDigest: journalSnapshot.version.digest,
    journalBytes: journalSnapshot.version.size,
  })) {
    throw saveJournalAuthorizationError(
      "保存恢复日志的事务 ID 或完整摘要与宿主授权不匹配；未写入任何项目文件",
    );
  }
  if (authorization.state === "committed") {
    await deleteVersionedFile(
      context,
      SAVE_JOURNAL_PATH,
      journalSnapshot.version,
      { maxBytes: maxJournalBytes, operation: "清理已提交保存的恢复日志" },
    );
    trustedAuthorization.clear(authorization, { states: ["committed"] });
    return Object.freeze({
      recovered: false,
      cleanedCommitted: true,
      manifestPath: journal.manifestPath,
      restoredVersions: new Map(),
    });
  }
  if (authorization.state !== "armed") {
    throw saveJournalAuthorizationError("保存恢复授权状态无效；未写入任何项目文件");
  }
  const decodedBackups = new Map();
  for (const entry of journal.entries) {
    if (entry.beforeVersion.kind !== "file") continue;
    const bytes = base64ChunksToBytes(
      entry.backupChunks,
      entry.beforeVersion.size,
      maxFileBytes,
    );
    const decodedVersion = await versionForContent(bytes);
    if (
      decodedVersion.size !== entry.beforeVersion.size
      || decodedVersion.digest !== entry.beforeVersion.digest
    ) {
      throw new Error(`保存恢复日志的备份摘要不匹配，项目保持冻结：${entry.path}`);
    }
    decodedBackups.set(entry.path, bytes);
  }
  const snapshots = new Map();
  const conflicts = [];
  for (const entry of journal.entries) {
    const current = await readVersionedBackup(context, entry.path, { maxBytes: maxFileBytes });
    snapshots.set(entry.path, current);
    if (
      !fileVersionsEqual(current.version, entry.beforeVersion)
      && !matchesIntended(current.version, entry.intendedVersion)
    ) {
      conflicts.push(entry.path);
    }
  }
  if (conflicts.length > 0) {
    const error = new Error(
      `未完成保存与外部文件修改发生冲突，未自动覆盖：${conflicts.join(", ")}`,
    );
    error.code = "SAVE_JOURNAL_CONFLICT";
    error.paths = Object.freeze(conflicts);
    throw error;
  }

  const restoredVersions = new Map();
  for (const entry of [...journal.entries].reverse()) {
    const current = snapshots.get(entry.path);
    if (fileVersionsEqual(current.version, entry.beforeVersion)) {
      restoredVersions.set(entry.path, current.version);
      continue;
    }
    const restored = entry.beforeVersion.kind === "missing"
      ? await deleteVersionedFile(
        context,
        entry.path,
        current.version,
        { maxBytes: maxFileBytes, operation: "恢复中断保存" },
      )
      : await writeVersionedFile(
        context,
        entry.path,
        decodedBackups.get(entry.path),
        current.version,
        { maxBytes: maxFileBytes, operation: "恢复中断保存" },
      );
    restoredVersions.set(entry.path, restored);
  }

  // Recovery has now produced one complete old deck. Mark it coherent before
  // journal deletion so the same armed→missing uncertainty window cannot
  // occur while cleaning up a successful recovery.
  trustedAuthorization.commit(authorization);
  await deleteVersionedFile(
    context,
    SAVE_JOURNAL_PATH,
    journalSnapshot.version,
    { maxBytes: maxJournalBytes, operation: "清理已恢复的保存日志" },
  );
  trustedAuthorization.clear(authorization, { states: ["committed"] });
  return Object.freeze({
    recovered: true,
    manifestPath: journal.manifestPath,
    restoredVersions,
  });
}
