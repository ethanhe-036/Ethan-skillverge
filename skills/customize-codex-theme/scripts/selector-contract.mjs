import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

export function validateSelectorContract(contract) {
  if (contract?.schemaVersion !== 1 ||
      contract.contractId !== "codex-theme-runtime-surfaces" ||
      !contract.parts || typeof contract.parts !== "object" || Array.isArray(contract.parts) ||
      !contract.scopes || typeof contract.scopes !== "object" || Array.isArray(contract.scopes)) {
    throw new Error("Selector contract has an unsupported schema");
  }
  return contract;
}

export function compatibilitySelectorContractHash(contract) {
  validateSelectorContract(contract);
  const topology = {
    schemaVersion: contract.schemaVersion,
    contractId: contract.contractId,
    scopes: contract.scopes,
    parts: contract.parts,
  };
  return crypto.createHash("sha256").update(JSON.stringify(topology)).digest("hex");
}

export async function loadSelectorContract(root) {
  const file = path.join(root, "assets", "selectors.json");
  const text = await fs.readFile(file, "utf8");
  const contract = validateSelectorContract(JSON.parse(text));
  return {
    file,
    text,
    contract,
    payloadHash: crypto.createHash("sha256").update(text).digest("hex"),
    compatibilityHash: compatibilitySelectorContractHash(contract),
  };
}
