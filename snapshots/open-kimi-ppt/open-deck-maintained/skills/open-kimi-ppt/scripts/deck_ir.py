#!/usr/bin/env python3
"""Parse a fail-closed subset of PPTD v2 into a normalized local Deck IR.

This module intentionally has no third-party dependencies.  PPTD is YAML 1.2,
so the parser below implements the conservative subset used by PPTD projects:
block mappings/sequences, flow mappings/sequences, quoted/plain scalars, and
literal/folded block strings.  Ambiguous or executable YAML features are
rejected instead of being guessed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import struct
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union


MAX_TEXT_BYTES = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_PAGES = 500
MAX_ELEMENTS_PER_PAGE = 10_000
MAX_YAML_DEPTH = 128
MAX_YAML_NODES = 200_000

SUPPORTED_SHAPES = frozenset(
    {
        "rect",
        "roundRect",
        "ellipse",
        "triangle",
        "rtTriangle",
        "diamond",
        "parallelogram",
        "trapezoid",
        "hexagon",
        "octagon",
        "plus",
        "star5",
        "rightArrow",
        "leftArrow",
        "upArrow",
        "downArrow",
        "chevron",
        "homePlate",
        "donut",
        "wedgeRectCallout",
        "bracePair",
    }
)
SUPPORTED_ELEMENT_TYPES = frozenset({"text", "shape", "line", "image"})
MANIFEST_KEYS = frozenset({"version", "title", "size", "theme", "customFonts", "pages"})
THEME_KEYS = frozenset({"colors", "textStyles", "tableStyles"})
TEXT_STYLE_KEYS = frozenset(
    {
        "color",
        "fontSize",
        "fontFamily",
        "bold",
        "italic",
        "backgroundColor",
        "lineHeight",
        "lineHeightPx",
        "letterSpacing",
        "marginTop",
    }
)
TEXT_CONTENT_KEYS = TEXT_STYLE_KEYS | {
    "text",
    "style",
    "textDirection",
    "wrap",
    "align",
    "gradient",
    "shadow",
}
PAGE_KEYS = frozenset({"pageType", "background", "notes", "elements", "animations"})
COMMON_ELEMENT_KEYS = frozenset(
    {"elementId", "elementType", "bounds", "rotation", "opacity", "flip"}
)
ELEMENT_KEYS = {
    "text": COMMON_ELEMENT_KEYS | {"content"},
    # viewBox/path are admitted only so the normalizer can classify custom
    # shapes precisely and reject them as unsupported rather than as a typo.
    "shape": COMMON_ELEMENT_KEYS
    | {"shapeName", "adjustments", "fill", "border", "shadow", "viewBox", "path"},
    "line": COMMON_ELEMENT_KEYS | {"viewBox", "points", "arrow", "border", "shadow"},
    "image": COMMON_ELEMENT_KEYS | {"src", "fit", "crop", "cropShape", "border", "shadow"},
}
FILL_KEYS = frozenset({"type", "color"})
BORDER_KEYS = frozenset({"style", "width", "color"})
FIT_KEYS = frozenset({"mode"})
CROP_KEYS = frozenset({"left", "top", "right", "bottom"})
CROP_SHAPE_KEYS = frozenset({"shapeName", "adjustments"})


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str
    location: str
    message: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "location": self.location,
            "message": self.message,
        }


class DeckIRValidationError(ValueError):
    """Raised when no lossless-enough local representation can be produced."""

    def __init__(self, diagnostics: Sequence[Diagnostic]):
        self.diagnostics = tuple(diagnostics)
        summary = "; ".join(
            f"{item.code} at {item.location}: {item.message}"
            for item in self.diagnostics[:8]
        )
        if len(self.diagnostics) > 8:
            summary += f"; and {len(self.diagnostics) - 8} more"
        super().__init__(summary or "invalid PPTD project")


class _YamlSubsetError(ValueError):
    pass


@dataclass(frozen=True)
class ImageAsset:
    source: Path
    project_path: str
    extension: str
    content_type: str
    width: int
    height: int
    sha256: str
    data: bytes = field(repr=False)

    def summary(self) -> Dict[str, Any]:
        return {
            "path": self.project_path,
            "extension": self.extension,
            "contentType": self.content_type,
            "width": self.width,
            "height": self.height,
            "sha256": self.sha256,
            "bytes": len(self.data),
        }


@dataclass(frozen=True)
class ElementIR:
    element_id: str
    shape_id: int
    kind: str
    bounds: Tuple[float, float, float, float]
    rotation: float
    opacity: float
    flip: Tuple[bool, bool]
    properties: Mapping[str, Any]

    def summary(self) -> Dict[str, Any]:
        properties: Dict[str, Any] = {}
        for key, value in self.properties.items():
            properties[key] = value.summary() if isinstance(value, ImageAsset) else value
        return {
            "elementId": self.element_id,
            "shapeId": self.shape_id,
            "kind": self.kind,
            "bounds": list(self.bounds),
            "rotation": self.rotation,
            "opacity": self.opacity,
            "flip": list(self.flip),
            "properties": properties,
        }


@dataclass(frozen=True)
class PageIR:
    index: int
    source: Path
    project_path: str
    background: Mapping[str, Any]
    notes: Optional[str]
    elements: Tuple[ElementIR, ...]

    def summary(self) -> Dict[str, Any]:
        background = {
            key: value.summary() if isinstance(value, ImageAsset) else value
            for key, value in self.background.items()
        }
        return {
            "index": self.index,
            "path": self.project_path,
            "background": background,
            "notes": self.notes,
            "elements": [element.summary() for element in self.elements],
        }


@dataclass(frozen=True)
class DeckIR:
    source: Path
    project_root: Path
    title: str
    width: float
    height: float
    pages: Tuple[PageIR, ...]
    diagnostics: Tuple[Diagnostic, ...]

    def summary(self) -> Dict[str, Any]:
        return {
            "version": "v2",
            "title": self.title,
            "size": [self.width, self.height],
            "source": str(self.source),
            "pages": [page.summary() for page in self.pages],
            "diagnostics": [item.as_dict() for item in self.diagnostics],
        }


@dataclass
class _Line:
    number: int
    raw: str
    indent: int
    content: str


def _strip_yaml_comment(value: str, line_number: int) -> str:
    quote: Optional[str] = None
    depth = 0
    index = 0
    while index < len(value):
        character = value[index]
        if quote == '"':
            if character == "\\":
                index += 2
                continue
            if character == '"':
                quote = None
        elif quote == "'":
            if character == "'" and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            if character == "'":
                quote = None
        elif character in ('"', "'"):
            quote = character
        elif character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise _YamlSubsetError(f"line {line_number}: unmatched flow delimiter")
        elif character == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
        index += 1
    if quote:
        raise _YamlSubsetError(f"line {line_number}: unterminated quoted scalar")
    if depth != 0:
        raise _YamlSubsetError(f"line {line_number}: unbalanced flow value")
    return value.rstrip()


def _mapping_colon(value: str) -> int:
    quote: Optional[str] = None
    depth = 0
    index = 0
    while index < len(value):
        character = value[index]
        if quote == '"':
            if character == "\\":
                index += 2
                continue
            if character == '"':
                quote = None
        elif quote == "'":
            if character == "'" and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            if character == "'":
                quote = None
        elif character in ('"', "'"):
            quote = character
        elif character in "[{":
            depth += 1
        elif character in "]}":
            depth -= 1
        elif character == ":" and depth == 0:
            return index
        index += 1
    return -1


class _FlowParser:
    def __init__(self, source: str, line_number: int):
        self.source = source
        self.line_number = line_number
        self.index = 0

    def parse(self) -> Any:
        value = self._value()
        self._space()
        if self.index != len(self.source):
            self._error("extra content after flow value")
        return value

    def _error(self, message: str) -> None:
        raise _YamlSubsetError(
            f"line {self.line_number}, column {self.index + 1}: {message}"
        )

    def _space(self) -> None:
        while self.index < len(self.source) and self.source[self.index].isspace():
            self.index += 1

    def _value(self) -> Any:
        self._space()
        if self.index >= len(self.source):
            self._error("expected a value")
        character = self.source[self.index]
        if character == "[":
            return self._sequence()
        if character == "{":
            return self._mapping()
        if character in ('"', "'"):
            return self._quoted()
        return self._plain(stop=",]}")

    def _quoted(self) -> str:
        quote = self.source[self.index]
        start = self.index
        self.index += 1
        if quote == '"':
            escaped = False
            while self.index < len(self.source):
                character = self.source[self.index]
                self.index += 1
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    try:
                        result = json.loads(self.source[start:self.index])
                    except (ValueError, TypeError) as exc:
                        self._error(f"invalid double-quoted string: {exc}")
                    return result
            self._error("unterminated double-quoted string")
        result: List[str] = []
        while self.index < len(self.source):
            character = self.source[self.index]
            self.index += 1
            if character != "'":
                result.append(character)
                continue
            if self.index < len(self.source) and self.source[self.index] == "'":
                result.append("'")
                self.index += 1
                continue
            return "".join(result)
        self._error("unterminated single-quoted string")
        raise AssertionError

    def _sequence(self) -> List[Any]:
        self.index += 1
        result: List[Any] = []
        self._space()
        if self.index < len(self.source) and self.source[self.index] == "]":
            self.index += 1
            return result
        while True:
            result.append(self._value())
            self._space()
            if self.index >= len(self.source):
                self._error("unterminated flow sequence")
            character = self.source[self.index]
            self.index += 1
            if character == "]":
                return result
            if character != ",":
                self._error("expected ',' or ']' in flow sequence")
            self._space()
            if self.index < len(self.source) and self.source[self.index] == "]":
                self._error("trailing commas are not supported")

    def _mapping(self) -> Dict[str, Any]:
        self.index += 1
        result: Dict[str, Any] = {}
        self._space()
        if self.index < len(self.source) and self.source[self.index] == "}":
            self.index += 1
            return result
        while True:
            self._space()
            if self.index >= len(self.source):
                self._error("unterminated flow mapping")
            if self.source[self.index] in ('"', "'"):
                key = self._quoted()
            else:
                start = self.index
                while self.index < len(self.source) and self.source[self.index] not in ":,}":
                    self.index += 1
                key = self.source[start:self.index].strip()
            if not isinstance(key, str) or not key:
                self._error("mapping keys must be non-empty strings")
            if key == "<<":
                self._error("YAML merge keys are not supported")
            self._space()
            if self.index >= len(self.source) or self.source[self.index] != ":":
                self._error("expected ':' after mapping key")
            self.index += 1
            value = self._value()
            if key in result:
                self._error(f"duplicate mapping key {key!r}")
            result[key] = value
            self._space()
            if self.index >= len(self.source):
                self._error("unterminated flow mapping")
            character = self.source[self.index]
            self.index += 1
            if character == "}":
                return result
            if character != ",":
                self._error("expected ',' or '}' in flow mapping")
            self._space()
            if self.index < len(self.source) and self.source[self.index] == "}":
                self._error("trailing commas are not supported")

    def _plain(self, stop: str) -> Any:
        start = self.index
        while self.index < len(self.source) and self.source[self.index] not in stop:
            self.index += 1
        token = self.source[start:self.index].strip()
        if not token:
            self._error("empty scalar")
        return _plain_scalar(token, self.line_number)


def _plain_scalar(token: str, line_number: int) -> Any:
    if token.startswith(("&", "*", "!")):
        raise _YamlSubsetError(
            f"line {line_number}: anchors, aliases, and tags are not supported"
        )
    lowered = token.casefold()
    if lowered in {"null", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if re.fullmatch(r"[-+]?(?:0|[1-9]\d*)", token):
        if len(token.lstrip("+-")) > 100:
            raise _YamlSubsetError(f"line {line_number}: numeric scalar is too long")
        return int(token, 10)
    if re.fullmatch(
        r"[-+]?(?:(?:\d+\.\d*)|(?:\d*\.\d+)|(?:\d+))(?:[eE][-+]?\d+)?",
        token,
    ) and ("." in token or "e" in lowered):
        value = float(token)
        if not math.isfinite(value):
            raise _YamlSubsetError(f"line {line_number}: non-finite numbers are not supported")
        return value
    return token


class _YamlSubsetParser:
    def __init__(self, source: str):
        if "\x00" in source:
            raise _YamlSubsetError("YAML contains NUL")
        self.lines = source.splitlines()
        self.index = 0
        self.nodes = 0

    def _node(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_YAML_NODES:
            raise _YamlSubsetError("YAML exceeds the node-count limit")

    def _line(self, index: int) -> _Line:
        raw = self.lines[index]
        prefix = re.match(r"^[ \t]*", raw).group(0)
        if "\t" in prefix:
            raise _YamlSubsetError(f"line {index + 1}: tabs are not allowed for indentation")
        indent = len(prefix)
        content = _strip_yaml_comment(raw[indent:], index + 1).rstrip()
        return _Line(index + 1, raw, indent, content)

    def _skip_ignored(self) -> None:
        while self.index < len(self.lines):
            line = self._line(self.index)
            stripped = line.content.strip()
            if not stripped:
                self.index += 1
                continue
            if stripped == "---" and self.index == 0:
                self.index += 1
                continue
            if stripped == "..." or stripped.startswith("%"):
                raise _YamlSubsetError(
                    f"line {line.number}: directives and multiple documents are not supported"
                )
            break

    def _peek(self) -> Optional[_Line]:
        self._skip_ignored()
        return self._line(self.index) if self.index < len(self.lines) else None

    def parse(self) -> Any:
        self._skip_ignored()
        if self.index >= len(self.lines):
            raise _YamlSubsetError("YAML document is empty")
        first = self._line(self.index)
        stripped = first.content.strip()
        if first.indent == 0 and stripped[:1] in "[{":
            self.index += 1
            value = _FlowParser(stripped, first.number).parse()
        else:
            value = self._block(first.indent, 0)
        self._skip_ignored()
        if self.index != len(self.lines):
            line = self._line(self.index)
            raise _YamlSubsetError(f"line {line.number}: unexpected trailing content")
        return value

    def _block(self, indent: int, depth: int) -> Any:
        if depth > MAX_YAML_DEPTH:
            raise _YamlSubsetError("YAML exceeds the nesting-depth limit")
        line = self._peek()
        if line is None or line.indent != indent:
            number = line.number if line else len(self.lines)
            raise _YamlSubsetError(f"line {number}: inconsistent indentation")
        if line.content.startswith("-") and (
            line.content == "-" or line.content[1:2].isspace()
        ):
            return self._sequence(indent, depth)
        return self._mapping(indent, depth)

    def _mapping(self, indent: int, depth: int) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        self._node()
        while True:
            line = self._peek()
            if line is None or line.indent < indent:
                return result
            if line.indent > indent:
                raise _YamlSubsetError(f"line {line.number}: unexpected indentation")
            if line.content.startswith("-") and (
                line.content == "-" or line.content[1:2].isspace()
            ):
                return result
            key, value = self._mapping_entry(line.content, line, indent, depth)
            if key in result:
                raise _YamlSubsetError(f"line {line.number}: duplicate mapping key {key!r}")
            result[key] = value

    def _mapping_entry(
        self,
        content: str,
        line: _Line,
        key_indent: int,
        depth: int,
        *,
        consume_line: bool = True,
    ) -> Tuple[str, Any]:
        colon = _mapping_colon(content)
        if colon <= 0:
            raise _YamlSubsetError(f"line {line.number}: expected a simple mapping entry")
        raw_key = content[:colon].strip()
        if raw_key.startswith(('"', "'")):
            key = _FlowParser(raw_key, line.number).parse()
        else:
            key = raw_key
        if not isinstance(key, str) or not key or key == "<<":
            raise _YamlSubsetError(f"line {line.number}: unsupported mapping key")
        self._node()
        rest = content[colon + 1 :].strip()
        if consume_line:
            self.index += 1
        if rest.startswith(("|", ">")):
            return key, self._block_scalar(rest, key_indent, line.number)
        if rest:
            return key, self._inline(rest, line.number)
        next_line = self._peek()
        if next_line is None or next_line.indent <= key_indent:
            return key, None
        return key, self._block(next_line.indent, depth + 1)

    def _sequence(self, indent: int, depth: int) -> List[Any]:
        result: List[Any] = []
        self._node()
        while True:
            line = self._peek()
            if line is None or line.indent < indent:
                return result
            if line.indent != indent:
                raise _YamlSubsetError(f"line {line.number}: unexpected sequence indentation")
            if not (
                line.content.startswith("-")
                and (line.content == "-" or line.content[1:2].isspace())
            ):
                return result
            rest = line.content[1:].strip()
            self.index += 1
            self._node()
            if not rest:
                child = self._peek()
                if child is None or child.indent <= indent:
                    result.append(None)
                else:
                    result.append(self._block(child.indent, depth + 1))
                continue
            colon = _mapping_colon(rest)
            if colon > 0 and not rest.startswith(("{", "[", '"', "'")):
                map_indent = indent + 2
                item: Dict[str, Any] = {}
                synthetic = _Line(line.number, line.raw, map_indent, rest)
                key, value = self._mapping_entry(
                    rest,
                    synthetic,
                    map_indent,
                    depth + 1,
                    consume_line=False,
                )
                item[key] = value
                while True:
                    continuation = self._peek()
                    if continuation is None or continuation.indent <= indent:
                        break
                    if continuation.indent != map_indent:
                        raise _YamlSubsetError(
                            f"line {continuation.number}: inconsistent sequence mapping indentation"
                        )
                    key, value = self._mapping_entry(
                        continuation.content, continuation, map_indent, depth + 1
                    )
                    if key in item:
                        raise _YamlSubsetError(
                            f"line {continuation.number}: duplicate mapping key {key!r}"
                        )
                    item[key] = value
                result.append(item)
                continue
            result.append(self._inline(rest, line.number))

    def _inline(self, value: str, line_number: int) -> Any:
        if value.startswith(("[", "{", '"', "'")):
            result = _FlowParser(value, line_number).parse()
        else:
            result = _plain_scalar(value, line_number)
        self._node()
        return result

    def _block_scalar(self, marker: str, parent_indent: int, line_number: int) -> str:
        if not re.fullmatch(r"[|>][+-]?", marker):
            raise _YamlSubsetError(
                f"line {line_number}: explicit block indentation is not supported"
            )
        start = self.index
        end = start
        content_indents: List[int] = []
        while end < len(self.lines):
            raw = self.lines[end]
            if not raw.strip():
                end += 1
                continue
            prefix = re.match(r"^[ \t]*", raw).group(0)
            if "\t" in prefix:
                raise _YamlSubsetError(f"line {end + 1}: tabs are not allowed")
            indent = len(prefix)
            if indent <= parent_indent:
                break
            content_indents.append(indent)
            end += 1
        if not content_indents:
            self.index = end
            return ""
        content_indent = min(content_indents)
        values: List[str] = []
        for index in range(start, end):
            raw = self.lines[index]
            if not raw.strip():
                values.append("")
            elif len(raw) < content_indent:
                values.append("")
            else:
                values.append(raw[content_indent:])
        self.index = end
        if marker.startswith(">"):
            text = "\n".join(values)
            text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
        else:
            text = "\n".join(values)
        if marker.endswith("-"):
            return text.rstrip("\n")
        if marker.endswith("+"):
            return text + "\n"
        return text.rstrip("\n") + "\n"


def parse_yaml_subset(source: str) -> Any:
    """Parse the non-executable YAML subset accepted by the local backend."""

    return _YamlSubsetParser(source).parse()


class _PlainTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []

    def _newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in {"p", "li"}:
            self._newline()
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "li"}:
            self._newline()

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def result(self) -> str:
        return "".join(self.parts).strip("\n")


class _Context:
    def __init__(self) -> None:
        self.diagnostics: List[Diagnostic] = []

    def error(self, code: str, location: str, message: str) -> None:
        self.diagnostics.append(Diagnostic("error", code, location, message))

    def warning(self, code: str, location: str, message: str) -> None:
        self.diagnostics.append(Diagnostic("warning", code, location, message))


class _InvalidItem(Exception):
    pass


def _fail(context: _Context, code: str, location: str, message: str) -> None:
    context.error(code, location, message)
    raise _InvalidItem


def _reject_unknown_keys(
    value: Mapping[str, Any],
    allowed: Set[str],
    location: str,
    context: _Context,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        _fail(
            context,
            "unsupported.fields",
            location,
            "local backend cannot safely ignore fields: " + ", ".join(unknown),
        )


def _read_regular(path: Path, maximum: int, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ValueError(f"cannot stat {label} {path}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} must be a regular, non-symlink file: {path}")
    if before.st_size > maximum:
        raise ValueError(f"{label} exceeds {maximum} bytes: {path}")
    try:
        data = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise ValueError(f"cannot read {label} {path}: {exc}") from exc
    if len(data) != before.st_size or (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError(f"{label} changed while it was read: {path}")
    return data


def _read_yaml_mapping(path: Path) -> Dict[str, Any]:
    try:
        text = _read_regular(path, MAX_TEXT_BYTES, "PPTD text file").decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"PPTD text file is not strict UTF-8: {path}") from exc
    value = parse_yaml_subset(text)
    if not isinstance(value, dict):
        raise ValueError(f"PPTD document must be a mapping: {path}")
    return value


def _safe_project_file(root: Path, relative: Any, label: str) -> Tuple[Path, str]:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(f"{label} path must be a non-empty string")
    if "\x00" in relative or "\\" in relative:
        raise ValueError(f"{label} path contains an unsupported character: {relative!r}")
    pure = Path(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"{label} path must be a normalized project-relative path: {relative!r}")
    candidate = root.joinpath(*pure.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"{label} path escapes or is missing: {relative!r}") from exc
    cursor = root
    for part in pure.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{label} path may not traverse a symlink: {relative!r}")
    return resolved, pure.as_posix()


def _number(value: Any, location: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{location} must be numeric")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{location} must be a finite{' positive' if positive else ''} number")
    return result


def _tuple_numbers(value: Any, count: int, location: str, *, positive_tail: int = 0) -> Tuple[float, ...]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError(f"{location} must be an array of {count} numbers")
    result = tuple(_number(item, f"{location}[{index}]") for index, item in enumerate(value))
    if positive_tail:
        for item in result[-positive_tail:]:
            if item <= 0:
                raise ValueError(f"{location} width/height must be positive")
    return result


def _resolve_color(value: Any, theme_colors: Mapping[str, Any], location: str) -> Dict[str, Any]:
    if not isinstance(value, str):
        raise ValueError(f"{location} must be a HEX color or theme reference")
    seen: Set[str] = set()
    while value.startswith("$"):
        key = value[1:]
        if key in seen or key not in theme_colors:
            raise ValueError(f"{location} has an unknown or cyclic theme color {value!r}")
        seen.add(key)
        replacement = theme_colors[key]
        if not isinstance(replacement, str):
            raise ValueError(f"theme color {key!r} is not a string")
        value = replacement
    match = re.fullmatch(r"#([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?", value)
    if not match:
        raise ValueError(f"{location} must be #RRGGBB or #RRGGBBAA")
    return {
        "rgb": match.group(1).upper(),
        "alpha": int(match.group(2), 16) / 255 if match.group(2) else 1.0,
    }


def _normalize_fill(
    value: Any,
    theme_colors: Mapping[str, Any],
    location: str,
    context: _Context,
    *,
    allow_none: bool = True,
) -> Optional[Dict[str, Any]]:
    if value is None and allow_none:
        return None
    if not isinstance(value, dict):
        _fail(context, "invalid.fill", location, "fill must be a mapping")
    _reject_unknown_keys(value, set(FILL_KEYS), location, context)
    fill_type = value.get("type")
    if fill_type != "solid":
        _fail(
            context,
            "unsupported.fill",
            location,
            f"local backend supports only solid fills, received {fill_type!r}",
        )
    try:
        color = _resolve_color(value.get("color"), theme_colors, f"{location}.color")
    except ValueError as exc:
        _fail(context, "invalid.color", location, str(exc))
    return {"type": "solid", "color": color}


def _normalize_border(
    value: Any, theme_colors: Mapping[str, Any], location: str, context: _Context
) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        _fail(context, "invalid.border", location, "border must be a mapping")
    _reject_unknown_keys(value, set(BORDER_KEYS), location, context)
    style = value.get("style", "solid")
    if style not in {"solid", "dash", "dot"}:
        _fail(context, "invalid.border", location, f"unsupported border style {style!r}")
    try:
        width = _number(value.get("width", 1), f"{location}.width", positive=True)
        color = _resolve_color(value.get("color", "#000000"), theme_colors, f"{location}.color")
    except ValueError as exc:
        _fail(context, "invalid.border", location, str(exc))
    return {"style": style, "width": width, "color": color}


def _image_dimensions(data: bytes, location: str) -> Tuple[str, str, int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        if width < 1 or height < 1:
            raise ValueError(f"PNG has invalid dimensions: {location}")
        return ".png", "image/png", width, height
    if data.startswith(b"\xff\xd8"):
        index = 2
        while index + 4 <= len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            while index < len(data) and data[index] == 0xFF:
                index += 1
            if index >= len(data):
                break
            marker = data[index]
            index += 1
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if index + 2 > len(data):
                break
            length = struct.unpack(">H", data[index : index + 2])[0]
            if length < 2 or index + length > len(data):
                break
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            } and length >= 7:
                height, width = struct.unpack(">HH", data[index + 3 : index + 7])
                if width < 1 or height < 1:
                    break
                return ".jpg", "image/jpeg", width, height
            index += length
        raise ValueError(f"JPEG dimensions could not be read: {location}")
    raise ValueError(f"only signature-valid PNG and JPEG images are supported: {location}")


def _load_image(root: Path, src: Any, location: str) -> ImageAsset:
    if isinstance(src, str) and re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", src):
        raise ValueError(f"remote images are not available offline: {src}")
    path, project_path = _safe_project_file(root, src, "image")
    data = _read_regular(path, MAX_IMAGE_BYTES, "image")
    extension, content_type, width, height = _image_dimensions(data, location)
    return ImageAsset(
        source=path,
        project_path=project_path,
        extension=extension,
        content_type=content_type,
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
    )


def _stable_shape_id(element_id: str, used: Set[int]) -> int:
    candidate = 2 + int.from_bytes(hashlib.sha256(element_id.encode("utf-8")).digest()[:4], "big") % 2_000_000_000
    while candidate in used:
        candidate += 1
        if candidate > 2_000_000_001:
            candidate = 2
    used.add(candidate)
    return candidate


def _common_element(
    raw: Mapping[str, Any], location: str, context: _Context, used_ids: Set[int]
) -> Tuple[str, int, Tuple[float, float, float, float], float, float, Tuple[bool, bool]]:
    element_id = raw.get("elementId")
    if not isinstance(element_id, str) or not element_id.strip():
        _fail(context, "invalid.element_id", location, "elementId must be a non-empty string")
    try:
        bounds = _tuple_numbers(raw.get("bounds"), 4, f"{location}.bounds", positive_tail=2)
        rotation = _number(raw.get("rotation", 0), f"{location}.rotation")
        opacity = _number(raw.get("opacity", 1), f"{location}.opacity")
    except ValueError as exc:
        _fail(context, "invalid.element_geometry", location, str(exc))
    if not 0 <= opacity <= 1:
        _fail(context, "invalid.opacity", location, "opacity must be between 0 and 1")
    flip_value = raw.get("flip", [False, False])
    if not (
        isinstance(flip_value, list)
        and len(flip_value) == 2
        and all(isinstance(item, bool) for item in flip_value)
    ):
        _fail(context, "invalid.flip", location, "flip must be [boolean, boolean]")
    return (
        element_id,
        _stable_shape_id(element_id, used_ids),
        bounds,
        rotation,
        opacity,
        (flip_value[0], flip_value[1]),
    )


def _normalize_text(
    raw: Mapping[str, Any],
    common: Tuple[Any, ...],
    theme: Mapping[str, Any],
    location: str,
    context: _Context,
) -> ElementIR:
    content = raw.get("content")
    if not isinstance(content, dict):
        _fail(context, "invalid.text", location, "text content must be a mapping")
    _reject_unknown_keys(content, set(TEXT_CONTENT_KEYS), f"{location}.content", context)
    styles = theme.get("textStyles", {}) if isinstance(theme.get("textStyles", {}), dict) else {}
    merged: Dict[str, Any] = {}
    style_ref = content.get("style")
    if style_ref is not None:
        if not isinstance(style_ref, str) or not style_ref.startswith("$"):
            _fail(context, "invalid.text_style", location, "text style must be a $theme reference")
        style = styles.get(style_ref[1:])
        if not isinstance(style, dict):
            _fail(context, "invalid.text_style", location, f"unknown text style {style_ref!r}")
        _reject_unknown_keys(
            style,
            set(TEXT_STYLE_KEYS),
            f"manifest.theme.textStyles.{style_ref[1:]}",
            context,
        )
        merged.update(style)
    merged.update(content)
    text = merged.get("text")
    if not isinstance(text, str):
        _fail(context, "invalid.text", location, "content.text must be a string")
    plain_text = text
    if re.search(r"</?[A-Za-z][^>]*>", text):
        extractor = _PlainTextExtractor()
        try:
            extractor.feed(text)
            extractor.close()
            plain_text = extractor.result()
        except Exception as exc:
            _fail(context, "invalid.rich_text", location, f"rich text cannot be parsed: {exc}")
        context.warning(
            "degraded.rich_text",
            location,
            "inline rich-text runs are flattened to plain paragraphs by the local MVP backend",
        )
    unsupported = sorted(
        key
        for key in {
            "backgroundColor",
            "gradient",
            "shadow",
            "letterSpacing",
            "marginTop",
            "lineHeight",
            "lineHeightPx",
        }
        if key in merged
    )
    if unsupported:
        context.warning(
            "degraded.text_style",
            location,
            "local MVP does not render: " + ", ".join(unsupported),
        )
    colors = theme.get("colors", {}) if isinstance(theme.get("colors", {}), dict) else {}
    try:
        color = _resolve_color(merged.get("color", "#000000"), colors, f"{location}.content.color")
        font_size = _number(merged.get("fontSize", 18), f"{location}.content.fontSize", positive=True)
    except ValueError as exc:
        _fail(context, "invalid.text_style", location, str(exc))
    font = merged.get("fontFamily", "Arial")
    if isinstance(font, str) and font:
        fonts = {"latin": font, "ea": font}
    elif (
        isinstance(font, dict)
        and set(font) == {"latin", "ea"}
        and all(isinstance(font.get(key), str) and font.get(key) for key in ("latin", "ea"))
    ):
        fonts = {"latin": font["latin"], "ea": font["ea"]}
    else:
        _fail(context, "invalid.font_family", location, "fontFamily must be a string or {latin, ea}")
    align = merged.get("align", ["left", "top"])
    if not (
        isinstance(align, list)
        and len(align) == 2
        and align[0] in {"left", "center", "right", "justify", "distributed"}
        and align[1] in {"top", "middle", "bottom"}
    ):
        _fail(context, "invalid.text_alignment", location, "align has an unsupported value")
    direction = merged.get("textDirection", "horizontal")
    if direction not in {"horizontal", "vertical"}:
        _fail(context, "invalid.text_direction", location, "textDirection must be horizontal or vertical")
    for key, default in (("bold", False), ("italic", False), ("wrap", True)):
        if not isinstance(merged.get(key, default), bool):
            _fail(
                context,
                "invalid.text_style",
                location,
                f"content.{key} must be a boolean",
            )
    properties = {
        "text": plain_text,
        "color": color,
        "fontSize": font_size,
        "fontFamily": fonts,
        "bold": merged.get("bold", False),
        "italic": merged.get("italic", False),
        "align": (align[0], align[1]),
        "wrap": merged.get("wrap", True),
        "textDirection": direction,
    }
    return ElementIR(*common[:2], "text", *common[2:], properties)


def _normalize_shape(
    raw: Mapping[str, Any],
    common: Tuple[Any, ...],
    theme: Mapping[str, Any],
    location: str,
    context: _Context,
) -> ElementIR:
    shape_name = raw.get("shapeName")
    if shape_name == "custom":
        _fail(context, "unsupported.custom_shape", location, "custom SVG paths require the remote backend")
    if shape_name not in SUPPORTED_SHAPES:
        _fail(context, "unsupported.shape", location, f"unsupported local preset shape {shape_name!r}")
    adjustments = raw.get("adjustments", [])
    if not isinstance(adjustments, list):
        _fail(context, "invalid.adjustments", location, "adjustments must be an array")
    try:
        normalized_adjustments = tuple(_number(item, f"{location}.adjustments") for item in adjustments)
    except ValueError as exc:
        _fail(context, "invalid.adjustments", location, str(exc))
    colors = theme.get("colors", {}) if isinstance(theme.get("colors", {}), dict) else {}
    fill = _normalize_fill(raw.get("fill"), colors, f"{location}.fill", context)
    border = _normalize_border(raw.get("border"), colors, f"{location}.border", context)
    if raw.get("shadow") is not None:
        context.warning("degraded.shadow", location, "shape shadows are not rendered by the local MVP")
    properties = {
        "shapeName": shape_name,
        "adjustments": normalized_adjustments,
        "fill": fill,
        "border": border,
    }
    return ElementIR(*common[:2], "shape", *common[2:], properties)


def _normalize_line(
    raw: Mapping[str, Any],
    common: Tuple[Any, ...],
    theme: Mapping[str, Any],
    location: str,
    context: _Context,
) -> ElementIR:
    try:
        view_box = _tuple_numbers(raw.get("viewBox"), 2, f"{location}.viewBox")
    except ValueError as exc:
        _fail(context, "invalid.line", location, str(exc))
    if view_box[0] <= 0 or view_box[1] <= 0:
        _fail(context, "invalid.line", location, "line viewBox dimensions must be positive")
    points_value = raw.get("points")
    if not isinstance(points_value, str):
        _fail(context, "invalid.line", location, "line points must be a string")
    point_tokens = points_value.split()
    points: List[Tuple[float, float]] = []
    try:
        for token in point_tokens:
            pieces = token.split(",")
            if len(pieces) != 2:
                raise ValueError("each point must be x,y")
            points.append((float(pieces[0]), float(pieces[1])))
    except (ValueError, OverflowError) as exc:
        _fail(context, "invalid.line", location, f"cannot parse line points: {exc}")
    if len(points) != 2 or not all(math.isfinite(value) for point in points for value in point):
        _fail(
            context,
            "unsupported.line_path",
            location,
            "local MVP supports straight two-point lines only",
        )
    arrows = raw.get("arrow", [None, None])
    if not (
        isinstance(arrows, list)
        and len(arrows) == 2
        and all(item in {None, "arrow", "stealth", "diamond", "oval"} for item in arrows)
    ):
        _fail(context, "invalid.line_arrow", location, "arrow must contain two supported endpoints")
    colors = theme.get("colors", {}) if isinstance(theme.get("colors", {}), dict) else {}
    border = _normalize_border(
        raw.get("border", {"style": "solid", "width": 1, "color": "#000000"}),
        colors,
        f"{location}.border",
        context,
    )
    if raw.get("shadow") is not None:
        context.warning("degraded.shadow", location, "line shadows are not rendered by the local MVP")
    properties = {"viewBox": view_box, "points": tuple(points), "arrow": tuple(arrows), "border": border}
    return ElementIR(*common[:2], "line", *common[2:], properties)


def _normalize_crop(value: Any, location: str, context: _Context) -> Dict[str, float]:
    if value is None:
        return {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}
    if not isinstance(value, dict):
        _fail(context, "invalid.image_crop", location, "crop must be a mapping")
    _reject_unknown_keys(value, set(CROP_KEYS), location, context)
    try:
        crop = {key: _number(value.get(key, 0), f"{location}.{key}") for key in ("left", "top", "right", "bottom")}
    except ValueError as exc:
        _fail(context, "invalid.image_crop", location, str(exc))
    if any(item < 0 for item in crop.values()):
        _fail(
            context,
            "unsupported.image_outset",
            location,
            "negative image crop requires transparent padding and is not supported locally",
        )
    if crop["left"] + crop["right"] >= 1 or crop["top"] + crop["bottom"] >= 1:
        _fail(context, "invalid.image_crop", location, "image crop removes the entire source")
    return crop


def _normalize_image(
    raw: Mapping[str, Any],
    common: Tuple[Any, ...],
    theme: Mapping[str, Any],
    root: Path,
    location: str,
    context: _Context,
) -> ElementIR:
    try:
        asset = _load_image(root, raw.get("src"), location)
    except ValueError as exc:
        _fail(context, "invalid.image", location, str(exc))
    fit = raw.get("fit", {"mode": "cover"})
    if not isinstance(fit, dict) or fit.get("mode", "cover") not in {"cover", "contain", "fill"}:
        _fail(context, "invalid.image_fit", location, "fit.mode must be cover, contain, or fill")
    _reject_unknown_keys(fit, set(FIT_KEYS), f"{location}.fit", context)
    crop = _normalize_crop(raw.get("crop"), f"{location}.crop", context)
    crop_shape = raw.get("cropShape")
    shape_name = "rect"
    if crop_shape is not None:
        if not isinstance(crop_shape, dict):
            _fail(context, "invalid.crop_shape", location, "cropShape must be a mapping")
        _reject_unknown_keys(
            crop_shape,
            set(CROP_SHAPE_KEYS),
            f"{location}.cropShape",
            context,
        )
        shape_name = crop_shape.get("shapeName")
        if shape_name == "custom":
            _fail(context, "unsupported.custom_path", location, "custom image crop paths require the remote backend")
        if shape_name not in SUPPORTED_SHAPES:
            _fail(context, "unsupported.crop_shape", location, f"unsupported crop shape {shape_name!r}")
        if crop_shape.get("adjustments"):
            context.warning(
                "degraded.crop_adjustments",
                location,
                "image crop-shape adjustments are not rendered by the local MVP",
            )
    colors = theme.get("colors", {}) if isinstance(theme.get("colors", {}), dict) else {}
    border = _normalize_border(raw.get("border"), colors, f"{location}.border", context)
    if raw.get("shadow") is not None:
        context.warning("degraded.shadow", location, "image shadows are not rendered by the local MVP")
    properties = {
        "asset": asset,
        "fit": fit.get("mode", "cover"),
        "crop": crop,
        "cropShape": shape_name,
        "border": border,
    }
    return ElementIR(*common[:2], "image", *common[2:], properties)


def _find_manifest(source: Path) -> Path:
    if source.is_file():
        if source.suffix.casefold() != ".pptd":
            raise ValueError("source file must have a .pptd extension")
        return source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError(f"PPTD source does not exist: {source}")
    manifests = sorted(source.glob("*.pptd"))
    if len(manifests) != 1:
        raise ValueError(
            f"project directory must contain exactly one top-level .pptd manifest; found {len(manifests)}"
        )
    return manifests[0].resolve(strict=True)


def load_deck_ir(source: Union[os.PathLike[str], str]) -> DeckIR:
    """Load and normalize a PPTD v2 project, failing closed on lost features."""

    context = _Context()
    try:
        manifest = _find_manifest(Path(source).expanduser())
        root = manifest.parent.resolve(strict=True)
        manifest_value = _read_yaml_mapping(manifest)
    except (_YamlSubsetError, ValueError, OSError) as exc:
        raise DeckIRValidationError(
            [Diagnostic("error", "invalid.manifest", str(source), str(exc))]
        ) from exc
    if manifest_value.get("version") != "v2":
        context.error("invalid.version", str(manifest), "local backend accepts PPTD version v2 only")
    unknown_manifest = sorted(set(manifest_value) - MANIFEST_KEYS)
    if unknown_manifest:
        context.error(
            "unsupported.manifest_fields",
            str(manifest),
            "local backend cannot safely ignore manifest fields: "
            + ", ".join(unknown_manifest),
        )
    try:
        width, height = _tuple_numbers(manifest_value.get("size"), 2, "manifest.size")
        if width <= 0 or height <= 0:
            raise ValueError("manifest.size dimensions must be positive")
    except ValueError as exc:
        context.error("invalid.canvas", str(manifest), str(exc))
        width, height = 960.0, 540.0
    title = manifest_value.get("title", "")
    if not isinstance(title, str):
        context.error("invalid.title", str(manifest), "title must be a string")
        title = ""
    theme = manifest_value.get("theme", {})
    if theme is None:
        theme = {}
    if not isinstance(theme, dict):
        context.error("invalid.theme", str(manifest), "theme must be a mapping")
        theme = {}
    unknown_theme = sorted(set(theme) - THEME_KEYS)
    if unknown_theme:
        context.error(
            "unsupported.theme_fields",
            str(manifest),
            "local backend cannot safely ignore theme fields: "
            + ", ".join(unknown_theme),
        )
    for key in ("colors", "textStyles", "tableStyles"):
        if key in theme and not isinstance(theme[key], dict):
            context.error(
                "invalid.theme",
                str(manifest),
                f"theme.{key} must be a mapping",
            )
    custom_fonts = manifest_value.get("customFonts")
    if custom_fonts is not None and not isinstance(custom_fonts, list):
        context.error("invalid.custom_fonts", str(manifest), "customFonts must be an array")
    elif isinstance(custom_fonts, list):
        for index, font in enumerate(custom_fonts):
            if (
                not isinstance(font, dict)
                or set(font) != {"family", "src"}
                or not all(isinstance(font.get(key), str) and font.get(key) for key in ("family", "src"))
            ):
                context.error(
                    "invalid.custom_fonts",
                    str(manifest),
                    f"customFonts[{index}] must be exactly {{family, src}} with non-empty strings",
                )
    if custom_fonts:
        context.warning(
            "degraded.custom_fonts",
            str(manifest),
            "custom web fonts are not fetched or embedded by the offline MVP",
        )
    pages_value = manifest_value.get("pages")
    if not isinstance(pages_value, list) or not pages_value or len(pages_value) > MAX_PAGES:
        context.error(
            "invalid.pages",
            str(manifest),
            f"pages must contain between 1 and {MAX_PAGES} project-relative paths",
        )
        pages_value = []
    pages: List[PageIR] = []
    seen_page_paths: Set[str] = set()
    for page_index, relative in enumerate(pages_value, start=1):
        page_location = f"{manifest}:pages[{page_index - 1}]"
        try:
            page_path, project_path = _safe_project_file(root, relative, "page")
            if project_path in seen_page_paths:
                raise ValueError(f"duplicate page path {project_path!r}")
            seen_page_paths.add(project_path)
            raw_page = _read_yaml_mapping(page_path)
        except (_YamlSubsetError, ValueError, OSError) as exc:
            context.error("invalid.page", page_location, str(exc))
            continue
        unknown_page = sorted(set(raw_page) - PAGE_KEYS)
        if unknown_page:
            context.error(
                "unsupported.page_fields",
                project_path,
                "local backend cannot safely ignore page fields: "
                + ", ".join(unknown_page),
            )
        animations = raw_page.get("animations")
        if animations:
            context.error(
                "unsupported.animations",
                project_path,
                "animations are not emitted by the local MVP; use the remote exporter",
            )
        notes = raw_page.get("notes")
        if notes is not None and not isinstance(notes, str):
            context.error("invalid.notes", project_path, "speaker notes must be plain text")
            notes = None
        colors = theme.get("colors", {}) if isinstance(theme.get("colors", {}), dict) else {}
        background_value = raw_page.get(
            "background", {"type": "solid", "color": "#FFFFFF"}
        )
        try:
            background = _normalize_fill(
                background_value,
                colors,
                f"{project_path}.background",
                context,
                allow_none=False,
            )
        except _InvalidItem:
            background = {"type": "solid", "color": {"rgb": "FFFFFF", "alpha": 1.0}}
        raw_elements = raw_page.get("elements")
        if not isinstance(raw_elements, list) or len(raw_elements) > MAX_ELEMENTS_PER_PAGE:
            context.error(
                "invalid.elements",
                project_path,
                f"elements must be an array with at most {MAX_ELEMENTS_PER_PAGE} items",
            )
            raw_elements = []
        used_numeric_ids = {1}
        used_element_ids: Set[str] = set()
        elements: List[ElementIR] = []
        for element_index, raw_element in enumerate(raw_elements):
            location = f"{project_path}.elements[{element_index}]"
            if not isinstance(raw_element, dict):
                context.error("invalid.element", location, "element must be a mapping")
                continue
            element_id = raw_element.get("elementId")
            if isinstance(element_id, str) and element_id in used_element_ids:
                context.error("invalid.duplicate_element_id", location, f"duplicate elementId {element_id!r}")
                continue
            try:
                common = _common_element(raw_element, location, context, used_numeric_ids)
                used_element_ids.add(common[0])
                kind = raw_element.get("elementType")
                if kind not in SUPPORTED_ELEMENT_TYPES:
                    _fail(
                        context,
                        "unsupported.element_type",
                        location,
                        f"elementType {kind!r} is not supported locally (table/chart/icon require remote export)",
                    )
                _reject_unknown_keys(
                    raw_element,
                    set(ELEMENT_KEYS[kind]),
                    location,
                    context,
                )
                if kind == "text":
                    element = _normalize_text(raw_element, common, theme, location, context)
                elif kind == "shape":
                    element = _normalize_shape(raw_element, common, theme, location, context)
                elif kind == "line":
                    element = _normalize_line(raw_element, common, theme, location, context)
                else:
                    element = _normalize_image(raw_element, common, theme, root, location, context)
                elements.append(element)
            except _InvalidItem:
                continue
        pages.append(
            PageIR(
                index=page_index,
                source=page_path,
                project_path=project_path,
                background=background,
                notes=notes,
                elements=tuple(elements),
            )
        )
    errors = [item for item in context.diagnostics if item.severity == "error"]
    if errors:
        raise DeckIRValidationError(context.diagnostics)
    return DeckIR(
        source=manifest,
        project_root=root,
        title=title,
        width=width,
        height=height,
        pages=tuple(pages),
        diagnostics=tuple(context.diagnostics),
    )


__all__ = [
    "DeckIR",
    "DeckIRValidationError",
    "Diagnostic",
    "ElementIR",
    "ImageAsset",
    "PageIR",
    "load_deck_ir",
    "parse_yaml_subset",
]
