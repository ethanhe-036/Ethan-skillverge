#!/usr/bin/env python3
"""Inspect, validate, and make narrowly-scoped native edits to PPTX files.

This module is deliberately independent of the browser-based exporter.  It
works directly with the OPC ZIP package and supports only two mutation types:

* plain text in an existing ``p:sp`` shape, selected by slide and shape id/name;
* plain text in an existing notes-body placeholder.

The fill plan is a JSON object with this shape (slide numbers are one-based)::

    {
      "replacements": [
        {"slide": 1, "shape_id": 2, "text": "New title", "lang": "en-US"},
        {"slide": 2, "shape_name": "Subtitle 3", "text": "Plain text"}
      ],
      "notes": [
        {"slide": 1, "text": "Speaker notes", "lang": "zh-CN"}
      ]
    }

Tables, charts, pictures, groups, connectors, OLE objects, SmartArt, and other
graphic frames are inventoried but intentionally rejected as edit targets.
Before publication, the candidate is checked as a ZIP, every XML/relationship
part is parsed, every internal relationship target is resolved, and each
slide's normalized ``p:timing`` fingerprint is compared with the source.  The
candidate is then atomically published from the destination directory.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import posixpath
import re
import shutil
import stat
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
from pathlib import Path, PurePosixPath
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Set, Tuple, Union
from urllib.parse import unquote, urlsplit


P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DGM_NS = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
XML_NS = "http://www.w3.org/XML/1998/namespace"

NS = {"p": P_NS, "a": A_NS, "r": R_NS, "c": C_NS, "dgm": DGM_NS}

# Register the stable prefixes used in the parts this module may rewrite.
for _prefix, _uri in (
    ("p", P_NS),
    ("a", A_NS),
    ("r", R_NS),
    ("c", C_NS),
    ("dgm", DGM_NS),
    ("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006"),
    ("p14", "http://schemas.microsoft.com/office/powerpoint/2010/main"),
    ("p15", "http://schemas.microsoft.com/office/powerpoint/2012/main"),
    ("p16", "http://schemas.microsoft.com/office/powerpoint/2015/main"),
):
    ET.register_namespace(_prefix, _uri)


MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 20_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_XML_BYTES = 32 * 1024 * 1024
MAX_XML_NODES = 300_000
MAX_XML_DEPTH = 256
MAX_FILL_PLAN_BYTES = 8 * 1024 * 1024
MAX_FILL_EDITS = 10_000
MAX_FILL_TEXT_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES_PER_EDIT = 2 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024

INSPECT_SCHEMA = "open-kimi-ppt.pptx-native.inspect.v1"
VALIDATE_SCHEMA = "open-kimi-ppt.pptx-native.validate.v1"
FILL_SCHEMA = "open-kimi-ppt.pptx-native.fill.v1"
TIMING_FINGERPRINT_ALGORITHM = "sha256-elementtree-p-timing-v1"

XML_PART_SUFFIXES = (".xml", ".rels", ".vml")
SHAPE_TAGS = {
    f"{{{P_NS}}}sp",
    f"{{{P_NS}}}pic",
    f"{{{P_NS}}}graphicFrame",
    f"{{{P_NS}}}grpSp",
    f"{{{P_NS}}}cxnSp",
    f"{{{P_NS}}}contentPart",
}
CHART_TAG_NAMES = {
    "area3DChart",
    "areaChart",
    "bar3DChart",
    "barChart",
    "bubbleChart",
    "doughnutChart",
    "line3DChart",
    "lineChart",
    "ofPieChart",
    "pie3DChart",
    "pieChart",
    "radarChart",
    "scatterChart",
    "stockChart",
    "surface3DChart",
    "surfaceChart",
}
LANGUAGE_TAG_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
ROOT_START_TAG_RE = re.compile(
    br"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?[A-Za-z_][A-Za-z0-9_.-]*\b[^>]*>"
)
XMLNS_RE = re.compile(
    br"\sxmlns(?::([A-Za-z_][A-Za-z0-9_.-]*))?\s*=\s*(['\"])(.*?)\2"
)


class PptxNativeError(RuntimeError):
    """Base error for deterministic PPTX sidecar failures."""


class ValidationError(PptxNativeError):
    """The OPC/OOXML package failed structural validation."""


class FillPlanError(PptxNativeError):
    """The requested fill plan is malformed or ambiguous."""


class UnsupportedObjectError(FillPlanError):
    """The plan selected an object this conservative editor will not mutate."""


class _ForbiddenXmlDeclaration(Exception):
    pass


class Relationship(NamedTuple):
    relationship_id: str
    relationship_type: str
    target: str
    target_mode: Optional[str]
    resolved_target: Optional[str]

    @property
    def kind(self) -> str:
        return self.relationship_type.rstrip("/").rsplit("/", 1)[-1]

    @property
    def external(self) -> bool:
        return (self.target_mode or "").lower() == "external"


class PackageSnapshot(NamedTuple):
    path: Path
    infos: Mapping[str, zipfile.ZipInfo]
    archive_comment: bytes

    @property
    def names(self) -> FrozenSet[str]:
        return frozenset(self.infos)

    def read(self, part_name: str) -> bytes:
        info = self.infos.get(part_name)
        if info is None:
            raise ValidationError(f"missing package part: {part_name}")
        if info.file_size > MAX_XML_BYTES and _is_xml_part(part_name):
            raise ValidationError(
                f"XML part exceeds {MAX_XML_BYTES} bytes: {part_name}"
            )
        try:
            with zipfile.ZipFile(self.path, "r") as archive:
                return archive.read(part_name)
        except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as error:
            raise ValidationError(f"could not read package part {part_name}: {error}") from error


def _q(namespace: str, local_name: str) -> str:
    return f"{{{namespace}}}{local_name}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _is_xml_part(name: str) -> bool:
    lower = name.lower()
    return name == "[Content_Types].xml" or lower.endswith(XML_PART_SUFFIXES)


def _natural_part_key(name: str) -> Tuple[Any, ...]:
    return tuple(
        int(piece) if piece.isdigit() else piece.casefold()
        for piece in re.split(r"(\d+)", name)
    )


def _validate_member_name(name: str) -> None:
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        raise ValidationError(f"unsafe or non-canonical ZIP member name: {name!r}")
    canonical_target = name[:-1] if name.endswith("/") else name
    if not canonical_target:
        raise ValidationError(f"unsafe or non-canonical ZIP member name: {name!r}")
    path = PurePosixPath(canonical_target)
    if any(piece in ("", ".", "..") for piece in path.parts):
        raise ValidationError(f"unsafe or non-canonical ZIP member name: {name!r}")
    if posixpath.normpath(canonical_target) != canonical_target:
        raise ValidationError(f"non-canonical ZIP member name: {name!r}")


def _open_package(path: Union[str, os.PathLike[str]]) -> PackageSnapshot:
    source = Path(path)
    try:
        archive_size = source.stat().st_size
    except OSError as error:
        raise ValidationError(f"could not stat PPTX {source}: {error}") from error
    if archive_size > MAX_ARCHIVE_BYTES:
        raise ValidationError(
            f"PPTX exceeds the {MAX_ARCHIVE_BYTES}-byte compressed-size limit"
        )
    try:
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS:
                raise ValidationError(f"PPTX contains more than {MAX_MEMBERS} members")
            names: Dict[str, zipfile.ZipInfo] = {}
            casefolded: Dict[str, str] = {}
            total_uncompressed = 0
            for info in infos:
                _validate_member_name(info.filename)
                if info.filename in names:
                    raise ValidationError(f"duplicate ZIP member: {info.filename}")
                folded = info.filename.casefold()
                if folded in casefolded:
                    raise ValidationError(
                        "case-colliding ZIP members: "
                        f"{casefolded[folded]} and {info.filename}"
                    )
                casefolded[folded] = info.filename
                if info.flag_bits & 0x1:
                    raise ValidationError(f"encrypted ZIP member is unsupported: {info.filename}")
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                if unix_mode and stat.S_ISLNK(unix_mode):
                    raise ValidationError(f"symbolic-link ZIP member is unsupported: {info.filename}")
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise ValidationError(
                        "PPTX exceeds the uncompressed-size safety limit"
                    )
                names[info.filename] = copy.copy(info)
            bad_member = archive.testzip()
            if bad_member is not None:
                raise ValidationError(f"ZIP CRC/decompression failure in {bad_member}")
            comment = archive.comment
    except ValidationError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ValidationError(f"invalid PPTX ZIP package: {error}") from error
    return PackageSnapshot(source, names, comment)


def _parse_xml(data: bytes, part_name: str) -> ET.Element:
    if len(data) > MAX_XML_BYTES:
        raise ValidationError(f"XML part exceeds {MAX_XML_BYTES} bytes: {part_name}")
    # Parse the complete bounded byte stream with declaration callbacks before
    # building an ElementTree.  This catches declarations after long padding
    # and in UTF-16/UTF-32 documents, which a byte-prefix search cannot do.
    declaration_scanner = expat.ParserCreate()

    def reject_declaration(*_arguments: Any) -> None:
        raise _ForbiddenXmlDeclaration

    declaration_scanner.StartDoctypeDeclHandler = reject_declaration
    declaration_scanner.EntityDeclHandler = reject_declaration
    try:
        declaration_scanner.Parse(data, True)
    except _ForbiddenXmlDeclaration:
        raise ValidationError(f"DTD/entity declarations are not allowed: {part_name}")
    except expat.ExpatError as error:
        raise ValidationError(f"malformed XML in {part_name}: {error}") from error
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValidationError(f"malformed XML in {part_name}: {error}") from error
    count = 0
    stack: List[Tuple[ET.Element, int]] = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        count += 1
        if count > MAX_XML_NODES:
            raise ValidationError(f"XML node limit exceeded in {part_name}")
        if depth > MAX_XML_DEPTH:
            raise ValidationError(f"XML depth limit exceeded in {part_name}")
        stack.extend((child, depth + 1) for child in list(element))
    return root


def _read_xml(package: PackageSnapshot, part_name: str) -> ET.Element:
    return _parse_xml(package.read(part_name), part_name)


def _relationship_part(source_part: Optional[str]) -> str:
    if source_part is None:
        return "_rels/.rels"
    source = PurePosixPath(source_part)
    return str(source.parent / "_rels" / f"{source.name}.rels")


def _source_for_relationship_part(relationship_part: str) -> Optional[str]:
    if relationship_part == "_rels/.rels":
        return None
    path = PurePosixPath(relationship_part)
    if len(path.parts) < 3 or path.parent.name != "_rels" or not path.name.endswith(".rels"):
        raise ValidationError(f"invalid relationship part path: {relationship_part}")
    return str(path.parent.parent / path.name[: -len(".rels")])


def _resolve_internal_target(source_part: Optional[str], target: str) -> str:
    if not target or "\\" in target or "\x00" in target:
        raise ValidationError(f"invalid relationship target: {target!r}")
    lower_target = target.lower()
    if "%2f" in lower_target or "%5c" in lower_target:
        raise ValidationError(f"encoded path separator in relationship target: {target!r}")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValidationError(f"invalid internal relationship target URI: {target!r}")
    decoded = unquote(parsed.path)
    if not decoded or "\\" in decoded or "\x00" in decoded:
        raise ValidationError(f"invalid relationship target: {target!r}")
    if decoded.startswith("/"):
        joined = decoded.lstrip("/")
    else:
        base = "" if source_part is None else str(PurePosixPath(source_part).parent)
        joined = posixpath.join(base, decoded)
    normalized = posixpath.normpath(joined)
    if normalized in ("", ".") or normalized == ".." or normalized.startswith("../"):
        raise ValidationError(f"relationship target escapes the package: {target!r}")
    _validate_member_name(normalized)
    return normalized


def _read_relationships(
    package: PackageSnapshot,
    source_part: Optional[str],
    *,
    required: bool = False,
) -> Dict[str, Relationship]:
    rel_part = _relationship_part(source_part)
    if rel_part not in package.names:
        if required:
            raise ValidationError(f"missing relationship part: {rel_part}")
        return {}
    root = _read_xml(package, rel_part)
    if _local_name(root.tag) != "Relationships":
        raise ValidationError(f"unexpected relationship root in {rel_part}")
    relationships: Dict[str, Relationship] = {}
    for element in list(root):
        if _local_name(element.tag) != "Relationship":
            continue
        relationship_id = element.get("Id", "")
        relationship_type = element.get("Type", "")
        target = element.get("Target", "")
        target_mode = element.get("TargetMode")
        if not relationship_id or not relationship_type or not target:
            raise ValidationError(f"incomplete relationship in {rel_part}")
        if relationship_id in relationships:
            raise ValidationError(
                f"duplicate relationship id {relationship_id!r} in {rel_part}"
            )
        external = (target_mode or "").lower() == "external"
        if target_mode and not external:
            raise ValidationError(
                f"unsupported TargetMode {target_mode!r} in {rel_part}"
            )
        resolved = None if external else _resolve_internal_target(source_part, target)
        relationships[relationship_id] = Relationship(
            relationship_id, relationship_type, target, target_mode, resolved
        )
    return relationships


def _content_types(package: PackageSnapshot) -> Tuple[Dict[str, str], Dict[str, str]]:
    part = "[Content_Types].xml"
    if part not in package.names:
        raise ValidationError("missing [Content_Types].xml")
    root = _read_xml(package, part)
    if _local_name(root.tag) != "Types":
        raise ValidationError("unexpected [Content_Types].xml root")
    defaults: Dict[str, str] = {}
    overrides: Dict[str, str] = {}
    for element in list(root):
        local = _local_name(element.tag)
        content_type = element.get("ContentType", "")
        if not content_type:
            raise ValidationError("content-type entry is missing ContentType")
        if local == "Default":
            extension = element.get("Extension", "").casefold()
            if not extension or extension in defaults:
                raise ValidationError(f"invalid/duplicate content-type default: {extension!r}")
            defaults[extension] = content_type
        elif local == "Override":
            raw_name = element.get("PartName", "")
            if not raw_name.startswith("/"):
                raise ValidationError(f"invalid content-type override name: {raw_name!r}")
            name = unquote(raw_name[1:])
            _validate_member_name(name)
            if name in overrides:
                raise ValidationError(f"duplicate content-type override: {name}")
            if name not in package.names:
                raise ValidationError(f"content-type override targets missing part: {name}")
            overrides[name] = content_type
    for name in package.names:
        if name == part or name.endswith("/"):
            continue
        basename = PurePosixPath(name).name
        extension = PurePosixPath(name).suffix.lstrip(".").casefold()
        # OPC's root relationship part is literally ``_rels/.rels``; pathlib
        # treats that dot-prefixed basename as extensionless even though the
        # package content-type convention treats it as the ``rels`` extension.
        if not extension and basename.startswith(".") and basename.count(".") == 1:
            extension = basename[1:].casefold()
        if name not in overrides and extension not in defaults:
            raise ValidationError(f"package part has no content type: {name}")
    return defaults, overrides


def _relationship_by_kind(
    relationships: Mapping[str, Relationship], kind: str
) -> List[Relationship]:
    return [relationship for relationship in relationships.values() if relationship.kind == kind]


def _presentation_part(package: PackageSnapshot) -> str:
    root_relationships = _read_relationships(package, None, required=True)
    office_documents = _relationship_by_kind(root_relationships, "officeDocument")
    if len(office_documents) != 1 or office_documents[0].external:
        raise ValidationError("package must have one internal officeDocument relationship")
    target = office_documents[0].resolved_target
    assert target is not None
    if target not in package.names:
        raise ValidationError(f"officeDocument target is missing: {target}")
    return target


def _ordered_parts(
    root: ET.Element,
    list_path: str,
    item_tag: str,
    relationships: Mapping[str, Relationship],
    expected_kind: str,
) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()
    container = root.find(list_path, NS)
    if container is None:
        return result
    for item in container.findall(item_tag, NS):
        relationship_id = item.get(_q(R_NS, "id"), "")
        relationship = relationships.get(relationship_id)
        if relationship is None:
            raise ValidationError(f"unresolved relationship id {relationship_id!r}")
        if relationship.kind != expected_kind or relationship.external:
            raise ValidationError(
                f"relationship {relationship_id!r} is not an internal {expected_kind}"
            )
        target = relationship.resolved_target
        assert target is not None
        if target in seen:
            raise ValidationError(f"duplicate {expected_kind} target in presentation: {target}")
        seen.add(target)
        result.append(target)
    return result


def _validate_package(
    path: Union[str, os.PathLike[str]]
) -> Tuple[PackageSnapshot, str, ET.Element, List[str], List[str]]:
    package = _open_package(path)
    _content_types(package)
    # Parse every XML-bearing part, not only the parts this editor understands.
    for name in sorted(package.names, key=_natural_part_key):
        if _is_xml_part(name):
            _read_xml(package, name)
    # Validate every relationship part, including relationships the inspector
    # does not semantically understand.
    for name in sorted(package.names, key=_natural_part_key):
        if not name.lower().endswith(".rels"):
            continue
        source = _source_for_relationship_part(name)
        if source is not None and source not in package.names:
            raise ValidationError(f"relationship source part is missing: {source}")
        relationships = _read_relationships(package, source, required=True)
        for relationship in relationships.values():
            if not relationship.external:
                target = relationship.resolved_target
                assert target is not None
                if target not in package.names:
                    raise ValidationError(
                        f"relationship {relationship.relationship_id!r} from "
                        f"{source or '/'} targets missing part {target}"
                    )
    presentation_part = _presentation_part(package)
    presentation = _read_xml(package, presentation_part)
    if presentation.tag != _q(P_NS, "presentation"):
        raise ValidationError(f"officeDocument is not a PresentationML presentation: {presentation_part}")
    presentation_relationships = _read_relationships(
        package, presentation_part, required=True
    )
    slides = _ordered_parts(
        presentation,
        "p:sldIdLst",
        "p:sldId",
        presentation_relationships,
        "slide",
    )
    masters = _ordered_parts(
        presentation,
        "p:sldMasterIdLst",
        "p:sldMasterId",
        presentation_relationships,
        "slideMaster",
    )
    for slide_part in slides:
        if slide_part not in package.names:
            raise ValidationError(f"presentation references missing slide: {slide_part}")
        slide = _read_xml(package, slide_part)
        if slide.tag != _q(P_NS, "sld"):
            raise ValidationError(f"slide relationship targets non-slide XML: {slide_part}")
        slide_rels = _read_relationships(package, slide_part, required=True)
        layouts = _relationship_by_kind(slide_rels, "slideLayout")
        if len(layouts) != 1 or layouts[0].external:
            raise ValidationError(f"slide must have exactly one internal slideLayout: {slide_part}")
    for master_part in masters:
        if master_part not in package.names:
            raise ValidationError(f"presentation references missing master: {master_part}")
        if _read_xml(package, master_part).tag != _q(P_NS, "sldMaster"):
            raise ValidationError(f"slideMaster relationship targets wrong XML: {master_part}")
    return package, presentation_part, presentation, slides, masters


def _timing_fingerprint(root: ET.Element) -> Optional[str]:
    timing = root.find("p:timing", NS)
    if timing is None:
        return None
    return hashlib.sha256(ET.tostring(timing, encoding="utf-8")).hexdigest()


def _timing_fingerprints(
    package: PackageSnapshot, slide_parts: Sequence[str]
) -> Dict[str, Optional[str]]:
    return {
        part: _timing_fingerprint(_read_xml(package, part)) for part in slide_parts
    }


def validate_pptx(path: Union[str, os.PathLike[str]]) -> Dict[str, Any]:
    """Return a machine-readable OPC/OOXML validation report."""

    source = Path(path)
    report: Dict[str, Any] = {
        "schema": VALIDATE_SCHEMA,
        "file": str(source),
        "status": "invalid",
        "errors": [],
        "warnings": [],
        "timing_fingerprint_algorithm": TIMING_FINGERPRINT_ALGORITHM,
    }
    try:
        package, presentation_part, _presentation, slides, masters = _validate_package(source)
        layouts = sorted(
            (
                name
                for name in package.names
                if "/slideLayouts/" in f"/{name}" and name.lower().endswith(".xml")
            ),
            key=_natural_part_key,
        )
        report.update(
            status="valid",
            package={
                "parts": len(package.names),
                "presentation_part": presentation_part,
                "slides": len(slides),
                "masters": len(masters),
                "layouts": len(layouts),
            },
            timing_fingerprints=_timing_fingerprints(package, slides),
        )
    except (PptxNativeError, OSError, ValueError) as error:
        report["errors"].append(str(error))
    return report


def _shape_identity(element: ET.Element) -> Tuple[Optional[int], Optional[str]]:
    container_by_tag = {
        _q(P_NS, "sp"): "p:nvSpPr/p:cNvPr",
        _q(P_NS, "pic"): "p:nvPicPr/p:cNvPr",
        _q(P_NS, "graphicFrame"): "p:nvGraphicFramePr/p:cNvPr",
        _q(P_NS, "grpSp"): "p:nvGrpSpPr/p:cNvPr",
        _q(P_NS, "cxnSp"): "p:nvCxnSpPr/p:cNvPr",
        _q(P_NS, "contentPart"): "p:nvContentPartPr/p:cNvPr",
    }
    path = container_by_tag.get(element.tag)
    non_visual = element.find(path, NS) if path else None
    if non_visual is None:
        return None, None
    raw_id = non_visual.get("id")
    shape_id = int(raw_id) if raw_id and raw_id.isdigit() else None
    return shape_id, non_visual.get("name")


def _iter_shape_elements(
    container: ET.Element, parent_group_id: Optional[int] = None
) -> Iterable[Tuple[ET.Element, Optional[int]]]:
    for child in list(container):
        if child.tag not in SHAPE_TAGS:
            continue
        yield child, parent_group_id
        if child.tag == _q(P_NS, "grpSp"):
            group_id, _name = _shape_identity(child)
            yield from _iter_shape_elements(child, group_id)


def _text_body_text(text_body: Optional[ET.Element]) -> str:
    if text_body is None:
        return ""
    paragraphs: List[str] = []
    for paragraph in text_body.findall("a:p", NS):
        fragments: List[str] = []
        for element in paragraph.iter():
            if element.tag == _q(A_NS, "t"):
                fragments.append(element.text or "")
            elif element.tag == _q(A_NS, "br"):
                fragments.append("\n")
        paragraphs.append("".join(fragments))
    return "\n".join(paragraphs)


def _placeholder_type(element: ET.Element) -> Optional[str]:
    placeholder = element.find("p:nvSpPr/p:nvPr/p:ph", NS)
    if placeholder is None:
        return None
    return placeholder.get("type", "obj")


def _graphic_frame_type(element: ET.Element) -> Tuple[str, Optional[str]]:
    graphic_data = element.find("a:graphic/a:graphicData", NS)
    if graphic_data is None:
        return "graphic_frame", None
    uri = graphic_data.get("uri")
    if graphic_data.find("a:tbl", NS) is not None:
        return "table", uri
    if graphic_data.find("c:chart", NS) is not None:
        return "classic_chart", uri
    if graphic_data.find("dgm:relIds", NS) is not None:
        return "smartart", uri
    if any(_local_name(child.tag) in ("chart", "chartSpace") for child in graphic_data):
        return "extended_chart", uri
    if any(_local_name(child.tag) in ("oleObj", "oleObject") for child in graphic_data):
        return "ole_object", uri
    return "graphic_frame", uri


def _shape_type(element: ET.Element) -> Tuple[str, Optional[str]]:
    if element.tag == _q(P_NS, "sp"):
        placeholder = _placeholder_type(element)
        if placeholder is not None:
            return "placeholder", placeholder
        geometry = element.find("p:spPr/a:prstGeom", NS)
        return "text_shape" if element.find("p:txBody", NS) is not None else "shape", (
            geometry.get("prst") if geometry is not None else None
        )
    if element.tag == _q(P_NS, "pic"):
        return "picture", None
    if element.tag == _q(P_NS, "graphicFrame"):
        return _graphic_frame_type(element)
    if element.tag == _q(P_NS, "grpSp"):
        return "group", None
    if element.tag == _q(P_NS, "cxnSp"):
        return "connector", None
    if element.tag == _q(P_NS, "contentPart"):
        return "content_part", None
    return _local_name(element.tag), None


def _table_inventory(element: ET.Element, shape_id: Optional[int], name: Optional[str]) -> Dict[str, Any]:
    table = element.find("a:graphic/a:graphicData/a:tbl", NS)
    assert table is not None
    rows = table.findall("a:tr", NS)
    cell_rows: List[List[str]] = []
    for row in rows:
        cell_rows.append(
            [_text_body_text(cell.find("a:txBody", NS)) for cell in row.findall("a:tc", NS)]
        )
    return {
        "shape_id": shape_id,
        "name": name,
        "rows": len(cell_rows),
        "columns": max((len(row) for row in cell_rows), default=0),
        "cells": cell_rows,
    }


def _cache_values(container: Optional[ET.Element]) -> List[str]:
    if container is None:
        return []
    cache = None
    for path in ("c:strRef/c:strCache", "c:numRef/c:numCache", "c:strLit", "c:numLit"):
        cache = container.find(path, NS)
        if cache is not None:
            break
    if cache is None:
        direct = container.find("c:v", NS)
        return [direct.text or ""] if direct is not None else []
    indexed: List[Tuple[int, str]] = []
    for point in cache.findall("c:pt", NS):
        raw_index = point.get("idx", "0")
        index = int(raw_index) if raw_index.isdigit() else len(indexed)
        value = point.find("c:v", NS)
        indexed.append((index, value.text or "" if value is not None else ""))
    return [value for _index, value in sorted(indexed)]


def _series_inventory(series: ET.Element) -> Dict[str, Any]:
    title_container = series.find("c:tx", NS)
    title_values = _cache_values(title_container)
    if not title_values and title_container is not None:
        direct = title_container.find("c:v", NS)
        if direct is not None:
            title_values = [direct.text or ""]
    categories = series.find("c:cat", NS)
    if categories is None:
        categories = series.find("c:xVal", NS)
    values = series.find("c:val", NS)
    if values is None:
        values = series.find("c:yVal", NS)
    formula_nodes = series.findall(".//c:f", NS)
    return {
        "name": title_values[0] if title_values else None,
        "categories": _cache_values(categories),
        "values": _cache_values(values),
        "formulas": [node.text or "" for node in formula_nodes],
    }


def _chart_inventory(
    package: PackageSnapshot,
    source_part: str,
    element: ET.Element,
    relationships: Mapping[str, Relationship],
    shape_id: Optional[int],
    name: Optional[str],
) -> Dict[str, Any]:
    chart_reference = element.find("a:graphic/a:graphicData/c:chart", NS)
    assert chart_reference is not None
    relationship_id = chart_reference.get(_q(R_NS, "id"), "")
    relationship = relationships.get(relationship_id)
    if relationship is None or relationship.external or relationship.kind != "chart":
        raise ValidationError(
            f"classic chart {name or shape_id!r} has an invalid chart relationship in {source_part}"
        )
    chart_part = relationship.resolved_target
    assert chart_part is not None
    chart_root = _read_xml(package, chart_part)
    plot_area = chart_root.find("c:chart/c:plotArea", NS)
    chart_types: List[str] = []
    series: List[Dict[str, Any]] = []
    if plot_area is not None:
        for child in list(plot_area):
            local = _local_name(child.tag)
            if local not in CHART_TAG_NAMES:
                continue
            chart_types.append(local)
            series.extend(_series_inventory(item) for item in child.findall("c:ser", NS))
    return {
        "shape_id": shape_id,
        "name": name,
        "relationship_id": relationship_id,
        "part": chart_part,
        "chart_types": chart_types,
        "series": series,
    }


def _shape_inventory(
    package: PackageSnapshot,
    part_name: str,
    root: ET.Element,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    relationships = _read_relationships(package, part_name)
    shape_tree = root.find("p:cSld/p:spTree", NS)
    if shape_tree is None:
        return [], [], []
    shapes: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    charts: List[Dict[str, Any]] = []
    for element, parent_group_id in _iter_shape_elements(shape_tree):
        shape_id, name = _shape_identity(element)
        object_type, subtype = _shape_type(element)
        text_body = element.find("p:txBody", NS)
        item: Dict[str, Any] = {
            "id": shape_id,
            "name": name,
            "type": object_type,
            "text": _text_body_text(text_body),
        }
        if subtype is not None:
            if object_type == "placeholder":
                item["placeholder_type"] = subtype
            elif object_type in ("graphic_frame", "smartart", "extended_chart", "ole_object"):
                item["graphic_uri"] = subtype
            else:
                item["geometry"] = subtype
        if parent_group_id is not None:
            item["parent_group_id"] = parent_group_id
        shapes.append(item)
        if object_type == "table":
            tables.append(_table_inventory(element, shape_id, name))
        elif object_type == "classic_chart":
            charts.append(
                _chart_inventory(
                    package, part_name, element, relationships, shape_id, name
                )
            )
    return shapes, tables, charts


def _single_internal_relationship_target(
    package: PackageSnapshot, source_part: str, kind: str
) -> Optional[str]:
    relationships = _read_relationships(package, source_part)
    matches = _relationship_by_kind(relationships, kind)
    if not matches:
        return None
    if len(matches) != 1 or matches[0].external:
        raise ValidationError(f"{source_part} has ambiguous {kind} relationships")
    return matches[0].resolved_target


def _notes_inventory(package: PackageSnapshot, slide_part: str) -> Optional[Dict[str, Any]]:
    notes_part = _single_internal_relationship_target(package, slide_part, "notesSlide")
    if notes_part is None:
        return None
    root = _read_xml(package, notes_part)
    if root.tag != _q(P_NS, "notes"):
        raise ValidationError(f"notesSlide relationship targets wrong XML: {notes_part}")
    shapes, tables, charts = _shape_inventory(package, notes_part, root)
    body_texts: List[str] = []
    tree = root.find("p:cSld/p:spTree", NS)
    if tree is not None:
        for element, _parent in _iter_shape_elements(tree):
            if element.tag != _q(P_NS, "sp"):
                continue
            if _placeholder_type(element) == "body":
                body_texts.append(_text_body_text(element.find("p:txBody", NS)))
    return {
        "part": notes_part,
        "text": "\n".join(body_texts),
        "shapes": shapes,
        "tables": tables,
        "charts": charts,
    }


def _layout_parts(
    package: PackageSnapshot, masters: Sequence[str]
) -> Tuple[List[str], Dict[str, Optional[str]], Dict[str, List[str]]]:
    ordered: List[str] = []
    layout_master: Dict[str, Optional[str]] = {}
    master_layouts: Dict[str, List[str]] = {}
    for master in masters:
        targets = [
            relationship.resolved_target
            for relationship in _relationship_by_kind(
                _read_relationships(package, master), "slideLayout"
            )
            if not relationship.external
        ]
        concrete = [target for target in targets if target is not None]
        master_layouts[master] = concrete
        for target in concrete:
            if target not in ordered:
                ordered.append(target)
            layout_master[target] = master
    discovered = sorted(
        (
            name
            for name in package.names
            if "/slideLayouts/" in f"/{name}" and name.lower().endswith(".xml")
        ),
        key=_natural_part_key,
    )
    for layout in discovered:
        if layout not in ordered:
            ordered.append(layout)
        layout_master.setdefault(
            layout, _single_internal_relationship_target(package, layout, "slideMaster")
        )
    return ordered, layout_master, master_layouts


def inspect_pptx(path: Union[str, os.PathLike[str]]) -> Dict[str, Any]:
    """Inspect slides, masters, layouts, shapes, notes, tables, and charts."""

    source = Path(path)
    package, presentation_part, _presentation, slide_parts, masters = _validate_package(source)
    layouts, layout_master, master_layouts = _layout_parts(package, masters)

    master_entries: List[Dict[str, Any]] = []
    for index, part in enumerate(masters, 1):
        root = _read_xml(package, part)
        shapes, tables, charts = _shape_inventory(package, part, root)
        common = root.find("p:cSld", NS)
        master_entries.append(
            {
                "index": index,
                "part": part,
                "name": common.get("name") if common is not None else None,
                "layout_parts": master_layouts.get(part, []),
                "shapes": shapes,
                "tables": tables,
                "charts": charts,
            }
        )

    layout_entries: List[Dict[str, Any]] = []
    for index, part in enumerate(layouts, 1):
        root = _read_xml(package, part)
        if root.tag != _q(P_NS, "sldLayout"):
            raise ValidationError(f"layout inventory contains non-layout XML: {part}")
        shapes, tables, charts = _shape_inventory(package, part, root)
        common = root.find("p:cSld", NS)
        layout_entries.append(
            {
                "index": index,
                "part": part,
                "name": root.get("name") or (common.get("name") if common is not None else None),
                "type": root.get("type"),
                "master_part": layout_master.get(part),
                "shapes": shapes,
                "tables": tables,
                "charts": charts,
            }
        )

    slide_entries: List[Dict[str, Any]] = []
    for index, part in enumerate(slide_parts, 1):
        root = _read_xml(package, part)
        shapes, tables, charts = _shape_inventory(package, part, root)
        layout_part = _single_internal_relationship_target(package, part, "slideLayout")
        slide_entries.append(
            {
                "index": index,
                "part": part,
                "layout_part": layout_part,
                "master_part": layout_master.get(layout_part) if layout_part else None,
                "shapes": shapes,
                "notes": _notes_inventory(package, part),
                "tables": tables,
                "charts": charts,
                "timing_fingerprint": _timing_fingerprint(root),
            }
        )

    return {
        "schema": INSPECT_SCHEMA,
        "file": str(source),
        "presentation_part": presentation_part,
        "timing_fingerprint_algorithm": TIMING_FINGERPRINT_ALGORITHM,
        "slides": slide_entries,
        "masters": master_entries,
        "layouts": layout_entries,
    }


analyze_pptx = inspect_pptx


def _stable_file_identity(info: os.stat_result) -> Tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
        getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000)),
    )


def _path_file_snapshot(path: Path) -> os.stat_result:
    """Return a regular-file snapshot comparable with ``fstat`` on Windows."""

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
            raise ValidationError(f"file type changed during snapshot: {path}")
        return opened
    finally:
        os.close(descriptor)


def _read_fill_plan_bytes(plan_path: Path) -> bytes:
    try:
        before = _path_file_snapshot(plan_path)
    except OSError as error:
        raise FillPlanError(f"could not stat fill plan {plan_path}: {error}") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise FillPlanError(f"fill plan must be a regular non-symlink file: {plan_path}")
    if before.st_size > MAX_FILL_PLAN_BYTES:
        raise FillPlanError(
            f"fill plan exceeds the {MAX_FILL_PLAN_BYTES}-byte safety limit: {plan_path}"
        )

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(plan_path, flags)
    except OSError as error:
        raise FillPlanError(f"could not open fill plan {plan_path}: {error}") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _stable_file_identity(opened) != _stable_file_identity(before)
        ):
            raise FillPlanError(f"fill plan changed before it was read: {plan_path}")
        chunks: List[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, MAX_FILL_PLAN_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_FILL_PLAN_BYTES:
                raise FillPlanError(
                    f"fill plan grew past the {MAX_FILL_PLAN_BYTES}-byte safety limit: {plan_path}"
                )
        after = os.fstat(descriptor)
        if total != opened.st_size or _stable_file_identity(after) != _stable_file_identity(opened):
            raise FillPlanError(f"fill plan changed while it was read: {plan_path}")
    finally:
        os.close(descriptor)
    try:
        final = _path_file_snapshot(plan_path)
    except OSError as error:
        raise FillPlanError(f"fill plan path changed while it was read: {plan_path}") from error
    if _stable_file_identity(final) != _stable_file_identity(opened):
        raise FillPlanError(f"fill plan path changed while it was read: {plan_path}")
    return b"".join(chunks)


def _copy_regular_source(source: Path, destination: Path) -> None:
    """Copy one stable, non-symlink PPTX snapshot into private staging."""

    try:
        before = _path_file_snapshot(source)
    except OSError as error:
        raise ValidationError(f"could not stat source PPTX {source}: {error}") from error
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValidationError(f"source PPTX must be a regular non-symlink file: {source}")
    if before.st_size > MAX_ARCHIVE_BYTES:
        raise ValidationError(
            f"PPTX exceeds the {MAX_ARCHIVE_BYTES}-byte compressed-size limit"
        )
    source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    destination_flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
        destination_flags |= os.O_NOFOLLOW
    try:
        source_descriptor = os.open(source, source_flags)
    except OSError as error:
        raise ValidationError(f"could not open source PPTX {source}: {error}") from error
    try:
        opened = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _stable_file_identity(opened) != _stable_file_identity(before)
        ):
            raise ValidationError(f"source PPTX changed before it was copied: {source}")
        try:
            destination_descriptor = os.open(destination, destination_flags)
        except OSError as error:
            raise ValidationError(
                f"could not open private source staging file {destination}: {error}"
            ) from error
        total = 0
        try:
            while True:
                chunk = os.read(source_descriptor, COPY_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise ValidationError(
                        f"PPTX grew past the {MAX_ARCHIVE_BYTES}-byte compressed-size limit"
                    )
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_descriptor, view)
                    if written <= 0:
                        raise ValidationError("could not complete private source snapshot")
                    view = view[written:]
            os.fsync(destination_descriptor)
        finally:
            os.close(destination_descriptor)
        after = os.fstat(source_descriptor)
        if total != opened.st_size or _stable_file_identity(after) != _stable_file_identity(opened):
            raise ValidationError(f"source PPTX changed while it was copied: {source}")
    finally:
        os.close(source_descriptor)
    try:
        final = _path_file_snapshot(source)
    except OSError as error:
        raise ValidationError(f"source PPTX path changed while it was copied: {source}") from error
    if _stable_file_identity(final) != _stable_file_identity(opened):
        raise ValidationError(f"source PPTX path changed while it was copied: {source}")


def _load_plan(plan: Union[Mapping[str, Any], str, os.PathLike[str]]) -> Dict[str, Any]:
    if isinstance(plan, Mapping):
        return dict(plan)
    plan_path = Path(plan)

    def unique_object(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
        value: Dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise FillPlanError(f"duplicate fill-plan object key: {key!r}")
            value[key] = item
        return value

    try:
        value = json.loads(
            _read_fill_plan_bytes(plan_path).decode("utf-8"),
            object_pairs_hook=unique_object,
        )
    except FillPlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise FillPlanError(f"could not read fill plan {plan_path}: {error}") from error
    if not isinstance(value, dict):
        raise FillPlanError("fill plan must be a JSON object")
    return value


def _validate_plain_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise FillPlanError(f"{field} must be a string")
    for character in value:
        codepoint = ord(character)
        if (
            (codepoint < 0x20 and character not in ("\t", "\n", "\r"))
            or 0xD800 <= codepoint <= 0xDFFF
            or codepoint in (0xFFFE, 0xFFFF)
        ):
            raise FillPlanError(f"{field} contains an XML-illegal control character")
    encoded_size = len(value.encode("utf-8"))
    if encoded_size > MAX_TEXT_BYTES_PER_EDIT:
        raise FillPlanError(
            f"{field} exceeds the {MAX_TEXT_BYTES_PER_EDIT}-byte per-edit safety limit"
        )
    return value


def _canonical_language_tag(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 63 or not LANGUAGE_TAG_RE.fullmatch(value):
        raise FillPlanError(f"invalid conservative BCP-47 language tag: {value!r}")
    pieces = value.split("-")
    canonical = [pieces[0].lower()]
    for piece in pieces[1:]:
        if len(piece) == 4 and piece.isalpha():
            canonical.append(piece.title())
        elif len(piece) == 2 and piece.isalpha():
            canonical.append(piece.upper())
        else:
            canonical.append(piece.lower())
    return "-".join(canonical)


def _language_from_entry(entry: Mapping[str, Any]) -> Optional[str]:
    if "lang" not in entry or entry["lang"] is None:
        return None
    return _canonical_language_tag(entry["lang"])


def _slide_index(value: Any, count: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FillPlanError("slide must be a one-based integer")
    if value < 1 or value > count:
        raise FillPlanError(f"slide {value} is outside the 1..{count} range")
    return value


def _copy_formatting(text_body: ET.Element) -> Tuple[Optional[ET.Element], Optional[ET.Element]]:
    paragraph_properties = text_body.find("a:p/a:pPr", NS)
    run_properties = text_body.find("a:p/a:r/a:rPr", NS)
    if run_properties is None:
        run_properties = text_body.find("a:p/a:fld/a:rPr", NS)
    if run_properties is None:
        run_properties = text_body.find("a:lstStyle/a:lvl1pPr/a:defRPr", NS)
    return (
        copy.deepcopy(paragraph_properties) if paragraph_properties is not None else None,
        copy.deepcopy(run_properties) if run_properties is not None else None,
    )


def _replace_text_body(text_body: ET.Element, text: str, language: Optional[str]) -> None:
    paragraph_properties, run_properties = _copy_formatting(text_body)
    for paragraph in list(text_body.findall("a:p", NS)):
        text_body.remove(paragraph)
    lines = text.split("\n")
    for line in lines:
        paragraph = ET.SubElement(text_body, _q(A_NS, "p"))
        if paragraph_properties is not None:
            paragraph.append(copy.deepcopy(paragraph_properties))
        if line:
            run = ET.SubElement(paragraph, _q(A_NS, "r"))
            properties = copy.deepcopy(run_properties) if run_properties is not None else ET.Element(_q(A_NS, "rPr"))
            if language is not None:
                properties.set("lang", language)
            if properties.attrib or len(properties):
                run.append(properties)
            text_element = ET.SubElement(run, _q(A_NS, "t"))
            if line[:1].isspace() or line[-1:].isspace():
                text_element.set(_q(XML_NS, "space"), "preserve")
            text_element.text = line
        else:
            end_properties = ET.SubElement(paragraph, _q(A_NS, "endParaRPr"))
            if language is not None:
                end_properties.set("lang", language)


def _all_shapes(root: ET.Element) -> List[ET.Element]:
    tree = root.find("p:cSld/p:spTree", NS)
    if tree is None:
        return []
    return [element for element, _parent in _iter_shape_elements(tree)]


def _select_shape(root: ET.Element, entry: Mapping[str, Any]) -> ET.Element:
    has_id = "shape_id" in entry
    has_name = "shape_name" in entry
    if has_id == has_name:
        raise FillPlanError("each replacement must contain exactly one of shape_id or shape_name")
    shapes = _all_shapes(root)
    if has_id:
        raw_id = entry["shape_id"]
        if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id < 0:
            raise FillPlanError("shape_id must be a non-negative integer")
        matches = [element for element in shapes if _shape_identity(element)[0] == raw_id]
        selector = f"shape id {raw_id}"
    else:
        raw_name = entry["shape_name"]
        if not isinstance(raw_name, str) or not raw_name:
            raise FillPlanError("shape_name must be a non-empty string")
        matches = [element for element in shapes if _shape_identity(element)[1] == raw_name]
        selector = f"shape name {raw_name!r}"
    if not matches:
        raise FillPlanError(f"{selector} was not found")
    if len(matches) > 1:
        raise FillPlanError(f"{selector} is ambiguous")
    element = matches[0]
    object_type, _subtype = _shape_type(element)
    if element.tag != _q(P_NS, "sp"):
        raise UnsupportedObjectError(
            f"{selector} is a {object_type}; only existing p:sp text shapes are editable"
        )
    if element.find("p:txBody", NS) is None:
        raise UnsupportedObjectError(f"{selector} has no editable p:txBody")
    return element


def _notes_body_shape(root: ET.Element) -> ET.Element:
    body_shapes = [
        element
        for element in _all_shapes(root)
        if element.tag == _q(P_NS, "sp")
        and _placeholder_type(element) == "body"
        and element.find("p:txBody", NS) is not None
    ]
    if not body_shapes:
        raise UnsupportedObjectError("notes slide has no existing body placeholder to edit")
    if len(body_shapes) > 1:
        raise FillPlanError("notes slide has ambiguous body placeholders")
    return body_shapes[0]


def _namespace_declarations(data: bytes) -> Dict[Optional[str], str]:
    match = ROOT_START_TAG_RE.search(data)
    if match is None:
        return {}
    declarations: Dict[Optional[str], str] = {}
    for namespace in XMLNS_RE.finditer(match.group(0)):
        prefix = namespace.group(1).decode("ascii") if namespace.group(1) else None
        declarations[prefix] = namespace.group(3).decode("utf-8")
    return declarations


def _serialize_xml_preserving_root_namespaces(data: bytes, root: ET.Element) -> bytes:
    declarations = _namespace_declarations(data)
    # Preserve extension prefixes used by mc:Ignorable values whenever possible.
    for prefix, uri in declarations.items():
        if prefix is None or prefix in ("xml", "xmlns"):
            continue
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass
    serialized = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    start = ROOT_START_TAG_RE.search(serialized)
    if start is None:
        raise ValidationError("could not serialize modified XML root")
    existing = _namespace_declarations(serialized)
    additions: List[bytes] = []
    for prefix, uri in declarations.items():
        if prefix in existing:
            continue
        escaped = (
            uri.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .encode("utf-8")
        )
        if prefix is None:
            additions.append(b' xmlns="' + escaped + b'"')
        else:
            additions.append(b" xmlns:" + prefix.encode("ascii") + b'="' + escaped + b'"')
    if not additions:
        return serialized
    insertion = start.end() - 1
    if serialized[insertion - 1 : insertion + 1] == b"/>":
        insertion -= 1
    return serialized[:insertion] + b"".join(additions) + serialized[insertion:]


def _normalized_plan(plan: Mapping[str, Any]) -> Tuple[List[Mapping[str, Any]], List[Mapping[str, Any]]]:
    allowed = {"replacements", "notes"}
    unknown = sorted(set(plan) - allowed)
    if unknown:
        raise FillPlanError(
            "unsupported fill-plan fields (transitions and object edits are not enabled): "
            + ", ".join(unknown)
        )
    replacements = plan.get("replacements", [])
    notes = plan.get("notes", [])
    if not isinstance(replacements, list) or not isinstance(notes, list):
        raise FillPlanError("replacements and notes must be arrays")
    if not replacements and not notes:
        raise FillPlanError("fill plan contains no edits")
    if len(replacements) + len(notes) > MAX_FILL_EDITS:
        raise FillPlanError(
            f"fill plan contains more than {MAX_FILL_EDITS} edits"
        )
    total_text_bytes = 0
    normalized_replacements: List[Mapping[str, Any]] = []
    for index, entry in enumerate(replacements):
        if not isinstance(entry, dict):
            raise FillPlanError(f"replacements[{index}] must be an object")
        unknown_entry = sorted(set(entry) - {"slide", "shape_id", "shape_name", "text", "lang"})
        if unknown_entry:
            raise FillPlanError(
                f"replacements[{index}] has unsupported fields: {', '.join(unknown_entry)}"
            )
        if "slide" not in entry or "text" not in entry:
            raise FillPlanError(f"replacements[{index}] requires slide and text")
        text = _validate_plain_text(entry["text"], f"replacements[{index}].text")
        total_text_bytes += len(text.encode("utf-8"))
        _language_from_entry(entry)
        normalized_replacements.append(entry)
    normalized_notes: List[Mapping[str, Any]] = []
    for index, entry in enumerate(notes):
        if not isinstance(entry, dict):
            raise FillPlanError(f"notes[{index}] must be an object")
        unknown_entry = sorted(set(entry) - {"slide", "text", "lang"})
        if unknown_entry:
            raise FillPlanError(f"notes[{index}] has unsupported fields: {', '.join(unknown_entry)}")
        if "slide" not in entry or "text" not in entry:
            raise FillPlanError(f"notes[{index}] requires slide and text")
        text = _validate_plain_text(entry["text"], f"notes[{index}].text")
        total_text_bytes += len(text.encode("utf-8"))
        _language_from_entry(entry)
        normalized_notes.append(entry)
    if total_text_bytes > MAX_FILL_TEXT_BYTES:
        raise FillPlanError(
            f"fill-plan text exceeds the {MAX_FILL_TEXT_BYTES}-byte total safety limit"
        )
    return normalized_replacements, normalized_notes


def _paths_alias(left: Path, right: Path) -> bool:
    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except (OSError, RuntimeError, ValueError):
        pass
    try:
        return right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _fsync_regular_file(path: Path) -> None:
    """Flush a completed regular file through a Windows-compatible handle."""

    # Windows' _commit-backed fsync requires a writable descriptor.  This
    # handle is used only as a durability barrier and does not modify bytes.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _write_candidate(
    source: Path,
    destination: Path,
    modifications: Mapping[str, bytes],
) -> None:
    try:
        with zipfile.ZipFile(source, "r") as input_archive, zipfile.ZipFile(
            destination, "w", allowZip64=False
        ) as output_archive:
            output_archive.comment = input_archive.comment
            source_names = {info.filename for info in input_archive.infolist()}
            missing = set(modifications) - source_names
            if missing:
                raise ValidationError(
                    "cannot modify missing package parts: " + ", ".join(sorted(missing))
                )
            for source_info in input_archive.infolist():
                target_info = copy.copy(source_info)
                if source_info.filename in modifications:
                    output_archive.writestr(
                        target_info, modifications[source_info.filename]
                    )
                    continue
                with input_archive.open(source_info, "r") as reader, output_archive.open(
                    target_info, "w", force_zip64=False
                ) as writer:
                    shutil.copyfileobj(reader, writer, length=COPY_CHUNK_BYTES)
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise ValidationError(f"could not build candidate PPTX: {error}") from error
    _fsync_regular_file(destination)


def _temporary_path(directory: Path, prefix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=prefix, suffix=".pptx", dir=directory)
    os.close(descriptor)
    return Path(raw_path)


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_candidate(candidate: Path, output: Path, *, force: bool) -> None:
    if force:
        os.replace(candidate, output)
        return
    # Candidate and output share a directory, so a hard link provides a
    # same-filesystem, atomic no-replace publish: readers see either no output
    # or the complete, already-fsynced ZIP.  Unlike exists()+replace(), this
    # cannot overwrite a file that appears during validation.
    try:
        os.link(candidate, output)
    except FileExistsError as error:
        raise FillPlanError(
            f"output appeared during fill and was not replaced: {output}"
        ) from error
    except OSError as error:
        raise FillPlanError(
            f"atomic no-replace publication is unavailable for {output}: {error}"
        ) from error
    candidate.unlink()


def fill_pptx(
    source_path: Union[str, os.PathLike[str]],
    plan: Union[Mapping[str, Any], str, os.PathLike[str]],
    output_path: Union[str, os.PathLike[str]],
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Apply safe text/notes edits, validate, then atomically publish a PPTX."""

    source = Path(source_path)
    output = Path(output_path)
    if not output.parent.is_dir():
        raise FillPlanError(f"output directory does not exist: {output.parent}")
    if output.is_symlink():
        raise FillPlanError(f"output must not be a symbolic link: {output}")
    if _paths_alias(source, output):
        raise FillPlanError("source and output must be different files, even with force=True")
    if output.exists() and not force:
        raise FillPlanError(f"output already exists (use force=True to replace): {output}")
    raw_plan = _load_plan(plan)
    replacements, notes_edits = _normalized_plan(raw_plan)
    working_copy = _temporary_path(output.parent, ".pptx-native-source-")
    candidate = _temporary_path(output.parent, ".pptx-native-candidate-")
    published = False
    try:
        _copy_regular_source(source, working_copy)
        working_package, _pp, _pr, working_slides, _pm = _validate_package(working_copy)
        slide_parts = working_slides
        source_fingerprints = _timing_fingerprints(working_package, slide_parts)

        slide_roots: Dict[str, ET.Element] = {}
        slide_bytes: Dict[str, bytes] = {}
        note_roots: Dict[str, ET.Element] = {}
        note_bytes: Dict[str, bytes] = {}
        modified_parts: Dict[str, bytes] = {}
        changed_shape_targets: Set[Tuple[int, Optional[int], Optional[str]]] = set()
        changed_note_slides: Set[int] = set()

        for entry in replacements:
            slide_number = _slide_index(entry["slide"], len(slide_parts))
            slide_part = slide_parts[slide_number - 1]
            if slide_part not in slide_roots:
                original = working_package.read(slide_part)
                slide_bytes[slide_part] = original
                slide_roots[slide_part] = _parse_xml(original, slide_part)
            root = slide_roots[slide_part]
            element = _select_shape(root, entry)
            shape_id, shape_name = _shape_identity(element)
            target_key = (slide_number, shape_id, shape_name)
            if target_key in changed_shape_targets:
                raise FillPlanError(
                    f"duplicate replacement target on slide {slide_number}: {shape_name or shape_id}"
                )
            changed_shape_targets.add(target_key)
            text = _validate_plain_text(entry["text"], "replacement text")
            language = _language_from_entry(entry)
            text_body = element.find("p:txBody", NS)
            assert text_body is not None
            _replace_text_body(text_body, text, language)

        for entry in notes_edits:
            slide_number = _slide_index(entry["slide"], len(slide_parts))
            if slide_number in changed_note_slides:
                raise FillPlanError(f"duplicate notes edit for slide {slide_number}")
            changed_note_slides.add(slide_number)
            slide_part = slide_parts[slide_number - 1]
            notes_part = _single_internal_relationship_target(
                working_package, slide_part, "notesSlide"
            )
            if notes_part is None:
                raise UnsupportedObjectError(
                    f"slide {slide_number} has no existing notesSlide; creating notes parts is unsupported"
                )
            if notes_part not in note_roots:
                original = working_package.read(notes_part)
                note_bytes[notes_part] = original
                note_roots[notes_part] = _parse_xml(original, notes_part)
            root = note_roots[notes_part]
            body_shape = _notes_body_shape(root)
            text_body = body_shape.find("p:txBody", NS)
            assert text_body is not None
            _replace_text_body(
                text_body,
                _validate_plain_text(entry["text"], "notes text"),
                _language_from_entry(entry),
            )

        for part, root in slide_roots.items():
            if _timing_fingerprint(root) != source_fingerprints[part]:
                raise ValidationError(f"animation timing changed while editing {part}")
            modified_parts[part] = _serialize_xml_preserving_root_namespaces(
                slide_bytes[part], root
            )
        for part, root in note_roots.items():
            modified_parts[part] = _serialize_xml_preserving_root_namespaces(
                note_bytes[part], root
            )

        _write_candidate(working_copy, candidate, modified_parts)
        candidate_package, _cp, _cr, candidate_slides, _cm = _validate_package(candidate)
        candidate_fingerprints = _timing_fingerprints(candidate_package, candidate_slides)
        if source_fingerprints != candidate_fingerprints:
            raise ValidationError("candidate animation timing fingerprints differ from source")
        if output.is_symlink():
            raise FillPlanError(f"output became a symbolic link during fill: {output}")
        if _paths_alias(source, output):
            raise FillPlanError("output became an alias of the source during fill")
        _publish_candidate(candidate, output, force=force)
        published = True
        _fsync_directory(output.parent)
        return {
            "schema": FILL_SCHEMA,
            "source": str(source),
            "output": str(output),
            "status": "written",
            "text_replacements": len(replacements),
            "notes_replacements": len(notes_edits),
            "modified_parts": sorted(modified_parts, key=_natural_part_key),
            "timing_fingerprint_algorithm": TIMING_FINGERPRINT_ALGORITHM,
            "timing_fingerprints": candidate_fingerprints,
            "validation": "valid",
        }
    finally:
        for temporary in (working_copy, candidate):
            if published and temporary == candidate:
                continue
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _print_json(value: Mapping[str, Any], pretty: bool) -> None:
    json.dump(
        value,
        sys.stdout,
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=True,
    )
    sys.stdout.write("\n")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Native read/validate/conservative-fill sidecar for PPTX"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "analyze"):
        inspect_parser = subparsers.add_parser(command, help="inventory an existing PPTX")
        inspect_parser.add_argument("source", type=Path)
        inspect_parser.add_argument("--pretty", action="store_true")
    validate_parser = subparsers.add_parser("validate", help="validate OPC/OOXML structure")
    validate_parser.add_argument("source", type=Path)
    validate_parser.add_argument("--pretty", action="store_true")
    fill_parser = subparsers.add_parser("fill", help="apply a JSON fill plan")
    fill_parser.add_argument("source", type=Path)
    fill_parser.add_argument("plan", type=Path)
    fill_parser.add_argument("-o", "--output", type=Path, required=True)
    fill_parser.add_argument("--force", action="store_true")
    fill_parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    try:
        if arguments.command in ("inspect", "analyze"):
            _print_json(inspect_pptx(arguments.source), arguments.pretty)
            return 0
        if arguments.command == "validate":
            report = validate_pptx(arguments.source)
            _print_json(report, arguments.pretty)
            return 0 if report["status"] == "valid" else 1
        if arguments.command == "fill":
            _print_json(
                fill_pptx(
                    arguments.source,
                    arguments.plan,
                    arguments.output,
                    force=arguments.force,
                ),
                arguments.pretty,
            )
            return 0
    except PptxNativeError as error:
        _print_json({"status": "error", "error": str(error)}, True)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
