#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pptx_native.py"
SPEC = importlib.util.spec_from_file_location("pptx_native", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/notesSlides/notesSlide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>
  <Override PartName="/ppt/charts/chart1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>"""

PRESENTATION = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
  <p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst>
</p:presentation>"""

PRESENTATION_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>"""

SLIDE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:cSld name="Fixture slide"><p:spTree>
    <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>
    <p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr><a:prstGeom prst="rect"/></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US" sz="3200"/><a:t>Original title</a:t></a:r></a:p></p:txBody></p:sp>
    <p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="3" name="Table 2"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl><a:tblPr/><a:tblGrid><a:gridCol w="100"/><a:gridCol w="100"/></a:tblGrid><a:tr h="100"><a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>A1</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc><a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>B1</a:t></a:r></a:p></a:txBody><a:tcPr/></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>
    <p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Chart 3"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" r:id="rId3"/></a:graphicData></a:graphic></p:graphicFrame>
  </p:spTree></p:cSld>
  <p:timing><p:tnLst><p:par><p:cTn id="1" dur="indefinite"/></p:par></p:tnLst></p:timing>
</p:sld>"""

SLIDE_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart1.xml"/>
</Relationships>"""

LAYOUT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" type="title" name="Title layout"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Layout title"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp></p:spTree></p:cSld></p:sldLayout>"""

LAYOUT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>"""

MASTER = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld name="Fixture master"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Master footer"/><p:cNvSpPr/><p:nvPr><p:ph type="ftr"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Footer</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld><p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></p:sldLayoutIdLst></p:sldMaster>"""

MASTER_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>"""

NOTES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Notes Placeholder 1"/><p:cNvSpPr/><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US"/><a:t>Original notes</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>"""

NOTES_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="../slides/slide1.xml"/>
</Relationships>"""

CHART = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart><c:plotArea><c:layout/><c:barChart><c:barDir val="col"/><c:ser><c:idx val="0"/><c:order val="0"/><c:tx><c:v>Revenue</c:v></c:tx><c:cat><c:strLit><c:ptCount val="2"/><c:pt idx="0"><c:v>Q1</c:v></c:pt><c:pt idx="1"><c:v>Q2</c:v></c:pt></c:strLit></c:cat><c:val><c:numLit><c:ptCount val="2"/><c:pt idx="0"><c:v>10</c:v></c:pt><c:pt idx="1"><c:v>20</c:v></c:pt></c:numLit></c:val></c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>"""


def write_fixture(path, *, broken_target=False, duplicate_slide=False):
    slide_rels = SLIDE_RELS
    if broken_target:
        slide_rels = slide_rels.replace(
            "../slideLayouts/slideLayout1.xml", "../slideLayouts/missing.xml"
        )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        parts = {
            "[Content_Types].xml": CONTENT_TYPES,
            "_rels/.rels": ROOT_RELS,
            "ppt/presentation.xml": PRESENTATION,
            "ppt/_rels/presentation.xml.rels": PRESENTATION_RELS,
            "ppt/slides/slide1.xml": SLIDE,
            "ppt/slides/_rels/slide1.xml.rels": slide_rels,
            "ppt/slideLayouts/slideLayout1.xml": LAYOUT,
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels": LAYOUT_RELS,
            "ppt/slideMasters/slideMaster1.xml": MASTER,
            "ppt/slideMasters/_rels/slideMaster1.xml.rels": MASTER_RELS,
            "ppt/notesSlides/notesSlide1.xml": NOTES,
            "ppt/notesSlides/_rels/notesSlide1.xml.rels": NOTES_RELS,
            "ppt/charts/chart1.xml": CHART,
        }
        for name, data in parts.items():
            archive.writestr(name, data)
        if duplicate_slide:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr("ppt/slides/slide1.xml", SLIDE)


def raw_timing_fingerprint(path):
    with zipfile.ZipFile(path, "r") as archive:
        root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
    timing = root.find(f"{{{MODULE.P_NS}}}timing")
    assert timing is not None
    return hashlib.sha256(ET.tostring(timing, encoding="utf-8")).hexdigest()


class PptxNativeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "source.pptx"
        write_fixture(self.source)

    def tearDown(self):
        self.temporary.cleanup()

    def test_windows_path_and_descriptor_ids_do_not_trigger_false_change(self):
        destination = self.directory / "staged.pptx"
        destination.write_bytes(b"")
        actual = self.source.lstat()
        path_values = list(actual)
        path_values[1] = actual.st_ino + 97
        path_snapshot = type(actual)(path_values)

        with (
            patch.object(MODULE.os, "name", "nt"),
            patch.object(type(self.source), "lstat", return_value=path_snapshot),
        ):
            MODULE._copy_regular_source(self.source, destination)

        self.assertEqual(destination.read_bytes(), self.source.read_bytes())

    def test_completed_archive_fsync_uses_a_writable_handle(self):
        opened_modes = []
        original_open = Path.open

        def recording_open(path, mode="r", *args, **kwargs):
            if path == self.source:
                opened_modes.append(mode)
            return original_open(path, mode, *args, **kwargs)

        with (
            patch.object(Path, "open", new=recording_open),
            patch.object(MODULE.os, "fsync") as fsync,
        ):
            MODULE._fsync_regular_file(self.source)

        self.assertEqual(opened_modes, ["r+b"])
        fsync.assert_called_once()

    def test_validate_and_inspect_full_inventory(self):
        validation = MODULE.validate_pptx(self.source)
        self.assertEqual(validation["status"], "valid")
        self.assertEqual(validation["package"]["slides"], 1)
        self.assertEqual(validation["package"]["masters"], 1)
        self.assertEqual(validation["package"]["layouts"], 1)

        report = MODULE.inspect_pptx(self.source)
        self.assertEqual(report["schema"], MODULE.INSPECT_SCHEMA)
        self.assertEqual(report["slides"][0]["layout_part"], "ppt/slideLayouts/slideLayout1.xml")
        self.assertEqual(report["slides"][0]["master_part"], "ppt/slideMasters/slideMaster1.xml")
        title = report["slides"][0]["shapes"][0]
        self.assertEqual(
            (title["id"], title["name"], title["type"], title["text"]),
            (2, "Title 1", "placeholder", "Original title"),
        )
        self.assertEqual(report["slides"][0]["notes"]["text"], "Original notes")
        self.assertEqual(report["slides"][0]["tables"][0]["cells"], [["A1", "B1"]])
        chart = report["slides"][0]["charts"][0]
        self.assertEqual(chart["part"], "ppt/charts/chart1.xml")
        self.assertEqual(chart["chart_types"], ["barChart"])
        self.assertEqual(chart["series"][0]["name"], "Revenue")
        self.assertEqual(chart["series"][0]["categories"], ["Q1", "Q2"])
        self.assertEqual(chart["series"][0]["values"], ["10", "20"])
        self.assertEqual(report["masters"][0]["shapes"][0]["name"], "Master footer")
        self.assertEqual(report["layouts"][0]["shapes"][0]["name"], "Layout title")

    def test_fill_text_and_notes_preserves_timing_and_source(self):
        output = self.directory / "filled.pptx"
        source_bytes = self.source.read_bytes()
        source_timing = raw_timing_fingerprint(self.source)
        receipt = MODULE.fill_pptx(
            self.source,
            {
                "replacements": [
                    {"slide": 1, "shape_id": 2, "text": " 新标题\nSecond line ", "lang": "zh-cn"}
                ],
                "notes": [{"slide": 1, "text": "新的备注", "lang": "zh-Hans-CN"}],
            },
            output,
        )
        self.assertEqual(receipt["status"], "written")
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertTrue(output.exists())
        self.assertEqual(raw_timing_fingerprint(output), source_timing)
        self.assertEqual(MODULE.validate_pptx(output)["status"], "valid")
        report = MODULE.inspect_pptx(output)
        self.assertEqual(report["slides"][0]["shapes"][0]["text"], " 新标题\nSecond line ")
        self.assertEqual(report["slides"][0]["notes"]["text"], "新的备注")
        with zipfile.ZipFile(output, "r") as archive:
            slide_root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            langs = [
                element.get("lang")
                for element in slide_root.findall(f".//{{{MODULE.A_NS}}}rPr")
            ]
            self.assertIn("zh-CN", langs)
            notes_root = ET.fromstring(archive.read("ppt/notesSlides/notesSlide1.xml"))
            notes_langs = [
                element.get("lang")
                for element in notes_root.findall(f".//{{{MODULE.A_NS}}}rPr")
            ]
            self.assertIn("zh-Hans-CN", notes_langs)

    def test_fill_by_shape_name_and_force_policy(self):
        output = self.directory / "filled.pptx"
        output.write_bytes(b"sentinel")
        plan = {
            "replacements": [
                {"slide": 1, "shape_name": "Title 1", "text": "By name"}
            ]
        }
        with self.assertRaisesRegex(MODULE.FillPlanError, "already exists"):
            MODULE.fill_pptx(self.source, plan, output)
        self.assertEqual(output.read_bytes(), b"sentinel")
        MODULE.fill_pptx(self.source, plan, output, force=True)
        self.assertEqual(
            MODULE.inspect_pptx(output)["slides"][0]["shapes"][0]["text"],
            "By name",
        )

    def test_invalid_candidate_never_replaces_output_and_temporaries_are_cleaned(self):
        output = self.directory / "protected.pptx"
        output.write_bytes(b"existing output")

        def write_malformed_candidate(_source, destination, _modifications):
            destination.write_bytes(b"not a zip package")

        with patch.object(
            MODULE, "_write_candidate", side_effect=write_malformed_candidate
        ):
            with self.assertRaisesRegex(MODULE.ValidationError, "invalid PPTX ZIP"):
                MODULE.fill_pptx(
                    self.source,
                    {
                        "replacements": [
                            {"slide": 1, "shape_id": 2, "text": "Never publish"}
                        ]
                    },
                    output,
                    force=True,
                )

        self.assertEqual(output.read_bytes(), b"existing output")
        self.assertEqual(
            list(self.directory.glob(".pptx-native-source-*.pptx")), []
        )
        self.assertEqual(
            list(self.directory.glob(".pptx-native-candidate-*.pptx")), []
        )

    def test_atomic_no_replace_publish_rejects_destination_race(self):
        candidate = self.directory / "candidate.pptx"
        output = self.directory / "raced.pptx"
        candidate.write_bytes(b"complete candidate")
        output.write_bytes(b"racing writer")
        with self.assertRaisesRegex(MODULE.FillPlanError, "appeared during fill"):
            MODULE._publish_candidate(candidate, output, force=False)
        self.assertEqual(output.read_bytes(), b"racing writer")
        self.assertEqual(candidate.read_bytes(), b"complete candidate")

    def test_explicitly_rejects_table_and_unknown_edit_fields_without_output(self):
        output = self.directory / "rejected.pptx"
        with self.assertRaisesRegex(MODULE.UnsupportedObjectError, "table"):
            MODULE.fill_pptx(
                self.source,
                {"replacements": [{"slide": 1, "shape_id": 3, "text": "No"}]},
                output,
            )
        self.assertFalse(output.exists())
        with self.assertRaisesRegex(MODULE.FillPlanError, "transitions"):
            MODULE.fill_pptx(
                self.source,
                {"replacements": [{"slide": 1, "shape_id": 2, "text": "No"}], "transitions": []},
                output,
            )
        self.assertFalse(output.exists())

    def test_rejects_invalid_language_and_duplicate_target(self):
        output = self.directory / "rejected.pptx"
        with self.assertRaisesRegex(MODULE.FillPlanError, "BCP-47"):
            MODULE.fill_pptx(
                self.source,
                {"replacements": [{"slide": 1, "shape_id": 2, "text": "X", "lang": "en_US"}]},
                output,
            )
        self.assertFalse(output.exists())
        with self.assertRaisesRegex(MODULE.FillPlanError, "duplicate replacement"):
            MODULE.fill_pptx(
                self.source,
                {
                    "replacements": [
                        {"slide": 1, "shape_id": 2, "text": "X"},
                        {"slide": 1, "shape_name": "Title 1", "text": "Y"},
                    ]
                },
                output,
            )
        self.assertFalse(output.exists())

    def test_validate_rejects_missing_relationship_target_and_duplicate_member(self):
        broken = self.directory / "broken.pptx"
        write_fixture(broken, broken_target=True)
        report = MODULE.validate_pptx(broken)
        self.assertEqual(report["status"], "invalid")
        self.assertIn("targets missing part", report["errors"][0])

        duplicate = self.directory / "duplicate.pptx"
        write_fixture(duplicate, duplicate_slide=True)
        duplicate_report = MODULE.validate_pptx(duplicate)
        self.assertEqual(duplicate_report["status"], "invalid")
        self.assertIn("duplicate ZIP member", duplicate_report["errors"][0])

    def test_rejects_late_dtd_and_entity_declaration_after_prefix_scan_boundary(self):
        documents = {
            "late-declaration.xml": (
                b" " * (64 * 1024 + 128)
                + b'<!DOCTYPE root [<!ENTITY late "expanded">]><root>&late;</root>'
            ),
            "late-declaration-utf16.xml": (
                " " * (64 * 1024 + 128)
                + '<!DOCTYPE root [<!ENTITY late "expanded">]><root>&late;</root>'
            ).encode("utf-16"),
        }
        for name, payload in documents.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(MODULE.ValidationError, "DTD/entity"):
                    MODULE._parse_xml(payload, name)

    def test_fill_rejects_source_as_output_even_with_force(self):
        source_bytes = self.source.read_bytes()
        with self.assertRaisesRegex(MODULE.FillPlanError, "different files"):
            MODULE.fill_pptx(
                self.source,
                {"replacements": [{"slide": 1, "shape_id": 2, "text": "No"}]},
                self.source,
                force=True,
            )
        self.assertEqual(self.source.read_bytes(), source_bytes)

    def test_fill_rejects_symlink_source_before_staging_mutation(self):
        linked_source = self.directory / "linked-source.pptx"
        linked_source.symlink_to(self.source.name)
        output = self.directory / "not-written.pptx"
        with self.assertRaisesRegex(MODULE.ValidationError, "non-symlink"):
            MODULE.fill_pptx(
                linked_source,
                {"replacements": [{"slide": 1, "shape_id": 2, "text": "No"}]},
                output,
            )
        self.assertFalse(output.exists())
        self.assertEqual(list(self.directory.glob(".pptx-native-source-*.pptx")), [])
        self.assertEqual(list(self.directory.glob(".pptx-native-candidate-*.pptx")), [])

    def test_fill_force_rejects_output_symlink_without_touching_target(self):
        target = self.directory / "target.pptx"
        target.write_bytes(b"do not replace")
        output = self.directory / "linked-output.pptx"
        output.symlink_to(target.name)
        with self.assertRaisesRegex(MODULE.FillPlanError, "symbolic link"):
            MODULE.fill_pptx(
                self.source,
                {"replacements": [{"slide": 1, "shape_id": 2, "text": "No"}]},
                output,
                force=True,
            )
        self.assertTrue(output.is_symlink())
        self.assertEqual(target.read_bytes(), b"do not replace")

    def test_fill_plan_resource_limits_are_enforced_before_output(self):
        output = self.directory / "limited.pptx"
        with patch.object(MODULE, "MAX_FILL_EDITS", 1):
            with self.assertRaisesRegex(MODULE.FillPlanError, "more than 1 edits"):
                MODULE.fill_pptx(
                    self.source,
                    {
                        "replacements": [
                            {"slide": 1, "shape_id": 2, "text": "A"},
                            {"slide": 1, "shape_name": "Title 1", "text": "B"},
                        ]
                    },
                    output,
                )
        with patch.object(MODULE, "MAX_FILL_TEXT_BYTES", 3):
            with self.assertRaisesRegex(MODULE.FillPlanError, "total safety limit"):
                MODULE.fill_pptx(
                    self.source,
                    {"replacements": [{"slide": 1, "shape_id": 2, "text": "four"}]},
                    output,
                )

        plan_path = self.directory / "oversized-plan.json"
        plan_path.write_text('{"replacements":[]}', encoding="utf-8")
        with patch.object(MODULE, "MAX_FILL_PLAN_BYTES", 8):
            with self.assertRaisesRegex(MODULE.FillPlanError, "safety limit"):
                MODULE.fill_pptx(self.source, plan_path, output)
        self.assertFalse(output.exists())

    def test_cli_analyze_fill_and_validate(self):
        analyze = subprocess.run(
            [sys.executable, str(SCRIPT), "analyze", str(self.source)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(analyze.returncode, 0, analyze.stderr)
        self.assertEqual(json.loads(analyze.stdout)["slides"][0]["index"], 1)

        plan_path = self.directory / "plan.json"
        plan_path.write_text(
            json.dumps(
                {
                    "replacements": [
                        {"slide": 1, "shape_name": "Title 1", "text": "CLI title"}
                    ]
                }
            ),
            encoding="utf-8",
        )
        output = self.directory / "cli.pptx"
        fill = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "fill",
                str(self.source),
                str(plan_path),
                "--output",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(fill.returncode, 0, fill.stderr or fill.stdout)
        validate = subprocess.run(
            [sys.executable, str(SCRIPT), "validate", str(output)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(validate.returncode, 0, validate.stderr or validate.stdout)
        self.assertEqual(json.loads(validate.stdout)["status"], "valid")


if __name__ == "__main__":
    unittest.main()
