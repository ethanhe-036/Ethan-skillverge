import assert from "node:assert/strict";
import test from "node:test";
import {
  ExternalFileConflictError,
  assertBackupMatchesBaseline,
  deleteVersionedFile,
  fileVersionsEqual,
  readVersionedBackup,
  rollbackVersionedChanges,
  writeVersionedFile,
} from "../editor/file-transaction.js";

const MAX_BYTES = 1024 * 1024;
const encoder = new TextEncoder();
const decoder = new TextDecoder();

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

class MemoryFileSystem {
  constructor(initial = {}) {
    this.entries = new Map();
    this.clock = 100;
    this.createWritableCalls = 0;
    this.failWrites = new Set();
    this.onCreateWritable = null;
    this.failReadsAfterClose = new Set();
    this.closedPaths = new Set();
    for (const [path, content] of Object.entries(initial)) this.externalSet(path, content);
    this.root = this.directory("");
  }

  directory(prefix) {
    return {
      getDirectoryHandle: async (name, { create } = {}) => {
        const nextPrefix = prefix ? `${prefix}/${name}` : name;
        const hasChild = [...this.entries].some(([path]) => path.startsWith(`${nextPrefix}/`));
        if (!create && !hasChild) throw missing(nextPrefix);
        return this.directory(nextPrefix);
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
        if (this.closedPaths.has(path) && this.failReadsAfterClose.has(path)) {
          throw new Error(`simulated post-close read failure: ${path}`);
        }
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
        this.createWritableCalls += 1;
        this.onCreateWritable?.(path);
        let staged = null;
        return {
          write: async (content) => {
            if (this.failWrites.has(path)) throw new Error(`simulated write failure: ${path}`);
            staged = await toBytes(content);
          },
          close: async () => {
            if (staged === null) staged = new Uint8Array();
            this.entries.set(path, { bytes: staged.slice(), lastModified: ++this.clock });
            this.closedPaths.add(path);
          },
          abort: async () => undefined,
        };
      },
    };
  }

  externalSet(path, content, { lastModified = ++this.clock } = {}) {
    this.entries.set(path, { bytes: encoder.encode(content), lastModified });
  }

  text(path) {
    const entry = this.entries.get(path);
    return entry ? decoder.decode(entry.bytes) : null;
  }

  context() {
    return {
      directoryHandle: this.root,
      fileIndex: new Map(),
    };
  }
}

test("rejects an external change after backup before write without mutating it", async () => {
  const fs = new MemoryFileSystem({ "page.one": "AAAA" });
  const context = fs.context();
  const backup = await readVersionedBackup(context, "page.one", { maxBytes: MAX_BYTES });

  fs.externalSet("page.one", "BBBB", {
    // Exercise the digest check: size and timestamp deliberately collide.
    lastModified: backup.version.lastModified,
  });

  await assert.rejects(
    writeVersionedFile(
      context,
      "page.one",
      "OURS",
      backup.version,
      { maxBytes: MAX_BYTES },
    ),
    (error) => error.code === "EXTERNAL_FILE_CONFLICT"
      && error.preservedExternalVersion === true,
  );
  assert.equal(fs.createWritableCalls, 0, "the conflict must be detected before opening a writer");
  assert.equal(fs.text("page.one"), "BBBB");
});

test("rejects load-to-save lost updates against the version captured at load", async () => {
  const fs = new MemoryFileSystem({ "page.one": "A" });
  const context = fs.context();
  const loaded = await readVersionedBackup(context, "page.one", { maxBytes: MAX_BYTES });

  fs.externalSet("page.one", "B");
  const atSaveStart = await readVersionedBackup(context, "page.one", { maxBytes: MAX_BYTES });
  assert.throws(
    () => assertBackupMatchesBaseline(
      "page.one",
      atSaveStart,
      loaded.version,
    ),
    (error) => error.code === "EXTERNAL_FILE_CONFLICT"
      && error.saveScopeUncertain === true,
  );
  assert.equal(fs.createWritableCalls, 0);
  assert.equal(fs.text("page.one"), "B");
});

test("rechecks the visible version after opening and staging a writable stream", async () => {
  const fs = new MemoryFileSystem({ "page.one": "BASE" });
  const context = fs.context();
  const backup = await readVersionedBackup(context, "page.one", { maxBytes: MAX_BYTES });
  fs.onCreateWritable = (path) => {
    if (path === "page.one") fs.externalSet(path, "EXTERNAL");
  };

  await assert.rejects(
    writeVersionedFile(
      context,
      "page.one",
      "OURS",
      backup.version,
      { maxBytes: MAX_BYTES },
    ),
    (error) => error.code === "EXTERNAL_FILE_CONFLICT"
      && error.preservedExternalVersion === true,
  );
  assert.equal(fs.text("page.one"), "EXTERNAL");
});

test("creating a file never recreates an intermediate directory that disappeared", async () => {
  const fs = new MemoryFileSystem({ "pages/existing.page": "BASE" });
  const context = fs.context();
  const backup = await readVersionedBackup(
    context,
    "pages/new.page",
    { maxBytes: MAX_BYTES },
  );
  fs.entries.delete("pages/existing.page");

  await assert.rejects(
    writeVersionedFile(
      context,
      "pages/new.page",
      "NEW",
      backup.version,
      { maxBytes: MAX_BYTES },
    ),
    /missing: pages/,
  );
  assert.equal(fs.createWritableCalls, 0);
  assert.equal(fs.text("pages/new.page"), null);
  assert.equal(
    [...fs.entries.keys()].some((path) => path === "pages" || path.startsWith("pages/")),
    false,
  );
});

test("post-close verification failure is marked as an uncertain applied mutation", async () => {
  const fs = new MemoryFileSystem({ "page.one": "OLD" });
  const context = fs.context();
  const backup = await readVersionedBackup(context, "page.one", { maxBytes: MAX_BYTES });
  fs.failReadsAfterClose.add("page.one");

  await assert.rejects(
    writeVersionedFile(
      context,
      "page.one",
      "NEW",
      backup.version,
      { maxBytes: MAX_BYTES },
    ),
    (error) => error.code === "FILE_MUTATION_UNCERTAIN"
      && error.mayHaveApplied === true
      && error.saveScopeUncertain === true,
  );
  assert.equal(fs.text("page.one"), "NEW");
});

test("rollback preserves an external replacement after an earlier local write", async () => {
  const fs = new MemoryFileSystem({
    "first.page": "first-old",
    "second.page": "second-old",
  });
  const context = fs.context();
  const firstBackup = await readVersionedBackup(context, "first.page", { maxBytes: MAX_BYTES });
  const secondBackup = await readVersionedBackup(context, "second.page", { maxBytes: MAX_BYTES });
  const firstWrittenVersion = await writeVersionedFile(
    context,
    "first.page",
    "first-new",
    firstBackup.version,
    { maxBytes: MAX_BYTES },
  );
  const applied = new Map([
    ["first.page", { backup: firstBackup, writtenVersion: firstWrittenVersion }],
  ]);

  fs.externalSet("first.page", "third-new", {
    lastModified: firstWrittenVersion.lastModified,
  });
  fs.failWrites.add("second.page");
  await assert.rejects(
    writeVersionedFile(
      context,
      "second.page",
      "second-new",
      secondBackup.version,
      { maxBytes: MAX_BYTES },
    ),
    /simulated write failure/,
  );

  await assert.rejects(
    rollbackVersionedChanges(context, applied, { maxBytes: MAX_BYTES }),
    (error) => error instanceof AggregateError
      && /已保留外部版本而未覆盖/.test(error.message)
      && error.errors[0]?.cause?.code === "EXTERNAL_FILE_CONFLICT",
  );
  assert.equal(fs.text("first.page"), "third-new");
  assert.equal(fs.text("second.page"), "second-old");
});

test("normal writes and deletes roll back to their original versions", async () => {
  const fs = new MemoryFileSystem({
    "written.page": "written-old",
    "deleted.page": "deleted-old",
  });
  const context = fs.context();
  const writtenBackup = await readVersionedBackup(context, "written.page", {
    maxBytes: MAX_BYTES,
  });
  const deletedBackup = await readVersionedBackup(context, "deleted.page", {
    maxBytes: MAX_BYTES,
  });
  const writtenVersion = await writeVersionedFile(
    context,
    "written.page",
    "written-new",
    writtenBackup.version,
    { maxBytes: MAX_BYTES },
  );
  const deletedVersion = await deleteVersionedFile(
    context,
    "deleted.page",
    deletedBackup.version,
    { maxBytes: MAX_BYTES },
  );
  assert.equal(fs.text("written.page"), "written-new");
  assert.equal(fs.text("deleted.page"), null);

  const restoredVersions = await rollbackVersionedChanges(context, new Map([
    ["written.page", { backup: writtenBackup, writtenVersion }],
    ["deleted.page", { backup: deletedBackup, writtenVersion: deletedVersion }],
  ]), { maxBytes: MAX_BYTES });

  assert.equal(fs.text("written.page"), "written-old");
  assert.equal(fs.text("deleted.page"), "deleted-old");
  assert.equal(context.fileIndex.has("written.page"), true);
  assert.equal(context.fileIndex.has("deleted.page"), true);
  const writtenAfterRollback = await readVersionedBackup(context, "written.page", {
    maxBytes: MAX_BYTES,
  });
  const deletedAfterRollback = await readVersionedBackup(context, "deleted.page", {
    maxBytes: MAX_BYTES,
  });
  assert.equal(
    fileVersionsEqual(restoredVersions.get("written.page"), writtenAfterRollback.version),
    true,
  );
  assert.equal(
    fileVersionsEqual(restoredVersions.get("deleted.page"), deletedAfterRollback.version),
    true,
  );
});

test("post-mutation conflicts explicitly preserve the recovery journal", () => {
  const before = new ExternalFileConflictError(
    "page.one",
    "保存",
    "版本在提交前变化",
  );
  const after = new ExternalFileConflictError(
    "page.one",
    "保存",
    "提交后检测到其他版本",
    { phase: "after" },
  );
  assert.equal(before.mayHaveApplied, false);
  assert.equal(after.mayHaveApplied, true);
  assert.equal(after.saveScopeUncertain, true);
});
