#!/usr/bin/env python3
"""Run the fictional feedback loop in a disposable directory; no external paths."""

import json
from pathlib import Path
import sys
import tempfile

# Importing the sibling checker must not create a cache in the repository.
sys.dont_write_bytecode = True
from check_knowledge import InputError, check_bundle, read_document  # noqa: E402


FIXTURES = (
    "registry.json", "ROUTING.md", "rules/brief-basics.md", "rules/uncertainty.md",
    "evidence/baseline-acceptance.md", "evidence/uncertainty-confirmation.md",
    "outputs/complete.md", "outputs/missing-uncertainty.md",
)


def run_demo():
    repo = Path(__file__).resolve().parents[1]
    cases = []

    def record(name, root, output, expected_exit, expected_issues):
        report, code = check_bundle(root, output)
        codes = sorted(item["code"] for item in report.get("issues", []))
        cases.append({"name": name, "expected_exit": expected_exit, "actual_exit": code,
                      "issue_codes": codes,
                      "assertion_passed": code == expected_exit and codes == expected_issues})

    with tempfile.TemporaryDirectory(prefix="knowledge-demo-") as temporary:
        root = Path(temporary)
        for relative in FIXTURES:
            content = read_document(repo, "examples/feedback-loop/" + relative)
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")

        record("baseline", root, "outputs/complete.md", 0, [])
        registry_path = root / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        candidate = next(rule for rule in registry["rules"] if rule["id"] == "brief-uncertainty")
        candidate["status"] = "active"
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
        routing_path = root / "ROUTING.md"
        routing_path.write_text(routing_path.read_text(encoding="utf-8")
                                + "\n[未验证事项](rules/uncertainty.md)\n", encoding="utf-8")
        record("unconfirmed_promotion", root, "outputs/complete.md", 1, ["missing_active_evidence"])

        candidate["evidence"] = {"type": "user_confirmation", "path": "evidence/uncertainty-confirmation.md"}
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
        record("missing_section", root, "outputs/missing-uncertainty.md", 1, ["missing_required_heading"])
        record("confirmed_reuse", root, "outputs/complete.md", 0, [])

    removed = not root.exists()
    passed = sum(item["assertion_passed"] and item["expected_exit"] == 0 for item in cases)
    expected_failures = sum(item["assertion_passed"] and item["expected_exit"] == 1 for item in cases)
    success = all(item["assertion_passed"] for item in cases) and removed
    return {"status": "passed" if success else "failed", "all_assertions_passed": success,
            "passed": passed, "expected_failures": expected_failures,
            "temporary_files_removed": removed, "cases": cases,
            "scope": "Fictional structural demo only; no real approval, model execution, or synchronization."}, 0 if success else 1


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        report, code = {"status": "error", "error": "demo_accepts_no_arguments"}, 2
    else:
        try:
            report, code = run_demo()
        except (InputError, OSError, ValueError, KeyError, StopIteration):
            report, code = {"status": "error", "error": "demo_fixture_or_temporary_directory_error"}, 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
