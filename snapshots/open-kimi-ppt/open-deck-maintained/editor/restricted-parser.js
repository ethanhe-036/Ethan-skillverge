const DEFAULT_MAX_SOURCE_BYTES = 20 * 1024 * 1024;
const DEFAULT_MAX_JSON_NODES = 100_000;
const DEFAULT_MAX_JSON_DEPTH = 128;
const DEFAULT_MAX_JSON_STRING_CHARS = DEFAULT_MAX_SOURCE_BYTES;
const DEFAULT_MAX_JSON_TOTAL_STRING_CHARS = DEFAULT_MAX_SOURCE_BYTES;
const DEFAULT_MAX_YAML_LINES = 100_000;
const MAX_SAFE_SCANNER_DEPTH = 512;

export class JsonPreflightSyntaxError extends SyntaxError {}
export class JsonResourceLimitError extends Error {}
export class JsonDuplicateKeyError extends Error {}

function assertPositiveBudget(value, name, { allowZero = false } = {}) {
  if (
    !Number.isSafeInteger(value)
    || (allowZero ? value < 0 : value < 1)
  ) {
    throw new TypeError(`${name} 必须是${allowZero ? "非负" : "正"}安全整数`);
  }
}

function utf8LengthUpTo(value, limit) {
  let bytes = 0;
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code <= 0x7f) bytes += 1;
    else if (code <= 0x7ff) bytes += 2;
    else if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        bytes += 4;
        index += 1;
      } else {
        bytes += 3;
      }
    } else {
      bytes += 3;
    }
    if (bytes > limit) return bytes;
  }
  return bytes;
}

/**
 * Validate a JSON document and its resource budgets before JSON.parse creates
 * an object graph. The scanner deliberately performs its own grammar walk so
 * duplicate keys and over-budget inputs fail before materialisation.
 */
export function assertJsonResourceLimits(
  source,
  {
    label = "JSON",
    maxBytes = DEFAULT_MAX_SOURCE_BYTES,
    maxNodes = DEFAULT_MAX_JSON_NODES,
    maxDepth = DEFAULT_MAX_JSON_DEPTH,
    maxStringChars = DEFAULT_MAX_JSON_STRING_CHARS,
    maxTotalStringChars = DEFAULT_MAX_JSON_TOTAL_STRING_CHARS,
  } = {},
) {
  if (typeof source !== "string") throw new TypeError(`${label} 内容必须是字符串`);
  assertPositiveBudget(maxBytes, "maxBytes");
  assertPositiveBudget(maxNodes, "maxNodes");
  assertPositiveBudget(maxDepth, "maxDepth");
  assertPositiveBudget(maxStringChars, "maxStringChars", { allowZero: true });
  assertPositiveBudget(maxTotalStringChars, "maxTotalStringChars", { allowZero: true });
  if (maxDepth > MAX_SAFE_SCANNER_DEPTH) {
    throw new TypeError(`maxDepth 不能超过 ${MAX_SAFE_SCANNER_DEPTH}`);
  }

  const byteLength = utf8LengthUpTo(source, maxBytes);
  if (byteLength > maxBytes) {
    throw new JsonResourceLimitError(`${label} 源字节超过 ${maxBytes} 安全上限`);
  }

  let index = 0;
  let nodes = 0;
  let totalStringChars = 0;

  const syntax = (message) => {
    throw new JsonPreflightSyntaxError(`${label} 语法错误（字符 ${index}）：${message}`);
  };
  const skipWhitespace = () => {
    while (/\s/.test(source[index] ?? "") && source.charCodeAt(index) <= 0x20) index += 1;
  };
  const addNode = () => {
    nodes += 1;
    if (nodes > maxNodes) {
      throw new JsonResourceLimitError(`${label} 节点数超过 ${maxNodes} 安全上限`);
    }
  };
  const parseString = (capture) => {
    if (source[index] !== '"') syntax("字符串必须以双引号开始");
    index += 1;
    let length = 0;
    let value = "";
    while (index < source.length) {
      const character = source[index];
      if (character === '"') {
        index += 1;
        totalStringChars += length;
        return capture ? value : null;
      }
      if (source.charCodeAt(index) < 0x20) syntax("字符串包含未转义控制字符");
      if (character !== "\\") {
        if (capture) value += character;
        length += 1;
        if (length > maxStringChars) {
          throw new JsonResourceLimitError(
            `${label} 单字符串长度超过 ${maxStringChars} 安全上限`,
          );
        }
        if (totalStringChars + length > maxTotalStringChars) {
          throw new JsonResourceLimitError(
            `${label} 字符串总长度超过 ${maxTotalStringChars} 安全上限`,
          );
        }
        index += 1;
        continue;
      }

      index += 1;
      const escape = source[index];
      if (!escape) syntax("字符串转义不完整");
      const simpleEscapes = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        b: "\b",
        f: "\f",
        n: "\n",
        r: "\r",
        t: "\t",
      };
      if (Object.hasOwn(simpleEscapes, escape)) {
        if (capture) value += simpleEscapes[escape];
        length += 1;
        if (length > maxStringChars) {
          throw new JsonResourceLimitError(
            `${label} 单字符串长度超过 ${maxStringChars} 安全上限`,
          );
        }
        if (totalStringChars + length > maxTotalStringChars) {
          throw new JsonResourceLimitError(
            `${label} 字符串总长度超过 ${maxTotalStringChars} 安全上限`,
          );
        }
        index += 1;
        continue;
      }
      if (escape !== "u") syntax(`不支持的转义 \\${escape}`);
      const hex = source.slice(index + 1, index + 5);
      if (!/^[0-9A-Fa-f]{4}$/.test(hex)) syntax("Unicode 转义必须包含四位十六进制数");
      if (capture) value += String.fromCharCode(Number.parseInt(hex, 16));
      length += 1;
      if (length > maxStringChars) {
        throw new JsonResourceLimitError(
          `${label} 单字符串长度超过 ${maxStringChars} 安全上限`,
        );
      }
      if (totalStringChars + length > maxTotalStringChars) {
        throw new JsonResourceLimitError(
          `${label} 字符串总长度超过 ${maxTotalStringChars} 安全上限`,
        );
      }
      index += 5;
    }
    syntax("字符串没有结束引号");
  };

  const parseNumber = () => {
    const rest = source.slice(index);
    const match = rest.match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/);
    if (!match) syntax("数字格式无效");
    index += match[0].length;
  };

  const assertContainerDepth = (depth) => {
    if (depth >= maxDepth) {
      throw new JsonResourceLimitError(`${label} 嵌套深度超过 ${maxDepth} 安全上限`);
    }
  };

  const parseValue = (depth) => {
    skipWhitespace();
    addNode();
    const character = source[index];
    if (character === '"') {
      parseString(false);
      return;
    }
    if (character === "{") {
      assertContainerDepth(depth);
      index += 1;
      skipWhitespace();
      const keys = new Set();
      if (source[index] === "}") {
        index += 1;
        return;
      }
      while (true) {
        skipWhitespace();
        if (source[index] !== '"') syntax("对象键必须是字符串");
        const key = parseString(true);
        if (keys.has(key)) {
          throw new JsonDuplicateKeyError(`${label} 包含重复键：${key}`);
        }
        keys.add(key);
        skipWhitespace();
        if (source[index] !== ":") syntax("对象键后缺少冒号");
        index += 1;
        parseValue(depth + 1);
        skipWhitespace();
        if (source[index] === "}") {
          index += 1;
          return;
        }
        if (source[index] !== ",") syntax("对象成员之间缺少逗号");
        index += 1;
      }
    }
    if (character === "[") {
      assertContainerDepth(depth);
      index += 1;
      skipWhitespace();
      if (source[index] === "]") {
        index += 1;
        return;
      }
      while (true) {
        parseValue(depth + 1);
        skipWhitespace();
        if (source[index] === "]") {
          index += 1;
          return;
        }
        if (source[index] !== ",") syntax("数组成员之间缺少逗号");
        index += 1;
      }
    }
    if (character === "-" || /[0-9]/.test(character ?? "")) {
      parseNumber();
      return;
    }
    for (const literal of ["true", "false", "null"]) {
      if (source.startsWith(literal, index)) {
        index += literal.length;
        return;
      }
    }
    syntax("无法识别的值");
  };

  skipWhitespace();
  if (index >= source.length) syntax("文档为空");
  parseValue(0);
  skipWhitespace();
  if (index !== source.length) syntax("根值之后存在额外内容");
  return { byteLength, nodes, totalStringChars };
}

function decommentYamlLine(line, lineNumber) {
  let quote = null;
  let result = "";
  let masked = "";
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    if (quote === '"') {
      result += character;
      masked += " ";
      if (character === "\\") {
        if (index + 1 >= line.length) throw new Error(`YAML 第 ${lineNumber} 行转义不完整`);
        index += 1;
        result += line[index];
        masked += " ";
      } else if (character === '"') {
        quote = null;
      }
      continue;
    }
    if (quote === "'") {
      result += character;
      masked += " ";
      if (character === "'" && line[index + 1] === "'") {
        index += 1;
        result += "'";
        masked += " ";
      } else if (character === "'") {
        quote = null;
      }
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      result += character;
      masked += " ";
      continue;
    }
    if (character === "#" && (index === 0 || /\s/.test(line[index - 1]))) break;
    result += character;
    masked += character;
  }
  if (quote) throw new Error(`YAML 第 ${lineNumber} 行字符串没有结束引号`);
  return { content: result.trimEnd(), masked: masked.trimEnd() };
}

function findYamlMappingColon(value) {
  let quote = null;
  let flowDepth = 0;
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if (quote === '"') {
      if (character === "\\") index += 1;
      else if (character === '"') quote = null;
      continue;
    }
    if (quote === "'") {
      if (character === "'" && value[index + 1] === "'") index += 1;
      else if (character === "'") quote = null;
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === "[" || character === "{") flowDepth += 1;
    else if (character === "]" || character === "}") flowDepth -= 1;
    else if (character === ":" && flowDepth === 0) return index;
    if (flowDepth < 0) return -1;
  }
  return -1;
}

function parseYamlStringScalar(source, lineNumber) {
  const value = source.trim();
  if (!value) throw new Error(`YAML 第 ${lineNumber} 行缺少字符串值`);
  if (value.startsWith('"')) {
    try {
      const parsed = JSON.parse(value);
      if (typeof parsed !== "string") throw new Error();
      return parsed;
    } catch {
      throw new Error(`YAML 第 ${lineNumber} 行双引号字符串无效`);
    }
  }
  if (value.startsWith("'")) {
    let parsed = "";
    for (let index = 1; index < value.length; index += 1) {
      if (value[index] !== "'") {
        parsed += value[index];
        continue;
      }
      if (value[index + 1] === "'") {
        parsed += "'";
        index += 1;
        continue;
      }
      if (index !== value.length - 1) {
        throw new Error(`YAML 第 ${lineNumber} 行单引号字符串后存在额外内容`);
      }
      return parsed;
    }
    throw new Error(`YAML 第 ${lineNumber} 行单引号字符串没有结束引号`);
  }
  if (
    /^[\[\]{}|>@`]/.test(value)
    || /:\s/.test(value)
    || /^(?:~|null|true|false|yes|no|on|off|[-+]?(?:\.inf|\.nan|(?:\d[\d_]*)(?:\.\d[\d_]*)?(?:e[-+]?\d+)?))$/i.test(value)
  ) {
    throw new Error(`YAML 第 ${lineNumber} 行的 pages 成员必须是字符串路径`);
  }
  return value;
}

function splitYamlFlowSequence(source, lineNumber, maxItems) {
  const value = source.trim();
  if (!value.startsWith("[") || !value.endsWith("]")) {
    throw new Error(`YAML 第 ${lineNumber} 行的 pages 必须是字符串路径数组`);
  }
  const inner = value.slice(1, -1).trim();
  if (!inner) return [];
  const items = [];
  let quote = null;
  let start = 0;
  for (let index = 0; index < inner.length; index += 1) {
    const character = inner[index];
    if (quote === '"') {
      if (character === "\\") index += 1;
      else if (character === '"') quote = null;
      continue;
    }
    if (quote === "'") {
      if (character === "'" && inner[index + 1] === "'") index += 1;
      else if (character === "'") quote = null;
      continue;
    }
    if (character === '"' || character === "'") quote = character;
    else if ("[]{}".includes(character)) {
      throw new Error(`YAML 第 ${lineNumber} 行的 pages 不支持嵌套 flow 结构`);
    } else if (character === ",") {
      if (items.length >= maxItems) {
        throw new Error(`文稿页数超过 ${maxItems} 页安全上限`);
      }
      items.push(parseYamlStringScalar(inner.slice(start, index), lineNumber));
      start = index + 1;
    }
  }
  if (quote) throw new Error(`YAML 第 ${lineNumber} 行字符串没有结束引号`);
  if (items.length >= maxItems) {
    throw new Error(`文稿页数超过 ${maxItems} 页安全上限`);
  }
  items.push(parseYamlStringScalar(inner.slice(start), lineNumber));
  return items;
}

function scanSafeYamlFlowValue(
  value,
  lineNumber,
  {
    baseDepth,
    maxDepth,
    countNode,
  },
) {
  let index = 0;

  const skipWhitespace = () => {
    while (/\s/.test(value[index] ?? "")) index += 1;
  };
  const assertDepth = (depth) => {
    if (depth > maxDepth) {
      throw new Error(`PPTD manifest YAML 嵌套深度超过 ${maxDepth} 安全上限`);
    }
  };
  const parseQuoted = (quote) => {
    index += 1;
    while (index < value.length) {
      const character = value[index];
      if (quote === '"' && character === "\\") {
        index += 2;
        continue;
      }
      if (quote === "'" && character === "'" && value[index + 1] === "'") {
        index += 2;
        continue;
      }
      index += 1;
      if (character === quote) return;
    }
    throw new Error(`YAML 第 ${lineNumber} 行字符串没有结束引号`);
  };

  const parseValue = (depth, terminators = "") => {
    skipWhitespace();
    const character = value[index];
    if (!character || terminators.includes(character)) {
      throw new Error(`YAML 第 ${lineNumber} 行 flow 数组包含空成员`);
    }

    if (character === "[") {
      assertDepth(depth);
      countNode();
      index += 1;
      skipWhitespace();
      if (value[index] === "]") {
        index += 1;
        return;
      }
      while (true) {
        parseValue(depth + 1, ",]");
        skipWhitespace();
        if (value[index] === "]") {
          index += 1;
          return;
        }
        if (value[index] !== ",") {
          throw new Error(`YAML 第 ${lineNumber} 行 flow 数组成员之间缺少逗号`);
        }
        index += 1;
        skipWhitespace();
        if (value[index] === "]") {
          throw new Error(`YAML 第 ${lineNumber} 行 flow 数组不允许尾随逗号`);
        }
      }
    }

    if (character === "{") {
      assertDepth(depth);
      countNode();
      index += 1;
      skipWhitespace();
      if (value[index] !== "}") {
        throw new Error(`YAML 第 ${lineNumber} 行不支持非空 flow mapping`);
      }
      index += 1;
      return;
    }

    countNode();
    if (character === '"' || character === "'") {
      parseQuoted(character);
      return;
    }

    const start = index;
    while (index < value.length && !terminators.includes(value[index])) {
      if ("[]{}".includes(value[index])) {
        throw new Error(`YAML 第 ${lineNumber} 行 flow 结构无效`);
      }
      index += 1;
    }
    const scalar = value.slice(start, index).trim();
    if (!scalar) throw new Error(`YAML 第 ${lineNumber} 行 flow 数组包含空成员`);
    if (/:\s/.test(scalar) || /:\s*$/.test(scalar)) {
      throw new Error(`YAML 第 ${lineNumber} 行不支持 flow mapping`);
    }
  };

  parseValue(baseDepth + 1);
  skipWhitespace();
  if (index !== value.length) throw new Error(`YAML 第 ${lineNumber} 行 flow 结构后存在额外内容`);
}

function assertYamlLineLimit(source, maxLines) {
  let lineCount = 1;
  let newline = source.indexOf("\n");
  while (newline !== -1) {
    lineCount += 1;
    if (lineCount > maxLines) {
      throw new Error(`PPTD manifest YAML 总行数超过 ${maxLines} 行安全上限`);
    }
    newline = source.indexOf("\n", newline + 1);
  }
  return lineCount;
}

function* yamlLines(source) {
  let start = 0;
  let lineNumber = 1;
  while (start <= source.length) {
    const newline = source.indexOf("\n", start);
    const end = newline === -1 ? source.length : newline;
    yield { text: source.slice(start, end).replace(/\r$/, ""), lineNumber };
    if (newline === -1) break;
    start = newline + 1;
    lineNumber += 1;
  }
}

/** Parse only the unambiguous YAML subset needed to derive page capabilities. */
export function extractRestrictedYamlManifestPages(
  source,
  {
    maxPageCount = 500,
    maxBytes = DEFAULT_MAX_SOURCE_BYTES,
    maxNodes = DEFAULT_MAX_JSON_NODES,
    maxDepth = DEFAULT_MAX_JSON_DEPTH,
    maxLines = DEFAULT_MAX_YAML_LINES,
    normalizePagePath = (path) => path,
  } = {},
) {
  if (typeof source !== "string") throw new TypeError("PPTD manifest 内容必须是字符串");
  assertPositiveBudget(maxPageCount, "maxPageCount");
  assertPositiveBudget(maxBytes, "maxBytes");
  assertPositiveBudget(maxNodes, "maxNodes");
  assertPositiveBudget(maxDepth, "maxDepth");
  assertPositiveBudget(maxLines, "maxLines");
  if (maxDepth > MAX_SAFE_SCANNER_DEPTH) {
    throw new TypeError(`maxDepth 不能超过 ${MAX_SAFE_SCANNER_DEPTH}`);
  }
  if (typeof normalizePagePath !== "function") throw new TypeError("normalizePagePath 必须是函数");
  if (utf8LengthUpTo(source, maxBytes) > maxBytes) {
    throw new Error(`PPTD manifest YAML 源字节超过 ${maxBytes} 安全上限`);
  }
  // Empty/comment-only lines do not consume YAML nodes. Bound them before
  // creating even one per-line substring, then parse through a lazy iterator
  // so accepted inputs never retain an array of every source line.
  assertYamlLineLimit(source, maxLines);

  const root = { type: "map", indent: 0, keys: new Set(), path: [] };
  const stack = [root];
  let pending = null;
  let nodes = 0;
  let pagesFound = false;
  const rawPages = [];
  let sawContent = false;
  let sawDocumentStart = false;

  const countNode = () => {
    nodes += 1;
    if (nodes > maxNodes) {
      throw new Error(`PPTD manifest YAML 节点数超过 ${maxNodes} 安全上限`);
    }
  };
  const addPage = (path, lineNumber) => {
    if (rawPages.length >= maxPageCount) {
      throw new Error(`文稿页数超过 ${maxPageCount} 页安全上限`);
    }
    if (path.includes("\0")) throw new Error(`YAML 第 ${lineNumber} 行路径包含 NUL`);
    // Keep bounded syntax tokens only. Canonical paths are capabilities, so
    // they are not constructed until the entire manifest passes every budget.
    rawPages.push(path);
  };

  for (const { text, lineNumber } of yamlLines(source)) {
    const indentMatch = text.match(/^[ \t]*/)[0];
    if (indentMatch.includes("\t")) throw new Error(`YAML 第 ${lineNumber} 行不能使用 Tab 缩进`);
    const indent = indentMatch.length;
    const { content, masked } = decommentYamlLine(text.slice(indent), lineNumber);
    if (!content.trim()) continue;
    const body = content.trim();
    const maskedBody = masked.trim();

    if (body === "---") {
      if (indent !== 0 || sawContent || sawDocumentStart) {
        throw new Error("PPTD manifest YAML 不允许多个文档");
      }
      sawDocumentStart = true;
      continue;
    }
    if (body === "..." || body.startsWith("%")) {
      throw new Error("PPTD manifest YAML 不支持文档指令或多个文档");
    }
    sawContent = true;
    if (/^(?:\?|:\s)/.test(maskedBody)) {
      throw new Error(`YAML 第 ${lineNumber} 行不支持复杂键`);
    }
    if (/(?:^|[\s\[{:,-])[&*!](?=\S)/.test(maskedBody)) {
      throw new Error(`YAML 第 ${lineNumber} 行不支持 anchor、alias 或 tag`);
    }
    if (/(?:^|[\s{,])<<\s*:/.test(maskedBody)) {
      throw new Error(`YAML 第 ${lineNumber} 行不支持 merge key`);
    }
    if (/^[|>]/.test(maskedBody) || /:\s*[|>]\s*$/.test(maskedBody)) {
      throw new Error(`YAML 第 ${lineNumber} 行不支持块字符串`);
    }

    const sequence = body === "-" || /^-\s+/.test(body);
    if (pending && indent > pending.parentIndent) {
      const frame = {
        type: sequence ? "seq" : "map",
        indent,
        keys: new Set(),
        path: [...pending.path, pending.key],
      };
      if (frame.path.length > maxDepth) {
        throw new Error(`PPTD manifest YAML 嵌套深度超过 ${maxDepth} 安全上限`);
      }
      if (frame.path.length === 1 && frame.path[0] === "pages" && frame.type !== "seq") {
        throw new Error("PPTD manifest 的 pages 必须是字符串路径数组");
      }
      countNode();
      stack.push(frame);
      pending = null;
    } else {
      pending = null;
      while (stack.length > 1 && indent < stack[stack.length - 1].indent) stack.pop();
      if (indent > stack[stack.length - 1].indent) {
        throw new Error(`YAML 第 ${lineNumber} 行缩进没有对应的父节点`);
      }
    }

    const frame = stack[stack.length - 1];
    if (indent !== frame.indent) throw new Error(`YAML 第 ${lineNumber} 行缩进不一致`);
    if (sequence) {
      if (frame.type !== "seq") throw new Error(`YAML 第 ${lineNumber} 行数组缩进无效`);
      const item = body === "-" ? "" : body.slice(1).trim();
      if (!item) throw new Error(`YAML 第 ${lineNumber} 行不支持空或嵌套数组成员`);
      if (findYamlMappingColon(item) !== -1) {
        throw new Error(`YAML 第 ${lineNumber} 行不支持数组内映射`);
      }
      countNode();
      const scalar = parseYamlStringScalar(item, lineNumber);
      if (frame.path.length === 1 && frame.path[0] === "pages") addPage(scalar, lineNumber);
      continue;
    }

    if (frame.type !== "map") throw new Error(`YAML 第 ${lineNumber} 行映射缩进无效`);
    const colon = findYamlMappingColon(body);
    if (colon <= 0) throw new Error(`YAML 第 ${lineNumber} 行必须是简单映射键`);
    const key = body.slice(0, colon).trim();
    if (!/^[A-Za-z0-9_$.-]+$/.test(key)) {
      throw new Error(`YAML 第 ${lineNumber} 行不支持复杂键`);
    }
    if (key === "<<") throw new Error(`YAML 第 ${lineNumber} 行不支持 merge key`);
    if (frame.keys.has(key)) throw new Error(`PPTD manifest YAML 包含重复键：${key}`);
    frame.keys.add(key);
    countNode();
    const value = body.slice(colon + 1).trim();

    if (frame.path.length === 0 && key === "pages") {
      pagesFound = true;
      if (!value) {
        pending = { parentIndent: indent, path: frame.path, key };
      } else {
        scanSafeYamlFlowValue(value, lineNumber, {
          baseDepth: frame.path.length,
          maxDepth,
          countNode,
        });
        const inlinePages = splitYamlFlowSequence(value, lineNumber, maxPageCount);
        for (const page of inlinePages) addPage(page, lineNumber);
      }
      continue;
    }

    if (!value) {
      pending = { parentIndent: indent, path: frame.path, key };
      continue;
    }
    scanSafeYamlFlowValue(value, lineNumber, {
      baseDepth: frame.path.length,
      maxDepth,
      countNode,
    });
  }

  if (!pagesFound) throw new Error("PPTD manifest 缺少顶层 pages 列表");
  return rawPages.map((path) => normalizePagePath(path));
}
