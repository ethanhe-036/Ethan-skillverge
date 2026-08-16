import assert from "node:assert/strict";
import test from "node:test";
import {
  assertWritableChangePath,
  buildImagePathAliases,
  buildWritablePathAliases,
  collectImageProjectPaths,
  extractReferencedImagePaths,
  extractPagePaths,
  joinDeckPath,
  joinManifestPath,
  normalizeSaveChanges,
  normalizeRelativePath,
  recordSaveFailure,
  resolveSaveFailure,
  titleFromManifest,
  validateImageReferencesGranted,
  validateNewPageBackups,
  validatePageClosure,
} from "../editor/lib.js";
import { extractRestrictedYamlManifestPages } from "../editor/restricted-parser.js";

const yaml = `---
version: v2
title: "测试文稿"
pages:
  - pages/01.page
  - 'pages/02.page' # comment
theme:
  colors: {}
`;

test("parses PPTD manifests and keeps file access inside the project", () => {
  assert.deepEqual(extractPagePaths(yaml), ["pages/01.page", "pages/02.page"]);
  assert.deepEqual(extractPagePaths('{"pages":["a.page","folder/b.page"]}'), ["a.page", "folder/b.page"]);
  assert.equal(titleFromManifest(yaml, "fallback"), "测试文稿");
  assert.equal(joinDeckPath("deck", "pages/01.page"), "deck/pages/01.page");
  assert.equal(joinDeckPath("deck", "deck/pages/01.page"), "deck/pages/01.page");
  assert.equal(assertWritableChangePath("deck", "pages/01.page"), "deck/pages/01.page");
  assert.throws(() => normalizeRelativePath("../secret"), /不允许越过/);
  assert.throws(() => normalizeRelativePath("/absolute/path"), /绝对路径/);
  const depth64 = `${Array.from({ length: 64 }, (_, index) => `d${index}`).join("/")}/file.page`;
  const depth65 = `extra/${depth64}`;
  assert.equal(normalizeRelativePath(depth64), depth64);
  assert.throws(() => normalizeRelativePath(depth65), /目录深度超过 64 层/);
  assert.throws(() => normalizeRelativePath(`${"a".repeat(4092)}.page`), /4096 个字符/);
  assert.throws(() => assertWritableChangePath("deck", "media/image.png"), /仅允许编辑/);
});

test("keeps manifest-relative paths and literal percent filenames unambiguous", () => {
  assert.equal(joinManifestPath("slides", "slides/01.page"), "slides/slides/01.page");
  assert.equal(joinDeckPath("deck", "pages/literal%252Fname.page"), "deck/pages/literal%252Fname.page");
  assert.throws(() => normalizeRelativePath("file:///tmp/secret.page"), /绝对路径/);
  assert.deepEqual(extractPagePaths("pages:\n  - 'pages/it''s.page'\n"), ["pages/it's.page"]);
});

test("only grants image access to explicit, safe page dependencies", () => {
  const page = `
layers:
  - src: media/cover.png
  - src: 'media/it''s.webp'
  - src: ../../.env
  - src: https://example.com/remote.png
  - src: notes/private.txt
`;
  assert.deepEqual(extractReferencedImagePaths(page), ["media/cover.png", "media/it's.webp"]);
  assert.deepEqual(
    extractReferencedImagePaths(JSON.stringify({ layers: [{ src: "media/a.jpg" }, { src: "/etc/passwd" }] })),
    ["media/a.jpg"],
  );

  const aliases = buildImagePathAliases(
    "deck/presentation.pptd",
    ["media/a.png", "deck/media/a.png"],
  );
  assert.equal(aliases.get("media/a.png"), "deck/media/a.png");
  assert.equal(aliases.get("deck/media/a.png"), null);
  assert.equal(aliases.get("deck/deck/media/a.png"), "deck/deck/media/a.png");
  assert.deepEqual(
    [...collectImageProjectPaths("deck/presentation.pptd", ["media/a.png"])],
    ["deck/media/a.png"],
  );
  assert.doesNotThrow(() => validateImageReferencesGranted(
    "deck/presentation.pptd",
    ["media/a.png"],
    new Set(["deck/media/a.png"]),
  ));
  assert.throws(
    () => validateImageReferencesGranted(
      "deck/presentation.pptd",
      ["private/secret.png"],
      new Set(["deck/media/a.png"]),
    ),
    /未授权的本地图片/,
  );
});

test("bounds JSON image-source traversal without recursive stack growth", () => {
  assert.deepEqual(
    extractReferencedImagePaths(JSON.stringify({
      first: { src: "media/first.png" },
      group: [{ src: "media/second.webp" }],
    })),
    ["media/first.png", "media/second.webp"],
  );
  assert.throws(
    () => extractReferencedImagePaths(
      JSON.stringify({ outer: { inner: { src: "media/deep.png" } } }),
      { maxJsonDepth: 2 },
    ),
    /嵌套深度超过 2/,
  );
  assert.throws(
    () => extractReferencedImagePaths(
      JSON.stringify({ values: [{ src: "media/a.png" }, { src: "media/b.png" }] }),
      { maxJsonNodes: 2 },
    ),
    /节点数超过 2/,
  );
});

test("rejects over-limit manifests before constructing the page result", () => {
  assert.deepEqual(
    extractPagePaths('{"pages":["01.page","02.page"]}', { maxPageCount: 2 }),
    ["01.page", "02.page"],
  );
  assert.throws(
    () => extractPagePaths(
      '{"pages":["01.page","02.page","03.page"]}',
      { maxPageCount: 2 },
    ),
    /包含 3 页，超过 2 页/,
  );
  assert.throws(
    () => extractPagePaths(
      "pages:\n  - 01.page\n  - 02.page\n  - 03.page\n",
      { maxPageCount: 2 },
    ),
    /页数超过 2 页/,
  );
});

test("derives page capabilities only from one unambiguous top-level YAML pages key", () => {
  const nestedPages = `
version: v2
metadata:
  pages:
    - private.page
pages:
  - ./pages/01.page
`;
  assert.deepEqual(extractPagePaths(nestedPages), ["pages/01.page"]);
  assert.throws(
    () => extractPagePaths("metadata:\n  pages:\n    - private.page\n"),
    /缺少顶层 pages/,
  );
  assert.throws(
    () => extractPagePaths(" pages:\n   - private.page\n"),
    /缩进没有对应的父节点/,
  );
  assert.throws(
    () => extractPagePaths("pages:\n  - first.page\npages:\n  - second.page\n"),
    /重复键：pages/,
  );
  assert.throws(
    () => extractPagePaths("metadata:\n  title: first\n  title: second\npages: [safe.page]\n"),
    /重复键：title/,
  );
});

test("rejects YAML references, merge keys, complex keys, and unsupported ambiguous structures", () => {
  assert.throws(
    () => extractPagePaths("pageList: &pageList [private.page]\npages: *pageList\n"),
    /anchor、alias/,
  );
  assert.throws(
    () => extractPagePaths("defaults:\n  pages: [private.page]\nmetadata:\n  <<: *defaults\npages: [safe.page]\n"),
    /anchor、alias|merge key/,
  );
  assert.throws(
    () => extractPagePaths("? [pages]\n: [private.page]\npages: [safe.page]\n"),
    /复杂键/,
  );
  assert.throws(
    () => extractPagePaths("metadata: { pages: [private.page] }\npages: [safe.page]\n"),
    /不支持非空 flow mapping/,
  );
});

test("keeps JSON and YAML manifest page semantics aligned", () => {
  const expected = ["pages/01.page", "nested/02.page"];
  assert.deepEqual(
    extractPagePaths('{"metadata":{"pages":["private.page"]},"pages":["./pages/01.page","nested/./02.page"]}'),
    expected,
  );
  assert.deepEqual(
    extractPagePaths("metadata:\n  pages: [private.page]\npages: [./pages/01.page, nested/./02.page]\n"),
    expected,
  );
  for (const emptyManifest of ['{"pages":[]}', "pages: []\n", "pages:\n"]) {
    assert.throws(() => extractPagePaths(emptyManifest), /pages 数组不能为空/);
  }
  assert.throws(
    () => extractPagePaths('{"pages":["first.page"],"p\\u0061ges":["second.page"]}'),
    /重复键：pages/,
  );
  for (const nonStringManifest of ['{"pages":[1]}', "pages:\n  - 1\n"]) {
    assert.throws(() => extractPagePaths(nonStringManifest), /必须是字符串/);
  }
});

test("preflights JSON bytes, nodes, depth, and strings before JSON.parse", { concurrency: false }, () => {
  const originalParse = JSON.parse;
  let parseCalls = 0;
  JSON.parse = (...args) => {
    parseCalls += 1;
    return originalParse(...args);
  };
  try {
    assert.throws(
      () => extractPagePaths('{"pages":["01.page"]}', { maxJsonBytes: 10 }),
      /源字节超过 10/,
    );
    assert.equal(parseCalls, 0);

    assert.throws(
      () => extractPagePaths('{"pages":["01.page"]}', { maxJsonNodes: 2 }),
      /节点数超过 2/,
    );
    assert.equal(parseCalls, 0);

    assert.throws(
      () => extractPagePaths('{"outer":{"inner":{}},"pages":["01.page"]}', { maxJsonDepth: 2 }),
      /嵌套深度超过 2/,
    );
    assert.equal(parseCalls, 0);

    assert.throws(
      () => extractPagePaths('{"pages":["123456.page"]}', { maxJsonStringChars: 5 }),
      /单字符串长度超过 5/,
    );
    assert.equal(parseCalls, 0);

    assert.throws(
      () => extractPagePaths('{"pages":["a.page","b.page"]}', {
        maxJsonStringChars: 10,
        maxJsonTotalStringChars: 12,
      }),
      /字符串总长度超过 12/,
    );
    assert.equal(parseCalls, 0);

    const boundary = '{"pages":["a"]}';
    assert.deepEqual(extractPagePaths(boundary, {
      maxJsonBytes: new TextEncoder().encode(boundary).byteLength,
      maxJsonNodes: 3,
      maxJsonDepth: 2,
      maxJsonStringChars: 5,
      maxJsonTotalStringChars: 6,
    }), ["a"]);
    assert.equal(parseCalls, 1);
  } finally {
    JSON.parse = originalParse;
  }
});

test("preflights page JSON before collecting image capabilities", { concurrency: false }, () => {
  const originalParse = JSON.parse;
  let parseCalls = 0;
  JSON.parse = (...args) => {
    parseCalls += 1;
    return originalParse(...args);
  };
  try {
    assert.throws(
      () => extractReferencedImagePaths('{"outer":{"src":"media/deep.png"}}', {
        maxJsonDepth: 1,
      }),
      /嵌套深度超过 1/,
    );
    assert.equal(parseCalls, 0);
    assert.throws(
      () => extractReferencedImagePaths('{"src":"media/a.png","s\\u0072c":"media/b.png"}'),
      /重复键：src/,
    );
    assert.equal(parseCalls, 0);
  } finally {
    JSON.parse = originalParse;
  }
});

test("bounds restricted YAML structure before returning page capabilities", () => {
  assert.throws(
    () => extractPagePaths("metadata:\n  nested:\n    value: safe\npages: [safe.page]\n", {
      maxYamlDepth: 1,
    }),
    /嵌套深度超过 1/,
  );
  assert.throws(
    () => extractPagePaths("version: v2\ntitle: deck\npages: [safe.page]\n", {
      maxYamlNodes: 4,
    }),
    /节点数超过 4/,
  );

  const depthBoundary = "metadata: [[safe]]\npages: [safe.page]\n";
  assert.deepEqual(
    extractPagePaths(depthBoundary, { maxYamlDepth: 2 }),
    ["safe.page"],
  );
  assert.throws(
    () => extractPagePaths("metadata: [[[safe]]]\npages: [safe.page]\n", {
      maxYamlDepth: 2,
    }),
    /嵌套深度超过 2/,
  );

  const nodeBoundary = "metadata: [a, b]\npages: [safe.page]\n";
  assert.deepEqual(
    extractPagePaths(nodeBoundary, { maxYamlNodes: 7 }),
    ["safe.page"],
  );
  assert.throws(
    () => extractPagePaths("metadata: [a, b, c]\npages: [safe.page]\n", {
      maxYamlNodes: 7,
    }),
    /节点数超过 7/,
  );

  assert.throws(
    () => extractPagePaths("metadata: [[[[safe]]]]\npages: [safe.page]\n", {
      maxYamlDepth: 1,
    }),
    /嵌套深度超过 1/,
  );
  assert.throws(
    () => extractPagePaths("metadata: [a, b, c, d, e, f]\npages: [safe.page]\n", {
      maxYamlNodes: 3,
    }),
    /节点数超过 3/,
  );

  let normalizedPaths = 0;
  assert.throws(
    () => extractRestrictedYamlManifestPages(
      "pages: [safe.page]\nmetadata: [a, b, c]\n",
      {
        maxNodes: 4,
        normalizePagePath(path) {
          normalizedPaths += 1;
          return path;
        },
      },
    ),
    /节点数超过 4/,
  );
  assert.equal(normalizedPaths, 0, "capability paths must not be constructed before full preflight");
});

test("preflights YAML line count before materializing per-line strings", { concurrency: false }, () => {
  const blockBoundary = "\n\npages:\n  - safe.page";
  assert.deepEqual(
    extractPagePaths(blockBoundary, { maxYamlLines: 4 }),
    ["safe.page"],
  );
  assert.throws(
    () => extractPagePaths(blockBoundary, { maxYamlLines: 3 }),
    /总行数超过 3 行/,
  );
  assert.deepEqual(
    extractPagePaths("pages: [safe.page]", { maxYamlLines: 1 }),
    ["safe.page"],
  );

  const overLimitBlankLines = "\n".repeat(2 * 1024 * 1024);
  const originalSlice = String.prototype.slice;
  let sliceCalls = 0;
  let caught;
  String.prototype.slice = function instrumentedSlice(...args) {
    sliceCalls += 1;
    return Reflect.apply(originalSlice, this, args);
  };
  try {
    extractRestrictedYamlManifestPages(overLimitBlankLines);
  } catch (error) {
    caught = error;
  } finally {
    String.prototype.slice = originalSlice;
  }

  assert.match(caught?.message ?? "", /总行数超过 100000 行/);
  assert.equal(sliceCalls, 0, "line budget must fail before creating a line substring");
});

test("rejects malformed save RPCs before they become file writes", () => {
  assert.deepEqual(
    normalizeSaveChanges(
      { changes: [{ operate: "update", path: "pages/01.page", content: "updated" }] },
      "deck",
    ),
    [{ operation: "put", path: "deck/pages/01.page", content: "updated" }],
  );
  assert.throws(
    () => normalizeSaveChanges({ changes: [{ operate: "remove", path: "pages/01.page" }] }, "deck"),
    /不支持的保存操作/,
  );
  assert.throws(
    () => normalizeSaveChanges({ changes: [{ operate: "put", path: "pages/01.page" }] }, "deck"),
    /缺少字符串内容/,
  );
  assert.throws(
    () => normalizeSaveChanges({ changes: [
      { operate: "delete", path: "pages/01.page" },
      { operate: "put", path: "pages/01.page", content: "" },
    ] }, "deck"),
    /重复路径/,
  );
  assert.throws(
    () => normalizeSaveChanges(
      { changes: [{ operate: "put", path: "pages/01.page", content: "abcd" }] },
      "deck",
      { maxFileBytes: 3 },
    ),
    /单文件上限/,
  );
});

test("maps ambiguous-looking nested page paths to the file loaded by the manifest", () => {
  const aliases = buildWritablePathAliases(
    "deck/presentation.pptd",
    ["deck/pages/01.page"],
  );
  assert.equal(aliases.get("deck/pages/01.page"), "deck/deck/pages/01.page");
  assert.deepEqual(
    normalizeSaveChanges(
      { changes: [{ operate: "update", path: "deck/pages/01.page", content: "next" }] },
      "deck",
      { pathAliases: aliases, requireKnownPaths: true },
    ),
    [{ operation: "put", path: "deck/deck/pages/01.page", content: "next" }],
  );
  assert.throws(
    () => normalizeSaveChanges(
      { changes: [{ operate: "create", path: "pages/undeclared.page", content: "new" }] },
      "deck",
      { pathAliases: aliases, requireKnownPaths: true },
    ),
    /未声明的路径/,
  );
  assert.throws(
    () => buildWritablePathAliases(
      "deck/presentation.pptd",
      ["deck/pages/01.page", "pages/01.page"],
    ),
    /路径存在歧义/,
  );
});

test("rejects saves that would leave the manifest/page graph incomplete", () => {
  const existing = new Set(["deck/pages/01.page"]);
  assert.throws(
    () => validatePageClosure(
      ["deck/pages/01.page", "deck/pages/02.page"],
      existing,
      [{ path: "deck/presentation.pptd", operation: "put", content: "manifest" }],
    ),
    /引用缺失页面.*02\.page/,
  );
  assert.throws(
    () => validatePageClosure(
      ["deck/pages/01.page"],
      existing,
      [{ path: "deck/pages/01.page", operation: "delete", content: null }],
    ),
    /引用缺失页面.*01\.page/,
  );
  assert.doesNotThrow(() => validatePageClosure(
    ["deck/pages/01.page", "deck/pages/02.page"],
    existing,
    [{ path: "deck/pages/02.page", operation: "put", content: "new page" }],
  ));
  assert.throws(
    () => validatePageClosure(
      ["deck/pages/01.page"],
      existing,
      [{ path: "deck/pages/orphan.page", operation: "put", content: "orphan" }],
    ),
    /未引用的页面/,
  );
  assert.throws(
    () => validatePageClosure(
      ["deck/pages/01.page", "deck/other/private.page"],
      new Set(["deck/pages/01.page", "deck/other/private.page"]),
      [{ path: "deck/other/private.page", operation: "put", content: "overwrite" }],
      { declaredPagePaths: ["deck/pages/01.page"] },
    ),
    /未授权的现存页面/,
  );
  assert.throws(
    () => validateNewPageBackups(
      ["deck/other/private.page"],
      new Map([["deck/other/private.page", { existed: true }]]),
    ),
    /覆盖当前文稿未授权的现存文件/,
  );
  assert.doesNotThrow(() => validateNewPageBackups(
    ["deck/pages/new.page"],
    new Map([["deck/pages/new.page", { existed: false }]]),
  ));
});

test("keeps a failed save sticky until successful retries cover its paths", () => {
  const failure = recordSaveFailure(
    null,
    7,
    new Error("page 1 failed"),
    new Set(["pages/01.page"]),
  );
  const afterUnrelatedSuccess = resolveSaveFailure(
    failure,
    7,
    new Set(["pages/02.page"]),
  );
  assert.deepEqual([...afterUnrelatedSuccess.paths], ["pages/01.page"]);
  assert.equal(
    resolveSaveFailure(afterUnrelatedSuccess, 7, new Set(["pages/01.page"])),
    null,
  );

  const unscoped = recordSaveFailure(null, 7, new Error("invalid payload"), new Set());
  assert.equal(resolveSaveFailure(unscoped, 7, new Set(["pages/01.page"])), unscoped);

  const mixed = recordSaveFailure(
    null,
    7,
    new Error("one path was invalid"),
    new Set(["pages/01.page"]),
    { unscoped: true },
  );
  assert.equal(resolveSaveFailure(mixed, 7, new Set(["pages/01.page"])), mixed);

  const nestedCanonical = "deck/deck/pages/new.page";
  const aliasFailure = recordSaveFailure(
    null,
    7,
    new Error("new page failed"),
    new Set([nestedCanonical]),
    {
      pathAliases: new Map([
        ["deck/pages/new.page", nestedCanonical],
        [nestedCanonical, nestedCanonical],
      ]),
    },
  );
  assert.equal(
    resolveSaveFailure(aliasFailure, 7, new Set(["deck/pages/new.page"])),
    null,
    "an equivalent protocol alias must prove the same failed physical path was retried",
  );
});
