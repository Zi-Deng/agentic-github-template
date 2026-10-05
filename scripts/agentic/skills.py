"""Mirror `.agents/skills/*/SKILL.md` into `.claude/skills/` so Claude Code discovers the same skills."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from workflow import WorkflowError

SOURCE = ".agents/skills"
TARGET = ".claude/skills"


def refuse_symlink(path):
    if path.is_symlink():
        raise WorkflowError(f"Skill path must not be a symlink: {path}")
    return path


def sources(root):
    root = Path(root)
    base = refuse_symlink(root / SOURCE)
    if not base.is_dir():
        raise WorkflowError(f"Missing skill source directory: {base}")
    names = []
    for entry in sorted(base.iterdir()):
        refuse_symlink(entry)
        if not entry.is_dir():
            continue
        skill = refuse_symlink(entry / "SKILL.md")
        if skill.is_file():
            names.append(entry.name)
    if not names:
        raise WorkflowError(f"No skills found under {base}")
    return names


def report(root):
    root = Path(root)
    names = sources(root)
    target = refuse_symlink(root / TARGET)
    result = {"current": [], "missing": [], "drifted": [], "stale": [], "extra_files": []}
    for name in names:
        source = root / SOURCE / name / "SKILL.md"
        copy = target / name / "SKILL.md"
        refuse_symlink(target / name)
        refuse_symlink(copy)
        if not copy.is_file():
            result["missing"].append(name)
        elif copy.read_bytes() != source.read_bytes():
            result["drifted"].append(name)
        else:
            result["current"].append(name)
    if target.is_dir():
        for entry in sorted(target.iterdir()):
            refuse_symlink(entry)
            if not entry.is_dir():
                result["extra_files"].append(str(entry.relative_to(root)))
                continue
            if entry.name not in names:
                result["stale"].append(entry.name)
                continue
            for item in sorted(entry.rglob("*")):
                refuse_symlink(item)
                if item.is_file() and item.relative_to(entry) != Path("SKILL.md"):
                    result["extra_files"].append(str(item.relative_to(root)))
    return result


def write_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync(root, check=False):
    root = Path(root)
    observed = report(root)
    if check:
        if any(observed[key] for key in ("missing", "drifted", "stale", "extra_files")):
            raise WorkflowError(
                "Skill mirror is out of date; run `make sync-skills` and remove stale entries: "
                + json.dumps({k: v for k, v in observed.items() if k != "current"})
            )
        return {"checked": True, **observed}
    for name in observed["missing"] + observed["drifted"]:
        write_copy(root / SOURCE / name / "SKILL.md", root / TARGET / name / "SKILL.md")
    refreshed = report(root)
    if refreshed["stale"] or refreshed["extra_files"]:
        raise WorkflowError(
            "Mirror refreshed, but stale or extra entries remain for deliberate removal: "
            + json.dumps({"stale": refreshed["stale"], "extra_files": refreshed["extra_files"]})
        )
    return {"synced": observed["missing"] + observed["drifted"], **refreshed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(sync(args.root, check=args.check), indent=2))
        return 0
    except (WorkflowError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
