#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prompt_audit.py"
SPEC = importlib.util.spec_from_file_location("prompt_audit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class PromptAuditTests(unittest.TestCase):
    def test_bundled_manifest_passes_context_budgets(self):
        manifest = SCRIPT.with_name("prompt_audit_manifest.json")
        report = MODULE.audit_prompt_manifest(manifest)
        self.assertEqual(report["status"], "pass", report["findings"])
        self.assertEqual(report["receipt"]["documents"], 5)
        self.assertRegex(report["sourceSha256"], r"^[0-9a-f]{64}$")

    def test_missing_link_and_exceeded_budget_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text("[missing](reference/nope.md)\n", encoding="utf-8")
            manifest = {
                "minimumDuplicateChars": 80,
                "allowDuplicateParagraphs": [],
                "documents": [{"id": "skill", "path": "SKILL.md", "maxTokens": 1}],
                "loadSets": [{"id": "core", "documents": ["skill"], "maxTokens": 1}],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            report = MODULE.audit_prompt_manifest(path, root=root)
        codes = {item["code"] for item in report["findings"]}
        self.assertEqual(report["status"], "fail")
        self.assertIn("document.link.broken", codes)
        self.assertIn("document.budget.exceeded", codes)

    def test_duplicate_long_guidance_is_reported_as_warning(self):
        paragraph = "This is deliberately repeated guidance with enough words and detail to exceed the configured minimum length. " * 2
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text(paragraph, encoding="utf-8")
            (root / "b.md").write_text(paragraph, encoding="utf-8")
            manifest = {
                "minimumDuplicateChars": 80,
                "allowDuplicateParagraphs": [],
                "documents": [
                    {"id": "a", "path": "a.md", "maxTokens": 1000},
                    {"id": "b", "path": "b.md", "maxTokens": 1000},
                ],
                "loadSets": [{"id": "all", "documents": ["a", "b"], "maxTokens": 2000}],
            }
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            report = MODULE.audit_prompt_manifest(path, root=root)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["receipt"]["duplicateGroups"], 1)
        self.assertEqual(report["receipt"]["warnings"], 1)

    def test_report_publish_is_no_clobber_and_refuses_symlink(self):
        report = {"status": "pass"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "prompt-audit.json"
            MODULE._write_report(destination, report, force=False)
            with self.assertRaisesRegex(MODULE.PromptAuditError, "already exists"):
                MODULE._write_report(destination, report, force=False)

            target = root / "target.json"
            target.write_text("keep", encoding="utf-8")
            destination.unlink()
            try:
                destination.symlink_to(target)
            except (NotImplementedError, OSError):
                self.skipTest("symbolic links are unavailable")
            with self.assertRaisesRegex(MODULE.PromptAuditError, "symbolic link"):
                MODULE._write_report(destination, report, force=True)
            self.assertEqual(target.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
