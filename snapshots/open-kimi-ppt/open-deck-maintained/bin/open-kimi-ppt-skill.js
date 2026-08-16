#!/usr/bin/env node

import {
  accessSync,
  closeSync,
  constants,
  cpSync,
  existsSync,
  fstatSync,
  linkSync,
  lstatSync,
  mkdtempSync,
  mkdirSync,
  openSync,
  realpathSync,
  readSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { basename, dirname, join, resolve, sep } from "node:path";
import { spawn } from "node:child_process";
import { stdin as input, stdout as output } from "node:process";
import { fileURLToPath } from "node:url";
import { startEditorServer } from "../lib/editor-server.js";
import {
  assertSafeInstallPaths,
  resolveProspectivePath,
} from "../lib/install-paths.js";

const SKILL_NAME = "open-kimi-ppt";
const MIN_NODE_MAJOR = 18;
const MAX_USER_METADATA_BYTES = 64 * 1024;
const MAX_INSTALLED_TREE_BYTES = 64 * 1024 * 1024;
const MAX_INSTALLED_TREE_ENTRIES = 4096;
const MAX_INSTALLED_TREE_DEPTH = 64;
const TREE_HASH_CHUNK_BYTES = 64 * 1024;
const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceDirectory = join(packageRoot, "skills", SKILL_NAME);
const packageVersion = JSON.parse(readFileSync(join(packageRoot, "package.json"), "utf8")).version;

const PYTHON_TOOLS = Object.freeze({
  lint: Object.freeze({
    script: "pptd_quality.py",
    args: Object.freeze([]),
    summary: "Lint a PPTD project and emit a quality receipt",
  }),
  "inspect-pptx": Object.freeze({
    script: "pptx_native.py",
    args: Object.freeze(["inspect"]),
    summary: "Inventory an existing PPTX without changing it",
  }),
  "validate-pptx": Object.freeze({
    script: "pptx_native.py",
    args: Object.freeze(["validate"]),
    summary: "Validate a PPTX package and its relationships",
  }),
  "fill-pptx": Object.freeze({
    script: "pptx_native.py",
    args: Object.freeze(["fill"]),
    summary: "Apply a conservative JSON fill plan to a PPTX",
  }),
  "export-local": Object.freeze({
    script: "export_pptx_local.py",
    args: Object.freeze([]),
    summary: "Export the supported PPTD subset to PPTX offline",
  }),
  "audit-assets": Object.freeze({
    script: "asset_pack_audit.py",
    args: Object.freeze([]),
    summary: "Audit an optional asset pack and its licenses offline",
  }),
  "audit-prompts": Object.freeze({
    script: "prompt_audit.py",
    args: Object.freeze([]),
    summary: "Audit prompt budgets, references, and duplication",
  }),
});

const KNOWN_TARGETS = [
  { id: "agents", label: "Shared / default", relative: [".agents", "skills"] },
  { id: "codex", label: "Codex", relative: [".codex", "skills"] },
  { id: "claude", label: "Claude Code", relative: [".claude", "skills"] },
  { id: "cursor", label: "Cursor", relative: [".cursor", "skills"] },
  { id: "workbuddy", label: "WorkBuddy", relative: [".workbuddy", "skills"] },
];

function assertNodeVersion() {
  const major = Number.parseInt(process.versions.node.split(".")[0], 10);
  if (!Number.isInteger(major) || major < MIN_NODE_MAJOR) {
    throw new Error(
      `Node.js ${MIN_NODE_MAJOR}+ is required; found ${process.version}. Install from https://nodejs.org`,
    );
  }
}

function printHelp(command) {
  if (command === "serve") {
    console.log(`Start the local PPTD editor and exporter.

Usage:
  open-deck-skill serve [options]

Options:
  --port <number>  HTTP port (default: 55173)
  --open           Open the editor in the default browser
  -h, --help       Show this help
  -V, --version    Show version
`);
    return;
  }

  const pythonCommands = Object.entries(PYTHON_TOOLS)
    .map(([name, tool]) => `  ${name.padEnd(16)} ${tool.summary}`)
    .join("\n");

  console.log(`Install ${SKILL_NAME}, start its local editor, or run a deck tool.

Usage:
  open-deck-skill [install] [options]
  open-deck-skill serve [options]
  open-deck-skill <deck-command> [arguments...]

Install options:
  --target <directory>  Skills directory (repeatable)
  -y, --yes             Non-interactive; install to ~/.agents/skills when no --target
  --all                 Install to all detected agent skill directories
                        (agents whose home directory is missing are skipped)
  -h, --help            Show this help
  -V, --version         Show version

Deck commands (arguments are passed directly to the Python tool):
${pythonCommands}

In an interactive terminal (no --target / --yes / --all), a checklist is shown:
  ↑/↓ move  space select  a all  enter confirm

For agents / CI:
  npx open-deck-skill@latest install -y
If the CLI is already installed globally:
  open-deck-skill install -y
From an audited source checkout:
  node bin/open-kimi-ppt-skill.js install -y

Re-running install replaces an existing open-kimi-ppt installation.
Repeated targets are resolved and deduplicated, and every target is preflighted
before installation starts. If a later commit fails, earlier targets are rolled
back when their installed trees are still unchanged; otherwise recovery paths
are reported and preserved.
Run "open-deck-skill serve --help" for server options.
Run "open-deck-skill <deck-command> --help" for command-specific options.
Set OPEN_DECK_PYTHON to an explicit Python 3 executable when auto-detection is unsuitable.
`);
}

function printVersion() {
  console.log(packageVersion);
}

function parseArguments(arguments_) {
  const args = [...arguments_];
  const firstArgument = args[0];
  const isPythonTool = Object.hasOwn(PYTHON_TOOLS, firstArgument);
  const command = firstArgument === "install" || firstArgument === "serve" || isPythonTool
    ? args.shift()
    : "install";

  if (isPythonTool) {
    return { command, pythonArgs: args };
  }

  const options = command === "serve"
    ? { command, open: false, port: 55173 }
    : { command, targets: [], yes: false, all: false };

  while (args.length > 0) {
    const argument = args.shift();

    // Accepted for backward compatibility; install always overwrites.
    if (command === "install" && argument === "--force") {
      continue;
    }

    if (command === "install" && (argument === "-y" || argument === "--yes")) {
      options.yes = true;
      continue;
    }

    if (command === "install" && argument === "--all") {
      options.all = true;
      continue;
    }

    if (command === "install" && argument === "--target") {
      const target = args.shift();
      if (!target || target.startsWith("-")) {
        throw new Error("--target requires a directory");
      }
      options.targets.push(resolve(target));
      continue;
    }

    if (command === "serve" && argument === "--port") {
      const port = Number(args.shift());
      if (!Number.isInteger(port) || port < 1 || port > 65_535) {
        throw new Error("--port must be an integer between 1 and 65535");
      }
      options.port = port;
      continue;
    }

    if (command === "serve" && argument === "--open") {
      options.open = true;
      continue;
    }

    if (argument === "--help" || argument === "-h") {
      options.help = true;
      continue;
    }

    if (argument === "--version" || argument === "-V") {
      options.version = true;
      continue;
    }

    throw new Error(`unknown argument: ${argument}`);
  }

  return options;
}

function pythonCandidates() {
  const configured = process.env.OPEN_DECK_PYTHON?.trim();
  if (configured) {
    return [{ executable: configured, args: [] }];
  }
  if (process.platform === "win32") {
    return [
      { executable: "py", args: ["-3"] },
      { executable: "python", args: [] },
      { executable: "python3", args: [] },
    ];
  }
  return [
    { executable: "python3", args: [] },
    { executable: "python", args: [] },
  ];
}

function runChild(executable, args) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(executable, args, {
      shell: false,
      stdio: "inherit",
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => resolvePromise({ code, signal }));
  });
}

async function runPythonTool(command, passedArguments) {
  const tool = PYTHON_TOOLS[command];
  if (!tool) {
    throw new Error(`unknown deck command: ${command}`);
  }

  const script = join(sourceDirectory, "scripts", tool.script);
  if (!existsSync(script)) {
    throw new Error(
      `The ${command} tool is unavailable because ${tool.script} is not included in this installation.`,
    );
  }

  const attempted = [];
  for (const candidate of pythonCandidates()) {
    attempted.push(candidate.executable);
    try {
      const result = await runChild(candidate.executable, [
        ...candidate.args,
        script,
        ...tool.args,
        ...passedArguments,
      ]);
      if (result.signal) {
        throw new Error(`${command} was terminated by signal ${result.signal}`);
      }
      return result.code ?? 1;
    } catch (error) {
      if (error?.code === "ENOENT") {
        continue;
      }
      throw new Error(`Could not start Python for ${command}: ${error.message}`, { cause: error });
    }
  }

  const configured = process.env.OPEN_DECK_PYTHON?.trim();
  const detail = configured
    ? `OPEN_DECK_PYTHON points to an executable that could not be found: ${configured}`
    : `Tried: ${attempted.join(", ")}`;
  throw new Error(
    `Python 3 is required to run ${command}. ${detail}. Install Python 3 or set `
    + "OPEN_DECK_PYTHON to its executable path.",
  );
}

function openBrowser(url) {
  const command = process.platform === "darwin" ? "open" : process.platform === "win32" ? "cmd" : "xdg-open";
  const args = process.platform === "win32" ? ["/c", "start", "", url] : [url];
  const child = spawn(command, args, { detached: true, stdio: "ignore" });
  child.on("error", (error) => console.warn(`Could not open the browser: ${error.message}`));
  child.unref();
}

function defaultSkillsDirectory() {
  return join(homedir(), ".agents", "skills");
}

function knownTargetDirectories() {
  const home = homedir();
  return KNOWN_TARGETS.map((entry) => ({
    ...entry,
    directory: join(home, ...entry.relative),
  }));
}

function displayPath(absolutePath) {
  const home = homedir();
  const normalized = absolutePath.split(sep).join("/");
  const homeNormalized = home.split(sep).join("/");
  if (normalized === homeNormalized || normalized.startsWith(`${homeNormalized}/`)) {
    return `~${normalized.slice(homeNormalized.length)}`;
  }
  return absolutePath;
}

function isInteractiveInstall() {
  return Boolean(input.isTTY && output.isTTY);
}

function uniqueDirectories(directories) {
  const seen = new Set();
  const unique = [];
  for (const directory of directories) {
    const key = resolveProspectivePath(directory);
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(resolve(directory));
  }
  return unique;
}

async function promptInstallTargets() {
  const choices = knownTargetDirectories();
  const selected = new Set([0]);
  let cursor = 0;
  const lines = choices.length + 3;

  const render = (initial = false) => {
    if (!initial) {
      output.write(`\u001b[${lines}A`);
    }
    output.write("Install open-kimi-ppt to which skills directories?\n");
    output.write("↑/↓ move · space select · a all · enter confirm · ctrl+c cancel\n");
    for (const [index, choice] of choices.entries()) {
      const pointer = index === cursor ? "❯" : " ";
      const mark = selected.has(index) ? "◉" : "◯";
      const installed = existsSync(join(choice.directory, SKILL_NAME, "SKILL.md"))
        ? " (installed)"
        : "";
      const line = `${pointer}${mark} ${displayPath(choice.directory).padEnd(28)} ${choice.label}${installed}`;
      output.write(`\u001b[2K${line}\n`);
    }
  };

  return new Promise((resolvePromise, reject) => {
    if (typeof input.setRawMode !== "function") {
      resolvePromise([defaultSkillsDirectory()]);
      return;
    }

    render(true);
    input.setRawMode(true);
    input.resume();
    input.setEncoding("utf8");

    const cleanup = () => {
      input.off("data", onData);
      input.setRawMode(false);
      input.pause();
    };

    const onData = (key) => {
      if (key === "\u0003") {
        cleanup();
        output.write("\n");
        reject(new Error("install cancelled"));
        return;
      }

      if (key === "\u001b[A" || key === "k") {
        cursor = (cursor - 1 + choices.length) % choices.length;
        render();
        return;
      }

      if (key === "\u001b[B" || key === "j") {
        cursor = (cursor + 1) % choices.length;
        render();
        return;
      }

      if (key === " ") {
        if (selected.has(cursor)) selected.delete(cursor);
        else selected.add(cursor);
        render();
        return;
      }

      if (key === "a" || key === "A") {
        if (selected.size === choices.length) selected.clear();
        else for (let index = 0; index < choices.length; index += 1) selected.add(index);
        render();
        return;
      }

      if (key === "\r" || key === "\n") {
        if (selected.size === 0) {
          output.write("\u0007");
          return;
        }
        cleanup();
        output.write("\n");
        resolvePromise([...selected].sort((a, b) => a - b).map((index) => choices[index].directory));
      }
    };

    input.on("data", onData);
  });
}

function detectedTargetDirectories() {
  const home = homedir();
  const detected = [];
  const skipped = [];
  for (const entry of knownTargetDirectories()) {
    if (existsSync(join(home, entry.relative[0]))) detected.push(entry);
    else skipped.push(entry);
  }
  return { detected, skipped };
}

async function resolveInstallTargets(options) {
  if (options.targets.length > 0) {
    return uniqueDirectories(options.targets);
  }

  if (options.all) {
    const { detected, skipped } = detectedTargetDirectories();
    for (const entry of skipped) {
      console.log(`Skipped ${entry.directory} (${entry.label} not found)`);
    }
    if (detected.length === 0) {
      throw new Error(
        "No known agent directories found; nothing installed. Use -y for ~/.agents/skills or --target <directory>.",
      );
    }
    return uniqueDirectories(detected.map((entry) => entry.directory));
  }

  if (options.yes || !isInteractiveInstall()) {
    return [defaultSkillsDirectory()];
  }

  return promptInstallTargets();
}

function sameFileVersion(left, right) {
  return (
    left.dev === right.dev
    && left.ino === right.ino
    && left.size === right.size
    && left.nlink === right.nlink
    && left.mtimeMs === right.mtimeMs
    && left.ctimeMs === right.ctimeMs
  );
}

function sameFileIdentity(left, right) {
  return left.dev === right.dev && left.ino === right.ino;
}

function sameDirectoryIdentity(left, right) {
  return (
    sameFileIdentity(left, right)
    && left.birthtimeMs === right.birthtimeMs
  );
}

function directoryAncestors(path) {
  const ancestors = [];
  let cursor = resolve(path);
  while (true) {
    ancestors.push(cursor);
    const parent = dirname(cursor);
    if (parent === cursor) break;
    cursor = parent;
  }
  return ancestors.reverse();
}

function captureDirectoryAncestry(path) {
  const entries = [];
  let foundMissing = false;
  for (const ancestor of directoryAncestors(path)) {
    let stats;
    try {
      stats = lstatSync(ancestor);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      foundMissing = true;
      entries.push(Object.freeze({ path: ancestor, exists: false, stats: null }));
      continue;
    }
    if (foundMissing || stats.isSymbolicLink() || !stats.isDirectory()) {
      throw new Error(`install parent must be a real directory: ${ancestor}`);
    }
    entries.push(Object.freeze({ path: ancestor, exists: true, stats }));
  }

  const deepestExisting = [...entries].reverse().find((entry) => entry.exists);
  accessSync(deepestExisting.path, constants.W_OK | constants.X_OK);
  return Object.freeze(entries);
}

function assertDirectoryAncestry(entries) {
  for (const entry of entries) {
    let current;
    try {
      current = lstatSync(entry.path);
    } catch (error) {
      if (error?.code === "ENOENT" && !entry.exists) continue;
      throw new Error(
        `install parent ancestry changed after preflight: ${entry.path}`,
        { cause: error },
      );
    }

    if (
      !entry.exists
      || current.isSymbolicLink()
      || !current.isDirectory()
      || !sameDirectoryIdentity(current, entry.stats)
    ) {
      throw new Error(`install parent ancestry changed after preflight: ${entry.path}`);
    }
  }
}

function materializeSkillsDirectory(plan) {
  const entries = plan.ancestorSnapshot.map((entry) => ({ ...entry }));
  assertDirectoryAncestry(entries);
  for (let index = 0; index < entries.length; index += 1) {
    if (entries[index].exists) continue;
    // Revalidate the complete chain before creating each component. Creating
    // one directory at a time also makes any concurrent insertion fail closed
    // instead of letting recursive mkdir traverse it.
    assertDirectoryAncestry(entries);
    mkdirSync(entries[index].path);
    const current = lstatSync(entries[index].path);
    if (current.isSymbolicLink() || !current.isDirectory()) {
      throw new Error(`install parent must be a real directory: ${entries[index].path}`);
    }
    entries[index] = { path: entries[index].path, exists: true, stats: current };
  }
  assertDirectoryAncestry(entries);
  accessSync(plan.skillsDirectory, constants.W_OK | constants.X_OK);
  return entries.at(-1).stats;
}

function openDirectoryGuard(path, expected) {
  let descriptor;
  try {
    descriptor = openSync(
      path,
      constants.O_RDONLY
        | (constants.O_DIRECTORY ?? 0)
        | (constants.O_NOFOLLOW ?? 0),
    );
  } catch (error) {
    // Some Windows filesystems/runtimes do not expose directory handles via
    // fs.open. Keep the full identity-chain checks as a fail-closed fallback;
    // POSIX platforms always retain the stronger open-handle guard.
    if (
      process.platform !== "win32"
      || !["EISDIR", "EINVAL", "EPERM", "EACCES"].includes(error?.code)
    ) {
      throw error;
    }
    const current = lstatSync(path);
    if (
      current.isSymbolicLink()
      || !current.isDirectory()
      || !sameDirectoryIdentity(current, expected)
    ) {
      throw new Error(`install parent changed before it could be opened safely: ${path}`, {
        cause: error,
      });
    }
    return { descriptor: null, expected: current, path, closed: false };
  }
  try {
    const opened = fstatSync(descriptor);
    const current = lstatSync(path);
    if (
      !opened.isDirectory()
      || current.isSymbolicLink()
      || !current.isDirectory()
      || !sameDirectoryIdentity(opened, expected)
      || !sameDirectoryIdentity(current, opened)
    ) {
      throw new Error(`install parent changed before it could be opened safely: ${path}`);
    }
    return { descriptor, expected: opened, path, closed: false };
  } catch (error) {
    closeSync(descriptor);
    throw error;
  }
}

function assertDirectoryGuard(guard) {
  if (guard.closed) {
    throw new Error(`install parent guard is already closed: ${guard.path}`);
  }
  let opened;
  let current;
  try {
    opened = guard.descriptor === null
      ? guard.expected
      : fstatSync(guard.descriptor);
    current = lstatSync(guard.path);
  } catch (error) {
    throw new Error(`install parent changed during installation: ${guard.path}`, {
      cause: error,
    });
  }
  if (
    !opened.isDirectory()
    || current.isSymbolicLink()
    || !current.isDirectory()
    || !sameDirectoryIdentity(opened, guard.expected)
    || !sameDirectoryIdentity(current, opened)
  ) {
    throw new Error(`install parent changed during installation: ${guard.path}`);
  }
}

function closeDirectoryGuard(guard) {
  if (!guard || guard.closed) return;
  if (guard.descriptor !== null) closeSync(guard.descriptor);
  guard.closed = true;
}

function assertDirectoryUnchanged(path, expected) {
  const current = lstatSync(path);
  if (
    current.isSymbolicLink()
    || !current.isDirectory()
    || !sameFileVersion(current, expected)
  ) {
    throw new Error(`skill destination changed during installation: ${path}`);
  }
}

function readRegularFileNoFollow(path, maxBytes, { expectedLinks = 1 } = {}) {
  let before;
  try {
    before = lstatSync(path);
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
  if (
    before.isSymbolicLink()
    || !before.isFile()
    || before.nlink !== expectedLinks
  ) return null;
  if (before.size > maxBytes) {
    throw new Error(`agent metadata exceeds the ${maxBytes}-byte safety limit: ${path}`);
  }

  const descriptor = openSync(
    path,
    constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
  );
  try {
    const opened = fstatSync(descriptor);
    if (
      opened.nlink !== expectedLinks
      || !opened.isFile()
      || !sameFileVersion(opened, before)
    ) {
      throw new Error(`agent metadata changed before it could be preserved: ${path}`);
    }
    const content = Buffer.alloc(opened.size);
    let offset = 0;
    while (offset < content.length) {
      const count = readSync(
        descriptor,
        content,
        offset,
        content.length - offset,
        offset,
      );
      if (count === 0) break;
      offset += count;
    }
    const extra = Buffer.allocUnsafe(1);
    const hasExtraByte = readSync(descriptor, extra, 0, 1, offset) !== 0;
    const after = fstatSync(descriptor);
    if (
      offset !== content.length
      || hasExtraByte
      || !sameFileVersion(after, opened)
    ) {
      throw new Error(`agent metadata changed while it was being preserved: ${path}`);
    }
    return Object.freeze({ content, version: after });
  } finally {
    closeSync(descriptor);
  }
}

function statsFingerprint(stats, { directory = false } = {}) {
  const stable = {
    dev: stats.dev,
    ino: stats.ino,
    mode: stats.mode,
    nlink: stats.nlink,
    size: stats.size,
    birthtimeMs: stats.birthtimeMs,
  };
  // Renaming a directory changes ctime on some filesystems even though its
  // identity and contents are unchanged. Directory child names and records
  // already cover structural edits, so only file timestamps participate in
  // the content/version fingerprint.
  if (!directory) {
    stable.mtimeMs = stats.mtimeMs;
    stable.ctimeMs = stats.ctimeMs;
  }
  return stable;
}

function addTreeRecord(hash, record) {
  hash.update(JSON.stringify(record));
  hash.update("\0");
}

function snapshotInstalledTree(root) {
  const hash = createHash("sha256");
  const limits = { entries: 0, bytes: 0 };

  const walk = (path, relativePath, depth) => {
    if (depth > MAX_INSTALLED_TREE_DEPTH) {
      throw new Error(
        `installed tree exceeds the ${MAX_INSTALLED_TREE_DEPTH}-level safety limit: ${root}`,
      );
    }
    limits.entries += 1;
    if (limits.entries > MAX_INSTALLED_TREE_ENTRIES) {
      throw new Error(
        `installed tree exceeds the ${MAX_INSTALLED_TREE_ENTRIES}-entry safety limit: ${root}`,
      );
    }

    const before = lstatSync(path);
    if (before.isSymbolicLink()) {
      throw new Error(`installed tree contains a symbolic link: ${path}`);
    }

    if (before.isDirectory()) {
      const names = readdirSync(path).sort((left, right) => (
        left < right ? -1 : left > right ? 1 : 0
      ));
      for (const name of names) {
        walk(
          join(path, name),
          relativePath === "" ? name : `${relativePath}/${name}`,
          depth + 1,
        );
      }
      const after = lstatSync(path);
      if (
        after.isSymbolicLink()
        || !after.isDirectory()
        || !sameFileVersion(after, before)
      ) {
        throw new Error(`installed tree changed while it was being fingerprinted: ${path}`);
      }
      addTreeRecord(hash, {
        path: relativePath,
        type: "directory",
        stats: statsFingerprint(after, { directory: true }),
      });
      return;
    }

    // A preserved top-level metadata file deliberately has one link in the
    // staged/new tree and one in the retained previous tree until the
    // transaction is finalized. No other installed file may be hard-linked.
    const expectedLinks = relativePath === "_user_meta.json" && before.nlink === 2
      ? 2
      : 1;
    if (!before.isFile() || before.nlink !== expectedLinks) {
      throw new Error(`installed tree contains an unsupported file type: ${path}`);
    }
    if (before.size > MAX_INSTALLED_TREE_BYTES - limits.bytes) {
      throw new Error(
        `installed tree exceeds the ${MAX_INSTALLED_TREE_BYTES}-byte safety limit: ${root}`,
      );
    }

    const descriptor = openSync(
      path,
      constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0),
    );
    let after;
    const contentHash = createHash("sha256");
    try {
      const opened = fstatSync(descriptor);
      if (
        !opened.isFile()
        || opened.nlink !== expectedLinks
        || !sameFileVersion(opened, before)
      ) {
        throw new Error(`installed file changed before fingerprinting: ${path}`);
      }
      const chunk = Buffer.allocUnsafe(TREE_HASH_CHUNK_BYTES);
      let offset = 0;
      while (offset < opened.size) {
        const count = readSync(
          descriptor,
          chunk,
          0,
          Math.min(chunk.length, opened.size - offset),
          offset,
        );
        if (count === 0) break;
        contentHash.update(chunk.subarray(0, count));
        offset += count;
      }
      const extra = Buffer.allocUnsafe(1);
      const hasExtraByte = readSync(descriptor, extra, 0, 1, offset) !== 0;
      after = fstatSync(descriptor);
      if (
        offset !== opened.size
        || hasExtraByte
        || !sameFileVersion(after, opened)
      ) {
        throw new Error(`installed file changed while fingerprinting: ${path}`);
      }
    } finally {
      closeSync(descriptor);
    }

    limits.bytes += after.size;
    addTreeRecord(hash, {
      path: relativePath,
      type: "file",
      stats: statsFingerprint(after),
      sha256: contentHash.digest("hex"),
    });
  };

  walk(root, "", 0);
  return Object.freeze({
    sha256: hash.digest("hex"),
    entries: limits.entries,
    bytes: limits.bytes,
  });
}

function sameTreeSnapshot(left, right) {
  return (
    left.sha256 === right.sha256
    && left.entries === right.entries
    && left.bytes === right.bytes
  );
}

function preflightSkillInstall(skillsDirectory) {
  if (!existsSync(join(sourceDirectory, "SKILL.md"))) {
    throw new Error(`packaged skill is incomplete: ${sourceDirectory}`);
  }

  assertSafeInstallPaths(sourceDirectory, skillsDirectory, SKILL_NAME);
  // Commit through the prospective real path captured at preflight time. A
  // stable pre-existing symlink supplied by the user is therefore supported,
  // while replacing any lexical ancestor after preflight cannot redirect the
  // subsequent transaction.
  const canonicalSkillsDirectory = resolveProspectivePath(skillsDirectory);
  assertSafeInstallPaths(sourceDirectory, canonicalSkillsDirectory, SKILL_NAME);
  const ancestorSnapshot = captureDirectoryAncestry(canonicalSkillsDirectory);
  const destination = join(canonicalSkillsDirectory, SKILL_NAME);
  let destinationStats = null;
  try {
    destinationStats = lstatSync(destination);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  const replaced = destinationStats !== null;
  if (
    destinationStats
    && (!destinationStats.isDirectory() || destinationStats.isSymbolicLink())
  ) {
    throw new Error(
      `existing skill destination must be a real directory, not a file or symbolic link: ${destination}`,
    );
  }

  // Validate agent-owned metadata for every destination before mutating any
  // target. The commit phase reads it again after moving the old tree, which
  // closes the path-based update window for the bytes that are preserved.
  if (destinationStats) {
    readRegularFileNoFollow(
      join(destination, "_user_meta.json"),
      MAX_USER_METADATA_BYTES,
    );
  }

  return Object.freeze({
    requestedSkillsDirectory: skillsDirectory,
    skillsDirectory: canonicalSkillsDirectory,
    destination,
    destinationStats,
    replaced,
    ancestorSnapshot,
  });
}

function commitSkillInstall(plan, { afterPublish = () => undefined } = {}) {
  const {
    skillsDirectory,
    destination,
    destinationStats,
    replaced,
  } = plan;

  const parentStats = materializeSkillsDirectory(plan);
  const parentGuard = openDirectoryGuard(skillsDirectory, parentStats);
  let stagingRoot = null;
  let stagedSkill = null;
  let previousSkill = null;
  let movedExisting = false;
  let preserveStaging = false;
  let committedTransaction = null;
  let publishedTransaction = null;

  try {
    assertDirectoryGuard(parentGuard);
    stagingRoot = mkdtempSync(join(skillsDirectory, `.${SKILL_NAME}-`));
    stagedSkill = join(stagingRoot, SKILL_NAME);
    previousSkill = join(stagingRoot, `${SKILL_NAME}.previous`);
    assertDirectoryGuard(parentGuard);

    cpSync(sourceDirectory, stagedSkill, {
      recursive: true,
      filter: (source) => ![".DS_Store", "_user_meta.json"].includes(basename(source)),
    });
    assertDirectoryGuard(parentGuard);

    if (replaced) {
      assertDirectoryUnchanged(destination, destinationStats);
      assertDirectoryGuard(parentGuard);
      // Keep the previous installation on the same filesystem until the new
      // tree has been committed. A failed rename must never turn an update
      // into an uninstall.
      renameSync(destination, previousSkill);
      movedExisting = true;
      assertDirectoryGuard(parentGuard);
      const movedStats = lstatSync(previousSkill);
      if (!sameFileIdentity(movedStats, destinationStats)) {
        throw new Error(
          `skill destination changed while it was being committed: ${destination}`,
        );
      }

      // `_user_meta.json` belongs to the installed agent, not to this package.
      // Preserve the same inode in the staged tree instead of copying its
      // bytes. A process that already holds the old file open can therefore
      // never have a late metadata write discarded when the previous tree is
      // cleaned up: its write is also visible through the newly installed
      // name. Hard-link and both no-follow reads are verified before publish.
      const previousMetadataPath = join(previousSkill, "_user_meta.json");
      const metadata = readRegularFileNoFollow(
        previousMetadataPath,
        MAX_USER_METADATA_BYTES,
      );
      if (metadata !== null) {
        assertDirectoryGuard(parentGuard);
        const stagedMetadataPath = join(stagedSkill, "_user_meta.json");
        linkSync(previousMetadataPath, stagedMetadataPath);
        assertDirectoryGuard(parentGuard);
        const confirmedPrevious = readRegularFileNoFollow(
          previousMetadataPath,
          MAX_USER_METADATA_BYTES,
          { expectedLinks: 2 },
        );
        const confirmedStaged = readRegularFileNoFollow(
          stagedMetadataPath,
          MAX_USER_METADATA_BYTES,
          { expectedLinks: 2 },
        );
        if (
          confirmedPrevious === null
          || confirmedStaged === null
          || !sameFileIdentity(confirmedPrevious.version, metadata.version)
          || !sameFileVersion(
            confirmedPrevious.version,
            confirmedStaged.version,
          )
          || !confirmedPrevious.content.equals(metadata.content)
          || !confirmedStaged.content.equals(metadata.content)
        ) {
          throw new Error(
            `agent metadata changed before it could be committed: ${previousMetadataPath}`,
          );
        }
      }
    }
    // Fingerprint the private staged tree before it becomes public. If the
    // commit fails after rename, this immutable expectation lets rollback
    // distinguish our exact tree from a concurrent edit.
    const expectedInstalledTree = snapshotInstalledTree(stagedSkill);
    const stagedStats = lstatSync(stagedSkill);
    assertDirectoryGuard(parentGuard);
    renameSync(stagedSkill, destination);
    publishedTransaction = {
      destination,
      installedStats: stagedStats,
      installedTreeSnapshot: expectedInstalledTree,
      previousSkill,
      replaced,
      stagingRoot,
      parentGuard,
      closed: false,
    };
    afterPublish(publishedTransaction);
    assertDirectoryGuard(parentGuard);
    const installedStats = lstatSync(destination);
    if (
      installedStats.isSymbolicLink()
      || !installedStats.isDirectory()
      || !sameFileIdentity(installedStats, stagedStats)
    ) {
      throw new Error(`installed skill is not a real directory: ${destination}`);
    }
    const installedTreeSnapshot = snapshotInstalledTree(destination);
    if (!sameTreeSnapshot(installedTreeSnapshot, expectedInstalledTree)) {
      throw new Error(
        `installed skill changed while it was being published: ${destination}`,
      );
    }
    assertDirectoryGuard(parentGuard);
    committedTransaction = publishedTransaction;
    return committedTransaction;
  } catch (error) {
    if (publishedTransaction && !publishedTransaction.closed) {
      try {
        rollbackCommittedInstall(publishedTransaction);
      } catch (rollbackError) {
        preserveStaging = true;
        throw new AggregateError(
          [error, rollbackError],
          `install failed after publishing ${destination}, and the current target could not `
          + `be restored safely: ${rollbackError.message}`,
        );
      }
      // rollbackCommittedInstall already restored/deleted the public tree and
      // closed this transaction. Do not run the pre-publication recovery path
      // a second time.
      throw error;
    }
    if (movedExisting) {
      try {
        assertDirectoryGuard(parentGuard);
      } catch (guardError) {
        preserveStaging = true;
        throw new Error(
          `install failed (${error.message}); the install parent also changed `
          + `(${guardError.message}). Recovery data was preserved near ${stagingRoot}`,
          { cause: error },
        );
      }
      let destinationExists = false;
      try {
        lstatSync(destination);
        destinationExists = true;
      } catch (existsError) {
        if (existsError?.code !== "ENOENT") throw existsError;
      }
      if (destinationExists) {
        preserveStaging = true;
        throw new Error(
          `install failed (${error.message}); the destination changed during installation. `
          + `Previous installation retained at ${previousSkill}`,
          { cause: error },
        );
      }
      try {
        assertDirectoryGuard(parentGuard);
        renameSync(previousSkill, destination);
        assertDirectoryGuard(parentGuard);
        movedExisting = false;
      } catch (rollbackError) {
        preserveStaging = true;
        throw new Error(
          `install failed (${error.message}); restoring the previous installation also failed `
          + `(${rollbackError.message}). Backup retained at ${previousSkill}`,
          { cause: error },
        );
      }
    }
    throw error;
  } finally {
    if (!committedTransaction) {
      if (stagingRoot && !preserveStaging) {
        try {
          assertDirectoryGuard(parentGuard);
          rmSync(stagingRoot, { recursive: true, force: true });
          assertDirectoryGuard(parentGuard);
        } catch {
          // If the public path no longer resolves to the opened install
          // parent, do not attempt a second path-based cleanup.
        }
      }
      closeDirectoryGuard(parentGuard);
    }
  }
}

function installedTreeConflict(transaction, cause = null) {
  const recovery = transaction.replaced
    ? `The externally changed installation remains at ${transaction.destination}; `
      + `the previous installation is retained at ${transaction.previousSkill}`
    : `The externally changed installation remains at ${transaction.destination}; `
      + `transaction data is retained at ${transaction.stagingRoot}`;
  closeDirectoryGuard(transaction.parentGuard);
  transaction.closed = true;
  transaction.preserved = true;
  return new Error(
    `installed tree changed after commit; refusing rollback to avoid deleting external changes. `
    + recovery
    + (cause ? ` (${cause.message})` : ""),
    cause ? { cause } : undefined,
  );
}

function assertInstalledTreeUnchanged(transaction) {
  let current;
  try {
    current = snapshotInstalledTree(transaction.destination);
  } catch (error) {
    throw installedTreeConflict(transaction, error);
  }
  if (!sameTreeSnapshot(current, transaction.installedTreeSnapshot)) {
    throw installedTreeConflict(transaction);
  }
}

function assertPublishedDirectoryIdentity(transaction) {
  const current = lstatSync(transaction.destination);
  if (
    current.isSymbolicLink()
    || !current.isDirectory()
    || !sameFileIdentity(current, transaction.installedStats)
  ) {
    throw new Error(`published skill directory changed identity: ${transaction.destination}`);
  }
}

function rollbackCommittedInstall(transaction) {
  if (transaction.closed) return;
  const {
    destination,
    previousSkill,
    replaced,
    stagingRoot,
    parentGuard,
  } = transaction;
  try {
    assertDirectoryGuard(parentGuard);
  } catch (error) {
    throw installedTreeConflict(transaction, error);
  }
  try {
    // A same-filesystem rename can legitimately change the directory ctime.
    // Identity proves this is still the directory we published; the complete
    // tree snapshot below detects content and metadata edits independently.
    assertPublishedDirectoryIdentity(transaction);
  } catch (error) {
    throw installedTreeConflict(transaction, error);
  }
  assertInstalledTreeUnchanged(transaction);
  try {
    assertDirectoryGuard(parentGuard);
  } catch (error) {
    throw installedTreeConflict(transaction, error);
  }

  const committedSkill = join(stagingRoot, `${SKILL_NAME}.committed`);
  renameSync(destination, committedSkill);
  assertDirectoryGuard(parentGuard);
  if (replaced) {
    try {
      renameSync(previousSkill, destination);
      assertDirectoryGuard(parentGuard);
    } catch (restoreError) {
      let restoredNew = false;
      try {
        if (!existsSync(destination)) {
          renameSync(committedSkill, destination);
          restoredNew = true;
        }
      } catch {
        // Both trees remain in or next to staging. The caller reports the
        // retained path rather than risking another destructive rename.
      }
      throw new Error(
        `could not restore ${destination} after a later target failed: ${restoreError.message}. `
        + `${restoredNew ? "The newly installed tree remains active; " : ""}`
        + `recovery data retained at ${stagingRoot}`,
        { cause: restoreError },
      );
    }
  }

  transaction.closed = true;
  rmSync(stagingRoot, { recursive: true, force: true });
  closeDirectoryGuard(parentGuard);
}

function finalizeCommittedInstall(transaction) {
  if (transaction.closed) return;
  try {
    assertDirectoryGuard(transaction.parentGuard);
    rmSync(transaction.stagingRoot, { recursive: true, force: true });
    assertDirectoryGuard(transaction.parentGuard);
    transaction.closed = true;
  } catch (error) {
    console.warn(
      `Installed ${SKILL_NAME}, but could not remove transaction data at `
      + `${transaction.stagingRoot}: ${error.message}`,
    );
  } finally {
    closeDirectoryGuard(transaction.parentGuard);
  }
}

export function installSkillTargets(
  targets,
  {
    beforeCommit = () => undefined,
    afterPublish = () => undefined,
    commit = (plan) => commitSkillInstall(plan, { afterPublish }),
  } = {},
) {
  // This is deliberately a separate phase: a bad second target must not be
  // discovered after the first agent installation has already been replaced.
  const plans = uniqueDirectories(targets).map(preflightSkillInstall);
  const transactions = [];
  try {
    for (const [index, plan] of plans.entries()) {
      beforeCommit(plan, index);
      transactions.push(commit(plan));
    }
  } catch (error) {
    const rollbackErrors = [];
    for (const transaction of transactions.reverse()) {
      try {
        rollbackCommittedInstall(transaction);
      } catch (rollbackError) {
        rollbackErrors.push(rollbackError);
      }
    }
    if (rollbackErrors.length > 0) {
      throw new AggregateError(
        [error, ...rollbackErrors],
        `install failed and ${rollbackErrors.length} earlier target(s) could not be restored; `
        + rollbackErrors.map((item) => item.message).join("; "),
      );
    }
    throw error;
  }

  for (const [index, transaction] of transactions.entries()) {
    finalizeCommittedInstall(transaction);
    const { destination, replaced } = plans[index];
    console.log(
      replaced
        ? `Updated ${SKILL_NAME} at ${destination}`
        : `Installed ${SKILL_NAME} to ${destination}`,
    );
  }
}

async function installSkill(options) {
  const targets = await resolveInstallTargets(options);
  installSkillTargets(targets);
}

async function main() {
  assertNodeVersion();
  const options = parseArguments(process.argv.slice(2));
  if (options.version) {
    printVersion();
    return;
  }
  if (options.help) {
    printHelp(options.command);
    return;
  }

  if (options.pythonArgs) {
    process.exitCode = await runPythonTool(options.command, options.pythonArgs);
    return;
  }

  if (options.command === "install") {
    await installSkill(options);
    return;
  }

  const { server, url } = await startEditorServer({ port: options.port });
  console.log(`Open Kimi PPT editor is running at ${url}`);
  console.log("Press Ctrl+C to stop the server.");
  if (options.open) openBrowser(url);

  const shutdown = () => server.close(() => process.exit(0));
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
}

function isMainModule() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return resolve(process.argv[1]) === fileURLToPath(import.meta.url);
  }
}

if (isMainModule()) {
  main().catch((error) => {
    console.error(`Error: ${error.message}`);
    process.exitCode = 1;
  });
}
