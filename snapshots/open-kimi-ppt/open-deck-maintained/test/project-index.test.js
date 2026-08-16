import assert from "node:assert/strict";
import test from "node:test";
import {
  indexProjectDirectory,
  indexUploadedProjectFiles,
  projectIndexMetadata,
} from "../editor/project-index.js";

function fileHandle(name) {
  return { kind: "file", name, async getFile() { return { name }; } };
}

function directoryHandle(entries) {
  return {
    kind: "directory",
    async *entries() {
      for (const entry of entries) yield entry;
    },
  };
}

function uploaded(path) {
  return { name: path.split("/").at(-1), webkitRelativePath: path };
}

test("indexes an authorized directory within the entry and depth budgets", async () => {
  const page = fileHandle("01.page");
  const root = directoryHandle([
    [".DS_Store", fileHandle(".DS_Store")],
    ["deck.pptd", fileHandle("deck.pptd")],
    ["pages", directoryHandle([["01.page", page]])],
  ]);

  const index = await indexProjectDirectory(root, { maxEntries: 3, maxDepth: 1 });
  assert.deepEqual([...index.keys()], ["deck.pptd", "pages/01.page"]);
  assert.equal(index.get("pages/01.page"), page);
  assert.deepEqual(projectIndexMetadata(index), {
    entryCount: 3,
    directories: new Set(["pages"]),
  });
});

test("counts empty directories and rejects an over-wide project", async () => {
  const root = directoryHandle([
    ["one", directoryHandle([])],
    ["two", directoryHandle([])],
    ["three", directoryHandle([])],
  ]);
  await assert.rejects(
    indexProjectDirectory(root, { maxEntries: 2, maxDepth: 2 }),
    /项目条目超过 2 个/,
  );
});

test("rejects a project deeper than the configured directory budget", async () => {
  const root = directoryHandle([
    ["one", directoryHandle([
      ["two", directoryHandle([["page.page", fileHandle("page.page")]])],
    ])],
  ]);
  await assert.rejects(
    indexProjectDirectory(root, { maxEntries: 10, maxDepth: 1 }),
    /项目目录深度超过 1 层/,
  );
});

test("rejects distinct filesystem entries that normalize to one path", async () => {
  const root = directoryHandle([
    ["a\\b.page", fileHandle("a\\b.page")],
    ["a", directoryHandle([["b.page", fileHandle("b.page")]])],
  ]);
  await assert.rejects(
    indexProjectDirectory(root, { maxEntries: 4, maxDepth: 2 }),
    /规范化后重复的文件系统条目：a\/b\.page/,
  );
});

test("folder-upload fallback strips one root and enforces its budgets", () => {
  const files = [uploaded("deck/deck.pptd"), uploaded("deck/pages/01.page")];
  const index = indexUploadedProjectFiles(files, { maxEntries: 3, maxDepth: 1 });
  assert.deepEqual([...index.keys()], ["deck.pptd", "pages/01.page"]);
  assert.equal(projectIndexMetadata(index).entryCount, 3);

  assert.throws(
    () => indexUploadedProjectFiles(files, { maxEntries: 2, maxDepth: 1 }),
    /项目条目超过 2 个/,
  );
  assert.throws(
    () => indexUploadedProjectFiles(
      [uploaded("deck/a/b/01.page")],
      { maxEntries: 2, maxDepth: 1 },
    ),
    /项目目录深度超过 1 层/,
  );
});

test("folder-upload rejects an oversized FileList before materializing it", () => {
  let iterated = false;
  const files = {
    length: 10_001,
    [Symbol.iterator]() {
      iterated = true;
      throw new Error("must not iterate");
    },
  };
  assert.throws(
    () => indexUploadedProjectFiles(files, { maxEntries: 10_000 }),
    /项目文件超过 10000 个/,
  );
  assert.equal(iterated, false);
});

test("folder-upload reads exactly the declared indexed items and ignores iterators", () => {
  let iterated = false;
  let lengthReads = 0;
  const file = uploaded("deck/deck.pptd");
  const files = {
    0: file,
    get length() {
      lengthReads += 1;
      return lengthReads === 1 ? 1 : Number.POSITIVE_INFINITY;
    },
    [Symbol.iterator]() {
      iterated = true;
      throw new Error("must not iterate");
    },
  };
  const index = indexUploadedProjectFiles(files, { maxEntries: 2 });
  assert.deepEqual([...index.keys()], ["deck.pptd"]);
  assert.equal(iterated, false);
  assert.equal(lengthReads, 1);
});
