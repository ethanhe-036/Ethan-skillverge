import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const candidates = [
  process.env.OPEN_KIMI_PYTHON,
  process.platform === "win32" ? "python" : "python3",
  "python",
].filter((value, index, values) => value && values.indexOf(value) === index);

for (const executable of candidates) {
  const probe = spawnSync(executable, ["--version"], { encoding: "utf8" });
  if (probe.error?.code === "ENOENT" || probe.status !== 0) continue;

  const result = spawnSync(
    executable,
    ["-Wd", "-m", "unittest", "discover", "-s", "skills/open-kimi-ppt/tests"],
    { cwd: packageRoot, stdio: "inherit" },
  );
  process.exit(result.status ?? 1);
}

console.error(
  "Python 3 is required to run exporter tests. Set OPEN_KIMI_PYTHON to its executable if it is not on PATH.",
);
process.exit(1);
