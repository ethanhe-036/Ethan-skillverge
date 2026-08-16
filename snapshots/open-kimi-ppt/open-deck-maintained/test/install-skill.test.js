import assert from "node:assert/strict";
import {
  existsSync,
  chmodSync,
  closeSync,
  fsyncSync,
  linkSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
  writeSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { installSkillTargets } from "../bin/open-kimi-ppt-skill.js";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const cli = join(projectRoot, "bin", "open-kimi-ppt-skill.js");

function runCli(args, env = process.env) {
  return spawnSync(process.execPath, [cli, ...args], {
    encoding: "utf8",
    env,
  });
}

test("installs the packaged skill into a custom skills directory", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const target = join(root, "skills");

  try {
    const result = runCli(["install", "--target", target]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(join(target, "open-kimi-ppt", "SKILL.md")), true);
    assert.equal(
      existsSync(join(target, "open-kimi-ppt", "agents", "openai.yaml")),
      true,
    );
    assert.equal(existsSync(join(target, "open-kimi-ppt", "scripts", "export_pptx.py")), true);
    assert.equal(existsSync(join(target, "open-kimi-ppt", "_user_meta.json")), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("installs into ~/.agents/skills when no target is provided (non-interactive)", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));

  try {
    const result = runCli([], { ...process.env, HOME: root, USERPROFILE: root, CODEX_HOME: undefined });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(join(root, ".agents", "skills", "open-kimi-ppt", "SKILL.md")), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("supports -y for non-interactive default install", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));

  try {
    const result = runCli(["install", "-y"], {
      ...process.env,
      HOME: root,
      USERPROFILE: root,
      CODEX_HOME: undefined,
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(join(root, ".agents", "skills", "open-kimi-ppt", "SKILL.md")), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("installs into multiple --target directories", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const first = join(root, "codex", "skills");
  const second = join(root, "claude", "skills");

  try {
    const result = runCli(["install", "--target", first, "--target", second]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(join(first, "open-kimi-ppt", "SKILL.md")), true);
    assert.equal(existsSync(join(second, "open-kimi-ppt", "SKILL.md")), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("preflights every target before mutating the first installation", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const first = join(root, "first", "skills");
  const firstSkill = join(first, "open-kimi-ppt");
  const firstSkillFile = join(firstSkill, "SKILL.md");
  const invalidSecond = join(
    projectRoot,
    "skills",
    "open-kimi-ppt",
    "nested-install-target",
  );

  try {
    mkdirSync(firstSkill, { recursive: true });
    writeFileSync(firstSkillFile, "working first installation", "utf8");

    const result = runCli([
      "install",
      "--target",
      first,
      "--target",
      invalidSecond,
    ]);
    assert.notEqual(result.status, 0, result.stderr);
    assert.match(result.stderr, /must not be inside the packaged skill source/i);
    assert.equal(readFileSync(firstSkillFile, "utf8"), "working first installation");
    assert.equal(existsSync(invalidSecond), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("rolls back earlier targets when a later commit fails", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const first = join(root, "first", "skills");
  const second = join(root, "second", "skills");
  const firstSkillFile = join(first, "open-kimi-ppt", "SKILL.md");

  try {
    mkdirSync(dirname(firstSkillFile), { recursive: true });
    writeFileSync(firstSkillFile, "original first installation", "utf8");

    assert.throws(
      () => installSkillTargets([first, second], {
        beforeCommit(_plan, index) {
          if (index === 1) throw new Error("injected second-target commit failure");
        },
      }),
      /injected second-target commit failure/,
    );
    assert.equal(readFileSync(firstSkillFile, "utf8"), "original first installation");
    assert.equal(existsSync(join(second, "open-kimi-ppt")), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("removes a newly published current target when its post-publish check fails", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const target = join(root, "skills");

  try {
    assert.throws(
      () => installSkillTargets([target], {
        afterPublish() {
          throw new Error("injected post-publish verification failure");
        },
      }),
      /injected post-publish verification failure/,
    );
    assert.equal(existsSync(join(target, "open-kimi-ppt")), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("restores the previous current target when a post-publish check fails", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const target = join(root, "skills");
  const skillFile = join(target, "open-kimi-ppt", "SKILL.md");

  try {
    mkdirSync(dirname(skillFile), { recursive: true });
    writeFileSync(skillFile, "previous installation", "utf8");
    assert.throws(
      () => installSkillTargets([target], {
        afterPublish() {
          throw new Error("injected post-publish verification failure");
        },
      }),
      /injected post-publish verification failure/,
    );
    assert.equal(readFileSync(skillFile, "utf8"), "previous installation");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("preserves and reports a concurrently changed current target after publication", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const target = join(root, "skills");
  const skillFile = join(target, "open-kimi-ppt", "SKILL.md");
  const changed = "external edit after publication";

  try {
    mkdirSync(dirname(skillFile), { recursive: true });
    writeFileSync(skillFile, "previous installation", "utf8");
    let reported;
    assert.throws(
      () => installSkillTargets([target], {
        afterPublish(transaction) {
          writeFileSync(join(transaction.destination, "SKILL.md"), changed, "utf8");
          throw new Error("injected post-publish verification failure");
        },
      }),
      (error) => {
        reported = error;
        return error instanceof AggregateError;
      },
    );
    assert.match(reported.message, /could not be restored safely/i);
    assert.match(reported.message, /previous installation is retained at/i);
    assert.equal(readFileSync(skillFile, "utf8"), changed);
    const transactionDirectory = readdirSync(target).find((name) => (
      name.startsWith(".open-kimi-ppt-")
    ));
    assert.ok(transactionDirectory, "previous installation recovery tree must be retained");
    assert.equal(
      readFileSync(
        join(target, transactionDirectory, "open-kimi-ppt.previous", "SKILL.md"),
        "utf8",
      ),
      "previous installation",
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("rejects an install when a preflighted ancestor is replaced by a symlink", (t) => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const parent = join(root, "install-parent");
  const displacedParent = join(root, "install-parent.before-swap");
  const outside = join(root, "outside");
  const target = join(parent, "skills");
  const outsideSkills = join(outside, "skills");
  const probe = join(root, "symlink-probe");

  try {
    mkdirSync(target, { recursive: true });
    mkdirSync(outsideSkills, { recursive: true });
    writeFileSync(join(outsideSkills, "sentinel.txt"), "outside must stay unchanged", "utf8");
    try {
      symlinkSync(outside, probe, process.platform === "win32" ? "junction" : "dir");
      rmSync(probe, { force: true });
    } catch (error) {
      if (["EPERM", "EACCES", "ENOTSUP"].includes(error?.code)) {
        t.skip(`directory symlinks are unavailable: ${error.code}`);
        return;
      }
      throw error;
    }

    assert.throws(
      () => installSkillTargets([target], {
        beforeCommit() {
          renameSync(parent, displacedParent);
          symlinkSync(outside, parent, process.platform === "win32" ? "junction" : "dir");
        },
      }),
      /install parent ancestry changed after preflight/i,
    );
    assert.equal(existsSync(join(outsideSkills, "open-kimi-ppt")), false);
    assert.deepEqual(readdirSync(outsideSkills), ["sentinel.txt"]);
    assert.equal(
      existsSync(join(displacedParent, "skills", "open-kimi-ppt")),
      false,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("preserves an externally modified committed tree when a later target fails", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const first = join(root, "first", "skills");
  const second = join(root, "second", "skills");
  const firstSkillFile = join(first, "open-kimi-ppt", "SKILL.md");
  const externalContent = "externally modified after the first commit";

  try {
    mkdirSync(dirname(firstSkillFile), { recursive: true });
    writeFileSync(firstSkillFile, "original first installation", "utf8");

    let error;
    assert.throws(
      () => installSkillTargets([first, second], {
        beforeCommit(_plan, index) {
          if (index !== 1) return;
          writeFileSync(firstSkillFile, externalContent, "utf8");
          throw new Error("injected second-target commit failure");
        },
      }),
      (caught) => {
        error = caught;
        return caught instanceof AggregateError;
      },
    );
    assert.match(error.message, /refusing rollback to avoid deleting external changes/i);
    assert.match(error.message, /previous installation is retained at/i);
    assert.equal(readFileSync(firstSkillFile, "utf8"), externalContent);

    const transactionDirectory = readdirSync(first).find((name) => (
      name.startsWith(".open-kimi-ppt-")
    ));
    assert.ok(transactionDirectory, "recovery transaction directory should be retained");
    assert.equal(
      readFileSync(
        join(first, transactionDirectory, "open-kimi-ppt.previous", "SKILL.md"),
        "utf8",
      ),
      "original first installation",
    );
    assert.equal(existsSync(join(second, "open-kimi-ppt")), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("deduplicates targets that resolve to the same skills directory", (t) => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const realTarget = join(root, "real-skills");
  const aliasTarget = join(root, "alias-skills");

  try {
    mkdirSync(realTarget, { recursive: true });
    try {
      symlinkSync(realTarget, aliasTarget, process.platform === "win32" ? "junction" : "dir");
    } catch (error) {
      if (["EPERM", "EACCES", "ENOTSUP"].includes(error?.code)) {
        t.skip(`directory symlinks are unavailable: ${error.code}`);
        return;
      }
      throw error;
    }

    const result = runCli([
      "install",
      "--target",
      realTarget,
      "--target",
      aliasTarget,
    ]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(join(realTarget, "open-kimi-ppt", "SKILL.md")), true);
    assert.equal(
      result.stdout.match(/Installed open-kimi-ppt/g)?.length,
      1,
      result.stdout,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("--all installs into detected agent directories and skips missing ones", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));

  try {
    mkdirSync(join(root, ".agents"), { recursive: true });
    mkdirSync(join(root, ".codex"), { recursive: true });
    mkdirSync(join(root, ".claude"), { recursive: true });

    const result = runCli(["install", "--all"], {
      ...process.env,
      HOME: root,
      USERPROFILE: root,
      CODEX_HOME: undefined,
    });
    assert.equal(result.status, 0, result.stderr);

    for (const relative of [[".agents", "skills"], [".codex", "skills"], [".claude", "skills"]]) {
      assert.equal(
        existsSync(join(root, ...relative, "open-kimi-ppt", "SKILL.md")),
        true,
        relative.join("/"),
      );
    }
    for (const missing of [".cursor", ".workbuddy"]) {
      assert.equal(existsSync(join(root, missing)), false, missing);
    }
    assert.match(result.stdout, /Skipped .*\.cursor/);
    assert.match(result.stdout, /Skipped .*\.workbuddy/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("--all fails clearly when no agent directory exists", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));

  try {
    const result = runCli(["install", "--all"], {
      ...process.env,
      HOME: root,
      USERPROFILE: root,
      CODEX_HOME: undefined,
    });
    assert.notEqual(result.status, 0, result.stderr);
    assert.equal(existsSync(join(root, ".agents")), false);
    assert.match(result.stderr, /No known agent directories found/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("overwrites an existing installation by default", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const target = join(root, "skills");
  const skillFile = join(target, "open-kimi-ppt", "SKILL.md");

  try {
    assert.equal(runCli(["--target", target]).status, 0);
    writeFileSync(skillFile, "modified", "utf8");

    const result = runCli(["--target", target]);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /Updated open-kimi-ppt/);
    assert.match(
      readFileSync(skillFile, "utf8").replaceAll("\r\n", "\n"),
      /^---\nname: open-kimi-ppt/m,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("preserves agent-owned metadata while updating skill files", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const target = join(root, "skills");
  const installed = join(target, "open-kimi-ppt");

  try {
    assert.equal(runCli(["--target", target]).status, 0);
    writeFileSync(join(installed, "_user_meta.json"), '{"enabled":true}\n', "utf8");
    writeFileSync(join(installed, "custom.txt"), "replace me", "utf8");

    const result = runCli(["--target", target]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(
      readFileSync(join(installed, "_user_meta.json"), "utf8"),
      '{"enabled":true}\n',
    );
    assert.equal(existsSync(join(installed, "custom.txt")), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("never discards a late write through an already-open metadata handle", {
  // Windows deliberately refuses the directory rename while this test keeps
  // a child file handle open. That is already a safe fail-closed result; the
  // late-write/inode behavior exercised below is specific to POSIX rename.
  skip: process.platform === "win32",
}, () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const target = join(root, "skills");
  const installed = join(target, "open-kimi-ppt");
  const metadataPath = join(installed, "_user_meta.json");
  const original = Buffer.from('{"value":"OLD"}\n');
  const changed = Buffer.from('{"value":"NEW"}\n');
  let descriptor = null;

  try {
    assert.equal(runCli(["--target", target]).status, 0);
    writeFileSync(metadataPath, original);
    descriptor = openSync(metadataPath, "r+");

    let reported;
    assert.throws(
      () => installSkillTargets([target], {
        afterPublish() {
          assert.equal(writeSync(descriptor, changed, 0, changed.length, 0), changed.length);
          fsyncSync(descriptor);
        },
      }),
      (error) => {
        reported = error;
        return error instanceof AggregateError;
      },
    );

    assert.match(reported.message, /installed tree changed after commit/i);
    assert.deepEqual(readFileSync(metadataPath), changed);
    const transactionDirectory = readdirSync(target).find((name) => (
      name.startsWith(".open-kimi-ppt-")
    ));
    assert.ok(transactionDirectory, "the previous tree must remain recoverable");
    assert.deepEqual(
      readFileSync(
        join(target, transactionDirectory, "open-kimi-ppt.previous", "_user_meta.json"),
      ),
      changed,
    );
  } finally {
    if (descriptor !== null) closeSync(descriptor);
    rmSync(root, { recursive: true, force: true });
  }
});

test("refuses to update through a symbolic-link skill destination", (t) => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const target = join(root, "skills");
  const installed = join(target, "open-kimi-ppt");
  const outside = join(root, "outside");
  const metadata = join(outside, "_user_meta.json");

  try {
    mkdirSync(target, { recursive: true });
    mkdirSync(outside, { recursive: true });
    writeFileSync(metadata, '{"outside":true}\n', "utf8");
    try {
      symlinkSync(outside, installed, process.platform === "win32" ? "junction" : "dir");
    } catch (error) {
      if (["EPERM", "EACCES", "ENOTSUP"].includes(error?.code)) {
        t.skip(`directory symlinks are unavailable: ${error.code}`);
        return;
      }
      throw error;
    }

    const result = runCli(["--target", target]);
    assert.notEqual(result.status, 0, result.stderr);
    assert.match(result.stderr, /real directory.*symbolic link/i);
    assert.equal(lstatSync(installed).isSymbolicLink(), true);
    assert.equal(readFileSync(metadata, "utf8"), '{"outside":true}\n');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("does not follow agent metadata symbolic links during an update", (t) => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const target = join(root, "skills");
  const installed = join(target, "open-kimi-ppt");
  const outsideMetadata = join(root, "outside-meta.json");
  const installedMetadata = join(installed, "_user_meta.json");

  try {
    assert.equal(runCli(["--target", target]).status, 0);
    writeFileSync(outsideMetadata, '{"secret":"outside"}\n', "utf8");
    try {
      symlinkSync(outsideMetadata, installedMetadata, "file");
    } catch (error) {
      if (["EPERM", "EACCES", "ENOTSUP"].includes(error?.code)) {
        t.skip(`file symlinks are unavailable: ${error.code}`);
        return;
      }
      throw error;
    }

    const result = runCli(["--target", target]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(installedMetadata), false);
    assert.equal(readFileSync(outsideMetadata, "utf8"), '{"secret":"outside"}\n');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("does not preserve agent metadata through a hard link", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const target = join(root, "skills");
  const installed = join(target, "open-kimi-ppt");
  const outsideMetadata = join(root, "outside-meta.json");
  const installedMetadata = join(installed, "_user_meta.json");

  try {
    assert.equal(runCli(["--target", target]).status, 0);
    writeFileSync(outsideMetadata, '{"secret":"hard-linked"}\n', "utf8");
    linkSync(outsideMetadata, installedMetadata);

    const result = runCli(["--target", target]);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(existsSync(installedMetadata), false);
    assert.equal(readFileSync(outsideMetadata, "utf8"), '{"secret":"hard-linked"}\n');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("rejects oversized agent metadata without replacing the working installation", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const target = join(root, "skills");
  const installed = join(target, "open-kimi-ppt");
  const skillFile = join(installed, "SKILL.md");
  const metadata = join(installed, "_user_meta.json");

  try {
    assert.equal(runCli(["--target", target]).status, 0);
    writeFileSync(skillFile, "working local installation", "utf8");
    writeFileSync(metadata, Buffer.alloc(64 * 1024 + 1, 0x61));

    const result = runCli(["--target", target]);
    assert.notEqual(result.status, 0, result.stderr);
    assert.match(result.stderr, /metadata exceeds.*safety limit/i);
    assert.equal(readFileSync(skillFile, "utf8"), "working local installation");
    assert.equal(readFileSync(metadata).length, 64 * 1024 + 1);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("accepts legacy --force without changing overwrite behavior", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-test-"));
  const target = join(root, "skills");

  try {
    assert.equal(runCli(["--target", target]).status, 0);
    const result = runCli(["--target", target, "--force"]);
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /Updated open-kimi-ppt/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("help documents interactive install and -y for agents", () => {
  const result = runCli(["install", "--help"]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /space select/);
  assert.match(result.stdout, /-y, --yes/);
  assert.match(result.stdout, /--all/);
  assert.match(result.stdout, /npx open-deck-skill@latest install -y/);
  assert.match(result.stdout, /open-deck-skill install -y/);
  assert.match(result.stdout, /node bin\/open-kimi-ppt-skill\.js install -y/);
});

test("help documents the P0-P2 tools and Python override", () => {
  const result = runCli(["--help"]);
  assert.equal(result.status, 0, result.stderr);
  for (const command of [
    "lint",
    "inspect-pptx",
    "validate-pptx",
    "fill-pptx",
    "export-local",
    "audit-assets",
    "audit-prompts",
  ]) {
    assert.match(result.stdout, new RegExp(`^  ${command}\\s`, "m"));
  }
  assert.match(result.stdout, /OPEN_DECK_PYTHON/);
  assert.match(result.stdout, /<deck-command> --help/);
});

test("deck commands use the expected Python scripts and preserve literal arguments", {
  skip: process.platform === "win32" ? "test helper uses a POSIX executable script" : false,
}, () => {
  const root = mkdtempSync(join(tmpdir(), "open-deck-python-router-test-"));
  const fakePython = join(root, "fake-python");
  const marker = join(root, "must-not-exist");
  const literalArgument = `;touch ${marker}`;
  const routes = {
    lint: ["pptd_quality.py"],
    "inspect-pptx": ["pptx_native.py", "inspect"],
    "validate-pptx": ["pptx_native.py", "validate"],
    "fill-pptx": ["pptx_native.py", "fill"],
    "export-local": ["export_pptx_local.py"],
    "audit-assets": ["asset_pack_audit.py"],
    "audit-prompts": ["prompt_audit.py"],
  };

  try {
    writeFileSync(
      fakePython,
      "#!/usr/bin/env node\nconsole.log(JSON.stringify(process.argv.slice(2)));\n",
    );
    chmodSync(fakePython, 0o755);

    for (const [command, expectedPrefix] of Object.entries(routes)) {
      const result = runCli([command, "--literal", literalArgument], {
        ...process.env,
        OPEN_DECK_PYTHON: fakePython,
      });
      assert.equal(result.status, 0, `${command}: ${result.stderr}`);
      const forwarded = JSON.parse(result.stdout);
      const [scriptName, ...toolPrefix] = expectedPrefix;
      assert.equal(
        forwarded[0],
        join(projectRoot, "skills", "open-kimi-ppt", "scripts", scriptName),
      );
      assert.deepEqual(forwarded.slice(1), [
        ...toolPrefix,
        "--literal",
        literalArgument,
      ]);
    }
    assert.equal(existsSync(marker), false, "literal shell metacharacters must not execute");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("deck commands preserve the Python tool exit status", {
  skip: process.platform === "win32" ? "test helper uses a POSIX executable script" : false,
}, () => {
  const root = mkdtempSync(join(tmpdir(), "open-deck-python-exit-test-"));
  const fakePython = join(root, "fake-python");

  try {
    writeFileSync(fakePython, "#!/usr/bin/env node\nprocess.exit(23);\n");
    chmodSync(fakePython, 0o755);
    const result = runCli(["lint", "example"], {
      ...process.env,
      OPEN_DECK_PYTHON: fakePython,
    });
    assert.equal(result.status, 23, result.stderr);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("deck commands report a missing configured Python executable clearly", () => {
  const root = mkdtempSync(join(tmpdir(), "open-deck-no-python-test-"));
  const missingPython = join(root, "missing-python");

  try {
    const result = runCli(["lint", "--help"], {
      ...process.env,
      OPEN_DECK_PYTHON: missingPython,
    });
    assert.equal(result.status, 1, result.stderr);
    assert.match(result.stderr, /Python 3 is required to run lint/);
    assert.match(result.stderr, /OPEN_DECK_PYTHON points to an executable that could not be found/);
    assert.match(result.stderr, /set OPEN_DECK_PYTHON/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("-h shows help", () => {
  const result = runCli(["-h"]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Usage:/);
  assert.match(result.stdout, /-h, --help/);
  assert.match(result.stdout, /-V, --version/);
});

test("--version and -V print the package version", () => {
  const { version } = JSON.parse(readFileSync(join(projectRoot, "package.json"), "utf8"));
  for (const args of [["--version"], ["-V"], ["serve", "--version"]]) {
    const result = runCli(args);
    assert.equal(result.status, 0, `${args.join(" ")}: ${result.stderr}`);
    assert.equal(result.stdout.trim(), version);
  }
});

test("runs through an npm-style executable symlink", {
  skip: process.platform === "win32" ? "npm uses command shims rather than symlinks on Windows" : false,
}, () => {
  const root = mkdtempSync(join(tmpdir(), "open-deck-skill-bin-test-"));
  const executable = join(root, "open-deck-skill");
  const { version } = JSON.parse(readFileSync(join(projectRoot, "package.json"), "utf8"));

  try {
    symlinkSync(cli, executable);
    const result = spawnSync(executable, ["--version"], { encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), version);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
