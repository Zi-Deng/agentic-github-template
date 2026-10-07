#!/usr/bin/env python3
"""Check template configuration, workflows, skills and local Markdown links during development."""

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/agentic"))
import profiles  # noqa: E402
import skills  # noqa: E402
from workflow import configuration  # noqa: E402

# Claude Code reads AGENTS.md only while none of these instruction files exists, and a
# `claude -p` executor would run shipped hooks or MCP servers without a trust dialog.
# Only tracked files count: an adopter may keep an ignored, product-only CLAUDE.md on disk.
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
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", *FORBIDDEN_FILES],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert not tracked, f"{', '.join(tracked)} must not be shipped"
    ignore_rules = (root / ".gitignore").read_text().splitlines()
    assert "/.claude/settings.local.json" in ignore_rules, (
        ".gitignore must ignore /.claude/settings.local.json"
    )


def validate_configuration(root):
    config = configuration(root)
    declared = profiles.load_profiles(config)
    assert declared["config_schema"] == 3 and not declared["shimmed"], "Ship schema 3 profiles"
    hosted = declared["hosted_profile"]
    assert hosted and declared["profiles"][hosted]["reviewer"]["backend"] == "copilot", (
        "hosted_profile must name a profile whose reviewer backend is copilot"
    )
    # Native login stores are per machine: the committed configuration never names one.
    assert config["claude_review_login_root"] is None, (
        "Use the local claude-login-root override, not configuration"
    )
    for name, profile in declared["profiles"].items():
        assert profile["reviewer"].get("login_root") is None, f"Profile {name!r} must not commit a login path"
    schema = json.loads((root / ".agentic/schemas/executor-result.json").read_text())
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"]) == {"status", "summary", "checks", "blockers"}
    assert schema["properties"]["status"] == {
        "type": "string",
        "enum": ["completed", "checkpoint", "blocked"],
    }
    assert schema["properties"]["summary"] == {"type": "string"}
    for key in ("checks", "blockers"):
        assert schema["properties"][key] == {"type": "array", "items": {"type": "string"}}
    for name in ("new-task.sh", "cleanup-task.sh", "finish-task.sh"):
        wrapper = root / "scripts" / name
        assert wrapper.is_file() and wrapper.stat().st_mode & 0o111 == 0o111, wrapper
    return config


def validate_workflows(root, required):
    observed_jobs = []
    for path in (root / ".github").rglob("*.yml"):
        # BaseLoader retains the Actions key `on` rather than YAML 1.1's boolean True.
        value = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        assert isinstance(value, dict), path
        if path.parent.name != "workflows":
            continue
        assert "on" in value and "jobs" in value, path
        assert "pull_request_target" not in value["on"], path
        assert value["permissions"]["contents"] == "read", path
        for name, job in value["jobs"].items():
            assert "timeout-minutes" in job, path
            steps = job.get("steps", [])
            receipts = [step for step in steps if "ci_evidence.py" in step.get("run", "")]
            uploads = [
                step
                for step in steps
                if step.get("uses", "").startswith("actions/upload-artifact@")
                and f"validation-{name}-" in str(step.get("with", {}).get("name", ""))
            ]
            for step in receipts:
                assert f"--check {name} " in step["run"] and step.get("if") == "always()", (path, name)
            if receipts:
                assert len(receipts) == 1 and len(uploads) == 1, (path, name)
                assert uploads[0].get("if") == "always()", (path, name)
            if name in required:
                observed_jobs.append(name)
                assert "pull_request" in value["on"] and "push" in value["on"], path
                assert value["on"]["push"] == {"branches": ["main"]}, path
                assert "if" not in job and "continue-on-error" not in job, path
                assert job.get("name", name) == name, path
                assert receipts, (path, name)
            for step in job.get("steps", []):
                if "uses" in step:
                    assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"]), step["uses"]
                    if step["uses"].startswith("actions/checkout@"):
                        assert step["with"]["persist-credentials"] == "false", path
    assert sorted(observed_jobs) == sorted(required), "Required check names must match unique CI jobs"
    hosted = (root / ".github/workflows/copilot-review.yml").read_text()
    assert "profile:" in hosted and "AGENTIC_PROFILE" in hosted, "hosted review must accept a profile input"
    assert "register-reviewer copilot" in hosted, "hosted review must register the pinned Copilot binary"
    assert "--require-reviewer-backend copilot" in hosted, "hosted review is Copilot-only"
    assert "review.py qualify" in hosted, "hosted review must end with the coverage qualification gate"
    assert "always() && inputs.publish" in hosted, "hosted publication must also publish INCOMPLETE reports"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in hosted and "claude-review-token" not in hosted


def validate_links(root):
    paths = [
        root / "README.md",
        root / "AGENTS.md",
        *(root / "docs").rglob("*.md"),
        *(root / ".agents/skills").rglob("*.md"),
        *(root / ".claude/skills").rglob("*.md"),
    ]
    errors = []
    for path in paths:
        text = path.read_text()
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            if re.match(r"[a-z]+://|mailto:|#", link):
                continue
            target = unquote(link.split("#", 1)[0].strip("<>"))
            if target and not (path.parent / target).exists():
                errors.append(f"{path.relative_to(root)}: missing {target}")
    if errors:
        raise SystemExit("\n".join(errors))


def main():
    validate_skills(ROOT)
    validate_mirror(ROOT)
    config = validate_configuration(ROOT)
    validate_workflows(ROOT, config["required_checks"])
    validate_links(ROOT)
    print(
        "Skills, mirror, schema 3 configuration, hosted profile, CI check names, workflow YAML and links validated"
    )


if __name__ == "__main__":
    main()
