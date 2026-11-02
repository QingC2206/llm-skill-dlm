from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_doc_llm as VALIDATOR


class TaskDependencyTests(unittest.TestCase):
    def setUp(self):
        out = VALIDATOR.PROJECT_ROOT / "out/dlm-dependency-tests"
        out.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=out)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tasks = self.root / "doc_llm/Tasks"
        self.patch = mock.patch.object(VALIDATOR, "PROJECT_ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def task(self, epic: str, task_id: str, depends: str = "无") -> Path:
        directory = self.tasks / (epic + "_fixture")
        path = directory / "subtasks" / (task_id + "_fixture.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        (directory / "README.md").write_text("# " + epic + "\n", encoding="utf-8")
        path.write_text(
            f"# {task_id}\n## Meta\n| 字段 | 值 |\n|---|---|\n"
            f"| 状态 | 📋 待办 |\n| 依赖 | {depends} |\n", encoding="utf-8"
        )
        return path

    def check(self, *paths):
        with contextlib.redirect_stdout(io.StringIO()):
            return VALIDATOR.validate_subtask_dependency_graph(list(paths))

    def test_same_subtask_number_in_another_epic_is_not_a_self_dependency(self):
        source = self.task("E001", "S001", "E002/S001")
        self.task("E002", "S001")
        self.assertEqual(self.check(source).errors, 0)

    def test_cross_epic_cycle_reports_full_identity(self):
        source = self.task("E001", "S001", "E002/S001")
        self.task("E002", "S001", "E001/S001")
        report = self.check(source)
        self.assertEqual(report.errors, 1)
        self.assertIn("E001/S001 -> E002/S001 -> E001/S001", report.messages[0].message)

    def test_range_checks_missing_middle_and_keeps_external_namespace(self):
        self.task("E002", "S011")
        self.task("E002", "S013")
        self.task("E001", "S012")  # Same local ID must not satisfy E002's gap.
        for dash in ("-", "–", "—", "～", "至"):
            with self.subTest(dash=dash):
                source = self.task("E001", "S001", f"E002/S011{dash}S013")
                report = self.check(source)
                self.assertEqual(report.errors, 1)
                self.assertIn("E002/S012", report.messages[0].message)

    def test_legacy_qualified_form_and_letter_ranges_resolve(self):
        source = self.task("E001", "S001", "E004-034, **E002 S013a–S013c**")
        self.task("E004", "S034")
        for task_id in ("S013a", "S013b", "S013c"):
            self.task("E002", task_id)
        self.assertEqual(self.check(source).errors, 0)

    def test_local_slash_list_checks_every_task_without_inventing_a_namespace(self):
        source = self.task("E001", "S001", "S002/S004/S006 已验证的输入 owner")
        self.task("E001", "S002")
        self.task("E001", "S006")
        report = self.check(source)
        self.assertEqual(report.errors, 1)
        self.assertTrue(any("S004" in item.message and item.level == "error" for item in report.messages))
        self.task("E001", "S004")
        self.assertEqual(self.check(source).errors, 0)

    def test_missing_and_ambiguous_epic_are_not_local_fallbacks(self):
        source = self.task("E001", "S001", "E002/S001")
        self.assertEqual(self.check(source).errors, 1)
        self.task("E002", "S001")
        (self.tasks / "E002_other").mkdir()
        report = self.check(source)
        self.assertEqual(report.errors, 1)
        self.assertIn("不唯一", report.messages[0].message)

    def test_duplicate_subtask_ids_are_errors(self):
        source = self.task("E001", "S001", "S002")
        target = self.task("E001", "S002")
        target.with_name("S002_duplicate.md").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        report = self.check(source)
        self.assertEqual(report.errors, 1)
        self.assertIn("不唯一", report.messages[0].message)

    def test_related_closure_does_not_read_unrelated_tasks_or_require_done_status(self):
        source = self.task("E001", "S001", "E002/S002")
        target = self.task("E002", "S002")
        unrelated = self.task("E002", "S099", "S999")
        with mock.patch.object(VALIDATOR, "read_text", wraps=VALIDATOR.read_text) as reads:
            self.assertEqual(self.check(source).errors, 0)
        read_paths = {call.args[0] for call in reads.call_args_list}
        self.assertEqual(read_paths, {source.resolve(), target.resolve()})
        self.assertNotIn(unrelated, read_paths)

    def test_epic_only_reference_checks_existence_without_visiting_its_tasks(self):
        source = self.task("E001", "S001", "E002 Verified")
        self.task("E002", "S099", "S999")
        report = self.check(source)
        self.assertEqual(report.errors, 0)
        self.assertEqual(report.warnings, 1)

    def test_uninterpreted_conditions_are_visible_without_claiming_readiness(self):
        source = self.task("E001", "S001", "业务接口交付后开始")
        report = self.check(source)
        self.assertEqual(report.errors, 0)
        self.assertEqual(report.warnings, 1)
        self.assertIn("产物就绪需执行者核实", report.messages[0].message)

    def test_malformed_or_ambiguous_task_expressions_are_errors(self):
        for dependency in ("S001-Sbogus", "S013-S011", "E002/S001/S002", "S001-E002/S003"):
            with self.subTest(dependency=dependency):
                source = self.task("E001", "S004", dependency)
                self.assertGreater(self.check(source).errors, 0)


if __name__ == "__main__":
    unittest.main()
