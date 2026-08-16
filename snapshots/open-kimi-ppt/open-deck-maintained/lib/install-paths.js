import { realpathSync } from "node:fs";
import { basename, dirname, join, resolve, sep } from "node:path";

function isMissing(error) {
  return error?.code === "ENOENT" || error?.code === "ENOTDIR";
}

/** Resolve symlinked existing ancestors without requiring the final path to exist. */
export function resolveProspectivePath(path) {
  const absolute = resolve(path);
  const suffix = [];
  let cursor = absolute;
  while (true) {
    try {
      return resolve(realpathSync(cursor), ...suffix.reverse());
    } catch (error) {
      if (!isMissing(error)) throw error;
      const parent = dirname(cursor);
      if (parent === cursor) throw error;
      suffix.push(basename(cursor));
      cursor = parent;
    }
  }
}

function isSameOrAncestor(candidate, target) {
  return candidate === target || target.startsWith(`${candidate}${sep}`);
}

/**
 * Keep installer staging outside its copy source and prevent self-replacement.
 * Both checks use prospective real paths so a symlinked existing ancestor
 * cannot disguise an overlap that is lexically absent.
 */
export function assertSafeInstallPaths(sourceDirectory, skillsDirectory, skillName) {
  const source = realpathSync(resolve(sourceDirectory));
  const skills = resolveProspectivePath(skillsDirectory);
  const destination = resolveProspectivePath(join(skillsDirectory, skillName));
  if (isSameOrAncestor(source, skills)) {
    throw new Error(
      `skills directory must not be inside the packaged skill source: ${skillsDirectory}`,
    );
  }
  if (
    isSameOrAncestor(source, destination)
    || isSameOrAncestor(destination, source)
  ) {
    throw new Error(
      `skill destination overlaps the packaged skill source: ${destination}`,
    );
  }
}
