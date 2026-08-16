import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const skillRoot = join(projectRoot, "skills", "open-kimi-ppt");

function filesNamed(root, filename) {
  const result = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) result.push(...filesNamed(path, filename));
    else if (entry.isFile() && entry.name === filename) result.push(path);
  }
  return result;
}

test("Codex metadata presents the skill with explicit invocation, branding, and a usable prompt", () => {
  const metadata = readFileSync(join(skillRoot, "agents", "openai.yaml"), "utf8");
  assert.match(metadata, /display_name: "Open Kimi PPT"/);
  assert.match(metadata, /short_description: ".{25,64}"/);
  assert.match(metadata, /default_prompt: "Use \$open-kimi-ppt\b/);
  assert.match(metadata, /allow_implicit_invocation: false/);

  for (const asset of ["assets/icon.svg", "assets/logo.svg"]) {
    assert.match(metadata, new RegExp(asset.replace("/", "\\/")));
    const path = join(skillRoot, asset);
    assert.equal(existsSync(path), true, `${asset} must ship with the skill`);
    assert.ok(statSync(path).size > 100, `${asset} must not be an empty placeholder`);
  }
});

test("the skill contract keeps dual delivery and the PPTX import boundary explicit", () => {
  const skill = readFileSync(join(skillRoot, "SKILL.md"), "utf8");
  assert.match(skill, /default deliverables are BOTH/i);
  assert.match(skill, /complete editable PPTD project directory/i);
  assert.match(skill, /matching locally generated `?\.pptx`?/i);
  assert.match(skill.replaceAll("**", ""), /does not bundle a PPTX-to-PPTD importer/i);
  assert.match(skill, /replication, not conversion/i);
  assert.match(skill, /scripts\/export_images\.py/);
  assert.match(skill, /scripts\/export_pptx\.py/);
});

test("all thirty advertised primary design systems and the animation sample are present", () => {
  const designRoot = join(skillRoot, "reference", "design_system");
  const primaryDesigns = filesNamed(designRoot, "design.md");
  assert.equal(primaryDesigns.length, 30);

  const themeCatalog = readFileSync(join(projectRoot, "theme.md"), "utf8");
  for (const path of primaryDesigns) {
    const relative = path.slice(designRoot.length + 1).replaceAll("\\", "/");
    assert.match(themeCatalog, new RegExp(relative.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }

  const animationRoot = join(projectRoot, "example", "xiaomi-yu7-ppt-animation");
  const pages = readdirSync(join(animationRoot, "pages"))
    .filter((name) => name.endsWith(".page"))
    .sort();
  assert.equal(pages.length, 8);
  for (const page of pages) {
    assert.match(
      readFileSync(join(animationRoot, "pages", page), "utf8"),
      /^animations:/m,
      `${page} must exercise the documented element-animation contract`,
    );
  }
  assert.ok(statSync(join(animationRoot, "xiaomi-yu7.pptx")).size > 1_000_000);
});

test("the npm artifact includes every Codex runtime surface", () => {
  const packageJson = JSON.parse(readFileSync(join(projectRoot, "package.json"), "utf8"));
  for (const entry of [
    "skills/open-kimi-ppt/SKILL.md",
    "skills/open-kimi-ppt/agents/",
    "skills/open-kimi-ppt/assets/",
    "skills/open-kimi-ppt/reference/",
    "editor/",
    "bin/",
    "urgent.md",
  ]) {
    assert.equal(packageJson.files.includes(entry), true, `${entry} must be published`);
  }
  for (const runtime of [
    "asset_pack_audit.py",
    "deck_ir.py",
    "export_images.py",
    "export_pptx.py",
    "export_pptx_local.py",
    "pptd_quality.py",
    "pptx_native.py",
  ]) {
    const packagedPath = `skills/open-kimi-ppt/scripts/${runtime}`;
    assert.equal(
      packageJson.files.includes(packagedPath),
      true,
      `${packagedPath} must be explicitly published`,
    );
    assert.equal(
      existsSync(join(skillRoot, "scripts", runtime)),
      true,
      `${runtime} must ship with the skill`,
    );
  }
  for (const runtime of [
    "export_host.html",
    "penpal.mjs",
    "penpal.LICENSE.txt",
    "prompt_audit.py",
    "prompt_audit_manifest.json",
  ]) {
    assert.equal(
      packageJson.files.includes(`skills/open-kimi-ppt/scripts/${runtime}`),
      true,
      `${runtime} must be explicitly published`,
    );
  }
  assert.equal(
    packageJson.files.includes("skills/open-kimi-ppt/scripts/"),
    false,
    "the whole scripts directory would publish local bytecode caches",
  );
  assert.equal(packageJson.name, "open-deck-skill");
  assert.equal(packageJson.version, "1.4.1");
  assert.deepEqual(packageJson.bin, {
    "open-deck-skill": "bin/open-kimi-ppt-skill.js",
  });
  assert.equal(
    packageJson.repository?.url,
    "git+https://github.com/FeidaWang/Open-Deck-Skill.git",
  );
  assert.equal(packageJson.homepage, "https://github.com/FeidaWang/Open-Deck-Skill#readme");
  assert.equal(packageJson.bugs?.url, "https://github.com/FeidaWang/Open-Deck-Skill/issues");
});

test("README keeps public acquisition, retired-package migration, and host boundaries explicit", () => {
  const chinese = readFileSync(join(projectRoot, "README.md"), "utf8");
  const english = readFileSync(join(projectRoot, "README_EN.md"), "utf8");

  for (const readme of [chinese, english]) {
    assert.match(readme, /\$open-kimi-ppt/);
    assert.match(readme, /1\.4\.1/);
    assert.match(readme, /npm view open-deck-skill version/);
    assert.match(readme, /npx open-deck-skill@latest install/);
    assert.match(readme, /node bin\/open-kimi-ppt-skill\.js install -y/);
    assert.match(readme, /open-kimi-ppt-skill/);
    assert.match(readme, /HTTP 401/);
    assert.match(readme, /\$open-kimi-ppt/);
    assert.doesNotMatch(readme, /source-GitHub/);
    assert.doesNotMatch(readme, /binaryify\/open-kimi-ppt-skill/i);
    assert.doesNotMatch(readme, /release[_ ]candidate/i);
  }

  assert.match(chinese, /通过公共 npm 包安装（推荐）/);
  assert.match(chinese, /旧包 `open-kimi-ppt-skill` 已于 2026-08-07 从 npm 撤下/);
  assert.match(chinese, /其他 Agent 取决于其 Skill\/工具支持/);
  assert.match(chinese, /脚本本身不是审美模型/);
  assert.match(english, /Install from the public npm package \(recommended\)/);
  assert.match(english, /old `open-kimi-ppt-skill` package was unpublished from npm on 2026-08-07/);
  assert.match(english, /other hosts depend on their Skill\/tool support/);
  assert.match(english, /The script is not an aesthetic model/);
});
