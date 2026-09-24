"""Profile resolution, local selection and family gates with real temporary Git repositories."""

import contextlib
import io
import json
import os
import subprocess
from unittest.mock import patch

from test_workflow import GitFixture, workflow

# The shared fixture establishes the scripts import path.
# isort: split
import profiles


class ProfileTests(GitFixture):
    def setUp(self):
        super().setUp()
        self.cfg = workflow.configuration(self.root)

    def same_family_config(self):
        cfg = workflow.configuration(self.root)
        cfg["profiles"]["claude-claude"] = {
            "implementer": {"backend": "claude", "model": "claude-fable-5-1"},
            "reviewer": {"backend": "copilot", "model": "claude-opus-5"},
        }
        workflow.write_json(self.root / ".agentic/config.json", cfg)

    def test_precedence_is_environment_then_local_file_then_default(self):
        default = profiles.active_profile(self.repo)
        self.assertEqual((default["name"], default["source"]), ("astra-claude", "default"))
        self.assertEqual(default["implementer"]["backend"], "codex")
        profiles.use_profile(self.repo, "fable-gpt")
        saved = json.loads((self.root / ".agentic-local/profile.json").read_text())
        self.assertEqual(saved["profile"], "fable-gpt")
        local = profiles.active_profile(self.repo)
        self.assertEqual((local["name"], local["source"]), ("fable-gpt", "local"))
        self.assertEqual(local["implementer"]["backend"], "claude")
        self.assertEqual(local["reviewer"]["model"], "gpt-6-astra")
        self.assertFalse(local["same_family"])
        with patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"}):
            env = profiles.active_profile(self.repo)
        self.assertEqual((env["name"], env["source"]), ("astra-claude", "env"))
        self.assertTrue(profiles.clear_profile(self.repo)["cleared"])
        self.assertEqual(profiles.active_profile(self.repo)["source"], "default")
        self.assertFalse(profiles.clear_profile(self.repo)["cleared"])

    def test_unknown_or_malformed_selections_fail_closed(self):
        with patch.dict(os.environ, {profiles.ENV_NAME: "bogus"}):
            with self.assertRaisesRegex(workflow.WorkflowError, "not declared"):
                profiles.active_profile(self.repo)
        with self.assertRaisesRegex(workflow.WorkflowError, "not declared"):
            profiles.use_profile(self.repo, "bogus")
        selection = self.root / ".agentic-local/profile.json"
        selection.parent.mkdir(exist_ok=True)
        selection.write_text(json.dumps({"schema_version": 1, "profile": "gone", "allow_same_family": False}))
        with self.assertRaisesRegex(workflow.WorkflowError, "not declared"):
            profiles.active_profile(self.repo)
        selection.write_text("{not json")
        with self.assertRaisesRegex(workflow.WorkflowError, "malformed"):
            profiles.active_profile(self.repo)

    def test_schema_one_shim_and_schema_two_rules(self):
        legacy = profiles.load_profiles(
            {"schema_version": 1, "openai_model": "gpt-6-astra", "copilot_model": "claude-opus-5"}
        )
        self.assertEqual(legacy["default_profile"], "legacy")
        self.assertEqual(legacy["profiles"]["legacy"]["implementer"]["backend"], "codex")
        self.assertEqual(legacy["profiles"]["legacy"]["reviewer"]["model"], "claude-opus-5")
        with self.assertRaisesRegex(workflow.WorkflowError, "migrate"):
            profiles.load_profiles({"schema_version": 1})
        with self.assertRaisesRegex(workflow.WorkflowError, "remove openai_model"):
            profiles.load_profiles({**self.cfg, "openai_model": "gpt-6-astra"})
        with self.assertRaisesRegex(workflow.WorkflowError, "default_profile"):
            profiles.load_profiles({**self.cfg, "default_profile": "missing"})
        with self.assertRaisesRegex(workflow.WorkflowError, "expected 1 or 2"):
            profiles.load_profiles({"schema_version": 3})

    def test_floating_models_wrong_families_and_bypass_defaults_are_refused(self):
        implementer_cases = [
            ({"backend": "claude", "model": "opus"}, "explicit ID"),
            ({"backend": "claude", "model": "gpt-6-astra"}, "explicit ID"),
            ({"backend": "codex", "model": "claude-fable-5-1"}, "explicit ID"),
            ({"backend": "claude", "model": "claude-fable-5-1", "permission_policy": "bypass"}, "per-launch"),
            ({"backend": "claude", "model": "claude-fable-5-1", "setting_sources": []}, "setting_sources"),
            ({"backend": "claude", "model": "claude-fable-5-1", "bogus": 1}, "unknown implementer keys"),
            ({"backend": "codex", "model": "gpt-6-astra", "sandbox": None}, "unknown implementer keys"),
            ({"backend": "other", "model": "gpt-6-astra"}, "codex or claude"),
        ]
        reviewer = {"backend": "copilot", "model": "claude-opus-5"}
        for implementer, pattern in implementer_cases:
            with self.subTest(implementer=implementer):
                cfg = {
                    **self.cfg,
                    "default_profile": "x",
                    "profiles": {"x": {"implementer": implementer, "reviewer": reviewer}},
                }
                with self.assertRaisesRegex(workflow.WorkflowError, pattern):
                    profiles.load_profiles(cfg)
        reviewer_cases = [
            ({"backend": "copilot", "model": "auto"}, "explicit ID"),
            ({"backend": "claude", "model": "claude-opus-5"}, "must be copilot"),
            ({"backend": "copilot", "model": "gpt-6-astra", "max_ai_credits": 0}, "positive integer"),
        ]
        implementer = {"backend": "codex", "model": "gpt-6-astra"}
        for reviewer, pattern in reviewer_cases:
            with self.subTest(reviewer=reviewer):
                cfg = {
                    **self.cfg,
                    "default_profile": "x",
                    "profiles": {"x": {"implementer": implementer, "reviewer": reviewer}},
                }
                with self.assertRaisesRegex(workflow.WorkflowError, pattern):
                    profiles.load_profiles(cfg)

    def test_same_family_requires_a_recorded_allowance_and_warns(self):
        self.same_family_config()
        with self.assertRaisesRegex(workflow.WorkflowError, "same-family"):
            profiles.use_profile(self.repo, "claude-claude")
        with patch.dict(os.environ, {profiles.ENV_NAME: "claude-claude"}):
            with self.assertRaisesRegex(workflow.WorkflowError, "same-family"):
                profiles.active_profile(self.repo)
        record = profiles.use_profile(self.repo, "claude-claude", allow_same_family=True, reason="fixture")
        self.assertTrue(record["allow_same_family"])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            active = profiles.active_profile(self.repo)
        self.assertTrue(active["same_family"])
        self.assertTrue(active["allow_same_family"])
        self.assertIn("warning: profile 'claude-claude'", stderr.getvalue())
        # The environment cannot select a same-family profile that the local record does not name.
        profiles.use_profile(self.repo, "astra-claude")
        with patch.dict(os.environ, {profiles.ENV_NAME: "claude-claude"}):
            with self.assertRaisesRegex(workflow.WorkflowError, "same-family"):
                profiles.active_profile(self.repo)

    def test_managed_executors_cannot_change_the_active_profile(self):
        with patch.dict(os.environ, {"AGENTIC_EXECUTOR_ROLE": "implement"}):
            with self.assertRaisesRegex(workflow.WorkflowError, "Managed executors"):
                profiles.use_profile(self.repo, "fable-gpt")
            with self.assertRaisesRegex(workflow.WorkflowError, "Managed executors"):
                profiles.clear_profile(self.repo)
        self.assertFalse((self.root / ".agentic-local/profile.json").exists())

    def test_pinned_executor_treats_records_without_a_pin_as_codex(self):
        legacy = profiles.pinned_executor({"uuid": None, "runs": []})
        self.assertEqual(
            (legacy["backend"], legacy["model"], legacy["legacy"]), ("codex", "gpt-6-astra", True)
        )
        pinned = profiles.pinned_executor(
            {"backend": "claude", "model": "claude-fable-5-1", "profile": "fable-gpt"}
        )
        self.assertEqual(
            (pinned["backend"], pinned["profile"], pinned["legacy"]), ("claude", "fable-gpt", False)
        )
        with self.assertRaisesRegex(workflow.WorkflowError, "unsupported backend"):
            profiles.pinned_executor({"backend": "other", "model": "gpt-6-astra"})

    def test_listing_and_show_report_state_without_raising(self):
        with patch.dict(os.environ, {profiles.ENV_NAME: "bogus"}):
            view = profiles.listing(self.repo)
        self.assertIsNone(view["active"])
        self.assertIn("not declared", view["active_error"])
        self.assertEqual(set(view["profiles"]), {"astra-claude", "fable-gpt"})
        shown = profiles.show(self.repo)
        self.assertEqual(shown["name"], "astra-claude")
        self.assertIsNone(shown["local_file"])
        self.assertEqual(set(shown["tools"]), {"codex", "claude", "copilot"})

    def test_claude_login_probe_parses_json_and_fails_closed(self):
        outcomes = [
            (subprocess.CompletedProcess(["claude"], 0, '{"loggedIn": true}', ""), True),
            (subprocess.CompletedProcess(["claude"], 0, '{"loggedIn": false}', ""), False),
            (subprocess.CompletedProcess(["claude"], 1, "", "not logged in"), False),
            (subprocess.CompletedProcess(["claude"], 0, "not json", ""), False),
        ]
        for completed, expected in outcomes:
            with self.subTest(stdout=completed.stdout, code=completed.returncode):
                with patch.object(workflow, "run", return_value=completed):
                    self.assertIs(workflow.claude_logged_in(), expected)
