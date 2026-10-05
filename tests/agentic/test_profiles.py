"""Profile resolution, reviewer backends, shims, overrides and family gates on real Git fixtures."""

import contextlib
import io
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from test_workflow import GitFixture, workflow

# The shared fixture establishes the scripts import path.
# isort: split
import profiles
import review_policy

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CODEX = {"backend": "codex", "model": "gpt-6-astra"}
CLAUDE = {"backend": "claude", "model": "claude-fable-5-1"}


class ProfileTests(GitFixture):
    def setUp(self):
        super().setUp()
        self.cfg = workflow.configuration(self.root)

    def single(self, implementer, reviewer, **extra):
        return {
            **self.cfg,
            "hosted_profile": None,
            "default_profile": "x",
            "profiles": {"x": {"implementer": implementer, "reviewer": reviewer}},
            **extra,
        }

    def with_profile(self, name, implementer, reviewer, **extra):
        cfg = workflow.configuration(self.root)
        cfg["profiles"][name] = {"implementer": implementer, "reviewer": reviewer}
        cfg.update(extra)
        workflow.write_json(self.root / ".agentic/config.json", cfg)
        return cfg

    def test_precedence_is_environment_then_local_file_then_default(self):
        default = profiles.active_profile(self.repo)
        self.assertEqual((default["name"], default["source"]), ("astra-copilot", "default"))
        self.assertEqual(default["implementer"]["backend"], "codex")
        self.assertEqual(default["reviewer"]["backend"], "copilot")
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
        self.assertEqual(env["reviewer"]["backend"], "claude-code")
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

    def test_schema_one_and_template_schema_two_shims(self):
        legacy = profiles.load_profiles(
            {"schema_version": 1, "openai_model": "gpt-6-astra", "copilot_model": "claude-opus-5"}
        )
        self.assertEqual(
            (legacy["default_profile"], legacy["config_schema"], legacy["shimmed"]), ("legacy", 1, True)
        )
        self.assertEqual(legacy["profiles"]["legacy"]["implementer"]["backend"], "codex")
        self.assertEqual(legacy["profiles"]["legacy"]["reviewer"]["model"], "claude-opus-5")
        with self.assertRaisesRegex(workflow.WorkflowError, "migrate"):
            profiles.load_profiles({"schema_version": 1})
        with self.assertRaisesRegex(workflow.WorkflowError, "schema_version 3"):
            profiles.load_profiles({"schema_version": 1, "profiles": {}, "default_profile": "x"})
        template = json.loads((FIXTURES / "config-template-schema2.json").read_text())
        declared = profiles.load_profiles(template)
        self.assertEqual((declared["default_profile"], declared["shimmed"]), ("astra-claude", True))
        self.assertEqual(set(declared["profiles"]), {"astra-claude", "fable-gpt"})
        self.assertEqual(declared["profiles"]["astra-claude"]["reviewer"]["backend"], "copilot")
        self.assertEqual(declared["profiles"]["astra-claude"]["reviewer"]["effort"], "default")
        self.assertIsNone(declared["hosted_profile"])
        with self.assertRaisesRegex(workflow.WorkflowError, "remove openai_model"):
            profiles.load_profiles({**template, "openai_model": "gpt-6-astra"})
        template["profiles"]["astra-claude"]["reviewer"] = {
            "backend": "claude-code",
            "model": "claude-opus-5-5",
        }
        with self.assertRaisesRegex(workflow.WorkflowError, "requires schema_version 3"):
            profiles.load_profiles(template)
        hint = profiles.migration_hint(declared)
        self.assertEqual(hint["schema_version"], 3)
        self.assertEqual(
            profiles.load_profiles({**self.cfg, "hosted_profile": None, **hint})["default_profile"],
            "astra-claude",
        )

    def test_flowdc_schema_two_shim_derives_both_legacy_profiles(self):
        flowdc = json.loads((FIXTURES / "config-flowdc-schema2.json").read_text())
        declared = profiles.load_profiles(flowdc)
        self.assertEqual(declared["default_profile"], "legacy-claude-code")
        self.assertEqual(declared["hosted_profile"], "legacy-copilot")
        self.assertTrue(declared["shimmed"])
        native = declared["profiles"]["legacy-claude-code"]["reviewer"]
        self.assertEqual(
            (native["backend"], native["model"], native["effort"]),
            ("claude-code", "claude-opus-5-5", "medium"),
        )
        self.assertEqual(native["max_estimated_usd"], 10)
        hosted = declared["profiles"]["legacy-copilot"]["reviewer"]
        self.assertEqual(
            (hosted["backend"], hosted["model"], hosted["effort"]), ("copilot", "claude-opus-5", "default")
        )
        copilot_only = {
            **flowdc,
            "review_provider": "copilot",
            "review_model": "gpt-6-astra",
            "review_effort": "default",
        }
        declared = profiles.load_profiles(copilot_only)
        self.assertEqual(set(declared["profiles"]), {"legacy-copilot"})
        self.assertEqual(declared["profiles"]["legacy-copilot"]["reviewer"]["model"], "gpt-6-astra")
        with self.assertRaisesRegex(workflow.WorkflowError, "review_provider"):
            profiles.load_profiles({**flowdc, "review_provider": "other"})
        stderr = io.StringIO()
        workflow.write_json(self.root / ".agentic/config.json", flowdc)
        with contextlib.redirect_stderr(stderr):
            active = profiles.active_profile(self.repo)
        self.assertEqual(active["name"], "legacy-claude-code")
        self.assertIn("note: configuration schema 2 was shimmed", stderr.getvalue())
        self.assertIn('"schema_version":3', stderr.getvalue())
        # Helpers resolve the profile several times; the note is still printed once per process.
        profiles._NOTED_MIGRATIONS.clear()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            profiles.show(self.repo)
        self.assertEqual(stderr.getvalue().count("note: configuration schema 2 was shimmed"), 1)

    def test_schema_three_rules_and_unsupported_schemas(self):
        with self.assertRaisesRegex(workflow.WorkflowError, "remove openai_model"):
            profiles.load_profiles({**self.cfg, "openai_model": "gpt-6-astra"})
        with self.assertRaisesRegex(workflow.WorkflowError, "remove openai_model"):
            profiles.load_profiles({**self.cfg, "review_provider": "copilot"})
        with self.assertRaisesRegex(workflow.WorkflowError, "default_profile"):
            profiles.load_profiles({**self.cfg, "default_profile": "missing"})
        with self.assertRaisesRegex(workflow.WorkflowError, "expected 1, 2 or 3"):
            profiles.load_profiles({"schema_version": 4})
        with self.assertRaisesRegex(workflow.WorkflowError, "hosted_profile must name"):
            profiles.load_profiles({**self.cfg, "hosted_profile": "missing"})
        with self.assertRaisesRegex(workflow.WorkflowError, "Copilot-only"):
            profiles.load_profiles({**self.cfg, "hosted_profile": "astra-claude"})
        with self.assertRaisesRegex(workflow.WorkflowError, "exactly implementer and reviewer"):
            profiles.load_profiles(
                {
                    **self.cfg,
                    "hosted_profile": None,
                    "profiles": {"x": {"implementer": CODEX}},
                    "default_profile": "x",
                }
            )
        reviewer = {"backend": "copilot", "model": "claude-opus-5"}
        with self.assertRaisesRegex(workflow.WorkflowError, "Profile name"):
            profiles.load_profiles(
                {
                    **self.cfg,
                    "hosted_profile": None,
                    "profiles": {"Bad Name": {"implementer": CODEX, "reviewer": reviewer}},
                    "default_profile": "Bad Name",
                }
            )

    def test_floating_models_wrong_families_and_bypass_defaults_are_refused(self):
        implementer_cases = [
            ({"backend": "claude", "model": "opus"}, "explicit ID"),
            ({"backend": "claude", "model": "gpt-6-astra"}, "explicit ID"),
            ({"backend": "codex", "model": "claude-fable-5-1"}, "explicit ID"),
            ({**CLAUDE, "permission_policy": "bypass"}, "per-launch"),
            ({**CLAUDE, "setting_sources": []}, "setting_sources"),
            ({**CLAUDE, "bogus": 1}, "unknown implementer keys"),
            ({**CODEX, "sandbox": None}, "unknown implementer keys"),
            ({"backend": "other", "model": "gpt-6-astra"}, "codex or claude"),
        ]
        reviewer = {"backend": "copilot", "model": "claude-opus-5"}
        for implementer, pattern in implementer_cases:
            with self.subTest(implementer=implementer):
                with self.assertRaisesRegex(workflow.WorkflowError, pattern):
                    profiles.load_profiles(self.single(implementer, reviewer))

    def test_reviewer_specs_are_validated_against_the_closed_catalog(self):
        cases = [
            ({"backend": "copilot", "model": "auto"}, "Unsupported exact review model"),
            ({"backend": "copilot"}, "explicit exact model ID"),
            ({"backend": "claude", "model": "claude-opus-5"}, "copilot or claude-code"),
            ({"backend": "claude-code", "model": "claude-opus-5.5"}, "Unsupported exact review model"),
            ({"backend": "copilot", "model": "claude-opus-5-5"}, "Unsupported exact review model"),
            ({"backend": "copilot", "model": "gpt-6-astra", "effort": "xhigh"}, "Unsupported effort"),
            (
                {"backend": "claude-code", "model": "claude-opus-5-5", "effort": "default"},
                "Unsupported effort",
            ),
            ({"backend": "copilot", "model": "gpt-6-astra", "max_ai_credits": 0}, "positive integer"),
            (
                {"backend": "claude-code", "model": "claude-opus-5-5", "max_ai_credits": 5},
                "unknown reviewer keys",
            ),
            (
                {"backend": "copilot", "model": "claude-opus-5", "max_estimated_usd": 5},
                "unknown reviewer keys",
            ),
            ({"backend": "claude-code", "model": "claude-opus-5-5", "max_estimated_usd": 11}, r"\(0, 10\]"),
            ({"backend": "claude-code", "model": "claude-opus-5-5", "timeout_seconds": 901}, "at most 900"),
            (
                {"backend": "copilot", "model": "claude-opus-5", "cli_version": "1.0.0"},
                "pins reviewer cli_version",
            ),
            (
                {"backend": "claude-code", "model": "claude-opus-5-5", "adapter": "old"},
                "pins reviewer adapter",
            ),
            (
                {"backend": "claude-code", "model": "claude-opus-5-5", "billing_mode": "extra-usage"},
                "billing_mode",
            ),
            (
                {"backend": "claude-code", "model": "claude-opus-5-5", "login_root": "relative"},
                "absolute path",
            ),
        ]
        for reviewer, pattern in cases:
            with self.subTest(reviewer=reviewer):
                with self.assertRaisesRegex(workflow.WorkflowError, pattern):
                    profiles.load_profiles(self.single(CODEX, reviewer))
        accepted = {
            "backend": "claude-code",
            "model": "claude-opus-5-5",
            "effort": "high",
            "cli_version": review_policy.PROVIDERS["claude-code"]["cli"]["version"],
            "adapter": review_policy.PROVIDERS["claude-code"]["adapter"],
            "billing_mode": "included-max-subscription-only",
            "login_root": "~/login",
            "max_estimated_usd": 2.5,
        }
        declared = profiles.load_profiles(self.single(CODEX, accepted))["profiles"]["x"]["reviewer"]
        self.assertEqual(declared["effort"], "high")
        self.assertTrue(Path(declared["login_root"]).is_absolute())
        extension = {
            "provider": "copilot",
            "model": "gpt-7-nova",
            "efforts": ["default"],
            "cli_version": review_policy.PROVIDERS["copilot"]["cli"]["version"],
            "adapter": review_policy.PROVIDERS["copilot"]["adapter"],
            "evidence": ["https://docs.github.com/en/copilot/example"],
        }
        cfg = self.single(
            CLAUDE, {"backend": "copilot", "model": "gpt-7-nova"}, review_model_extensions=[extension]
        )
        self.assertEqual(profiles.load_profiles(cfg)["profiles"]["x"]["reviewer"]["model"], "gpt-7-nova")

    def test_same_family_requires_a_recorded_allowance_and_warns(self):
        self.with_profile("claude-claude", CLAUDE, {"backend": "copilot", "model": "claude-opus-5"})
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
        profiles.use_profile(self.repo, "astra-copilot")
        with patch.dict(os.environ, {profiles.ENV_NAME: "claude-claude"}):
            with self.assertRaisesRegex(workflow.WorkflowError, "same-family"):
                profiles.active_profile(self.repo)

    def test_review_selection_binds_the_profile_into_an_immutable_policy(self):
        selection = profiles.review_selection(self.repo)
        policy = selection["policy"]
        self.assertEqual(review_policy.validate_policy(policy), policy)
        self.assertEqual(
            (policy["provider"], policy["model"], policy["effort"]), ("copilot", "claude-opus-5", "default")
        )
        self.assertEqual(
            policy["budget"],
            {"schema_version": 1, "kind": "ai-credits", "timeout_seconds": 900, "ai_credits": 400},
        )
        self.assertEqual(policy["cli"]["version"], review_policy.PROVIDERS["copilot"]["cli"]["version"])
        self.assertEqual(
            selection["sources"], {"provider": "profile", "model": "profile", "effort": "profile"}
        )
        self.assertIsNone(selection["overrides"])
        self.assertEqual(
            selection["provenance"]["implementer"],
            {"backend": "codex", "model": "gpt-6-astra", "family": "openai"},
        )
        self.assertFalse(selection["provenance"]["same_family"])
        self.assertIsNone(selection["login_root"])
        with patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"}):
            native = profiles.review_selection(self.repo)
        self.assertEqual(native["policy"]["provider"], "claude-code")
        self.assertEqual(
            native["policy"]["budget"],
            {
                "schema_version": 1,
                "kind": "reference-usd",
                "timeout_seconds": 900,
                "estimated_usd": 10,
                "extra_spend_authorized_usd": 0,
            },
        )
        self.assertEqual(native["policy"]["billing_mode"], "included-max-subscription-only")
        self.assertIsNone(native["login_root"])
        cfg = {**self.cfg, "claude_review_login_root": "/srv/claude-login"}
        with patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"}):
            self.assertEqual(profiles.review_selection(self.repo, cfg)["login_root"], "/srv/claude-login")
        cfg["profiles"]["astra-claude"]["reviewer"]["login_root"] = "/srv/profile-login"
        with patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"}):
            self.assertEqual(profiles.review_selection(self.repo, cfg)["login_root"], "/srv/profile-login")
        # A per-call switch to claude-code from a Copilot profile keeps the configured login root.
        switched = profiles.review_selection(self.repo, cfg, review_provider="claude-code")
        self.assertEqual(
            (switched["policy"]["provider"], switched["login_root"]), ("claude-code", "/srv/claude-login")
        )
        self.assertIsNone(profiles.review_selection(self.repo, cfg)["login_root"])

    def test_profile_budgets_apply_only_to_their_own_backend(self):
        cfg = workflow.configuration(self.root)
        cfg["profiles"]["astra-copilot"]["reviewer"].update(max_ai_credits=30, timeout_seconds=120)
        selection = profiles.review_selection(self.repo, cfg)
        self.assertEqual(
            (selection["policy"]["budget"]["ai_credits"], selection["policy"]["budget"]["timeout_seconds"]),
            (30, 120),
        )
        # A per-call switch to the other backend uses the top-level budgets, not the profile's.
        with patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"}):
            switched = profiles.review_selection(self.repo, cfg, review_provider="copilot")
        self.assertEqual(
            switched["policy"]["budget"],
            {"schema_version": 1, "kind": "ai-credits", "timeout_seconds": 900, "ai_credits": 400},
        )

    def test_per_call_overrides_follow_provider_reset_precedence_and_are_recorded(self):
        with patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"}):
            switched = profiles.review_selection(self.repo, review_provider="copilot")
        self.assertEqual(
            (switched["policy"]["provider"], switched["policy"]["model"], switched["policy"]["effort"]),
            ("copilot", "claude-opus-5", "default"),
        )
        self.assertEqual(
            switched["sources"],
            {
                "provider": "per-call",
                "model": "per-call-provider-default",
                "effort": "per-call-provider-default",
            },
        )
        self.assertEqual(
            switched["overrides"], {"review_provider": "copilot", "review_model": None, "review_effort": None}
        )
        model = profiles.review_selection(self.repo, review_model="claude-sonnet-5", review_effort="high")
        self.assertEqual((model["policy"]["model"], model["policy"]["effort"]), ("claude-sonnet-5", "high"))
        self.assertEqual(model["sources"], {"provider": "profile", "model": "per-call", "effort": "per-call"})
        with self.assertRaisesRegex(workflow.WorkflowError, "Unsupported exact review model"):
            profiles.review_selection(self.repo, review_model="claude-opus-5-5")
        with self.assertRaisesRegex(workflow.WorkflowError, "Unsupported effort"):
            profiles.review_selection(
                self.repo, review_effort="xhigh", review_model="gpt-6-astra", allow_same_family=True
            )
        with patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"}):
            with self.assertRaisesRegex(workflow.WorkflowError, "requires a reviewer backend of copilot"):
                profiles.review_selection(self.repo, require_backend="copilot")
            self.assertEqual(
                profiles.review_selection(self.repo, review_provider="copilot", require_backend="copilot")[
                    "policy"
                ]["provider"],
                "copilot",
            )

    def test_same_family_gate_applies_to_overrides_and_pinned_implementers(self):
        with self.assertRaisesRegex(workflow.WorkflowError, "implementer family"):
            profiles.review_selection(self.repo, review_model="gpt-6-astra")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            acknowledged = profiles.review_selection(
                self.repo, review_model="gpt-6-astra", allow_same_family=True
            )
        self.assertTrue(acknowledged["provenance"]["same_family"])
        self.assertTrue(acknowledged["provenance"]["same_family_acknowledged"])
        self.assertIn("warning:", stderr.getvalue())
        pinned = {"backend": "claude", "model": "claude-fable-5-1", "family": "anthropic"}
        with self.assertRaisesRegex(workflow.WorkflowError, "implementer family"):
            profiles.review_selection(self.repo, implementer=pinned)
        result = profiles.review_selection(
            self.repo, implementer=pinned, allow_same_family=True, warn_same_family=False
        )
        self.assertEqual(result["provenance"]["implementer_source"], "pinned executor")
        # A recorded profile allowance covers its own pairing, never a per-call override.
        self.with_profile("claude-claude", CLAUDE, {"backend": "copilot", "model": "claude-opus-5"})
        profiles.use_profile(self.repo, "claude-claude", allow_same_family=True, reason="fixture")
        self.assertTrue(
            profiles.review_selection(self.repo, warn_same_family=False)["provenance"][
                "same_family_acknowledged"
            ]
        )
        with self.assertRaisesRegex(workflow.WorkflowError, "never covers a per-call override"):
            profiles.review_selection(self.repo, review_model="claude-sonnet-5")

    def test_stale_saved_selection_file_is_reported_not_honored(self):
        stale = self.root / ".agentic-local/review-selection.json"
        stale.parent.mkdir(exist_ok=True)
        stale.write_text(json.dumps({"provider": "claude-code"}))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            selection = profiles.review_selection(self.repo)
        self.assertEqual(selection["policy"]["provider"], "copilot")
        self.assertIn("no longer honored", stderr.getvalue())

    def test_status_reports_blockers_without_running_anything(self):
        copilot = review_policy.status(self.repo, self.cfg, profiles.review_selection(self.repo))
        self.assertEqual(copilot["activation_blockers"], ["verified_pinned_cli_unavailable"])
        self.assertIn("claude-opus-5", copilot["supported_models"])
        with patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"}):
            native = review_policy.status(self.repo, self.cfg, profiles.review_selection(self.repo))
        self.assertIn("claude_reviewer_adapter_not_installed", native["activation_blockers"])
        self.assertIn("verified_pinned_cli_unavailable", native["activation_blockers"])
        self.assertEqual(native["model_compatibility_sources"]["claude-opus-5-5"], "built-in")

    def test_managed_executors_cannot_change_the_active_profile(self):
        with patch.dict(os.environ, {"AGENTIC_EXECUTOR_ROLE": "implement"}):
            with self.assertRaisesRegex(workflow.WorkflowError, "Managed executors"):
                profiles.use_profile(self.repo, "fable-gpt")
            with self.assertRaisesRegex(workflow.WorkflowError, "Managed executors"):
                profiles.clear_profile(self.repo)
        self.assertFalse((self.root / ".agentic-local/profile.json").exists())

    def test_pinned_executor_treats_records_without_a_pin_as_codex(self):
        legacy = profiles.pinned_executor({"uuid": None, "runs": []})
        self.assertEqual((legacy["backend"], legacy["model"], legacy["legacy"]), ("codex", None, True))
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
        self.assertEqual(set(view["profiles"]), {"astra-claude", "astra-copilot", "fable-gpt"})
        self.assertEqual(
            (view["config_schema"], view["shimmed"], view["hosted_profile"]), (3, False, "astra-copilot")
        )
        self.assertEqual(view["profiles"]["astra-claude"]["reviewer"]["backend"], "claude-code")
        shown = profiles.show(self.repo)
        self.assertEqual(shown["name"], "astra-copilot")
        self.assertIsNone(shown["local_file"])
        self.assertEqual(set(shown["tools"]), {"codex", "claude", "copilot"})
        self.assertEqual(shown["review_policy"]["policy"]["provider"], "copilot")
        self.assertIn("activation_blockers", shown["review_policy"])
        with patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"}):
            native = profiles.show(self.repo)
        self.assertIn("claude_reviewer_adapter_not_installed", native["review_policy"]["activation_blockers"])

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
