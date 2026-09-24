#!/usr/bin/env python3
"""Check template configuration and local Markdown links during development."""

import re
import sys
from pathlib import Path
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/agentic"))
import skills  # noqa: E402

# Claude Code reads AGENTS.md only while none of these instruction files exists, and a
# `claude -p` executor would run shipped hooks or MCP servers without a trust dialog.
FORBIDDEN_FILES = ["CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md", ".claude/settings.json", ".mcp.json"]

SKILLS = {
    "agentic-workflow",
    "agentic-capture",
    "agentic-plan",
    "agentic-prepare",
    "agentic-implement",
    "agentic-review",
    "agentic-repair",
    "agentic-finish",
}


def validate_skills(root):
    for name in sorted(SKILLS):
        directory = root / ".agents/skills" / name
        skill = directory / "SKILL.md"
        metadata = directory / "agents/openai.yaml"
        for path in (directory, skill, metadata):
            assert not path.is_symlink(), path
        text = skill.read_text()
        assert text.startswith("---\n"), skill
        front, separator, body = text[4:].partition("\n---\n")
        assert separator and body.strip(), skill
        fields = yaml.safe_load(front)
        assert isinstance(fields, dict) and fields.get("name") == name, skill
        assert isinstance(fields.get("description"), str) and fields["description"].strip(), skill
        invocation = yaml.safe_load(metadata.read_text())
        assert isinstance(invocation, dict), metadata
        interface = invocation.get("interface", {})
        for key in ("display_name", "short_description", "default_prompt"):
            assert isinstance(interface.get(key), str) and interface[key].strip(), metadata
        assert re.search(rf"\${re.escape(name)}(?![a-z0-9-])", interface["default_prompt"]), metadata
        policy = invocation.get("policy", {})
        assert isinstance(policy, dict), metadata
        assert policy.get("allow_implicit_invocation", True) is True, metadata


def validate_mirror(root):
    observed = skills.sync(root, check=True)
    assert sorted(observed["current"]) == sorted(SKILLS), observed
    for name in SKILLS:
        source = root / ".agents/skills" / name / "SKILL.md"
        copy = root / ".claude/skills" / name / "SKILL.md"
        assert not copy.is_symlink() and not copy.parent.is_symlink(), copy
        assert copy.read_bytes() == source.read_bytes(), copy
    for relative in FORBIDDEN_FILES:
        assert not (root / relative).exists(), f"{relative} must not be shipped"
    ignore_rules = (root / ".gitignore").read_text().splitlines()
    assert "/.claude/settings.local.json" in ignore_rules, (
        ".gitignore must ignore /.claude/settings.local.json"
    )


def main():
    validate_skills(ROOT)
    validate_mirror(ROOT)
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
    paths = [
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / ".agents/skills").rglob("*.md"),
        *(ROOT / ".claude/skills").rglob("*.md"),
    ]
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
