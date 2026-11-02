#!/usr/bin/env python3
"""Behavioural tests for the DLM v2 core: every rule has a must-reject and a
must-accept case, plus replays of historical failure patterns."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import dlm  # noqa: E402
from dlm_config import load_host  # noqa: E402
from dlm_model import Ledger  # noqa: E402
from dlm_status import obligation_status  # noqa: E402


class Host(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="dlm_test_"))
        (self.root / "doc_llm").mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "a.txt").write_text("x", encoding="utf-8")
        (self.root / "out").mkdir()
        for name in ("x", "f.log", "compile.log", "core.log", "all-tests-pass.log", "sample-run.log"):
            (self.root / "out" / name).write_text("evidence", encoding="utf-8")
        self._env = os.environ.get("DLM_PROJECT_ROOT")
        os.environ["DLM_PROJECT_ROOT"] = str(self.root)

    def tearDown(self) -> None:
        if self._env is None:
            os.environ.pop("DLM_PROJECT_ROOT", None)
        else:
            os.environ["DLM_PROJECT_ROOT"] = self._env
        shutil.rmtree(self.root, ignore_errors=True)

    def run_dlm(self, *argv: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dlm.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def ok(self, *argv: str) -> str:
        code, text = self.run_dlm(*argv)
        self.assertEqual(code, 0, text)
        return text

    def fails(self, *argv: str, needle: str | None = None) -> str:
        code, text = self.run_dlm(*argv)
        self.assertNotEqual(code, 0, text)
        if needle:
            self.assertIn(needle, text)
        return text

    def epic(self, *extra: str) -> None:
        self.ok("epic", "create", "Demo", "--outcome", "demo works for users", "--acceptance", "demo accepted",
                "--principle", "real behaviour over proxies", "--confirmation", "用户：确认这组原则并创建",
                "--write", "src/**", *extra)

    def ledger(self) -> Ledger:
        return Ledger.load(load_host(), "E001")

    def task(self, title: str = "T", *extra: str) -> None:
        source = [] if ("--origin" in extra or "--derives" in extra or "--oblige" not in extra) else ["--derives", "O1"]
        self.ok("task", "create", "E001", title, "--depth", "High", "--depth-reason", "reason", "--write", "src/**",
                *source, *extra)


class EpicCreation(Host):
    def test_requires_confirmation_principles_outcome_acceptance(self) -> None:
        base = ["epic", "create", "X", "--outcome", "o", "--acceptance", "a", "--principle", "p",
                "--confirmation", "yes"]
        for drop in ("--confirmation", "--principle", "--acceptance"):
            argv = list(base)
            index = argv.index(drop)
            del argv[index:index + 2]
            self.fails(*argv)
        self.assertFalse((self.root / "doc_llm" / "Tasks").exists() and any((self.root / "doc_llm" / "Tasks").iterdir()))
        self.ok(*base)

    def test_placeholder_confirmation_rejected(self) -> None:
        self.fails("epic", "create", "X", "--outcome", "o", "--acceptance", "a", "--principle", "p",
                   "--confirmation", "{原则确认依据}")

    def test_dry_run_writes_nothing(self) -> None:
        self.ok("--dry-run", "epic", "create", "X", "--outcome", "o", "--acceptance", "a", "--principle", "p",
                "--confirmation", "yes")
        self.assertFalse((self.root / "doc_llm" / "Tasks").exists())


class CompletionReplay(Host):
    """E024/S017: 167 items, 18 usable; completion must not be claimable."""

    def setUp(self) -> None:
        super().setUp()
        self.epic()
        items = ",".join(f"cmd{i}" for i in range(167))
        self.ok("inv", "new", "commands", "--epic", "E001", "--reference", "command registry", "--items", items)
        self.task("UI", "--oblige", "all 167 commands really send", "--coverage", "full", "--population", "N1")
        self.task("Follow-up", "--oblige", "follow-up result")
        self.ok("inv", "mark", "commands", "--epic", "E001", "--ids", ",".join(str(i) for i in range(1, 19)),
                "--status", "pass", "--evidence", "out/compile.log")
        self.ok("checkpoint", "E001", "S001", "--verdict", "aligned", "--changed", "src/a.txt")

    def test_partial_population_blocks_close_even_with_claimed_review(self) -> None:
        self.fails("close", "E001", "S001", "--line", "P1=全部完成", "--line", "O2=主目标完成，剩余为无",
                   "--changed", "src/a.txt", needle="I3")

    def test_generic_evidence_does_not_flip_items(self) -> None:
        self.ok("evidence", "E001", "--obligations", "O2", "--scope", "full", "--locator", "out/all-tests-pass.log")
        ledger = self.ledger()
        self.assertNotEqual(obligation_status(ledger, ledger.get("O2")), "verified")

    def test_transfer_to_named_task_allows_task_close_not_epic_close(self) -> None:
        self.ok("transfer", "E001", "O2", "--to", "S002", "--entry", "live sending available")
        self.ok("close", "E001", "S001", "--line", "P1=compile-only stage kept within scope",
                "--line", "O2=transferred to S002, 18/167", "--changed", "src/a.txt")
        self.fails("epic", "close", "E001", needle="I5")

    def test_transfer_to_done_task_rejected(self) -> None:
        self.ok("checkpoint", "E001", "S002", "--verdict", "aligned", "--changed")
        self.ok("evidence", "E001", "--obligations", "O3", "--scope", "full", "--locator", "out/f.log")
        self.ok("close", "E001", "S002", "--line", "P1=kept principle", "--line", "O3=done with evidence",
                "--changed", "src/a.txt")
        self.fails("transfer", "E001", "O2", "--to", "S002", "--entry", "x", needle="I3")

    def test_waive_requires_user_decision(self) -> None:
        self.ok("decide", "E001", "--source", "executor", "--kind", "waive", "--summary", "I think optional")
        self.fails("waive", "E001", "O2", "--decision", "D2")
        self.ok("decide", "E001", "--source", "user", "--kind", "waive", "--quote", "用户：这项可以不做")
        self.ok("waive", "E001", "O2", "--decision", "D3")


class ParityReplay(Host):
    """macOS 1:1 parity: sampling must not pass as full alignment."""

    def test_diff_and_gate(self) -> None:
        ref = ",".join(f"f{i}" for i in range(312))
        target = ",".join(f"f{i}" for i in range(280))
        self.ok("inv", "new", "parity", "--reference", "Windows features", "--target", "macOS")
        self.ok("inv", "source", "parity", "--side", "ref", "--kind", "list", "--spec", ref)
        self.ok("inv", "source", "parity", "--side", "target", "--kind", "list", "--spec", target)
        text = self.ok("inv", "diff", "parity")
        self.assertIn("目标缺失 32", text)
        self.ok("inv", "mark", "parity", "--ids", ",".join(str(i) for i in range(1, 41)), "--status", "pass",
                "--evidence", "out/sample-run.log")
        code, text = self.run_dlm("inv", "report", "parity", "--gate")
        self.assertEqual(code, 1, text)
        self.assertIn("gap 32", text)
        self.assertIn("未闭合", text)

    def test_pass_requires_evidence_and_na_requires_reason(self) -> None:
        self.ok("inv", "new", "small", "--reference", "r", "--items", "a,b")
        self.fails("inv", "mark", "small", "--ids", "1", "--status", "pass")
        self.fails("inv", "mark", "small", "--ids", "1", "--status", "na")
        self.ok("inv", "mark", "small", "--ids", "1", "--status", "pass", "--evidence", "out/x")
        self.ok("inv", "mark", "small", "--ids", "2", "--status", "na", "--reason", "not applicable on platform")
        self.ok("inv", "report", "small", "--gate")

    def test_empty_inventory_is_unknown(self) -> None:
        self.ok("inv", "new", "empty", "--reference", "r")
        code, text = self.run_dlm("inv", "report", "empty", "--gate")
        self.assertEqual(code, 1)
        self.assertIn("未知", text)

    def test_glob_and_grep_sources(self) -> None:
        (self.root / "src" / "b.txt").write_text("command: alpha\ncommand: beta\n", encoding="utf-8")
        self.ok("inv", "new", "files", "--reference", "src files")
        self.assertIn("2 项", self.ok("inv", "source", "files", "--kind", "glob", "--spec", "src/*.txt"))
        self.ok("inv", "new", "cmds", "--reference", "commands")
        self.assertIn("2 项", self.ok("inv", "source", "cmds", "--kind", "grep", "--spec", r"command: (\w+)",
                                      "--in", "src/*.txt"))


class Rules(Host):
    def setUp(self) -> None:
        super().setUp()
        self.epic()

    def test_task_without_obligation_rejected(self) -> None:
        self.fails("task", "create", "E001", "T", "--depth", "High", "--depth-reason", "r", needle="E3")
        self.ok("task", "create", "E001", "Q", "--depth", "Lite", "--depth-reason", "r", "--kind", "investigation",
                "--question", "does X exist?", "--stop", "answer with evidence")

    def test_hypothesis_cannot_be_prerequisite(self) -> None:
        self.ok("hypo", "E001", "--claim", "plugin lacks paired ports")
        self.fails("task", "create", "E001", "T", "--depth", "High", "--depth-reason", "r", "--oblige", "x",
                   "--needs", "H1", needle="I6")
        self.ok("artifact", "E001", "--text", "paired port contract", "--state", "contract")
        self.task("T", "--oblige", "x", "--needs", "A1:contract")

    def test_budget_limit_cannot_reject(self) -> None:
        self.fails("limit", "E001", "1 GiB per volume", "--kind", "budget", "--on-exceed", "reject", needle="I10")
        self.ok("limit", "E001", "1 GiB per volume", "--kind", "budget", "--on-exceed", "chunk")
        self.fails("limit", "E001", "max 8 GiB", "--kind", "product", "--on-exceed", "reject", needle="I10")

    def test_gate_requires_user_decision(self) -> None:
        self.ok("decide", "E001", "--source", "executor", "--kind", "gate", "--summary", "batch admission 8-16")
        self.fails("epic", "gate", "E001", "--name", "batch", "--decision", "D2")

    def test_scope_decision_on_unverified_hypothesis_rejected(self) -> None:
        self.ok("hypo", "E001", "--claim", "lower layer bug")
        self.fails("decide", "E001", "--source", "user", "--kind", "scope", "--quote", "ok", "--basis", "H1",
                   needle="I6")

    def test_boundary_subset_and_specific_rule(self) -> None:
        self.fails("task", "create", "E001", "T", "--depth", "High", "--depth-reason", "r", "--oblige", "x",
                   "--write", "other/**", needle="I9")
        self.ok("decide", "E001", "--source", "user", "--kind", "scope", "--quote", "允许只读 src/core")
        self.ok("epic", "boundary", "E001", "--add-read-only", "src/core/**")
        self.fails("task", "create", "E001", "T", "--depth", "High", "--depth-reason", "r", "--oblige", "x",
                   "--write", "src/core/**", needle="I9")
        self.fails("epic", "boundary", "E001", "--add-write", "lib/**")
        self.ok("epic", "boundary", "E001", "--add-write", "lib/**", "--decision", "D2")

    def test_unknown_scope_blocks_close(self) -> None:
        self.task("T", "--oblige", "x")
        self.ok("evidence", "E001", "--obligations", "O2", "--scope", "full", "--locator", "out/x")
        self.ok("checkpoint", "E001", "S001", "--verdict", "aligned", "--changed")
        self.fails("close", "E001", "S001", "--line", "P1=kept", "--line", "O2=done", needle="I14")

    def test_checkpoint_deviation_needs_note_and_blocks_close(self) -> None:
        self.task("T", "--oblige", "x")
        self.ok("evidence", "E001", "--obligations", "O2", "--scope", "full", "--locator", "out/x")
        self.fails("checkpoint", "E001", "S001", "--verdict", "deviated")
        self.ok("checkpoint", "E001", "S001", "--verdict", "deviated", "--note", "used proxy test", "--changed")
        self.assertIn("deviated", self.ok("brief", "E001", "S001").splitlines()[1])
        self.fails("close", "E001", "S001", "--line", "P1=kept", "--line", "O2=done", "--changed", needle="K3")

    def test_review_must_cover_every_principle_and_obligation(self) -> None:
        self.task("T", "--oblige", "x")
        self.ok("evidence", "E001", "--obligations", "O2", "--scope", "full", "--locator", "out/x")
        self.ok("checkpoint", "E001", "S001", "--verdict", "aligned", "--changed")
        self.fails("close", "E001", "S001", "--line", "O2=done", "--changed", needle="K4")
        self.ok("close", "E001", "S001", "--line", "P1=kept real behaviour", "--line", "O2=done", "--changed")

    def test_reopen_omission_reactivates_owner(self) -> None:
        self.task("T", "--oblige", "x")
        self.ok("evidence", "E001", "--obligations", "O2", "--scope", "full", "--locator", "out/x")
        self.ok("checkpoint", "E001", "S001", "--verdict", "aligned", "--changed")
        self.ok("close", "E001", "S001", "--line", "P1=kept", "--line", "O2=done", "--changed")
        self.ok("reopen", "E001", "O2", "--cause", "omission", "--note", "checkbox never clicked")
        ledger = self.ledger()
        self.assertEqual(ledger.task("S001")["status"], "active")
        self.assertEqual(obligation_status(ledger, ledger.get("O2")), "open")

    def test_reopen_evidence_lost_does_not_reopen(self) -> None:
        self.task("T", "--oblige", "x")
        self.ok("evidence", "E001", "--obligations", "O2", "--scope", "full", "--locator", "out/x")
        self.ok("checkpoint", "E001", "S001", "--verdict", "aligned", "--changed")
        self.ok("close", "E001", "S001", "--line", "P1=kept", "--line", "O2=done", "--changed")
        self.ok("reopen", "E001", "O2", "--cause", "evidence-lost")
        ledger = self.ledger()
        self.assertEqual(ledger.task("S001")["status"], "done")
        self.assertEqual(obligation_status(ledger, ledger.get("O2")), "stale")

    def test_historical_source_rejected(self) -> None:
        (self.root / "old.md").write_text("<!-- dlm:historical -->\n# Old\n", encoding="utf-8")
        (self.root / "cur.md").write_text("# Current\n## Contract\n", encoding="utf-8")
        self.fails("task", "create", "E001", "T", "--depth", "High", "--depth-reason", "r", "--oblige", "x",
                   "--source", "old.md::history", needle="I13")
        self.fails("task", "create", "E001", "T", "--depth", "High", "--depth-reason", "r", "--oblige", "x",
                   "--source", "cur.md#Missing::contract", needle="I1")
        self.task("T", "--oblige", "x", "--source", "cur.md#Contract::contract")

    def test_readme_hand_edit_detected(self) -> None:
        readme = next((self.root / "doc_llm" / "Tasks").glob("E001_*/README.md"))
        readme.write_text(readme.read_text(encoding="utf-8") + "\nmanual note\n", encoding="utf-8")
        self.fails("check", needle="I13")
        self.ok("render", "E001")
        self.ok("check")

    def test_coverage_word_without_population_warns(self) -> None:
        text = self.ok("oblige", "E001", "所有插件配置页对齐", "--owner", "epic", "--derives", "O1")
        self.assertIn("D7", text)

    def test_epic_relations(self) -> None:
        self.ok("epic", "create", "Verify", "--outcome", "verified", "--acceptance", "a", "--principle", "p",
                "--confirmation", "yes", "--relation", "verifies:E001")
        self.fails("epic", "create", "Wait", "--outcome", "o", "--acceptance", "a", "--principle", "p",
                   "--confirmation", "yes", "--relation", "depends:E001", needle="E2")
        self.fails("epic", "create", "Art", "--outcome", "o", "--acceptance", "a", "--principle", "p",
                   "--confirmation", "yes", "--relation", "artifact:E001/A9", needle="E2")


class PublicLayer(Host):
    def test_public_layer_needs_justification_and_gate_evidence(self) -> None:
        policy = {"public_layers": ["src/core/**"], "public_layer_justification": ["why", "risk"],
                  "gates": [{"name": "core-suite", "paths": ["src/core/**"], "command": "run core suite"}]}
        (self.root / "doc_llm" / "dlm.policy.json").write_text(json.dumps(policy), encoding="utf-8")
        self.epic()
        self.task("T", "--oblige", "x")
        self.ok("evidence", "E001", "--obligations", "O2", "--scope", "full", "--locator", "out/x")
        self.ok("checkpoint", "E001", "S001", "--verdict", "aligned", "--changed", "src/core/c.txt")
        text = self.fails("close", "E001", "S001", "--line", "P1=kept", "--line", "O2=done",
                          "--changed", "src/core/c.txt")
        self.assertIn("I12", text)
        self.assertIn("I9", text)
        self.ok("decide", "E001", "--source", "user", "--kind", "scope", "--quote", "同意改公共层")
        self.fails("justify", "E001", "S001", "--layer", "src/core/**", "--field", "why=x", "--decision", "D2")
        self.ok("justify", "E001", "S001", "--layer", "src/core/**", "--field", "why=existing adapter insufficient",
                "--field", "risk=abi stable", "--decision", "D2")
        self.ok("evidence", "E001", "--gate", "core-suite", "--scope", "full", "--locator", "out/core.log",
                "--task", "S001")
        self.ok("close", "E001", "S001", "--line", "P1=kept", "--line", "O2=done", "--changed", "src/core/c.txt")


class Granularity(Host):
    def test_roadmap_draft_raises_signals(self) -> None:
        tasks = []
        for i in range(25):
            tasks.append({"id": f"T{i}", "title": f"Phase {i // 6}: step {i}", "group": f"G{i // 6}",
                          "obligations": [f"o{i}"], "needs": [f"T{i - 1}"] if i else []})
        draft = {"epics": [{"title": "V2 implementation and delivery", "outcome": "V2 delivered",
                            "acceptance": ["V2 works"], "tasks": tasks}]}
        path = self.root / "draft.json"
        path.write_text(json.dumps(draft), encoding="utf-8")
        text = self.ok("plan-check", "--draft", str(path))
        for needle in ("个子任务 >", "依赖链深度", "阶段/分组"):
            self.assertIn(needle, text)

    def test_independent_epics_and_disallowed_dependency(self) -> None:
        draft = {"epics": [
            {"title": "Slicing via SDK", "outcome": "slices reach the validation app", "acceptance": ["a"],
             "tasks": [{"id": "S1", "obligations": ["grid"], "produces": ["grid"]},
                       {"id": "S2", "obligations": ["resample"], "needs": ["grid"]},
                       {"id": "S3", "obligations": ["sdk"], "needs": ["S2"]}]},
            {"title": "Release regression", "outcome": "release verified", "acceptance": ["b"],
             "relations": [{"kind": "verifies", "target": "Slicing via SDK"}],
             "tasks": [{"id": "R1", "obligations": ["regression"]}, {"id": "R2", "obligations": ["package"], "needs": ["R1"]}]},
        ]}
        path = self.root / "good.json"
        path.write_text(json.dumps(draft), encoding="utf-8")
        text = self.ok("plan-check", "--draft", str(path))
        self.assertNotIn("SIGNAL", text)
        draft["epics"][1]["depends_on"] = ["Slicing via SDK"]
        path.write_text(json.dumps(draft), encoding="utf-8")
        self.fails("plan-check", "--draft", str(path), needle="E2")

    def test_disconnected_work_signal(self) -> None:
        draft = {"epics": [{"title": "Mixed", "outcome": "o", "acceptance": ["a"], "tasks": [
            {"id": "A1", "obligations": ["x"]}, {"id": "A2", "obligations": ["y"], "needs": ["A1"]},
            {"id": "B1", "obligations": ["z"]}, {"id": "B2", "obligations": ["w"], "needs": ["B1"]}]}]}
        path = self.root / "mixed.json"
        path.write_text(json.dumps(draft), encoding="utf-8")
        self.assertIn("互不相连", self.ok("plan-check", "--draft", str(path)))


class ReviewFixes(Host):
    """Loopholes found when re-checking the 15 historical failure patterns."""

    def setUp(self) -> None:
        super().setUp()
        self.epic()

    def test_new_obligation_needs_origin_or_derives(self) -> None:
        self.fails("oblige", "E001", "整条链路性能全部达标", "--owner", "epic", needle="I12")
        self.ok("oblige", "E001", "step result", "--owner", "epic", "--derives", "O1")
        self.ok("decide", "E001", "--source", "user", "--kind", "requirement", "--quote", "用户：加上这个结果")
        self.ok("oblige", "E001", "user added result", "--owner", "epic", "--origin", "D2")

    def test_na_on_full_population_requires_user_decision(self) -> None:
        self.ok("inv", "new", "cmds", "--epic", "E001", "--reference", "registry", "--items", "a,b,c")
        self.task("UI", "--oblige", "all commands send", "--coverage", "full", "--population", "N1")
        self.fails("inv", "mark", "cmds", "--epic", "E001", "--ids", "2,3", "--status", "na",
                   "--reason", "out of scope", needle="I8")
        self.ok("decide", "E001", "--source", "user", "--kind", "waive", "--quote", "用户：b、c 不适用")
        self.ok("inv", "mark", "cmds", "--epic", "E001", "--ids", "2,3", "--status", "na", "--reason",
                "platform n/a", "--decision", "D2")

    def test_population_swap_requires_user_decision(self) -> None:
        self.ok("inv", "new", "all", "--epic", "E001", "--reference", "registry", "--items", "a,b,c")
        self.ok("inv", "new", "easy", "--epic", "E001", "--reference", "easy subset", "--items", "a")
        self.task("UI", "--oblige", "all commands send", "--coverage", "full", "--population", "N1")
        self.fails("obligation", "E001", "O2", "--population", "N2")

    def test_transferred_in_obligation_blocks_target_close(self) -> None:
        self.task("A", "--oblige", "a")
        self.task("B", "--oblige", "b")
        self.ok("transfer", "E001", "O2", "--to", "S002", "--entry", "after A stage")
        self.ok("evidence", "E001", "--obligations", "O3", "--scope", "full", "--locator", "out/x")
        self.ok("checkpoint", "E001", "S002", "--verdict", "aligned", "--changed")
        self.fails("close", "E001", "S002", "--line", "P1=kept", "--line", "O3=done", "--changed", needle="未接手")

    def test_missing_evidence_file_blocks_close(self) -> None:
        self.task("T", "--oblige", "x")
        self.ok("evidence", "E001", "--obligations", "O2", "--scope", "full", "--locator", "out/never-written.log")
        self.ok("checkpoint", "E001", "S001", "--verdict", "aligned", "--changed")
        self.fails("close", "E001", "S001", "--line", "P1=kept", "--line", "O2=done", "--changed", needle="I14")
        self.ok("evidence", "E001", "--obligations", "O2", "--scope", "full", "--locator", "note:用户已人工复验")

    def test_epic_inventory_mark_obeys_invariants(self) -> None:
        self.ok("inv", "new", "cmds", "--epic", "E001", "--reference", "registry", "--items", "a,b")
        self.task("UI", "--oblige", "all", "--coverage", "full", "--population", "N1")
        self.fails("inv", "mark", "cmds", "--epic", "E001", "--ids", "1", "--status", "na", "--reason", "skip")
        self.assertEqual(self.ledger().find("N1.1")["status"], "pending")

    def test_task_obligation_count_signal(self) -> None:
        extra = []
        for i in range(6):
            extra += ["--oblige", f"result {i}"]
        text = self.ok("task", "create", "E001", "Big", "--depth", "Ultra", "--depth-reason", "r", "--write",
                       "src/**", "--derives", "O1", *extra)
        self.assertIn("项义务 >", text)


class GitScope(Host):
    def setUp(self) -> None:
        super().setUp()
        import subprocess
        for args in (["init", "-q"], ["add", "."], ["-c", "user.email=a@b", "-c", "user.name=n", "commit", "-qm", "i"]):
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)
        self.epic()

    def test_explicit_changed_cannot_hide_git_changes(self) -> None:
        self.task("T", "--oblige", "x")
        (self.root / "src" / "a.txt").write_text("changed", encoding="utf-8")
        text = self.ok("scope", "E001", "S001", "--changed")
        self.assertIn("src/a.txt", text)

    def test_baseline_excludes_untouched_preexisting_changes(self) -> None:
        (self.root / "src" / "a.txt").write_text("someone else", encoding="utf-8")
        self.task("T", "--oblige", "x")
        self.ok("task", "start", "E001", "S001")
        self.assertNotIn("+ src/a.txt", self.ok("scope", "E001", "S001"))
        (self.root / "src" / "a.txt").write_text("this task", encoding="utf-8")
        self.assertIn("+ src/a.txt", self.ok("scope", "E001", "S001"))

    def test_unattributed_forbidden_git_changes_do_not_block_close_while_real_violations_do(self) -> None:
        self.ok("epic", "boundary", "E001", "--add-forbidden", "core/**", "--add-forbidden", "src/secret/**",
                "--add-read-only", "ro/**")
        self.task("T", "--oblige", "x")
        (self.root / "core").mkdir(exist_ok=True)
        (self.root / "ro").mkdir(exist_ok=True)
        # Unrelated worktree edits in Epic forbidden / read_only paths outside task write scope (src/**).
        (self.root / "core" / "api.h").write_text("unrelated task edit", encoding="utf-8")
        (self.root / "ro" / "sdk.h").write_text("unrelated ro edit", encoding="utf-8")
        (self.root / "src" / "impl.txt").write_text("task work", encoding="utf-8")
        scope_out = self.ok("scope", "E001", "S001")
        self.assertIn("边界冲突 0", scope_out)
        self.assertIn("+ src/impl.txt", scope_out)
        self.ok("evidence", "E001", "--obligations", "O2", "--scope", "full", "--locator", "out/x")
        self.ok("checkpoint", "E001", "S001", "--verdict", "aligned")
        # Negative 1a: explicitly claiming a forbidden path via --changed is rejected.
        self.fails("scope", "E001", "S001", "--changed", "core/api.h", needle="Epic forbidden")
        self.fails("close", "E001", "S001", "--line", "P1=kept", "--line", "O2=done",
                   "--changed", "core/api.h", needle="I9")
        # Negative 1b: explicitly claiming an out-of-task-scope path via --changed is rejected.
        self.fails("close", "E001", "S001", "--line", "P1=kept", "--line", "O2=done",
                   "--changed", "other/outside.txt", needle="I9")
        # Negative 2: a git change inside task write scope (src/**) that hits Epic forbidden (src/secret/**) is rejected.
        (self.root / "src" / "secret").mkdir(exist_ok=True)
        secret_file = self.root / "src" / "secret" / "hack.txt"
        secret_file.write_text("in-scope forbidden", encoding="utf-8")
        self.fails("scope", "E001", "S001", needle="Epic forbidden")
        self.fails("close", "E001", "S001", "--line", "P1=kept", "--line", "O2=done", needle="I9")
        secret_file.unlink()
        # Positive: once only unrelated worktree edits remain in core/** and ro/**, close succeeds.
        self.ok("close", "E001", "S001", "--line", "P1=kept", "--line", "O2=done")


class AdoptHistory(Host):
    def test_adopted_brief_hides_v1_state_sections(self) -> None:
        epic = self.root / "doc_llm" / "Tasks" / "E001_old"
        (epic / "subtasks").mkdir(parents=True)
        (epic / "README.md").write_text(
            "# E001: Old\n\n## Meta\n\n| 字段 | 值 |\n|---|---|\n| 状态 | 🔄 进行中 |\n| 原则确认 | 用户：确认 |\n\n"
            "## Epic 核心原则\n\n1. real behaviour\n2. keep owners\n\n## 核心目标\n\nusers can do X\n", encoding="utf-8")
        (epic / "subtasks" / "S001_x.md").write_text(
            "# S001: X\n\n## Meta\n\n| 状态 | 🔄 进行中 |\n\n## 方案承接与实施设计\n\nreuse the owner\n\n"
            "## 执行日志\n\nold queue: resume S005\n", encoding="utf-8")
        self.ok("adopt", "E001")
        brief = self.ok("brief", "E001", "S001")
        self.assertIn("reuse the owner", brief)
        self.assertNotIn("resume S005", brief)
        self.assertNotIn("🔄 进行中", brief)
        self.assertTrue((epic / "epic.md").read_text(encoding="utf-8").startswith("<!-- dlm: adopted"))


class LayerSplitReplay(Host):
    """Epics cut by algorithm stage / technical layer that depend on each other."""

    def test_dependency_web_draft(self) -> None:
        layers = ["common", "partition", "localization", "planning", "resampling", "pipeline", "sdk", "workbench"]
        epics = []
        for i, layer in enumerate(layers):
            needs = [f"{layers[j]}-out" for j in range(len(layers)) if j != i and abs(j - i) <= 2]
            epics.append({"id": layer, "title": f"{layer} layer", "outcome": f"{layer} delivered",
                          "acceptance": ["ok"], "tasks": [
                              {"id": "S1", "obligations": [f"{layer} contract"], "needs": needs, "produces": [f"{layer}-out"]},
                              {"id": "S2", "obligations": [f"{layer} impl"], "needs": [f"{layer}-out"]},
                              {"id": "S3", "obligations": [f"{layer} tests"], "needs": [f"{layer}-out"]}]})
        path = self.root / "layers.json"
        path.write_text(json.dumps({"epics": epics}), encoding="utf-8")
        text = self.ok("plan-check", "--draft", str(path))
        self.assertIn("依赖成网", text.replace("连成依赖网", "依赖成网"))
        self.assertIn("个其他 Epic 的产物", text)

    def test_cross_epic_needs_signal_on_create(self) -> None:
        for i in range(3):
            self.ok("epic", "create", f"P{i}", "--outcome", "o", "--acceptance", "a", "--principle", "p",
                    "--confirmation", "yes", "--write", "src/**")
            self.ok("artifact", f"E00{i + 1}", "--text", f"artifact {i}", "--state", "implemented")
        self.ok("epic", "create", "Consumer", "--outcome", "o", "--acceptance", "a", "--principle", "p",
                "--confirmation", "yes", "--write", "src/**")
        text = self.ok("task", "create", "E004", "T", "--depth", "High", "--depth-reason", "r", "--derives", "O1",
                       "--oblige", "x", "--write", "src/**", "--needs", "E001/A1", "--needs", "E002/A1",
                       "--needs", "E003/A1")
        self.assertIn("依赖 3 个其他 Epic", text)


class NarrativeAndRuns(Host):
    def setUp(self) -> None:
        super().setUp()
        self.epic()

    def test_pasted_paragraph_and_log_accumulation(self) -> None:
        self.task("A", "--oblige", "a")
        self.task("B", "--oblige", "b")
        paragraph = "当前执行基线：只保留旧版能力，后续路线全部归档；性能和鲁棒性均不得退化，旧的放宽许可撤销，下文旧方案仅供历史查阅。" * 2
        epic_dir = next((self.root / "doc_llm" / "Tasks").glob("E001_*"))
        for path in [epic_dir / "epic.md", *sorted((epic_dir / "tasks").glob("*.md"))]:
            path.write_text(path.read_text(encoding="utf-8") + "\n\n" + paragraph + "\n", encoding="utf-8")
        log = "\n".join(f"- 2026-10-{d:02d} 又跑了一轮全量对照" for d in range(1, 20))
        task_file = sorted((epic_dir / "tasks").glob("*.md"))[0]
        task_file.write_text(task_file.read_text(encoding="utf-8") + "\n" + log + "\n", encoding="utf-8")
        text = self.ok("check")
        self.assertIn("同一段落出现在 3 个文件", text)
        self.assertIn("行带日期", text)

    def test_repeated_full_validation_signal(self) -> None:
        self.task("T", "--oblige", "x")
        for _ in range(3):
            self.ok("evidence", "E001", "--obligations", "O2", "--scope", "full", "--locator", "out/x",
                    "--task", "S001", "--duration", "114m")
        self.assertIn("次全量验证", self.ok("check"))

    def test_drop_task_requires_user_decision_and_resolved_obligations(self) -> None:
        self.task("Enhanced route", "--oblige", "beat the old version")
        self.ok("decide", "E001", "--source", "executor", "--kind", "direction", "--summary", "stop it")
        self.fails("task", "drop", "E001", "S001", "--decision", "D2")
        self.ok("decide", "E001", "--source", "user", "--kind", "supersede", "--quote", "用户：只保留旧版能力，增强路线归档")
        self.fails("task", "drop", "E001", "S001", "--decision", "D3", needle="I3")
        self.ok("supersede", "E001", "O2", "--decision", "D3")
        self.ok("task", "drop", "E001", "S001", "--decision", "D3")


class TwoDigitIds(Host):
    def test_host_with_two_digit_ids(self) -> None:
        old = self.root / "doc_llm" / "Tasks" / "E01_old"
        (old / "subtasks").mkdir(parents=True)
        (old / "README.md").write_text("# E01: Old\n\n## 核心目标\n\nusers can do X\n", encoding="utf-8")
        (old / "subtasks" / "S01_a.md").write_text("# S01: A\n\n| 状态 | 🔄 进行中 |\n", encoding="utf-8")
        (old / "subtasks" / "S02_b.md").write_text("# S02: B\n\n| 状态 | 已停止 |\n", encoding="utf-8")
        self.ok("adopt", "E01")
        ledger = Ledger.load(load_host(), "E01")
        self.assertEqual([t["id"] for t in ledger.collection("S")], ["S01", "S02"])
        self.assertEqual(ledger.task("S02")["status"], "dropped")
        self.assertEqual(ledger.next_id("S"), "S03")
        self.ok("epic", "create", "New", "--outcome", "o", "--acceptance", "a", "--principle", "p", "--confirmation", "yes")
        self.assertTrue(any(p.name.startswith("E02_") for p in (self.root / "doc_llm" / "Tasks").iterdir()))


class KnowledgeCliForwarding(Host):
    def test_kb_route_preview_and_custom_knowledge_dir(self) -> None:
        (self.root / "dlm.config.json").write_text(
            json.dumps({"knowledge_dir": "kb_docs", "tasks_dir": "kb_docs/Tasks", "cache_dir": "out"}),
            encoding="utf-8",
        )
        # Global --dry-run (or trailing --dry-run) on kb scaffold writes nothing
        self.ok("--dry-run", "kb", "scaffold", "init-project", "Demo")
        self.assertFalse((self.root / "kb_docs" / "BRIEF.md").exists())
        self.ok("kb", "scaffold", "init-project", "Demo", "--dry-run")
        self.assertFalse((self.root / "kb_docs" / "BRIEF.md").exists())
        # Actual scaffold in custom knowledge_dir
        self.ok("kb", "scaffold", "init-project", "Demo")
        self.ok("kb", "scaffold", "init-domain", "1_core", "--domain-title", "Core", "--root-path", "src")
        self.ok("kb", "scaffold", "create-l2", "1_core", "engine",
                "--module-title", "Engine", "--match-glob", "src/*.py")
        l1_path = self.root / "kb_docs" / "Domains" / "1_core" / "L1_core_router.md"
        before_l1 = l1_path.read_text(encoding="utf-8")
        # kb route with --preview must not modify L1 on disk
        preview_out = self.ok("kb", "route", "kb_docs/Domains/1_core", "--preview")
        self.assertIn("[PREVIEW]", preview_out)
        self.assertEqual(l1_path.read_text(encoding="utf-8"), before_l1)
        # Global --dry-run on kb route also acts as --preview
        self.ok("--dry-run", "kb", "route", "--all")
        self.assertEqual(l1_path.read_text(encoding="utf-8"), before_l1)
        # Real kb route updates L1
        self.ok("kb", "route", "kb_docs/Domains/1_core")
        self.assertIn("L2_Modules/engine.md", l1_path.read_text(encoding="utf-8"))
        # kb map with default target uses custom knowledge_dir
        map_out = json.loads(self.ok("kb", "map", "--json"))
        self.assertEqual(map_out["summary"]["l2_files"], 1)

    def test_default_docs_llm_and_non_default_doc_llm_compatibility(self) -> None:
        # 1. Fresh repository without doc_llm/ defaults to docs-llm/
        shutil.rmtree(self.root / "doc_llm")
        fresh_host = load_host(self.root)
        self.assertEqual(fresh_host.knowledge_dir, self.root / "docs-llm")
        self.assertEqual(fresh_host.tasks_dir, self.root / "docs-llm" / "Tasks")
        self.ok("kb", "scaffold", "init-project", "Fresh Default Repo")
        self.assertTrue((self.root / "docs-llm" / "BRIEF.md").exists())
        self.assertFalse((self.root / "doc_llm").exists())
        self.ok(
            "epic", "create", "默认目录验证",
            "--outcome", "在默认 docs-llm 下正常建单",
            "--acceptance", "通过检查",
            "--principle", "使用默认目录",
            "--confirmation", "确认原则并建单",
        )
        self.assertTrue(any((self.root / "docs-llm" / "Tasks").glob("E001_*/ledger.json")))
        self.ok("check", "E001")

        # 2. Non-default repository with existing doc_llm/ (and no dlm.config.json) auto-detects doc_llm/
        shutil.rmtree(self.root / "docs-llm")
        (self.root / "doc_llm").mkdir()
        compat_host = load_host(self.root)
        self.assertEqual(compat_host.knowledge_dir, self.root / "doc_llm")
        self.assertEqual(compat_host.tasks_dir, self.root / "doc_llm" / "Tasks")
        self.ok("kb", "scaffold", "init-project", "Non-Default Compat Repo")
        self.assertTrue((self.root / "doc_llm" / "BRIEF.md").exists())
        self.assertFalse((self.root / "docs-llm").exists())
        self.ok(
            "epic", "create", "兼容目录验证",
            "--outcome", "在非默认 doc_llm 下正常建单",
            "--acceptance", "通过检查",
            "--principle", "兼容既有目录",
            "--confirmation", "确认原则并建单",
        )
        self.assertTrue(any((self.root / "doc_llm" / "Tasks").glob("E001_*/ledger.json")))
        self.ok("check", "E001")
        self.ok("kb", "check", "doc_llm/00_Global")

    def test_inv_export_and_sync_lease_bridge(self) -> None:
        import subprocess
        self.epic("--write", "doc_llm/Tasks/**")
        self.ok("inv", "new", "modules", "--epic", "E001", "--reference", "目标模块全集", "--items", "ModB1,ModA1,ModA2")
        self.ok("task", "create", "E001", "多模块并行收敛与收口", "--depth", "Medium", "--depth-reason", "多模块 SOP 执行",
                "--oblige", "全部模块按统一 SOP 完成", "--coverage", "full", "--population", "N1", "--origin", "D1",
                "--write", "doc_llm/Tasks/**")
        self.ok("task", "start", "E001", "S001")

        # 1. One-click export from N1 -> work_items.md + work_item_runtime.json with priority rules & remarks
        out = self.ok(
            "inv", "export-lease", "N1", "--epic", "E001", "--task", "S001",
            "--sop-ref", "doc_llm/SOPs/mod/README.md",
            "--priority-rule", "A=^ModA", "--priority-rule", "B=^ModB",
            "--remark", "ModA1=核心模块注意边界",
        )
        self.assertIn("导出 3 项", out)
        epic_dir = next((self.root / "doc_llm" / "Tasks").glob("E001_*"))
        wi_path = epic_dir / "references" / "work_items.md"
        rt_path = epic_dir / "references" / "work_item_runtime.json"
        self.assertTrue(wi_path.is_file())
        self.assertTrue(rt_path.is_file())
        wi_text = wi_path.read_text(encoding="utf-8")
        # Sorted by priority A then B; ModA1 has remark, ModA2 has empty remark cell
        self.assertIn("| W002 | ModA1 | A | 核心模块注意边界 | ⬜ | — | — | — | — |", wi_text)
        self.assertIn("| W003 | ModA2 | A |  | ⬜ | — | — | — | — |", wi_text)
        self.assertIn("| W001 | ModB1 | B |  | ⬜ | — | — | — | — |", wi_text)

        # 2. Simulate real dlm-multi-llm-lease worker claiming and completing items
        lease_scripts = SCRIPT_DIR.parent.parent / "dlm-multi-llm-lease" / "scripts"
        env = os.environ.copy()
        env["CODEX_THREAD_ID"] = "worker-1"
        env["CODEX_SESSION_ID"] = "worker-1"

        claim1 = subprocess.run(
            [sys.executable, "-X", "utf8", str(lease_scripts / "claim_next_work_item.py"), "--config", str(rt_path)],
            cwd=str(self.root), env=env, text=True, encoding="utf-8", capture_output=True, check=False,
        )
        self.assertEqual(0, claim1.returncode, claim1.stderr)
        c1_data = json.loads(claim1.stdout)
        self.assertEqual(("W002", "ModA1", "核心模块注意边界"), (
            c1_data["work_item_id"], c1_data["work_item_module"], c1_data["work_item_remark"],
        ))

        # Sync with --require-zero-active must fail while worker-1 holds an active claim
        self.fails("inv", "sync-lease", "N1", "--epic", "E001", "--require-zero-active")

        # Complete all 3 items via lease CLI
        for idx, note in enumerate(("ModA1 done", "ModA2 done", "ModB1 done")):
            comp = subprocess.run(
                [sys.executable, "-X", "utf8", str(lease_scripts / "complete_work_item.py"),
                 "--config", str(rt_path), "--status", "done", "--note", note],
                cwd=str(self.root), env=env, text=True, encoding="utf-8", capture_output=True, check=False,
            )
            self.assertEqual(0, comp.returncode, comp.stderr)
            nxt = subprocess.run(
                [sys.executable, "-X", "utf8", str(lease_scripts / "claim_next_work_item.py"), "--config", str(rt_path)],
                cwd=str(self.root), env=env, text=True, encoding="utf-8", capture_output=True, check=False,
            )
            if idx < 2:
                self.assertEqual(0, nxt.returncode, nxt.stderr)
            else:
                self.assertNotEqual(0, nxt.returncode)
                self.assertIn("No available work items.", nxt.stderr)

        # 3. One-click sync-lease back to N1 + V# evidence, then close S001
        sync_out = self.ok("inv", "sync-lease", "N1", "--epic", "E001", "--task", "S001", "--require-zero-active", "--gate")
        self.assertIn("pass=3", sync_out)
        self.assertIn("结论：已逐项闭合", sync_out)
        self.ok("checkpoint", "E001", "S001", "--verdict", "aligned", "--changed")
        self.ok("close", "E001", "S001", "--line", "P1=遵守原则完成", "--line", "O2=3项模块均由lease同步闭合", "--changed")
        self.ok("check", "E001")


class Portability(unittest.TestCase):
    """The pack must not carry host-specific content (paths, project names)."""

    FORBIDDEN = ("ss-group", "ss_com", "ss_core", "Snapshot", "Gitee", "csharp/", "cpp/", "E024", "E041",
                 "E058", "D:\\", "D:/")

    def test_pack_has_no_host_tokens(self) -> None:
        pack = SCRIPT_DIR.parent.parent
        offenders = []
        roots = [pack / "_shared"] + sorted(pack.glob("dlm-*"))
        for root in roots:
            for path in root.rglob("*"):
                if path.is_dir() or "__pycache__" in path.parts or path.suffix in {".pyc"}:
                    continue
                if path.name.startswith("test_dlm_v2"):
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                for token in self.FORBIDDEN:
                    if token in text:
                        offenders.append(f"{path.relative_to(pack)}: {token}")
        self.assertEqual(offenders, [], "\n".join(offenders[:50]))

    def test_skill_prompts_are_english_and_doc_llm_templates_are_chinese(self) -> None:
        import re
        pack = SCRIPT_DIR.parent.parent
        cjk = re.compile(r"[\u4e00-\u9fff]")
        prompt_files = (
            list(pack.glob("dlm-*/SKILL.md"))
            + list(pack.glob("dlm-*/SELF-DESCRIPTION.md"))
            + list(pack.glob("dlm-*/agents/openai.yaml"))
            + list(pack.glob("dlm-*/references/*.md"))
            + [pack / "_shared" / "README.md"]
            + list((pack / "_shared" / "references").glob("*.md"))
        )
        self.assertGreaterEqual(len(prompt_files), 24)
        heading_offenders = []
        for path in prompt_files:
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.startswith("#") and cjk.search(line):
                    heading_offenders.append(f"{path.relative_to(pack)}:{lineno}: {line}")
        self.assertEqual(heading_offenders, [], "\n".join(heading_offenders))

        # Verify all doc_llm generation templates and default zh-CN locale remain Chinese.
        zh_locale = json.loads((pack / "_shared" / "locales" / "zh-CN.json").read_text(encoding="utf-8"))
        for key in ("principles", "obligations", "tasks", "task_design", "epic_background", "backlog_title"):
            self.assertRegex(zh_locale[key], cjk)
        templates = list((pack / "dlm-knowledge" / "templates").glob("*.md")) + list(
            (pack / "_shared" / "scripts" / "legacy" / "templates").glob("*.md")
        )
        self.assertEqual(len(templates), 11)
        for path in templates:
            self.assertRegex(path.read_text(encoding="utf-8-sig"), cjk, f"Template must stay Chinese: {path.name}")

        # Verify cross-language anchors in English prompts match exact Chinese headings/enums/reasons.
        task_skill = (pack / "dlm-task" / "SKILL.md").read_text(encoding="utf-8")
        for anchor in ("## 背景与预期效果", "## 共享设计与取舍", "## 范围与非目标", "## 方案承接与实施设计", "## 关键路径与入口", "总数 (total)"):
            self.assertIn(anchor, task_skill)
        inv_ref = (pack / "dlm-task" / "references" / "inventory.md").read_text(encoding="utf-8")
        for anchor in ("目标侧缺失", "仅目标侧存在（多出项，记录）"):
            self.assertIn(anchor, inv_ref)
        kb_skill = (pack / "dlm-knowledge" / "SKILL.md").read_text(encoding="utf-8")
        for anchor in ("## 根路由表", "## 二、L2 路由表", "## 三、参考资料路由", "## SOP 路由表", "已实现", "已归档"):
            self.assertIn(anchor, kb_skill)


if __name__ == "__main__":
    unittest.main()
