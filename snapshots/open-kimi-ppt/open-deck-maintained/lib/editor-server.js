import {
  closeSync,
  constants,
  fstatSync,
  lstatSync,
  openSync,
  opendirSync,
  readSync,
  realpathSync,
  statSync,
} from "node:fs";
import { createServer } from "node:http";
import { dirname, extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const defaultEditorDirectory = resolve(packageRoot, "editor");
const penpalModulePath = resolve(packageRoot, "skills/open-kimi-ppt/scripts/penpal.mjs");
const MAX_SNAPSHOT_FILE_BYTES = 32 * 1024 * 1024;
const MAX_SNAPSHOT_TOTAL_BYTES = 64 * 1024 * 1024;
const MAX_SNAPSHOT_FILE_COUNT = 1024;
const MAX_SNAPSHOT_ENTRY_COUNT = 2048;
const MAX_SNAPSHOT_DEPTH = 64;

const securityHeaders = {
  "Cache-Control": "no-store",
  "Content-Security-Policy": [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data: blob:",
    "frame-src https://www.kimi.com",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "form-action 'none'",
  ].join("; "),
  "Cross-Origin-Resource-Policy": "same-origin",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
};

const contentTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".mjs", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
]);

function respond(response, statusCode, message) {
  response.writeHead(statusCode, {
    "Content-Type": "text/plain; charset=utf-8",
    ...securityHeaders,
  });
  response.end(message);
}

function isContained(root, candidate) {
  return candidate === root || candidate.startsWith(`${root}${sep}`);
}

function sameFileIdentity(left, right) {
  return left.dev === right.dev && left.ino === right.ino;
}

function sameFileVersion(left, right) {
  return (
    sameFileIdentity(left, right)
    && left.size === right.size
    && left.mtimeMs === right.mtimeMs
    && left.ctimeMs === right.ctimeMs
  );
}

function assertContained(root, candidate, label) {
  if (!isContained(root, candidate)) {
    throw new Error(`${label} resolves outside the editor directory: ${candidate}`);
  }
}

/**
 * Read one regular file into a verified immutable snapshot.
 *
 * Node does not expose openat(2), so resolving a path and opening it later can
 * otherwise follow an intermediate directory symlink swapped between those two
 * operations. We validate the opened descriptor against the path again after
 * reading. More importantly, requests are served only from the resulting
 * in-memory snapshot and never resolve or open filesystem paths at runtime.
 */
function snapshotFile(requestedPath, allowedRoot) {
  const firstResolvedPath = realpathSync(requestedPath);
  assertContained(allowedRoot, firstResolvedPath, "Editor file");

  let descriptor;
  try {
    descriptor = openSync(
      firstResolvedPath,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    );
    const before = fstatSync(descriptor);
    if (!before.isFile()) throw new Error(`Editor asset is not a regular file: ${requestedPath}`);
    if (before.size > MAX_SNAPSHOT_FILE_BYTES) {
      throw new Error(`Editor asset exceeds the 32 MiB snapshot limit: ${requestedPath}`);
    }

    const body = Buffer.alloc(before.size);
    let offset = 0;
    while (offset < body.length) {
      const bytesRead = readSync(descriptor, body, offset, body.length - offset, offset);
      if (bytesRead === 0) break;
      offset += bytesRead;
    }
    const extra = Buffer.allocUnsafe(1);
    const hasExtraByte = readSync(descriptor, extra, 0, 1, offset) !== 0;
    const after = fstatSync(descriptor);

    const finalResolvedPath = realpathSync(requestedPath);
    assertContained(allowedRoot, finalResolvedPath, "Editor file");
    const finalPathStats = statSync(finalResolvedPath);
    if (
      offset !== body.length
      || hasExtraByte
      || firstResolvedPath !== finalResolvedPath
      || !sameFileVersion(before, after)
      || !sameFileIdentity(after, finalPathStats)
    ) {
      throw new Error(`Editor asset changed while its secure snapshot was created: ${requestedPath}`);
    }

    return {
      body,
      contentType: contentTypes.get(extname(firstResolvedPath).toLowerCase())
        ?? "application/octet-stream",
    };
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
  }
}

function createAssetSnapshot(editorDirectory) {
  const realRoot = realpathSync(resolve(editorDirectory));
  const assets = new Map();
  const forbiddenPrefixes = new Set();
  let totalBytes = 0;
  let fileCount = 0;
  let entryCount = 0;

  const addAsset = (route, requestedPath, allowedRoot) => {
    fileCount += 1;
    if (fileCount > MAX_SNAPSHOT_FILE_COUNT) {
      throw new Error(`Editor assets exceed the ${MAX_SNAPSHOT_FILE_COUNT}-file snapshot limit`);
    }
    const asset = snapshotFile(requestedPath, allowedRoot);
    totalBytes += asset.body.length;
    if (totalBytes > MAX_SNAPSHOT_TOTAL_BYTES) {
      throw new Error("Editor assets exceed the 64 MiB snapshot limit");
    }
    assets.set(route, asset);
  };

  const walk = (directoryPath, routePrefix = "", depth = 0) => {
    if (depth > MAX_SNAPSHOT_DEPTH) {
      throw new Error(`Editor assets exceed the ${MAX_SNAPSHOT_DEPTH}-level directory depth limit`);
    }
    const resolvedDirectory = realpathSync(directoryPath);
    assertContained(realRoot, resolvedDirectory, "Editor directory");
    const before = statSync(resolvedDirectory);
    if (!before.isDirectory()) throw new Error(`Editor asset directory is invalid: ${directoryPath}`);

    // Stream directory entries instead of materializing an attacker-controlled
    // wide directory before the global entry ceiling can be enforced.
    const directory = opendirSync(resolvedDirectory);
    try {
      let entry;
      while ((entry = directory.readSync()) !== null) {
        entryCount += 1;
        if (entryCount > MAX_SNAPSHOT_ENTRY_COUNT) {
          throw new Error(`Editor assets exceed the ${MAX_SNAPSHOT_ENTRY_COUNT}-entry traversal limit`);
        }
        const requestedPath = join(resolvedDirectory, entry.name);
        const route = `${routePrefix}/${entry.name}`;
        // Stable symlinks are rejected without dereferencing them. If an entry is
        // swapped after this check, snapshotFile's descriptor/path identity check
        // detects it and aborts server creation.
        if (entry.isSymbolicLink() || lstatSync(requestedPath).isSymbolicLink()) {
          forbiddenPrefixes.add(route);
        } else if (entry.isDirectory()) {
          walk(requestedPath, route, depth + 1);
        } else if (entry.isFile()) {
          addAsset(route, requestedPath, realRoot);
        } else {
          forbiddenPrefixes.add(route);
        }
      }
    } finally {
      directory.closeSync();
    }

    const after = statSync(resolvedDirectory);
    if (!sameFileVersion(before, after)) {
      throw new Error(`Editor directory changed while its snapshot was created: ${directoryPath}`);
    }
  };

  walk(realRoot);

  const realPenpalRoot = realpathSync(dirname(penpalModulePath));
  addAsset("/penpal.mjs", penpalModulePath, realPenpalRoot);
  // This route is deliberately backed by the packaged module even when a
  // custom editor directory contains a same-named symlink.
  forbiddenPrefixes.delete("/penpal.mjs");
  return { assets, forbiddenPrefixes };
}

function isForbidden(pathname, forbiddenPrefixes) {
  for (const prefix of forbiddenPrefixes) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) return true;
  }
  return false;
}

function hasAuthorizedLoopbackHost(request) {
  const port = request.socket.localPort;
  const suffix = port === 80 ? "" : `:${port}`;
  const allowed = new Set([
    `127.0.0.1${suffix}`,
    `localhost${suffix}`,
    `[::1]${suffix}`,
  ]);
  return allowed.has(String(request.headers.host ?? "").toLowerCase());
}

export function createEditorServer({ editorDirectory = defaultEditorDirectory } = {}) {
  // Capture all bytes before accepting a request. Runtime requests therefore
  // have no filesystem lookup for an attacker to race with a symlink swap.
  const { assets, forbiddenPrefixes } = createAssetSnapshot(editorDirectory);

  return createServer((request, response) => {
    if (!hasAuthorizedLoopbackHost(request)) {
      respond(response, 403, "Forbidden Host");
      return;
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      respond(response, 405, "Method Not Allowed");
      return;
    }

    let pathname;
    try {
      pathname = decodeURIComponent(new URL(request.url ?? "/", "http://localhost").pathname);
    } catch {
      respond(response, 400, "Bad Request");
      return;
    }

    if (pathname.endsWith("/")) pathname += "index.html";
    if (isForbidden(pathname, forbiddenPrefixes)) {
      respond(response, 403, "Forbidden");
      return;
    }

    const asset = assets.get(pathname);
    if (!asset) {
      respond(response, 404, "Not Found");
      return;
    }

    response.writeHead(200, {
      "Content-Type": asset.contentType,
      "Content-Length": asset.body.length,
      ...securityHeaders,
    });
    response.end(request.method === "HEAD" ? undefined : asset.body);
  });
}

export function startEditorServer({ host = "127.0.0.1", port = 55173 } = {}) {
  const server = createEditorServer();

  return new Promise((resolvePromise, reject) => {
    const onError = (error) => reject(error);
    server.once("error", onError);
    server.listen(port, host, () => {
      server.off("error", onError);
      const address = server.address();
      const actualPort = typeof address === "object" && address ? address.port : port;
      const urlHost = host.includes(":") && !host.startsWith("[") ? `[${host}]` : host;
      resolvePromise({ server, url: `http://${urlHost}:${actualPort}/` });
    });
  });
}
