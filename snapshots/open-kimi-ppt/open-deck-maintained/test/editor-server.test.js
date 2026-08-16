import assert from "node:assert/strict";
import { once } from "node:events";
import { request as httpRequest } from "node:http";
import {
  mkdirSync,
  mkdtempSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createEditorServer } from "../lib/editor-server.js";

async function withServer(callback, options) {
  const server = createEditorServer(options);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();

  try {
    await callback(`http://127.0.0.1:${address.port}`);
  } finally {
    server.close();
    await once(server, "close");
  }
}

function requestWithHost(url, host) {
  return new Promise((resolvePromise, reject) => {
    const request = httpRequest(url, { headers: { Host: host } }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => resolvePromise({
        status: response.statusCode,
        body: Buffer.concat(chunks).toString("utf8"),
      }));
    });
    request.on("error", reject);
    request.end();
  });
}

test("serves the PPTD editor and its JavaScript modules", async () => {
  await withServer(async (url) => {
    const index = await fetch(`${url}/`);
    assert.equal(index.status, 200);
    assert.match(index.headers.get("content-type"), /^text\/html/);
    const indexHtml = await index.text();
    assert.match(indexHtml, /打开 PPTD 文件夹/);
    assert.match(indexHtml, /allow="fullscreen"/);
    assert.doesNotMatch(indexHtml, /clipboard-(?:read|write)/);

    const app = await fetch(`${url}/app.js`);
    assert.equal(app.status, 200);
    assert.match(app.headers.get("content-type"), /^text\/javascript/);

    const penpal = await fetch(`${url}/penpal.mjs`);
    assert.equal(penpal.status, 200);
    assert.match(penpal.headers.get("content-type"), /^text\/javascript/);
    assert.match(await penpal.text(), /WindowMessenger/);
  });
});

test("sets a restrictive security policy on editor responses", async () => {
  await withServer(async (url) => {
    const response = await fetch(`${url}/`);
    assert.match(response.headers.get("content-security-policy"), /script-src 'self'/);
    assert.match(response.headers.get("content-security-policy"), /frame-src https:\/\/www\.kimi\.com/);
    assert.equal(response.headers.get("x-frame-options"), "DENY");
    assert.equal(response.headers.get("referrer-policy"), "no-referrer");
    assert.equal(response.headers.get("cross-origin-resource-policy"), "same-origin");
  });
});

test("returns 404 for files outside the packaged editor", async () => {
  await withServer(async (url) => {
    const response = await fetch(`${url}/missing.js`);
    assert.equal(response.status, 404);
  });
});

test("rejects forged Host headers on the loopback editor server", async () => {
  await withServer(async (url) => {
    const response = await requestWithHost(`${url}/app.js`, "attacker.example");
    assert.equal(response.status, 403);
    assert.match(response.body, /Forbidden Host/);
  });
});

test("supports HEAD requests without a response body", async () => {
  await withServer(async (url) => {
    const response = await fetch(`${url}/styles.css`, { method: "HEAD" });
    assert.equal(response.status, 200);
    assert.equal(await response.text(), "");
  });
});

test("does not follow symlinks outside a custom editor directory", async (t) => {
  const temporaryDirectory = mkdtempSync(join(tmpdir(), "open-kimi-editor-server-"));
  const editorDirectory = join(temporaryDirectory, "editor");
  const outsideFile = join(temporaryDirectory, "outside.txt");
  t.after(() => rmSync(temporaryDirectory, { recursive: true, force: true }));

  writeFileSync(outsideFile, "private\n");
  mkdirSync(editorDirectory);
  writeFileSync(join(editorDirectory, "index.html"), "safe\n");
  symlinkSync(outsideFile, join(editorDirectory, "leak.txt"));

  await withServer(async (url) => {
    const response = await fetch(`${url}/leak.txt`);
    assert.equal(response.status, 403);
    assert.doesNotMatch(await response.text(), /private/);
  }, { editorDirectory });
});

test("serves a startup snapshot when an intermediate directory is swapped for a symlink", async (t) => {
  const temporaryDirectory = mkdtempSync(join(tmpdir(), "open-kimi-editor-race-"));
  const editorDirectory = join(temporaryDirectory, "editor");
  const assetsDirectory = join(editorDirectory, "assets");
  const originalAssetsDirectory = join(editorDirectory, "assets-original");
  const outsideDirectory = join(temporaryDirectory, "outside");
  t.after(() => rmSync(temporaryDirectory, { recursive: true, force: true }));

  mkdirSync(assetsDirectory, { recursive: true });
  mkdirSync(outsideDirectory);
  writeFileSync(join(editorDirectory, "index.html"), "safe index\n");
  writeFileSync(join(assetsDirectory, "value.txt"), "safe snapshot\n");
  writeFileSync(join(outsideDirectory, "value.txt"), "private outside value\n");

  await withServer(async (url) => {
    // createEditorServer has already completed before this callback. Replace an
    // intermediate directory exactly as an attacker would during a later request.
    renameSync(assetsDirectory, originalAssetsDirectory);
    symlinkSync(outsideDirectory, assetsDirectory, "dir");

    const response = await fetch(`${url}/assets/value.txt`);
    assert.equal(response.status, 200);
    assert.equal(await response.text(), "safe snapshot\n");
  }, { editorDirectory });
});

test("rejects snapshots with too many zero-byte files", (t) => {
  const temporaryDirectory = mkdtempSync(join(tmpdir(), "open-kimi-editor-count-"));
  const editorDirectory = join(temporaryDirectory, "editor");
  t.after(() => rmSync(temporaryDirectory, { recursive: true, force: true }));

  mkdirSync(editorDirectory);
  for (let index = 0; index < 1025; index += 1) {
    writeFileSync(join(editorDirectory, `${String(index).padStart(4, "0")}.js`), "");
  }

  assert.throws(
    () => createEditorServer({ editorDirectory }),
    /1024-file snapshot limit/,
  );
});

test("rejects snapshots whose directory nesting is too deep", (t) => {
  const temporaryDirectory = mkdtempSync(join(tmpdir(), "open-kimi-editor-depth-"));
  const editorDirectory = join(temporaryDirectory, "editor");
  t.after(() => rmSync(temporaryDirectory, { recursive: true, force: true }));

  mkdirSync(editorDirectory);
  let directory = editorDirectory;
  for (let depth = 0; depth < 65; depth += 1) {
    directory = join(directory, "d");
    mkdirSync(directory);
  }

  assert.throws(
    () => createEditorServer({ editorDirectory }),
    /64-level directory depth limit/,
  );
});
