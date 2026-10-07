"""The per-machine native login-store override: precedence, recording, refusal and the CLI."""

import contextlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

from test_workflow import GitFixture, workflow

# isort: split
import claude_native_auth
import profiles


class LoginRootOverrideTests(GitFixture):
    def setUp(self):
        super().setUp()
        self.cfg = workflow.configuration(self.root)
        self.env = patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        previous = os.getcwd()
        os.chdir(self.root)
        try:
            with (
                patch("sys.argv", ["workflow.py", *argv]),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                code = workflow.main()
        finally:
            os.chdir(previous)
        return code, out.getvalue(), err.getvalue()

    def test_precedence_local_file_then_profile_then_configuration_then_default(self):
        selection = profiles.review_selection(self.repo, self.cfg)
        self.assertEqual((selection["login_root"], selection["login_root_source"]), (None, "default"))
        shown = profiles.show(self.repo)
        self.assertEqual(shown["login_root_source"], "default")
        self.assertEqual(shown["login_root"], str(claude_native_auth.default_root()))
        cfg = {**self.cfg, "claude_review_login_root": "/srv/configured"}
        self.assertEqual(profiles.review_selection(self.repo, cfg)["login_root_source"], "configuration")
        cfg["profiles"]["astra-claude"]["reviewer"]["login_root"] = "/srv/profile"
        selection = profiles.review_selection(self.repo, cfg)
        self.assertEqual(
            (selection["login_root"], selection["login_root_source"]), ("/srv/profile", "profile")
        )
        record = profiles.use_login_root(self.repo, "~/dedicated-login", "reuse the FLOW-DC store")
        self.assertEqual(record["login_root"], str(Path.home() / "dedicated-login"))
        selection = profiles.review_selection(self.repo, cfg)
        self.assertEqual(
            (selection["login_root"], selection["login_root_source"]),
            (str(Path.home() / "dedicated-login"), "local-file"),
        )
        shown = profiles.show(self.repo)
        self.assertEqual(
            (shown["login_root"], shown["login_root_source"]), (record["login_root"], "local-file")
        )
        # The override is private state: ignored, untracked and never part of the policy.
        self.assertEqual(self.repo.git("status", "--porcelain"), "")
        self.assertNotIn("login", json.dumps(selection["policy"]))
        with patch.dict(os.environ, {profiles.ENV_NAME: "astra-copilot"}):
            copilot = profiles.review_selection(self.repo, cfg)
        self.assertEqual((copilot["login_root"], copilot["login_root_source"]), (None, None))

    def test_override_records_are_validated_and_fail_closed(self):
        for path in ("relative/login", "/srv/../login", "", None):
            with self.subTest(path=path), self.assertRaises(workflow.WorkflowError):
                profiles.use_login_root(self.repo, path, "reason")
        for reason in ("", "   ", "x" * 2001, None):
            with self.subTest(reason=reason), self.assertRaisesRegex(workflow.WorkflowError, "reason"):
                profiles.use_login_root(self.repo, "/srv/login", reason)
        self.assertFalse(profiles.login_root_path(self.repo).exists())
        target = profiles.login_root_path(self.repo)
        target.parent.mkdir(exist_ok=True)
        for raw in (
            "{",
            "[]",
            '{"schema_version": 2, "login_root": "/x", "reason": "r"}',
            '{"schema_version": 1}',
        ):
            target.write_text(raw, encoding="utf-8")
            with (
                self.subTest(raw=raw),
                self.assertRaisesRegex(workflow.WorkflowError, "claude-login-root clear"),
            ):
                profiles.review_selection(self.repo, self.cfg)
        target.write_text('{"schema_version": 1, "login_root": "relative", "reason": "r"}', encoding="utf-8")
        with self.assertRaisesRegex(workflow.WorkflowError, "absolute path"):
            profiles.local_login_root(self.repo)
        target.unlink()
        target.mkdir()
        with self.assertRaisesRegex(workflow.WorkflowError, "regular file"):
            profiles.local_login_root(self.repo)
        target.rmdir()
        with patch.dict(os.environ, {"AGENTIC_EXECUTOR_ROLE": "implement"}):
            with self.assertRaisesRegex(workflow.WorkflowError, "Managed executors"):
                profiles.use_login_root(self.repo, "/srv/login", "reason")
            with self.assertRaisesRegex(workflow.WorkflowError, "Managed executors"):
                profiles.clear_login_root(self.repo)

    def test_cli_show_use_clear_round_trip(self):
        code, out, _ = self.cli("claude-login-root", "show")
        self.assertEqual(code, 0)
        shown = json.loads(out)
        self.assertEqual((shown["local_file"], shown["login_root_source"]), (None, "default"))
        self.assertEqual(shown["effective_login_root"], str(claude_native_auth.default_root()))
        code, out, _ = self.cli("claude-login-root", "use", "/srv/dedicated", "--reason", "pilot store")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["login_root"], "/srv/dedicated")
        code, out, _ = self.cli("claude-login-root", "show")
        shown = json.loads(out)
        self.assertEqual(shown["local_file"], str(self.root / ".agentic-local/claude-login-root.json"))
        self.assertEqual(shown["local_override"]["reason"], "pilot store")
        self.assertEqual(
            (shown["effective_login_root"], shown["login_root_source"]), ("/srv/dedicated", "local-file")
        )
        code, out, _ = self.cli("claude-login-root", "clear")
        self.assertEqual(code, 0)
        cleared = json.loads(out)
        self.assertEqual((cleared["cleared"], cleared["login_root_source"]), (True, "default"))
        self.assertFalse(profiles.login_root_path(self.repo).exists())
        code, out, err = self.cli("claude-login-root", "use", "relative", "--reason", "r")
        self.assertEqual(code, 1)
        self.assertIn("absolute path", err)

    def test_login_setup_resolves_the_store_and_delegates_to_the_guarded_setup(self):
        code, _, err = self.cli("claude-login-setup")
        self.assertEqual(code, 1)
        self.assertIn("paid-usage-disabled", err)
        profiles.use_login_root(self.repo, "/srv/dedicated", "pilot store")
        with patch.object(
            claude_native_auth, "setup", return_value={"authentication": {"registration_id": "r"}}
        ) as setup:
            with patch.dict(os.environ, {profiles.ENV_NAME: "astra-copilot"}):
                code, out, _ = self.cli("claude-login-setup", "--renew", "--paid-usage-disabled")
        self.assertEqual(code, 0)
        setup.assert_called_once_with(
            self.repo_argument(setup), root="/srv/dedicated", renew=True, paid_usage_disabled=True
        )
        result = json.loads(out)
        self.assertEqual(
            (result["login_root"], result["login_root_source"], result["renewed"]),
            ("/srv/dedicated", "local-file", True),
        )
        self.assertNotIn("account", json.dumps(result))
        with patch.dict(os.environ, {"AGENTIC_EXECUTOR_ROLE": "implement"}):
            code, _, err = self.cli("claude-login-root", "use", "/srv/other", "--reason", "r")
        self.assertEqual(code, 1)
        self.assertIn("Managed executors", err)

    @staticmethod
    def repo_argument(mock):
        return mock.call_args.args[0]
