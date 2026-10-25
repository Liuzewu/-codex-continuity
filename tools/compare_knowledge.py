#!/usr/bin/env python3
"""Compare explicitly selected knowledge files. No network, copying, or writes."""

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath
import sys
import unicodedata


TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".toml"}
MAX_BYTES = 4 * 1024 * 1024
MAX_FILES = 10000
BLOCKED = {"private", "logs", "sessions", "credentials", "node_modules", "__pycache__"}


def is_link(path):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def validate_include(raw):
    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ":" in normalized or not path.parts:
        raise ValueError("include must be a relative document path")
    if any(part in {".", ".."} or part.startswith(".") or part.casefold() in BLOCKED
           for part in path.parts):
        raise ValueError("private, hidden, or parent paths are not allowed")
    return path


def reject_symlink_components(root, path):
    check = root
    for part in path.relative_to(root).parts:
        check /= part
        if is_link(check):
            raise ValueError("symlink in selected path")


def manifest(root, includes):
    result, portable_paths = {}, {}

    def inspect(path):
        relative = path.relative_to(root).as_posix()
        # Reject symlinks in every selected path component, before opening anything.
        reject_symlink_components(root, path)
        if path.is_dir():
            for child in sorted(path.iterdir()):
                validate_include(child.relative_to(root).as_posix())
                inspect(child)
            return
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            raise ValueError("selected path is not an approved text document")
        if relative in result:
            return
        if len(result) >= MAX_FILES or path.stat().st_size > MAX_BYTES:
            raise ValueError("selected content exceeds the bounded comparison limit")
        key = unicodedata.normalize("NFC", relative).casefold()
        if key in portable_paths and portable_paths[key] != relative:
            raise ValueError("case or Unicode collision in selected filenames")
        portable_paths[key] = relative
        payload = path.read_bytes()
        if b"\0" in payload:
            raise ValueError("binary data in selected document")
        payload.decode("utf-8")
        result[relative] = hashlib.sha256(payload).hexdigest()

    for include in includes:
        selected = root.joinpath(*include.parts)
        if is_link(selected) or selected.exists():
            inspect(selected)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--include", action="append", required=True, help="Relative file or directory; repeat to create an allowlist")
    args = parser.parse_args()
    try:
        if any(is_link(p) or not p.is_dir() for p in (args.left, args.right)):
            raise ValueError("both roots must be real directories")
        left, right = args.left.resolve(), args.right.resolve()
        if os.path.samefile(left, right):
            raise ValueError("comparison roots must be different")
        includes = [validate_include(raw) for raw in args.include]
        for include in includes:
            candidates = [root.joinpath(*include.parts) for root in (left, right)]
            for root, candidate in zip((left, right), candidates):
                reject_symlink_components(root, candidate)
            if not any(p.exists() or p.is_symlink() for p in candidates):
                raise ValueError("an include is absent on both sides")
        a, b = manifest(left, includes), manifest(right, includes)
        if not a and not b:
            raise ValueError("no selected files to compare")
        common = a.keys() & b.keys()
        different = sorted(p for p in common if a[p] != b[p])
        only_left, only_right = sorted(a.keys() - b.keys()), sorted(b.keys() - a.keys())
        report = {"different": different, "only_left": only_left, "only_right": only_right,
                  "matched_files": sum(a[p] == b[p] for p in common),
                  "scope": "selected UTF-8 files; SHA-256 byte equality, not task or sync verification"}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return int(bool(different or only_left or only_right))
    except (ValueError, OSError, UnicodeError):
        # Paths and contents can be private. Do not echo them through exception text.
        print(json.dumps({"error": "comparison refused; check roots, allowlist, symlinks, types, encoding, naming collisions, and size limits"}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
