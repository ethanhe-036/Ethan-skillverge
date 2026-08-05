"""Regression tests for the Coda protocol and Ralph task extraction."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.dont_write_bytecode = True

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "extract_task_candidates.py"
MANIFEST_PATH = SKILL_ROOT / "references" / "protocol.yaml"


def load_extractor():
    """Load the extractor directly from the skill directory."""
    spec = importlib.util.spec_from_file_location("coda_task_extractor", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load extractor: {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProtocolTests(unittest.TestCase):
    """Check canonical vocabulary, authority, and documentation parity."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        cls.modes = (SKILL_ROOT / "references" / "modes.md").read_text(
            encoding="utf-8"
        )
        cls.output = (SKILL_ROOT / "references" / "output-contract.md").read_text(
            encoding="utf-8"
        )
        cls.examples = (
            SKILL_ROOT / "references" / "invocation-examples.md"
        ).read_text(encoding="utf-8")
        cls.compatibility = (
            SKILL_ROOT / "references" / "compatibility.md"
        ).read_text(encoding="utf-8")
        cls.openai_yaml = (SKILL_ROOT / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )

    def test_manifest_is_canonical_v2(self) -> None:
        self.assertTrue(self.manifest["canonical"])
        self.assertEqual(self.manifest["version"], 2)

    def test_public_entries_and_operators_are_documented(self) -> None:
        all_entry_docs = self.skill + self.examples + self.compatibility
        for entry in self.manifest["public_entries"]:
            self.assertIn(entry, all_entry_docs)

        for operator in self.manifest["operators"]:
            self.assertIn(operator, self.skill)
            self.assertIn(operator, self.modes)
            self.assertIn(operator, self.examples)

    def test_every_mode_alias_is_routed(self) -> None:
        routing_docs = self.skill + self.modes
        for mode in ("review_only", "review_fix"):
            for alias in self.manifest["modes"][mode]["aliases"]:
                self.assertIn(alias, routing_docs)

    def test_plain_review_is_read_only(self) -> None:
        self.assertIn("Default plain `review`", self.skill)
        self.assertIn("Use plain `review` for review-only convergence", self.modes)
        normative = self.skill + self.modes + self.examples
        self.assertNotIn("defaults to `review-fix loop`", normative)
        self.assertNotIn("routes to `review-fix loop`", normative)

    def test_authority_vocabulary_and_monotonic_rule(self) -> None:
        for field in self.manifest["authority"]["fields"]:
            self.assertIn(field, self.skill)
            self.assertIn(field, self.output)
        self.assertEqual(
            self.manifest["authority"]["composition"], "monotonic_non_increasing"
        )
        review_fix = self.manifest["modes"]["review_fix"]
        self.assertTrue(review_fix["capability_ceiling"]["edit"])
        self.assertTrue(review_fix["capability_ceiling"]["commit"])
        self.assertIn("edit", review_fix["requires_explicit_capability"])
        self.assertIn("commit", review_fix["does_not_grant"])
        review_only = self.manifest["modes"]["review_only"]
        self.assertFalse(review_only["capability_ceiling"]["edit"])
        self.assertFalse(review_only["capability_ceiling"]["commit"])
        self.assertEqual(
            self.manifest["modes"]["fixed_review"]["with_explicit_edit"],
            "review_fix_with_round_budget",
        )
        self.assertIn("never add", self.modes)

    def test_terminal_state_schema_matches_normative_docs(self) -> None:
        for state in self.manifest["readiness"]:
            self.assertIn(state, self.skill)
            self.assertIn(state, self.modes)
            self.assertIn(state, self.output)

        for reason in self.manifest["stop_reasons"]:
            self.assertIn(reason, self.skill)
            self.assertIn(reason, self.modes)
            self.assertIn(reason, self.output)

        normative = self.skill + self.modes + self.output
        self.assertNotIn("MAX_ROUNDS_REACHED", normative)
        self.assertNotIn("MAX_BUDGET_REACHED", normative)
        self.assertEqual(
            set(self.manifest["readiness_transitions"]),
            set(self.manifest["readiness"]),
        )

    def test_evidence_is_final_state_bound(self) -> None:
        self.assertTrue(self.manifest["evidence"]["state_bound"])
        self.assertTrue(
            self.manifest["evidence"]["state_id"]["method_must_be_recorded"]
        )
        self.assertIn(
            "relevant_untracked_target_content",
            self.manifest["evidence"]["state_id"]["git_inputs"],
        )
        self.assertIn("state_id", self.skill)
        self.assertIn("state_id", self.output)
        self.assertIn("state_id method", self.output)
        self.assertIn("STALE", self.output)
        self.assertIn("final `state_id`", self.output)

    def test_findings_registry_is_lossless_and_residual_has_acceptor(self) -> None:
        registry = self.manifest["finding_registry"]
        self.assertTrue(registry["lossless"])
        self.assertEqual(registry["focus_limit"], 5)
        for field in ("accepted_by", "acceptance_rationale", "policy_ref"):
            self.assertIn(field, self.skill)
            self.assertIn(field, self.output)

    def test_default_prompt_uses_stable_entry(self) -> None:
        self.assertIn("$coda", self.openai_yaml)
        self.assertIn("review-fix", self.openai_yaml)
        self.assertIn("ralph", self.openai_yaml)
        match = re.search(
            r'^\s*short_description:\s*"(?P<value>[^"]+)"\s*$',
            self.openai_yaml,
            re.MULTILINE,
        )
        self.assertIsNotNone(match)
        self.assertGreaterEqual(len(match.group("value")), 25)
        self.assertLessEqual(len(match.group("value")), 64)

    def test_markdown_links_resolve(self) -> None:
        for markdown_path in SKILL_ROOT.rglob("*.md"):
            text = markdown_path.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                if target.startswith(("http://", "https://", "#")):
                    continue
                relative_target = target.split("#", 1)[0]
                resolved = (markdown_path.parent / relative_target).resolve()
                self.assertTrue(
                    resolved.exists(),
                    f"Broken link in {markdown_path}: {target}",
                )

    def test_skill_body_stays_within_progressive_disclosure_limit(self) -> None:
        self.assertLessEqual(len(self.skill.splitlines()), 500)

    def test_golden_prompts_are_present(self) -> None:
        prompts = [
            "Use $coda to review the current diff.",
            "$coda REVIEW方案 docs/plan.md",
            "review-fix，但不要修改文件",
            "$coda review 3 当前改动，只review",
            "修复这个问题，然后 review 3",
            "Use $coda to review this authentication change.",
            'cook "Approach A" vs "Approach B" merge',
            "ralph @tasks.md，执行下一个 TODO；允许改代码，不要commit",
        ]
        for prompt in prompts:
            self.assertIn(prompt, self.examples)

    def test_golden_prompt_blocks_preserve_expected_semantics(self) -> None:
        cases = {
            "Use $coda to review the current diff.": [
                "Mode: `review_only`",
                "edit=false",
            ],
            "$coda REVIEW方案 docs/plan.md": [
                "Mode: `review_only`",
                "edit=false",
            ],
            "Use $coda to review-fix this patch; edits are allowed; do not commit or push.": [
                "Mode: `review_fix`",
                "edit=true",
                "commit=false",
                "push=false",
            ],
            "review-fix，但不要修改文件": [
                "Mode: `review_only` or `ASK_USER`",
                "dominates",
            ],
            "$coda review 3 当前改动，只review": [
                "Mode: `fixed_review`",
                "NOT_READY / BUDGET",
            ],
            "Use $coda to review this authentication change.": [
                "Risk: `high`",
                "genuine independence source",
            ],
            'cook "Approach A" vs "Approach B" merge': [
                "conceptual hybrid",
                "Do not run `git merge`",
            ],
        }
        for prompt, expectations in cases.items():
            start = self.examples.index(prompt)
            prompt_block = self.examples[start : start + 650]
            for expectation in expectations:
                self.assertIn(
                    expectation,
                    prompt_block,
                    f"Missing {expectation!r} near golden prompt {prompt!r}",
                )


class RalphExtractorTests(unittest.TestCase):
    """Exercise fail-closed and actionable-task behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.extractor = load_extractor()

    def test_numbered_architecture_prose_is_rejected(self) -> None:
        text = "1. Background\n2. Constraints\n3. Alternatives\n"
        self.assertEqual(self.extractor.extract_candidates(text), [])

    def test_done_only_source_has_no_actionable_task(self) -> None:
        text = "- [x] Already completed\nDONE: Shipped\n"
        self.assertEqual(self.extractor.extract_candidates(text), [])

    def test_backtick_and_tilde_fences_are_ignored(self) -> None:
        text = (
            "```markdown\nTODO: Backtick example\n1. Example\n```\n"
            "~~~\nTODO: Tilde example\n2. Example\n~~~\n"
        )
        self.assertEqual(self.extractor.extract_candidates(text), [])

    def test_indented_code_markers_are_rejected(self) -> None:
        text = "    TODO: Example only\n## Tasks\n    1. Code sample\n"
        self.assertEqual(self.extractor.extract_candidates(text), [])

    def test_numbered_items_require_an_explicit_task_section(self) -> None:
        text = (
            "# Architecture\n1. Background\n"
            "## Tasks\n1. Implement parser\n"
            "### Backend\n2. Add fixtures\n"
            "## Alternatives\n3. Do not execute\n"
        )
        candidates = self.extractor.extract_candidates(text)
        self.assertEqual(
            [candidate["text"] for candidate in candidates],
            ["Implement parser", "Add fixtures"],
        )

    def test_next_is_selected_before_todo_and_source_order(self) -> None:
        text = (
            "- [ ] First in file\n"
            "TODO: Explicit todo\n"
            "NEXT: Urgent\n"
            "DONE: Old\n"
            "- [x] Checked\n"
            "- [ ] Unchecked\n"
            "+ [ ] Plus checkbox\n"
        )
        candidates = self.extractor.extract_candidates(text)
        self.assertEqual(len(candidates), 5)
        selected = self.extractor.choose_next_candidate(candidates)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["status"], "next")
        self.assertEqual(selected["text"], "Urgent")

    def test_todo_marker_is_selected_before_other_actionable_items(self) -> None:
        text = "- [ ] First checkbox\nTODO: Explicit todo\n## Tasks\n1. Numbered\n"
        candidates = self.extractor.extract_candidates(text)
        selected = self.extractor.choose_next_candidate(candidates)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["kind"], "status")
        self.assertEqual(selected["text"], "Explicit todo")

    def test_cli_returns_no_actionable_task_and_exit_two(self) -> None:
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".md",
                encoding="utf-8",
                delete=False,
            ) as handle:
                handle.write("# Architecture\n1. Background\nDONE: Complete\n")
                temp_path = Path(handle.name)

            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), str(temp_path)],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "NO_ACTIONABLE_TASK")
            self.assertEqual(payload["candidate_count"], 0)
            self.assertIsNone(payload["next_candidate"])
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
