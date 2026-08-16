import assert from "node:assert/strict";
import test from "node:test";
import {
  assertNoReservedSaveJournalPathAliases,
  beginSaveJournal,
  finishSaveJournal,
  hasPendingSaveJournal,
  orderChangesForCrashConsistency,
  recoverPendingSaveJournal,
  SAVE_JOURNAL_PATH,
} from "../editor/save-journal.js";
import {
  createDirectoryProjectScopeResolver,
  createJournalAuthorizationStore,
  createProjectJournalLock,
  JOURNAL_AUTHORIZATION_STORAGE_KEY,
} from "../editor/journal-authorization.js";
import {
  deleteVersionedFile,
  readVersionedBackup,
  versionForContent,
  writeVersionedFile,
} from "../editor/file-transaction.js";

const MAX_BYTES = 1024 * 1024;
const encoder = new TextEncoder();
const decoder = new TextDecoder();
let nextProjectScope = 1;

function missing(path) {
  const error = new Error(`missing: ${path}`);
  error.name = "NotFoundError";
  return error;
}

async function toBytes(content) {
  if (typeof content === "string") return encoder.encode(content);
  if (content instanceof ArrayBuffer) return new Uint8Array(content.slice(0));
  if (ArrayBuffer.isView(content)) {
    return new Uint8Array(
      content.buffer.slice(content.byteOffset, content.byteOffset + content.byteLength),
    );
  }
  return new Uint8Array(await content.arrayBuffer());
}

class MemoryStorage {
  constructor() {
    this.values = new Map();
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }

  removeItem(key) {
    this.values.delete(key);
  }
}

class MemoryScopeRegistry {
  constructor(records = []) {
    this.records = [...records];
  }

  async list(limit) {
    return this.records.slice(0, limit);
  }

  async add(record) {
    if (this.records.some((entry) => entry.projectScopeId === record.projectScopeId)) {
      throw new Error(`duplicate scope: ${record.projectScopeId}`);
    }
    this.records.push(record);
  }

  async remove(projectScopeId) {
    this.records = this.records.filter((entry) => entry.projectScopeId !== projectScopeId);
  }
}

class SharedLockManager {
  constructor() {
    this.tail = Promise.resolve();
  }

  request(_name, _options, callback) {
    const result = this.tail.then(callback);
    this.tail = result.catch(() => undefined);
    return result;
  }
}

function directoryHandle(token, permission = "granted") {
  return {
    kind: "directory",
    token,
    async isSameEntry(other) {
      return other?.token === token;
    },
    async queryPermission() {
      return permission;
    },
  };
}

class MemoryFileSystem {
  constructor(
    initial = {},
    {
      authorizationStorage = new MemoryStorage(),
      projectScopeId = `dir-test-${nextProjectScope++}`,
    } = {},
  ) {
    this.entries = new Map();
    this.clock = 100;
    for (const [path, content] of Object.entries(initial)) this.externalSet(path, content);
    this.root = this.directory("");
    this.authorizationStorage = authorizationStorage;
    this.projectScopeId = projectScopeId;
    this.authorizationStore = createJournalAuthorizationStore(
      this.authorizationStorage,
      { projectScopeId },
    );
  }

  directory(prefix) {
    return {
      getDirectoryHandle: async (name, { create } = {}) => {
        const next = prefix ? `${prefix}/${name}` : name;
        const hasChild = [...this.entries.keys()].some((path) => path.startsWith(`${next}/`));
        if (!create && !hasChild) throw missing(next);
        return this.directory(next);
      },
      getFileHandle: async (name, { create } = {}) => {
        const path = prefix ? `${prefix}/${name}` : name;
        if (!this.entries.has(path)) {
          if (!create) throw missing(path);
          this.entries.set(path, { bytes: new Uint8Array(), lastModified: ++this.clock });
        }
        return this.fileHandle(path);
      },
      removeEntry: async (name) => {
        const path = prefix ? `${prefix}/${name}` : name;
        if (!this.entries.delete(path)) throw missing(path);
      },
    };
  }

  fileHandle(path) {
    return {
      getFile: async () => {
        const entry = this.entries.get(path);
        if (!entry) throw missing(path);
        const snapshot = entry.bytes.slice();
        return {
          size: snapshot.byteLength,
          lastModified: entry.lastModified,
          arrayBuffer: async () => snapshot.buffer.slice(0),
        };
      },
      createWritable: async () => {
        let staged = null;
        return {
          write: async (content) => {
            staged = await toBytes(content);
          },
          close: async () => {
            this.entries.set(path, {
              bytes: (staged ?? new Uint8Array()).slice(),
              lastModified: ++this.clock,
            });
          },
          abort: async () => undefined,
        };
      },
    };
  }

  externalSet(path, content) {
    this.entries.set(path, { bytes: encoder.encode(content), lastModified: ++this.clock });
  }

  text(path) {
    const entry = this.entries.get(path);
    return entry ? decoder.decode(entry.bytes) : null;
  }

  context() {
    return {
      directoryHandle: this.root,
      fileIndex: new Map(),
      authorizationStore: this.authorizationStore,
    };
  }
}

function recoveryOptions(fs, extra = {}) {
  return {
    maxJournalBytes: MAX_BYTES,
    maxFileBytes: MAX_BYTES,
    maxBackupBytes: MAX_BYTES,
    authorizationStore: fs.authorizationStore,
    ...extra,
  };
}

async function replaceHostAuthorizationForCurrentJournal(fs, state = "armed") {
  const serialized = fs.text(SAVE_JOURNAL_PATH);
  const journal = JSON.parse(serialized);
  const version = await versionForContent(serialized);
  fs.authorizationStorage.setItem(fs.authorizationStore.storageKey, JSON.stringify({
    version: 2,
    state,
    projectScopeId: fs.projectScopeId,
    transactionId: journal.transactionId,
    journalDigest: version.digest,
    journalBytes: version.size,
  }));
}

async function backupsFor(context, changes) {
  const backups = new Map();
  for (const change of changes) {
    backups.set(
      change.path,
      await readVersionedBackup(context, change.path, { maxBytes: MAX_BYTES }),
    );
  }
  return backups;
}

test("orders page writes before manifest and defers deletes", () => {
  const changes = [
    { path: "old.page", operation: "delete" },
    { path: "presentation.pptd", operation: "put" },
    { path: "notes.page", operation: "put" },
    { path: "new.page", operation: "put" },
  ];
  assert.deepEqual(
    orderChangesForCrashConsistency(changes, "presentation.pptd").map((item) => item.path),
    ["notes.page", "new.page", "presentation.pptd", "old.page"],
  );
});

test("recovers a crash after a partially applied multi-file save", async () => {
  const fs = new MemoryFileSystem({
    "presentation.pptd": "pages: [old.page]",
    "old.page": "OLD PAGE",
  });
  const context = fs.context();
  const changes = orderChangesForCrashConsistency([
    { path: "presentation.pptd", operation: "put", content: "pages: [new.page]" },
    { path: "new.page", operation: "put", content: "NEW PAGE" },
    { path: "old.page", operation: "delete" },
  ], "presentation.pptd");
  const backups = await backupsFor(context, changes);
  await beginSaveJournal(context, changes, backups, {
    manifestPath: "presentation.pptd",
    maxBytes: MAX_BYTES,
  });

  const newBackup = backups.get("new.page");
  await writeVersionedFile(
    context,
    "new.page",
    "NEW PAGE",
    newBackup.version,
    { maxBytes: MAX_BYTES },
  );
  const manifestBackup = backups.get("presentation.pptd");
  await writeVersionedFile(
    context,
    "presentation.pptd",
    "pages: [new.page]",
    manifestBackup.version,
    { maxBytes: MAX_BYTES },
  );

  const result = await recoverPendingSaveJournal(fs.root, recoveryOptions(fs));
  assert.equal(result.recovered, true);
  assert.equal(fs.text("presentation.pptd"), "pages: [old.page]");
  assert.equal(fs.text("old.page"), "OLD PAGE");
  assert.equal(fs.text("new.page"), null);
  assert.equal(fs.text(SAVE_JOURNAL_PATH), null);
});

test("recovers to the complete old deck after every mutation boundary", async () => {
  for (let appliedCount = 0; appliedCount <= 3; appliedCount += 1) {
    const fs = new MemoryFileSystem({
      "presentation.pptd": "pages: [old.page]",
      "old.page": "OLD PAGE",
    });
    const context = fs.context();
    const changes = orderChangesForCrashConsistency([
      { path: "old.page", operation: "delete" },
      { path: "presentation.pptd", operation: "put", content: "pages: [new.page]" },
      { path: "new.page", operation: "put", content: "NEW PAGE" },
    ], "presentation.pptd");
    const backups = await backupsFor(context, changes);
    await beginSaveJournal(context, changes, backups, {
      manifestPath: "presentation.pptd",
      maxBytes: MAX_BYTES,
    });

    for (const change of changes.slice(0, appliedCount)) {
      const backup = backups.get(change.path);
      if (change.operation === "delete") {
        await deleteVersionedFile(
          context,
          change.path,
          backup.version,
          { maxBytes: MAX_BYTES },
        );
      } else {
        await writeVersionedFile(
          context,
          change.path,
          change.content,
          backup.version,
          { maxBytes: MAX_BYTES },
        );
      }
    }

    await recoverPendingSaveJournal(fs.root, recoveryOptions(fs));
    assert.equal(fs.text("presentation.pptd"), "pages: [old.page]", `boundary ${appliedCount}`);
    assert.equal(fs.text("old.page"), "OLD PAGE", `boundary ${appliedCount}`);
    assert.equal(fs.text("new.page"), null, `boundary ${appliedCount}`);
    assert.equal(fs.text(SAVE_JOURNAL_PATH), null, `boundary ${appliedCount}`);
  }
});

test("preflights recovery conflicts before changing any file", async () => {
  const fs = new MemoryFileSystem({
    "presentation.pptd": "OLD MANIFEST",
    "one.page": "OLD PAGE",
  });
  const context = fs.context();
  const changes = orderChangesForCrashConsistency([
    { path: "presentation.pptd", operation: "put", content: "NEW MANIFEST" },
    { path: "one.page", operation: "put", content: "NEW PAGE" },
  ], "presentation.pptd");
  const backups = await backupsFor(context, changes);
  await beginSaveJournal(context, changes, backups, {
    manifestPath: "presentation.pptd",
    maxBytes: MAX_BYTES,
  });
  const pageWritten = await writeVersionedFile(
    context,
    "one.page",
    "NEW PAGE",
    backups.get("one.page").version,
    { maxBytes: MAX_BYTES },
  );
  fs.externalSet("presentation.pptd", "EXTERNAL MANIFEST");

  await assert.rejects(
    recoverPendingSaveJournal(fs.root, recoveryOptions(fs)),
    (error) => error.code === "SAVE_JOURNAL_CONFLICT"
      && error.paths.includes("presentation.pptd"),
  );
  assert.equal(fs.text("one.page"), "NEW PAGE", "preflight conflict must prevent partial recovery");
  assert.equal(fs.text("presentation.pptd"), "EXTERNAL MANIFEST");
  assert.equal(fs.text(SAVE_JOURNAL_PATH) !== null, true);
  assert.equal(pageWritten.kind, "file");
});

test("successful save removes its recovery journal", async () => {
  const fs = new MemoryFileSystem({ "presentation.pptd": "OLD" });
  const context = fs.context();
  const changes = [{ path: "presentation.pptd", operation: "put", content: "NEW" }];
  const backups = await backupsFor(context, changes);
  const journal = await beginSaveJournal(context, changes, backups, {
    manifestPath: "presentation.pptd",
    maxBytes: MAX_BYTES,
  });
  assert.equal(await hasPendingSaveJournal(fs.root), true);
  await finishSaveJournal(context, journal, { maxBytes: MAX_BYTES });
  assert.equal(await hasPendingSaveJournal(fs.root), false);
  assert.equal(fs.text(SAVE_JOURNAL_PATH), null);
  assert.equal(fs.authorizationStore.read(), null);
});

test("cleanup failure after committed preserves the complete deck for scoped recovery", async () => {
  const fs = new MemoryFileSystem({ "presentation.pptd": "OLD" });
  const context = fs.context();
  const changes = [{ path: "presentation.pptd", operation: "put", content: "NEW" }];
  const backups = await backupsFor(context, changes);
  const journal = await beginSaveJournal(context, changes, backups, {
    manifestPath: "presentation.pptd",
    maxBytes: MAX_BYTES,
  });
  await writeVersionedFile(
    context,
    "presentation.pptd",
    "NEW",
    backups.get("presentation.pptd").version,
    { maxBytes: MAX_BYTES },
  );

  const removeEntry = fs.root.removeEntry;
  fs.root.removeEntry = async (name) => {
    if (name === SAVE_JOURNAL_PATH) throw new Error("simulated journal cleanup failure");
    return removeEntry(name);
  };
  await assert.rejects(
    finishSaveJournal(context, journal, { maxBytes: MAX_BYTES }),
    (error) => error.code === "SAVE_JOURNAL_CLEANUP_PENDING"
      && error.journalFinalizationStarted === true
      && error.durableCommitReached === true
      && error.mayHaveApplied === true
      && error.saveScopeUncertain === true,
  );
  assert.equal(fs.authorizationStore.read().state, "committed");
  assert.notEqual(fs.text(SAVE_JOURNAL_PATH), null);
  assert.equal(fs.text("presentation.pptd"), "NEW");

  fs.root.removeEntry = removeEntry;
  const result = await recoverPendingSaveJournal(fs.root, recoveryOptions(fs));
  assert.equal(result.cleanedCommitted, true);
  assert.equal(fs.text("presentation.pptd"), "NEW");
  assert.equal(fs.text(SAVE_JOURNAL_PATH), null);
  assert.equal(fs.authorizationStore.read(), null);
});

test("committed authorization survives a host-clear failure without deck rollback", async () => {
  const fs = new MemoryFileSystem({ "presentation.pptd": "OLD" });
  const context = fs.context();
  const changes = [{ path: "presentation.pptd", operation: "put", content: "NEW" }];
  const backups = await backupsFor(context, changes);
  const journal = await beginSaveJournal(context, changes, backups, {
    manifestPath: "presentation.pptd",
    maxBytes: MAX_BYTES,
  });
  await writeVersionedFile(
    context,
    "presentation.pptd",
    "NEW",
    backups.get("presentation.pptd").version,
    { maxBytes: MAX_BYTES },
  );

  const removeItem = fs.authorizationStorage.removeItem.bind(fs.authorizationStorage);
  fs.authorizationStorage.removeItem = () => {
    throw new Error("simulated host authorization cleanup failure");
  };
  await assert.rejects(
    finishSaveJournal(context, journal, { maxBytes: MAX_BYTES }),
    (error) => error.code === "SAVE_JOURNAL_CLEANUP_PENDING"
      && error.journalFinalizationStarted === true
      && error.durableCommitReached === true,
  );
  assert.equal(fs.authorizationStore.read().state, "committed");
  assert.equal(fs.text(SAVE_JOURNAL_PATH), null);
  assert.equal(fs.text("presentation.pptd"), "NEW");

  fs.authorizationStorage.removeItem = removeItem;
  const result = await recoverPendingSaveJournal(fs.root, recoveryOptions(fs));
  assert.equal(result.cleanedCommitted, true);
  assert.equal(fs.text("presentation.pptd"), "NEW");
  assert.equal(fs.authorizationStore.read(), null);
});

test("uncertain committed readback starts finalization and forbids a second rollback", async () => {
  const fs = new MemoryFileSystem({ "presentation.pptd": "OLD" });
  const context = fs.context();
  const changes = [{ path: "presentation.pptd", operation: "put", content: "NEW" }];
  const backups = await backupsFor(context, changes);
  const journal = await beginSaveJournal(context, changes, backups, {
    manifestPath: "presentation.pptd",
    maxBytes: MAX_BYTES,
  });
  await writeVersionedFile(
    context,
    "presentation.pptd",
    "NEW",
    backups.get("presentation.pptd").version,
    { maxBytes: MAX_BYTES },
  );

  const getItem = fs.authorizationStorage.getItem.bind(fs.authorizationStorage);
  const setItem = fs.authorizationStorage.setItem.bind(fs.authorizationStorage);
  let failCommittedReadback = false;
  fs.authorizationStorage.setItem = (key, value) => {
    setItem(key, value);
    if (JSON.parse(value).state === "committed") failCommittedReadback = true;
  };
  fs.authorizationStorage.getItem = (key) => {
    if (failCommittedReadback) {
      failCommittedReadback = false;
      throw new Error("simulated committed confirmation read failure");
    }
    return getItem(key);
  };
  await assert.rejects(
    finishSaveJournal(context, journal, { maxBytes: MAX_BYTES }),
    (error) => error.code === "SAVE_JOURNAL_CLEANUP_PENDING"
      && error.journalFinalizationStarted === true
      && error.durableCommitReached === false,
  );
  assert.equal(fs.authorizationStore.read().state, "committed");
  assert.notEqual(fs.text(SAVE_JOURNAL_PATH), null);
  assert.equal(fs.text("presentation.pptd"), "NEW");

  fs.authorizationStorage.getItem = getItem;
  fs.authorizationStorage.setItem = setItem;
  const result = await recoverPendingSaveJournal(fs.root, recoveryOptions(fs));
  assert.equal(result.cleanedCommitted, true);
  assert.equal(fs.text("presentation.pptd"), "NEW");
  assert.equal(fs.authorizationStore.read(), null);
});

test("rejects a tampered backup digest before changing any project file", async () => {
  const fs = new MemoryFileSystem({ "presentation.pptd": "OLD" });
  const context = fs.context();
  const changes = [{ path: "presentation.pptd", operation: "put", content: "NEW" }];
  const backups = await backupsFor(context, changes);
  await beginSaveJournal(context, changes, backups, {
    manifestPath: "presentation.pptd",
    maxBytes: MAX_BYTES,
    maxBackupBytes: MAX_BYTES,
  });
  await writeVersionedFile(
    context,
    "presentation.pptd",
    "NEW",
    backups.get("presentation.pptd").version,
    { maxBytes: MAX_BYTES },
  );

  const journal = JSON.parse(fs.text(SAVE_JOURNAL_PATH));
  journal.entries[0].backupChunks = [btoa("BAD")];
  fs.externalSet(SAVE_JOURNAL_PATH, JSON.stringify(journal));

  await assert.rejects(
    recoverPendingSaveJournal(fs.root, recoveryOptions(fs)),
    /宿主授权摘要不匹配|截断或篡改/,
  );
  assert.equal(fs.text("presentation.pptd"), "NEW");
  assert.notEqual(fs.text(SAVE_JOURNAL_PATH), null);

  // Even with matching host evidence (defence in depth), the embedded backup
  // digest is independently checked before any recovery mutation.
  await replaceHostAuthorizationForCurrentJournal(fs);
  await assert.rejects(
    recoverPendingSaveJournal(fs.root, recoveryOptions(fs)),
    /备份摘要不匹配/,
  );
  assert.equal(fs.text("presentation.pptd"), "NEW");
});

test("rejects oversized journal backup declarations before allocation or recovery", async () => {
  const fs = new MemoryFileSystem({ "presentation.pptd": "OLD" });
  const context = fs.context();
  const changes = [{ path: "presentation.pptd", operation: "put", content: "NEW" }];
  const backups = await backupsFor(context, changes);
  await beginSaveJournal(context, changes, backups, {
    manifestPath: "presentation.pptd",
    maxBytes: MAX_BYTES,
    maxBackupBytes: MAX_BYTES,
  });
  const journal = JSON.parse(fs.text(SAVE_JOURNAL_PATH));
  journal.entries[0].beforeVersion.size = MAX_BYTES + 1;
  journal.entries[0].backupChunks = [];
  fs.externalSet(SAVE_JOURNAL_PATH, JSON.stringify(journal));
  await replaceHostAuthorizationForCurrentJournal(fs);

  await assert.rejects(
    recoverPendingSaveJournal(fs.root, recoveryOptions(fs)),
    /文件版本|安全上限/,
  );
  assert.equal(fs.text("presentation.pptd"), "OLD");
  assert.notEqual(fs.text(SAVE_JOURNAL_PATH), null);
});

test("journal capabilities reject victim.txt, other manifests, and alias-duplicate pages", async () => {
  const fs = new MemoryFileSystem({
    "presentation.pptd": "OLD",
    "victim.txt": "KEEP",
    "Pages/A.page": "A",
    "pages/a.page": "B",
  });
  const context = fs.context();

  const manifestProbe = [{ path: "Pages/A.page", operation: "put", content: "A2" }];
  await assert.rejects(
    beginSaveJournal(context, manifestProbe, await backupsFor(context, manifestProbe), {
      manifestPath: "presentation.page",
      maxBytes: MAX_BYTES,
    }),
    /文稿清单必须是非保留的 .pptd/,
  );

  for (const changes of [
    [{ path: "victim.txt", operation: "delete" }],
    [{ path: "other.pptd", operation: "put", content: "OTHER" }],
    [
      { path: "Pages/A.page", operation: "put", content: "A2" },
      { path: "pages/a.page", operation: "put", content: "B2" },
    ],
  ]) {
    const backups = await backupsFor(context, changes);
    await assert.rejects(
      beginSaveJournal(context, changes, backups, {
        manifestPath: "presentation.pptd",
        maxBytes: MAX_BYTES,
      }),
      /.pptd 或 .page|当前 .pptd|重复或保留/,
    );
  }

  const composed = "pag\u00e9s/a.page";
  const decomposed = "page\u0301s/a.page";
  fs.externalSet(composed, "C");
  fs.externalSet(decomposed, "D");
  const unicodeChanges = [
    { path: composed, operation: "put", content: "C2" },
    { path: decomposed, operation: "put", content: "D2" },
  ];
  await assert.rejects(
    beginSaveJournal(context, unicodeChanges, await backupsFor(context, unicodeChanges), {
      manifestPath: "presentation.pptd",
      maxBytes: MAX_BYTES,
    }),
    /重复或保留/,
  );

  assert.equal(fs.text("victim.txt"), "KEEP");
  assert.equal(fs.text(SAVE_JOURNAL_PATH), null);
  assert.equal(fs.authorizationStore.read(), null);
});

test("reserved journal name rejects case and NFC aliases from indexed projects", () => {
  assert.throws(
    () => assertNoReservedSaveJournalPathAliases([".OPEN-KIMI-PPT-SAVE-JOURNAL.JSON"]),
    /大小写\/Unicode 别名/,
  );
  assert.throws(
    () => assertNoReservedSaveJournalPathAliases([SAVE_JOURNAL_PATH.normalize("NFD")]),
    /保留的保存恢复日志路径/,
  );
});

test("a forged victim.txt journal without host evidence cannot delete files", async () => {
  const fs = new MemoryFileSystem({ "victim.txt": "KEEP" });
  fs.externalSet(SAVE_JOURNAL_PATH, JSON.stringify({
    version: 1,
    transactionId: "forged-victim",
    manifestPath: "presentation.pptd",
    entries: [{
      path: "victim.txt",
      operation: "delete",
      beforeVersion: { kind: "file", size: 4, lastModified: 1, digest: "0".repeat(64) },
      intendedVersion: { kind: "missing" },
      backupChunks: [btoa("KEEP")],
    }],
  }));

  await assert.rejects(
    recoverPendingSaveJournal(fs.root, recoveryOptions(fs)),
    (error) => error.code === "SAVE_JOURNAL_AUTHORIZATION",
  );
  assert.equal(fs.text("victim.txt"), "KEEP");
  assert.notEqual(fs.text(SAVE_JOURNAL_PATH), null);

  // Capability validation remains independent of the host evidence check.
  await replaceHostAuthorizationForCurrentJournal(fs);
  await assert.rejects(
    recoverPendingSaveJournal(fs.root, recoveryOptions(fs)),
    /只能包含 .pptd 或 .page/,
  );
  assert.equal(fs.text("victim.txt"), "KEEP");
});

test("a valid page journal copied from another project is inert without host evidence", async () => {
  const source = new MemoryFileSystem({
    "presentation.pptd": "pages: [victim.page]",
    "victim.page": "SOURCE",
  });
  const changes = [{ path: "victim.page", operation: "delete" }];
  const sourceContext = source.context();
  await beginSaveJournal(sourceContext, changes, await backupsFor(sourceContext, changes), {
    manifestPath: "presentation.pptd",
    maxBytes: MAX_BYTES,
  });

  const target = new MemoryFileSystem({ "presentation.pptd": "pages: []" });
  target.externalSet(SAVE_JOURNAL_PATH, source.text(SAVE_JOURNAL_PATH));
  await assert.rejects(
    recoverPendingSaveJournal(target.root, recoveryOptions(target)),
    (error) => error.code === "SAVE_JOURNAL_AUTHORIZATION",
  );
  assert.equal(target.text("victim.page"), null);
  assert.equal(target.text("presentation.pptd"), "pages: []");
});

test("only preparing authority can safely clean zero-byte or absent journals", async () => {
  const evidence = {
    transactionId: "zero-byte-boundary",
    journalDigest: "a".repeat(64),
    journalBytes: 123,
  };

  const absent = new MemoryFileSystem();
  absent.authorizationStore.prepare(evidence);
  const absentResult = await recoverPendingSaveJournal(absent.root, recoveryOptions(absent));
  assert.equal(absentResult.cleanedPreparation, true);
  assert.equal(absent.authorizationStore.read(), null);

  const preparing = new MemoryFileSystem();
  preparing.authorizationStore.prepare(evidence);
  preparing.externalSet(SAVE_JOURNAL_PATH, "");
  const preparingResult = await recoverPendingSaveJournal(
    preparing.root,
    recoveryOptions(preparing),
  );
  assert.equal(preparingResult.cleanedPreparation, true);
  assert.equal(preparing.text(SAVE_JOURNAL_PATH), null);
  assert.equal(preparing.authorizationStore.read(), null);

  const armed = new MemoryFileSystem();
  armed.authorizationStore.prepare(evidence);
  armed.authorizationStore.arm(evidence);
  armed.externalSet(SAVE_JOURNAL_PATH, "");
  await assert.rejects(
    recoverPendingSaveJournal(armed.root, recoveryOptions(armed)),
    (error) => error.code === "SAVE_JOURNAL_AUTHORIZATION",
  );
  assert.equal(armed.text(SAVE_JOURNAL_PATH), "");
  assert.equal(armed.authorizationStore.read().state, "armed");

  const unauthorized = new MemoryFileSystem();
  unauthorized.externalSet(SAVE_JOURNAL_PATH, "");
  await assert.rejects(
    recoverPendingSaveJournal(unauthorized.root, recoveryOptions(unauthorized)),
    (error) => error.code === "SAVE_JOURNAL_AUTHORIZATION",
  );
  assert.equal(unauthorized.text(SAVE_JOURNAL_PATH), "");
});

test("a complete journal left in preparing state is cleaned without touching the deck", async () => {
  const fs = new MemoryFileSystem({ "presentation.pptd": "OLD" });
  const context = fs.context();
  const changes = [{ path: "presentation.pptd", operation: "put", content: "NEW" }];
  await beginSaveJournal(context, changes, await backupsFor(context, changes), {
    manifestPath: "presentation.pptd",
    maxBytes: MAX_BYTES,
  });
  await replaceHostAuthorizationForCurrentJournal(fs, "preparing");

  const result = await recoverPendingSaveJournal(fs.root, recoveryOptions(fs));
  assert.equal(result.cleanedPreparation, true);
  assert.equal(fs.text("presentation.pptd"), "OLD");
  assert.equal(fs.text(SAVE_JOURNAL_PATH), null);
  assert.equal(fs.authorizationStore.read(), null);
});

test("one origin keeps two directory journals in independent scoped authorization keys", async () => {
  const sharedStorage = new MemoryStorage();
  const projectA = new MemoryFileSystem(
    { "presentation.pptd": "A" },
    { authorizationStorage: sharedStorage, projectScopeId: "dir-project-a" },
  );
  const projectB = new MemoryFileSystem(
    { "presentation.pptd": "B" },
    { authorizationStorage: sharedStorage, projectScopeId: "dir-project-b" },
  );
  const changeA = [{ path: "presentation.pptd", operation: "put", content: "A2" }];
  const changeB = [{ path: "presentation.pptd", operation: "put", content: "B2" }];
  const contextA = projectA.context();
  const contextB = projectB.context();
  const journalA = await beginSaveJournal(
    contextA,
    changeA,
    await backupsFor(contextA, changeA),
    { manifestPath: "presentation.pptd", maxBytes: MAX_BYTES },
  );
  const journalB = await beginSaveJournal(
    contextB,
    changeB,
    await backupsFor(contextB, changeB),
    { manifestPath: "presentation.pptd", maxBytes: MAX_BYTES },
  );

  assert.notEqual(projectA.authorizationStore.storageKey, projectB.authorizationStore.storageKey);
  assert.equal(projectA.authorizationStore.read().state, "armed");
  assert.equal(projectB.authorizationStore.read().state, "armed");
  await finishSaveJournal(contextB, journalB, { maxBytes: MAX_BYTES });
  assert.equal(projectB.authorizationStore.read(), null);
  assert.equal(projectA.authorizationStore.read().state, "armed");
  assert.notEqual(projectA.text(SAVE_JOURNAL_PATH), null);
  await finishSaveJournal(contextA, journalA, { maxBytes: MAX_BYTES });
});

test("an armed missing journal in project A neither freezes nor gets cleared by project B", async () => {
  const sharedStorage = new MemoryStorage();
  const projectA = new MemoryFileSystem(
    { "presentation.pptd": "A" },
    { authorizationStorage: sharedStorage, projectScopeId: "dir-missing-a" },
  );
  const projectB = new MemoryFileSystem(
    { "presentation.pptd": "B" },
    { authorizationStorage: sharedStorage, projectScopeId: "dir-unrelated-b" },
  );
  const changes = [{ path: "presentation.pptd", operation: "put", content: "A2" }];
  const contextA = projectA.context();
  await beginSaveJournal(contextA, changes, await backupsFor(contextA, changes), {
    manifestPath: "presentation.pptd",
    maxBytes: MAX_BYTES,
  });
  projectA.entries.delete(SAVE_JOURNAL_PATH);

  const unrelated = await recoverPendingSaveJournal(projectB.root, recoveryOptions(projectB));
  assert.equal(unrelated.recovered, false);
  assert.equal(projectA.authorizationStore.read().state, "armed");
  await assert.rejects(
    recoverPendingSaveJournal(projectA.root, recoveryOptions(projectA)),
    (error) => error.code === "SAVE_JOURNAL_AUTHORIZATION" && /committed 前缺失|日志缺失/.test(error.message),
  );
  assert.equal(projectA.authorizationStore.read().state, "armed");
});

test("tabs sharing one project scope observe and serialize the same authorization", () => {
  const sharedStorage = new MemoryStorage();
  const firstTab = createJournalAuthorizationStore(sharedStorage, {
    projectScopeId: "dir-shared-tab-project",
  });
  const secondTab = createJournalAuthorizationStore(sharedStorage, {
    projectScopeId: "dir-shared-tab-project",
  });
  const evidence = {
    transactionId: "tab-transaction",
    journalDigest: "b".repeat(64),
    journalBytes: 100,
  };

  firstTab.prepare(evidence);
  assert.equal(secondTab.read().state, "preparing");
  assert.throws(() => secondTab.prepare({
    transactionId: "other-tab-transaction",
    journalDigest: "c".repeat(64),
    journalBytes: 101,
  }), /当前项目存在另一笔/);
  firstTab.arm(evidence);
  assert.equal(secondTab.read().state, "armed");
  secondTab.commit(evidence);
  assert.equal(firstTab.read().state, "committed");
  firstTab.clear(evidence, { states: ["committed"] });
  assert.equal(secondTab.read(), null);
});

test("one project Web Lock serializes complete journal transactions across tabs", async () => {
  const sharedLocks = new SharedLockManager();
  const sharedStorage = new MemoryStorage();
  const projectScopeId = "dir-cross-tab-transaction";
  const firstStore = createJournalAuthorizationStore(sharedStorage, { projectScopeId });
  const secondStore = createJournalAuthorizationStore(sharedStorage, { projectScopeId });
  const firstLock = createProjectJournalLock(projectScopeId, sharedLocks);
  const secondLock = createProjectJournalLock(projectScopeId, sharedLocks);
  let releaseFirst;
  let firstEntered;
  const firstEnteredPromise = new Promise((resolve) => { firstEntered = resolve; });
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  const events = [];
  const firstEvidence = {
    transactionId: "txn-lock-a",
    journalDigest: "a".repeat(64),
    journalBytes: 10,
  };
  const secondEvidence = {
    transactionId: "txn-lock-b",
    journalDigest: "b".repeat(64),
    journalBytes: 20,
  };

  const first = firstLock.runExclusive(async () => {
    events.push("first-enter");
    firstStore.prepare(firstEvidence);
    firstEntered();
    await firstGate;
    firstStore.clear(firstEvidence, { states: ["preparing"] });
    events.push("first-exit");
  });
  await firstEnteredPromise;
  const second = secondLock.runExclusive(async () => {
    events.push("second-enter");
    assert.equal(secondStore.read(), null);
    secondStore.prepare(secondEvidence);
    secondStore.clear(secondEvidence, { states: ["preparing"] });
    events.push("second-exit");
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(events, ["first-enter"]);
  releaseFirst();
  await Promise.all([first, second]);
  assert.deepEqual(events, ["first-enter", "first-exit", "second-enter", "second-exit"]);
  assert.equal(firstLock.lockName, secondLock.lockName);
  assert.equal(firstStore.read(), null);
});

test("committed closes every finish cleanup crash boundary without rolling back the deck", async () => {
  for (const boundary of ["journal-present", "journal-missing"]) {
    const fs = new MemoryFileSystem({ "presentation.pptd": "OLD" });
    const context = fs.context();
    const changes = [{ path: "presentation.pptd", operation: "put", content: "NEW" }];
    const backups = await backupsFor(context, changes);
    const journal = await beginSaveJournal(context, changes, backups, {
      manifestPath: "presentation.pptd",
      maxBytes: MAX_BYTES,
    });
    await writeVersionedFile(
      context,
      "presentation.pptd",
      "NEW",
      backups.get("presentation.pptd").version,
      { maxBytes: MAX_BYTES },
    );
    fs.authorizationStore.commit(journal);
    if (boundary === "journal-missing") fs.entries.delete(SAVE_JOURNAL_PATH);

    const result = await recoverPendingSaveJournal(fs.root, recoveryOptions(fs));
    assert.equal(result.cleanedCommitted, true, boundary);
    assert.equal(fs.text("presentation.pptd"), "NEW", boundary);
    assert.equal(fs.text(SAVE_JOURNAL_PATH), null, boundary);
    assert.equal(fs.authorizationStore.read(), null, boundary);
  }
});

test("armed plus missing remains fail-closed while committed plus missing is cleanable", async () => {
  const armed = new MemoryFileSystem({ "presentation.pptd": "OLD" });
  const armedContext = armed.context();
  const changes = [{ path: "presentation.pptd", operation: "put", content: "NEW" }];
  await beginSaveJournal(armedContext, changes, await backupsFor(armedContext, changes), {
    manifestPath: "presentation.pptd",
    maxBytes: MAX_BYTES,
  });
  armed.entries.delete(SAVE_JOURNAL_PATH);

  await assert.rejects(
    recoverPendingSaveJournal(armed.root, recoveryOptions(armed)),
    (error) => error.code === "SAVE_JOURNAL_AUTHORIZATION",
  );
  assert.equal(armed.authorizationStore.read().state, "armed");
  assert.equal(armed.text("presentation.pptd"), "OLD");
});

test("directory scope IDs are stable, concurrent across tabs, and never derived from paths", async () => {
  const registry = new MemoryScopeRegistry();
  const lockManager = new SharedLockManager();
  let nextId = 0;
  const options = {
    registry,
    lockManagerOrProvider: lockManager,
    idFactory: () => `scope-${++nextId}`,
  };
  const firstResolver = createDirectoryProjectScopeResolver(options);
  const secondResolver = createDirectoryProjectScopeResolver(options);
  const firstHandle = directoryHandle("opaque-entry-a");
  const sameDirectoryFromAnotherTab = directoryHandle("opaque-entry-a");

  const [firstScope, secondScope] = await Promise.all([
    firstResolver.resolve(firstHandle),
    secondResolver.resolve(sameDirectoryFromAnotherTab),
  ]);
  assert.equal(firstScope, secondScope);
  assert.match(firstScope, /^dir-scope-\d+$/);
  assert.equal(registry.records.length, 1);

  const otherScope = await firstResolver.resolve(directoryHandle("opaque-entry-b"));
  assert.notEqual(otherScope, firstScope);
  assert.equal(registry.records.length, 2);
  assert.equal(Object.hasOwn(registry.records[0], "path"), false);
});

test("directory scope resolution prunes only explicitly revoked or invalid host records", async () => {
  const registry = new MemoryScopeRegistry([
    { projectScopeId: "dir-revoked-1", directoryHandle: directoryHandle("old-1", "denied") },
    { projectScopeId: "dir-revoked-2", directoryHandle: directoryHandle("old-2", "denied") },
    { projectScopeId: "dir-invalid", directoryHandle: { kind: "file" } },
  ]);
  const resolver = createDirectoryProjectScopeResolver({
    registry,
    lockManagerOrProvider: new SharedLockManager(),
    idFactory: () => "fresh-scope",
    maxScopes: 2,
  });

  const scope = await resolver.resolve(directoryHandle("new-project"));
  assert.equal(scope, "dir-fresh-scope");
  assert.deepEqual(registry.records.map((record) => record.projectScopeId), ["dir-fresh-scope"]);

  const fullRegistry = new MemoryScopeRegistry([
    { projectScopeId: "dir-prompt-1", directoryHandle: directoryHandle("prompt-1", "prompt") },
    { projectScopeId: "dir-prompt-2", directoryHandle: directoryHandle("prompt-2", "prompt") },
  ]);
  const fullResolver = createDirectoryProjectScopeResolver({
    registry: fullRegistry,
    lockManagerOrProvider: new SharedLockManager(),
    idFactory: () => "must-not-be-used",
    maxScopes: 2,
  });
  await assert.rejects(
    fullResolver.resolve(directoryHandle("new-project")),
    /已达到 2 个安全上限/,
  );
  assert.equal(fullRegistry.records.length, 2, "prompt is not proof of revocation");
});

test("legacy unscoped v1 evidence is ignored and cannot authorize a v2 project journal", async () => {
  const sharedStorage = new MemoryStorage();
  sharedStorage.setItem("open-kimi-ppt.save-journal-authorization.v1", JSON.stringify({
    version: 1,
    state: "armed",
    transactionId: "legacy",
    journalDigest: "d".repeat(64),
    journalBytes: 100,
  }));
  const fs = new MemoryFileSystem({}, {
    authorizationStorage: sharedStorage,
    projectScopeId: "dir-v2-project",
  });
  fs.externalSet(SAVE_JOURNAL_PATH, "{}");

  assert.equal(fs.authorizationStore.storageKey.startsWith(`${JOURNAL_AUTHORIZATION_STORAGE_KEY}:`), true);
  assert.equal(fs.authorizationStore.read(), null);
  await assert.rejects(
    recoverPendingSaveJournal(fs.root, recoveryOptions(fs)),
    (error) => error.code === "SAVE_JOURNAL_AUTHORIZATION",
  );
  assert.equal(
    sharedStorage.getItem("open-kimi-ppt.save-journal-authorization.v1") !== null,
    true,
    "unknown legacy ownership must not be destructively reassigned or cleared",
  );
  assert.equal(fs.text(SAVE_JOURNAL_PATH), "{}");
});
