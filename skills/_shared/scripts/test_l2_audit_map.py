#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().with_name("l2_audit_map.py")
SPEC = importlib.util.spec_from_file_location("l2_audit_map_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
L2_AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = L2_AUDIT
SPEC.loader.exec_module(L2_AUDIT)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def l2_doc(module: str, body: str) -> str:
    return f"""---
domain: "9_fixture"
module: "{module}"
match_globs:
  - "src/{module}/**/*"
status: "已实现"
last_updated: "2026-01-01"
---

# {module}

{body}
"""


class L2AuditMapTests(unittest.TestCase):
    def test_analyze_doc_flags_journal_like_l2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            L2_AUDIT.PROJECT_ROOT = root
            path = root / "doc_llm/Domains/9_fixture/L2_Modules/journal.md"
            write_text(
                path,
                l2_doc(
                    "journal",
                    """
## 演进记录

### 2026-01-01 Session 1
- **完成**: 本轮完成若干调整。
- **发现**: 这次发现一个问题。
- **下次**: 下次继续处理。

### 2026-01-02 Session 2
- **完成**: 本轮继续调整。
- **发现**: 仍待确认。
- **沉淀**: 已记录。

### 2026-01-03 Session 3
- **完成**: 本次又做了调整。
- **下次**: 继续。
""",
                ),
            )

            doc = L2_AUDIT.analyze_doc(path)
            codes = {signal["code"] for signal in doc.signals}

            self.assertIn("journal_like", codes)
            self.assertGreaterEqual(doc.attention_score, 3)

    def test_payload_reports_overlap_candidates_without_deciding_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            L2_AUDIT.PROJECT_ROOT = root
            first = root / "doc_llm/Domains/9_fixture/L2_Modules/first.md"
            second = root / "doc_llm/Domains/9_fixture/L2_Modules/second.md"
            body = """
## 关键约束

- 生命周期边界必须清晰。
- 状态机切换必须保持所有权一致。
- 数据流入口必须通过稳定契约。
- 失败路径需要记录根因和影响。

## 已知陷阱

| 陷阱 | 现象 | 根因 | 解决方案 | 发现日期 |
|------|------|------|----------|---------|
| owner-boundary | 所有权混乱 | 生命周期跨界 | 收敛边界 | 2026-01-01 |
"""
            write_text(first, l2_doc("first", body))
            write_text(second, l2_doc("second", body.replace("owner-boundary", "state-boundary")))

            class Args:
                target = "doc_llm"
                min_similarity = 0.4
                max_pairs = 10
                max_candidates = 10
                no_overlap = False
                include_all = False
                json = True

            old_cwd = Path.cwd()
            try:
                # build_payload resolves relative targets against PROJECT_ROOT, not cwd.
                payload = L2_AUDIT.build_payload(Args())
            finally:
                self.assertEqual(old_cwd, Path.cwd())

            self.assertEqual(payload["summary"]["l2_files"], 2)
            self.assertEqual(payload["summary"]["overlap_pairs"], 1)
            self.assertIn("facts and candidates only", payload["guardrails"][0])

    def test_resolve_target_rejects_path_outside_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            L2_AUDIT.PROJECT_ROOT = root
            outside = root.parent / "outside_l2_audit.md"
            write_text(outside, "# outside\n")
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as ctx:
                        L2_AUDIT.resolve_target(str(outside))
                self.assertEqual(ctx.exception.code, 2)
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
