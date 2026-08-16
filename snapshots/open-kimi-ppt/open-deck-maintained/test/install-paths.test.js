import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { assertSafeInstallPaths } from "../lib/install-paths.js";

const SKILL_NAME = "open-kimi-ppt";

test("rejects installer staging inside the packaged copy source", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-install-paths-"));
  const source = join(root, "package", "skills", SKILL_NAME);
  try {
    mkdirSync(source, { recursive: true });
    assert.throws(
      () => assertSafeInstallPaths(source, join(source, "nested"), SKILL_NAME),
      /must not be inside the packaged skill source/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("rejects a destination that would replace the packaged source", () => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-install-paths-"));
  const skills = join(root, "package", "skills");
  const source = join(skills, SKILL_NAME);
  try {
    mkdirSync(source, { recursive: true });
    assert.throws(
      () => assertSafeInstallPaths(source, skills, SKILL_NAME),
      /overlaps the packaged skill source/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("resolves existing symlink ancestors and allows a separate target", (t) => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-install-paths-"));
  const source = join(root, "package", "skills", SKILL_NAME);
  const outside = join(root, "outside");
  const alias = join(root, "target-alias");
  try {
    mkdirSync(source, { recursive: true });
    mkdirSync(outside, { recursive: true });
    try {
      symlinkSync(outside, alias, process.platform === "win32" ? "junction" : "dir");
    } catch (error) {
      if (["EPERM", "EACCES", "ENOTSUP"].includes(error?.code)) {
        t.skip(`directory symlinks are unavailable: ${error.code}`);
        return;
      }
      throw error;
    }
    assert.doesNotThrow(
      () => assertSafeInstallPaths(source, alias, SKILL_NAME),
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("rejects a symlinked target that resolves inside the packaged source", (t) => {
  const root = mkdtempSync(join(tmpdir(), "open-kimi-ppt-install-paths-"));
  const source = join(root, "package", "skills", SKILL_NAME);
  const alias = join(root, "source-alias");
  try {
    mkdirSync(source, { recursive: true });
    try {
      symlinkSync(source, alias, process.platform === "win32" ? "junction" : "dir");
    } catch (error) {
      if (["EPERM", "EACCES", "ENOTSUP"].includes(error?.code)) {
        t.skip(`directory symlinks are unavailable: ${error.code}`);
        return;
      }
      throw error;
    }
    assert.throws(
      () => assertSafeInstallPaths(source, join(alias, "nested"), SKILL_NAME),
      /must not be inside the packaged skill source/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
