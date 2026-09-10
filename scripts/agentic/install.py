#!/usr/bin/env python3
"""Preview or install workflow files without overwriting an existing project."""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from workflow import WorkflowError

ROOT = Path(__file__).resolve().parents[2]
PATHS = [
    ".agentic",
    ".github/ISSUE_TEMPLATE/change.yml",
    ".github/pull_request_template.md",
    ".github/agents/independent-reviewer.agent.md",
    ".github/copilot-instructions.md",
    ".github/workflows/agentic-quality.yml",
    ".github/workflows/copilot-review.yml",
    "scripts/agentic",
    "scripts/new-task.sh",
    "scripts/cleanup-task.sh",
    "tests/agentic",
    "docs/agent-workflow",
    "AGENTS.md",
]


def payload(source):
    result = []
    for relative in PATHS:
        path = source / relative
        for entry in sorted(path.rglob("*")) if path.is_dir() else [path]:
            if "__pycache__" not in entry.parts and entry.is_file():
                if entry.is_symlink():
                    raise WorkflowError(f"Source symlink is not supported: {entry}")
                result.append(entry.relative_to(source))
    return result


def install(source, target, apply=False):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target == source or target.is_relative_to(source):
        raise WorkflowError("Install into a separate repository or staging directory")
    files = payload(source)
    conflicts, pending, identical = [], [], []
    for relative in files:
        destination = target / relative
        if any(p.is_symlink() for p in [destination, *destination.parents] if p != target.parent):
            conflicts.append(str(relative) + " (symlink)")
        elif destination.exists():
            if destination.is_file() and destination.read_bytes() == (source / relative).read_bytes():
                identical.append(str(relative))
            else:
                conflicts.append(str(relative))
        else:
            pending.append(relative)
    manifest = target / ".agentic/template-origin.json"
    if manifest.is_symlink():
        conflicts.append(".agentic/template-origin.json (symlink)")
    result = {
        "target": str(target),
        "conflicts": conflicts,
        "identical": identical,
        "new_files": [str(p) for p in pending],
        "applied": False,
        "next": "Merge /memory/ and /.agentic-local/ into .gitignore; adapt AGENTS, domain rubric and project CI. See SETUP.md.",
    }
    if apply:
        if conflicts:
            raise WorkflowError(
                "Installation refused before any writes; reconcile these paths: " + ", ".join(conflicts)
            )
        for relative in pending:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, destination)
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source": "agentic-github-template",
                    "files": {str(p): hashlib.sha256((source / p).read_bytes()).hexdigest() for p in files},
                },
                indent=2,
            )
            + "\n"
        )
        result["applied"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(install(ROOT, args.target, args.apply), indent=2))
        return 0
    except (WorkflowError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
