#!/usr/bin/env python3
"""Offline PPTD v2 -> Deck IR -> editable OOXML PPTX MVP exporter.

The implementation is deliberately dependency-free and fail-closed.  It does
not call Kimi, launch a browser, or use a network API.  Unsupported PPTD
features are rejected by :mod:`deck_ir` with structured diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import stat
import sys
import tempfile
import zipfile
from html import escape
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

from deck_ir import DeckIR, DeckIRValidationError, ElementIR, ImageAsset, PageIR, load_deck_ir


P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
PACKAGE_REL_BASE = "http://schemas.openxmlformats.org/package/2006/relationships/"
PPTX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
SLIDE_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
NOTES_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"
NOTES_MASTER_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"

MAX_METADATA_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024 * 1024
SAFE_TRANSITIONS = frozenset({"none", "fade", "push", "wipe", "morph"})
TRANSITION_DIRECTIONS = frozenset({"left", "right", "up", "down"})
BCP47_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
METADATA_TOP_LEVEL_KEYS = frozenset(
    {
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
)


class LocalExportError(RuntimeError):
    pass


def _fsync_regular_file(path: Path) -> None:
    """Flush a completed regular file through a Windows-compatible handle."""

    # Windows implements fsync with _commit(), which rejects a descriptor
    # opened read-only even when the file's writers have already closed.  This
    # handle is used only as a durability barrier and does not modify bytes.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _verify_quality_gate(
    source: os.PathLike[str] | str,
    report_path: os.PathLike[str] | str,
) -> Dict[str, Any]:
    """Verify the bundled source-bound gate before parsing or writing output."""

    try:
        import pptd_quality
    except ImportError as exc:  # pragma: no cover - same scripts directory in normal use
        raise LocalExportError("bundled pptd_quality module is unavailable") from exc
    try:
        receipt = pptd_quality.verify_quality_report(
            source,
            report_path,
            minimum_fail_on="warning",
        )
    except (pptd_quality.QualityInputError, OSError, ValueError) as exc:
        code = getattr(exc, "code", "quality_report.invalid")
        raise LocalExportError(f"quality gate rejected the report ({code}): {exc}") from exc
    media = receipt.get("media", {})
    if media.get("referenced", 0) and (
        media.get("tracked") != media.get("referenced")
        or receipt.get("media_sources_sha256") is None
    ):
        raise LocalExportError(
            "quality gate rejected the report: referenced local media must be "
            "fully tracked by a bound provenance manifest"
        )
    return receipt


def _quality_bound_inputs(
    project_root: Path,
    report_path: os.PathLike[str] | str,
) -> Tuple[Path, ...]:
    try:
        import pptd_quality
    except ImportError as exc:  # pragma: no cover - same scripts directory in normal use
        raise LocalExportError("bundled pptd_quality module is unavailable") from exc
    try:
        return pptd_quality.quality_report_bound_inputs(report_path, project_root)
    except (pptd_quality.QualityInputError, OSError, ValueError) as exc:
        code = getattr(exc, "code", "quality_report.invalid")
        raise LocalExportError(
            f"quality gate source bindings are invalid ({code}): {exc}"
        ) from exc


def _attr(value: Any) -> str:
    return escape(str(value), quote=True)


def _text(value: str) -> str:
    return escape(value, quote=False)


def _xml(body: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + body).encode("utf-8")


def _emu(value: float) -> int:
    return int(round(value * 12_700))


def _rotation(value: float) -> int:
    return int(round((value % 360) * 60_000))


def _alpha(value: float) -> int:
    return max(0, min(100_000, int(round(value * 100_000))))


def _solid_fill(color: Mapping[str, Any], opacity: float = 1.0) -> str:
    alpha = _alpha(float(color["alpha"]) * opacity)
    alpha_xml = f'<a:alpha val="{alpha}"/>' if alpha < 100_000 else ""
    return f'<a:solidFill><a:srgbClr val="{_attr(color["rgb"])}">{alpha_xml}</a:srgbClr></a:solidFill>'


def _line_xml(border: Optional[Mapping[str, Any]], opacity: float = 1.0) -> str:
    if border is None:
        return "<a:ln><a:noFill/></a:ln>"
    dash = {"solid": "solid", "dash": "dash", "dot": "sysDot"}[str(border["style"])]
    return (
        f'<a:ln w="{max(1, _emu(float(border["width"])))}" cap="flat" cmpd="sng" algn="ctr">'
        f'{_solid_fill(border["color"], opacity)}<a:prstDash val="{dash}"/></a:ln>'
    )


def _transform(
    bounds: Tuple[float, float, float, float],
    rotation: float,
    flip: Tuple[bool, bool],
) -> str:
    x, y, width, height = bounds
    attributes = []
    if rotation % 360:
        attributes.append(f'rot="{_rotation(rotation)}"')
    if flip[0]:
        attributes.append('flipH="1"')
    if flip[1]:
        attributes.append('flipV="1"')
    suffix = " " + " ".join(attributes) if attributes else ""
    return (
        f'<a:xfrm{suffix}><a:off x="{_emu(x)}" y="{_emu(y)}"/>'
        f'<a:ext cx="{max(1, _emu(width))}" cy="{max(1, _emu(height))}"/></a:xfrm>'
    )


def _geometry(shape_name: str, adjustments: Sequence[float] = ()) -> str:
    guides = []
    for index, value in enumerate(adjustments):
        name = "adj" if index == 0 else f"adj{index + 1}"
        guides.append(f'<a:gd name="{name}" fmla="val {int(round(value))}"/>')
    return f'<a:prstGeom prst="{_attr(shape_name)}"><a:avLst>{"".join(guides)}</a:avLst></a:prstGeom>'


def _nonvisual_shape(element: ElementIR, *, text_box: bool = False) -> str:
    tx_box = ' txBox="1"' if text_box else ""
    return (
        '<p:nvSpPr>'
        f'<p:cNvPr id="{element.shape_id}" name="{_attr(element.element_id)}"/>'
        f'<p:cNvSpPr{tx_box}/><p:nvPr/></p:nvSpPr>'
    )


def _shape_xml(element: ElementIR) -> str:
    properties = element.properties
    fill = properties.get("fill")
    fill_xml = "<a:noFill/>" if fill is None else _solid_fill(fill["color"], element.opacity)
    return (
        '<p:sp>'
        f'{_nonvisual_shape(element)}<p:spPr>{_transform(element.bounds, element.rotation, element.flip)}'
        f'{_geometry(str(properties["shapeName"]), properties.get("adjustments", ()))}'
        f'{fill_xml}{_line_xml(properties.get("border"), element.opacity)}</p:spPr></p:sp>'
    )


def _run_properties(element: ElementIR, language: str) -> str:
    properties = element.properties
    fonts = properties["fontFamily"]
    attributes = [
        f'lang="{_attr(language)}"',
        f'sz="{max(100, int(round(float(properties["fontSize"]) * 100)))}"',
        f'b="{1 if properties["bold"] else 0}"',
        f'i="{1 if properties["italic"] else 0}"',
        'dirty="0"',
    ]
    return (
        f'<a:rPr {" ".join(attributes)}>{_solid_fill(properties["color"], element.opacity)}'
        f'<a:latin typeface="{_attr(fonts["latin"])}"/><a:ea typeface="{_attr(fonts["ea"])}"/>'
        f'<a:cs typeface="{_attr(fonts["latin"])}"/></a:rPr>'
    )


def _paragraph_xml(element: ElementIR, value: str, language: str) -> str:
    horizontal = {
        "left": "l",
        "center": "ctr",
        "right": "r",
        "justify": "just",
        "distributed": "dist",
    }[str(element.properties["align"][0])]
    run = ""
    if value:
        space = ' xml:space="preserve"' if value[:1].isspace() or value[-1:].isspace() else ""
        run = f'<a:r>{_run_properties(element, language)}<a:t{space}>{_text(value)}</a:t></a:r>'
    return (
        f'<a:p><a:pPr algn="{horizontal}"><a:buNone/></a:pPr>{run}'
        f'<a:endParaRPr lang="{_attr(language)}"/></a:p>'
    )


def _text_xml(element: ElementIR, language: str) -> str:
    properties = element.properties
    vertical = {"top": "t", "middle": "ctr", "bottom": "b"}[str(properties["align"][1])]
    wrap = "square" if properties["wrap"] else "none"
    direction = ' vert="eaVert"' if properties["textDirection"] == "vertical" else ""
    paragraphs = str(properties["text"]).split("\n")
    if not paragraphs:
        paragraphs = [""]
    body = "".join(_paragraph_xml(element, paragraph, language) for paragraph in paragraphs)
    return (
        '<p:sp>'
        f'{_nonvisual_shape(element, text_box=True)}<p:spPr>{_transform(element.bounds, element.rotation, element.flip)}'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr>'
        f'<p:txBody><a:bodyPr wrap="{wrap}" anchor="{vertical}" lIns="0" tIns="0" rIns="0" bIns="0"{direction}/>'
        f'<a:lstStyle/>{body}</p:txBody></p:sp>'
    )


def _line_shape_xml(element: ElementIR) -> str:
    properties = element.properties
    view_width, view_height = properties["viewBox"]
    (start_x, start_y), (end_x, end_y) = properties["points"]
    x, y, width, height = element.bounds
    x1 = x + start_x / view_width * width
    y1 = y + start_y / view_height * height
    x2 = x + end_x / view_width * width
    y2 = y + end_y / view_height * height
    line_bounds = (min(x1, x2), min(y1, y2), max(abs(x2 - x1), 1 / 12_700), max(abs(y2 - y1), 1 / 12_700))
    line_flip = (x2 < x1, y2 < y1)
    border = properties["border"]
    arrow_map = {None: "none", "arrow": "triangle", "stealth": "stealth", "diamond": "diamond", "oval": "oval"}
    head, tail = properties["arrow"]
    line_xml = _line_xml(border, element.opacity)
    line_xml = line_xml[:-7] + f'<a:headEnd type="{arrow_map[head]}"/><a:tailEnd type="{arrow_map[tail]}"/></a:ln>'
    return (
        '<p:cxnSp><p:nvCxnSpPr>'
        f'<p:cNvPr id="{element.shape_id}" name="{_attr(element.element_id)}"/><p:cNvCxnSpPr/><p:nvPr/>'
        '</p:nvCxnSpPr>'
        f'<p:spPr>{_transform(line_bounds, element.rotation, line_flip)}'
        f'<a:prstGeom prst="line"><a:avLst/></a:prstGeom>{line_xml}</p:spPr></p:cxnSp>'
    )


def _image_layout(element: ElementIR) -> Tuple[Tuple[float, float, float, float], Dict[str, float]]:
    properties = element.properties
    asset: ImageAsset = properties["asset"]
    crop = dict(properties["crop"])
    x, y, width, height = element.bounds
    usable_width = asset.width * (1 - crop["left"] - crop["right"])
    usable_height = asset.height * (1 - crop["top"] - crop["bottom"])
    source_aspect = usable_width / usable_height
    box_aspect = width / height
    mode = properties["fit"]
    if mode == "cover":
        if source_aspect > box_aspect:
            extra = (1 - box_aspect / source_aspect) / 2
            amount = (1 - crop["left"] - crop["right"]) * extra
            crop["left"] += amount
            crop["right"] += amount
        elif source_aspect < box_aspect:
            extra = (1 - source_aspect / box_aspect) / 2
            amount = (1 - crop["top"] - crop["bottom"]) * extra
            crop["top"] += amount
            crop["bottom"] += amount
    elif mode == "contain":
        if source_aspect > box_aspect:
            drawn_height = width / source_aspect
            y += (height - drawn_height) / 2
            height = drawn_height
        elif source_aspect < box_aspect:
            drawn_width = height * source_aspect
            x += (width - drawn_width) / 2
            width = drawn_width
    return (x, y, width, height), crop


def _picture_xml(element: ElementIR, relationship_id: str) -> str:
    properties = element.properties
    bounds, crop = _image_layout(element)
    source_rect = "".join(
        f' {short}="{max(0, min(100_000, int(round(crop[key] * 100_000))))}"'
        for key, short in (("left", "l"), ("top", "t"), ("right", "r"), ("bottom", "b"))
        if crop[key]
    )
    alpha = _alpha(element.opacity)
    alpha_xml = f'<a:alphaModFix amt="{alpha}"/>' if alpha < 100_000 else ""
    return (
        '<p:pic><p:nvPicPr>'
        f'<p:cNvPr id="{element.shape_id}" name="{_attr(element.element_id)}"/>'
        '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
        f'<p:blipFill><a:blip r:embed="{_attr(relationship_id)}">{alpha_xml}</a:blip>'
        f'<a:srcRect{source_rect}/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
        f'<p:spPr>{_transform(bounds, element.rotation, element.flip)}'
        f'{_geometry(str(properties["cropShape"]))}{_line_xml(properties.get("border"), element.opacity)}</p:spPr></p:pic>'
    )


def _group_shape_tree_start() -> str:
    return (
        '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
        '</p:nvGrpSpPr><p:grpSpPr/>'
    )


def _relationships(entries: Sequence[Tuple[str, str, str]]) -> bytes:
    body = "".join(
        f'<Relationship Id="{_attr(identifier)}" Type="{_attr(relation_type)}" Target="{_attr(target)}"/>'
        for identifier, relation_type, target in entries
    )
    return _xml(f'<Relationships xmlns="{REL_NS}">{body}</Relationships>')


def _transition_attributes(transition: Mapping[str, Any], *, duration: bool) -> str:
    attributes: List[str] = []
    if transition.get("advanceOnClick") is not None:
        attributes.append(f'advClick="{1 if transition["advanceOnClick"] else 0}"')
    if transition.get("advanceAfterMs") is not None:
        attributes.append(f'advTm="{transition["advanceAfterMs"]}"')
    if duration and transition.get("durationMs") is not None:
        attributes.append(f'p14:dur="{transition["durationMs"]}"')
    return " " + " ".join(attributes) if attributes else ""


def _transition_effect(transition: Mapping[str, Any]) -> str:
    transition_type = transition["type"]
    if transition_type == "none":
        return ""
    if transition_type == "fade":
        return "<p:fade/>"
    direction = transition.get("direction")
    direction_attribute = ""
    if direction is not None:
        direction_attribute = f' dir="{ {"left":"l", "right":"r", "up":"u", "down":"d"}[direction] }"'
    return f'<p:{transition_type}{direction_attribute}/>'


def _transition_xml(transition: Optional[Mapping[str, Any]]) -> str:
    if transition is None:
        return ""
    effect = _transition_effect(transition)
    has_timing = transition.get("advanceOnClick") is not None or transition.get("advanceAfterMs") is not None
    if transition["type"] == "none" and not has_timing:
        return ""
    fallback = f'<p:transition{_transition_attributes(transition, duration=False)}>{effect}</p:transition>'
    if transition.get("durationMs") is None:
        return fallback
    choice = f'<p:transition{_transition_attributes(transition, duration=True)}>{effect}</p:transition>'
    return (
        '<mc:AlternateContent><mc:Choice Requires="p14">'
        f'{choice}</mc:Choice><mc:Fallback>{fallback}</mc:Fallback></mc:AlternateContent>'
    )


def _slide_parts(
    deck: DeckIR,
    page: PageIR,
    language: str,
    transition: Optional[Mapping[str, Any]],
    media_names: Mapping[str, str],
) -> Tuple[bytes, bytes, int]:
    relationship_entries: List[Tuple[str, str, str]] = [
        ("rId1", REL_BASE + "slideLayout", "../slideLayouts/slideLayout1.xml")
    ]
    relationship_by_asset: Dict[str, str] = {}
    shape_xml: List[str] = []
    next_relationship = 2
    for element in page.elements:
        if element.kind == "text":
            shape_xml.append(_text_xml(element, language))
        elif element.kind == "shape":
            shape_xml.append(_shape_xml(element))
        elif element.kind == "line":
            shape_xml.append(_line_shape_xml(element))
        elif element.kind == "image":
            asset: ImageAsset = element.properties["asset"]
            relationship_id = relationship_by_asset.get(asset.sha256)
            if relationship_id is None:
                relationship_id = f"rId{next_relationship}"
                next_relationship += 1
                relationship_by_asset[asset.sha256] = relationship_id
                relationship_entries.append(
                    (relationship_id, REL_BASE + "image", "../media/" + media_names[asset.sha256])
                )
            shape_xml.append(_picture_xml(element, relationship_id))
        else:  # pragma: no cover - guarded by Deck IR
            raise LocalExportError(f"unexpected Deck IR element kind: {element.kind}")
    if page.notes is not None:
        relationship_entries.append(
            (f"rId{next_relationship}", REL_BASE + "notesSlide", f"../notesSlides/notesSlide{page.index}.xml")
        )
    background = page.background
    background_xml = (
        f'<p:bg><p:bgPr>{_solid_fill(background["color"])}</p:bgPr></p:bg>'
        if background.get("type") == "solid"
        else ""
    )
    root_attributes = (
        f'xmlns:p="{P_NS}" xmlns:a="{A_NS}" xmlns:r="{R_NS}" '
        f'xmlns:mc="{MC_NS}" xmlns:p14="{P14_NS}" mc:Ignorable="p14"'
    )
    slide = _xml(
        f'<p:sld {root_attributes}><p:cSld>{background_xml}{_group_shape_tree_start()}'
        f'{"".join(shape_xml)}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
        f'{_transition_xml(transition)}</p:sld>'
    )
    return slide, _relationships(relationship_entries), len(relationship_by_asset)


def _notes_text_body(notes: str, language: str) -> str:
    paragraphs = notes.split("\n") or [""]
    body = []
    for paragraph in paragraphs:
        run = ""
        if paragraph:
            space = ' xml:space="preserve"' if paragraph[:1].isspace() or paragraph[-1:].isspace() else ""
            run = (
                f'<a:r><a:rPr lang="{_attr(language)}" sz="1200" dirty="0"/>'
                f'<a:t{space}>{_text(paragraph)}</a:t></a:r>'
            )
        body.append(f'<a:p>{run}<a:endParaRPr lang="{_attr(language)}"/></a:p>')
    return "".join(body)


def _notes_slide(page: PageIR, language: str) -> bytes:
    return _xml(
        f'<p:notes xmlns:p="{P_NS}" xmlns:a="{A_NS}" xmlns:r="{R_NS}"><p:cSld>'
        f'{_group_shape_tree_start()}<p:sp><p:nvSpPr><p:cNvPr id="2" name="Notes Placeholder 1"/>'
        '<p:cNvSpPr txBox="1"/><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="685800" y="914400"/><a:ext cx="5486400" cy="5486400"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr>'
        f'<p:txBody><a:bodyPr/><a:lstStyle/>{_notes_text_body(page.notes or "", language)}</p:txBody>'
        '</p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>'
    )


def _notes_master() -> bytes:
    return _xml(
        f'<p:notesMaster xmlns:p="{P_NS}" xmlns:a="{A_NS}" xmlns:r="{R_NS}">'
        f'<p:cSld>{_group_shape_tree_start()}</p:spTree></p:cSld>'
        '<p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" '
        'accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" '
        'hlink="hlink" tx1="dk1" tx2="dk2"/><p:hf hdr="1" ftr="1" dt="1" sldNum="1"/>'
        '<p:notesStyle><a:lvl1pPr marL="0" indent="0"><a:defRPr sz="1200"/></a:lvl1pPr></p:notesStyle>'
        '</p:notesMaster>'
    )


def _theme() -> bytes:
    colors = [
        ("dk1", "000000"),
        ("lt1", "FFFFFF"),
        ("dk2", "1F2937"),
        ("lt2", "F3F4F6"),
        ("accent1", "4472C4"),
        ("accent2", "ED7D31"),
        ("accent3", "A5A5A5"),
        ("accent4", "FFC000"),
        ("accent5", "5B9BD5"),
        ("accent6", "70AD47"),
        ("hlink", "0563C1"),
        ("folHlink", "954F72"),
    ]
    color_xml = "".join(f'<a:{name}><a:srgbClr val="{value}"/></a:{name}>' for name, value in colors)
    fills = "".join(
        f'<a:solidFill><a:schemeClr val="{name}"/></a:solidFill>'
        for name in ("accent1", "accent2", "accent3")
    )
    lines = "".join(
        f'<a:ln w="{width}" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="accent1"/>'
        f'</a:solidFill><a:prstDash val="solid"/></a:ln>'
        for width in (6350, 12700, 19050)
    )
    effects = "<a:effectStyle><a:effectLst/></a:effectStyle>" * 3
    backgrounds = "".join(
        f'<a:solidFill><a:schemeClr val="{name}"/></a:solidFill>' for name in ("lt1", "lt2", "dk1")
    )
    return _xml(
        f'<a:theme xmlns:a="{A_NS}" name="Open Deck Local"><a:themeElements>'
        f'<a:clrScheme name="Open Deck Local">{color_xml}</a:clrScheme>'
        '<a:fontScheme name="Open Deck Local"><a:majorFont><a:latin typeface="Arial"/>'
        '<a:ea typeface="Arial"/><a:cs typeface="Arial"/></a:majorFont><a:minorFont>'
        '<a:latin typeface="Arial"/><a:ea typeface="Arial"/><a:cs typeface="Arial"/>'
        f'</a:minorFont></a:fontScheme><a:fmtScheme name="Open Deck Local"><a:fillStyleLst>{fills}'
        f'</a:fillStyleLst><a:lnStyleLst>{lines}</a:lnStyleLst><a:effectStyleLst>{effects}'
        f'</a:effectStyleLst><a:bgFillStyleLst>{backgrounds}</a:bgFillStyleLst></a:fmtScheme>'
        '</a:themeElements></a:theme>'
    )


def _slide_master() -> bytes:
    return _xml(
        f'<p:sldMaster xmlns:p="{P_NS}" xmlns:a="{A_NS}" xmlns:r="{R_NS}"><p:cSld>'
        f'{_group_shape_tree_start()}</p:spTree></p:cSld>'
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
        'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
        'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        '<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles></p:sldMaster>'
    )


def _slide_layout() -> bytes:
    return _xml(
        f'<p:sldLayout xmlns:p="{P_NS}" xmlns:a="{A_NS}" xmlns:r="{R_NS}" type="blank" preserve="1">'
        f'<p:cSld name="Blank">{_group_shape_tree_start()}</p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    )


def _presentation(deck: DeckIR, notes_master: bool) -> Tuple[bytes, bytes]:
    master_id = '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
    notes_id = '<p:notesMasterIdLst><p:notesMasterId r:id="rId3"/></p:notesMasterIdLst>' if notes_master else ""
    slide_start = 4 if notes_master else 3
    slide_ids = "".join(
        f'<p:sldId id="{255 + page.index}" r:id="rId{slide_start + page.index - 1}"/>' for page in deck.pages
    )
    presentation = _xml(
        f'<p:presentation xmlns:p="{P_NS}" xmlns:a="{A_NS}" xmlns:r="{R_NS}">'
        f'{master_id}{notes_id}<p:sldIdLst>{slide_ids}</p:sldIdLst>'
        f'<p:sldSz cx="{_emu(deck.width)}" cy="{_emu(deck.height)}" type="custom"/>'
        '<p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
    )
    entries: List[Tuple[str, str, str]] = [
        ("rId1", REL_BASE + "slideMaster", "slideMasters/slideMaster1.xml"),
        ("rId2", REL_BASE + "theme", "theme/theme1.xml"),
    ]
    if notes_master:
        entries.append(("rId3", REL_BASE + "notesMaster", "notesMasters/notesMaster1.xml"))
    for page in deck.pages:
        identifier = slide_start + page.index - 1
        entries.append((f"rId{identifier}", REL_BASE + "slide", f"slides/slide{page.index}.xml"))
    return presentation, _relationships(entries)


def _content_types(deck: DeckIR, media: Mapping[str, str], notes_pages: Sequence[int]) -> bytes:
    defaults = [
        ("rels", "application/vnd.openxmlformats-package.relationships+xml"),
        ("xml", "application/xml"),
    ]
    extensions = {PurePosixPath(name).suffix[1:] for name in media.values()}
    if "png" in extensions:
        defaults.append(("png", "image/png"))
    if "jpg" in extensions:
        defaults.append(("jpg", "image/jpeg"))
    overrides = [
        ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml"),
        ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
        ("/ppt/presentation.xml", PPTX_CONTENT_TYPE),
        ("/ppt/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml"),
        ("/ppt/slideMasters/slideMaster1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"),
        ("/ppt/slideLayouts/slideLayout1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"),
    ]
    if notes_pages:
        overrides.append(("/ppt/notesMasters/notesMaster1.xml", NOTES_MASTER_CONTENT_TYPE))
    overrides.extend((f"/ppt/slides/slide{page.index}.xml", SLIDE_CONTENT_TYPE) for page in deck.pages)
    overrides.extend((f"/ppt/notesSlides/notesSlide{index}.xml", NOTES_CONTENT_TYPE) for index in notes_pages)
    default_xml = "".join(f'<Default Extension="{ext}" ContentType="{mime}"/>' for ext, mime in defaults)
    override_xml = "".join(
        f'<Override PartName="{part}" ContentType="{mime}"/>' for part, mime in overrides
    )
    return _xml(f'<Types xmlns="{CT_NS}">{default_xml}{override_xml}</Types>')


def _core_properties(deck: DeckIR) -> bytes:
    title = deck.title or deck.source.stem
    return _xml(
        f'<cp:coreProperties xmlns:cp="{CP_NS}" xmlns:dc="{DC_NS}"><dc:title>{_text(title)}</dc:title>'
        '<dc:creator>Open Deck Skill Local Exporter</dc:creator></cp:coreProperties>'
    )


def _app_properties(deck: DeckIR, notes_count: int) -> bytes:
    return _xml(
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        f'<Application>Open Deck Skill Local Exporter</Application><Slides>{len(deck.pages)}</Slides>'
        f'<Notes>{notes_count}</Notes></Properties>'
    )


def _json_object_pairs(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LocalExportError(f"deck.meta.json contains duplicate key {key!r}")
        result[key] = value
    return result


def _load_metadata(deck: DeckIR) -> Tuple[str, List[Optional[Dict[str, Any]]], bool]:
    metadata_path = deck.project_root / "deck.meta.json"
    if not metadata_path.exists():
        return "en-US", [None for _page in deck.pages], False
    try:
        info = metadata_path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise LocalExportError("deck.meta.json must be a regular, non-symlink file")
        if info.st_size > MAX_METADATA_BYTES:
            raise LocalExportError("deck.meta.json exceeds 1 MiB")
        raw = metadata_path.read_bytes()
        after = metadata_path.stat()
    except OSError as exc:
        raise LocalExportError(f"cannot read deck.meta.json: {exc}") from exc
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise LocalExportError("deck.meta.json changed while it was read")
    try:
        metadata = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object_pairs)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise LocalExportError(f"invalid strict UTF-8 deck.meta.json: {exc}") from exc
    if not isinstance(metadata, dict):
        raise LocalExportError("deck.meta.json must contain an object")
    unknown = sorted(set(metadata) - METADATA_TOP_LEVEL_KEYS)
    if unknown:
        raise LocalExportError("deck.meta.json contains unknown fields: " + ", ".join(unknown))
    if metadata.get("schemaVersion") != 1:
        raise LocalExportError("deck.meta.json schemaVersion must be 1")
    language = metadata.get("primaryLanguage", "en-US")
    if not isinstance(language, str) or BCP47_RE.fullmatch(language) is None:
        raise LocalExportError("deck.meta.json primaryLanguage must be a BCP-47 language tag")
    transitions = metadata.get("transitions", {})
    if not isinstance(transitions, dict) or set(transitions) - {"default", "pages"}:
        raise LocalExportError("deck.meta.json transitions must contain only default and pages")

    def validate_transition(value: Any, location: str) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise LocalExportError(f"{location} must be an object")
        unknown_transition = sorted(set(value) - {"type", "direction", "durationMs", "advanceAfterMs", "advanceOnClick"})
        if unknown_transition:
            raise LocalExportError(f"{location} contains unknown fields: {', '.join(unknown_transition)}")
        transition_type = value.get("type")
        if transition_type not in SAFE_TRANSITIONS:
            raise LocalExportError(f"{location}.type must be one of: {', '.join(sorted(SAFE_TRANSITIONS))}")
        if transition_type == "morph":
            raise LocalExportError(f"{location}: morph is safe metadata but is not supported by the local OOXML MVP")
        direction = value.get("direction")
        if direction is not None and direction not in TRANSITION_DIRECTIONS:
            raise LocalExportError(f"{location}.direction must be left, right, up, or down")
        if direction is not None and transition_type not in {"push", "wipe"}:
            raise LocalExportError(f"{location}.direction is meaningful only for push or wipe")
        duration = value.get("durationMs")
        if duration is not None and (
            not isinstance(duration, int) or isinstance(duration, bool) or not 0 <= duration <= 60_000
        ):
            raise LocalExportError(f"{location}.durationMs must be an integer from 0 to 60000")
        if duration is not None and transition_type == "none":
            raise LocalExportError(f"{location}.durationMs cannot be encoded for transition type none")
        advance = value.get("advanceAfterMs")
        if advance is not None and (
            not isinstance(advance, int) or isinstance(advance, bool) or not 0 <= advance <= 86_400_000
        ):
            raise LocalExportError(f"{location}.advanceAfterMs must be an integer from 0 to 86400000")
        click = value.get("advanceOnClick")
        if click is not None and not isinstance(click, bool):
            raise LocalExportError(f"{location}.advanceOnClick must be boolean")
        return dict(value)

    default = validate_transition(transitions["default"], "transitions.default") if "default" in transitions else None
    pages = transitions.get("pages", {})
    if not isinstance(pages, dict):
        raise LocalExportError("transitions.pages must be an object")
    known_pages = {page.project_path for page in deck.pages}
    unknown_pages = sorted(set(pages) - known_pages)
    if unknown_pages:
        raise LocalExportError("transition overrides refer to unknown pages: " + ", ".join(unknown_pages))
    overrides = {
        key: validate_transition(value, f"transitions.pages[{key!r}]") for key, value in pages.items()
    }
    return language, [overrides.get(page.project_path, default) for page in deck.pages], True


def _collect_media(deck: DeckIR) -> Tuple[Dict[str, ImageAsset], Dict[str, str]]:
    assets: Dict[str, ImageAsset] = {}
    for page in deck.pages:
        for element in page.elements:
            if element.kind == "image":
                asset: ImageAsset = element.properties["asset"]
                assets.setdefault(asset.sha256, asset)
    names: Dict[str, str] = {}
    for index, digest in enumerate(sorted(assets), start=1):
        names[digest] = f"image{index}{assets[digest].extension}"
    return assets, names


def _zip_write(archive: zipfile.ZipFile, name: str, data: bytes, *, compress: bool = True) -> None:
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    info.external_attr = 0o600 << 16
    archive.writestr(info, data)


def _build_archive(
    deck: DeckIR,
    destination: Path,
    language: str,
    transitions: Sequence[Optional[Mapping[str, Any]]],
) -> Dict[str, Any]:
    assets, media_names = _collect_media(deck)
    notes_pages = [page.index for page in deck.pages if page.notes is not None]
    presentation, presentation_rels = _presentation(deck, bool(notes_pages))
    with zipfile.ZipFile(destination, "w", allowZip64=True) as archive:
        _zip_write(archive, "[Content_Types].xml", _content_types(deck, media_names, notes_pages))
        _zip_write(
            archive,
            "_rels/.rels",
            _relationships(
                [
                    ("rId1", REL_BASE + "officeDocument", "ppt/presentation.xml"),
                    ("rId2", PACKAGE_REL_BASE + "metadata/core-properties", "docProps/core.xml"),
                    ("rId3", REL_BASE + "extended-properties", "docProps/app.xml"),
                ]
            ),
        )
        _zip_write(archive, "docProps/core.xml", _core_properties(deck))
        _zip_write(archive, "docProps/app.xml", _app_properties(deck, len(notes_pages)))
        _zip_write(archive, "ppt/presentation.xml", presentation)
        _zip_write(archive, "ppt/_rels/presentation.xml.rels", presentation_rels)
        _zip_write(archive, "ppt/theme/theme1.xml", _theme())
        _zip_write(archive, "ppt/slideMasters/slideMaster1.xml", _slide_master())
        _zip_write(
            archive,
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            _relationships(
                [
                    ("rId1", REL_BASE + "slideLayout", "../slideLayouts/slideLayout1.xml"),
                    ("rId2", REL_BASE + "theme", "../theme/theme1.xml"),
                ]
            ),
        )
        _zip_write(archive, "ppt/slideLayouts/slideLayout1.xml", _slide_layout())
        _zip_write(
            archive,
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            _relationships([("rId1", REL_BASE + "slideMaster", "../slideMasters/slideMaster1.xml")]),
        )
        if notes_pages:
            _zip_write(archive, "ppt/notesMasters/notesMaster1.xml", _notes_master())
            _zip_write(
                archive,
                "ppt/notesMasters/_rels/notesMaster1.xml.rels",
                _relationships([("rId1", REL_BASE + "theme", "../theme/theme1.xml")]),
            )
        image_relationships = 0
        for page, transition in zip(deck.pages, transitions):
            slide, relationships, image_count = _slide_parts(deck, page, language, transition, media_names)
            image_relationships += image_count
            _zip_write(archive, f"ppt/slides/slide{page.index}.xml", slide)
            _zip_write(archive, f"ppt/slides/_rels/slide{page.index}.xml.rels", relationships)
            if page.notes is not None:
                _zip_write(archive, f"ppt/notesSlides/notesSlide{page.index}.xml", _notes_slide(page, language))
                _zip_write(
                    archive,
                    f"ppt/notesSlides/_rels/notesSlide{page.index}.xml.rels",
                    _relationships(
                        [
                            ("rId1", REL_BASE + "notesMaster", "../notesMasters/notesMaster1.xml"),
                            ("rId2", REL_BASE + "slide", f"../slides/slide{page.index}.xml"),
                        ]
                    ),
                )
        for digest in sorted(assets):
            _zip_write(archive, "ppt/media/" + media_names[digest], assets[digest].data, compress=False)
    return {
        "slides": len(deck.pages),
        "shapes": sum(len(page.elements) for page in deck.pages),
        "media": len(assets),
        "imageRelationships": image_relationships,
        "notes": len(notes_pages),
    }


def _relationship_source(name: str) -> Tuple[str, str]:
    if name == "_rels/.rels":
        return "", ""
    path = PurePosixPath(name)
    if len(path.parts) < 3 or path.parts[-2] != "_rels" or not path.name.endswith(".rels"):
        raise LocalExportError(f"non-canonical relationships part: {name}")
    source_name = path.name[: -len(".rels")]
    source = PurePosixPath(*path.parts[:-2], source_name).as_posix()
    return source, PurePosixPath(source).parent.as_posix()


def verify_local_pptx(path: os.PathLike[str] | str, *, expected_slides: Optional[int] = None) -> Dict[str, int]:
    """Perform bounded ZIP/XML/OPC graph checks on a generated local deck."""

    candidate = Path(path)
    try:
        size = candidate.stat().st_size
    except OSError as exc:
        raise LocalExportError(f"cannot stat generated PPTX: {exc}") from exc
    if size < 1 or size > MAX_OUTPUT_BYTES:
        raise LocalExportError("generated PPTX has an invalid size")
    try:
        with zipfile.ZipFile(candidate) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or len({name.casefold() for name in names}) != len(names):
                raise LocalExportError("generated PPTX has duplicate or case-colliding members")
            required = {
                "[Content_Types].xml",
                "_rels/.rels",
                "ppt/presentation.xml",
                "ppt/_rels/presentation.xml.rels",
                "ppt/theme/theme1.xml",
                "ppt/slideMasters/slideMaster1.xml",
                "ppt/slideLayouts/slideLayout1.xml",
            }
            if not required.issubset(names):
                raise LocalExportError("generated PPTX is missing required OPC parts")
            if archive.testzip() is not None:
                raise LocalExportError("generated PPTX failed its CRC check")
            xml_names = [name for name in names if name.endswith((".xml", ".rels")) or name == "[Content_Types].xml"]
            roots: Dict[str, ET.Element] = {}
            for name in xml_names:
                data = archive.read(name)
                if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                    raise LocalExportError(f"generated XML contains a forbidden DTD/entity: {name}")
                try:
                    roots[name] = ET.fromstring(data)
                except ET.ParseError as exc:
                    raise LocalExportError(f"generated XML is malformed at {name}: {exc}") from exc
            name_set = set(names)
            for rel_name in (name for name in names if name.endswith(".rels")):
                source, base = _relationship_source(rel_name)
                if source and source not in name_set:
                    raise LocalExportError(f"relationships source is missing: {source}")
                for relationship in roots[rel_name]:
                    if relationship.get("TargetMode") == "External":
                        raise LocalExportError("offline exporter emitted an external relationship")
                    target = relationship.get("Target")
                    if not target or target.startswith(("/", "\\")):
                        raise LocalExportError(f"invalid relationship target in {rel_name}")
                    resolved = posixpath.normpath(posixpath.join(base, target))
                    if resolved == ".." or resolved.startswith("../") or resolved not in name_set:
                        raise LocalExportError(f"broken relationship {rel_name} -> {target}")
            slides = sorted(
                (name for name in names if re.fullmatch(r"ppt/slides/slide[1-9]\d*\.xml", name)),
                key=lambda value: int(re.search(r"\d+", PurePosixPath(value).name).group()),
            )
            if expected_slides is not None and len(slides) != expected_slides:
                raise LocalExportError(
                    f"generated slide count mismatch: expected {expected_slides}, received {len(slides)}"
                )
            presentation = roots["ppt/presentation.xml"]
            sld_ids = presentation.findall(f".//{{{P_NS}}}sldId")
            if len(sld_ids) != len(slides):
                raise LocalExportError("presentation slide list does not match slide parts")
            for slide in slides:
                root = roots[slide]
                if root.tag != f"{{{P_NS}}}sld" or root.find(f"./{{{P_NS}}}cSld/{{{P_NS}}}spTree") is None:
                    raise LocalExportError(f"slide has an invalid PresentationML root/tree: {slide}")
            return {
                "bytes": size,
                "members": len(names),
                "slides": len(slides),
                "notes": sum(1 for name in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", name)),
                "media": sum(1 for name in names if name.startswith("ppt/media/")),
            }
    except LocalExportError:
        raise
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise LocalExportError(f"invalid generated PPTX: {exc}") from exc


def _default_output(source: Path) -> Path:
    if source.is_file():
        return source.with_suffix(".local.pptx")
    return source / (source.resolve().name + ".local.pptx")


def _paths_alias(left: Path, right: Path) -> bool:
    """Return true for lexical, symlink, or existing hard-link aliases."""

    try:
        if left.resolve(strict=False) == right.resolve(strict=False):
            return True
    except (OSError, RuntimeError, ValueError):
        pass
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def export_local_pptx(
    source: os.PathLike[str] | str,
    output: os.PathLike[str] | str,
    *,
    quality_report: os.PathLike[str] | str,
    force: bool = False,
) -> Dict[str, Any]:
    """Build, verify, and atomically publish one local PPTX."""

    # Contractually first: a stale/weak/tampered report must be rejected before
    # Deck IR parsing, staging-file creation, or any output artifact write.
    quality_receipt = _verify_quality_gate(source, quality_report)
    deck = load_deck_ir(source)
    language, transitions, metadata_present = _load_metadata(deck)
    confirmed_receipt = _verify_quality_gate(source, quality_report)
    if confirmed_receipt != quality_receipt:
        raise LocalExportError("quality receipt changed while project inputs were captured")
    requested = Path(output).expanduser()
    if not requested.name or requested.name in {".", ".."}:
        raise LocalExportError(f"invalid output path: {output}")
    try:
        destination = requested.parent.resolve(strict=True) / requested.name
    except OSError as exc:
        raise LocalExportError(f"output directory does not exist: {requested.parent}") from exc
    if destination.suffix.casefold() != ".pptx":
        raise LocalExportError("output must have a .pptx extension")
    if destination.is_symlink():
        raise LocalExportError(f"output destination must not be a symbolic link: {destination}")
    protected_inputs = {
        *_quality_bound_inputs(deck.project_root, quality_report),
        Path(quality_report).expanduser(),
    }
    if any(_paths_alias(destination, path) for path in protected_inputs):
        raise LocalExportError(f"output would overwrite a bound project input: {destination}")
    if destination.exists() and not force:
        raise LocalExportError(f"output already exists (use --force to replace it): {destination}")
    descriptor, staging_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    os.close(descriptor)
    staging = Path(staging_name)
    try:
        build = _build_archive(deck, staging, language, transitions)
        _fsync_regular_file(staging)
        verified = verify_local_pptx(staging, expected_slides=len(deck.pages))
        digest = hashlib.sha256(staging.read_bytes()).hexdigest()
        final_receipt = _verify_quality_gate(source, quality_report)
        if final_receipt != quality_receipt:
            raise LocalExportError("quality receipt changed before publication")
        if force:
            # Refuse a link that appeared after preflight.  os.replace would
            # replace the directory entry rather than its target, but silently
            # accepting a raced destination would violate the output contract.
            if destination.is_symlink():
                raise LocalExportError(
                    f"output destination became a symbolic link: {destination}"
                )
            os.replace(staging, destination)
        else:
            try:
                os.link(staging, destination)
            except FileExistsError as exc:
                raise LocalExportError(f"output appeared during publication: {destination}") from exc
            staging.unlink()
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
    transition_counts: Dict[str, int] = {}
    for transition in transitions:
        key = transition["type"] if transition is not None else "unspecified"
        transition_counts[key] = transition_counts.get(key, 0) + 1
    receipt: Dict[str, Any] = {
        "ok": True,
        "backend": "local-ooxml-mvp",
        "output": str(destination),
        "sha256": digest,
        "bytes": verified["bytes"],
        "slides": build["slides"],
        "shapes": build["shapes"],
        "media": build["media"],
        "notes": build["notes"],
        "language": language,
        "transitions": transition_counts,
        "metadata": metadata_present,
        "quality": quality_receipt,
        "warnings": [item.as_dict() for item in deck.diagnostics],
    }
    return receipt


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline, fail-closed PPTD v2 to editable OOXML PPTX MVP exporter."
    )
    parser.add_argument("source", help=".pptd manifest or project directory")
    parser.add_argument("-o", "--output", help="output .pptx path (default: <manifest>.local.pptx)")
    parser.add_argument(
        "--quality-report",
        required=True,
        help="current source-bound pptd_quality full JSON report (fail_on=warning or stricter)",
    )
    parser.add_argument("--force", action="store_true", help="atomically replace an existing output")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    source = Path(args.source).expanduser()
    output = Path(args.output).expanduser() if args.output else _default_output(source)
    try:
        receipt = export_local_pptx(
            source,
            output,
            quality_report=args.quality_report,
            force=args.force,
        )
    except DeckIRValidationError as exc:
        payload = {
            "ok": False,
            "error": "pptd_validation_failed",
            "diagnostics": [item.as_dict() for item in exc.diagnostics],
        }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 2
    except (LocalExportError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": "local_export_failed", "message": str(exc)},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["LocalExportError", "export_local_pptx", "verify_local_pptx"]
