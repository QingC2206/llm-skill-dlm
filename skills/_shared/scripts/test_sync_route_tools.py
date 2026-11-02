#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import sync_l1_route as SYNC_L1_ROUTE  # noqa: E402
import validate_doc_llm as VALIDATOR  # noqa: E402


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@contextlib.contextmanager
def quiet_output():
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        yield


class SyncRouteToolTests(unittest.TestCase):
    def test_l1_sync_preserves_optional_sections_and_validates_with_or_without_numbering(self) -> None:
        layouts = {
            "tree_removed": "## 二、L2 路由表\nold\n\n## 四、关系\nkeep\n",
            "renumbered": "## 一、L2 路由表\nold\n\n## 二、关系\nkeep\n",
            "unnumbered": "## L2 路由表\nold\n\n## 关系\nkeep\n",
            "with_entry": "## 一、入口\nentry\n\n## 二、L2 路由表\nold\n\n## 三、关系\nkeep\n",
        }
        l2_table = "| Topic | Target |\n|---|---|\n| Runtime | `L2_Modules/runtime.md` |\n"
        reference_table = "| Topic | Target |\n|---|---|\n| Vendor | `references/vendor/README.md` |\n"
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in layouts.items():
                with self.subTest(layout=name):
                    domain_dir = Path(tmp) / name / "doc_llm/Domains/9_fixture"
                    l1_path = domain_dir / "L1_fixture_router.md"
                    write_text(l1_path, "# Fixture\n\n" + content)
                    for refs in (reference_table, None):
                        with self.subTest(has_reference=refs is not None), quiet_output():
                            self.assertTrue(SYNC_L1_ROUTE.update_l1_file(domain_dir, l2_table, refs))
                            report = VALIDATOR.validate_l1_router(l1_path, refs is not None)
                            self.assertEqual(report.errors, 0, [item.message for item in report.messages])
                            result = l1_path.read_text(encoding="utf-8-sig")
                            self.assertIn(l2_table, result)
                            self.assertIn("keep\n", result)
                            self.assertNotIn("old\n", result)
                            self.assertEqual("references/vendor/README.md" in result, refs is not None)
                            if name == "with_entry":
                                self.assertIn("## 一、入口\nentry\n", result)
                            if name == "unnumbered":
                                self.assertNotIn("、", result)
                            before = l1_path.read_bytes()
                            self.assertTrue(SYNC_L1_ROUTE.update_l1_file(domain_dir, l2_table, refs))
                            self.assertEqual(before, l1_path.read_bytes())

    def test_resolve_domain_dir_accepts_domain_name_under_domains_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            domain_dir = root / "doc_llm/Domains/9_fixture"
            domain_dir.mkdir(parents=True, exist_ok=True)

            resolved = SYNC_L1_ROUTE.resolve_domain_dir(root, "9_fixture")

            self.assertEqual(resolved, domain_dir.resolve())

    def test_resolve_domain_dir_rejects_path_outside_domains_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "scratch" / "9_escape"
            outside.mkdir(parents=True, exist_ok=True)

            with quiet_output(), self.assertRaises(SystemExit) as ctx:
                SYNC_L1_ROUTE.resolve_domain_dir(root, str(outside))

            self.assertEqual(ctx.exception.code, 1)

    def test_generate_l2_route_table_rejects_domain_outside_domains_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "scratch" / "9_escape"
            outside.mkdir(parents=True, exist_ok=True)

            with quiet_output(), self.assertRaises(SystemExit) as ctx:
                SYNC_L1_ROUTE.generate_l2_route_table(outside)

            self.assertEqual(ctx.exception.code, 1)

if __name__ == "__main__":
    unittest.main()
