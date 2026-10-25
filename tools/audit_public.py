#!/usr/bin/env python3
"""Read-only heuristic checks for a deliberately prepared public text export.

This does not redact files, inspect Git history, or certify that data is public.
"""

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys


TEXT_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".toml", ".txt"}
TEXT_NAMES = {"LICENSE", ".gitignore", ".gitattributes", ".editorconfig"}
MAX_BYTES = 2 * 1024 * 1024
PATTERNS = {
    "credential_token": re.compile(
        r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})"),
    "private_key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "personal_home": re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+(?:/|\b)|[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+", re.I),
    "email": re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "device_or_thread_id": re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|remote-control:env_[0-9a-f]+", re.I),
    "local_hostname": re.compile(r"\b[A-Za-z0-9_-]+\.local\b"),
}
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}(?![\w:])", re.I)


def is_link(path):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def text_findings(text):
    findings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                if rule == "email" and match.group().lower().endswith(("@example.invalid", "@example.com", "@example.org")):
                    continue
                findings.append({"line": line_number, "rule": rule})
                break
        for pattern in (IPV4, IPV6):
            for match in pattern.finditer(line):
                try:
                    ipaddress.ip_address(match.group())
                except ValueError:
                    continue
                findings.append({"line": line_number, "rule": "network_address"})
                break
    return findings


def audit(root):
    issues, checked = [], 0
    def cannot_walk(error):
        raise error

    for current, dirs, files in os.walk(root, followlinks=False, onerror=cannot_walk):
        dirs.sort()
        files.sort()
        for name in list(dirs):
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            if is_link(path):
                issues.append({"path": rel, "rule": "symlink"})
                dirs.remove(name)
            elif name == ".git":
                dirs.remove(name)  # Git history is intentionally outside this tool's scope.
            elif (name.startswith(".") and name != ".github") or name in {"__pycache__", "node_modules", "private", "sessions", "logs"} or name.endswith(".nosync"):
                issues.append({"path": rel, "rule": "private_or_generated_directory"})
                dirs.remove(name)
        for name in files:
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            if is_link(path):
                issues.append({"path": rel, "rule": "symlink"})
                continue
            if not path.is_file():
                issues.append({"path": rel, "rule": "not_regular_file"})
                continue
            if ((name.startswith(".") and name not in TEXT_NAMES)
                    or name.casefold() in {"auth.json", "auth.toml", "credentials.json", "credentials.toml"}):
                issues.append({"path": rel, "rule": "private_state_filename"})
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES and name not in TEXT_NAMES:
                issues.append({"path": rel, "rule": "unapproved_file_type"})
                continue
            try:
                if path.stat().st_size > MAX_BYTES:
                    issues.append({"path": rel, "rule": "file_too_large"})
                    continue
                payload = path.read_bytes()
                if b"\0" in payload:
                    raise UnicodeError("binary content")
                content = payload.decode("utf-8")
            except UnicodeError:
                issues.append({"path": rel, "rule": "unreadable_or_non_utf8"})
                continue
            checked += 1
            for finding in text_findings(rel):
                issues.append({"path": "[redacted filename]", "rule": "sensitive_filename_" + finding["rule"]})
            for finding in text_findings(content):
                issues.append({"path": rel, **finding})
    return {"checked_files": checked, "issues": issues,
            "scope": "working-tree text only; excludes .git; manual review required"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Prepared public export, not your home or application data directory")
    args = parser.parse_args()
    try:
        if is_link(args.root) or not args.root.is_dir():
            print(json.dumps({"error": "root must be a real directory"}))
            return 2
        result = audit(args.root.resolve())
    except OSError:
        print(json.dumps({"error": "directory could not be inspected"}))
        return 2
    if result["checked_files"] == 0 and not result["issues"]:
        print(json.dumps({"error": "no approved text files were inspected"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(result["issues"]))


if __name__ == "__main__":
    sys.exit(main())
