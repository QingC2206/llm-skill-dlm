#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import doc_llm_scaffold_tool as SCAFFOLD_TOOL  # noqa: E402


@contextlib.contextmanager
def patched_project_root(root: Path):
    original = SCAFFOLD_TOOL.PROJECT_ROOT
    SCAFFOLD_TOOL.PROJECT_ROOT = root
    try:
        yield
    finally:
        SCAFFOLD_TOOL.PROJECT_ROOT = original


class DocLlmScaffoldToolTests(unittest.TestCase):
    def test_init_project_writes_global_scaffold_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = mock.Mock(project_title="Fixture Project", dry_run=False, force=False)

            with patched_project_root(root):
                records = SCAFFOLD_TOOL.scaffold_project(args)

            brief = root / "docs-llm/BRIEF.md"
            l0 = root / "docs-llm/00_Global/L0_global_redlines.md"
            l1 = root / "docs-llm/00_Global/L1_target_architecture.md"
            sops = root / "docs-llm/SOPs/README.md"
            self.assertTrue(brief.exists())
            self.assertTrue(l0.exists())
            self.assertTrue(l1.exists())
            self.assertTrue(sops.exists())
            self.assertIn("Fixture Project", brief.read_text(encoding="utf-8"))
            self.assertIn(SCAFFOLD_TOOL.TODAY, l1.read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(records), 4)

    def test_init_domain_creates_expected_files_and_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = mock.Mock(
                domain_key="9_fixture",
                domain_title="Fixture Domain",
                root_path="app/Foo",
                dry_run=False,
                force=False,
            )

            with patched_project_root(root):
                records = SCAFFOLD_TOOL.scaffold_domain(args)

            domain_dir = root / "docs-llm/Domains/9_fixture"
            self.assertTrue((domain_dir / "L2_Modules").is_dir())
            self.assertTrue((domain_dir / "references").is_dir())
            self.assertTrue((domain_dir / "BRIEF.md").exists())
            self.assertTrue((domain_dir / "L0_fixture_rules.md").exists())
            router = domain_dir / "L1_fixture_router.md"
            self.assertTrue(router.exists())
            self.assertIn("Fixture Domain", router.read_text(encoding="utf-8"))
            self.assertIn("app/Foo", router.read_text(encoding="utf-8"))
            self.assertTrue(any(record.path.endswith("L1_fixture_router.md") for record in records))

    def test_create_l2_and_reference_fill_frontmatter_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            l2_args = mock.Mock(
                domain_key="9_fixture",
                module_key="foo_runtime",
                module_title="Foo Runtime",
                match_glob=["app/Foo/*.cs"],
                status="已实现",
                dry_run=False,
                force=False,
            )
            ref_args = mock.Mock(
                domain_key="9_fixture",
                topic_key="vendor_docs",
                topic_title="Vendor Docs",
                match_glob=["docs/vendor/**/*.md"],
                reference_type="official_doc",
                authority="vendor",
                status="已归档",
                dry_run=False,
                force=False,
            )

            with patched_project_root(root):
                SCAFFOLD_TOOL.scaffold_l2(l2_args)
                SCAFFOLD_TOOL.scaffold_reference(ref_args)

            l2_path = root / "docs-llm/Domains/9_fixture/L2_Modules/foo_runtime.md"
            ref_path = root / "docs-llm/Domains/9_fixture/references/vendor_docs/README.md"
            l2_text = l2_path.read_text(encoding="utf-8")
            ref_text = ref_path.read_text(encoding="utf-8")
            self.assertIn('domain: "9_fixture"', l2_text)
            self.assertIn('module: "foo_runtime"', l2_text)
            self.assertIn('  - "app/Foo/*.cs"', l2_text)
            self.assertIn(SCAFFOLD_TOOL.TODAY, l2_text)
            self.assertIn('topic: "vendor_docs"', ref_text)
            self.assertIn('authority: "vendor"', ref_text)
            self.assertIn('  - "docs/vendor/**/*.md"', ref_text)

    def test_create_sop_fills_frontmatter_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = mock.Mock(
                sop_key="app_ui_development",
                sop_title="APP UI Development",
                owner_domain="Domains/4_frontend",
                alias=["UI SOP", "APP UI"],
                match_glob=["src/App/**/*.xaml"],
                validator_command=[],
                dry_run=False,
                force=False,
            )

            with patched_project_root(root):
                records = SCAFFOLD_TOOL.scaffold_sop(args)

            sop_path = root / "docs-llm/SOPs/app_ui_development/README.md"
            sop_text = sop_path.read_text(encoding="utf-8")
            self.assertTrue((root / "docs-llm/SOPs/app_ui_development/checks").is_dir())
            self.assertIn('kind: "sop"', sop_text)
            self.assertIn('id: "app_ui_development"', sop_text)
            self.assertIn('owner_domain: "Domains/4_frontend"', sop_text)
            self.assertIn('  - "UI SOP"', sop_text)
            self.assertIn('  - "APP UI"', sop_text)
            self.assertIn('  - "src/App/**/*.xaml"', sop_text)
            self.assertIn("validator_commands: []", sop_text)
            self.assertIn("尚未配置校验命令", sop_text)
            self.assertNotIn("audit_app_ui_development.py", sop_text)
            self.assertEqual([], list((sop_path.parent / "checks").iterdir()))
            self.assertTrue(any(record.path.endswith("SOPs/app_ui_development/README.md") for record in records))

    def test_create_sop_preserves_explicit_validator_commands(self) -> None:
        commands = ["python checks/validate_inputs.py", "python checks/validate_outputs.py"]
        with tempfile.TemporaryDirectory() as tmp:
            with patched_project_root(Path(tmp)):
                rendered = SCAFFOLD_TOOL.render_sop("validation", "Validation", "global", [], [], commands)
        for command in commands:
            self.assertIn(f'  - "{command}"', rendered)
        self.assertIn("```bash\n" + "\n".join(commands) + "\n```", rendered)
        self.assertNotIn("尚未配置校验命令", rendered)

    def test_non_default_doc_llm_directory_is_auto_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "doc_llm").mkdir()
            proj_args = mock.Mock(project_title="Legacy Dir Project", dry_run=False, force=False)
            sop_args = mock.Mock(
                sop_key="ui_check",
                sop_title="UI Check",
                owner_domain="global",
                alias=[],
                match_glob=[],
                validator_command=[],
                dry_run=False,
                force=False,
            )

            with patched_project_root(root):
                SCAFFOLD_TOOL.scaffold_project(proj_args)
                SCAFFOLD_TOOL.scaffold_sop(sop_args)

            self.assertTrue((root / "doc_llm/BRIEF.md").exists())
            self.assertFalse((root / "docs-llm").exists())
            sop_text = (root / "doc_llm/SOPs/ui_check/README.md").read_text(encoding="utf-8")
            self.assertIn("validator_commands: []", sop_text)
            self.assertIn("尚未配置校验命令", sop_text)
            self.assertNotIn("audit_ui_check.py", sop_text)

    def test_main_supports_json_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patched_project_root(root), io.StringIO() as buffer:
                with contextlib.redirect_stdout(buffer):
                    with mock.patch.object(
                        sys,
                        "argv",
                        [
                            "doc_llm_scaffold_tool.py",
                            "--json",
                            "--dry-run",
                            "init-domain",
                            "9_fixture",
                            "--domain-title",
                            "Fixture Domain",
                        ],
                    ):
                        exit_code = SCAFFOLD_TOOL.main()
                payload = json.loads(buffer.getvalue())

            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["command"], "init-domain")
            self.assertTrue(any(item["action"] == "create" for item in payload["records"]))

    def test_init_domain_rejects_path_traversal_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = mock.Mock(
                domain_key="../escape",
                domain_title="Escape Domain",
                root_path="src/escape",
                dry_run=False,
                force=False,
            )

            with patched_project_root(root):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as ctx:
                        SCAFFOLD_TOOL.scaffold_domain(args)

            self.assertEqual(ctx.exception.code, 2)
            self.assertFalse((root.parent / "escape").exists())

    def test_create_l2_rejects_path_traversal_module_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = mock.Mock(
                domain_key="9_fixture",
                module_key="../escape",
                module_title="Escape Module",
                match_glob=[],
                status="已实现",
                dry_run=False,
                force=False,
            )

            with patched_project_root(root):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as ctx:
                        SCAFFOLD_TOOL.scaffold_l2(args)

            self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
