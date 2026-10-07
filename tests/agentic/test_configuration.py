"""Strict schema 3 configuration, user-visible defaults and the schema 1/2 shims."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from test_workflow import SOURCE, workflow

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ConfigurationTests(unittest.TestCase):
    def write(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / ".agentic").mkdir()
        (root / ".agentic/config.json").write_text(text, encoding="utf-8")
        return root

    def config(self, *, omit=(), **overrides):
        base = json.loads((SOURCE / ".agentic/config.json").read_text(encoding="utf-8"))
        for key in omit:
            base.pop(key, None)
        return workflow.configuration(self.write(json.dumps({**base, **overrides})))

    def test_shipped_configuration_is_schema_three_with_three_profiles(self):
        config = self.config()
        self.assertEqual(config["schema_version"], 3)
        self.assertEqual(config["default_profile"], "astra-copilot")
        self.assertEqual(config["hosted_profile"], "astra-copilot")
        self.assertEqual(set(config["profiles"]), {"astra-claude", "astra-copilot", "fable-gpt"})
        self.assertIsNone(config["max_diff_bytes"])
        self.assertEqual(config["managed_max_prompt_bytes"], 300_000)
        self.assertEqual(config["private_paths"], [])
        self.assertIsNone(config["claude_review_login_root"])

    def test_missing_optional_keys_take_documented_defaults(self):
        config = self.config(
            omit=[
                "max_diff_bytes",
                "managed_max_prompt_bytes",
                "review_max_estimated_usd",
                "review_model_extensions",
                "claude_review_login_root",
                "private_paths",
                "source_roots",
            ]
        )
        self.assertIsNone(config["max_diff_bytes"])
        self.assertEqual(config["managed_max_prompt_bytes"], 300_000)
        self.assertEqual(config["review_max_estimated_usd"], 10)
        self.assertEqual(config["review_model_extensions"], [])
        self.assertIsNone(config["claude_review_login_root"])
        self.assertEqual(config["private_paths"], [])
        self.assertEqual(config["source_roots"], ["scripts/agentic", "tests/agentic"])

    def test_schema_version_requires_a_supported_integer(self):
        for value in [None, True, "3", 0, 4, 5]:
            with self.subTest(value=value), self.assertRaisesRegex(workflow.WorkflowError, "schema"):
                self.config(schema_version=value)
        with self.assertRaisesRegex(workflow.WorkflowError, "not valid JSON"):
            workflow.configuration(self.write("{not json"))
        with self.assertRaisesRegex(workflow.WorkflowError, "schema"):
            workflow.configuration(self.write("[]"))

    def test_schema_three_refuses_legacy_model_keys(self):
        for key, value in [
            ("openai_model", "gpt-6-astra"),
            ("copilot_model", "claude-opus-5"),
            ("review_provider", "copilot"),
            ("review_model", "claude-opus-5"),
            ("review_effort", "default"),
        ]:
            with self.subTest(key=key), self.assertRaisesRegex(workflow.WorkflowError, "remove"):
                self.config(**{key: value})

    def test_explicit_null_or_positive_diff_cap_is_supported(self):
        for value in [None, 1, 300_000, 1_000_000]:
            with self.subTest(value=value):
                self.assertEqual(self.config(max_diff_bytes=value)["max_diff_bytes"], value)

    def test_invalid_diff_caps_are_rejected(self):
        for value in [True, False, 0, -1, "300000", "unlimited", 1.5, [], {}]:
            with self.subTest(value=value), self.assertRaisesRegex(workflow.WorkflowError, "max_diff_bytes"):
                self.config(max_diff_bytes=value)

    def test_prompt_cap_requires_positive_integer(self):
        self.assertEqual(self.config(managed_max_prompt_bytes=1)["managed_max_prompt_bytes"], 1)
        for value in [None, True, False, 0, -1, "300000", 1.5, [], {}]:
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(workflow.WorkflowError, "managed_max_prompt_bytes"),
            ):
                self.config(managed_max_prompt_bytes=value)

    def test_required_settings_fail_with_actionable_errors(self):
        for key in [
            "review_timeout_seconds",
            "review_max_ai_credits",
            "max_source_file_bytes",
            "max_snapshot_bytes",
            "domain_rubric",
            "required_checks",
            "profiles",
            "default_profile",
        ]:
            with self.subTest(key=key), self.assertRaisesRegex(workflow.WorkflowError, key):
                self.config(omit=[key])

    def test_budgets_reject_invalid_types_and_nonpositive_values(self):
        for key in [
            "review_timeout_seconds",
            "review_max_ai_credits",
            "max_source_file_bytes",
            "max_snapshot_bytes",
            "managed_timeout_seconds",
            "managed_max_output_bytes",
        ]:
            for value in [None, True, 0, -1, "100", 1.5]:
                with self.subTest(key=key, value=value), self.assertRaisesRegex(workflow.WorkflowError, key):
                    self.config(**{key: value})
        for value in [None, True, 0, -1, 10.5, 11, "5"]:
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(workflow.WorkflowError, "review_max_estimated_usd"),
            ):
                self.config(review_max_estimated_usd=value)
        self.assertEqual(self.config(review_max_estimated_usd=2.5)["review_max_estimated_usd"], 2.5)

    def test_rubric_check_names_private_paths_and_login_root_are_validated(self):
        for value in [None, True, 100, "", " ", []]:
            with self.subTest(value=value), self.assertRaisesRegex(workflow.WorkflowError, "domain_rubric"):
                self.config(domain_rubric=value)
        for value in [
            None,
            "quality",
            [],
            [""],
            [" quality"],
            [False],
            [["quality"]],
            ["quality", "quality"],
        ]:
            with self.subTest(value=value), self.assertRaisesRegex(workflow.WorkflowError, "required_checks"):
                self.config(required_checks=value)
        for value in [
            None,
            "data",
            [""],
            ["/data"],
            ["../data"],
            ["a//b"],
            ["data", "data"],
            [1],
            ["x" * 201],
        ]:
            with self.subTest(value=value), self.assertRaisesRegex(workflow.WorkflowError, "private_paths"):
                self.config(private_paths=value)
        self.assertEqual(
            self.config(private_paths=["files/input", "archives"])["private_paths"],
            ["files/input", "archives"],
        )
        for value in ["relative/path", "", " ", "/root/../x", 5]:
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(workflow.WorkflowError, "claude_review_login_root"),
            ):
                self.config(claude_review_login_root=value)
        self.assertEqual(
            self.config(claude_review_login_root="/srv/login")["claude_review_login_root"], "/srv/login"
        )
        with self.assertRaisesRegex(workflow.WorkflowError, "review_model_extensions"):
            self.config(review_model_extensions={})

    def test_hosted_profile_must_resolve_to_a_copilot_reviewer(self):
        with self.assertRaisesRegex(workflow.WorkflowError, "hosted_profile"):
            self.config(hosted_profile="astra-claude")
        with self.assertRaisesRegex(workflow.WorkflowError, "hosted_profile"):
            self.config(hosted_profile="missing")
        self.assertIsNone(self.config(omit=["hosted_profile"])["hosted_profile"])

    def test_template_schema_two_dialect_loads_through_the_shim(self):
        root = self.write((FIXTURES / "config-template-schema2.json").read_text(encoding="utf-8"))
        config = workflow.configuration(root)
        self.assertEqual(config["schema_version"], 2)
        self.assertEqual(config["default_profile"], "astra-claude")
        self.assertEqual(
            config["profiles"]["astra-claude"]["reviewer"], {"backend": "copilot", "model": "claude-opus-5"}
        )
        self.assertEqual(config["managed_max_prompt_bytes"], 300_000)

    def test_flowdc_schema_two_dialect_loads_through_the_shim(self):
        root = self.write((FIXTURES / "config-flowdc-schema2.json").read_text(encoding="utf-8"))
        config = workflow.configuration(root)
        self.assertEqual(config["schema_version"], 2)
        self.assertEqual(config["review_provider"], "claude-code")
        self.assertIsNone(config["max_diff_bytes"])
        self.assertEqual(config["required_checks"], ["flowdc-tests", "agentic-quality"])

    def test_fixtures_are_byte_copies_of_the_recorded_dialects(self):
        # The template dialect is the configuration shipped before this change; the FLOW-DC
        # dialect is FLOW-DC main 8271616. Keep them verbatim so the shims stay honest.
        for name in ("config-template-schema2.json", "config-flowdc-schema2.json"):
            with self.subTest(name=name):
                value = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
                self.assertEqual(value["schema_version"], 2)
        copied = self.write("{}")
        shutil.copyfile(FIXTURES / "config-template-schema2.json", copied / ".agentic/config.json")
        self.assertEqual(workflow.configuration(copied)["schema_version"], 2)
