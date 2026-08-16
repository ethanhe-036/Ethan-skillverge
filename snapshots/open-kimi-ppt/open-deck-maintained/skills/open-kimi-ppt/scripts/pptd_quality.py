#!/usr/bin/env python3
"""Deterministic, read-only quality checks for a PPTD project.

The linter deliberately does not import the exporter.  It binds a report to the
exact manifest and page bytes it inspected, validates authoring invariants, and
optionally verifies ``media/sources.json`` provenance metadata.

Public API:

    report = lint_project("path/to/deck.pptd", fail_on="warning")
    receipt = compact_receipt(report)

CLI:

    python3 pptd_quality.py PROJECT [--fail-on SEVERITY] [--receipt]

Exit status is 1 when an issue meets ``--fail-on`` and 0 otherwise.  Argument
errors retain argparse's exit status 2.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple, Union


TOOL_NAME = "pptd_quality"
TOOL_VERSION = "1.0.0"
REPORT_SCHEMA_VERSION = "1.0"
MEDIA_SOURCES_SCHEMA_VERSION = "1.0"
DECK_METADATA_SCHEMA_VERSION = 1

MAX_TEXT_FILE_BYTES = 20 * 1024 * 1024
MAX_MEDIA_FILE_BYTES = 512 * 1024 * 1024
MAX_DECK_PAGES = 500
MAX_YAML_DEPTH = 100
MAX_YAML_NODES = 200_000
MAX_MANIFEST_SCAN_DEPTH = 8
MAX_MANIFEST_SCAN_ENTRIES = 20_000

SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}
FAIL_ON_CHOICES = ("none", "info", "warning", "error")
FAIL_ON_STRICTNESS = {"none": 0, "error": 1, "warning": 2, "info": 3}
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
BCP47_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")
JSONPATH_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
PLACEHOLDER_LICENSES = {
    "",
    "n/a",
    "na",
    "none",
    "tbd",
    "unknown",
    "unspecified",
    "unlicensed",
}
SAFE_TRANSITIONS = {"none", "fade", "push", "wipe", "morph"}
TRANSITION_DIRECTIONS = {"left", "right", "up", "down"}
LICENSE_POLICIES = {"known-only", "allow-user-supplied", "project-defined"}
CONSUMPTION_MODES = {"live", "read", "print", "hybrid"}
PAGE_RHYTHMS = {"anchor", "dense", "breathing"}
TARGET_APPS = {"powerpoint", "libreoffice", "wps", "keynote", "web"}
DECK_METADATA_FIELDS = {
    "schemaVersion",
    "audience",
    "objective",
    "coreMessage",
    "primaryLanguage",
    "consumptionMode",
    "pageRhythm",
    "targetApps",
    "mediaPolicy",
    "transitions",
}
MEDIA_POLICY_FIELDS = {
    "licensePolicy",
    "requireAttribution",
    "allowRemote",
    "allowGenerative",
}
TRANSITIONS_FIELDS = {"default", "pages"}
TRANSITION_FIELDS = {
    "type",
    "direction",
    "durationMs",
    "advanceAfterMs",
    "advanceOnClick",
}

PathLike = Union[str, os.PathLike[str]]


class QualityInputError(RuntimeError):
    """A controlled problem while locating or reading project input."""

    def __init__(self, code: str, message: str, path: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.path = path


class DataFormatError(Exception):
    """A controlled YAML or JSON parse failure."""


class _IssueCollector:
    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        *,
        path: Optional[str] = None,
        location: Optional[str] = None,
        page_index: Optional[int] = None,
        element_id: Optional[str] = None,
    ) -> None:
        if severity not in SEVERITY_RANK:
            raise ValueError(f"unsupported severity: {severity}")
        issue: Dict[str, Any] = {
            "severity": severity,
            "code": code,
            "message": message,
        }
        if path is not None:
            issue["path"] = path
        if location is not None:
            issue["location"] = location
        if page_index is not None:
            issue["page_index"] = page_index
        if element_id is not None:
            issue["element_id"] = element_id
        self.items.append(issue)

    def sorted_items(self) -> List[Dict[str, Any]]:
        return sorted(
            self.items,
            key=lambda item: (
                -SEVERITY_RANK[item["severity"]],
                item.get("path", ""),
                item.get("location", ""),
                item["code"],
                item["message"],
            ),
        )


def _jsonpath(base: str, key: Any) -> str:
    if isinstance(key, str) and JSONPATH_KEY_RE.fullmatch(key):
        return f"{base}.{key}"
    return f"{base}[{json.dumps(str(key), ensure_ascii=False)}]"


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _snapshot(info: os.stat_result) -> Tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
        getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000)),
    )


def _path_snapshot(path: Path) -> os.stat_result:
    """Return a file snapshot comparable with ``fstat`` on every platform.

    On Windows, CPython may expose different file-index representations from
    path-based ``stat`` and descriptor-based ``fstat`` for the same file.  A
    descriptor snapshot avoids false change detection while keeping all
    subsequent read-time comparisons tied to the opened handle.
    """

    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return info
    if os.name != "nt":
        return info
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise QualityInputError(
                "input.changed", f"file type changed during snapshot: {path}"
            )
        return opened
    finally:
        os.close(descriptor)


def _open_regular_file(path: Path, maximum_bytes: int) -> Tuple[int, os.stat_result]:
    try:
        before = _path_snapshot(path)
    except FileNotFoundError as exc:
        raise QualityInputError("input.missing", f"file does not exist: {path}") from exc
    except OSError as exc:
        raise QualityInputError("input.unreadable", f"cannot stat file {path}: {exc}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise QualityInputError("input.not_regular", f"expected a regular file: {path}")
    if before.st_size > maximum_bytes:
        raise QualityInputError(
            "input.too_large",
            f"file exceeds the {maximum_bytes}-byte safety limit: {path}",
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise QualityInputError("input.unreadable", f"cannot open file {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _snapshot(opened) != _snapshot(before):
            raise QualityInputError("input.changed", f"file changed before it was read: {path}")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, opened


def _read_stable_bytes(path: Path, maximum_bytes: int = MAX_TEXT_FILE_BYTES) -> bytes:
    descriptor, opened = _open_regular_file(path, maximum_bytes)
    chunks: List[bytes] = []
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                raise QualityInputError("input.too_large", f"file grew past its safety limit: {path}")
        after = os.fstat(descriptor)
        if total != opened.st_size or _snapshot(after) != _snapshot(opened):
            raise QualityInputError("input.changed", f"file changed while it was read: {path}")
    finally:
        os.close(descriptor)
    try:
        final = _path_snapshot(path)
    except OSError as exc:
        raise QualityInputError("input.changed", f"file path changed while it was read: {path}") from exc
    if _snapshot(final) != _snapshot(opened):
        raise QualityInputError("input.changed", f"file path changed while it was read: {path}")
    return b"".join(chunks)


def _hash_stable_file(path: Path) -> Tuple[str, int]:
    descriptor, opened = _open_regular_file(path, MAX_MEDIA_FILE_BYTES)
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            if total > MAX_MEDIA_FILE_BYTES:
                raise QualityInputError("input.too_large", f"media grew past its safety limit: {path}")
        after = os.fstat(descriptor)
        if total != opened.st_size or _snapshot(after) != _snapshot(opened):
            raise QualityInputError("input.changed", f"media changed while it was hashed: {path}")
    finally:
        os.close(descriptor)
    try:
        final = _path_snapshot(path)
    except OSError as exc:
        raise QualityInputError("input.changed", f"media path changed while it was hashed: {path}") from exc
    if _snapshot(final) != _snapshot(opened):
        raise QualityInputError("input.changed", f"media path changed while it was hashed: {path}")
    return digest.hexdigest(), total


def _decode_utf8(raw: bytes, path: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeError as exc:
        raise DataFormatError(f"expected UTF-8 text in {path}: {exc}") from exc


def _load_yaml_mapping(raw: bytes, path: str) -> Dict[str, Any]:
    try:
        import yaml
    except ImportError:
        module_name = "_open_deck_quality_deck_ir"
        module = sys.modules.get(module_name)
        if module is None:
            deck_ir_path = Path(__file__).with_name("deck_ir.py")
            specification = importlib.util.spec_from_file_location(
                module_name, deck_ir_path
            )
            if specification is None or specification.loader is None:
                raise DataFormatError("could not load the bundled PPTD subset parser")
            module = importlib.util.module_from_spec(specification)
            sys.modules[module_name] = module
            try:
                specification.loader.exec_module(module)
            except Exception:
                sys.modules.pop(module_name, None)
                raise
        try:
            value = module.parse_yaml_subset(_decode_utf8(raw, path))
        except (ValueError, RecursionError, OverflowError) as exc:
            raise DataFormatError(f"invalid YAML: {exc}") from exc
        if not isinstance(value, dict):
            raise DataFormatError("top-level YAML value must be a mapping")
        return value

    text = _decode_utf8(raw, path)
    try:
        depth = 0
        nodes = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            event_name = type(event).__name__
            if event_name == "AliasEvent" or getattr(event, "anchor", None) is not None:
                raise DataFormatError("YAML anchors and aliases are not supported")
            if event_name in ("MappingStartEvent", "SequenceStartEvent"):
                nodes += 1
                depth += 1
                if depth - 1 > MAX_YAML_DEPTH:
                    raise DataFormatError("YAML exceeds the nesting-depth safety limit")
            elif event_name in ("MappingEndEvent", "SequenceEndEvent"):
                depth = max(0, depth - 1)
            elif event_name == "ScalarEvent":
                nodes += 1
            if nodes > MAX_YAML_NODES:
                raise DataFormatError("YAML exceeds the node-count safety limit")

        class UniqueKeySafeLoader(yaml.SafeLoader):
            pass

        def construct_mapping(loader: Any, node: Any, deep: bool = False) -> Dict[Any, Any]:
            loader.flatten_mapping(node)
            result: Dict[Any, Any] = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                try:
                    duplicate = key in result
                except TypeError as exc:
                    raise DataFormatError("YAML mapping keys must be scalar values") from exc
                if duplicate:
                    raise DataFormatError(f"duplicate YAML mapping key: {key!r}")
                result[key] = loader.construct_object(value_node, deep=deep)
            return result

        UniqueKeySafeLoader.add_constructor(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
            construct_mapping,
        )
        value = yaml.load(text, Loader=UniqueKeySafeLoader)
    except DataFormatError:
        raise
    except (yaml.YAMLError, RecursionError, ValueError, OverflowError) as exc:
        raise DataFormatError(f"invalid YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise DataFormatError("top-level YAML value must be a mapping")
    return value


def _load_json_mapping(raw: bytes, path: str) -> Dict[str, Any]:
    text = _decode_utf8(raw, path)

    def unique_object(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DataFormatError(f"duplicate JSON object key: {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=unique_object)
    except DataFormatError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise DataFormatError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DataFormatError("top-level JSON value must be an object")
    return value


def _discover_manifest(source: Path) -> Path:
    source = source.expanduser()
    if source.is_symlink():
        raise QualityInputError(
            "manifest.symlink",
            f"manifest input must not be a symbolic link: {source}",
            source.name,
        )
    try:
        resolved = source.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise QualityInputError("manifest.invalid_path", f"invalid input path: {source}") from exc
    if resolved.is_file():
        if resolved.suffix.lower() != ".pptd":
            raise QualityInputError(
                "manifest.invalid_extension",
                f"input file must end in .pptd: {source}",
                source.name,
            )
        return resolved
    if not resolved.is_dir():
        raise QualityInputError("manifest.missing", f"input does not exist: {source}", source.name)

    manifests: List[Path] = []
    scanned = 0
    pending: List[Tuple[Path, int]] = [(resolved, 0)]
    while pending:
        directory, depth = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise QualityInputError("manifest.scan_failed", f"cannot scan {directory}: {exc}") from exc
        for entry in entries:
            scanned += 1
            if scanned > MAX_MANIFEST_SCAN_ENTRIES:
                raise QualityInputError("manifest.scan_limit", "project manifest scan exceeded its entry limit")
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise QualityInputError("manifest.scan_failed", f"cannot inspect {entry.path}: {exc}") from exc
            path = Path(entry.path)
            if stat.S_ISDIR(info.st_mode):
                if depth + 1 <= MAX_MANIFEST_SCAN_DEPTH:
                    pending.append((path, depth + 1))
            elif path.suffix.lower() == ".pptd" and stat.S_ISREG(info.st_mode):
                manifests.append(path.resolve())
            elif path.suffix.lower() == ".pptd" and stat.S_ISLNK(info.st_mode):
                raise QualityInputError("manifest.symlink", f"discovered manifest is a symlink: {path}")
    manifests.sort()
    if not manifests:
        raise QualityInputError("manifest.missing", f"no .pptd manifest found under: {source}")
    if len(manifests) > 1:
        choices = ", ".join(str(path.relative_to(resolved)) for path in manifests[:10])
        raise QualityInputError(
            "manifest.ambiguous",
            f"multiple .pptd manifests found; pass one explicitly: {choices}",
        )
    return manifests[0]


def _normalize_member(root: Path, raw: str, *, allow_uri: bool) -> Optional[Tuple[str, Path]]:
    if not isinstance(raw, str) or not raw.strip():
        raise QualityInputError("path.invalid", "path must be a non-empty string")
    if "\0" in raw or any(0xD800 <= ord(character) <= 0xDFFF for character in raw):
        raise QualityInputError("path.invalid", "path contains an unsupported character")
    value = raw.strip().replace("\\", "/")
    if WINDOWS_DRIVE_RE.match(value) or value.startswith("/") or value.startswith("//"):
        raise QualityInputError("path.absolute", f"project path must be relative: {raw!r}")
    if URI_RE.match(value):
        if allow_uri:
            return None
        raise QualityInputError("path.uri", f"project path cannot be a URI: {raw!r}")
    parts: List[str] = []
    for part in PurePosixPath(value).parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise QualityInputError("path.traversal", f"project path escapes its root: {raw!r}")
        parts.append(part)
    if not parts:
        raise QualityInputError("path.invalid", f"invalid project path: {raw!r}")
    alias = "/".join(parts)
    try:
        candidate = (root / Path(*parts)).resolve()
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise QualityInputError("path.traversal", f"project path escapes its root: {raw!r}") from exc
    return alias, candidate


def _bound_record(path: str, raw: bytes) -> Dict[str, Any]:
    return {
        "path": path,
        "status": "bound",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _unbound_record(path: Optional[str], status: str) -> Dict[str, Any]:
    return {"path": path, "status": status, "bytes": None, "sha256": None}


def _source_digest(
    manifest: Mapping[str, Any],
    pages: Sequence[Mapping[str, Any]],
    deck_metadata: Mapping[str, Any],
    media_sources: Optional[Mapping[str, Any]] = None,
    media_files: Optional[Sequence[Mapping[str, Any]]] = None,
) -> str:
    canonical = json.dumps(
        {
            "binding_version": 2,
            "manifest": manifest,
            "pages": list(pages),
            "deck_metadata": deck_metadata,
            "media_sources": media_sources or _unbound_record(
                "media/sources.json", "absent"
            ),
            "media_files": list(media_files or []),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_transition(
    value: Any,
    issues: _IssueCollector,
    *,
    path: str,
    location: str,
) -> None:
    if not isinstance(value, dict):
        issues.add(
            "error",
            "metadata.transition_invalid",
            "transition must be an object",
            path=path,
            location=location,
        )
        return
    for key in sorted(value, key=str):
        if key not in TRANSITION_FIELDS:
            issues.add(
                "error",
                "metadata.transition_unknown_field",
                f"transition contains unknown field {key!r}",
                path=path,
                location=_jsonpath(location, key),
            )

    transition_type = value.get("type")
    if "type" not in value:
        issues.add(
            "error",
            "metadata.transition_type_missing",
            "transition must declare type",
            path=path,
            location=f"{location}.type",
        )
    elif not isinstance(transition_type, str) or transition_type not in SAFE_TRANSITIONS:
        issues.add(
            "error",
            "metadata.transition_unsafe",
            "transition type must be one of: " + ", ".join(sorted(SAFE_TRANSITIONS)),
            path=path,
            location=f"{location}.type",
        )
    direction = value.get("direction")
    if "direction" in value and (
        not isinstance(direction, str) or direction not in TRANSITION_DIRECTIONS
    ):
        issues.add(
            "error",
            "metadata.transition_direction_invalid",
            "transition direction must be left, right, up, or down",
            path=path,
            location=f"{location}.direction",
        )
    for field, maximum in (("durationMs", 60_000), ("advanceAfterMs", 86_400_000)):
        number = value.get(field)
        if field in value and (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number < 0
            or number > maximum
        ):
            issues.add(
                "error",
                "metadata.transition_timing_invalid",
                f"{field} must be an integer between 0 and {maximum}",
                path=path,
                location=f"{location}.{field}",
            )
    if "advanceOnClick" in value and not isinstance(value["advanceOnClick"], bool):
        issues.add(
            "error",
            "metadata.transition_click_invalid",
            "advanceOnClick must be boolean",
            path=path,
            location=f"{location}.advanceOnClick",
        )


def _validate_deck_metadata(
    data: Optional[Mapping[str, Any]],
    path: str,
    issues: _IssueCollector,
    *,
    root: Optional[Path] = None,
    declared_page_aliases: Optional[Set[str]] = None,
) -> None:
    if data is None:
        return
    for key in sorted(data, key=str):
        if key not in DECK_METADATA_FIELDS:
            issues.add(
                "error",
                "metadata.unknown_field",
                f"deck metadata contains unknown field {key!r}",
                path=path,
                location=_jsonpath("$", key),
            )

    if (
        not isinstance(data.get("schemaVersion"), int)
        or isinstance(data.get("schemaVersion"), bool)
        or data.get("schemaVersion") != DECK_METADATA_SCHEMA_VERSION
    ):
        issues.add(
            "error",
            "metadata.schema_version",
            f"schemaVersion must be {DECK_METADATA_SCHEMA_VERSION}",
            path=path,
            location="$.schemaVersion",
        )

    for field, maximum in (
        ("audience", 1_000),
        ("objective", 2_000),
        ("coreMessage", 2_000),
    ):
        if field not in data:
            continue
        value = data[field]
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            issues.add(
                "error",
                "metadata.string_invalid",
                f"{field} must be a string from 1 to {maximum} characters",
                path=path,
                location=f"$.{field}",
            )

    language = data.get("primaryLanguage")
    if "primaryLanguage" in data and (
        not isinstance(language, str)
        or not 2 <= len(language) <= 64
        or BCP47_RE.fullmatch(language) is None
    ):
        issues.add(
            "error",
            "metadata.primary_language_invalid",
            "primaryLanguage must be a BCP-47 language tag",
            path=path,
            location="$.primaryLanguage",
        )

    consumption_mode = data.get("consumptionMode")
    if "consumptionMode" in data and (
        not isinstance(consumption_mode, str)
        or consumption_mode not in CONSUMPTION_MODES
    ):
        issues.add(
            "error",
            "metadata.consumption_mode_invalid",
            "consumptionMode must be live, read, print, or hybrid",
            path=path,
            location="$.consumptionMode",
        )

    page_rhythm = data.get("pageRhythm")
    if "pageRhythm" in data:
        if not isinstance(page_rhythm, list):
            issues.add(
                "error",
                "metadata.page_rhythm_invalid",
                "pageRhythm must be an array",
                path=path,
                location="$.pageRhythm",
            )
        else:
            if len(page_rhythm) > MAX_DECK_PAGES:
                issues.add(
                    "error",
                    "metadata.page_rhythm_too_long",
                    f"pageRhythm cannot contain more than {MAX_DECK_PAGES} items",
                    path=path,
                    location="$.pageRhythm",
                )
            for index, value in enumerate(page_rhythm):
                if not isinstance(value, str) or value not in PAGE_RHYTHMS:
                    issues.add(
                        "error",
                        "metadata.page_rhythm_item_invalid",
                        "pageRhythm items must be anchor, dense, or breathing",
                        path=path,
                        location=f"$.pageRhythm[{index}]",
                    )

    target_apps = data.get("targetApps")
    if "targetApps" in data:
        if not isinstance(target_apps, list):
            issues.add(
                "error",
                "metadata.target_apps_invalid",
                "targetApps must be an array",
                path=path,
                location="$.targetApps",
            )
        else:
            seen_apps: Set[str] = set()
            for index, value in enumerate(target_apps):
                if not isinstance(value, str) or value not in TARGET_APPS:
                    issues.add(
                        "error",
                        "metadata.target_app_invalid",
                        "targetApps items must be powerpoint, libreoffice, wps, keynote, or web",
                        path=path,
                        location=f"$.targetApps[{index}]",
                    )
                identity = json.dumps(
                    value,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if identity in seen_apps:
                    issues.add(
                        "error",
                        "metadata.target_apps_duplicate",
                        f"targetApps contains duplicate item {value!r}",
                        path=path,
                        location=f"$.targetApps[{index}]",
                    )
                else:
                    seen_apps.add(identity)

    transitions = data.get("transitions")
    if "transitions" in data:
        if not isinstance(transitions, dict):
            issues.add(
                "error",
                "metadata.transitions_invalid",
                "transitions must be an object",
                path=path,
                location="$.transitions",
            )
        else:
            for key in sorted(transitions, key=str):
                if key not in TRANSITIONS_FIELDS:
                    issues.add(
                        "error",
                        "metadata.transitions_unknown_field",
                        f"transitions contains unknown field {key!r}",
                        path=path,
                        location=_jsonpath("$.transitions", key),
                    )
            if "default" in transitions:
                _validate_transition(
                    transitions["default"],
                    issues,
                    path=path,
                    location="$.transitions.default",
                )
            pages = transitions.get("pages")
            if "pages" in transitions:
                if not isinstance(pages, dict):
                    issues.add(
                        "error",
                        "metadata.transition_pages_invalid",
                        "transitions.pages must be an object",
                        path=path,
                        location="$.transitions.pages",
                    )
                else:
                    if len(pages) > MAX_DECK_PAGES:
                        issues.add(
                            "error",
                            "metadata.transition_pages_too_many",
                            f"transitions.pages cannot contain more than {MAX_DECK_PAGES} entries",
                            path=path,
                            location="$.transitions.pages",
                        )
                    for key, transition in sorted(pages.items(), key=lambda item: str(item[0])):
                        page_location = _jsonpath("$.transitions.pages", key)
                        _validate_transition(
                            transition,
                            issues,
                            path=path,
                            location=page_location,
                        )
                        if declared_page_aliases is not None and root is not None:
                            try:
                                normalized = _normalize_member(root, key, allow_uri=False)
                            except QualityInputError:
                                normalized = None
                            if normalized is None or normalized[0] not in declared_page_aliases:
                                issues.add(
                                    "error",
                                    "metadata.transition_page_unknown",
                                    f"transition override refers to undeclared page {key!r}",
                                    path=path,
                                    location=page_location,
                                )

    media_policy = data.get("mediaPolicy")
    if "mediaPolicy" in data:
        if not isinstance(media_policy, dict):
            issues.add(
                "error",
                "metadata.media_policy_invalid",
                "mediaPolicy must be an object",
                path=path,
                location="$.mediaPolicy",
            )
        else:
            for key in sorted(media_policy, key=str):
                if key not in MEDIA_POLICY_FIELDS:
                    issues.add(
                        "error",
                        "metadata.media_policy_unknown_field",
                        f"mediaPolicy contains unknown field {key!r}",
                        path=path,
                        location=_jsonpath("$.mediaPolicy", key),
                    )
            license_policy = media_policy.get("licensePolicy")
            if "licensePolicy" in media_policy and (
                not isinstance(license_policy, str)
                or license_policy not in LICENSE_POLICIES
            ):
                issues.add(
                    "error",
                    "metadata.license_policy_invalid",
                    "mediaPolicy.licensePolicy must be one of: "
                    + ", ".join(sorted(LICENSE_POLICIES)),
                    path=path,
                    location="$.mediaPolicy.licensePolicy",
                )
            for field in ("requireAttribution", "allowRemote", "allowGenerative"):
                if field in media_policy and not isinstance(media_policy[field], bool):
                    issues.add(
                        "error",
                        "metadata.media_policy_flag_invalid",
                        f"mediaPolicy.{field} must be boolean",
                        path=path,
                        location=f"$.mediaPolicy.{field}",
                    )


def _load_deck_metadata(
    root: Path,
    issues: _IssueCollector,
    *,
    declared_page_aliases: Optional[Set[str]] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    alias = "deck.meta.json"
    path = root / alias
    if not path.exists():
        return _unbound_record(alias, "absent"), None
    try:
        raw = _read_stable_bytes(path)
    except QualityInputError as exc:
        issues.add("error", "metadata.unreadable", str(exc), path=alias)
        return _unbound_record(alias, "unreadable"), None
    binding = _bound_record(alias, raw)
    try:
        data = _load_json_mapping(raw, alias)
    except DataFormatError as exc:
        issues.add("error", "metadata.invalid_json", str(exc), path=alias)
        return binding, None
    _validate_deck_metadata(
        data,
        alias,
        issues,
        root=root,
        declared_page_aliases=declared_page_aliases,
    )
    return binding, data


def _manifest_size(
    manifest: Mapping[str, Any],
    manifest_path: str,
    issues: _IssueCollector,
) -> Optional[Tuple[float, float]]:
    size = manifest.get("size")
    if not isinstance(size, list) or len(size) != 2 or not all(_is_finite_number(item) for item in size):
        issues.add(
            "error",
            "manifest.invalid_size",
            "manifest size must be [width, height] with finite numeric values",
            path=manifest_path,
            location="$.size",
        )
        return None
    width, height = float(size[0]), float(size[1])
    if width <= 0 or height <= 0:
        issues.add(
            "error",
            "manifest.invalid_size",
            "manifest width and height must both be greater than zero",
            path=manifest_path,
            location="$.size",
        )
        return None
    return width, height


def _validate_bounds(
    bounds: Any,
    page_size: Optional[Tuple[float, float]],
    issues: _IssueCollector,
    *,
    path: str,
    location: str,
    page_index: int,
    element_id: Optional[str],
) -> None:
    if not isinstance(bounds, list) or len(bounds) != 4 or not all(_is_finite_number(item) for item in bounds):
        issues.add(
            "error",
            "bounds.invalid",
            "bounds must be [x, y, width, height] with finite numeric values",
            path=path,
            location=location,
            page_index=page_index,
            element_id=element_id,
        )
        return
    x, y, width, height = (float(item) for item in bounds)
    if width == 0 or height == 0:
        issues.add(
            "error",
            "bounds.zero_size",
            "element width and height must both be greater than zero",
            path=path,
            location=location,
            page_index=page_index,
            element_id=element_id,
        )
    if width < 0 or height < 0:
        issues.add(
            "error",
            "bounds.negative_size",
            "element width and height cannot be negative",
            path=path,
            location=location,
            page_index=page_index,
            element_id=element_id,
        )
    if page_size is not None and width >= 0 and height >= 0:
        page_width, page_height = page_size
        epsilon = 1e-7
        if x < -epsilon or y < -epsilon or x + width > page_width + epsilon or y + height > page_height + epsilon:
            issues.add(
                "warning",
                "bounds.out_of_page",
                f"element bounds [{x:g}, {y:g}, {width:g}, {height:g}] extend outside "
                f"the {page_width:g} x {page_height:g} page",
                path=path,
                location=location,
                page_index=page_index,
                element_id=element_id,
            )


def _validate_page(
    page: Mapping[str, Any],
    page_size: Optional[Tuple[float, float]],
    issues: _IssueCollector,
    *,
    path: str,
    page_index: int,
) -> None:
    elements = page.get("elements")
    if not isinstance(elements, list):
        issues.add(
            "error",
            "page.invalid_elements",
            "page elements must be an array",
            path=path,
            location="$.elements",
            page_index=page_index,
        )
        elements = []

    seen: Dict[str, int] = {}
    duplicates: Set[str] = set()
    for index, element in enumerate(elements):
        location = f"$.elements[{index}]"
        if not isinstance(element, dict):
            issues.add(
                "error",
                "element.invalid",
                "each element must be a mapping",
                path=path,
                location=location,
                page_index=page_index,
            )
            continue
        raw_id = element.get("elementId")
        element_id = raw_id if isinstance(raw_id, str) and raw_id else None
        if element_id is None:
            issues.add(
                "error",
                "element.invalid_id",
                "elementId must be a non-empty string",
                path=path,
                location=f"{location}.elementId",
                page_index=page_index,
            )
        elif element_id in seen:
            duplicates.add(element_id)
            issues.add(
                "error",
                "element.duplicate_id",
                f"elementId {element_id!r} duplicates elements[{seen[element_id]}]",
                path=path,
                location=f"{location}.elementId",
                page_index=page_index,
                element_id=element_id,
            )
        else:
            seen[element_id] = index
        _validate_bounds(
            element.get("bounds"),
            page_size,
            issues,
            path=path,
            location=f"{location}.bounds",
            page_index=page_index,
            element_id=element_id,
        )

    animations = page.get("animations")
    if animations is None:
        return
    if not isinstance(animations, list):
        issues.add(
            "error",
            "animation.invalid_list",
            "animations must be an array",
            path=path,
            location="$.animations",
            page_index=page_index,
        )
        return
    for index, animation in enumerate(animations):
        location = f"$.animations[{index}]"
        if not isinstance(animation, dict):
            issues.add(
                "error",
                "animation.invalid",
                "each animation must be a mapping",
                path=path,
                location=location,
                page_index=page_index,
            )
            continue
        target = animation.get("elementId")
        if not isinstance(target, str) or not target:
            issues.add(
                "error",
                "animation.invalid_target",
                "animation elementId must be a non-empty string",
                path=path,
                location=f"{location}.elementId",
                page_index=page_index,
            )
        elif target not in seen:
            issues.add(
                "error",
                "animation.unknown_target",
                f"animation targets missing elementId {target!r}",
                path=path,
                location=f"{location}.elementId",
                page_index=page_index,
                element_id=target,
            )
        elif target in duplicates:
            issues.add(
                "error",
                "animation.ambiguous_target",
                f"animation target {target!r} is ambiguous because the ID is duplicated",
                path=path,
                location=f"{location}.elementId",
                page_index=page_index,
                element_id=target,
            )


def _collect_media_references(
    value: Any,
    root: Path,
    issues: _IssueCollector,
    references: MutableMapping[str, Dict[str, Any]],
    *,
    path: str,
) -> None:
    stack: List[Tuple[Any, str]] = [(value, "$")]
    visited = 0
    while stack:
        node, location = stack.pop()
        visited += 1
        if visited > MAX_YAML_NODES:
            issues.add(
                "error",
                "media.scan_limit",
                "media reference scan exceeded its node limit",
                path=path,
                location=location,
            )
            return
        if isinstance(node, list):
            for index in range(len(node) - 1, -1, -1):
                stack.append((node[index], f"{location}[{index}]"))
            continue
        if not isinstance(node, dict):
            continue
        for key, child in reversed(list(node.items())):
            child_location = _jsonpath(location, key)
            if key == "src":
                if not isinstance(child, str) or not child.strip():
                    issues.add(
                        "error",
                        "media.invalid_src",
                        "src must be a non-empty string",
                        path=path,
                        location=child_location,
                    )
                    continue
                try:
                    normalized = _normalize_member(root, child, allow_uri=True)
                except QualityInputError as exc:
                    issues.add(
                        "error",
                        "media.invalid_path",
                        str(exc),
                        path=path,
                        location=child_location,
                    )
                    continue
                if normalized is None:
                    continue
                alias, target = normalized
                entry = references.setdefault(alias, {"target": target, "references": []})
                entry["references"].append({"path": path, "location": child_location})
            else:
                stack.append((child, child_location))


def _validate_referenced_media(
    references: Mapping[str, Mapping[str, Any]],
    issues: _IssueCollector,
) -> Tuple[Dict[str, Any], Set[str], List[Dict[str, Any]]]:
    missing: Set[str] = set()
    present = 0
    bindings: List[Dict[str, Any]] = []
    for alias in sorted(references):
        reference = references[alias]
        target = reference["target"]
        first = reference["references"][0]
        try:
            info = target.stat()
            is_regular = stat.S_ISREG(info.st_mode)
        except OSError:
            is_regular = False
        if not is_regular:
            missing.add(alias)
            bindings.append(_unbound_record(alias, "missing"))
            issues.add(
                "error",
                "media.missing",
                f"referenced local media is missing or not a regular file: {alias}",
                path=first["path"],
                location=first["location"],
            )
        else:
            try:
                digest, size = _hash_stable_file(target)
            except QualityInputError as exc:
                missing.add(alias)
                bindings.append(_unbound_record(alias, "unreadable"))
                issues.add(
                    "error",
                    "media.unreadable",
                    f"referenced local media could not be bound: {alias}: {exc}",
                    path=first["path"],
                    location=first["location"],
                )
            else:
                bindings.append(
                    {
                        "path": alias,
                        "status": "bound",
                        "bytes": size,
                        "sha256": digest,
                    }
                )
                present += 1
    return {
        "referenced": len(references),
        "present": present,
        "missing": len(missing),
        "tracked": 0,
        "source_entries": 0,
        "sources_present": False,
    }, missing, bindings


def _sources_path(
    root: Path,
    configured: Optional[PathLike],
) -> Tuple[str, Path]:
    if configured is None:
        raw = "media/sources.json"
    else:
        raw = os.fspath(configured)
        configured_path = Path(raw).expanduser()
        if configured_path.is_absolute():
            try:
                resolved = configured_path.resolve()
                alias = resolved.relative_to(root).as_posix()
            except (OSError, RuntimeError, ValueError) as exc:
                raise QualityInputError(
                    "media.sources_outside_project",
                    "media sources file must be inside the PPTD project",
                ) from exc
            return alias, resolved
    normalized = _normalize_member(root, raw, allow_uri=False)
    assert normalized is not None
    return normalized


def _load_media_sources(
    root: Path,
    configured: Optional[PathLike],
    check_sources: bool,
    issues: _IssueCollector,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], bool]:
    if not check_sources:
        return _unbound_record("media/sources.json", "disabled"), None, False
    try:
        alias, path = _sources_path(root, configured)
    except QualityInputError as exc:
        issues.add("error", exc.code, str(exc), path="media/sources.json")
        return _unbound_record("media/sources.json", "unreadable"), None, configured is not None
    explicit = configured is not None
    if not path.exists():
        if explicit:
            issues.add(
                "error",
                "media.sources_missing",
                f"configured media sources file does not exist: {alias}",
                path=alias,
            )
        return _unbound_record(alias, "absent"), None, explicit
    try:
        raw = _read_stable_bytes(path)
    except QualityInputError as exc:
        issues.add("error", "media.sources_unreadable", str(exc), path=alias)
        return _unbound_record(alias, "unreadable"), None, explicit
    binding = _bound_record(alias, raw)
    try:
        data = _load_json_mapping(raw, alias)
    except DataFormatError as exc:
        issues.add("error", "media.sources_invalid_json", str(exc), path=alias)
        return binding, None, explicit
    return binding, data, explicit


def _media_hash(
    alias: str,
    path: Path,
    cache: MutableMapping[str, Optional[Tuple[str, int]]],
    issues: _IssueCollector,
    *,
    sources_path: str,
    location: str,
) -> Optional[Tuple[str, int]]:
    if alias in cache:
        return cache[alias]
    try:
        value = _hash_stable_file(path)
    except QualityInputError as exc:
        issues.add(
            "error",
            "media.source_unreadable",
            f"cannot hash {alias}: {exc}",
            path=sources_path,
            location=location,
        )
        value = None
    cache[alias] = value
    return value


def _find_derived_cycles(graph: Mapping[str, str]) -> List[List[str]]:
    cycles: Set[Tuple[str, ...]] = set()
    for start in sorted(graph):
        order: List[str] = []
        positions: Dict[str, int] = {}
        current = start
        while current in graph and current not in positions:
            positions[current] = len(order)
            order.append(current)
            current = graph[current]
        if current in positions:
            cycle = order[positions[current] :]
            minimum = min(range(len(cycle)), key=lambda index: cycle[index])
            canonical = tuple(cycle[minimum:] + cycle[:minimum])
            cycles.add(canonical)
    return [list(cycle) for cycle in sorted(cycles)]


def _validate_media_sources(
    data: Optional[Mapping[str, Any]],
    binding: Mapping[str, Any],
    root: Path,
    references: Mapping[str, Mapping[str, Any]],
    media_summary: MutableMapping[str, Any],
    issues: _IssueCollector,
) -> None:
    if data is None:
        return
    media_summary["sources_present"] = True
    sources_path = str(binding.get("path") or "media/sources.json")
    if data.get("schema_version") != MEDIA_SOURCES_SCHEMA_VERSION:
        issues.add(
            "error",
            "media.sources_schema_version",
            f"schema_version must be {MEDIA_SOURCES_SCHEMA_VERSION!r}",
            path=sources_path,
            location="$.schema_version",
        )
    raw_assets = data.get("assets")
    if not isinstance(raw_assets, dict):
        issues.add(
            "error",
            "media.sources_invalid_assets",
            "assets must be an object keyed by project-relative media path",
            path=sources_path,
            location="$.assets",
        )
        return

    entries: Dict[str, Tuple[str, Any, Path]] = {}
    for raw_alias, metadata in sorted(raw_assets.items(), key=lambda item: str(item[0])):
        location = _jsonpath("$.assets", raw_alias)
        if not isinstance(raw_alias, str):
            issues.add(
                "error",
                "media.source_invalid_path",
                "asset keys must be strings",
                path=sources_path,
                location=location,
            )
            continue
        try:
            normalized = _normalize_member(root, raw_alias, allow_uri=False)
        except QualityInputError as exc:
            issues.add(
                "error",
                "media.source_invalid_path",
                str(exc),
                path=sources_path,
                location=location,
            )
            continue
        assert normalized is not None
        alias, target = normalized
        if alias in entries:
            issues.add(
                "error",
                "media.source_duplicate_path",
                f"asset path normalizes to duplicate entry {alias!r}",
                path=sources_path,
                location=location,
            )
            continue
        if alias != raw_alias:
            issues.add(
                "warning",
                "media.source_noncanonical_path",
                f"use canonical project-relative path {alias!r}",
                path=sources_path,
                location=location,
            )
        entries[alias] = (location, metadata, target)

    media_summary["source_entries"] = len(entries)
    media_summary["tracked"] = sum(1 for alias in references if alias in entries)
    for alias in sorted(references):
        if alias not in entries:
            first = references[alias]["references"][0]
            issues.add(
                "warning",
                "media.untracked",
                f"referenced media has no sources.json entry: {alias}",
                path=first["path"],
                location=first["location"],
            )

    hash_cache: Dict[str, Optional[Tuple[str, int]]] = {}
    derived_graph: Dict[str, str] = {}
    for alias in sorted(entries):
        location, metadata, target = entries[alias]
        if not isinstance(metadata, dict):
            issues.add(
                "error",
                "media.source_invalid_metadata",
                "asset metadata must be an object",
                path=sources_path,
                location=location,
            )
            continue
        license_value = metadata.get("license")
        if not isinstance(license_value, str) or not license_value.strip():
            issues.add(
                "error",
                "media.license_missing",
                f"asset {alias!r} must declare a non-empty license",
                path=sources_path,
                location=f"{location}.license",
            )
        elif license_value.strip().casefold() in PLACEHOLDER_LICENSES:
            issues.add(
                "warning",
                "media.license_placeholder",
                f"asset {alias!r} uses placeholder license {license_value!r}",
                path=sources_path,
                location=f"{location}.license",
            )

        expected_sha = metadata.get("sha256")
        if not isinstance(expected_sha, str) or SHA256_RE.fullmatch(expected_sha) is None:
            issues.add(
                "error",
                "media.sha256_invalid",
                f"asset {alias!r} must declare a 64-character hexadecimal sha256",
                path=sources_path,
                location=f"{location}.sha256",
            )
        else:
            actual = _media_hash(
                alias,
                target,
                hash_cache,
                issues,
                sources_path=sources_path,
                location=location,
            )
            if actual is not None and actual[0].casefold() != expected_sha.casefold():
                issues.add(
                    "error",
                    "media.sha256_mismatch",
                    f"asset {alias!r} sha256 does not match its file",
                    path=sources_path,
                    location=f"{location}.sha256",
                )

        if "derived_from" not in metadata:
            continue
        raw_parent = metadata.get("derived_from")
        if not isinstance(raw_parent, str) or not raw_parent.strip():
            issues.add(
                "error",
                "media.derived_from_invalid",
                "derived_from must be a non-empty project-relative path",
                path=sources_path,
                location=f"{location}.derived_from",
            )
            continue
        try:
            normalized_parent = _normalize_member(root, raw_parent, allow_uri=False)
        except QualityInputError as exc:
            issues.add(
                "error",
                "media.derived_from_invalid",
                str(exc),
                path=sources_path,
                location=f"{location}.derived_from",
            )
            continue
        assert normalized_parent is not None
        parent_alias, parent_target = normalized_parent
        if parent_alias == alias:
            issues.add(
                "error",
                "media.derived_from_self",
                f"asset {alias!r} cannot derive from itself",
                path=sources_path,
                location=f"{location}.derived_from",
            )
            continue
        try:
            parent_is_file = stat.S_ISREG(parent_target.stat().st_mode)
        except OSError:
            parent_is_file = False
        if not parent_is_file:
            issues.add(
                "error",
                "media.derived_from_missing",
                f"derived_from file is missing or not regular: {parent_alias}",
                path=sources_path,
                location=f"{location}.derived_from",
            )
        if parent_alias not in entries:
            issues.add(
                "warning",
                "media.derived_from_untracked",
                f"derived_from asset has no sources.json entry: {parent_alias}",
                path=sources_path,
                location=f"{location}.derived_from",
            )
        else:
            derived_graph[alias] = parent_alias

    for cycle in _find_derived_cycles(derived_graph):
        first = cycle[0]
        location = entries[first][0]
        issues.add(
            "error",
            "media.derived_from_cycle",
            "derived_from cycle: " + " -> ".join(cycle + [cycle[0]]),
            path=sources_path,
            location=f"{location}.derived_from",
        )


def _empty_source_binding(manifest_path: Optional[str] = None) -> Dict[str, Any]:
    manifest = _unbound_record(manifest_path, "missing")
    pages: List[Dict[str, Any]] = []
    deck_metadata = _unbound_record("deck.meta.json", "absent")
    media_sources = _unbound_record("media/sources.json", "absent")
    media_files: List[Dict[str, Any]] = []
    return {
        "algorithm": "sha256",
        "canonicalization": "sorted-json-v2",
        "complete": False,
        "manifest": manifest,
        "pages": pages,
        "deck_metadata": deck_metadata,
        "media_sources": media_sources,
        "media_files": media_files,
        "deck_sha256": _source_digest(
            manifest, pages, deck_metadata, media_sources, media_files
        ),
    }


def compact_receipt(report: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the stable compact receipt embedded in a full quality report."""
    source_binding = report["source_binding"]
    project = report["project"]
    media = report["media"]
    summary = report["summary"]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "source_sha256": source_binding["deck_sha256"],
        "source_complete": source_binding["complete"],
        "deck_metadata_sha256": source_binding["deck_metadata"].get("sha256"),
        "media_sources_sha256": source_binding["media_sources"].get("sha256"),
        "media_files": {
            "declared": len(source_binding.get("media_files", [])),
            "bound": sum(
                item.get("status") == "bound"
                for item in source_binding.get("media_files", [])
            ),
        },
        "manifest": project.get("manifest"),
        "pages": {
            "declared": project["declared_pages"],
            "bound": project["bound_pages"],
        },
        "media": {
            "referenced": media["referenced"],
            "missing": media["missing"],
            "tracked": media["tracked"],
        },
        "issues": dict(summary["counts"]),
        "highest_severity": summary["highest_severity"],
        "fail_on": summary["fail_on"],
        "passed": summary["passed"],
    }


def verify_quality_report(
    source: PathLike,
    report_path: PathLike,
    *,
    minimum_fail_on: str = "warning",
) -> Dict[str, Any]:
    """Verify an untampered, current report and return its compact receipt.

    The gate reruns the deterministic linter against the current project.  It
    therefore rejects a stale report after any manifest, page, metadata,
    provenance, or referenced-media byte changes.
    """

    if minimum_fail_on not in FAIL_ON_STRICTNESS:
        raise ValueError(
            "minimum_fail_on must be one of: "
            + ", ".join(FAIL_ON_STRICTNESS)
        )
    path = Path(report_path).expanduser().resolve(strict=True)
    try:
        stored = _load_json_mapping(_read_stable_bytes(path), str(path))
    except (QualityInputError, DataFormatError) as exc:
        raise QualityInputError(
            "quality_report.invalid", f"cannot read quality report: {exc}", str(path)
        ) from exc
    if stored.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise QualityInputError(
            "quality_report.schema",
            f"quality report schema_version must be {REPORT_SCHEMA_VERSION!r}",
            str(path),
        )
    if stored.get("tool") != {"name": TOOL_NAME, "version": TOOL_VERSION}:
        raise QualityInputError(
            "quality_report.tool",
            "quality report was not produced by the current linter version",
            str(path),
        )
    summary = stored.get("summary")
    if not isinstance(summary, dict):
        raise QualityInputError(
            "quality_report.summary", "quality report summary is missing", str(path)
        )
    report_fail_on = summary.get("fail_on")
    if report_fail_on not in FAIL_ON_STRICTNESS:
        raise QualityInputError(
            "quality_report.threshold", "quality report has an invalid fail_on", str(path)
        )
    if FAIL_ON_STRICTNESS[report_fail_on] < FAIL_ON_STRICTNESS[minimum_fail_on]:
        raise QualityInputError(
            "quality_report.threshold",
            f"quality report must use fail_on={minimum_fail_on!r} or a stricter threshold",
            str(path),
        )
    if summary.get("passed") is not True:
        raise QualityInputError(
            "quality_report.failed", "quality report did not pass", str(path)
        )
    source_binding = stored.get("source_binding")
    if not isinstance(source_binding, dict) or source_binding.get("complete") is not True:
        raise QualityInputError(
            "quality_report.incomplete",
            "quality report does not bind every local project input",
            str(path),
        )
    embedded_receipt = stored.get("receipt")
    computed_receipt = compact_receipt(stored)
    if embedded_receipt != computed_receipt:
        raise QualityInputError(
            "quality_report.receipt",
            "quality report receipt does not match its full report",
            str(path),
        )

    stored_sources = source_binding.get("media_sources")
    if not isinstance(stored_sources, dict):
        raise QualityInputError(
            "quality_report.media_sources",
            "quality report media_sources binding is missing",
            str(path),
        )
    sources_status = stored_sources.get("status")
    sources_path = stored_sources.get("path")
    if sources_status == "disabled":
        replay_check_sources = False
        replay_sources: Optional[PathLike] = None
    else:
        replay_check_sources = True
        replay_sources = None
        if sources_path not in (None, "media/sources.json"):
            if not isinstance(sources_path, str) or not sources_path:
                raise QualityInputError(
                    "quality_report.media_sources",
                    "quality report has an invalid media sources path",
                    str(path),
                )
            replay_sources = sources_path

    current = lint_project(
        source,
        fail_on=report_fail_on,
        media_sources=replay_sources,
        check_media_sources=replay_check_sources,
    )
    current_binding = current["source_binding"]
    if current_binding.get("deck_sha256") != source_binding.get("deck_sha256"):
        raise QualityInputError(
            "quality_report.stale",
            "quality report source SHA-256 does not match the current project",
            str(path),
        )
    if current.get("summary") != stored.get("summary") or current.get("issues") != stored.get("issues"):
        raise QualityInputError(
            "quality_report.mismatch",
            "quality report findings do not match a current deterministic lint run",
            str(path),
        )
    if current["summary"].get("passed") is not True:
        raise QualityInputError(
            "quality_report.failed", "current project does not pass its quality gate", str(path)
        )
    return compact_receipt(current)


def quality_report_bound_inputs(
    report_path: PathLike,
    project_root: PathLike,
) -> Tuple[Path, ...]:
    """Return every project file named by a verified report's source binding.

    Exporters use this after :func:`verify_quality_report` to ensure an output
    path cannot alias metadata or provenance inputs that are not otherwise part
    of the render payload.  The paths are still normalized relative to the
    project root; callers must continue to re-run the quality gate immediately
    before publication.
    """

    root = Path(project_root).expanduser().resolve(strict=True)
    path = Path(report_path).expanduser().resolve(strict=True)
    try:
        stored = _load_json_mapping(_read_stable_bytes(path), str(path))
    except (QualityInputError, DataFormatError) as exc:
        raise QualityInputError(
            "quality_report.invalid",
            f"cannot read quality report bindings: {exc}",
            str(path),
        ) from exc
    source_binding = stored.get("source_binding")
    if not isinstance(source_binding, dict):
        raise QualityInputError(
            "quality_report.binding",
            "quality report source binding is missing",
            str(path),
        )
    records: List[Any] = [
        source_binding.get("manifest"),
        source_binding.get("deck_metadata"),
        source_binding.get("media_sources"),
    ]
    for key in ("pages", "media_files"):
        value = source_binding.get(key)
        if not isinstance(value, list):
            raise QualityInputError(
                "quality_report.binding",
                f"quality report {key} binding must be an array",
                str(path),
            )
        records.extend(value)
    resolved: Set[Path] = set()
    for record in records:
        if not isinstance(record, dict):
            raise QualityInputError(
                "quality_report.binding",
                "quality report contains an invalid source-binding record",
                str(path),
            )
        if record.get("status") != "bound":
            continue
        raw = record.get("path")
        normalized = _normalize_member(root, raw, allow_uri=False)
        if normalized is None:  # pragma: no cover - allow_uri is false
            raise QualityInputError(
                "quality_report.binding",
                "quality report contains a non-local bound input",
                str(path),
            )
        resolved.add(normalized[1])
    return tuple(sorted(resolved, key=lambda item: item.as_posix()))


def _finalize_report(
    *,
    project: Dict[str, Any],
    source_binding: Dict[str, Any],
    media: Dict[str, Any],
    collector: _IssueCollector,
    fail_on: str,
) -> Dict[str, Any]:
    issues = collector.sorted_items()
    counts = {severity: 0 for severity in ("info", "warning", "error")}
    for issue in issues:
        counts[issue["severity"]] += 1
    counts["total"] = len(issues)
    highest = "none"
    for severity in ("error", "warning", "info"):
        if counts[severity]:
            highest = severity
            break
    if fail_on == "none":
        passed = True
    else:
        threshold = SEVERITY_RANK[fail_on]
        passed = not any(SEVERITY_RANK[issue["severity"]] >= threshold for issue in issues)
    summary = {
        "counts": counts,
        "highest_severity": highest,
        "fail_on": fail_on,
        "passed": passed,
    }
    report: Dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "project": project,
        "source_binding": source_binding,
        "media": media,
        "summary": summary,
        "issues": issues,
    }
    report["receipt"] = compact_receipt(report)
    return report


def lint_project(
    source: PathLike,
    *,
    fail_on: str = "error",
    media_sources: Optional[PathLike] = None,
    check_media_sources: bool = True,
) -> Dict[str, Any]:
    """Lint a PPTD project and return a JSON-serializable deterministic report.

    ``source`` may be a manifest or a directory containing exactly one manifest.
    When present, ``media/sources.json`` is checked automatically.  Pass an
    explicit ``media_sources`` path to require that file, or set
    ``check_media_sources=False`` to disable provenance checks.
    """

    if fail_on not in FAIL_ON_CHOICES:
        raise ValueError(f"fail_on must be one of: {', '.join(FAIL_ON_CHOICES)}")
    collector = _IssueCollector()
    try:
        manifest_path = _discover_manifest(Path(source))
    except QualityInputError as exc:
        collector.add("error", exc.code, str(exc), path=exc.path)
        binding = _empty_source_binding(exc.path)
        return _finalize_report(
            project={
                "manifest": None,
                "title": None,
                "declared_pages": 0,
                "bound_pages": 0,
            },
            source_binding=binding,
            media={
                "referenced": 0,
                "present": 0,
                "missing": 0,
                "tracked": 0,
                "source_entries": 0,
                "sources_present": False,
            },
            collector=collector,
            fail_on=fail_on,
        )

    root = manifest_path.parent.resolve()
    manifest_alias = manifest_path.name
    try:
        manifest_raw = _read_stable_bytes(manifest_path)
        manifest_binding = _bound_record(manifest_alias, manifest_raw)
    except QualityInputError as exc:
        collector.add("error", "manifest.unreadable", str(exc), path=manifest_alias)
        source_binding = _empty_source_binding(manifest_alias)
        return _finalize_report(
            project={
                "manifest": manifest_alias,
                "title": None,
                "declared_pages": 0,
                "bound_pages": 0,
            },
            source_binding=source_binding,
            media={
                "referenced": 0,
                "present": 0,
                "missing": 0,
                "tracked": 0,
                "source_entries": 0,
                "sources_present": False,
            },
            collector=collector,
            fail_on=fail_on,
        )

    try:
        manifest = _load_yaml_mapping(manifest_raw, manifest_alias)
    except DataFormatError as exc:
        collector.add("error", "manifest.invalid_yaml", str(exc), path=manifest_alias)
        manifest = {}
    if manifest and manifest.get("version") != "v2":
        collector.add(
            "error",
            "manifest.invalid_version",
            "PPTD manifest version must be 'v2'",
            path=manifest_alias,
            location="$.version",
        )
    page_size = _manifest_size(manifest, manifest_alias, collector) if manifest else None
    references: Dict[str, Dict[str, Any]] = {}
    if manifest:
        _collect_media_references(manifest, root, collector, references, path=manifest_alias)

    declared = manifest.get("pages") if manifest else None
    if not isinstance(declared, list) or not declared:
        collector.add(
            "error",
            "manifest.invalid_pages",
            "manifest pages must be a non-empty array",
            path=manifest_alias,
            location="$.pages",
        )
        declared_pages: List[Any] = []
    elif len(declared) > MAX_DECK_PAGES:
        collector.add(
            "error",
            "manifest.too_many_pages",
            f"manifest declares more than {MAX_DECK_PAGES} pages",
            path=manifest_alias,
            location="$.pages",
        )
        declared_pages = declared[:MAX_DECK_PAGES]
    else:
        declared_pages = declared

    declared_page_aliases: Set[str] = set()
    for raw_entry in declared_pages:
        if not isinstance(raw_entry, str) or not raw_entry.strip():
            continue
        try:
            normalized_page = _normalize_member(root, raw_entry, allow_uri=False)
        except QualityInputError:
            continue
        assert normalized_page is not None
        declared_page_aliases.add(normalized_page[0])
    deck_metadata_binding, _deck_metadata = _load_deck_metadata(
        root,
        collector,
        declared_page_aliases=declared_page_aliases,
    )

    page_bindings: List[Dict[str, Any]] = []
    seen_pages: Dict[str, int] = {}
    bound_pages = 0
    for index, raw_entry in enumerate(declared_pages):
        location = f"$.pages[{index}]"
        if not isinstance(raw_entry, str) or not raw_entry.strip():
            collector.add(
                "error",
                "page.invalid_path",
                "page path must be a non-empty string",
                path=manifest_alias,
                location=location,
                page_index=index,
            )
            page_bindings.append({"index": index, **_unbound_record(str(raw_entry), "unreadable")})
            continue
        try:
            normalized = _normalize_member(root, raw_entry, allow_uri=False)
        except QualityInputError as exc:
            collector.add(
                "error",
                "page.invalid_path",
                str(exc),
                path=manifest_alias,
                location=location,
                page_index=index,
            )
            page_bindings.append({"index": index, **_unbound_record(raw_entry, "unreadable")})
            continue
        assert normalized is not None
        alias, page_path = normalized
        key = alias.casefold()
        if key in seen_pages:
            collector.add(
                "error",
                "page.duplicate",
                f"page path duplicates pages[{seen_pages[key]}]: {alias}",
                path=manifest_alias,
                location=location,
                page_index=index,
            )
        else:
            seen_pages[key] = index
        try:
            page_raw = _read_stable_bytes(page_path)
        except QualityInputError as exc:
            collector.add(
                "error",
                "page.missing" if exc.code == "input.missing" else "page.unreadable",
                f"cannot bind page {alias}: {exc}",
                path=alias,
                page_index=index,
            )
            status = "missing" if exc.code == "input.missing" else "unreadable"
            page_bindings.append({"index": index, **_unbound_record(alias, status)})
            continue
        page_bindings.append({"index": index, **_bound_record(alias, page_raw)})
        bound_pages += 1
        try:
            page = _load_yaml_mapping(page_raw, alias)
        except DataFormatError as exc:
            collector.add(
                "error",
                "page.invalid_yaml",
                str(exc),
                path=alias,
                page_index=index,
            )
            continue
        _validate_page(page, page_size, collector, path=alias, page_index=index)
        _collect_media_references(page, root, collector, references, path=alias)

    media_summary, _missing_media, media_bindings = _validate_referenced_media(
        references, collector
    )
    sources_binding, sources_data, _explicit_sources = _load_media_sources(
        root,
        media_sources,
        check_media_sources,
        collector,
    )
    if (
        references
        and check_media_sources
        and sources_binding.get("status") == "absent"
        and media_sources is None
    ):
        collector.add(
            "warning",
            "media.sources_absent",
            "referenced local media requires a media provenance manifest",
            path=str(sources_binding.get("path") or "media/sources.json"),
        )
    _validate_media_sources(
        sources_data,
        sources_binding,
        root,
        references,
        media_summary,
        collector,
    )

    source_binding = {
        "algorithm": "sha256",
        "canonicalization": "sorted-json-v2",
        "complete": (
            manifest_binding["status"] == "bound"
            and len(page_bindings) == len(declared_pages)
            and all(item["status"] == "bound" for item in page_bindings)
            and deck_metadata_binding["status"] in ("bound", "absent")
            and sources_binding["status"] in ("bound", "absent", "disabled")
            and all(item["status"] == "bound" for item in media_bindings)
        ),
        "manifest": manifest_binding,
        "pages": page_bindings,
        "deck_metadata": deck_metadata_binding,
        "media_sources": sources_binding,
        "media_files": media_bindings,
        "deck_sha256": _source_digest(
            manifest_binding,
            page_bindings,
            deck_metadata_binding,
            sources_binding,
            media_bindings,
        ),
    }
    title = manifest.get("title") if isinstance(manifest.get("title"), str) else None
    return _finalize_report(
        project={
            "manifest": manifest_alias,
            "title": title,
            "declared_pages": len(declared_pages),
            "bound_pages": bound_pages,
        },
        source_binding=source_binding,
        media=media_summary,
        collector=collector,
        fail_on=fail_on,
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lint a PPTD project and emit JSON")
    parser.add_argument("source", type=Path, help=".pptd manifest or project directory")
    parser.add_argument(
        "--fail-on",
        choices=FAIL_ON_CHOICES,
        default="error",
        help="return status 1 for this severity or higher (default: error)",
    )
    parser.add_argument(
        "--receipt",
        action="store_true",
        help="emit only the compact, source-bound receipt",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit the full report on one line",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="atomically write the full source-bound report to this JSON file",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing --output report",
    )
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument(
        "--media-sources",
        type=Path,
        help="require and validate this in-project provenance file",
    )
    sources.add_argument(
        "--no-media-sources",
        action="store_true",
        help="disable automatic media/sources.json validation",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    return parser.parse_args(argv)


def write_report(path: PathLike, report: Mapping[str, Any], *, force: bool = False) -> Path:
    """Atomically publish a full quality report without following a symlink."""

    requested = Path(path).expanduser()
    if not requested.name or requested.name in (".", ".."):
        raise QualityInputError("report.invalid_path", f"invalid report path: {path}")
    requested.parent.mkdir(parents=True, exist_ok=True)
    destination = requested.parent.resolve(strict=True) / requested.name
    if destination.is_symlink():
        raise QualityInputError(
            "report.symlink",
            f"report destination must not be a symbolic link: {destination}",
        )
    if destination.exists():
        if not force:
            raise QualityInputError(
                "report.exists",
                f"report already exists; pass --force to replace it: {destination}",
            )
    payload = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            # Replacing the directory entry does not follow a destination
            # symlink; the second check still refuses one that appeared after
            # preflight so a caller cannot replace an unexpected link.
            if destination.is_symlink():
                raise QualityInputError(
                    "report.symlink",
                    f"report destination became a symbolic link: {destination}",
                )
            os.replace(temporary, destination)
        else:
            # Publish with atomic create-if-absent semantics.  An exists()
            # check followed by replace() would overwrite a file that appeared
            # during the race window.
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise QualityInputError(
                    "report.exists",
                    f"report appeared while it was being published: {destination}",
                ) from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return destination


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    report = lint_project(
        args.source,
        fail_on=args.fail_on,
        media_sources=args.media_sources,
        check_media_sources=not args.no_media_sources,
    )
    if args.output:
        try:
            write_report(args.output, report, force=args.force)
        except (QualityInputError, OSError) as exc:
            print(f"open-deck PPTD quality report failed: {exc}", file=sys.stderr)
            return 2
    payload: Mapping[str, Any] = compact_receipt(report) if args.receipt else report
    if args.receipt or args.compact:
        output = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    else:
        output = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    print(output)
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
