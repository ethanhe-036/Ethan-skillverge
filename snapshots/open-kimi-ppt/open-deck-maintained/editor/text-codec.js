const UTF8_DECODER = new TextDecoder("utf-8", { fatal: true });

export function decodeUtf8(bytes, path = "文件") {
  let view;
  if (bytes instanceof ArrayBuffer) view = new Uint8Array(bytes);
  else if (ArrayBuffer.isView(bytes)) {
    view = new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  } else {
    throw new TypeError("UTF-8 解码输入必须是 ArrayBuffer 或 TypedArray");
  }
  try {
    return UTF8_DECODER.decode(view);
  } catch (error) {
    const controlled = new Error(`文件不是有效 UTF-8，已拒绝有损载入：${path}`, { cause: error });
    controlled.code = "INVALID_UTF8";
    controlled.path = path;
    throw controlled;
  }
}

export async function readFileUtf8(file, path, { maxBytes }) {
  if (!file || typeof file.arrayBuffer !== "function") {
    throw new TypeError(`无法读取文件：${path}`);
  }
  if (file.size > maxBytes) throw new Error(`文件超过 20 MiB：${path}`);
  const bytes = await file.arrayBuffer();
  if (bytes.byteLength > maxBytes) throw new Error(`文件超过 20 MiB：${path}`);
  return Object.freeze({
    text: decodeUtf8(bytes, path),
    byteLength: bytes.byteLength,
  });
}
