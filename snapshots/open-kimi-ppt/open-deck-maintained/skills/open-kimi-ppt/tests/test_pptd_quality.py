#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pptd_quality.py"
SPEC = importlib.util.spec_from_file_location("pptd_quality", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def write_project(
    root: Path,
    *,
    page: str,
    manifest: str = "version: v2\ntitle: QA fixture\nsize: [960, 540]\npages:\n  - pages/01.page\n",
) -> Path:
    manifest_path = root / "fixture.pptd"
    manifest_path.write_text(manifest, encoding="utf-8")
    (root / "pages").mkdir()
    (root / "pages" / "01.page").write_text(page, encoding="utf-8")
    return manifest_path


class PptdQualityTests(unittest.TestCase):
    def test_windows_path_and_descriptor_ids_do_not_trigger_false_change(self):
        with tempfile.TemporaryDirectory() as name:
            source = Path(name) / "source.txt"
            source.write_bytes(b"stable input")
            actual = source.lstat()
            path_values = list(actual)
            path_values[1] = actual.st_ino + 97
            path_snapshot = type(actual)(path_values)

            with (
                patch.object(MODULE.os, "name", "nt"),
                patch.object(type(source), "stat", return_value=path_snapshot),
                patch.object(type(source), "lstat", return_value=path_snapshot),
            ):
                self.assertEqual(MODULE._read_stable_bytes(source), b"stable input")

    def test_valid_project_has_exact_manifest_page_and_metadata_binding(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(
                root,
                page=(
                    "pageType: content\n"
                    "elements:\n"
                    "  - elementId: title\n"
                    "    elementType: text\n"
                    "    bounds: [40, 40, 600, 80]\n"
                    "    content: {text: Hello}\n"
                    "animations:\n"
                    "  - {elementId: title, effect: fade-in}\n"
                ),
            )
            metadata_raw = (
                '{"schemaVersion":1,"primaryLanguage":"zh-Hans",'
                '"mediaPolicy":{"licensePolicy":"known-only",'
                '"requireAttribution":true},'
                '"transitions":{"default":{"type":"fade","durationMs":500}}}'
            ).encode("utf-8")
            (root / "deck.meta.json").write_bytes(metadata_raw)

            report = MODULE.lint_project(manifest, fail_on="warning")

            self.assertTrue(report["summary"]["passed"])
            self.assertEqual(report["summary"]["counts"]["total"], 0)
            binding = report["source_binding"]
            self.assertTrue(binding["complete"])
            self.assertEqual(
                binding["manifest"]["sha256"],
                hashlib.sha256(manifest.read_bytes()).hexdigest(),
            )
            page = root / "pages" / "01.page"
            self.assertEqual(
                binding["pages"][0]["sha256"],
                hashlib.sha256(page.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                binding["deck_metadata"]["sha256"],
                hashlib.sha256(metadata_raw).hexdigest(),
            )
            self.assertEqual(
                report["receipt"]["deck_metadata_sha256"],
                binding["deck_metadata"]["sha256"],
            )
            original_digest = binding["deck_sha256"]
            metadata = json.loads(metadata_raw)
            metadata["primaryLanguage"] = "en-AU"
            (root / "deck.meta.json").write_text(json.dumps(metadata), encoding="utf-8")
            changed = MODULE.lint_project(root)
            self.assertNotEqual(changed["source_binding"]["deck_sha256"], original_digest)

    def test_structural_checks_find_duplicate_animation_bounds_and_missing_media(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(
                root,
                page=(
                    "elements:\n"
                    "  - elementId: duplicate\n"
                    "    elementType: image\n"
                    "    bounds: [-2, 10, 0, 100]\n"
                    "    src: media/missing.png\n"
                    "  - elementId: duplicate\n"
                    "    elementType: shape\n"
                    "    bounds: [900, 500, 80, 80]\n"
                    "animations:\n"
                    "  - {elementId: missing-target, effect: fade-in}\n"
                    "  - {elementId: duplicate, effect: fade-in}\n"
                ),
            )

            report = MODULE.lint_project(manifest, fail_on="error")
            codes = {issue["code"] for issue in report["issues"]}

            self.assertFalse(report["summary"]["passed"])
            self.assertTrue(
                {
                    "element.duplicate_id",
                    "animation.unknown_target",
                    "animation.ambiguous_target",
                    "bounds.zero_size",
                    "bounds.out_of_page",
                    "media.missing",
                }.issubset(codes)
            )
            self.assertEqual(report["media"]["referenced"], 1)
            self.assertEqual(report["media"]["missing"], 1)

    def test_media_sources_validate_license_hash_tracking_and_derivation(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(
                root,
                page=(
                    "elements:\n"
                    "  - elementId: hero\n"
                    "    elementType: image\n"
                    "    bounds: [0, 0, 960, 540]\n"
                    "    src: media/derived.png\n"
                ),
            )
            media = root / "media"
            media.mkdir()
            original = b"original image bytes"
            derived = b"derived image bytes"
            (media / "original.png").write_bytes(original)
            (media / "derived.png").write_bytes(derived)
            sources = {
                "schema_version": "1.0",
                "assets": {
                    "media/original.png": {
                        "license": "CC-BY-4.0",
                        "sha256": hashlib.sha256(original).hexdigest(),
                    },
                    "media/derived.png": {
                        "license": "CC-BY-4.0",
                        "sha256": hashlib.sha256(derived).hexdigest(),
                        "derived_from": "media/original.png",
                    },
                },
            }
            (media / "sources.json").write_text(json.dumps(sources), encoding="utf-8")

            valid = MODULE.lint_project(manifest, fail_on="warning")
            self.assertTrue(valid["summary"]["passed"])
            self.assertTrue(valid["media"]["sources_present"])
            self.assertEqual(valid["media"]["tracked"], 1)
            self.assertRegex(
                valid["source_binding"]["media_sources"]["sha256"],
                r"^[0-9a-f]{64}$",
            )
            original_digest = valid["source_binding"]["deck_sha256"]
            (media / "derived.png").write_bytes(b"changed derived image bytes")
            changed_media = MODULE.lint_project(manifest, fail_on="warning")
            self.assertNotEqual(
                changed_media["source_binding"]["deck_sha256"], original_digest
            )
            (media / "derived.png").write_bytes(derived)

            sources["assets"]["media/derived.png"]["license"] = "unknown"
            sources["assets"]["media/derived.png"]["sha256"] = "0" * 64
            sources["assets"]["media/derived.png"]["derived_from"] = "media/missing.png"
            (media / "sources.json").write_text(json.dumps(sources), encoding="utf-8")
            invalid = MODULE.lint_project(manifest, fail_on="warning")
            codes = {issue["code"] for issue in invalid["issues"]}
            self.assertFalse(invalid["summary"]["passed"])
            self.assertTrue(
                {
                    "media.license_placeholder",
                    "media.sha256_mismatch",
                    "media.derived_from_missing",
                    "media.derived_from_untracked",
                }.issubset(codes)
            )

    def test_invalid_deck_metadata_is_bound_and_reported(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(
                root,
                page=(
                    "elements:\n"
                    "  - elementId: title\n"
                    "    elementType: text\n"
                    "    bounds: [0, 0, 200, 50]\n"
                    "    content: {text: Hello}\n"
                ),
            )
            (root / "deck.meta.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "primaryLanguage": "not_a_tag",
                        "transitions": {"default": {"type": "random"}},
                        "mediaPolicy": {
                            "licensePolicy": "anything-goes",
                            "requireAttribution": "yes",
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = MODULE.lint_project(manifest)
            codes = {issue["code"] for issue in report["issues"]}
            self.assertTrue(
                {
                    "metadata.schema_version",
                    "metadata.primary_language_invalid",
                    "metadata.transition_unsafe",
                    "metadata.license_policy_invalid",
                    "metadata.media_policy_flag_invalid",
                }.issubset(codes)
            )
            self.assertEqual(report["source_binding"]["deck_metadata"]["status"], "bound")
            self.assertRegex(
                report["source_binding"]["deck_metadata"]["sha256"],
                r"^[0-9a-f]{64}$",
            )

    def test_deck_metadata_matches_nested_schema_constraints(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(
                root,
                page=(
                    "elements:\n"
                    "  - elementId: title\n"
                    "    elementType: text\n"
                    "    bounds: [0, 0, 200, 50]\n"
                    "    content: {text: Hello}\n"
                ),
            )
            metadata = {
                "schemaVersion": True,
                "audience": "",
                "objective": "x" * 2001,
                "coreMessage": 7,
                "primaryLanguage": "e",
                "consumptionMode": "cinema",
                "pageRhythm": ["anchor", "unknown"] + ["dense"] * 499,
                "targetApps": ["powerpoint", "powerpoint", "unknown"],
                "mediaPolicy": {
                    "requireAttribution": "yes",
                    "unknown": True,
                },
                "transitions": {
                    "unexpected": {},
                    "default": {"durationMs": None, "unknown": True},
                    "pages": {
                        "pages/01.page": {"type": "fade"},
                        "pages/undeclared.page": {
                            "type": "push",
                            "extra": False,
                        },
                    },
                },
                "unknown": "field",
            }
            (root / "deck.meta.json").write_text(json.dumps(metadata), encoding="utf-8")

            report = MODULE.lint_project(manifest, fail_on="warning")
            codes = {issue["code"] for issue in report["issues"]}
            self.assertFalse(report["summary"]["passed"])
            self.assertTrue(
                {
                    "metadata.unknown_field",
                    "metadata.schema_version",
                    "metadata.string_invalid",
                    "metadata.primary_language_invalid",
                    "metadata.consumption_mode_invalid",
                    "metadata.page_rhythm_too_long",
                    "metadata.page_rhythm_item_invalid",
                    "metadata.target_apps_duplicate",
                    "metadata.target_app_invalid",
                    "metadata.media_policy_unknown_field",
                    "metadata.media_policy_flag_invalid",
                    "metadata.transitions_unknown_field",
                    "metadata.transition_type_missing",
                    "metadata.transition_timing_invalid",
                    "metadata.transition_unknown_field",
                    "metadata.transition_page_unknown",
                }.issubset(codes)
            )
            unknown_pages = [
                issue
                for issue in report["issues"]
                if issue["code"] == "metadata.transition_page_unknown"
            ]
            self.assertEqual(len(unknown_pages), 1)
            self.assertIn("undeclared", unknown_pages[0]["message"])

    def test_local_media_without_default_sources_manifest_is_a_warning(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(
                root,
                page=(
                    "elements:\n"
                    "  - elementId: hero\n"
                    "    elementType: image\n"
                    "    bounds: [0, 0, 200, 100]\n"
                    "    src: media/hero.png\n"
                ),
            )
            (root / "media").mkdir()
            (root / "media" / "hero.png").write_bytes(b"png fixture")

            strict = MODULE.lint_project(manifest, fail_on="warning")
            self.assertFalse(strict["summary"]["passed"])
            self.assertIn(
                "media.sources_absent",
                {issue["code"] for issue in strict["issues"]},
            )

            disabled = MODULE.lint_project(
                manifest,
                fail_on="warning",
                check_media_sources=False,
            )
            self.assertTrue(disabled["summary"]["passed"])
            self.assertEqual(
                disabled["source_binding"]["media_sources"]["status"],
                "disabled",
            )

    def test_fail_on_threshold_and_compact_cli_receipt(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(
                root,
                page=(
                    "elements:\n"
                    "  - elementId: wide\n"
                    "    elementType: shape\n"
                    "    bounds: [900, 500, 80, 80]\n"
                ),
            )
            warning = MODULE.lint_project(manifest, fail_on="warning")
            error = MODULE.lint_project(manifest, fail_on="error")
            none = MODULE.lint_project(manifest, fail_on="none")
            self.assertFalse(warning["summary"]["passed"])
            self.assertTrue(error["summary"]["passed"])
            self.assertTrue(none["summary"]["passed"])

            command = [
                sys.executable,
                str(SCRIPT),
                str(manifest),
                "--receipt",
                "--fail-on",
                "warning",
            ]
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(len(result.stdout.splitlines()), 1)
            receipt = json.loads(result.stdout)
            self.assertFalse(receipt["passed"])
            self.assertRegex(receipt["source_sha256"], r"^[0-9a-f]{64}$")

    def test_full_report_is_written_atomically_and_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(
                root,
                page=(
                    "elements:\n"
                    "  - elementId: title\n"
                    "    elementType: text\n"
                    "    bounds: [10, 10, 300, 60]\n"
                    "    content: {text: Hello}\n"
                ),
            )
            report = MODULE.lint_project(manifest)
            destination = root / "quality-report.json"
            MODULE.write_report(destination, report)
            stored = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(
                stored["source_binding"]["deck_sha256"],
                report["source_binding"]["deck_sha256"],
            )
            with self.assertRaises(MODULE.QualityInputError):
                MODULE.write_report(destination, report)
            MODULE.write_report(destination, report, force=True)

            target = root / "symlink-target.json"
            target.write_text("do not replace", encoding="utf-8")
            destination.unlink()
            try:
                destination.symlink_to(target)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are unavailable")
            with self.assertRaisesRegex(MODULE.QualityInputError, "symbolic link"):
                MODULE.write_report(destination, report, force=True)
            self.assertEqual(target.read_text(encoding="utf-8"), "do not replace")

    def test_quality_gate_rejects_stale_weak_and_tampered_reports(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(
                root,
                page=(
                    "elements:\n"
                    "  - elementId: title\n"
                    "    elementType: text\n"
                    "    bounds: [10, 10, 300, 60]\n"
                    "    content: {text: Hello}\n"
                ),
            )
            report_path = root / "quality-report.json"
            report = MODULE.lint_project(manifest, fail_on="warning")
            MODULE.write_report(report_path, report)
            receipt = MODULE.verify_quality_report(manifest, report_path)
            self.assertEqual(receipt["source_sha256"], report["source_binding"]["deck_sha256"])

            page = root / "pages" / "01.page"
            original_page = page.read_text(encoding="utf-8")
            page.write_text(original_page.replace("Hello", "Changed"), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.QualityInputError, "source SHA-256"):
                MODULE.verify_quality_report(manifest, report_path)
            page.write_text(original_page, encoding="utf-8")

            weak = MODULE.lint_project(manifest, fail_on="error")
            MODULE.write_report(report_path, weak, force=True)
            with self.assertRaisesRegex(MODULE.QualityInputError, "stricter threshold"):
                MODULE.verify_quality_report(manifest, report_path)

            tampered = MODULE.lint_project(manifest, fail_on="warning")
            tampered["receipt"]["passed"] = False
            MODULE.write_report(report_path, tampered, force=True)
            with self.assertRaisesRegex(MODULE.QualityInputError, "receipt"):
                MODULE.verify_quality_report(manifest, report_path)

    def test_quality_gate_replays_custom_and_disabled_media_source_modes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = write_project(
                root,
                page=(
                    "elements:\n"
                    "  - elementId: hero\n"
                    "    elementType: image\n"
                    "    bounds: [0, 0, 200, 100]\n"
                    "    src: media/hero.png\n"
                ),
            )
            media = root / "media"
            media.mkdir()
            payload = b"image fixture"
            (media / "hero.png").write_bytes(payload)
            provenance = {
                "schema_version": "1.0",
                "assets": {
                    "media/hero.png": {
                        "license": "CC0-1.0",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                },
            }
            custom = root / "provenance" / "assets.json"
            custom.parent.mkdir()
            custom.write_text(json.dumps(provenance), encoding="utf-8")

            report_path = root / "custom-report.json"
            custom_report = MODULE.lint_project(
                manifest,
                fail_on="warning",
                media_sources="provenance/assets.json",
            )
            self.assertTrue(custom_report["summary"]["passed"])
            self.assertEqual(
                custom_report["source_binding"]["media_sources"]["path"],
                "provenance/assets.json",
            )
            MODULE.write_report(report_path, custom_report)
            receipt = MODULE.verify_quality_report(manifest, report_path)
            self.assertTrue(receipt["passed"])
            bound_inputs = MODULE.quality_report_bound_inputs(report_path, root)
            self.assertEqual(
                set(bound_inputs),
                {
                    manifest.resolve(),
                    (root / "pages" / "01.page").resolve(),
                    custom.resolve(),
                    (media / "hero.png").resolve(),
                },
            )

            disabled_path = root / "disabled-report.json"
            disabled_report = MODULE.lint_project(
                manifest,
                fail_on="warning",
                check_media_sources=False,
            )
            self.assertTrue(disabled_report["summary"]["passed"])
            MODULE.write_report(disabled_path, disabled_report)
            disabled_receipt = MODULE.verify_quality_report(manifest, disabled_path)
            self.assertTrue(disabled_receipt["passed"])

    def test_missing_page_keeps_a_partial_source_binding(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = root / "fixture.pptd"
            manifest.write_text(
                "version: v2\nsize: [960, 540]\npages:\n  - pages/missing.page\n",
                encoding="utf-8",
            )
            report = MODULE.lint_project(manifest)
            self.assertFalse(report["source_binding"]["complete"])
            self.assertEqual(report["source_binding"]["pages"][0]["status"], "missing")
            self.assertEqual(report["source_binding"]["pages"][0]["sha256"], None)
            self.assertIn("page.missing", {issue["code"] for issue in report["issues"]})


if __name__ == "__main__":
    unittest.main()
