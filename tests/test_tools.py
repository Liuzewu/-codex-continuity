"""Behavior tests use temporary, fabricated files only."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import runpy
import stat
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


class ToolTests(unittest.TestCase):
    def run_tool(self, tool, *args):
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools" / tool), *map(str, args)],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def test_audit_accepts_clean_text_and_does_not_change_it(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "README.md"
            p.write_text("# 公开方法\n\n所有示例均为虚构。\n", encoding="utf-8")
            before = (p.read_bytes(), p.stat().st_mtime_ns)
            r = self.run_tool("audit_public.py", d)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(json.loads(r.stdout)["issues"], [])
            self.assertEqual(before, (p.read_bytes(), p.stat().st_mtime_ns))

    def test_audit_detects_sensitive_patterns_without_echoing_values(self):
        values = [
            "gh" + "p_" + "a" * 36,
            "/Users/" + "sample-owner" + "/private-notes",
            "person" + "@" + "example.net",
            "198.51." + "100.9",
            "-----BEGIN " + "PRIVATE KEY-----",
            "abcd1234" + "-1234-5678-9012-abcdef123456",
        ]
        for value in values:
            with self.subTest(value_type=values.index(value)):
                with tempfile.TemporaryDirectory() as d:
                    (Path(d) / "notes.md").write_text(value, encoding="utf-8")
                    r = self.run_tool("audit_public.py", d)
                    self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
                    self.assertTrue(json.loads(r.stdout)["issues"])
                    self.assertNotIn(value, r.stdout + r.stderr)

    def test_audit_rejects_hidden_state_and_binary_files(self):
        for name, payload in [(".env", b"local-only"), (".env." + "local.md", b"local-only"),
                              (".private.md", b"local-only"),
                              ("auth.json", b"{}"), ("credentials.json", b"{}"), ("state.sqlite", b"SQLite"),
                              ("photo.png", b"\x89PNG\x00")]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                (Path(d) / name).write_bytes(payload)
                r = self.run_tool("audit_public.py", d)
                self.assertEqual(r.returncode, 1, r.stderr + r.stdout)

    @unittest.skipIf(os.name == "nt", "Creating symlinks may require Windows privileges")
    def test_tools_reject_symlinks_without_reading_the_target(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            left, right = base / "left", base / "right"
            left.mkdir()
            right.mkdir()
            (base / "outside.md").write_text("must remain outside", encoding="utf-8")
            (left / "link.md").symlink_to(base / "outside.md")
            a = self.run_tool("audit_public.py", left)
            self.assertEqual(a.returncode, 1, a.stderr + a.stdout)
            b = self.run_tool("compare_knowledge.py", left, right, "--include", "link.md")
            self.assertEqual(b.returncode, 2, b.stderr + b.stdout)

    def test_compare_reports_equal_content_without_changing_either_tree(self):
        with tempfile.TemporaryDirectory() as d:
            left, right = Path(d) / "left", Path(d) / "right"
            for root in (left, right):
                (root / "standards").mkdir(parents=True)
                (root / "standards" / "rules.md").write_bytes(b"# Rules\n")
            before = [(p.read_bytes(), p.stat().st_mtime_ns)
                      for root in (left, right) for p in root.rglob("*.md")]
            r = self.run_tool("compare_knowledge.py", left, right, "--include", "standards")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            report = json.loads(r.stdout)
            self.assertEqual(report["different"], [])
            self.assertEqual(report["matched_files"], 1)
            after = [(p.read_bytes(), p.stat().st_mtime_ns)
                     for root in (left, right) for p in root.rglob("*.md")]
            self.assertEqual(before, after)

    def test_compare_reports_changed_added_and_removed_files(self):
        with tempfile.TemporaryDirectory() as d:
            left, right = Path(d) / "left", Path(d) / "right"
            for root in (left, right):
                (root / "standards").mkdir(parents=True)
            (left / "standards/a.md").write_bytes(b"before\n")
            (right / "standards/a.md").write_bytes(b"after\r\n")
            (left / "standards/b.md").write_bytes(b"left\n")
            (right / "standards/c.md").write_bytes(b"right\n")
            r = self.run_tool("compare_knowledge.py", left, right, "--include", "standards")
            self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
            report = json.loads(r.stdout)
            self.assertEqual(report["different"], ["standards/a.md"])
            self.assertEqual(report["only_left"], ["standards/b.md"])
            self.assertEqual(report["only_right"], ["standards/c.md"])

    def test_compare_rejects_traversal_private_state_and_identical_roots(self):
        with tempfile.TemporaryDirectory() as d:
            left, right = Path(d) / "left", Path(d) / "right"
            left.mkdir()
            right.mkdir()
            for include in ("../outside", ".codex", ".git", "/absolute"):
                with self.subTest(include=include):
                    r = self.run_tool("compare_knowledge.py", left, right, "--include", include)
                    self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
            r = self.run_tool("compare_knowledge.py", left, left, "--include", "standards")
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)

    def test_compare_refuses_missing_allowlist_or_missing_on_both_sides(self):
        with tempfile.TemporaryDirectory() as d:
            left, right = Path(d) / "left", Path(d) / "right"
            left.mkdir()
            right.mkdir()
            a = self.run_tool("compare_knowledge.py", left, right)
            self.assertEqual(a.returncode, 2)
            b = self.run_tool("compare_knowledge.py", left, right, "--include", "not-present.md")
            self.assertEqual(b.returncode, 2, b.stderr + b.stdout)

    def test_compare_does_not_read_unselected_files(self):
        with tempfile.TemporaryDirectory() as d:
            left, right = Path(d) / "left", Path(d) / "right"
            for root in (left, right):
                root.mkdir()
                (root / "ROUTING.md").write_bytes(b"# Route\n")
                (root / "state.sqlite").write_bytes(b"not a document\x00")
            r = self.run_tool("compare_knowledge.py", left, right, "--include", "ROUTING.md")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertEqual(json.loads(r.stdout)["matched_files"], 1)

    def test_compare_refuses_two_empty_selected_directories(self):
        with tempfile.TemporaryDirectory() as d:
            left, right = Path(d) / "left", Path(d) / "right"
            for root in (left, right):
                (root / "standards").mkdir(parents=True)
            r = self.run_tool("compare_knowledge.py", left, right, "--include", "standards")
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)

    def test_compare_refuses_case_alias_of_same_directory(self):
        with tempfile.TemporaryDirectory() as d:
            left = Path(d) / "sample"
            left.mkdir()
            (left / "ROUTING.md").write_text("# Route\n", encoding="utf-8")
            alias = Path(d) / "SAMPLE"
            if not alias.exists() or not os.path.samefile(left, alias):
                self.skipTest("Filesystem is case-sensitive")
            r = self.run_tool("compare_knowledge.py", left, alias, "--include", "ROUTING.md")
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)

    def test_audit_does_not_silently_skip_unreadable_directories(self):
        module = runpy.run_path(str(ROOT / "tools/audit_public.py"))
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("os.scandir", side_effect=PermissionError("unreadable")):
                with self.assertRaises(OSError):
                    module["audit"](Path(d))

    def test_audit_does_not_classify_io_failure_as_a_complete_scan(self):
        module = runpy.run_path(str(ROOT / "tools/audit_public.py"))
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "README.md").write_text("# Safe\n", encoding="utf-8")
            with mock.patch.object(Path, "read_bytes", side_effect=OSError("read failure")):
                with self.assertRaises(OSError):
                    module["audit"](Path(d))

    @unittest.skipIf(os.name == "nt", "Creating symlinks may require Windows privileges")
    def test_compare_refuses_symlink_ancestor_even_when_leaf_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            left, right, outside = base / "left", base / "right", base / "outside"
            for root in (left, right, outside):
                root.mkdir()
            (left / "standards").symlink_to(outside, target_is_directory=True)
            (right / "standards").mkdir()
            (right / "standards/missing.md").write_bytes(b"# Rules\n")
            r = self.run_tool("compare_knowledge.py", left, right, "--include", "standards/missing.md")
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)

    def test_compare_preserves_line_ending_difference(self):
        with tempfile.TemporaryDirectory() as d:
            left, right = Path(d) / "left", Path(d) / "right"
            for root in (left, right):
                root.mkdir()
            (left / "rules.md").write_bytes(b"same text\n")
            (right / "rules.md").write_bytes(b"same text\r\n")
            r = self.run_tool("compare_knowledge.py", left, right, "--include", "rules.md")
            self.assertEqual(r.returncode, 1, r.stderr + r.stdout)
            self.assertEqual(json.loads(r.stdout)["different"], ["rules.md"])

    def test_audit_refuses_empty_exports(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_tool("audit_public.py", d)
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)

    def test_path_guards_recognize_windows_reparse_metadata(self):
        # Models the OS boundary unavailable on this platform, not the guard logic.
        entry = SimpleNamespace(lstat=lambda: SimpleNamespace(
            st_mode=stat.S_IFDIR, st_file_attributes=0x400))
        for tool in ("audit_public.py", "compare_knowledge.py"):
            with self.subTest(tool=tool):
                module = runpy.run_path(str(ROOT / "tools" / tool))
                with mock.patch.object(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, create=True):
                    self.assertTrue(module.get("is_link", lambda p: False)(entry))

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior requires Windows")
    def test_windows_directory_junction_is_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            left, right, outside = base / "left", base / "right", base / "outside"
            for p in (left, right, outside):
                p.mkdir()
            (outside / "rules.md").write_bytes(b"outside selected root\n")
            junction = left / "standards"
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode:
                self.skipTest("The runner could not create a directory junction")
            try:
                a = self.run_tool("audit_public.py", left)
                self.assertEqual(a.returncode, 1, a.stderr + a.stdout)
                b = self.run_tool("compare_knowledge.py", left, right, "--include", "standards")
                self.assertEqual(b.returncode, 2, b.stderr + b.stdout)
                self.assertEqual((outside / "rules.md").read_bytes(), b"outside selected root\n")
            finally:
                os.rmdir(junction)


if __name__ == "__main__":
    unittest.main()
