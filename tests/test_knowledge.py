"""Behavior checks with isolated, fictional files; no personal data or network."""

import copy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "tools" / "check_knowledge.py"


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="knowledge-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "bundle"
        self.root.mkdir()
        self.rule = {
            "id": "brief-uncertainty", "kind": "preference", "status": "active",
            "scope": "demo-project", "family": "brief-sections",
            "source": "rules/uncertainty.md", "replaces": [],
            "required_headings": ["未验证事项"],
            "evidence": {"type": "user_confirmation", "path": "evidence/confirmation.md"},
        }
        self.write("rules/uncertainty.md", "简报应单列未验证事项。\n")
        self.write("evidence/confirmation.md", "虚构反馈：今后这类简报单列未验证事项。\n")
        self.write("ROUTING.md", "# 用途导航\n\n[简报](rules/uncertainty.md)\n")
        self.registry([self.rule])

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def registry(self, rules):
        self.write("registry.json", json.dumps({"schema_version": 1, "rules": rules}, ensure_ascii=False))

    def check(self, *extra, root=None):
        result = subprocess.run(
            [sys.executable, "-B", str(CHECKER), str(root or self.root), *extra],
            capture_output=True, text=True, timeout=10,
        )
        try:
            report = json.loads(result.stdout)
        except ValueError:
            report = {"non_json_output": True}
        return result.returncode, report

    def assert_issue(self, expected, *extra):
        code, report = self.check(*extra)
        self.assertEqual(code, 1, report)
        self.assertIn(expected, [item["code"] for item in report.get("issues", [])], report)

    def test_active_bundle_passes_without_changing_any_file(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        code, report = self.check()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "passed")
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_candidate_cannot_be_used_as_current_route(self):
        self.rule["status"] = "candidate"
        self.registry([self.rule])
        self.assert_issue("route_not_active")

    def test_retired_rule_cannot_be_used_as_current_route(self):
        self.rule["status"] = "retired"
        self.registry([self.rule])
        self.assert_issue("route_not_active")

    def test_active_preference_needs_confirmation_evidence(self):
        self.rule.pop("evidence")
        self.registry([self.rule])
        self.assert_issue("missing_active_evidence")
        self.rule["evidence"] = {"type": "reproducible_result", "path": "evidence/confirmation.md"}
        self.registry([self.rule])
        self.assert_issue("evidence_kind_mismatch")

    def test_duplicate_id_and_competing_active_rules_are_rejected(self):
        other = copy.deepcopy(self.rule)
        self.registry([self.rule, other])
        self.assert_issue("duplicate_id")
        other["id"] = "brief-other"
        self.registry([self.rule, other])
        self.assert_issue("multiple_active")

    def test_replacement_requires_existing_ids_and_acyclic_history(self):
        self.rule["replaces"] = ["not-present"]
        self.registry([self.rule])
        self.assert_issue("unknown_replacement")
        old = copy.deepcopy(self.rule)
        old.update(id="brief-old", status="retired", source="rules/old.md", replaces=["brief-uncertainty"])
        self.write("rules/old.md", "旧版虚构规则。\n")
        self.rule["replaces"] = ["brief-old"]
        self.registry([self.rule, old])
        self.assert_issue("replacement_cycle")

    def test_active_rule_must_have_an_actual_route(self):
        self.write("ROUTING.md", "# 用途导航\n")
        self.assert_issue("active_not_routed")

    def test_invalid_relative_paths_are_rule_errors(self):
        for path in ["../outside.md", "/outside.md", "C:\\outside.md", "rules/../outside.md", ".hidden/file.md"]:
            with self.subTest(path=path):
                self.rule["source"] = path
                self.registry([self.rule])
                self.assert_issue("invalid_relative_path")

    def test_missing_file_and_invalid_utf8_are_read_errors(self):
        (self.root / "evidence/confirmation.md").unlink()
        code, report = self.check()
        self.assertEqual((code, report.get("status")), (2, "error"), report)
        (self.root / "evidence/confirmation.md").write_bytes(b"\xff\xfe")
        code, report = self.check()
        self.assertEqual((code, report.get("status")), (2, "error"), report)

    def test_symlink_reference_and_symlink_bundle_are_refused(self):
        target = Path(self.temp.name) / "outside.md"
        target.write_text("outside fixture", encoding="utf-8")
        source = self.root / "rules/uncertainty.md"
        source.unlink()
        try:
            source.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable in this test environment")
        code, report = self.check()
        self.assertEqual((code, report.get("status")), (2, "error"), report)
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        code, report = self.check(root=alias)
        self.assertEqual((code, report.get("status")), (2, "error"), report)

    def test_unreadable_file_is_a_read_error_when_permissions_are_enforced(self):
        target = self.root / "evidence/confirmation.md"
        mode = target.stat().st_mode
        target.chmod(0)
        self.addCleanup(target.chmod, stat.S_IMODE(mode))
        try:
            target.read_bytes()
        except PermissionError:
            code, report = self.check()
            self.assertEqual((code, report.get("status")), (2, "error"), report)
        else:
            self.skipTest("current account bypasses file permission bits")

    def test_malformed_registry_and_cli_usage_return_json_input_errors(self):
        self.write("registry.json", "{not json")
        code, report = self.check()
        self.assertEqual((code, report.get("status")), (2, "error"), report)
        result = subprocess.run([sys.executable, "-B", str(CHECKER)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "error")

    def test_malformed_rule_field_types_return_rule_errors_not_tracebacks(self):
        for field, value in [("kind", []), ("status", {}), ("required_headings", [4]), ("replaces", 4)]:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.rule)
                changed[field] = value
                self.registry([changed])
                code, report = self.check()
                self.assertEqual((code, report.get("status")), (1, "failed"), report)

    def test_duplicate_json_keys_are_rejected_instead_of_silently_overwritten(self):
        content = json.dumps({"schema_version": 1, "rules": [self.rule]}, ensure_ascii=False)
        self.write("registry.json", content.replace('"status": "active"', '"status": "candidate", "status": "active"'))
        self.assert_issue("duplicate_json_key")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "named pipes unavailable")
    def test_named_pipe_is_rejected_without_waiting_for_a_writer(self):
        target = self.root / "evidence/confirmation.md"
        target.unlink()
        os.mkfifo(target)
        try:
            code, report = self.check()
        except subprocess.TimeoutExpired:
            self.fail("reader blocked on a named pipe")
        self.assertEqual((code, report.get("status")), (2, "error"), report)

    def test_output_requires_real_headings_and_does_not_count_code_examples(self):
        self.write("outputs/good.md", "# 项目简报\n\n## 未验证事项\n尚未检查导出结果。\n")
        code, report = self.check("--check-output", "outputs/good.md")
        self.assertEqual((code, report.get("output_checked")), (0, True), report)
        self.write("outputs/missing.md", "# 项目简报\n\n正文提到未验证事项，但没有独立标题。\n")
        self.assert_issue("missing_required_heading", "--check-output", "outputs/missing.md")
        self.write("outputs/code.md", "# 项目简报\n\n```markdown\n## 未验证事项\n```\n")
        self.assert_issue("missing_required_heading", "--check-output", "outputs/code.md")

    def test_candidate_headings_do_not_apply_to_output(self):
        candidate = copy.deepcopy(self.rule)
        candidate.update(id="brief-candidate", family="candidate-family", status="candidate",
                         source="rules/candidate.md", required_headings=["不能强制的候选章节"])
        candidate.pop("evidence")
        self.write("rules/candidate.md", "尚未确认的想法。\n")
        self.registry([self.rule, candidate])
        self.write("outputs/good.md", "## 未验证事项\n尚未检查导出结果。\n")
        code, report = self.check("--check-output", "outputs/good.md")
        self.assertEqual(code, 0, report)

    def test_output_path_and_read_failures_keep_distinct_exit_codes(self):
        self.assert_issue("invalid_relative_path", "--check-output", "../outside.md")
        code, report = self.check("--check-output", "outputs/absent.md")
        self.assertEqual((code, report.get("status")), (2, "error"), report)

    def test_hidden_comment_does_not_satisfy_a_required_heading(self):
        self.write("outputs/comment.md", "# 项目简报\n\n<!--\n## 未验证事项\n-->\n只有草稿正文。\n")
        self.assert_issue("missing_required_heading", "--check-output", "outputs/comment.md")

    def test_boolean_schema_version_is_not_accepted_as_version_one(self):
        self.write("registry.json", json.dumps({"schema_version": True, "rules": [self.rule]}))
        self.assert_issue("invalid_registry")

    def test_deeply_nested_json_is_an_input_error_not_a_traceback(self):
        self.write("registry.json", "[" * 2000 + "0" + "]" * 2000)
        code, report = self.check()
        self.assertEqual((code, report.get("status")), (2, "error"), report)

    def test_nonstandard_json_numbers_are_input_errors(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token):
                self.write("registry.json", json.dumps({"schema_version": 1, "rules": [self.rule]})[:-1]
                           + ', "extra": ' + token + '}')
                code, report = self.check()
                self.assertEqual((code, report.get("status")), (2, "error"), report)


class DemoTests(unittest.TestCase):
    def test_demo_proves_all_four_outcomes_and_leaves_examples_unchanged(self):
        example = REPO / "examples" / "feedback-loop"
        before = {p.relative_to(example): p.read_bytes() for p in example.rglob("*") if p.is_file()}
        result = subprocess.run([sys.executable, "-B", str(REPO / "tools" / "demo.py")],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["all_assertions_passed"])
        self.assertEqual((report["passed"], report["expected_failures"]), (2, 2))
        expected = [
            ("baseline", 0, []),
            ("unconfirmed_promotion", 1, ["missing_active_evidence"]),
            ("missing_section", 1, ["missing_required_heading"]),
            ("confirmed_reuse", 0, []),
        ]
        actual = [(item["name"], item["actual_exit"], item["issue_codes"]) for item in report["cases"]]
        self.assertEqual(actual, expected)
        self.assertTrue(report["temporary_files_removed"])
        after = {p.relative_to(example): p.read_bytes() for p in example.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_demo_rejects_external_paths_without_touching_them(self):
        with tempfile.TemporaryDirectory(prefix="demo-external-test-") as directory:
            marker = Path(directory) / "keep.txt"
            marker.write_text("untouched", encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", str(REPO / "tools" / "demo.py"), directory],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertTrue(result.stdout.startswith("{"), "demo must return a JSON input error")
            self.assertEqual(json.loads(result.stdout)["status"], "error")
            self.assertEqual(marker.read_text(encoding="utf-8"), "untouched")


if __name__ == "__main__":
    unittest.main()
