export const JOURNAL_AUTHORIZATION_STORAGE_KEY = "open-kimi-ppt.save-journal-authorization.v2";

const AUTHORIZATION_VERSION = 2;
const AUTHORIZATION_STATES = new Set(["preparing", "armed", "committed"]);
const IDENTIFIER_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const DIRECTORY_SCOPE_DB_NAME = "open-kimi-ppt-host-state";
const DIRECTORY_SCOPE_STORE_NAME = "directoryScopes";
const DIRECTORY_SCOPE_LOCK_NAME = "open-kimi-ppt:directory-scope:v1";
const PROJECT_JOURNAL_LOCK_PREFIX = "open-kimi-ppt:save-journal:v2:";
const MAX_DIRECTORY_SCOPES = 256;

function authorizationError(message, options) {
  const error = new Error(message, options);
  error.code = "SAVE_JOURNAL_AUTHORIZATION";
  error.saveScopeUncertain = true;
  return error;
}

function assertIdentifier(value, label) {
  if (typeof value !== "string" || !IDENTIFIER_PATTERN.test(value)) {
    throw authorizationError(`${label}无效；项目已保持冻结`);
  }
  return value;
}

function normalizeEvidence(raw, expectedProjectScopeId) {
  if (
    !raw
    || typeof raw !== "object"
    || raw.version !== AUTHORIZATION_VERSION
    || !AUTHORIZATION_STATES.has(raw.state)
    || raw.projectScopeId !== expectedProjectScopeId
    || typeof raw.transactionId !== "string"
    || !IDENTIFIER_PATTERN.test(raw.transactionId)
    || typeof raw.journalDigest !== "string"
    || !SHA256_PATTERN.test(raw.journalDigest)
    || !Number.isSafeInteger(raw.journalBytes)
    || raw.journalBytes < 1
  ) {
    throw authorizationError("保存恢复授权证据无效或不属于当前项目；项目已保持冻结");
  }
  return Object.freeze({
    version: AUTHORIZATION_VERSION,
    state: raw.state,
    projectScopeId: raw.projectScopeId,
    transactionId: raw.transactionId,
    journalDigest: raw.journalDigest,
    journalBytes: raw.journalBytes,
  });
}

function sameTransaction(left, right) {
  return left.projectScopeId === right.projectScopeId
    && left.transactionId === right.transactionId
    && left.journalDigest === right.journalDigest
    && left.journalBytes === right.journalBytes;
}

function normalizeTransaction(evidence, projectScopeId) {
  return normalizeEvidence({
    version: AUTHORIZATION_VERSION,
    state: evidence?.state ?? "preparing",
    projectScopeId: evidence?.projectScopeId ?? projectScopeId,
    transactionId: evidence?.transactionId,
    journalDigest: evidence?.journalDigest,
    journalBytes: evidence?.journalBytes,
  }, projectScopeId);
}

/**
 * Store one authorization record per opaque project scope. The localStorage
 * key and value contain only random identifiers, never a directory name/path.
 */
export function createJournalAuthorizationStore(
  storageOrProvider = () => globalThis.localStorage,
  {
    keyPrefix = JOURNAL_AUTHORIZATION_STORAGE_KEY,
    projectScopeId,
  } = {},
) {
  const scopeId = assertIdentifier(projectScopeId, "保存恢复项目作用域");
  if (typeof keyPrefix !== "string" || keyPrefix.length < 1 || keyPrefix.length > 256) {
    throw new TypeError("保存恢复授权存储键前缀无效");
  }
  const key = `${keyPrefix}:${scopeId}`;
  const provider = typeof storageOrProvider === "function"
    ? storageOrProvider
    : () => storageOrProvider;

  function storage() {
    let candidate;
    try {
      candidate = provider();
    } catch (error) {
      throw authorizationError("无法访问保存恢复授权存储；已拒绝修改项目", { cause: error });
    }
    if (
      !candidate
      || typeof candidate.getItem !== "function"
      || typeof candidate.setItem !== "function"
      || typeof candidate.removeItem !== "function"
    ) {
      throw authorizationError("当前环境不支持可信的保存恢复授权存储；已拒绝修改项目");
    }
    return candidate;
  }

  function read() {
    let raw;
    try {
      raw = storage().getItem(key);
    } catch (error) {
      if (error?.code === "SAVE_JOURNAL_AUTHORIZATION") throw error;
      throw authorizationError("无法读取保存恢复授权证据；项目已保持冻结", { cause: error });
    }
    if (raw === null) return null;
    if (typeof raw !== "string" || raw.length > 1280) {
      throw authorizationError("保存恢复授权证据超过安全上限；项目已保持冻结");
    }
    try {
      return normalizeEvidence(JSON.parse(raw), scopeId);
    } catch (error) {
      if (error?.code === "SAVE_JOURNAL_AUTHORIZATION") throw error;
      throw authorizationError("保存恢复授权证据不是有效 JSON；项目已保持冻结", { cause: error });
    }
  }

  function write(evidence) {
    const normalized = normalizeEvidence(evidence, scopeId);
    try {
      storage().setItem(key, JSON.stringify(normalized));
    } catch (error) {
      throw authorizationError("无法持久化保存恢复授权证据；已拒绝修改项目", { cause: error });
    }
    const confirmed = read();
    if (!confirmed || confirmed.state !== normalized.state || !sameTransaction(confirmed, normalized)) {
      throw authorizationError("保存恢复授权证据写入后校验失败；已拒绝修改项目");
    }
    return confirmed;
  }

  function transition(evidence, fromState, toState) {
    const transaction = normalizeTransaction(evidence, scopeId);
    const current = read();
    if (!current || current.state !== fromState || !sameTransaction(current, transaction)) {
      throw authorizationError(
        `保存恢复授权不能从 ${fromState} 转换到 ${toState}；项目已保持冻结`,
      );
    }
    return write({ ...transaction, state: toState });
  }

  return Object.freeze({
    projectScopeId: scopeId,
    storageKey: key,
    read,
    prepare(evidence) {
      if (read()) {
        throw authorizationError("当前项目存在另一笔未收口的保存恢复授权；请先重新载入");
      }
      const transaction = normalizeTransaction(evidence, scopeId);
      return write({ ...transaction, state: "preparing" });
    },
    arm(evidence) {
      return transition(evidence, "preparing", "armed");
    },
    commit(evidence) {
      return transition(evidence, "armed", "committed");
    },
    clear(evidence, { states = ["preparing", "armed", "committed"] } = {}) {
      const transaction = normalizeTransaction(evidence, scopeId);
      const current = read();
      if (!current) return false;
      if (!states.includes(current.state) || !sameTransaction(current, transaction)) {
        throw authorizationError("拒绝清除不匹配的保存恢复授权证据；项目已保持冻结");
      }
      try {
        storage().removeItem(key);
      } catch (error) {
        throw authorizationError("无法清除保存恢复授权证据；项目已保持冻结", { cause: error });
      }
      if (read() !== null) {
        throw authorizationError("保存恢复授权证据清除后校验失败；项目已保持冻结");
      }
      return true;
    },
  });
}

export function journalAuthorizationMatches(evidence, transaction, { state } = {}) {
  if (!evidence) return false;
  if (state && evidence.state !== state) return false;
  try {
    return sameTransaction(
      evidence,
      normalizeTransaction(transaction, evidence.projectScopeId),
    );
  } catch {
    return false;
  }
}

function requestResult(request, label) {
  return new Promise((resolve, reject) => {
    request.addEventListener("success", () => resolve(request.result), { once: true });
    request.addEventListener("error", () => reject(
      authorizationError(`${label}失败；无法建立稳定项目作用域`, { cause: request.error }),
    ), { once: true });
  });
}

function transactionFinished(transaction, label) {
  return new Promise((resolve, reject) => {
    transaction.addEventListener("complete", () => resolve(), { once: true });
    transaction.addEventListener("abort", () => reject(
      authorizationError(`${label}被中止；无法建立稳定项目作用域`, { cause: transaction.error }),
    ), { once: true });
    transaction.addEventListener("error", () => reject(
      authorizationError(`${label}失败；无法建立稳定项目作用域`, { cause: transaction.error }),
    ), { once: true });
  });
}

export function createIndexedDbDirectoryScopeRegistry(
  indexedDbOrProvider = () => globalThis.indexedDB,
) {
  const provider = typeof indexedDbOrProvider === "function"
    ? indexedDbOrProvider
    : () => indexedDbOrProvider;

  async function openDatabase() {
    let indexedDb;
    try {
      indexedDb = provider();
    } catch (error) {
      throw authorizationError("无法访问目录作用域数据库", { cause: error });
    }
    if (!indexedDb || typeof indexedDb.open !== "function") {
      throw authorizationError("当前环境不支持目录作用域所需的 IndexedDB");
    }
    const request = indexedDb.open(DIRECTORY_SCOPE_DB_NAME, 1);
    request.addEventListener("upgradeneeded", () => {
      if (!request.result.objectStoreNames.contains(DIRECTORY_SCOPE_STORE_NAME)) {
        request.result.createObjectStore(DIRECTORY_SCOPE_STORE_NAME, { keyPath: "projectScopeId" });
      }
    }, { once: true });
    request.addEventListener("blocked", () => {
      request.transaction?.abort?.();
    }, { once: true });
    return requestResult(request, "打开目录作用域数据库");
  }

  return Object.freeze({
    async list(limit = MAX_DIRECTORY_SCOPES + 1) {
      if (!Number.isSafeInteger(limit) || limit < 1) {
        throw new TypeError("目录作用域读取上限必须是正安全整数");
      }
      const database = await openDatabase();
      try {
        const transaction = database.transaction(DIRECTORY_SCOPE_STORE_NAME, "readonly");
        const request = transaction.objectStore(DIRECTORY_SCOPE_STORE_NAME).getAll(undefined, limit);
        const [records] = await Promise.all([
          requestResult(request, "读取目录作用域"),
          transactionFinished(transaction, "读取目录作用域"),
        ]);
        return records;
      } finally {
        database.close();
      }
    },
    async add(record) {
      const database = await openDatabase();
      try {
        const transaction = database.transaction(DIRECTORY_SCOPE_STORE_NAME, "readwrite");
        transaction.objectStore(DIRECTORY_SCOPE_STORE_NAME).add(record);
        await transactionFinished(transaction, "保存目录作用域");
      } finally {
        database.close();
      }
    },
    async remove(projectScopeId) {
      assertIdentifier(projectScopeId, "待清理的目录项目作用域");
      const database = await openDatabase();
      try {
        const transaction = database.transaction(DIRECTORY_SCOPE_STORE_NAME, "readwrite");
        transaction.objectStore(DIRECTORY_SCOPE_STORE_NAME).delete(projectScopeId);
        await transactionFinished(transaction, "清理已失效目录作用域");
      } finally {
        database.close();
      }
    },
  });
}

/** Resolve one opaque, persistent ID for a directory without storing its path. */
export function createDirectoryProjectScopeResolver({
  registry = createIndexedDbDirectoryScopeRegistry(),
  lockManagerOrProvider = () => globalThis.navigator?.locks,
  idFactory = () => globalThis.crypto?.randomUUID?.(),
  maxScopes = MAX_DIRECTORY_SCOPES,
} = {}) {
  if (
    !registry
    || typeof registry.list !== "function"
    || typeof registry.add !== "function"
    || typeof registry.remove !== "function"
  ) {
    throw new TypeError("目录项目作用域 registry 无效");
  }
  if (!Number.isSafeInteger(maxScopes) || maxScopes < 1) {
    throw new TypeError("目录项目作用域上限必须是正安全整数");
  }
  const lockProvider = typeof lockManagerOrProvider === "function"
    ? lockManagerOrProvider
    : () => lockManagerOrProvider;

  async function resolveLocked(directoryHandle) {
    while (true) {
      const records = await registry.list(maxScopes + 1);
      if (!Array.isArray(records) || records.length > maxScopes + 1) {
        throw authorizationError("目录项目作用域 registry 返回了无界结果");
      }
      const revokedScopeIds = [];
      let matchingScopeId = null;
      for (const record of records) {
        const recordScopeId = assertIdentifier(record?.projectScopeId, "已保存的目录项目作用域");
        if (!record.directoryHandle || record.directoryHandle.kind !== "directory") {
          // Structurally invalid host records cannot designate any directory.
          // Removing the mapping is safe: any associated journal subsequently
          // lacks a scope-matching authorization and therefore freezes closed.
          revokedScopeIds.push(recordScopeId);
          continue;
        }
        try {
          if (await directoryHandle.isSameEntry(record.directoryHandle)) {
            matchingScopeId = recordScopeId;
            continue;
          }
        } catch {
          // Equivalence failure alone can be transient; it is not prune proof.
        }
        if (typeof record.directoryHandle.queryPermission === "function") {
          try {
            const permission = await record.directoryHandle.queryPermission({ mode: "read" });
            if (permission === "denied") revokedScopeIds.push(recordScopeId);
          } catch {
            // A query failure is not sufficient evidence of permanent revocation.
          }
        }
      }
      for (const revokedScopeId of revokedScopeIds) await registry.remove(revokedScopeId);
      if (matchingScopeId) return matchingScopeId;
      if (records.length === maxScopes + 1 && revokedScopeIds.length > 0) continue;

      const activeCount = records.length - revokedScopeIds.length;
      if (activeCount >= maxScopes) {
        throw authorizationError(`目录项目作用域已达到 ${maxScopes} 个安全上限`);
      }
      const randomId = idFactory();
      if (typeof randomId !== "string" || !IDENTIFIER_PATTERN.test(randomId)) {
        throw authorizationError("宿主无法生成随机目录项目作用域");
      }
      const projectScopeId = assertIdentifier(`dir-${randomId}`, "新目录项目作用域");
      await registry.add({ projectScopeId, directoryHandle });
      return projectScopeId;
    }
  }

  return Object.freeze({
    async resolve(directoryHandle) {
      if (
        !directoryHandle
        || directoryHandle.kind !== "directory"
        || typeof directoryHandle.isSameEntry !== "function"
      ) {
        throw authorizationError("无法为无效目录句柄分配保存恢复作用域");
      }
      let lockManager;
      try {
        lockManager = lockProvider();
      } catch (error) {
        throw authorizationError("无法访问目录作用域并发锁", { cause: error });
      }
      if (!lockManager || typeof lockManager.request !== "function") {
        throw authorizationError("当前环境缺少目录作用域所需的跨标签页并发锁");
      }
      return lockManager.request(
        DIRECTORY_SCOPE_LOCK_NAME,
        { mode: "exclusive" },
        () => resolveLocked(directoryHandle),
      );
    },
  });
}

/**
 * Serialize the complete journal + deck mutation transaction for one opaque
 * directory scope across every tab on this origin. Scope allocation has its
 * own global lock; this lock protects recovery and begin -> writes -> finish.
 */
export function createProjectJournalLock(
  projectScopeId,
  lockManagerOrProvider = () => globalThis.navigator?.locks,
) {
  const scopeId = assertIdentifier(projectScopeId, "保存事务项目作用域");
  const provider = typeof lockManagerOrProvider === "function"
    ? lockManagerOrProvider
    : () => lockManagerOrProvider;
  const lockName = `${PROJECT_JOURNAL_LOCK_PREFIX}${scopeId}`;

  return Object.freeze({
    projectScopeId: scopeId,
    lockName,
    async runExclusive(callback) {
      if (typeof callback !== "function") {
        throw new TypeError("保存事务锁回调必须是函数");
      }
      let lockManager;
      try {
        lockManager = provider();
      } catch (error) {
        throw authorizationError("无法访问保存事务并发锁；已拒绝修改项目", { cause: error });
      }
      if (!lockManager || typeof lockManager.request !== "function") {
        throw authorizationError("当前环境缺少保存事务所需的跨标签页并发锁");
      }
      return lockManager.request(lockName, { mode: "exclusive" }, callback);
    },
  });
}

export function createEphemeralProjectScope(kind, idFactory = () => globalThis.crypto?.randomUUID?.()) {
  if (kind !== "memory" && kind !== "upload") {
    throw new TypeError("临时项目作用域只能是 memory 或 upload");
  }
  const randomId = idFactory();
  if (typeof randomId !== "string" || !IDENTIFIER_PATTERN.test(randomId)) {
    throw authorizationError("宿主无法生成随机临时项目作用域");
  }
  return assertIdentifier(`${kind}-${randomId}`, "临时项目作用域");
}

export function saveJournalAuthorizationError(message, options) {
  return authorizationError(message, options);
}
