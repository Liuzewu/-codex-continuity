#!/usr/bin/env python3
"""Read-only checks for a small knowledge bundle; no semantic verification."""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys


MAX_BYTES = 1024 * 1024
MAX_RULES = 200
TOKEN = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
EVIDENCE_TYPES = {
    "preference": "user_confirmation",
    "accepted_artifact": "acceptance",
    "verified_method": "reproducible_result",
    "project_fact": "dated_source",
}
KINDS = set(EVIDENCE_TYPES) | {"observation"}
STATUSES = {"candidate", "active", "retired"}
SCOPE_NOTE = "Structure only; evidence truth, human approval, model reading, and layout are not verified."


class InputError(Exception):
    """An input cannot be safely read. Details deliberately exclude file content."""


class RelativePathError(Exception):
    """A document reference is not a slash-separated relative path."""


def relative_parts(value):
    if not isinstance(value, str) or not value or any(c in value for c in "\\:\x00?#"):
        raise RelativePathError()
    parts = value.split("/")
    if any(not part or part.startswith(".") for part in parts):
        raise RelativePathError()
    return parts


def is_link(path):
    metadata = path.lstat()
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def read_document(root, relative):
    """Read a bounded regular UTF-8 file, refusing links inside the bundle."""
    path = root
    try:
        for part in relative_parts(relative):
            path /= part
            if is_link(path):
                raise InputError("link_refused")
        if not stat.S_ISREG(path.lstat().st_mode):
            raise InputError("unsupported_file")
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                 | getattr(os, "O_NONBLOCK", 0))
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
                raise InputError("unsupported_file")
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES or b"\0" in content:
            raise InputError("unsupported_file")
        return content.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise InputError("cannot_read_utf8_file") from exc


def prose_lines(text):
    """Ignore fenced code and HTML comments; this is not a Markdown renderer."""
    fence = None
    in_comment = False
    for line in text.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence is not None:
            if match:
                marks, tail = match.groups()
                if marks[0] == fence[0] and len(marks) >= fence[1] and not tail.strip():
                    fence = None
            continue
        if not in_comment and match:
            marks, tail = match.groups()
            fence = (marks[0], len(marks))
            continue
        visible = []
        while line:
            if in_comment:
                end = line.find("-->")
                if end < 0:
                    break
                line, in_comment = line[end + 3:], False
            else:
                start = line.find("<!--")
                if start < 0:
                    visible.append(line)
                    break
                visible.append(line[:start])
                line, in_comment = line[start + 4:], True
        yield "".join(visible)


def check_bundle(bundle, output=None):
    """Return a JSON-serializable report and exit status without changing files."""
    issues = []

    def issue(code, index=None):
        item = {"code": code}
        if index is not None:
            item["rule_index"] = index
        issues.append(item)

    def document(value, index):
        try:
            relative_parts(value)
        except RelativePathError:
            issue("invalid_relative_path", index)
            return False
        read_document(root, value)
        return True

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                issue("duplicate_json_key")
            result[key] = value
        return result

    def reject_constant(value):
        raise InputError("invalid_json")

    try:
        given_root = Path(bundle)
        if is_link(given_root) or not given_root.is_dir():
            raise InputError("invalid_bundle")
        root = given_root.resolve(strict=True)
        try:
            registry = json.loads(read_document(root, "registry.json"),
                                  object_pairs_hook=unique_keys, parse_constant=reject_constant)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise InputError("invalid_json") from exc
        routing = read_document(root, "ROUTING.md")
        if (not isinstance(registry, dict) or type(registry.get("schema_version")) is not int
                or registry.get("schema_version") != 1
                or not isinstance(registry.get("rules"), list)
                or not 1 <= len(registry["rules"]) <= MAX_RULES):
            issue("invalid_registry")
            rules = []
        else:
            rules = registry["rules"]

        by_id, by_source, active_families = {}, {}, {}
        active_count = 0
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                issue("invalid_rule", index)
                continue
            valid_tokens = True
            for field in ("id", "scope", "family"):
                value = rule.get(field)
                if not isinstance(value, str) or not TOKEN.fullmatch(value):
                    issue("invalid_" + field, index)
                    valid_tokens = False
            kind = rule.get("kind")
            status = rule.get("status")
            if not isinstance(kind, str) or kind not in KINDS:
                issue("invalid_kind", index)
            if not isinstance(status, str) or status not in STATUSES:
                issue("invalid_status", index)
            if valid_tokens:
                if rule["id"] in by_id:
                    issue("duplicate_id", index)
                else:
                    by_id[rule["id"]] = (index, rule)
            if document(rule.get("source"), index):
                by_source.setdefault(rule["source"], []).append((index, rule))
            replacements = rule.get("replaces", [])
            if not isinstance(replacements, list) or any(
                    not isinstance(item, str) or not TOKEN.fullmatch(item) for item in replacements):
                issue("invalid_replacements", index)
            headings = rule.get("required_headings", [])
            if (not isinstance(headings, list) or any(
                    not isinstance(item, str) or not item.strip() or "\n" in item for item in headings)):
                issue("invalid_required_headings", index)
            evidence = rule.get("evidence")
            if evidence is not None:
                if not isinstance(evidence, dict):
                    issue("invalid_evidence", index)
                elif "path" in evidence:
                    document(evidence["path"], index)
            if rule.get("status") == "active":
                active_count += 1
                if valid_tokens:
                    family = (rule["scope"], rule["family"])
                    if family in active_families:
                        issue("multiple_active", index)
                    active_families[family] = index
                if not isinstance(evidence, dict) or not evidence.get("path") or not evidence.get("type"):
                    issue("missing_active_evidence", index)
                elif not isinstance(kind, str) or evidence.get("type") != EVIDENCE_TYPES.get(kind):
                    issue("evidence_kind_mismatch", index)
                if rule.get("kind") == "observation":
                    issue("unclassified_active_observation", index)

        graph = {}
        for rule_id, (index, rule) in by_id.items():
            replacements = rule.get("replaces", [])
            if not isinstance(replacements, list):
                continue
            graph[rule_id] = []
            for target in replacements:
                if not isinstance(target, str):
                    continue
                if target not in by_id:
                    issue("unknown_replacement", index)
                else:
                    graph[rule_id].append(target)
        visiting, visited = set(), set()

        def visit(rule_id):
            if rule_id in visiting:
                issue("replacement_cycle", by_id[rule_id][0])
                return
            if rule_id in visited:
                return
            visiting.add(rule_id)
            for target in graph.get(rule_id, []):
                visit(target)
            visiting.remove(rule_id)
            visited.add(rule_id)

        for rule_id in graph:
            visit(rule_id)

        routed = set()
        for line in prose_lines(routing):
            for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", line):
                try:
                    relative_parts(target)
                except RelativePathError:
                    issue("invalid_route_path")
                    continue
                if target not in by_source:
                    issue("unknown_route")
                    continue
                for index, rule in by_source[target]:
                    if rule.get("status") != "active":
                        issue("route_not_active", index)
                    else:
                        routed.add(index)
        for index, rule in enumerate(rules):
            if isinstance(rule, dict) and rule.get("status") == "active" and index not in routed:
                issue("active_not_routed", index)

        output_checked = False
        if output is not None:
            try:
                relative_parts(output)
            except RelativePathError:
                issue("invalid_relative_path")
            else:
                output_text = read_document(root, output)
                present = set()
                for line in prose_lines(output_text):
                    match = re.match(r"^ {0,3}#{1,6}[ \t]+(.+?)\s*$", line)
                    if match:
                        present.add(re.sub(r"[ \t]+#+[ \t]*$", "", match.group(1)).strip())
                for index, rule in enumerate(rules):
                    if not isinstance(rule, dict) or rule.get("status") != "active":
                        continue
                    headings = rule.get("required_headings", [])
                    if isinstance(headings, list):
                        for heading in headings:
                            if isinstance(heading, str) and heading not in present:
                                issue("missing_required_heading", index)
                output_checked = True

        report = {"status": "failed" if issues else "passed", "issues": issues,
                  "checked_rules": len(rules), "active_rules": active_count,
                  "output_checked": output_checked, "scope": SCOPE_NOTE}
        return report, int(bool(issues))
    except (InputError, OSError, RelativePathError) as exc:
        return {"status": "error", "error": str(exc) if isinstance(exc, InputError) else "invalid_input",
                "scope": SCOPE_NOTE}, 2


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError("invalid_arguments")


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("bundle", help="Directory containing registry.json and ROUTING.md")
    parser.add_argument("--check-output", metavar="RELATIVEFILE", help="Check required ATX headings in this bundle file")
    try:
        args = parser.parse_args(argv)
        report, code = check_bundle(args.bundle, args.check_output)
    except InputError as exc:
        report, code = {"status": "error", "error": str(exc), "scope": SCOPE_NOTE}, 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
