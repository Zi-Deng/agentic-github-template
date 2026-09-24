"""The Claude Code skill mirror must stay byte-identical to the Codex sources."""

import shutil
import tempfile
import unittest
from pathlib import Path

from test_workflow import SOURCE, workflow

# The shared fixture establishes the scripts import path.
# isort: split
import skills


class SkillMirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agentic-skills-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(SOURCE / ".agents/skills", self.root / ".agents/skills")
        self.names = skills.sources(self.root)

    def test_sync_creates_byte_identical_copies_and_only_skill_md(self):
        result = skills.sync(self.root)
        self.assertEqual(sorted(result["synced"]), self.names)
        for name in self.names:
            copy = self.root / ".claude/skills" / name / "SKILL.md"
            self.assertEqual(
                copy.read_bytes(), (self.root / ".agents/skills" / name / "SKILL.md").read_bytes()
            )
            self.assertEqual([item.name for item in copy.parent.iterdir()], ["SKILL.md"])
        self.assertEqual(skills.sync(self.root, check=True)["current"], self.names)

    def test_sync_is_idempotent_and_leaves_no_temporary_files(self):
        skills.sync(self.root)
        second = skills.sync(self.root)
        self.assertEqual(second["synced"], [])
        self.assertEqual(list((self.root / ".claude/skills").rglob(".pending-*")), [])

    def test_check_fails_on_drift_or_missing_copy(self):
        skills.sync(self.root)
        copy = self.root / ".claude/skills" / self.names[0] / "SKILL.md"
        copy.write_bytes(copy.read_bytes() + b"\n")
        with self.assertRaisesRegex(workflow.WorkflowError, "out of date"):
            skills.sync(self.root, check=True)
        self.assertEqual(skills.sync(self.root)["synced"], [self.names[0]])
        copy.unlink()
        with self.assertRaisesRegex(workflow.WorkflowError, "out of date"):
            skills.sync(self.root, check=True)

    def test_stale_and_extra_entries_are_reported_never_deleted(self):
        skills.sync(self.root)
        stale = self.root / ".claude/skills/old/SKILL.md"
        stale.parent.mkdir()
        stale.write_text("old skill")
        extra = self.root / ".claude/skills" / self.names[0] / "agents/openai.yaml"
        extra.parent.mkdir()
        extra.write_text("interface: {}\n")
        with self.assertRaisesRegex(workflow.WorkflowError, "out of date"):
            skills.sync(self.root, check=True)
        with self.assertRaisesRegex(workflow.WorkflowError, "stale or extra"):
            skills.sync(self.root)
        self.assertTrue(stale.exists())
        self.assertTrue(extra.exists())

    def test_symlinks_are_refused_in_source_and_mirror(self):
        skills.sync(self.root)
        link = self.root / ".claude/skills/linked"
        link.symlink_to(self.root / ".agents/skills" / self.names[0])
        with self.assertRaisesRegex(workflow.WorkflowError, "symlink"):
            skills.report(self.root)
        link.unlink()
        source_link = self.root / ".agents/skills/linked"
        source_link.symlink_to(self.root / ".agents/skills" / self.names[0])
        with self.assertRaisesRegex(workflow.WorkflowError, "symlink"):
            skills.sources(self.root)

    def test_sources_have_no_argument_placeholders(self):
        for name in self.names:
            text = (self.root / ".agents/skills" / name / "SKILL.md").read_text()
            self.assertNotRegex(text, r"\$(ARGUMENTS|[0-9])")
            self.assertNotIn("\narguments:", text)
