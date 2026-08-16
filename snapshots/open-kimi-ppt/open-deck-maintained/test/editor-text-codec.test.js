import assert from "node:assert/strict";
import test from "node:test";
import { decodeUtf8, readFileUtf8 } from "../editor/text-codec.js";

test("decodes valid UTF-8 without changing its byte length", async () => {
  const bytes = new TextEncoder().encode("你好, Kimi");
  const result = await readFileUtf8({
    size: bytes.byteLength,
    arrayBuffer: async () => bytes.buffer.slice(0),
  }, "pages/01.page", { maxBytes: 1024 });
  assert.equal(result.text, "你好, Kimi");
  assert.equal(result.byteLength, bytes.byteLength);
});

test("rejects malformed UTF-8 instead of inserting replacement characters", async () => {
  const bytes = Uint8Array.from([0x7b, 0x22, 0x78, 0x22, 0x3a, 0x22, 0xc3, 0x28, 0x22, 0x7d]);
  assert.throws(
    () => decodeUtf8(bytes, "pages/bad.page"),
    (error) => error.code === "INVALID_UTF8"
      && error.path === "pages/bad.page"
      && !error.message.includes("�"),
  );
});

test("checks the source and returned byte sizes before decoding", async () => {
  let read = false;
  await assert.rejects(
    readFileUtf8({
      size: 11,
      arrayBuffer: async () => {
        read = true;
        return new ArrayBuffer(11);
      },
    }, "large.page", { maxBytes: 10 }),
    /超过 20 MiB/,
  );
  assert.equal(read, false);

  await assert.rejects(
    readFileUtf8({
      size: 1,
      arrayBuffer: async () => new ArrayBuffer(11),
    }, "changed.page", { maxBytes: 10 }),
    /超过 20 MiB/,
  );
});
