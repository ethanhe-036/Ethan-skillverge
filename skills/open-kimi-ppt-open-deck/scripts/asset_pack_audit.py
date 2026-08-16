#!/usr/bin/env python3
"""Audit optional presentation asset packs before they enter the skill.

The core package intentionally ships no third-party icon, sound, or brand pack.
This tool validates a separately prepared pack manifest, its license evidence,
and the bytes that would be distributed.  It performs no network access.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit


MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_ASSETS = 50_000
MAX_SOURCES = 1_000
MAX_ASSET_BYTES = 128 * 1024 * 1024
MAX_PACK_BYTES = 4 * 1024 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,126}$")
ALLOWED_KINDS = frozenset({"icons", "sounds", "brand-specs", "brand-assets", "mixed"})
SOURCE_KINDS = frozenset({"icons", "sounds", "brand-specs", "brand-assets"})
ALLOWED_LICENSES = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "CC0-1.0",
        "CC-BY-4.0",
    }
)
NOTICE_REQUIRED_LICENSES = frozenset(
    {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "CC-BY-4.0"}
)
ATTRIBUTION_REQUIRED_LICENSES = frozenset({"CC-BY-4.0"})
BRAND_PERMISSION_BASES = frozenset(
    {"user-supplied", "official-redistribution-license", "documented-written-permission"}
)
MANIFEST_KEYS = frozenset(
    {
        "pack_id",
        "version",
        "snapshot_date",
        "kind",
        "distribution",
        "default_enabled",
        "sources",
        "assets",
    }
)
SOURCE_KEYS = frozenset(
    {
        "content_kind",
        "license",
        "license_url",
        "source_url",
        "notice",
        "attribution",
        "contains_trademarks",
        "usage_policy_url",
        "permission_basis",
        "permission_evidence",
        "redistributable",
    }
)
ASSET_KEYS = frozenset(
    {"path", "source", "sha256", "derived_from", "modifications"}
)


class AssetAuditError(RuntimeError):
    """Raised for an invalid or unsafe asset pack."""


def _finding(code: str, message: str, *, severity: str = "error", **context: Any) -> Dict[str, Any]:
    value: Dict[str, Any] = {"severity": severity, "code": code, "message": message}
    if context:
        value["context"] = context
    return value


def _is_https_url(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 4096:
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username


def _safe_relative_path(value: Any, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise AssetAuditError(f"{field} must be a non-empty relative POSIX path")
    if "\\" in value or PureWindowsPath(value).is_absolute():
        raise AssetAuditError(f"{field} must not use a Windows or absolute path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise AssetAuditError(f"{field} contains an unsafe path component: {value!r}")
    return path


def _load_json_mapping(path: Path) -> Tuple[bytes, Mapping[str, Any]]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise AssetAuditError(f"manifest must be a regular non-symlink file: {path}")
    if before.st_size > MAX_MANIFEST_BYTES:
        raise AssetAuditError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes: {path}")
    raw = path.read_bytes()
    after = path.lstat()
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or len(raw) != before.st_size:
        raise AssetAuditError(f"manifest changed while it was read: {path}")
    def reject_duplicate_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
        value: Dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise AssetAuditError(f"manifest contains duplicate JSON key: {key!r}")
            value[key] = item
        return value

    try:
        text = raw.decode("utf-8")
        value = json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except AssetAuditError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetAuditError(f"manifest is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AssetAuditError("manifest root must be a JSON object")
    return raw, value


def _resolve_regular_file(root: Path, relative: PurePosixPath, *, field: str) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise AssetAuditError(f"{field} traverses a symbolic link: {current}")
    resolved_root = root.resolve(strict=True)
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise AssetAuditError(f"{field} escapes the asset-pack root: {relative}") from exc
    info = resolved.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise AssetAuditError(f"{field} must identify a regular file: {relative}")
    return resolved


def _hash_regular_file(path: Path) -> Tuple[str, int]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise AssetAuditError(f"asset must be a regular non-symlink file: {path}")
    if before.st_size > MAX_ASSET_BYTES:
        raise AssetAuditError(f"asset exceeds {MAX_ASSET_BYTES} bytes: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    after = path.lstat()
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or size != before.st_size:
        raise AssetAuditError(f"asset changed while it was hashed: {path}")
    return digest.hexdigest(), size


def _evidence_digest(records: Mapping[str, Tuple[str, int]]) -> str:
    digest = hashlib.sha256()
    for path, (sha256, size) in sorted(records.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256.encode("ascii"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _derived_cycles(graph: Mapping[str, str]) -> List[List[str]]:
    cycles: Set[Tuple[str, ...]] = set()
    for start in sorted(graph):
        order: List[str] = []
        positions: Dict[str, int] = {}
        node = start
        while node in graph:
            if node in positions:
                cycle = order[positions[node] :]
                rotations = [tuple(cycle[index:] + cycle[:index]) for index in range(len(cycle))]
                cycles.add(min(rotations))
                break
            positions[node] = len(order)
            order.append(node)
            node = graph[node]
    return [list(cycle) for cycle in sorted(cycles)]


def _require_string(mapping: Mapping[str, Any], key: str, findings: List[Dict[str, Any]]) -> Optional[str]:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        findings.append(_finding("manifest.required", f"{key} must be a non-empty string", field=key))
        return None
    return value.strip()


def _validate_source(
    source_id: str,
    source: Any,
    *,
    kind: Optional[str],
    default_enabled: Any,
    root: Path,
    notice_cache: Dict[str, Tuple[str, int]],
    evidence_cache: Dict[str, Tuple[str, int]],
) -> Tuple[List[Dict[str, Any]], Optional[str], bool, Optional[str]]:
    findings: List[Dict[str, Any]] = []
    if not SOURCE_ID_RE.fullmatch(source_id):
        findings.append(_finding("source.id", "source id has an invalid format", source=source_id))
    if not isinstance(source, dict):
        return [_finding("source.type", "source entry must be an object", source=source_id)], None, False, None

    unknown = sorted(set(source) - SOURCE_KEYS)
    if unknown:
        findings.append(
            _finding(
                "source.fields",
                "source contains unsupported fields",
                source=source_id,
                fields=unknown,
            )
        )
    declared_kind = source.get("content_kind")
    if kind == "mixed":
        if declared_kind not in SOURCE_KINDS:
            findings.append(
                _finding(
                    "source.content_kind",
                    "every source in a mixed pack must declare content_kind",
                    source=source_id,
                    allowed=sorted(SOURCE_KINDS),
                )
            )
            effective_kind: Optional[str] = None
        else:
            effective_kind = declared_kind
    else:
        effective_kind = kind if kind in SOURCE_KINDS else None
        if declared_kind is not None and declared_kind != effective_kind:
            findings.append(
                _finding(
                    "source.content_kind",
                    "source content_kind must match the non-mixed pack kind",
                    source=source_id,
                    expected=effective_kind,
                    actual=declared_kind,
                )
            )

    license_id = _require_string(source, "license", findings)
    if license_id and license_id not in ALLOWED_LICENSES:
        findings.append(
            _finding(
                "source.license.unsupported",
                "license is not on the redistribution allowlist; legal review is required",
                source=source_id,
                license=license_id,
            )
        )
    if not _is_https_url(source.get("source_url")):
        findings.append(_finding("source.url", "source_url must be an HTTPS URL", source=source_id))
    if not _is_https_url(source.get("license_url")):
        findings.append(_finding("source.license_url", "license_url must be an HTTPS URL", source=source_id))

    notice = source.get("notice")
    if license_id in NOTICE_REQUIRED_LICENSES:
        try:
            notice_path = _safe_relative_path(notice, field=f"sources.{source_id}.notice")
            notice_key = notice_path.as_posix()
            if notice_key not in notice_cache:
                absolute_notice = _resolve_regular_file(root, notice_path, field="notice")
                notice_cache[notice_key] = _hash_regular_file(absolute_notice)
            if notice_cache[notice_key][1] == 0:
                findings.append(
                    _finding(
                        "source.notice.empty",
                        "required notice file must not be empty",
                        source=source_id,
                        path=notice_key,
                    )
                )
        except (AssetAuditError, FileNotFoundError, OSError) as exc:
            findings.append(_finding("source.notice", str(exc), source=source_id))
    elif notice is not None:
        try:
            _safe_relative_path(notice, field=f"sources.{source_id}.notice")
        except AssetAuditError as exc:
            findings.append(_finding("source.notice", str(exc), source=source_id))

    if license_id in ATTRIBUTION_REQUIRED_LICENSES:
        attribution = source.get("attribution")
        if not isinstance(attribution, str) or not attribution.strip():
            findings.append(
                _finding(
                    "source.attribution",
                    f"{license_id} requires a non-empty attribution string",
                    source=source_id,
                )
            )

    trademarks = source.get("contains_trademarks", False)
    if not isinstance(trademarks, bool):
        findings.append(_finding("source.trademarks.type", "contains_trademarks must be boolean", source=source_id))
        trademarks = False
    if trademarks:
        if default_enabled is not False:
            findings.append(
                _finding(
                    "source.trademarks.default",
                    "a pack containing trademarks must be opt-in",
                    source=source_id,
                )
            )
        if not _is_https_url(source.get("usage_policy_url")):
            findings.append(
                _finding(
                    "source.trademarks.policy",
                    "trademarked assets require an HTTPS usage_policy_url",
                    source=source_id,
                )
            )

    if effective_kind == "brand-assets":
        permission_basis = source.get("permission_basis")
        if permission_basis not in BRAND_PERMISSION_BASES:
            findings.append(
                _finding(
                    "source.brand.permission",
                    "brand assets require a documented permission basis",
                    source=source_id,
                    allowed=sorted(BRAND_PERMISSION_BASES),
                )
            )
        if source.get("redistributable") is not True:
            findings.append(
                _finding(
                    "source.brand.redistribution",
                    "brand assets cannot be packaged without explicit redistribution permission",
                    source=source_id,
                )
            )
        evidence = source.get("permission_evidence")
        if not isinstance(evidence, dict):
            findings.append(
                _finding(
                    "source.brand.evidence",
                    "brand assets require auditable permission_evidence",
                    source=source_id,
                )
            )
        else:
            evidence_type = evidence.get("type")
            if evidence_type == "url":
                if set(evidence) != {"type", "url"} or not _is_https_url(evidence.get("url")):
                    findings.append(
                        _finding(
                            "source.brand.evidence",
                            "URL permission evidence must contain only type=url and an HTTPS url",
                            source=source_id,
                        )
                    )
            elif evidence_type == "file":
                if set(evidence) != {"type", "path", "sha256"}:
                    findings.append(
                        _finding(
                            "source.brand.evidence",
                            "file permission evidence must contain type, path, and sha256",
                            source=source_id,
                        )
                    )
                else:
                    try:
                        evidence_path = _safe_relative_path(
                            evidence.get("path"),
                            field=f"sources.{source_id}.permission_evidence.path",
                        )
                        evidence_key = evidence_path.as_posix()
                        expected = evidence.get("sha256")
                        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
                            raise AssetAuditError("permission evidence sha256 must be 64 lowercase hex characters")
                        if evidence_key not in evidence_cache:
                            absolute = _resolve_regular_file(
                                root, evidence_path, field="permission evidence"
                            )
                            evidence_cache[evidence_key] = _hash_regular_file(absolute)
                        actual, evidence_size = evidence_cache[evidence_key]
                        if evidence_size == 0:
                            raise AssetAuditError("permission evidence file must not be empty")
                        if actual != expected:
                            raise AssetAuditError(
                                f"permission evidence sha256 mismatch: expected {expected}, actual {actual}"
                            )
                    except (AssetAuditError, FileNotFoundError, OSError) as exc:
                        findings.append(
                            _finding(
                                "source.brand.evidence",
                                str(exc),
                                source=source_id,
                            )
                        )
            else:
                findings.append(
                    _finding(
                        "source.brand.evidence",
                        "permission_evidence.type must be url or file",
                        source=source_id,
                    )
                )
    return findings, license_id, trademarks, effective_kind


def audit_asset_pack(manifest_path: Path, *, root: Optional[Path] = None) -> Dict[str, Any]:
    requested_manifest = manifest_path.expanduser()
    try:
        requested_info = requested_manifest.lstat()
    except OSError as exc:
        raise AssetAuditError(f"cannot inspect asset-pack manifest: {exc}") from exc
    if stat.S_ISLNK(requested_info.st_mode):
        raise AssetAuditError(
            f"manifest must be a regular non-symlink file: {requested_manifest}"
        )
    manifest_path = requested_manifest.resolve(strict=True)
    raw_manifest, manifest = _load_json_mapping(manifest_path)
    pack_root = (root or manifest_path.parent).expanduser().resolve(strict=True)
    if not pack_root.is_dir():
        raise AssetAuditError(f"asset-pack root is not a directory: {pack_root}")

    findings: List[Dict[str, Any]] = []
    unknown_manifest_fields = sorted(set(manifest) - MANIFEST_KEYS)
    if unknown_manifest_fields:
        findings.append(
            _finding(
                "manifest.fields",
                "manifest contains unsupported fields",
                fields=unknown_manifest_fields,
            )
        )
    pack_id = _require_string(manifest, "pack_id", findings)
    if pack_id and not PACK_ID_RE.fullmatch(pack_id):
        findings.append(_finding("manifest.pack_id", "pack_id must use lowercase kebab-case", value=pack_id))
    version = _require_string(manifest, "version", findings)
    if version and len(version) > 128:
        findings.append(_finding("manifest.version", "version must contain at most 128 characters"))
    snapshot_date = _require_string(manifest, "snapshot_date", findings)
    if snapshot_date:
        try:
            parsed_date = datetime.date.fromisoformat(snapshot_date)
        except ValueError:
            parsed_date = None
        if parsed_date is None or snapshot_date != parsed_date.isoformat():
            findings.append(
                _finding(
                    "manifest.snapshot_date",
                    "snapshot_date must be a valid calendar date in YYYY-MM-DD form",
                )
            )
    kind = _require_string(manifest, "kind", findings)
    if kind and kind not in ALLOWED_KINDS:
        findings.append(_finding("manifest.kind", "unsupported asset-pack kind", allowed=sorted(ALLOWED_KINDS)))
    if manifest.get("distribution") != "optional-pack":
        findings.append(
            _finding(
                "manifest.distribution",
                "third-party assets must use the optional-pack distribution boundary",
            )
        )
    if manifest.get("default_enabled") is not False:
        findings.append(
            _finding(
                "manifest.default_enabled",
                "external asset packs must be explicitly opt-in",
            )
        )

    sources = manifest.get("sources")
    if not isinstance(sources, dict):
        findings.append(_finding("manifest.sources", "sources must be an object"))
        sources = {}
    if len(sources) > MAX_SOURCES:
        findings.append(_finding("manifest.sources.limit", f"source count exceeds {MAX_SOURCES}"))

    notice_cache: Dict[str, Tuple[str, int]] = {}
    evidence_cache: Dict[str, Tuple[str, int]] = {}
    licenses: Set[str] = set()
    source_licenses: Dict[str, Optional[str]] = {}
    trademark_sources = 0
    for source_id, source in sorted(sources.items()):
        if not isinstance(source_id, str):
            findings.append(_finding("source.id.type", "source id must be a string"))
            continue
        source_findings, license_id, trademarks, _source_kind = _validate_source(
            source_id,
            source,
            kind=kind,
            default_enabled=manifest.get("default_enabled"),
            root=pack_root,
            notice_cache=notice_cache,
            evidence_cache=evidence_cache,
        )
        findings.extend(source_findings)
        source_licenses[source_id] = license_id
        if license_id:
            licenses.add(license_id)
        if trademarks:
            trademark_sources += 1

    assets = manifest.get("assets")
    if not isinstance(assets, list):
        findings.append(_finding("manifest.assets", "assets must be an array"))
        assets = []
    if len(assets) > MAX_ASSETS:
        findings.append(_finding("manifest.assets.limit", f"asset count exceeds {MAX_ASSETS}"))

    seen_paths: Set[str] = set()
    asset_records: Dict[str, Tuple[int, Mapping[str, Any]]] = {}
    tree_digest = hashlib.sha256()
    total_bytes = 0
    verified_assets = 0
    for index, asset in enumerate(assets[: MAX_ASSETS + 1]):
        if not isinstance(asset, dict):
            findings.append(_finding("asset.type", "asset entry must be an object", index=index))
            continue
        unknown_asset_fields = sorted(set(asset) - ASSET_KEYS)
        if unknown_asset_fields:
            findings.append(
                _finding(
                    "asset.fields",
                    "asset contains unsupported fields",
                    index=index,
                    fields=unknown_asset_fields,
                )
            )
        source_id = asset.get("source")
        if not isinstance(source_id, str) or source_id not in sources:
            findings.append(_finding("asset.source", "asset refers to an unknown source", index=index))
        expected_sha = asset.get("sha256")
        if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
            findings.append(_finding("asset.sha256", "asset sha256 must be 64 lowercase hex characters", index=index))
            expected_sha = None
        try:
            relative = _safe_relative_path(asset.get("path"), field=f"assets[{index}].path")
            relative_string = relative.as_posix()
            if relative_string in seen_paths:
                findings.append(_finding("asset.path.duplicate", "asset path is duplicated", path=relative_string))
                continue
            seen_paths.add(relative_string)
            asset_records[relative_string] = (index, asset)
            absolute = _resolve_regular_file(pack_root, relative, field="asset path")
            actual_sha, size = _hash_regular_file(absolute)
        except (AssetAuditError, FileNotFoundError, OSError) as exc:
            findings.append(_finding("asset.path", str(exc), index=index))
            continue
        total_bytes += size
        if total_bytes > MAX_PACK_BYTES:
            findings.append(_finding("asset.pack_size", f"asset pack exceeds {MAX_PACK_BYTES} bytes"))
            break
        if expected_sha and actual_sha != expected_sha:
            findings.append(
                _finding(
                    "asset.digest",
                    "asset sha256 does not match the manifest",
                    path=relative_string,
                    expected=expected_sha,
                    actual=actual_sha,
                )
            )
            continue
        tree_digest.update(b"asset\0")
        tree_digest.update(relative_string.encode("utf-8"))
        tree_digest.update(b"\0")
        tree_digest.update(actual_sha.encode("ascii"))
        tree_digest.update(b"\0")
        tree_digest.update(str(size).encode("ascii"))
        tree_digest.update(b"\0")
        verified_assets += 1

    derived_graph: Dict[str, str] = {}
    for asset_path, (index, asset) in sorted(asset_records.items()):
        modifications = asset.get("modifications")
        valid_modifications = (
            isinstance(modifications, list)
            and bool(modifications)
            and all(isinstance(item, str) and item.strip() for item in modifications)
        )
        if modifications is not None and not valid_modifications:
            findings.append(
                _finding(
                    "asset.modifications",
                    "modifications must be a non-empty array of non-empty strings",
                    path=asset_path,
                )
            )
        raw_parent = asset.get("derived_from")
        if raw_parent is None:
            continue
        try:
            parent = _safe_relative_path(
                raw_parent, field=f"assets[{index}].derived_from"
            ).as_posix()
        except AssetAuditError as exc:
            findings.append(
                _finding("asset.derived_from", str(exc), path=asset_path)
            )
            continue
        if parent == asset_path:
            findings.append(
                _finding(
                    "asset.derived_from.self",
                    "asset cannot derive from itself",
                    path=asset_path,
                )
            )
            continue
        if parent not in asset_records:
            findings.append(
                _finding(
                    "asset.derived_from.missing",
                    "derived_from must name another declared asset",
                    path=asset_path,
                    derived_from=parent,
                )
            )
            continue
        derived_graph[asset_path] = parent
        source_id = asset.get("source")
        parent_source_id = asset_records[parent][1].get("source")
        source_license = (
            source_licenses.get(source_id) if isinstance(source_id, str) else None
        )
        parent_source_license = (
            source_licenses.get(parent_source_id)
            if isinstance(parent_source_id, str)
            else None
        )
        if (
            source_license in ATTRIBUTION_REQUIRED_LICENSES
            or parent_source_license in ATTRIBUTION_REQUIRED_LICENSES
        ) and not valid_modifications:
            findings.append(
                _finding(
                    "asset.derived_from.modifications",
                    "a CC-BY-derived asset must record its modifications",
                    path=asset_path,
                    derived_from=parent,
                )
            )

    for cycle in _derived_cycles(derived_graph):
        findings.append(
            _finding(
                "asset.derived_from.cycle",
                "derived_from cycle: " + " -> ".join(cycle + [cycle[0]]),
                path=cycle[0],
            )
        )

    supporting_records = dict(notice_cache)
    supporting_records.update(evidence_cache)
    supporting_bytes = sum(size for _sha256, size in supporting_records.values())
    if total_bytes + supporting_bytes > MAX_PACK_BYTES:
        findings.append(
            _finding(
                "asset.pack_size",
                f"asset pack plus license evidence exceeds {MAX_PACK_BYTES} bytes",
            )
        )
    for support_path, (support_sha, support_size) in sorted(supporting_records.items()):
        tree_digest.update(b"support\0")
        tree_digest.update(support_path.encode("utf-8"))
        tree_digest.update(b"\0")
        tree_digest.update(support_sha.encode("ascii"))
        tree_digest.update(b"\0")
        tree_digest.update(str(support_size).encode("ascii"))
        tree_digest.update(b"\0")

    severity_order = {"info": 0, "warning": 1, "error": 2}
    findings.sort(
        key=lambda item: (
            -severity_order.get(str(item.get("severity")), 2),
            str(item.get("code", "")),
            json.dumps(item.get("context", {}), sort_keys=True, ensure_ascii=False),
        )
    )
    errors = sum(item["severity"] == "error" for item in findings)
    warnings = sum(item["severity"] == "warning" for item in findings)
    return {
        "schemaVersion": 1,
        "kind": "open-deck-asset-audit",
        "status": "pass" if errors == 0 else "fail",
        "pack": {
            "id": pack_id,
            "version": version,
            "kind": kind,
            "snapshotDate": snapshot_date,
            "manifest": str(manifest_path),
            "root": str(pack_root),
            "manifestSha256": hashlib.sha256(raw_manifest).hexdigest(),
            "treeSha256": tree_digest.hexdigest(),
            "noticeSha256": _evidence_digest(notice_cache),
            "permissionEvidenceSha256": _evidence_digest(evidence_cache),
        },
        "receipt": {
            "sources": len(sources),
            "assetsDeclared": len(assets),
            "assetsVerified": verified_assets,
            "bytesVerified": total_bytes + supporting_bytes,
            "assetBytesVerified": total_bytes,
            "supportingBytesVerified": supporting_bytes,
            "licenses": sorted(licenses),
            "trademarkSources": trademark_sources,
            "noticeFiles": len(notice_cache),
            "permissionEvidenceFiles": len(evidence_cache),
            "errors": errors,
            "warnings": warnings,
        },
        "findings": findings,
    }


def _write_report(path: Path, report: Mapping[str, Any], *, force: bool) -> None:
    requested = path.expanduser()
    if not requested.name or requested.name in {".", ".."}:
        raise AssetAuditError(f"invalid report path: {path}")
    requested.parent.mkdir(parents=True, exist_ok=True)
    path = requested.parent.resolve(strict=True) / requested.name
    if path.is_symlink():
        raise AssetAuditError(f"report destination must not be a symbolic link: {path}")
    if path.exists() and not force:
        raise AssetAuditError(f"report already exists; pass --force to replace it: {path}")
    payload = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            if path.is_symlink():
                raise AssetAuditError(
                    f"report destination became a symbolic link: {path}"
                )
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise AssetAuditError(
                    f"report appeared during publication: {path}"
                ) from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit an optional Open Deck asset pack offline")
    parser.add_argument("manifest", type=Path, help="asset-pack manifest JSON")
    parser.add_argument("--root", type=Path, help="pack root (defaults to the manifest directory)")
    parser.add_argument("--report", type=Path, help="write the full JSON report atomically")
    parser.add_argument("--force", action="store_true", help="replace an existing report")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        report = audit_asset_pack(args.manifest, root=args.root)
        if args.report:
            _write_report(args.report, report, force=args.force)
    except (AssetAuditError, OSError) as exc:
        print(f"open-deck asset audit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
