import { webcrypto } from "node:crypto";

// Node 18 does not expose Web Crypto on globalThis by default on every
// supported platform. The editor itself runs in Chromium, where crypto is a
// required browser capability; install Node's equivalent only for tests.
if (!globalThis.crypto) {
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: webcrypto,
  });
}
