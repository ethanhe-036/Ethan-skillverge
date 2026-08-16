#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SCRIPTS))

import deck_ir  # noqa: E402
import export_pptx  # noqa: E402
import export_pptx_local  # noqa: E402
import pptd_quality  # noqa: E402


P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
JPEG_FIXTURE = REPOSITORY_ROOT / "example" / "dji-pocket4" / "media" / "night_vlog_bl.jpg"


def write_project(root: Path, *, unsupported: str = "") -> Path:
    (root / "pages").mkdir()
    (root / "media").mkdir()
    (root / "media" / "pixel.png").write_bytes(PNG_1X1)
    (root / "media" / "pixel.jpg").write_bytes(JPEG_FIXTURE.read_bytes())
    manifest = root / "sample.pptd"
    manifest.write_text(
        "version: v2\n"
        "title: Offline MVP\n"
        "size: [960, 540]\n"
        "theme:\n"
        "  colors:\n"
        "    accent: '#336699CC'\n"
        "  textStyles:\n"
        "    title: {fontSize: 28, color: '$accent', fontFamily: Arial, bold: true}\n"
        "pages:\n"
        "  - pages/01.page\n"
        "  - pages/02.page\n",
        encoding="utf-8",
    )
    (root / "pages" / "01.page").write_text(
        "pageType: content\n"
        "background: {type: solid, color: '#F7F8FC'}\n"
        "notes: |\n"
        "  First speaker note\n"
        "  第二行 & source\n"
        "elements:\n"
        "  - elementId: title\n"
        "    elementType: text\n"
        "    bounds: [40, 30, 600, 70]\n"
        "    content:\n"
        "      style: '$title'\n"
        "      align: [left, middle]\n"
        "      text: Offline local deck\n"
        "  - elementId: panel\n"
        "    elementType: shape\n"
        "    bounds: [40, 130, 220, 120]\n"
        "    shapeName: roundRect\n"
        "    adjustments: [16000]\n"
        "    fill: {type: solid, color: '$accent'}\n"
        "    border: {style: dash, width: 2, color: '#112233'}\n"
        "  - elementId: connector\n"
        "    elementType: line\n"
        "    bounds: [280, 150, 240, 80]\n"
        "    viewBox: [240, 80]\n"
        "    points: '0,0 240,80'\n"
        "    arrow: [null, arrow]\n"
        "    border: {style: solid, width: 2, color: '#123456'}\n"
        "  - elementId: png-picture\n"
        "    elementType: image\n"
        "    bounds: [550, 120, 160, 160]\n"
        "    src: media/pixel.png\n"
        "    fit: {mode: cover}\n"
        "  - elementId: jpeg-picture\n"
        "    elementType: image\n"
        "    bounds: [730, 120, 160, 100]\n"
        "    src: media/pixel.jpg\n"
        "    fit: {mode: contain}\n"
        + unsupported,
        encoding="utf-8",
    )
    (root / "pages" / "02.page").write_text(
        "background: {type: solid, color: '#FFFFFF'}\n"
        "elements:\n"
        "  - elementId: closing\n"
        "    elementType: text\n"
        "    bounds: [100, 180, 760, 120]\n"
        "    content: {fontSize: 36, color: '#111111', align: [center, middle], text: Done}\n",
        encoding="utf-8",
    )
    (root / "deck.meta.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "primaryLanguage": "en-AU",
                "transitions": {
                    "default": {
                        "type": "fade",
                        "durationMs": 500,
                        "advanceOnClick": True,
                    },
                    "pages": {
                        "pages/02.page": {
                            "type": "wipe",
                            "direction": "left",
                            "advanceAfterMs": 12000,
                            "advanceOnClick": False,
                        }
                    },
                },
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (root / "media" / "sources.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "assets": {
                    "media/pixel.png": {
                        "license": "CC0-1.0",
                        "sha256": hashlib.sha256(PNG_1X1).hexdigest(),
                    },
                    "media/pixel.jpg": {
                        "license": "user-supplied",
                        "sha256": hashlib.sha256(JPEG_FIXTURE.read_bytes()).hexdigest(),
                    },
                },
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return manifest


def write_quality_report(manifest: Path, *, fail_on: str = "warning") -> Path:
    report_path = manifest.parent / "quality-report.json"
    report = pptd_quality.lint_project(manifest, fail_on=fail_on)
    if not report["summary"]["passed"]:
        raise AssertionError(f"fixture did not pass its quality gate: {report['issues']}")
    pptd_quality.write_report(report_path, report)
    return report_path


class LocalPptxExportTests(unittest.TestCase):
    def test_completed_archive_fsync_uses_a_writable_handle(self):
        with tempfile.TemporaryDirectory() as name:
            candidate = Path(name) / "candidate.pptx"
            candidate.write_bytes(b"archive")
            opened_modes = []
            original_open = Path.open

            def recording_open(path, mode="r", *args, **kwargs):
                if path == candidate:
                    opened_modes.append(mode)
                return original_open(path, mode, *args, **kwargs)

            with (
                patch.object(Path, "open", new=recording_open),
                patch.object(export_pptx_local.os, "fsync") as fsync,
            ):
                export_pptx_local._fsync_regular_file(candidate)

            self.assertEqual(opened_modes, ["r+b"])
            fsync.assert_called_once()

    def test_end_to_end_ir_opc_media_notes_transitions_and_readback(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(root)
            deck = deck_ir.load_deck_ir(manifest)
            self.assertEqual((deck.width, deck.height), (960, 540))
            self.assertEqual([len(page.elements) for page in deck.pages], [5, 1])
            self.assertEqual({element.kind for element in deck.pages[0].elements}, {"text", "shape", "line", "image"})
            self.assertTrue(all(element.shape_id > 1 for page in deck.pages for element in page.elements))

            first = root / "first.pptx"
            quality_report = write_quality_report(manifest)
            receipt = export_pptx_local.export_local_pptx(
                manifest,
                first,
                quality_report=quality_report,
            )
            self.assertEqual(receipt["backend"], "local-ooxml-mvp")
            self.assertEqual(receipt["slides"], 2)
            self.assertEqual(receipt["media"], 2)
            self.assertEqual(receipt["notes"], 1)
            self.assertEqual(receipt["language"], "en-AU")
            self.assertEqual(receipt["transitions"], {"fade": 1, "wipe": 1})
            self.assertEqual(receipt["quality"]["fail_on"], "warning")
            self.assertTrue(receipt["quality"]["source_complete"])
            self.assertEqual(receipt["quality"]["media"]["tracked"], 2)
            self.assertEqual(receipt["sha256"], hashlib.sha256(first.read_bytes()).hexdigest())

            verified = export_pptx_local.verify_local_pptx(first, expected_slides=2)
            self.assertEqual(verified["slides"], 2)
            self.assertEqual(verified["notes"], 1)
            self.assertEqual(verified["media"], 2)
            # Read back through the existing independent package validator too.
            slides = export_pptx.validate_pptx_archive(first, require_slides=True, expected_slides=2)
            self.assertEqual(len(slides), 2)

            with zipfile.ZipFile(first) as archive:
                names = set(archive.namelist())
                self.assertIn("ppt/notesMasters/notesMaster1.xml", names)
                self.assertIn("ppt/notesSlides/notesSlide1.xml", names)
                self.assertEqual(len([item for item in names if item.startswith("ppt/media/")]), 2)
                self.assertIsNone(archive.testzip())
                slide1 = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
                slide2 = ET.fromstring(archive.read("ppt/slides/slide2.xml"))
                notes = archive.read("ppt/notesSlides/notesSlide1.xml")
                shape_ids = [
                    int(item.get("id"))
                    for item in slide1.findall(f".//{{{P_NS}}}cNvPr")
                    if item.get("id") != "1"
                ]
                self.assertEqual(len(shape_ids), 5)
                self.assertEqual(len(shape_ids), len(set(shape_ids)))
                self.assertTrue(slide1.findall(f".//{{{A_NS}}}rPr[@lang='en-AU']"))
                self.assertIn(b'lang="en-AU"', notes)
                choice = slide1.find(f"./{{{MC_NS}}}AlternateContent/{{{MC_NS}}}Choice")
                self.assertIsNotNone(choice)
                fade = choice.find(f"./{{{P_NS}}}transition/{{{P_NS}}}fade")
                self.assertIsNotNone(fade)
                transition = choice.find(f"./{{{P_NS}}}transition")
                self.assertEqual(transition.get(f"{{{P14_NS}}}dur"), "500")
                wipe = slide2.find(f"./{{{P_NS}}}transition/{{{P_NS}}}wipe")
                self.assertIsNotNone(wipe)
                self.assertEqual(wipe.get("dir"), "l")
                self.assertEqual(slide2.find(f"./{{{P_NS}}}transition").get("advTm"), "12000")

            # Deterministic package bytes and stable cNvPr IDs across output paths.
            second = root / "second.pptx"
            second_receipt = export_pptx_local.export_local_pptx(
                root,
                second,
                quality_report=quality_report,
            )
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(receipt["sha256"], second_receipt["sha256"])

    def test_unsupported_elements_animations_and_custom_paths_fail_closed(self):
        cases = {
            "table": (
                "  - elementId: bad-table\n"
                "    elementType: table\n"
                "    bounds: [0, 0, 100, 100]\n"
                "    data: []\n",
                "unsupported.element_type",
            ),
            "custom": (
                "  - elementId: custom-shape\n"
                "    elementType: shape\n"
                "    bounds: [0, 0, 100, 100]\n"
                "    shapeName: custom\n"
                "    viewBox: [100, 100]\n"
                "    path: 'M0,0 L100,100 Z'\n",
                "unsupported.custom_shape",
            ),
        }
        for label, (unsupported, code) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                manifest = write_project(root, unsupported=unsupported)
                quality_report = write_quality_report(manifest)
                with self.assertRaises(deck_ir.DeckIRValidationError) as raised:
                    export_pptx_local.export_local_pptx(
                        manifest,
                        root / "bad.pptx",
                        quality_report=quality_report,
                    )
                self.assertIn(code, {item.code for item in raised.exception.diagnostics})
                self.assertFalse((root / "bad.pptx").exists())

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(root)
            page = root / "pages" / "02.page"
            page.write_text(page.read_text(encoding="utf-8") + "animations:\n  - {elementId: closing, effect: fade-in}\n", encoding="utf-8")
            with self.assertRaises(deck_ir.DeckIRValidationError) as raised:
                deck_ir.load_deck_ir(manifest)
            self.assertIn("unsupported.animations", {item.code for item in raised.exception.diagnostics})

    def test_morph_and_invalid_transition_page_fail_before_publication(self):
        for pages, expected, gate_rejects in (
            ({"pages/02.page": {"type": "morph"}}, "morph", False),
            ({"pages/missing.page": {"type": "fade"}}, "transition_page_unknown", True),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                manifest = write_project(root)
                metadata = json.loads((root / "deck.meta.json").read_text(encoding="utf-8"))
                metadata["transitions"]["pages"] = pages
                (root / "deck.meta.json").write_text(json.dumps(metadata), encoding="utf-8")
                report = pptd_quality.lint_project(manifest, fail_on="warning")
                if gate_rejects:
                    self.assertFalse(report["summary"]["passed"])
                    self.assertIn(
                        "metadata.transition_page_unknown",
                        {item["code"] for item in report["issues"]},
                    )
                    self.assertFalse((root / "bad.pptx").exists())
                    continue
                quality_report = root / "quality-report.json"
                pptd_quality.write_report(quality_report, report)
                with self.assertRaisesRegex(export_pptx_local.LocalExportError, expected):
                    export_pptx_local.export_local_pptx(
                        manifest,
                        root / "bad.pptx",
                        quality_report=quality_report,
                    )
                self.assertFalse((root / "bad.pptx").exists())

    def test_cli_prints_compact_receipt_and_does_not_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(root)
            output = root / "deck.pptx"
            quality_report = write_quality_report(manifest)
            command = [
                sys.executable,
                str(SCRIPTS / "export_pptx_local.py"),
                str(manifest),
                "-o",
                str(output),
                "--quality-report",
                str(quality_report),
            ]
            first = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            receipt = json.loads(first.stdout)
            self.assertEqual(receipt["output"], str(output.resolve()))
            before = output.read_bytes()
            second = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(before, output.read_bytes())
            forced = subprocess.run(command + ["--force"], capture_output=True, text=True, check=False)
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertEqual(before, output.read_bytes())

    def test_quality_gate_rejects_weak_stale_and_tampered_reports_before_staging(self):
        for mode in ("weak", "stale", "tampered"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                manifest = write_project(root)
                if mode == "weak":
                    report = write_quality_report(manifest, fail_on="error")
                else:
                    report = write_quality_report(manifest)
                if mode == "stale":
                    page = root / "pages" / "02.page"
                    page.write_text(page.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
                elif mode == "tampered":
                    value = json.loads(report.read_text(encoding="utf-8"))
                    value["receipt"]["passed"] = False
                    report.write_text(json.dumps(value), encoding="utf-8")
                output = root / "blocked.pptx"
                with self.assertRaisesRegex(export_pptx_local.LocalExportError, "quality gate"):
                    export_pptx_local.export_local_pptx(
                        manifest,
                        output,
                        quality_report=report,
                    )
                self.assertFalse(output.exists())
                self.assertFalse(list(root.glob(f".{output.name}.*.tmp")))

    def test_local_ir_fails_closed_on_unknown_fields(self):
        cases = (
            ("manifest", "futureFeature: true\n", None),
            ("theme", "themeFuture: true\n", None),
            ("page", None, "futurePageFeature: true\n"),
            (
                "element",
                None,
                "  - elementId: future\n"
                "    elementType: shape\n"
                "    bounds: [10, 10, 20, 20]\n"
                "    shapeName: rect\n"
                "    fill: {type: solid, color: '#000000'}\n"
                "    futureElementFeature: true\n",
            ),
            (
                "nested-text-and-type",
                None,
                "  - elementId: future-text\n"
                "    elementType: text\n"
                "    bounds: [10, 10, 200, 40]\n"
                "    content: {text: Future, bold: 'false', futureStyle: true}\n",
            ),
        )
        for label, manifest_extra, page_extra in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                manifest = write_project(root)
                if manifest_extra:
                    if label == "theme":
                        manifest.write_text(
                            manifest.read_text(encoding="utf-8").replace(
                                "  colors:\n", "  themeFuture: true\n  colors:\n"
                            ),
                            encoding="utf-8",
                        )
                    else:
                        manifest.write_text(
                            manifest.read_text(encoding="utf-8") + manifest_extra,
                            encoding="utf-8",
                        )
                elif page_extra:
                    page = root / "pages" / "01.page"
                    page.write_text(
                        page.read_text(encoding="utf-8") + page_extra,
                        encoding="utf-8",
                    )
                with self.assertRaises(deck_ir.DeckIRValidationError) as raised:
                    deck_ir.load_deck_ir(manifest)
                self.assertTrue(
                    any(
                        item.code.startswith("unsupported.")
                        for item in raised.exception.diagnostics
                    ),
                    raised.exception.diagnostics,
                )

    def test_export_rejects_disabled_media_provenance_and_input_aliases(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(root)
            disabled = pptd_quality.lint_project(
                manifest,
                fail_on="warning",
                check_media_sources=False,
            )
            report = root / "disabled-quality.json"
            pptd_quality.write_report(report, disabled)
            output = root / "blocked.pptx"
            with self.assertRaisesRegex(
                export_pptx_local.LocalExportError, "fully tracked"
            ):
                export_pptx_local.export_local_pptx(
                    manifest,
                    output,
                    quality_report=report,
                )
            self.assertFalse(output.exists())

            strict_report = write_quality_report(manifest)
            for label, protected in (
                ("quality", strict_report),
                ("provenance", root / "media" / "sources.json"),
                ("metadata", root / "deck.meta.json"),
            ):
                with self.subTest(alias=label):
                    alias = root / f"{label}-alias.pptx"
                    os.link(protected, alias)
                    with self.assertRaisesRegex(
                        export_pptx_local.LocalExportError, "bound project input"
                    ):
                        export_pptx_local.export_local_pptx(
                            manifest,
                            alias,
                            quality_report=strict_report,
                            force=True,
                        )

    def test_force_refuses_output_symlink_without_touching_its_target(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(root)
            quality_report = write_quality_report(manifest)
            target = root / "target.bin"
            original = b"do not overwrite"
            target.write_bytes(original)
            output = root / "linked.pptx"
            output.symlink_to(target.name)
            with self.assertRaisesRegex(export_pptx_local.LocalExportError, "symbolic link"):
                export_pptx_local.export_local_pptx(
                    manifest,
                    output,
                    quality_report=quality_report,
                    force=True,
                )
            self.assertTrue(output.is_symlink())
            self.assertEqual(target.read_bytes(), original)
            self.assertFalse(list(root.glob(f".{output.name}.*.tmp")))


if __name__ == "__main__":
    unittest.main()
