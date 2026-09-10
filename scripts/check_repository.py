#!/usr/bin/env python3
"""Check template configuration and local Markdown links during development."""

import re
from pathlib import Path
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    for path in (ROOT / ".github").rglob("*.yml"):
        # BaseLoader retains the Actions key `on` rather than YAML 1.1's boolean True.
        value = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        assert isinstance(value, dict), path
        if path.parent.name == "workflows":
            assert "on" in value and "jobs" in value, path
            assert "pull_request_target" not in value["on"], path
            assert value["permissions"]["contents"] == "read", path
            for job in value["jobs"].values():
                assert "timeout-minutes" in job, path
                for step in job.get("steps", []):
                    if "uses" in step:
                        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"]), step["uses"]
    paths = [ROOT / "README.md", ROOT / "AGENTS.md", *(ROOT / "docs").rglob("*.md")]
    errors = []
    for path in paths:
        text = path.read_text()
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            if re.match(r"[a-z]+://|mailto:|#", link):
                continue
            target = unquote(link.split("#", 1)[0].strip("<>"))
            if target and not (path.parent / target).exists():
                errors.append(f"{path.relative_to(ROOT)}: missing {target}")
    if errors:
        raise SystemExit("\n".join(errors))
    print("Workflow YAML and local Markdown links validated")


if __name__ == "__main__":
    main()
