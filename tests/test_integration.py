"""Exercise the installed pack in disposable repositories with no source-host dependency."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "skills"


class Integration(unittest.TestCase):
    def setUp(self) -> None:
        cache = ROOT / "out" / "tests"
        cache.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="consumer-", dir=cache)
        self.addCleanup(self.temporary.cleanup)
        self.host = Path(self.temporary.name) / "示例 host"
        self.host.mkdir()
        self.env = dict(os.environ)
        for key in ("DLM_PROJECT_ROOT", "DLM_DOC_LLM_DIR", "DLM_SKILL_ENTRYPOINT", "PYTHONPATH"):
            self.env.pop(key, None)
        self.env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")

    def run_cli(self, script: Path, *args: str, code: int = 0, cwd: Path | None = None) -> str:
        result = subprocess.run([sys.executable, "-X", "utf8", str(script), *args],
                                cwd=cwd or self.host, env=self.env, capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, code, output)
        return output

    def install(self, *args: str, code: int = 0) -> str:
        return self.run_cli(ROOT / "install.py", "--host", str(self.host), *args, code=code)

    def dlm(self, *args: str, code: int = 0, cwd: Path | None = None, skill: str = "dlm-task") -> str:
        return self.run_cli(self.host / ".agents/skills" / skill / "scripts/dlm.py",
                            *args, code=code, cwd=cwd)

    def snapshot(self) -> dict[str, bytes]:
        return {p.relative_to(self.host).as_posix(): p.read_bytes()
                for p in self.host.rglob("*") if p.is_file()}

    def test_install_is_complete_idempotent_and_independent(self) -> None:
        (self.host / "AGENTS.md").write_text("Host authorization rules\n", encoding="utf-8")
        before = self.snapshot()
        self.install("--dry-run")
        self.assertEqual(before, self.snapshot())
        self.install()
        for source in PACK.rglob("*"):
            if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
                self.assertEqual(source.read_bytes(),
                                 (self.host / ".agents/skills" / source.relative_to(PACK)).read_bytes())
        installed = self.snapshot()
        self.install()
        self.install("--check")
        self.assertEqual(installed, self.snapshot())
        self.assertEqual(before["AGENTS.md"], installed["AGENTS.md"])
        for skill in ("dlm-task", "dlm-load", "dlm-review", "dlm-knowledge", "dlm-multi-llm-lease"):
            self.dlm("--help", skill=skill)
        # Only installed paths are used; no reference to the checkout is recorded.
        self.assertNotIn(str(ROOT), (self.host / ".agents/dlm-install.json").read_text(encoding="utf-8"))
        nested = self.host / "src/nested"
        nested.mkdir(parents=True)
        self.dlm("kb", "scaffold", "init-project", "独立使用验证", cwd=nested)
        self.assertTrue((self.host / "docs-llm/BRIEF.md").is_file())

    def test_modified_install_is_rejected_before_any_write(self) -> None:
        self.install()
        changed = self.host / ".agents/skills/dlm-task/SKILL.md"
        changed.write_text("Host customized this skill", encoding="utf-8")
        (self.host / ".agents/skills/_shared/locales/en.json").unlink()
        before = self.snapshot()
        self.assertIn("No files written", self.install(code=1))
        self.assertEqual(before, self.snapshot())
        self.install("--check", code=1)

    def test_unmanaged_conflict_is_rejected_without_partial_install(self) -> None:
        conflict = self.host / ".agents/skills/dlm-task/SKILL.md"
        conflict.parent.mkdir(parents=True)
        conflict.write_text("Existing unrelated skill", encoding="utf-8")
        before = self.snapshot()
        self.install(code=1)
        self.assertEqual(before, self.snapshot())

    def test_identical_legacy_copy_can_be_adopted_and_unrelated_files_survive(self) -> None:
        shared = self.host / ".agents/skills/_shared"
        shutil.copytree(PACK / "_shared", shared, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (shared / "other-tool.txt").write_text("host-owned", encoding="utf-8")
        self.install()
        self.install("--check")
        self.assertEqual("host-owned", (shared / "other-tool.txt").read_text(encoding="utf-8"))

    def test_managed_update_replaces_old_bytes_and_removes_only_owned_files(self) -> None:
        self.install()
        manifest_file = self.host / ".agents/dlm-install.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        for relative in ("dlm-load/SKILL.md", "dlm-load/obsolete.txt"):
            path = self.host / ".agents/skills" / relative
            path.write_bytes(b"old managed version\n")
            manifest["files"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
        self.install("--check", code=1)
        self.install()
        self.install("--check")
        self.assertFalse((self.host / ".agents/skills/dlm-load/obsolete.txt").exists())

    def test_invalid_manifest_cannot_escape_install_directory(self) -> None:
        self.install()
        manifest_file = self.host / ".agents/dlm-install.json"
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest["files"]["dlm-task/../../../outside.txt"] = "0" * 64
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
        before = self.snapshot()
        self.install(code=1)
        self.assertEqual(before, self.snapshot())

    def test_existing_directory_and_config_are_preserved(self) -> None:
        for name in ("doc_llm", "docs_llm", "custom/knowledge"):
            with self.subTest(name=name):
                self.host = Path(self.temporary.name) / name.replace("/", "-")
                self.host.mkdir()
                (self.host / name).mkdir(parents=True)
                if name == "custom/knowledge":
                    config = {"knowledge_dir": name, "tasks_dir": "planning/tasks",
                              "cache_dir": "cache", "policy": "custom.policy.json", "locale": "zh-CN"}
                    (self.host / "dlm.config.json").write_text(json.dumps(config), encoding="utf-8")
                    (self.host / "custom.policy.json").write_text('{"public_layers": ["public/**"]}', encoding="utf-8")
                before = self.snapshot()
                self.install()
                for relative, content in before.items():
                    self.assertEqual(content, (self.host / relative).read_bytes())
                self.dlm("kb", "scaffold", "init-project", "目录兼容验证")
                self.assertTrue((self.host / name / "BRIEF.md").exists())
                self.assertFalse((self.host / "docs-llm").exists())

    def test_direct_checkout_entry_supports_an_explicit_empty_host(self) -> None:
        self.run_cli(ROOT / "dlm.py", "--root", str(self.host), "kb", "scaffold", "init-project", "直接调用")
        self.assertTrue((self.host / "docs-llm/BRIEF.md").exists())
        self.assertFalse((self.host / ".agents").exists())
        self.run_cli(ROOT / "dlm.py", "--root", str(self.host / "missing"), "check", code=2)

    def test_installed_task_and_knowledge_closed_loop(self) -> None:
        self.install()
        subprocess.run(["git", "init", "--quiet", str(self.host)], check=True, capture_output=True)
        (self.host / "src").mkdir()
        for name in ("a", "b"):
            (self.host / f"src/{name}.txt").write_text(name, encoding="utf-8")
        self.dlm("kb", "scaffold", "init-project", "闭环验证")
        self.dlm("kb", "scaffold", "init-domain", "1_core", "--domain-title", "核心", "--root-path", "src")
        self.dlm("kb", "scaffold", "create-l2", "1_core", "letters", "--module-title", "字母转换",
                 "--match-glob", "src/*.txt")
        self.dlm("kb", "scaffold", "create-reference", "1_core", "format", "--topic-title", "输入格式")
        self.dlm("kb", "scaffold", "create-sop", "convert", "--sop-title", "字母转换流程")
        router = self.host / "docs-llm/Domains/1_core/L1_core_router.md"
        before = router.read_bytes()
        self.dlm("kb", "route", "--all", "--preview")
        self.assertEqual(before, router.read_bytes())
        self.dlm("kb", "route", "--all")
        self.assertIn("L2_Modules/letters.md", router.read_text(encoding="utf-8"))
        self.assertIn("references/format/README.md", router.read_text(encoding="utf-8"))
        self.dlm("kb", "check", "docs-llm/00_Global", "docs-llm/Domains/1_core")
        mapping = json.loads(self.dlm("kb", "map", "--json"))
        self.assertEqual(mapping["summary"]["l2_files"], 1)
        self.assertIn("letters.md", self.dlm("kb", "sediment", "src/a.txt"))
        self.dlm("kb", "stale")

        create = ("epic", "create", "字母大写转换", "--outcome", "两份输入均输出大写字母",
                  "--acceptance", "验证两份输入输出", "--principle", "逐项依据结果验证",
                  "--confirmation", "测试夹具中的模拟用户：确认该原则并创建 Epic", "--write", "src/**")
        self.dlm("--dry-run", *create)
        self.assertFalse((self.host / "docs-llm/Tasks").exists())
        self.dlm(*create)
        self.dlm("inv", "new", "letters", "--epic", "E001", "--reference", "源文件全集")
        self.dlm("inv", "source", "letters", "--epic", "E001", "--side", "ref",
                 "--kind", "glob", "--spec", "src/*.txt")
        self.dlm("task", "create", "E001", "逐项转换并验证", "--depth", "Medium", "--depth-reason", "闭环集成验证",
                 "--oblige", "全集两份文件均正确", "--origin", "D1",
                 "--coverage", "full", "--population", "N1", "--write", "src/**")
        self.dlm("plan-check", "E001")
        self.dlm("task", "start", "E001", "S001")
        self.assertIn("逐项转换并验证", self.dlm("brief", "E001", "S001", skill="dlm-load"))
        self.dlm("checkpoint", "E001", "S001", "--verdict", "pending", "--note", "等待实际转换与验证")
        review = ("--line", "P1=逐项检查输出与输入的转换结果", "--line", "O2=全集两份文件均已校验")
        self.dlm("close", "E001", "S001", *review, code=1)
        for name in ("a", "b"):
            path = self.host / f"src/{name}.txt"
            path.write_text(path.read_text(encoding="utf-8").upper(), encoding="utf-8")
            self.assertEqual(name.upper(), path.read_text(encoding="utf-8"))
        (self.host / "out").mkdir(exist_ok=True)
        (self.host / "out/results.txt").write_text("a -> A\nb -> B\n", encoding="utf-8")
        evidence = ("evidence", "E001", "--scope", "full", "--locator", "out/results.txt",
                    "--obligations", "O1,O2", "--items", "N1.1,N1.2", "--item-status", "pass",
                    "--task", "S001", "--answers", "全部源文件的输出是否正确")
        self.dlm(*evidence)
        self.dlm("inv", "report", "N1", "--epic", "E001", "--gate")
        self.dlm("checkpoint", "E001", "S001", "--verdict", "aligned")
        self.dlm("close", "E001", "S001", *review)
        # An omission must reopen the task and invalidate the earlier completion proof.
        self.dlm("reopen", "E001", "O2", "--cause", "omission", "--note", "测试重开并重新验证")
        self.dlm("close", "E001", "S001", *review, code=1)
        self.dlm(*evidence)
        self.dlm("checkpoint", "E001", "S001", "--verdict", "aligned")
        self.dlm("close", "E001", "S001", *review)
        self.dlm("epic", "close", "E001")
        self.dlm("check", "E001", skill="dlm-review")
        ledger_path = next((self.host / "docs-llm/Tasks").glob("E001_*/ledger.json"))
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(ledger["epic"]["status"], "done")
        # Knowledge write-back stays a real editable host document and can be routed again.
        l2 = self.host / "docs-llm/Domains/1_core/L2_Modules/letters.md"
        l2.write_text(l2.read_text(encoding="utf-8").split("\n# ", 1)[0]
                      + "\n# 字母转换\n\n输入为单个小写字母，输出对应大写字母。"
                        "测试使用全部两份输入逐项验证输出，任务证据记录在各自 Epic 内。\n", encoding="utf-8")
        self.dlm("kb", "route", "--all")
        self.dlm("kb", "check", "docs-llm/Domains/1_core", skill="dlm-knowledge")


if __name__ == "__main__":
    unittest.main()
