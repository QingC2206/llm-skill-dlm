#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import context_manifest_tool as TOOL  # noqa: E402


class ContextManifestContractsTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture_parent = TOOL.PROJECT_ROOT / "out" / "dlm-plan-tests"
        fixture_parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="manifest-contracts-", dir=fixture_parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.epic_rel = "Tasks/E001_fixture"
        self.epic = self.root / "doc_llm" / self.epic_rel
        self.write("doc_llm/00_Global/L0_global_redlines.md", "# Global rules\n")
        self.write(f"doc_llm/{self.epic_rel}/README.md", "# Epic\n")
        self.write("doc/design.md", '# Design\n\n## Input contract\n\n### Active workload\n\n###### Deep detail ###\n')
        for name, value in (("PROJECT_ROOT", self.root), ("DOC_LLM_DIR", "doc_llm"), ("CATALOG", {})):
            patcher = mock.patch.object(TOOL, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def arguments(self, *extra: str):
        return TOOL.build_parser().parse_args([
            "generate", "--epic-dir", self.epic_rel, "--task-id", "S002", *extra,
        ])

    def lint_entries(self, entries: list[dict[str, str]]) -> tuple[int, dict]:
        root = ET.Element("context_manifest", task="S002", epic="E001")
        for entry in entries:
            ET.SubElement(root, "file", {"path": "doc/design.md", "intent": "contract", "required": "true", **entry})
        target = self.write("manifest.xml", ET.tostring(root, encoding="unicode"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = TOOL.lint_manifest(target, as_json=True)
        return code, json.loads(output.getvalue())

    def test_explicit_contracts_preserve_multiple_sections_and_promote_only_selected_sources(self) -> None:
        plan_rel = f"{self.epic_rel}/plan/execution_plan.md"
        dependency_rel = f"{self.epic_rel}/subtasks/S001_input.md"
        self.write(f"doc_llm/{plan_rel}", "# Plan\n## Settled behavior\n")
        self.write(f"doc_llm/{dependency_rel}", "# S001\n## 执行日志\n")
        args = self.arguments(
            "--dep-task", "S001",
            "--contract", "doc/design.md", "Input contract", "Current input semantics",
            "--contract", "doc/design.md", "Active workload", "Prepare and interact concurrently",
            "--contract", "doc/design.md", "Input contract", "Current input semantics",
        )
        manifest = TOOL.generate_manifest(args)
        entries = [item.attrib for item in ET.fromstring(manifest).findall("file")]
        contracts = [entry for entry in entries if entry["path"] == "doc/design.md"]
        self.assertEqual({entry["section"] for entry in contracts}, {"Input contract", "Active workload"})
        self.assertEqual(len(contracts), 2)
        self.assertTrue(all(entry["required"] == "true" for entry in contracts))
        self.assertEqual(next(entry for entry in entries if entry["path"] == plan_rel)["required"], "false")
        dependency = next(entry for entry in entries if entry["path"] == dependency_rel)
        self.assertEqual((dependency["required"], dependency["section"]), ("false", "执行日志"))
        target = self.write("manifest.xml", manifest)
        _, parsed = TOOL.parse_manifest(target)
        self.assertEqual(len([entry for entry in parsed if entry.path == "doc/design.md"]), 2)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(TOOL.lint_manifest(target), 0)

    def test_full_file_contract_uses_explicit_intent_and_escapes_xml(self) -> None:
        plan_rel = f"{self.epic_rel}/plan/execution_plan.md"
        self.write(f"doc_llm/{plan_rel}", "# Plan\n")
        intent = 'Required "behavior" & boundaries'
        args = self.arguments("--contract", plan_rel, "-", intent)
        entries = [item.attrib for item in ET.fromstring(TOOL.generate_manifest(args)).findall("file")]
        selected = [entry for entry in entries if entry["path"] == plan_rel]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["required"], "true")
        self.assertEqual(selected[0]["intent"], intent)
        self.assertNotIn("section", selected[0])

    def test_old_namespace_and_extra_required_remain_supported(self) -> None:
        args = self.arguments("--extra-required", "doc/design.md=Existing whole-file input")
        del args.contract
        entries = [item.attrib for item in ET.fromstring(TOOL.generate_manifest(args)).findall("file")]
        selected = next(entry for entry in entries if entry["path"] == "doc/design.md")
        self.assertEqual(selected["required"], "true")
        self.assertEqual(selected["intent"], "Existing whole-file input")
        self.assertNotIn("section", selected)

    def test_markdown_multilevel_headings_are_exact_and_fenced_examples_are_ignored(self) -> None:
        content = "\n".join([
            "# Root", "## Input contract", "### Nested ###", "###### Deep detail",
            "```markdown", "## Fenced fake", "```", "~~~", "## Tilde fake", "~~~",
            "````markdown", "```", "## Longer fence fake", "````",
            "    ## Indented code", "####### Not an ATX heading", "##No space", "## Input contract extended",
        ])
        for section, line in (("Root", 1), ("Input contract", 2), ("Nested", 3), ("Deep detail", 4)):
            with self.subTest(section=section):
                self.assertEqual(TOOL.markdown_section_matches(content, section), [line])
        for section in ("Fenced fake", "Tilde fake", "Longer fence fake", "Indented code", "Not an ATX heading", "No space", "Input", "#input-contract"):
            with self.subTest(section=section):
                self.assertEqual(TOOL.markdown_section_matches(content, section), [])

    def test_required_missing_or_ambiguous_section_is_error_optional_is_warning(self) -> None:
        self.write("doc/design.md", "# Design\n## Duplicate\n### Duplicate\n")
        for section, expected_issue in (("Missing", "not found"), ("Duplicate", "ambiguous"), (" ", "must name")):
            for required in (True, False):
                with self.subTest(section=section, required=required):
                    code, report = self.lint_entries([{"section": section, "required": str(required).lower()}])
                    self.assertEqual(code, 1 if required else 0)
                    messages = report["errors" if required else "warnings"]
                    self.assertTrue(any(expected_issue in message for message in messages), report)
                    self.assertEqual(report["warnings" if required else "errors"], [])

    def test_fenced_only_section_does_not_satisfy_required_reference(self) -> None:
        self.write("doc/design.md", "# Design\n```markdown\n## Phantom\n```\n")
        code, report = self.lint_entries([{"section": "Phantom"}])
        self.assertEqual(code, 1)
        self.assertIn("section not found", report["errors"][0])

    def test_html_comment_headings_cannot_satisfy_sections_or_open_code_fences(self) -> None:
        content = "\n".join([
            "# Design", "<!-- template instructions", "## Fake", "```markdown", "-->",
            "## Real <!-- explanatory comment -->", "```markdown", "<!-- unclosed example comment", "```",
            "### After example", "<!-- ## Inline fake -->", "## Final",
            "```text <!-- example annotation", "## Still code", "```", "## After annotated fence",
        ])
        self.write("doc/design.md", content)
        code, report = self.lint_entries([{"section": "Fake"}])
        self.assertEqual(code, 1)
        self.assertIn("section not found", report["errors"][0])
        self.assertEqual(TOOL.markdown_section_matches(content, "Real"), [6])
        self.assertEqual(TOOL.markdown_section_matches(content, "After example"), [10])
        self.assertEqual(TOOL.markdown_section_matches(content, "Final"), [12])
        self.assertEqual(TOOL.markdown_section_matches(content, "After annotated fence"), [16])
        self.assertEqual(TOOL.markdown_section_matches(content, "Still code"), [])
        self.assertEqual(TOOL.markdown_section_matches(content, "Inline fake"), [])

    def test_opening_yaml_frontmatter_comments_do_not_satisfy_sections(self) -> None:
        content = "\n".join([
            "---", "title: Fixture", "## YAML comment", "description: |", "  <!-- metadata", "---",
            "# Design", "### Real section",
        ])
        self.write("doc/design.md", content)
        code, report = self.lint_entries([{"section": "YAML comment"}])
        self.assertEqual(code, 1)
        self.assertIn("section not found", report["errors"][0])
        self.assertEqual(TOOL.markdown_section_matches(content, "Real section"), [8])

    def test_protocol_extraction_ignores_fenced_heading_and_preserves_nested_sections(self) -> None:
        content = "\n".join([
            "# Task", "## 方案承接与实施设计", "```markdown", "## 上下文构建协议",
            '<context_manifest task="fake" />', "```", "## 上下文构建协议", "### Required contract",
            '<context_manifest task="S002" epic="E001">',
            '<file path="doc/design.md" intent="input" required="true" section="Input contract" />',
            "</context_manifest>", "#### Reading notes", "Read the active input contract.",
            "## 完成判断", "Do not include this section.",
        ])
        self.assertEqual(TOOL.markdown_heading_spans(content), [
            (1, 1, "Task"), (2, 2, "方案承接与实施设计"), (7, 2, "上下文构建协议"),
            (8, 3, "Required contract"), (12, 4, "Reading notes"), (14, 2, "完成判断"),
        ])
        section = TOOL.extract_markdown_section(content, "## 上下文构建协议")
        self.assertIsNotNone(section)
        self.assertIn("### Required contract", section)
        self.assertIn("#### Reading notes", section)
        self.assertNotIn('task="fake"', section)
        self.assertNotIn("## 完成判断", section)
        self.assertEqual(TOOL.context_manifest_region(content), section)
        self.assertIsNone(TOOL.extract_markdown_section(content, "### 上下文构建协议"))
        self.assertEqual(TOOL.context_manifest_region("# Task\nNo protocol section."), "# Task\nNo protocol section.")
        target = self.write("task.md", content)
        self.assertEqual(len(TOOL.extract_manifest_texts(target)), 1)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(TOOL.lint_manifest(target), 0)

    def test_section_file_type_and_missing_file_have_explicit_diagnostics(self) -> None:
        self.write("source.py", "## Looks like a heading\n")
        for required in (True, False):
            with self.subTest(required=required):
                code, report = self.lint_entries([{"path": "source.py", "section": "Looks like a heading", "required": str(required).lower()}])
                self.assertEqual(code, 1 if required else 0)
                self.assertTrue(any("supports Markdown" in item for item in report["errors" if required else "warnings"]))
                code, report = self.lint_entries([{"path": "missing.md", "section": "Missing", "required": str(required).lower()}])
                self.assertEqual(code, 1 if required else 0)
                messages = report["errors" if required else "warnings"]
                self.assertEqual(len(messages), 1)
                self.assertIn("path", messages[0])
                self.assertNotIn("section", messages[0])

    def test_contract_rejects_empty_section_or_intent(self) -> None:
        for section, intent in ((" ", "meaning"), ("Input contract", " ")):
            with self.subTest(section=section, intent=intent), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    TOOL.generate_manifest(self.arguments("--contract", "doc/design.md", section, intent))
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
