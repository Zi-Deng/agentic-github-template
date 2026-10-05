"""Provider policy binding on packets: catalog spellings, typed budgets, legacy packets."""

import copy
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from test_workflow import GitFixture, review, workflow

# isort: split
import profiles
import review_coverage as coverage
import review_policy as policy


class ProviderPolicyTests(GitFixture):
    def config(self, **overrides):
        cfg = workflow.configuration(self.root)
        cfg.update(overrides)
        return cfg

    def extension(self):
        return {
            "provider": "copilot",
            "model": "claude-fixture-99",
            "efforts": ["default", "high"],
            "cli_version": policy.PROVIDERS["copilot"]["cli"]["version"],
            "adapter": policy.PROVIDERS["copilot"]["adapter"],
            "evidence": [
                "https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference"
            ],
        }

    def test_configured_exact_model_binds_compatibility_and_survives_config_removal(self):
        entry = self.extension()
        cfg = self.config(review_model_extensions=[entry])
        selected = profiles.review_selection(
            self.repo, cfg, review_model=entry["model"], review_effort="high"
        )
        self.assertEqual(selected["policy"]["model_compatibility"], entry)
        policy.validate_policy(selected["policy"])
        # The packet carries its own compatibility record; a later configuration cannot reinterpret it.
        self.assertEqual(policy.validate_policy(copy.deepcopy(selected["policy"])), selected["policy"])
        with self.assertRaises(workflow.WorkflowError):
            profiles.review_selection(self.repo, self.config(), review_model=entry["model"])

    def test_model_extension_rejects_aliases_conflicts_and_unverified_contracts(self):
        entry = self.extension()
        for model in ("opus", "claude-auto-99", "claude-latest-99", "claude-fixture-99[1m]", "--model-99"):
            with self.subTest(model=model), self.assertRaises(workflow.WorkflowError):
                policy.model_extensions(self.config(review_model_extensions=[{**entry, "model": model}]))
        for broken in (
            {**entry, "evidence": []},
            {**entry, "evidence": ["http://docs.github.com/plain"]},
            {**entry, "evidence": ["https://docs.github.com/en/copilot?token=private"]},
            {**entry, "model": "claude-opus-5"},
            {**entry, "cli_version": "9.9.9"},
            {**entry, "adapter": "other-adapter"},
            {**entry, "efforts": ["ultracode"]},
            {**entry, "provider": "openai"},
        ):
            with self.subTest(broken=broken), self.assertRaises(workflow.WorkflowError):
                policy.model_extensions(self.config(review_model_extensions=[broken]))
        with self.assertRaises(workflow.WorkflowError):
            policy.model_extensions({"schema_version": 1, "review_model_extensions": [entry]})

    def test_configured_copilot_model_reaches_native_command_without_substitution(self):
        import review_cli
        import review_process
        from review_fixtures import HELP, provider_response

        self.commit_task()
        entry = self.extension()
        cfg = self.config(review_model_extensions=[entry])
        workflow.write_json(self.root / ".agentic/config.json", cfg)
        with patch.object(review_cli, "executable", return_value="/fixture/copilot"):
            status = policy.status(
                self.repo, cfg, profiles.review_selection(self.repo, cfg, review_model=entry["model"])
            )
        self.assertEqual(status["model_compatibility_sources"][entry["model"]], "trusted-config-declaration")
        self.assertEqual(status["model_compatibility_sources"]["claude-opus-5"], "built-in")
        self.assertEqual(status["activation_blockers"], [])
        directory = review.prepare(
            self.repo,
            31,
            12,
            1234,
            review_provider="copilot",
            review_model=entry["model"],
            review_effort="high",
        )
        ordinary = review.run

        def native_info(args, **kwargs):
            if args[1:] in (["--help"], ["--version"]):
                return subprocess.CompletedProcess(args, 0, HELP if args[1] == "--help" else "1.0.83", "")
            return ordinary(args, **kwargs)

        def capture(args, **kwargs):
            self.assertEqual(args[args.index("--model") + 1], entry["model"])
            self.assertEqual(args[args.index("--effort") + 1], "high")
            stdout = provider_response(args, kwargs, directory / "packet")
            state = kwargs["env"]["COPILOT_HOME"]
            session = args[args.index("--session-id") + 1]
            event_file = Path(state) / "session-state" / session / "events.jsonl"
            rows = [json.loads(line) for line in event_file.read_text().splitlines()]
            rows[0]["data"]["selectedModel"] = entry["model"]
            event_file.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            return subprocess.CompletedProcess(args, 0, stdout, b"")

        with (
            patch.dict("os.environ", {"COPILOT_GITHUB_TOKEN": "test-only-token"}),
            patch.object(review_cli, "executable", return_value="/fixture/copilot"),
            patch.object(review, "run", side_effect=native_info),
            patch.object(review_process, "capture", side_effect=capture) as bounded,
        ):
            review.review(self.repo, directory)
        bounded.assert_called_once()
        self.assertEqual(review.verify_packet(directory)["requested_model"], entry["model"])
        self.assertTrue(review.coverage_ready(directory))

    def test_deliberate_supported_models_and_efforts_keep_exact_provider_spelling(self):
        for provider, model, effort in [
            ("claude-code", "claude-sonnet-5", "high"),
            ("copilot", "claude-opus-5.5", "xhigh"),
            ("copilot", "gpt-5.4", "none"),
        ]:
            selected = policy.policy(policy.choices(provider, model, effort), self.config())
            self.assertEqual(
                (selected["provider"], selected["model"], selected["effort"]), (provider, model, effort)
            )
            policy.validate_policy(selected)
        with self.assertRaises(workflow.WorkflowError):
            policy.choices("claude-code", "claude-sonnet-4-6", "xhigh")

    def test_invalid_combinations_never_prepare_packet(self):
        self.commit_task()
        for overrides in [
            {"review_provider": "auto"},
            {"review_model": "opus"},
            {"review_provider": "copilot", "review_model": "claude-opus-5-5"},
            {"review_provider": "copilot", "review_model": "claude-haiku-4.5", "review_effort": "medium"},
            {"review_provider": "claude-code", "review_effort": "ultracode"},
            {"review_provider": "claude-code"},
        ]:
            with self.subTest(overrides=overrides), self.assertRaises(workflow.WorkflowError):
                review.prepare(self.repo, 31, 12, 1234, **overrides)
        self.assertFalse((self.root / ".agentic-local/reviews").exists())

    def test_packet_binds_policy_and_rejects_reserved_schemas(self):
        self.commit_task()
        directory = review.prepare(self.repo, 31, 12, 1234)
        meta = review.verify_packet(directory)
        self.assertEqual((meta["schema_version"], meta["kind"]), (review.PACKET_SCHEMA, "single"))
        for field, value in [("model", "auto"), ("adapter", "future-adapter"), ("billing_mode", "api-key")]:
            changed = copy.deepcopy(meta)
            changed["review_policy"][field] = value
            review.atomic_json(directory / "metadata.json", changed)
            with self.subTest(field=field), self.assertRaises(workflow.WorkflowError):
                review.verify_packet(directory)
        for schema in (2, 3, 4, 6, 8):
            review.atomic_json(directory / "metadata.json", {**meta, "schema_version": schema})
            with (
                self.subTest(schema=schema),
                self.assertRaisesRegex(workflow.WorkflowError, "frozen adapters"),
            ):
                review.verify_packet(directory)
        review.atomic_json(directory / "metadata.json", {**meta, "kind": "batch-unit"})
        with self.assertRaisesRegex(workflow.WorkflowError, "packet kind"):
            review.verify_packet(directory)

    def test_typed_budgets_reject_unbounded_and_paid_claude_policy(self):
        selected = policy.choices("claude-code")
        for amount in [True, float("nan"), float("inf"), -1, 0, 11, "2"]:
            with self.subTest(amount=amount), self.assertRaises(workflow.WorkflowError):
                policy.policy(selected, {"review_max_estimated_usd": amount})
        value = policy.policy(selected, {})
        value["budget"]["extra_spend_authorized_usd"] = 1
        with self.assertRaises(workflow.WorkflowError):
            policy.validate_policy(value)
        value = policy.policy(selected, {})
        value["budget"]["ai_credits"] = 400
        with self.assertRaises(workflow.WorkflowError):
            policy.validate_policy(value)

    def test_pre_coverage_packet_publishes_and_verifies_exactly_but_never_qualifies(self):
        # Packets prepared before the coverage gate carry schema 1 with the report written
        # directly; they stay publishable and byte-verifiable, never qualified or runnable.
        self.commit_task()
        directory = review.prepare(self.repo, 31, 12, 1234)
        meta = review.verify_packet(directory)
        body = "Legacy exact café\r\n\x07\x1b\\n\r\n"
        (directory / "review.md").write_bytes(body.encode("utf-8"))
        legacy = {
            key: value for key, value in meta.items() if key not in {"kind", "overrides", "selection_sources"}
        }
        legacy.update(
            schema_version=1, review_sha256=coverage.checksum(body), copilot_version="legacy-identity"
        )
        review.atomic_json(directory / "metadata.json", legacy)
        with patch.object(coverage, "assess", side_effect=AssertionError("No new historical coverage")):
            expected = body + f"\n<!-- agentic-review:{self.head}:{coverage.checksum(body)} -->"
            self.assertEqual(review.publication_body(directory), expected)
            self.assertFalse(review.coverage_ready(directory))
            with self.assertRaisesRegex(workflow.WorkflowError, "Pre-coverage"):
                review.qualification(directory)
            with self.assertRaisesRegex(workflow.WorkflowError, "Legacy packets cannot run"):
                review.review(self.repo, directory)
            review.publish(self.repo, directory)
            self.reviews = [
                {
                    "body": expected,
                    "html_url": "existing",
                    "commit_id": self.head,
                    "state": "COMMENTED",
                    "id": 7,
                }
            ]
            verified = review.verify_publication(self.repo, directory)
        self.assertEqual((verified["exact_match"], verified["coverage_qualified"]), (True, False))
        with self.assertRaisesRegex(workflow.WorkflowError, "Pre-coverage"):
            review.verified_published(self.repo, directory, 31, self.head, self.base)
