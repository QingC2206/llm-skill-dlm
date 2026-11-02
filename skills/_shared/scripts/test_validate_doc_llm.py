#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().with_name("validate_doc_llm.py")
SPEC = importlib.util.spec_from_file_location("validate_doc_llm_under_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load validator module from {MODULE_PATH}")
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_fixture_sop(root: Path, sop_id: str = "fixture_sop", title: str = "Fixture SOP") -> None:
    write_text(root / f"doc_llm/SOPs/{sop_id}/README.md", f"# {title}\n")


@contextlib.contextmanager
def patched_project_root(root: Path):
    original = VALIDATOR.PROJECT_ROOT
    VALIDATOR.PROJECT_ROOT = root
    try:
        yield
    finally:
        VALIDATOR.PROJECT_ROOT = original


@contextlib.contextmanager
def quiet_stdout():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield


class ValidateDocLlmTests(unittest.TestCase):
    def test_task_depth_validates_enum_and_assessment_without_regrading_legacy(self) -> None:
        base = "## Meta\n\n| 字段 | 值 |\n|---|---|\n| 状态 | 📋 待办 |\n"
        assessment = "\n## 任务深度评估\n\n- 预估改动量：2 files, logic 100–200 lines\n- 评估依据：ABI errors affect every consumer\n"
        for depth in ("Ultra", "Max", "High", "Medium", "Lite"):
            text = base + f"| 任务深度 | {depth} |\n" + assessment
            with self.subTest(depth=depth), quiet_stdout():
                report = VALIDATOR.validate_task_depth(text, VALIDATOR.parse_meta_table(text), "fixture")
            self.assertEqual(report.errors, 0)
        invalid = [
            base + "| 任务深度 | Low |\n" + assessment,
            base + "| 任务深度 | |\n" + assessment,
            base + "| 任务深度 | Max |\n",
            base + "| 任务深度 | Max |\n" + assessment.replace("ABI errors affect every consumer", "{依据}"),
            base + "| 任务深度 | Max |\n" + assessment.replace("2 files, logic 100–200 lines", ""),
        ]
        for text in invalid:
            with self.subTest(text=text), quiet_stdout():
                report = VALIDATOR.validate_task_depth(text, VALIDATOR.parse_meta_table(text), "fixture")
                self.assertGreater(report.errors, 0)
        with quiet_stdout():
            report = VALIDATOR.validate_task_depth(base, VALIDATOR.parse_meta_table(base), "legacy")
        self.assertEqual(report.errors, 0)
        self.assertEqual(report.warnings, 1)

    @staticmethod
    def boundary_content(
        *,
        kind: str,
        write_scope: str,
        forbidden_scope: str = "engine/**",
    ) -> str:
        root_attrs = 'default_outside_write="forbidden"'
        if kind == "subtask":
            root_attrs += ' inherits="epic" mode="subset"'
        meta_lines = [
            "## Meta",
            "",
            "| 字段 | 值 |",
            "|------|---|",
        ]
        return "\n".join(
            meta_lines
            + [
                "",
                "## 授权边界",
                "",
                f"<modification_boundary {root_attrs}>",
                f'  <scope kind="write" path="{write_scope}" />',
                '  <scope kind="generated" path="out/**" />',
                f'  <scope kind="forbidden" path="{forbidden_scope}" />',
                "</modification_boundary>",
            ]
        )

    def test_explicit_epic_boundary_is_valid(self) -> None:
        content = self.boundary_content(kind="epic", write_scope="app/Workbench/**")

        with quiet_stdout():
            report = VALIDATOR.validate_modification_boundary(content, "fixture", contract_kind="epic")

        self.assertEqual(report.errors, 0)

    def test_subtask_boundary_rejects_write_scope_outside_epic(self) -> None:
        epic_content = self.boundary_content(kind="epic", write_scope="app/Workbench/**")
        subtask_content = self.boundary_content(kind="subtask", write_scope="engine/Runtime/**", forbidden_scope="native/**")

        with quiet_stdout():
            report = VALIDATOR.validate_modification_boundary(
                subtask_content,
                "fixture",
                contract_kind="subtask",
                epic_content=epic_content,
            )

        self.assertGreaterEqual(report.errors, 1)
        self.assertTrue(any("越过父 Epic 修改边界" in item.message for item in report.messages))

    def test_boundary_rejects_write_forbidden_overlap(self) -> None:
        content = self.boundary_content(
            kind="epic",
            write_scope="app/Workbench/**",
            forbidden_scope="app/Workbench/Generated/**",
        )

        with quiet_stdout():
            report = VALIDATOR.validate_modification_boundary(content, "fixture", contract_kind="epic")

        self.assertGreaterEqual(report.errors, 1)
        self.assertTrue(any("可写路径与 forbidden 路径重叠" in item.message for item in report.messages))

    def test_subtask_can_narrow_file_and_generated_scopes(self) -> None:
        epic = self.boundary_content(kind="epic", write_scope="app/Workbench/**")
        subtask = self.boundary_content(kind="subtask", write_scope="app/Workbench/Editor/View.cs")
        subtask = subtask.replace('kind="generated" path="out/**"', 'kind="generated" path="out/editor/**"')
        with quiet_stdout():
            report = VALIDATOR.validate_modification_boundary(
                subtask, "fixture", contract_kind="subtask", epic_content=epic,
            )
        self.assertEqual(report.errors, 0)

    def test_frozen_state_and_read_manifest_do_not_expand_write_boundary(self) -> None:
        epic = self.boundary_content(kind="epic", write_scope="app/Workbench/Editor/**")
        for target in ("app/Workbench/**", "app/Workbench/Other/View.cs"):
            subtask = self.boundary_content(kind="subtask", write_scope=target)
            subtask = subtask.replace("|------|---|", "|------|---|\n| 基线状态 | Frozen |")
            subtask += (
                '\n## 上下文构建协议\n<context_manifest task="S001" epic="E999">\n'
                '  <file path="app/Workbench/Other/View.cs" required="true" />\n'
                '</context_manifest>\n'
            )
            with self.subTest(target=target), quiet_stdout():
                report = VALIDATOR.validate_modification_boundary(
                    subtask, "fixture", contract_kind="subtask", epic_content=epic,
                )
            self.assertGreater(report.errors, 0)

    def test_generated_scope_cannot_be_repurposed_as_source_write_scope(self) -> None:
        epic = self.boundary_content(kind="epic", write_scope="app/Workbench/**")
        subtask = self.boundary_content(kind="subtask", write_scope="app/Workbench/Editor/View.cs")
        subtask = subtask.replace('kind="generated" path="out/**"', 'kind="generated" path="app/Workbench/Generated/**"')
        with quiet_stdout():
            report = VALIDATOR.validate_modification_boundary(
                subtask, "fixture", contract_kind="subtask", epic_content=epic,
            )
        self.assertGreater(report.errors, 0)

    def test_missing_boundary_is_advisory(self) -> None:
        content = "## Meta\n\n| 字段 | 值 |\n|------|---|\n| 状态 | 📋 待办 |\n"

        with quiet_stdout():
            report = VALIDATOR.validate_modification_boundary(content, "fixture", contract_kind="epic")

        self.assertEqual(report.errors, 0)
        self.assertEqual(report.warnings, 1)

    def test_read_only_scopes_reject_overlapping_writes_and_generated_outputs(self) -> None:
        base = self.boundary_content(kind="epic", write_scope="app/Workbench/**")
        for protected in ("app/Workbench/Contracts/**", "out/release/**"):
            content = base.replace(
                "</modification_boundary>",
                f'<scope kind="read_only" path="{protected}" />\n</modification_boundary>',
            )
            with self.subTest(protected=protected), quiet_stdout():
                report = VALIDATOR.validate_modification_boundary(content, "fixture", contract_kind="epic")
                self.assertGreater(report.errors, 0)
                self.assertTrue(any("read_only" in item.message for item in report.messages))

    def test_subtask_cannot_omit_parent_restrictions_to_gain_write_access(self) -> None:
        for restricted_kind in ("read_only", "forbidden"):
            for mutable_kind, target in (("write", "app/Workbench/Contracts/Config.cs"), ("generated", "out/release/result.json")):
                epic = self.boundary_content(kind="epic", write_scope="app/Workbench/**")
                epic = epic.replace(
                    "</modification_boundary>",
                    f'<scope kind="{restricted_kind}" path="{target}" />\n</modification_boundary>',
                )
                child = self.boundary_content(kind="subtask", write_scope="app/Workbench/Editor/View.cs")
                if mutable_kind == "write":
                    child = child.replace("app/Workbench/Editor/View.cs", target)
                else:
                    child = child.replace('kind="generated" path="out/**"', f'kind="generated" path="{target}"')
                with self.subTest(kind=restricted_kind, mutable=mutable_kind), quiet_stdout():
                    report = VALIDATOR.validate_modification_boundary(child, "fixture", contract_kind="subtask", epic_content=epic)
                    self.assertGreater(report.errors, 0)
                    self.assertTrue(any(restricted_kind in item.message for item in report.messages))

    def test_read_only_scope_allows_disjoint_writes_and_subset_inheritance(self) -> None:
        epic = self.boundary_content(kind="epic", write_scope="app/Workbench/Editor/**")
        epic = epic.replace(
            "</modification_boundary>",
            '<scope kind="read_only" path="app/Workbench/Contracts/**" />\n</modification_boundary>',
        )
        child = self.boundary_content(kind="subtask", write_scope="app/Workbench/Editor/View.cs")
        with quiet_stdout():
            parent_report = VALIDATOR.validate_modification_boundary(epic, "parent", contract_kind="epic")
            child_report = VALIDATOR.validate_modification_boundary(child, "child", contract_kind="subtask", epic_content=epic)
        self.assertEqual(parent_report.errors, 0)
        self.assertEqual(child_report.errors, 0)

    def test_duplicate_or_malformed_boundaries_are_errors_even_without_legacy_warnings(self) -> None:
        base = self.boundary_content(kind="epic", write_scope="app/Workbench/**")
        invalid = {
            "duplicate": base + "\n" + base,
            "unclosed": base.replace("</modification_boundary>", ""),
            "extra_open": base + '\n<modification_boundary default_outside_write="forbidden">',
            "extra_close": base + "\n</modification_boundary>",
            "nested": base.replace("</modification_boundary>", base + "\n</modification_boundary>"),
            "malformed_scope": base.replace('kind="generated" path="out/**" />', 'kind="generated" path="out/**" >'),
            "duplicate_attribute": base.replace('kind="generated"', 'kind="read_only" kind="generated"'),
        }
        for name, content in invalid.items():
            with self.subTest(case=name), quiet_stdout():
                report = VALIDATOR.validate_modification_boundary(content, "fixture", contract_kind="epic", warn_legacy=False)
                self.assertGreater(report.errors, 0)
                self.assertEqual(report.warnings, 0)

    def test_subtask_rejects_duplicate_parent_boundary(self) -> None:
        epic = self.boundary_content(kind="epic", write_scope="app/Workbench/**")
        child = self.boundary_content(kind="subtask", write_scope="app/Workbench/Editor/View.cs")
        with quiet_stdout():
            report = VALIDATOR.validate_modification_boundary(child, "fixture", contract_kind="subtask", epic_content=epic + "\n" + epic)
        self.assertGreater(report.errors, 0)

    def test_l1_requires_routes_but_not_numbered_or_optional_sections(self) -> None:
        cases = [
            ("## L2 路由表\n", False, False),
            ("## 一、L2 路由表\n", False, False),
            ("## 二、L2 路由表\n", False, False),
            ("## L2 路由表\n\n## 参考资料路由\n", True, False),
            ("## 入口\n", False, True),
            ("## L2 路由表\n", True, True),
            ("## L2 路由表\n\n## 参考资料路由\n", False, True),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "L1_fixture_router.md"
            for content, has_references, expects_error in cases:
                with self.subTest(content=content, refs=has_references), quiet_stdout():
                    write_text(path, "# Fixture\n\n" + content)
                    report = VALIDATOR.validate_l1_router(path, has_references)
                    self.assertEqual(report.errors > 0, expects_error, [item.message for item in report.messages])

    def test_dot_agents_required_file_resolves_from_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(root / ".agents/skills/dlm-plan/SKILL.md", "# skill\n")
            manifest = (
                '<context_manifest task="S001" epic="E999">\n'
                '  <file path=".agents/skills/dlm-plan/SKILL.md" intent="skill" required="true" />\n'
                "</context_manifest>"
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(manifest, "fixture")

            self.assertEqual(report.errors, 0)

    def test_tasks_path_resolves_from_doc_llm_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(root / "doc_llm/Tasks/backlog.md", "# backlog\n")
            manifest = (
                '<context_manifest task="S001" epic="E999">\n'
                '  <file path="Tasks/backlog.md" intent="backlog" required="true" />\n'
                "</context_manifest>"
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(manifest, "fixture")

            self.assertEqual(report.errors, 0)

    def test_sop_path_resolves_from_doc_llm_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_fixture_sop(root)
            manifest = (
                '<context_manifest task="S001" epic="E999">\n'
                '  <file path="SOPs/fixture_sop/README.md" intent="Fixture SOP" required="true" />\n'
                "</context_manifest>"
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(manifest, "fixture")

            self.assertEqual(report.errors, 0)

    def test_multiple_context_manifest_blocks_are_merged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(root / "doc_llm/00_Global/L0_global_redlines.md", "# L0\n")
            write_text(root / "doc_llm/Tasks/E999_fixture/README.md", "# Epic\n")
            manifest = "\n".join(
                [
                    "p0",
                    "> P0 hot context.",
                    "```xml",
                    '<context_manifest task="S001" epic="E999" priority="P0">',
                    '  <file path="00_Global/L0_global_redlines.md" intent="全局红线" required="true" />',
                    "</context_manifest>",
                    "```",
                    "p1",
                    "> P1 warm context.",
                    "```xml",
                    '<context_manifest task="S001" epic="E999" priority="P1">',
                    '  <file path="Tasks/E999_fixture/README.md" intent="目标与进度" required="false" />',
                    "</context_manifest>",
                    "```",
                ]
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(manifest, "fixture")

            self.assertEqual(report.errors, 0)

    def test_priority_manifest_blocks_without_description_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(root / "doc_llm/00_Global/L0_global_redlines.md", "# L0\n")
            manifest = "\n".join(
                [
                    "p0",
                    "```xml",
                    '<context_manifest task="S001" epic="E999" priority="P0">',
                    '  <file path="00_Global/L0_global_redlines.md" intent="全局红线" required="true" />',
                    "</context_manifest>",
                    "```",
                ]
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(manifest, "fixture")

            self.assertEqual(report.errors, 0)
            self.assertEqual(report.warnings, 1)
            self.assertIn("缺少说明行", report.messages[0].message)

    def test_priority_manifest_block_inconsistent_attrs_are_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(root / "doc_llm/00_Global/L0_global_redlines.md", "# L0\n")
            manifest = "\n".join(
                [
                    "p2",
                    "> P2 cold context.",
                    "```xml",
                    '<context_manifest task="S001" epic="E999" priority="P2">',
                    '  <file path="00_Global/L0_global_redlines.md" intent="全局红线" required="true" />',
                    "</context_manifest>",
                    "```",
                ]
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(manifest, "fixture")

            self.assertEqual(report.errors, 1)
            self.assertIn('priority="P2"', report.messages[0].message)

    def test_context_manifest_ignores_later_manifest_outside_protocol_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(root / "doc_llm/00_Global/L0_global_redlines.md", "# L0\n")
            content = "\n".join(
                [
                    "# S001",
                    "",
                    "## 上下文构建协议",
                    "p0",
                    "> P0 hot context.",
                    "```xml",
                    '<context_manifest task="S001" epic="E999" priority="P0">',
                    '  <file path="00_Global/L0_global_redlines.md" intent="全局红线" required="true" />',
                    "</context_manifest>",
                    "```",
                    "",
                    "## 执行日志",
                    "```xml",
                    '<context_manifest task="S001" epic="E999" priority="P0">',
                    '  <file path="Tasks/missing.md" intent="旧示例" required="true" />',
                    "</context_manifest>",
                    "```",
                ]
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(content, "fixture")

            self.assertEqual(report.errors, 0)

    def test_context_manifest_needs_no_meta_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(root / "doc_llm/00_Global/L0_global_redlines.md", "# L0\n")
            content = "\n".join(
                [
                    "# S001",
                    "",
                    "## Meta",
                    "| 字段 | 值 |",
                    "|------|---|",
                    "| 状态 | 📋 待办 |",
                    "",
                    "## 上下文构建协议",
                    "p0",
                    "> P0 hot context.",
                    "```xml",
                    '<context_manifest task="S001" epic="E999" priority="P0">',
                    '  <file path="00_Global/L0_global_redlines.md" intent="全局红线" required="true" />',
                    "</context_manifest>",
                    "```",
                ]
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(content, "fixture")

            self.assertEqual(report.errors, 0)
            self.assertEqual(report.warnings, 0)

    def test_large_p0_source_file_warns_to_use_local_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(root / "src/Large.cs", "\n".join(f"// line {index}" for index in range(301)))
            content = "\n".join(
                [
                    "# S001",
                    "",
                    "## 上下文构建协议",
                    "p0",
                    "> P0 hot context.",
                    "```xml",
                    '<context_manifest task="S001" epic="E999" priority="P0">',
                    '  <file path="src/Large.cs" intent="目标函数局部" required="true" />',
                    "</context_manifest>",
                    "```",
                ]
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(content, "fixture")

            self.assertEqual(report.errors, 0)
            self.assertEqual(report.warnings, 1)
            self.assertIn("不要全文读取", report.messages[0].message)

    def test_priority_label_contract_still_checks_after_plain_description_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(root / "doc_llm/00_Global/L0_global_redlines.md", "# L0\n")
            manifest = "\n".join(
                [
                    "p0",
                    "P0 hot context.",
                    "```xml",
                    '<context_manifest task="S001" epic="E999" priority="P1">',
                    '  <file path="00_Global/L0_global_redlines.md" intent="全局红线" required="false" />',
                    "</context_manifest>",
                    "```",
                ]
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(manifest, "fixture")

            self.assertEqual(report.errors, 1)
            self.assertEqual(report.warnings, 1)
            self.assertTrue(any("不一致" in item.message for item in report.messages))

    def test_missing_required_file_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = (
                '<context_manifest task="S001" epic="E999">\n'
                '  <file path="Tasks/backlog.md" intent="backlog" required="true" />\n'
                "</context_manifest>"
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(manifest, "fixture")

            self.assertEqual(report.errors, 1)

    def test_manifest_path_outside_repo_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = (root.parent / "external.md").resolve()
            outside.write_text("# external\n", encoding="utf-8")
            manifest = (
                '<context_manifest task="S001" epic="E999">\n'
                f'  <file path="{outside.as_posix()}" intent="external" required="true" />\n'
                "</context_manifest>"
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(manifest, "fixture")

            self.assertEqual(report.errors, 1)
            self.assertIn("越出仓库根目录", report.messages[0].message)

    def test_dependency_cycle_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtasks_dir = root / "doc_llm/Tasks/E999_fixture/subtasks"
            write_text(
                subtasks_dir / "S001_alpha.md",
                "# S001\n\n## Meta\n| 字段 | 值 |\n|------|---|\n| 依赖 | S002 |\n",
            )
            write_text(
                subtasks_dir / "S002_beta.md",
                "# S002\n\n## Meta\n| 字段 | 值 |\n|------|---|\n| 依赖 | S001 |\n",
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_subtask_dependency_graph(
                    sorted(subtasks_dir.glob("S*.md"))
                )

            self.assertEqual(report.errors, 1)

    def test_manifest_l2_reference_must_exist_in_l1_router(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            domain_dir = root / "doc_llm/Domains/9_fixture"
            write_text(
                domain_dir / "L1_fixture_router.md",
                "# D9\n\n## 二、L2 路由表\n\n| 当你需要了解... | 去读 |\n|---|---|\n",
            )
            write_text(domain_dir / "L2_Modules/foo.md", "# foo\n")
            manifest = (
                '<context_manifest task="S001" epic="E999">\n'
                '  <file path="Domains/9_fixture/L2_Modules/foo.md" intent="foo" required="true" />\n'
                "</context_manifest>"
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_context_manifest(manifest, "fixture")

            self.assertEqual(report.errors, 1)

    def test_l0_purity_warns_on_code_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            l0_path = root / "doc_llm/00_Global/L0_global_redlines.md"
            write_text(
                l0_path,
                "# L0\n\n```csharp\npublic void Foo() {}\n```\n",
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_l0_purity(l0_path)

            self.assertGreaterEqual(report.warnings, 1)

    def test_l1_purity_warns_on_signature_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            l1_path = root / "doc_llm/Domains/9_fixture/L1_fixture_router.md"
            write_text(
                l1_path,
                "# L1\n\n## 二、L2 路由表\n| 签名 | 返回值 |\n|---|---|\n| Foo() | void |\n",
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_l1_purity(l1_path)

            self.assertGreaterEqual(report.warnings, 1)

    def test_utf8_readability_reports_invalid_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad_path = root / "doc_llm/Domains/9_fixture/L0_fixture_rules.md"
            bad_path.parent.mkdir(parents=True, exist_ok=True)
            bad_path.write_bytes(b"\xff\xfe\x00\x00")

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_utf8_readability(bad_path)

            self.assertEqual(report.errors, 1)

    def test_validation_report_to_dict_contains_messages(self) -> None:
        report = VALIDATOR.ValidationReport()
        with quiet_stdout():
            report.warn("fixture", "warn message")

        payload = report.to_dict()
        self.assertEqual(payload["summary"]["warn"], 1)
        self.assertEqual(payload["messages"][0]["scope"], "fixture")
        self.assertEqual(payload["messages"][0]["message"], "warn message")

    def test_main_supports_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            global_dir = root / "doc_llm/00_Global"
            write_text(global_dir / "L0_global_redlines.md", "# L0\n")
            write_text(global_dir / "L1_target_architecture.md", "# L1\n")

            with patched_project_root(root), io.StringIO() as buffer:
                with contextlib.redirect_stdout(buffer):
                    with mock.patch.object(sys, "argv", ["validate_doc_llm.py", "--json", "doc_llm/00_Global"]):
                        exit_code = VALIDATOR.main()
                payload = json.loads(buffer.getvalue())

            self.assertEqual(exit_code, 0)
            self.assertIn("summary", payload)
            self.assertIn("messages", payload)
            self.assertEqual(payload["summary"]["error"], 0)

    def test_validate_target_argument_reports_missing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_target_argument("doc_llm/00_Global")

            self.assertEqual(report.errors, 1)
            self.assertEqual(report.messages[0].scope, "cli")

    def test_validate_target_argument_rejects_target_outside_doc_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_text(root / "notes.md", "# notes\n")

            with patched_project_root(root), quiet_stdout():
                report_default = VALIDATOR.validate_target_argument("notes.md")
            self.assertEqual(report_default.errors, 1)
            self.assertIn("inside docs-llm", report_default.messages[0].message)

            (root / "doc_llm").mkdir()
            with patched_project_root(root), quiet_stdout():
                report_compat = VALIDATOR.validate_target_argument("notes.md")
            self.assertEqual(report_compat.errors, 1)
            self.assertIn("inside doc_llm", report_compat.messages[0].message)

    def test_validate_sop_readme_accepts_flexible_body_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sop_path = root / "doc_llm/SOPs/app_ui/README.md"
            write_text(
                sop_path,
                "\n".join(
                    [
                        "---",
                        'kind: "sop"',
                        'id: "app_ui"',
                        'title: "APP UI"',
                        'owner_domain: "global"',
                        'status: "active"',
                        "last_updated: 2026-05-05",
                        "activation_aliases:",
                        '  - "APP UI"',
                        "match_globs:",
                        '  - "src/App/**/*.xaml"',
                        "---",
                        "",
                        "# APP UI",
                        "",
                        "## Trigger",
                        "APP surface changes.",
                        "",
                        "## Flow And Evidence",
                        "1. Inspect the affected views.",
                        "2. Keep validation notes with the task.",
                        "",
                        "## Local Rules",
                        "- Prefer theme resources over literals.",
                    ]
                ),
            )

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_sop_readme(sop_path)

            self.assertEqual(report.errors, 0)

    def test_validate_sop_readme_still_requires_routing_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sop_path = root / "doc_llm/SOPs/app_ui/README.md"
            write_text(sop_path, "# APP UI\n\n## Any Shape\n\nContent.\n")

            with patched_project_root(root), quiet_stdout():
                report = VALIDATOR.validate_sop_readme(sop_path)

            self.assertEqual(report.errors, 1)
            self.assertIn("缺少 YAML front-matter", report.messages[0].message)


if __name__ == "__main__":
    unittest.main()
