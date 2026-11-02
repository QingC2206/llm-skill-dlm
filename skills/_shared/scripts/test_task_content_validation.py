#!/usr/bin/env python3
"""Default task-content checks; no product implementation is executed."""
from __future__ import annotations

import contextlib
import importlib.util
import inspect
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("task_content_validator_under_test", SCRIPT_DIR / "validate_doc_llm.py")
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


EPIC = """# E999: 交接夹具
## 核心目标
公开结果保持原作用身份。
## 当前状态
仅完成计划，产品尚未实施。
## 范围
src 由本任务维护。
<modification_boundary default_outside_write="forbidden">
  <scope kind="write" path="src/**" />
</modification_boundary>
## 上下文构建协议
<context_manifest task="E999" epic="E999">
  <file path="contract.md" section="结果语义" intent="现存业务合同" required="true" />
</context_manifest>
## 子任务索引
| ID | 文件 |
|---|---|
| S001 | subtasks/S001_example.md |
"""

SUBTASK = """# S001: 保留原作用身份
## Meta
| 字段 | 值 |
|---|---|
| 状态 | 📋 待办 |
| 优先级 | P1 |
| 依赖 | 无 |
| 基线状态 | Draft |
## 核心目标
恢复读取返回同一次操作的结果。
## 方案承接与实施设计
按 contract.md 的结果语义复用现有 observer；新请求和原关联观察分别处理。
## 输入与交付
- 输入/进入条件：当前 contract.md 已存在；S002 的 observer.py 尚待交付，通过其读取用例后开始接入。
- 交付/下游：src/future_adapter.py 供调用层读取原操作；当前文件尚不存在。
## 授权边界
<modification_boundary default_outside_write="forbidden" inherits="epic" mode="subset">
  <scope kind="write" path="src/future_adapter.py" />
</modification_boundary>
## 上下文构建协议
<context_manifest task="S001" epic="E999" priority="P0">
  <file path="contract.md" section="结果语义" intent="现存结果合同，未来实现按输入条件等待" required="true" />
</context_manifest>
## 完成判断
- 成功：中断后的查询仍返回同一操作，写入次数保持一次。
- 失败：未知作用保持未知，不重新提交。
- 直接证据：运行将新增的 tests/recovery.py 中断后读取用例，核对操作身份与写入次数。
- 停止线：原关联恢复及失败反例通过后交给调用层，停止扩张其它业务。
## 执行日志
计划待进入条件就绪。
"""


class TaskContentValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        out = VALIDATOR.PROJECT_ROOT / "out" / "dlm-plan-content-tests"
        out.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="case-", dir=out)
        self.root = Path(self.temp.name)
        self.epic = self.root / "doc_llm" / "Tasks" / "E999_example"
        self.task = self.epic / "subtasks" / "S001_example.md"
        self.write(self.root / "contract.md", "# 合同\n\n## 结果语义\n保持同一作用身份。\n")
        self.write(self.epic / "README.md", EPIC)
        self.write(self.task, SUBTASK)
        self.root_patch = mock.patch.object(VALIDATOR, "PROJECT_ROOT", self.root)
        self.doc_patch = mock.patch.object(VALIDATOR, "DOC_LLM_DIR", "doc_llm")
        self.output_patch = mock.patch.object(VALIDATOR, "TEXT_OUTPUT", False)
        self.root_patch.start()
        self.doc_patch.start()
        self.output_patch.start()

    def tearDown(self) -> None:
        self.output_patch.stop()
        self.doc_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def report(self, content: str = SUBTASK):
        self.write(self.task, content)
        return VALIDATOR.validate_subtask(self.task)

    @staticmethod
    def replace_section(content: str, heading: str, body: str) -> str:
        section = VALIDATOR.extract_section(content, "## " + heading)
        assert section is not None
        return content.replace("## " + heading + "\n" + section, "## " + heading + "\n" + body + "\n", 1)

    @staticmethod
    def remove_section(content: str, heading: str) -> str:
        section = VALIDATOR.extract_section(content, "## " + heading)
        assert section is not None
        return content.replace("## " + heading + "\n" + section, "", 1)

    def test_complete_draft_accepts_future_artifacts_and_preserves_bytes(self) -> None:
        before = self.task.read_bytes()
        report = VALIDATOR.validate_subtask(self.task)
        self.assertEqual(report.errors, 0, report.to_dict())
        self.assertFalse((self.root / "src/future_adapter.py").exists())
        self.assertEqual(before, self.task.read_bytes())
        self.assertFalse(any("契约通过" in message.message for message in report.messages))

    def test_validation_entry_points_have_no_handoff_mode(self) -> None:
        for name in (
            "validate_subtask", "validate_epic_readme", "validate_epic_dir",
            "validate_tasks_root", "validate_single_path", "validate_target_argument",
        ):
            with self.subTest(function=name):
                self.assertNotIn("handoff", inspect.signature(getattr(VALIDATOR, name)).parameters)

    def test_design_examples_cannot_replace_real_protocol_or_acceptance_sections(self) -> None:
        design_example = "\n```markdown\n## 上下文构建协议\nnot a real manifest\n## 完成判断\n{核心目标}\n```\n### 设计细节\n消费原 observer 的版本，不创建第二个发布者。\n"
        content = SUBTASK.replace("## 输入与交付", design_example + "\n## 输入与交付")
        report = self.report(content)
        self.assertEqual(report.errors, 0, report.to_dict())
        actual = VALIDATOR.extract_section(content, "## 完成判断")
        self.assertIn("中断后的查询仍返回同一操作", actual)
        self.assertNotIn("{核心目标}", actual)

    def test_contract_headings_are_xml_decoded_before_resolution(self) -> None:
        self.write(self.root / "contract.md", '# 合同\n## 结果 "语义" & 行为\n保持作用身份。\n')
        for section_attribute in (
            'section="结果 &quot;语义&quot; &amp; 行为"',
            "section='结果 \"语义\" &amp; 行为'",
        ):
            with self.subTest(section_attribute=section_attribute):
                content = SUBTASK.replace('section="结果语义"', section_attribute)
                report = self.report(content)
                self.assertEqual(report.errors, 0, report.to_dict())

    def test_default_validation_rejects_draft_placeholders(self) -> None:
        content = SUBTASK.replace("恢复读取返回同一次操作的结果。", "{核心目标}")
        content = content.replace("按 contract.md 的结果语义复用现有 observer；新请求和原关联观察分别处理。", "{方案承接与实施设计}")
        report = self.report(content)
        self.assertGreaterEqual(report.errors, 2)
        self.assertTrue(any("模板占位符" in message.message for message in report.messages))

    def test_pending_upstream_task_is_allowed_without_future_implementation_file(self) -> None:
        upstream = self.epic / "subtasks" / "S002_observer.md"
        self.write(upstream, SUBTASK.replace("S001", "S002"))
        self.write(self.task, SUBTASK.replace("| 依赖 | 无 |", "| 依赖 | S002 |"))
        report = VALIDATOR.validate_epic_dir(self.epic)
        self.assertEqual(report.errors, 0, report.to_dict())
        self.assertFalse((self.root / "src/future_adapter.py").exists())
        self.assertIn("| 基线状态 | Draft |", upstream.read_text(encoding="utf-8"))

    def test_declared_key_sections_reject_empty_or_comment_only_content(self) -> None:
        for heading in ("核心目标", "方案承接与实施设计", "输入与交付", "完成判断"):
            section = VALIDATOR.extract_section(SUBTASK, "## " + heading)
            assert section is not None
            content = SUBTASK.replace(section, "\n<!-- 编写提示不算交接内容 -->\n", 1)
            with self.subTest(heading=heading):
                self.assertGreater(self.report(content).errors, 0)

    def test_declared_key_sections_reject_subheadings_without_body(self) -> None:
        # Regressions from the parameter audit: headings used to count as prose.
        for heading, body in (
            ("核心目标", "### 目标事实"),
            ("方案承接与实施设计", "### 机制\n### 取舍"),
            ("输入与交付", "### 输入\n### 交付"),
            ("完成判断", "### 成功\n### 失败\n### 直接证据\n### 停止线"),
        ):
            with self.subTest(heading=heading):
                report = self.report(self.replace_section(SUBTASK, heading, body))
                self.assertGreater(report.errors, 0, report.to_dict())

    def test_declared_key_sections_reject_tables_without_values(self) -> None:
        for body in (
            "| 项目 | 内容 |\n|---|---|",
            "| 项目 | 内容 |\n|---|---|\n| 输入 | |\n| 交付 | |",
        ):
            for heading in ("核心目标", "方案承接与实施设计", "输入与交付", "完成判断"):
                with self.subTest(heading=heading, body=body):
                    report = self.report(self.replace_section(SUBTASK, heading, body))
                    self.assertGreater(report.errors, 0, report.to_dict())

    def test_optional_design_and_input_sections_need_not_be_added_to_old_tasks(self) -> None:
        for headings in (
            ("方案承接与实施设计",),
            ("输入与交付",),
            ("方案承接与实施设计", "输入与交付"),
        ):
            content = SUBTASK
            for heading in headings:
                content = self.remove_section(content, heading)
            with self.subTest(headings=headings):
                report = self.report(content)
                self.assertEqual(report.errors, 0, report.to_dict())

    def test_existing_required_sections_remain_required(self) -> None:
        for heading in ("Meta", "核心目标", "上下文构建协议", "完成判断", "执行日志"):
            with self.subTest(heading=heading):
                self.assertGreater(self.report(self.remove_section(SUBTASK, heading)).errors, 0)

    def test_current_progress_placeholders_and_empty_content_fail(self) -> None:
        for body in (
            "- 当前事实：{直接影响行动选择的已知事实}\n- 当前推进动作：{此刻最应该先做的推进动作}",
            "<!-- 当前推进尚未补写 -->",
            "### 当前事实\n### 当前动作",
            "| 字段 | 值 |\n|---|---|\n| 当前事实 | |\n| 当前推进动作 | |",
        ):
            content = SUBTASK.replace("## 执行日志", "## 当前推进\n" + body + "\n## 执行日志")
            with self.subTest(body=body):
                report = self.report(content)
                self.assertGreater(report.errors, 0, report.to_dict())

    def test_known_placeholder_in_other_active_prose_is_not_silently_skipped(self) -> None:
        content = SUBTASK.replace("## 执行日志", "## 后续接入注意事项\n{此刻最应该先做的推进动作}\n## 执行日志")
        self.assertGreater(self.report(content).errors, 0)

    def test_historical_notes_and_code_examples_do_not_count_as_active_placeholders(self) -> None:
        for history_heading in ("执行日志", "历史记录", "变更历史"):
            with self.subTest(history_heading=history_heading):
                content = SUBTASK.replace(
                    "## 执行日志\n计划待进入条件就绪。",
                    "## 执行日志\n计划待进入条件就绪。\n"
                    + ("" if history_heading == "执行日志" else "## " + history_heading + "\n")
                    + "过去草稿使用 {核心目标} 和 {方案承接与实施设计}。\n### 首次记录\nTODO",
                )
                content = content.replace("## 输入与交付", "```markdown\n{方案承接与实施设计}\nTODO\n```\n示例中的 `{核心目标}` 按字面展示。\n## 输入与交付")
                report = self.report(content)
                self.assertEqual(report.errors, 0, report.to_dict())

    def test_active_section_after_history_still_checks_placeholders(self) -> None:
        content = SUBTASK + "\n## 当前推进\n{此刻最应该先做的推进动作}\n"
        self.assertGreater(self.report(content).errors, 0)

    def test_legacy_template_guidance_is_not_goal_or_acceptance_content(self) -> None:
        goal_hint = "> 本 Subtask 的所有动作都必须推动这个目标、消除会改变路线的未知，或降低已点名的实质风险。"
        content = SUBTASK.replace("恢复读取返回同一次操作的结果。", goal_hint)
        report = self.report(content)
        self.assertTrue(any("内容为空" in message.message and "核心目标" in message.message for message in report.messages))

        state_hint = "> 状态记录已有授权和完成事实。会话已明确授权时，执行者可据此记录 `Frozen`；旧的 `Draft` 标记不要求重复征求同一授权。状态更新不产生或扩大权限，也不把原因假设变成已验证事实。直接证据与停止线成立后记录 `Verified`。"
        content = self.replace_section(SUBTASK, "完成判断", state_hint + "\n<!-- 只剩模板提示，没有任务验收内容 -->")
        report = self.report(content)
        self.assertTrue(any("内容为空" in message.message and "完成判断" in message.message for message in report.messages))

        # A task's own quoted requirements remain ordinary task content.
        content = SUBTASK.replace("恢复读取返回同一次操作的结果。", "> 恢复读取必须保持原操作身份。")
        self.assertEqual(self.report(content).errors, 0)

    def test_ordinary_braces_and_bounded_unknowns_are_not_placeholders(self) -> None:
        content = SUBTASK.replace(
            "按 contract.md 的结果语义复用现有 observer；新请求和原关联观察分别处理。",
            "集合 {a, b} 中的 {x} 仅表示候选身份。\n- 关键未知：待定\n  S002 提供 observer 后由本任务检查字段映射，未确认前不接入。\n- 协议说明：TBD 是上游原有枚举值，原样保留。",
        )
        self.assertEqual(self.report(content).errors, 0)

    def test_known_template_tokens_and_empty_markers_still_fail(self) -> None:
        for marker in ("{方案承接与实施设计}", "TODO", "TBD", "待填写", "待补充"):
            content = SUBTASK.replace("按 contract.md 的结果语义复用现有 observer；新请求和原关联观察分别处理。", "- 实施设计：" + marker)
            with self.subTest(marker=marker):
                self.assertTrue(any("模板占位符" in message.message for message in self.report(content).messages))

    def test_blank_input_and_output_values_do_not_count_as_content(self) -> None:
        section = VALIDATOR.extract_section(SUBTASK, "## 输入与交付")
        assert section is not None
        content = SUBTASK.replace(section, "\n- 输入/进入条件：\n- 交付/下游：\n", 1)
        self.assertGreaterEqual(self.report(content).errors, 2)

    def test_old_effect_and_action_sections_are_valid_without_heading_migration(self) -> None:
        content = SUBTASK.replace(
            "## 方案承接与实施设计\n按 contract.md 的结果语义复用现有 observer；新请求和原关联观察分别处理。",
            "## 预期效果\n原作用不因连接中断重复执行。\n## 行动模型\n- 当前事实：observer 已定义原关联读取。\n- 当前推进动作：在调用适配层复用 observer 并覆盖丢失回执。",
        )
        self.assertEqual(self.report(content).errors, 0)
        self.assertGreater(self.report(content.replace("原作用不因连接中断重复执行。", "{预期效果}")).errors, 0)

    def test_existing_implementation_design_and_acceptance_aliases_are_valid(self) -> None:
        content = SUBTASK.replace("## 方案承接与实施设计", "## 职责与拟实施范围")
        content = content.replace("## 核心目标", "## 目标").replace("## 完成判断", "## 检查点")
        content = content.replace("- 直接证据：", "- 验收入口：")
        self.assertEqual(self.report(content).errors, 0)

    def test_missing_recommended_labels_do_not_prove_missing_acceptance_content(self) -> None:
        for target in ("直接证据", "停止线"):
            content = "\n".join(line for line in SUBTASK.splitlines() if not line.startswith("- " + target)) + "\n"
            with self.subTest(target=target):
                report = self.report(content)
                self.assertEqual(report.errors, 0, report.to_dict())

    def test_equivalent_acceptance_headings_tables_and_natural_prose_pass(self) -> None:
        bodies = (
            "### 成功\n中断后的查询返回同一操作，写入次数一次。\n### 失败\n未知保持未知，不重发。\n### 直接证据\n运行 tests/recovery.py 的中断后读取用例。\n### 停止线\n原关联恢复及失败反例通过后交给调用层。",
            "| 场景 | 条件及判定 |\n|---|---|\n| 成功 | 中断后读取同一操作且写入次数一次 |\n| 失败 | 未知保持未知不重发 |\n| 直接证据 | tests/recovery.py 中断后读取用例 |\n| 停止线 | 上述用例通过后交给调用层 |",
            "运行 tests/recovery.py：中断后仍读到同一操作且写入一次；未能确认原结果时保持未知并禁止重发。上述反例通过后交给调用层，停止扩张其它业务。",
        )
        for body in bodies:
            with self.subTest(body=body):
                report = self.report(self.replace_section(SUBTASK, "完成判断", body))
                self.assertEqual(report.errors, 0, report.to_dict())

    def test_ambiguous_acceptance_prose_is_left_to_planner_judgment(self) -> None:
        body = "读完现有中断恢复记录后，将调用层的直接证据与现存结果合同逐项核对，按调用层既有收口约定交付。"
        report = self.report(self.replace_section(SUBTASK, "完成判断", body))
        self.assertEqual(report.errors, 0, report.to_dict())

    def test_design_correctness_is_not_claimed_by_mechanical_validation(self) -> None:
        # The audit intentionally supplied a design contradicting its acceptance.
        # Recognizing that contradiction belongs to the planner's content review.
        content = self.replace_section(SUBTASK, "方案承接与实施设计", "收到未知结果后直接重发业务写入；不读取原作用 observer。")
        self.assertEqual(self.report(content).errors, 0)

    def test_evidence_and_stop_need_no_independent_scenario_paragraphs(self) -> None:
        content = "\n".join(line for line in SUBTASK.splitlines() if not line.startswith(("- 成功：", "- 失败："))) + "\n"
        report = self.report(content)
        self.assertEqual(report.errors, 0, report.to_dict())

    def test_evidence_and_stop_headings_or_tables_are_not_judged_for_semantic_quality(self) -> None:
        for body in (
            "### 直接证据\n运行 tests/recovery.py。\n### 停止线\n通过后交给调用层。",
            "| 项目 | 内容 |\n|---|---|\n| 直接证据 | tests/recovery.py |\n| 停止线 | 通过后交给调用层 |",
        ):
            with self.subTest(body=body):
                report = self.report(self.replace_section(SUBTASK, "完成判断", body))
                self.assertEqual(report.errors, 0, report.to_dict())

    def test_evidence_and_stop_accept_indented_continuations_without_extra_scenario_labels(self) -> None:
        content = SUBTASK.replace("- 直接证据：运行", "- 直接证据：\n  运行")
        content = content.replace("- 停止线：原关联", "- 停止线：\n  原关联")
        self.assertEqual(self.report(content).errors, 0)
        content = "\n".join(line for line in content.splitlines() if not line.startswith(("- 成功：", "- 失败："))) + "\n"
        report = self.report(content)
        self.assertEqual(report.errors, 0, report.to_dict())

    def test_complete_native_and_plugin_init_scenarios_may_live_inside_evidence_field(self) -> None:
        body = (
            "- 直接证据：针对同一 DLL 分别执行 native-init 与 plugin-init：缺失 native-init 导出时返回明确的缺失诊断，"
            "不进入未初始化调用；plugin-init 正常导出时解析并调用对应入口，核对返回的插件身份和端口结构。"
            "使用同一 DLL 夹具记录两条路径的实际结果，避免由另一个 DLL 的成功掩盖错误解析。\n"
            "- 停止线：同 DLL 的缺失入口反例与 plugin-init 解析结果均符合上述合同后交付，停止扩张其他加载器。"
        )
        report = self.report(self.replace_section(SUBTASK, "完成判断", body))
        self.assertEqual(report.errors, 0, report.to_dict())

    def test_declared_acceptance_fields_still_reject_definitely_empty_values(self) -> None:
        for body in (
            "- 成功：中断后读取同一操作且写入一次。\n- 直接证据：\n- 停止线：上述用例通过后交付。",
            "### 成功\n中断后读取同一操作且写入一次。\n### 直接证据\n<!-- 待填 -->\n### 停止线\n上述用例通过后交付。",
            "| 项目 | 内容 |\n|---|---|\n| 成功 | 中断后读取同一操作且写入一次 |\n| 直接证据 | |\n| 停止线 | 上述用例通过后交付 |",
        ):
            with self.subTest(body=body):
                report = self.report(self.replace_section(SUBTASK, "完成判断", body))
                self.assertGreater(report.errors, 0, report.to_dict())
                self.assertTrue(any("字段为空" in message.message and "直接证据" in message.message for message in report.messages))

    def test_explicit_section_must_exist_and_be_unambiguous(self) -> None:
        content = SUBTASK.replace('section="结果语义"', 'section="不存在"')
        self.assertGreater(self.report(content).errors, 0)
        self.write(self.root / "contract.md", "# 合同\n## 结果语义\nA\n## 结果语义\nB\n")
        self.assertGreater(self.report().errors, 0)

    def test_fenced_heading_is_not_a_section_and_optional_failure_is_advisory(self) -> None:
        self.write(self.root / "contract.md", "# 合同\n```md\n## 结果语义\n```\n")
        self.assertGreater(self.report().errors, 0)
        content = SUBTASK.replace('priority="P0"', 'priority="P1"').replace('required="true"', 'required="false"')
        report = self.report(content)
        self.assertEqual(report.errors, 0)
        self.assertGreater(report.warnings, 0)

    def test_literal_json_in_design_is_not_a_template_placeholder(self) -> None:
        content = SUBTASK.replace("按 contract.md 的结果语义复用现有 observer；新请求和原关联观察分别处理。", '请求示例：\n```json\n{"action": "read", "operation": "{id}"}\n```')
        self.assertEqual(self.report(content).errors, 0)

    def test_labeled_design_can_use_fenced_code_as_its_value(self) -> None:
        for indentation in ("", "  "):
            body = '- 接口例子：\n' + "\n".join(
                indentation + line for line in ('```json', '{"id": "{x}", "example": "{核心目标}"}', '```')
            )
            with self.subTest(indentation=indentation):
                report = self.report(self.replace_section(SUBTASK, "方案承接与实施设计", body))
                self.assertEqual(report.errors, 0, report.to_dict())

    def test_acceptance_labels_accept_plain_paragraph_and_code_values(self) -> None:
        for continuation in (
            "运行将新增的 tests/recovery.py，核对操作身份和写入次数。",
            "```text\npython tests/recovery.py --verify-original-operation\n```",
            "  ```text\n  python tests/recovery.py --verify-original-operation\n  ```",
        ):
            content = SUBTASK.replace(
                "- 直接证据：运行将新增的 tests/recovery.py 中断后读取用例，核对操作身份与写入次数。",
                "- 直接证据：\n" + continuation,
            )
            with self.subTest(continuation=continuation):
                report = self.report(content)
                self.assertEqual(report.errors, 0, report.to_dict())

    def test_epic_checks_explicit_responsibility_ids_by_default(self) -> None:
        self.assertEqual(VALIDATOR.validate_epic_dir(self.epic).errors, 0)
        table = """\n## 关键要求与验收责任
| 关键要求及来源 | 能力实现 | 后续保持 | 整体验收（承担任务与直接证据） |
|---|---|---|---|
| 来源 E123/S999 | S001 | E123/S998 | S404，运行读取用例 |
"""
        self.write(self.epic / "README.md", EPIC + table)
        report = VALIDATOR.validate_epic_dir(self.epic)
        errors = [message.message for message in report.messages if message.level == "error"]
        self.assertEqual(len(errors), 1, report.to_dict())
        self.assertIn("S404", errors[0])

    def test_responsibility_lists_and_tables_need_no_fixed_column_names(self) -> None:
        for body in (
            "- 原关联读取由 S001 实现，E123/S998 保持，S404 整体验收。",
            "| 需求 | 执行者 | 校验者 |\n|---|---|---|\n| 原关联读取 | S001 | S404 |",
        ):
            with self.subTest(body=body):
                self.write(self.epic / "README.md", EPIC + "\n## 关键要求与验收责任\n" + body + "\n")
                report = VALIDATOR.validate_epic_readme(self.epic / "README.md")
                errors = [message.message for message in report.messages if message.level == "error"]
                self.assertEqual(len(errors), 1, report.to_dict())
                self.assertIn("S404", errors[0])

    def test_external_responsibility_ids_do_not_become_local_dependencies(self) -> None:
        body = "\n## 关键要求与验收责任\n- S001 实现与验收；E123/S404 提供上游能力，E123／S405 负责后续保持。\n"
        self.write(self.epic / "README.md", EPIC + body)
        report = VALIDATOR.validate_epic_readme(self.epic / "README.md")
        self.assertEqual(report.errors, 0, report.to_dict())

    def test_current_epic_qualified_responsibility_id_is_local(self) -> None:
        body = "\n## 关键要求与验收责任\n- E999/S404 负责整体验收。\n"
        self.write(self.epic / "README.md", EPIC + body)
        report = VALIDATOR.validate_epic_readme(self.epic / "README.md")
        errors = [message.message for message in report.messages if message.level == "error"]
        self.assertEqual(len(errors), 1, report.to_dict())
        self.assertIn("S404", errors[0])

    def test_epic_goal_and_state_placeholders_fail_by_default(self) -> None:
        for heading, token in (
            ("核心目标", "{描述完成后应成立的可观察产品、工程或决策事实}"),
            ("当前状态", "{当前事实、症状和为什么现在值得推进}"),
        ):
            with self.subTest(heading=heading):
                self.write(self.epic / "README.md", self.replace_section(EPIC, heading, token))
                report = VALIDATOR.validate_epic_readme(self.epic / "README.md")
                self.assertGreater(report.errors, 0, report.to_dict())
                self.assertTrue(any("模板占位符" in message.message for message in report.messages))

    def test_epic_declared_goal_state_and_shared_design_must_have_content(self) -> None:
        for heading in ("核心目标", "当前状态", "共享设计与取舍"):
            for body in ("<!-- later -->", "### 内容", "| 项目 | 内容 |\n|---|---|"):
                content = EPIC + "\n## 共享设计与取舍\n复用已有 observer，保持调用身份。\n"
                with self.subTest(heading=heading, body=body):
                    self.write(self.epic / "README.md", self.replace_section(content, heading, body))
                    report = VALIDATOR.validate_epic_readme(self.epic / "README.md")
                    self.assertGreater(report.errors, 0, report.to_dict())

    def test_execution_plan_shared_design_placeholders_fail_by_default(self) -> None:
        path = self.epic / "plan" / "execution_plan.md"
        self.write(path, "# 执行方案\n## 共享设计与取舍\n{共享设计与取舍}\n")
        for check, target in ((VALIDATOR.validate_single_path, path), (VALIDATOR.validate_epic_dir, self.epic)):
            with self.subTest(check=check.__name__):
                report = check(target)
                self.assertGreater(report.errors, 0, report.to_dict())
                self.assertTrue(any("模板占位符" in message.message for message in report.messages))

    def test_execution_plan_shared_design_empty_shells_fail_by_default(self) -> None:
        path = self.epic / "plan" / "execution_plan.md"
        for body in ("<!-- 尚无设计内容 -->", "### 已定合同\n### 实施方案", "| 项目 | 内容 |\n|---|---|"):
            with self.subTest(body=body):
                self.write(path, "# 执行方案\n## 共享设计与取舍\n" + body + "\n")
                report = VALIDATOR.validate_execution_plan(path)
                self.assertGreater(report.errors, 0, report.to_dict())

    def test_execution_plan_shared_design_with_real_content_passes(self) -> None:
        path = self.epic / "plan" / "execution_plan.md"
        self.write(path, "# 执行方案\n## 共享设计与取舍\n复用原 observer 保持操作身份；示例 `{核心目标}` 仅为字面文本。\n")
        report = VALIDATOR.validate_single_path(path)
        self.assertEqual(report.errors, 0, report.to_dict())

    def test_json_cli_checks_task_content_by_default_and_readme_checks_only_itself(self) -> None:
        content = SUBTASK.replace("恢复读取返回同一次操作的结果。", "{核心目标}")
        self.write(self.task, content)
        for target, expected in ((self.task, 1), (self.epic, 1), (self.epic / "README.md", 0)):
            stream = io.StringIO()
            with self.subTest(target=target), mock.patch.object(sys, "argv", ["validate_doc_llm.py", "--json", str(target)]), contextlib.redirect_stdout(stream):
                self.assertEqual(VALIDATOR.main(), expected)
            self.assertEqual(json.loads(stream.getvalue())["summary"]["error"] > 0, expected > 0)

    def test_readme_single_file_does_not_traverse_subtask_or_execution_plan(self) -> None:
        self.write(self.task, "# broken child\n")
        self.write(self.epic / "plan" / "execution_plan.md", "# plan\n## 上下文构建协议\n<broken>\n")
        for check in (VALIDATOR.validate_epic_readme, VALIDATOR.validate_single_path):
            with self.subTest(check=check.__name__):
                report = check(self.epic / "README.md")
                self.assertEqual(report.errors, 0, report.to_dict())
        self.assertGreater(VALIDATOR.validate_epic_dir(self.epic).errors, 0)

    def test_json_cli_checks_epic_readme_content_itself(self) -> None:
        self.write(self.epic / "README.md", self.replace_section(EPIC, "核心目标", "{核心目标}"))
        stream = io.StringIO()
        with mock.patch.object(sys, "argv", ["validate_doc_llm.py", "--json", str(self.epic / "README.md")]), contextlib.redirect_stdout(stream):
            self.assertEqual(VALIDATOR.main(), 1)
        self.assertGreater(json.loads(stream.getvalue())["summary"]["error"], 0)

    def test_removed_handoff_cli_switch_is_an_argument_error(self) -> None:
        stream = io.StringIO()
        with mock.patch.object(sys, "argv", ["validate_doc_llm.py", "--json", "--handoff", str(self.task)]), contextlib.redirect_stdout(stream):
            self.assertEqual(VALIDATOR.main(), 2)


if __name__ == "__main__":
    unittest.main()
