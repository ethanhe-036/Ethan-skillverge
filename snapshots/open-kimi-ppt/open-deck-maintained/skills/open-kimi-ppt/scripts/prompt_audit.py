#!/usr/bin/env python3
"""Audit skill documents, progressive load sets, links, and repeated guidance."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


MAX_MANIFEST_BYTES = 1024 * 1024
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTS = 500
MAX_LOAD_SETS = 100
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")
TOKENISH_RE = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]|[^\s]", re.UNICODE)


class PromptAuditError(RuntimeError):
    pass


def _finding(code: str, message: str, *, severity: str = "error", **context: Any) -> Dict[str, Any]:
    value: Dict[str, Any] = {"severity": severity, "code": code, "message": message}
    if context:
        value["context"] = context
    return value


def _safe_relative(value: Any, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or len(value) > 4096 or "\\" in value:
        raise PromptAuditError(f"{field} must be a non-empty relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PromptAuditError(f"unsafe {field}: {value!r}")
    return path


def _read_regular(path: Path, limit: int) -> bytes:
    before = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise PromptAuditError(f"document must be a regular non-symlink file: {path}")
    if before.st_size > limit:
        raise PromptAuditError(f"document exceeds {limit} bytes: {path}")
    raw = path.read_bytes()
    after = path.lstat()
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(raw) != before.st_size
    ):
        raise PromptAuditError(f"document changed while it was read: {path}")
    return raw


def _token_count(text: str) -> Tuple[int, str]:
    try:
        import tiktoken  # type: ignore

        return len(tiktoken.get_encoding("o200k_base").encode(text)), "o200k_base"
    except (ImportError, AttributeError, KeyError, RuntimeError):
        lexical = len(TOKENISH_RE.findall(text))
        byte_estimate = math.ceil(len(text.encode("utf-8")) / 4)
        return max(lexical, byte_estimate), "deterministic-estimate"


def _normalize_paragraph(value: str) -> str:
    value = INLINE_CODE_RE.sub("`code`", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def _paragraphs(text: str, minimum_chars: int) -> Iterable[Tuple[int, str]]:
    without_code = FENCED_CODE_RE.sub("", text)
    line = 1
    for paragraph in PARAGRAPH_SPLIT_RE.split(without_code):
        normalized = _normalize_paragraph(paragraph)
        if len(normalized) >= minimum_chars and not normalized.startswith("|"):
            yield line, normalized
        line += paragraph.count("\n") + 2


def _local_markdown_links(text: str) -> Iterable[str]:
    for match in MARKDOWN_LINK_RE.finditer(text):
        target = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
        target = target.split("#", 1)[0]
        if not target or target.startswith(("http://", "https://", "mailto:", "data:")):
            continue
        yield target


def audit_prompt_manifest(manifest_path: Path, *, root: Optional[Path] = None) -> Dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve(strict=True)
    raw_manifest = _read_regular(manifest_path, MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(raw_manifest)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptAuditError(f"audit manifest is invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PromptAuditError("audit manifest root must be an object")
    skill_root = (root or manifest_path.parents[1]).expanduser().resolve(strict=True)

    findings: List[Dict[str, Any]] = []
    definitions = manifest.get("documents")
    if not isinstance(definitions, list):
        raise PromptAuditError("documents must be an array")
    if len(definitions) > MAX_DOCUMENTS:
        raise PromptAuditError(f"document count exceeds {MAX_DOCUMENTS}")

    documents: Dict[str, Dict[str, Any]] = {}
    paragraphs: Dict[str, List[Tuple[str, int]]] = {}
    minimum_duplicate_chars = manifest.get("minimumDuplicateChars", 180)
    if not isinstance(minimum_duplicate_chars, int) or not 80 <= minimum_duplicate_chars <= 5000:
        raise PromptAuditError("minimumDuplicateChars must be an integer from 80 to 5000")

    for index, definition in enumerate(definitions):
        if not isinstance(definition, dict):
            findings.append(_finding("document.type", "document entry must be an object", index=index))
            continue
        document_id = definition.get("id")
        if not isinstance(document_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,126}", document_id):
            findings.append(_finding("document.id", "invalid document id", index=index))
            continue
        if document_id in documents:
            findings.append(_finding("document.id.duplicate", "duplicate document id", id=document_id))
            continue
        try:
            relative = _safe_relative(definition.get("path"), field=f"documents[{index}].path")
            absolute = (skill_root / relative).resolve(strict=True)
            absolute.relative_to(skill_root)
            raw = _read_regular(absolute, MAX_DOCUMENT_BYTES)
            text = raw.decode("utf-8")
        except (PromptAuditError, FileNotFoundError, UnicodeDecodeError, ValueError, OSError) as exc:
            findings.append(_finding("document.read", str(exc), id=document_id))
            continue
        tokens, tokenizer = _token_count(text)
        lines = len(text.splitlines())
        maximum_tokens = definition.get("maxTokens")
        if not isinstance(maximum_tokens, int) or maximum_tokens <= 0:
            findings.append(_finding("document.budget", "maxTokens must be a positive integer", id=document_id))
            maximum_tokens = 0
        elif tokens > maximum_tokens:
            findings.append(
                _finding(
                    "document.budget.exceeded",
                    "document exceeds its token budget",
                    id=document_id,
                    tokens=tokens,
                    maxTokens=maximum_tokens,
                )
            )
        maximum_lines = definition.get("maxLines")
        if isinstance(maximum_lines, int) and lines > maximum_lines:
            findings.append(
                _finding(
                    "document.lines.exceeded",
                    "document exceeds its progressive-disclosure line budget",
                    id=document_id,
                    lines=lines,
                    maxLines=maximum_lines,
                )
            )
        documents[document_id] = {
            "id": document_id,
            "path": relative.as_posix(),
            "bytes": len(raw),
            "lines": lines,
            "tokens": tokens,
            "maxTokens": maximum_tokens,
            "tokenizer": tokenizer,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        for line, paragraph in _paragraphs(text, minimum_duplicate_chars):
            paragraph_hash = hashlib.sha256(paragraph.encode("utf-8")).hexdigest()
            paragraphs.setdefault(paragraph_hash, []).append((document_id, line))
        for target in _local_markdown_links(text):
            try:
                linked = (absolute.parent / target).resolve(strict=True)
                linked.relative_to(skill_root)
            except (FileNotFoundError, OSError, ValueError):
                findings.append(
                    _finding(
                        "document.link.broken",
                        "local Markdown link does not resolve inside the skill",
                        id=document_id,
                        target=target,
                    )
                )

    allow_duplicate_hashes = manifest.get("allowDuplicateParagraphs", [])
    if not isinstance(allow_duplicate_hashes, list) or not all(isinstance(item, str) for item in allow_duplicate_hashes):
        raise PromptAuditError("allowDuplicateParagraphs must be an array of SHA-256 strings")
    allow_duplicates = set(allow_duplicate_hashes)
    duplicate_groups = 0
    for paragraph_hash, locations in sorted(paragraphs.items()):
        if len(locations) < 2 or paragraph_hash in allow_duplicates:
            continue
        duplicate_groups += 1
        findings.append(
            _finding(
                "document.paragraph.duplicate",
                "long guidance paragraph is repeated",
                severity="warning",
                sha256=paragraph_hash,
                locations=[{"id": item[0], "line": item[1]} for item in locations],
            )
        )

    load_sets = manifest.get("loadSets")
    if not isinstance(load_sets, list):
        raise PromptAuditError("loadSets must be an array")
    if len(load_sets) > MAX_LOAD_SETS:
        raise PromptAuditError(f"load-set count exceeds {MAX_LOAD_SETS}")
    load_set_reports: List[Dict[str, Any]] = []
    seen_load_sets: Set[str] = set()
    for index, load_set in enumerate(load_sets):
        if not isinstance(load_set, dict):
            findings.append(_finding("load_set.type", "load-set entry must be an object", index=index))
            continue
        load_set_id = load_set.get("id")
        document_ids = load_set.get("documents")
        budget = load_set.get("maxTokens")
        if not isinstance(load_set_id, str) or not load_set_id or load_set_id in seen_load_sets:
            findings.append(_finding("load_set.id", "load-set id is missing or duplicated", index=index))
            continue
        seen_load_sets.add(load_set_id)
        if not isinstance(document_ids, list) or not all(isinstance(item, str) for item in document_ids):
            findings.append(_finding("load_set.documents", "load-set documents must be string ids", id=load_set_id))
            continue
        missing = [item for item in document_ids if item not in documents]
        if missing:
            findings.append(_finding("load_set.document.missing", "load-set references unavailable documents", id=load_set_id, documents=missing))
        total = sum(documents[item]["tokens"] for item in document_ids if item in documents)
        if not isinstance(budget, int) or budget <= 0:
            findings.append(_finding("load_set.budget", "load-set maxTokens must be positive", id=load_set_id))
            budget = 0
        elif total > budget:
            findings.append(
                _finding(
                    "load_set.budget.exceeded",
                    "load set exceeds its token budget",
                    id=load_set_id,
                    tokens=total,
                    maxTokens=budget,
                )
            )
        load_set_reports.append(
            {"id": load_set_id, "documents": document_ids, "tokens": total, "maxTokens": budget}
        )

    order = {"info": 0, "warning": 1, "error": 2}
    findings.sort(key=lambda item: (-order.get(item["severity"], 2), item["code"], json.dumps(item.get("context", {}), sort_keys=True)))
    errors = sum(item["severity"] == "error" for item in findings)
    warnings = sum(item["severity"] == "warning" for item in findings)
    source_digest = hashlib.sha256()
    source_digest.update(hashlib.sha256(raw_manifest).digest())
    for document in sorted(documents.values(), key=lambda item: item["id"]):
        source_digest.update(document["id"].encode("utf-8"))
        source_digest.update(bytes.fromhex(document["sha256"]))
    return {
        "schemaVersion": 1,
        "kind": "open-deck-prompt-audit",
        "status": "pass" if errors == 0 else "fail",
        "sourceSha256": source_digest.hexdigest(),
        "documents": sorted(documents.values(), key=lambda item: item["id"]),
        "loadSets": load_set_reports,
        "receipt": {
            "documents": len(documents),
            "loadSets": len(load_set_reports),
            "duplicateGroups": duplicate_groups,
            "errors": errors,
            "warnings": warnings,
        },
        "findings": findings,
    }


def _write_report(path: Path, report: Mapping[str, Any], *, force: bool) -> None:
    requested = path.expanduser()
    if not requested.name or requested.name in {".", ".."}:
        raise PromptAuditError(f"invalid report path: {path}")
    requested.parent.mkdir(parents=True, exist_ok=True)
    path = requested.parent.resolve(strict=True) / requested.name
    if path.is_symlink():
        raise PromptAuditError(f"report destination must not be a symbolic link: {path}")
    if path.exists() and not force:
        raise PromptAuditError(f"report already exists; pass --force to replace it: {path}")
    payload = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            if path.is_symlink():
                raise PromptAuditError(
                    f"report destination became a symbolic link: {path}"
                )
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise PromptAuditError(
                    f"report appeared during publication: {path}"
                ) from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Open Deck skill context budgets and references")
    parser.add_argument("manifest", type=Path, nargs="?", default=Path(__file__).with_name("prompt_audit_manifest.json"))
    parser.add_argument("--root", type=Path, help="skill root (normally auto-detected)")
    parser.add_argument("--report", type=Path, help="write the full JSON report atomically")
    parser.add_argument("--force", action="store_true", help="replace an existing report")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        report = audit_prompt_manifest(args.manifest, root=args.root)
        if args.report:
            _write_report(args.report, report, force=args.force)
    except (PromptAuditError, OSError) as exc:
        print(f"open-deck prompt audit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
