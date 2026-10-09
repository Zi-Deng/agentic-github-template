"""The recorded per-call budget exception: ceilings, policy binding, packet and publication."""

import argparse
import copy
import json
import os
from unittest.mock import patch

from test_workflow import GitFixture, review, workflow

# isort: split
import claude_native_auth
import claude_telemetry as telemetry
import profiles
import review_policy
from claude_fixtures import AUTHENTICATION, native_events

REASON = "Single native review of the whole update, maintainer approval 2026-10-07"


def exception(timeout=7200, usd=60, reason=REASON):
    return review_policy.exception_record(timeout, usd, reason)


class BudgetExceptionTests(GitFixture):
    def setUp(self):
        super().setUp()
        self.cfg = workflow.configuration(self.root)
        self.env = patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"})
        self.env.start()
        self.addCleanup(self.env.stop)
        binding = patch.object(claude_native_auth, "current_binding", return_value=AUTHENTICATION)
        binding.start()
        self.addCleanup(binding.stop)

    def test_exception_limits_may_only_raise_claude_budgets_within_the_ceilings(self):
        budget = review_policy.budget("claude-code", {}, exception=exception())
        self.assertEqual((budget["timeout_seconds"], budget["estimated_usd"]), (7200, 60))
        self.assertEqual(budget["extra_spend_authorized_usd"], 0)
        self.assertEqual(
            {k: v for k, v in budget["exception"].items() if k != "recorded_at"},
            {"reason": REASON, "default_timeout_seconds": 900, "default_estimated_usd": 10},
        )
        for timeout, usd in ((899, 60), (7201, 60), (7200, 9.99), (7200, 60.5), (True, 60), (7200, "60")):
            with self.subTest(timeout=timeout, usd=usd), self.assertRaises(workflow.WorkflowError):
                review_policy.budget("claude-code", {}, exception=exception(timeout, usd))
        for reason in ("", " ", "x" * 2001, None):
            with self.subTest(reason=reason), self.assertRaises(workflow.WorkflowError):
                review_policy.budget("claude-code", {}, exception=exception(reason=reason))
        with self.assertRaisesRegex(workflow.WorkflowError, "Claude Code reviewer only"):
            review_policy.budget("copilot", {}, exception=exception())
        with self.assertRaisesRegex(workflow.WorkflowError, "never carry"):
            review_policy.budget("claude-code", {}, diagnostic=True, exception=exception())
        # A profile that lowered its own limits records them as the defaults it raised from.
        lowered = review_policy.budget(
            "claude-code",
            {"review_timeout_seconds": 600, "review_max_estimated_usd": 5},
            exception=exception(900, 10),
        )
        self.assertEqual((lowered["timeout_seconds"], lowered["estimated_usd"]), (900, 10))
        self.assertEqual(
            (lowered["exception"]["default_timeout_seconds"], lowered["exception"]["default_estimated_usd"]),
            (600, 5),
        )
        self.assertNotIn("exception", review_policy.budget("claude-code", {}))

    def test_flags_are_all_or_nothing(self):
        parser = argparse.ArgumentParser()
        review_policy.add_arguments(parser)
        self.assertIsNone(review_policy.exception_from_args(parser.parse_args([])))
        args = parser.parse_args(
            [
                "--review-timeout-seconds",
                "3600",
                "--review-max-estimated-usd",
                "25",
                "--review-exception-reason",
                REASON,
            ]
        )
        record = review_policy.exception_from_args(args)
        self.assertEqual(
            (record["timeout_seconds"], record["estimated_usd"], record["reason"]), (3600, 25.0, REASON)
        )
        for partial in (["--review-timeout-seconds", "3600"], ["--review-exception-reason", REASON]):
            with self.subTest(partial=partial), self.assertRaisesRegex(workflow.WorkflowError, "together"):
                review_policy.exception_from_args(parser.parse_args(partial))

    def test_selection_binds_the_exception_into_an_immutable_policy(self):
        selection = profiles.review_selection(self.repo, self.cfg, review_exception=exception())
        policy = selection["policy"]
        self.assertEqual(review_policy.validate_policy(policy), policy)
        self.assertEqual(policy["budget"]["timeout_seconds"], 7200)
        self.assertIsNone(selection["overrides"])
        plain = profiles.review_selection(self.repo, self.cfg)["policy"]
        self.assertNotEqual(review.value_digest(plain), review.value_digest(policy))
        for mutate in (
            lambda p: p["budget"].update(timeout_seconds=7201),
            lambda p: p["budget"].update(estimated_usd=61),
            lambda p: p["budget"].pop("exception"),
            lambda p: p["budget"]["exception"].pop("recorded_at"),
            lambda p: p["budget"]["exception"].update(reason=""),
            lambda p: p["budget"]["exception"].update(default_timeout_seconds=7200),
            lambda p: p["budget"]["exception"].update(extra=True),
            lambda p: p["budget"].update(extra_spend_authorized_usd=1),
        ):
            tampered = copy.deepcopy(policy)
            mutate(tampered)
            with self.subTest(policy=tampered["budget"]), self.assertRaises(workflow.WorkflowError):
                review_policy.validate_policy(tampered)
        # The reason and the recorded defaults are part of the policy, so every round and
        # packet digest covers them; a different record is a different policy.
        for mutate in (
            lambda p: p["budget"]["exception"].update(reason="another reason"),
            lambda p: p["budget"]["exception"].update(default_timeout_seconds=600),
        ):
            rebound = copy.deepcopy(policy)
            mutate(rebound)
            self.assertEqual(review_policy.validate_policy(rebound), rebound)
            self.assertNotEqual(review.value_digest(rebound), review.value_digest(policy))
        with self.assertRaisesRegex(workflow.WorkflowError, "Claude Code reviewer only"):
            profiles.review_selection(
                self.repo, self.cfg, review_provider="copilot", review_exception=exception()
            )

    def test_batch_units_never_inherit_the_exception(self):
        parent = profiles.review_selection(self.repo, self.cfg, review_exception=exception())["policy"]
        bounds = {
            "requests": 3,
            "kind": "reference-usd",
            "cost": "30",
            "seconds": 2700,
            "unit_cost": "10",
            "unit_seconds": 900,
            "max_report_bytes": 50000,
            "max_integration_bytes": 500000,
        }
        normalized, unit = review_policy.batch_budget(bounds, parent, 3)
        self.assertNotIn("exception", unit["budget"])
        self.assertEqual((unit["budget"]["timeout_seconds"], unit["budget"]["estimated_usd"]), (900, 10))
        self.assertEqual(review_policy.validate_policy(unit), unit)
        self.assertEqual(normalized["unit_cost"], "10")
        for mutation in ({"unit_seconds": 901, "seconds": 2703}, {"unit_cost": "10.5", "cost": "31.5"}):
            with (
                self.subTest(mutation=mutation),
                self.assertRaisesRegex(workflow.WorkflowError, "parent policy"),
            ):
                review_policy.batch_budget({**bounds, **mutation}, parent, 3)

    def test_packet_and_publication_carry_the_recorded_exception(self):
        self.commit_task()
        directory = review.prepare(self.repo, 31, 12, 1234, review_exception=exception(3600, 25))
        meta = review.verify_packet(directory)
        self.assertEqual(meta["review_policy"]["authentication"], AUTHENTICATION)
        self.assertEqual(
            (meta["reviewer"]["timeout_seconds"], meta["reviewer"]["max_estimated_usd"]), (3600, 25)
        )
        self.assertEqual(meta["reviewer"]["budget_exception"]["reason"], REASON)
        self.assertEqual(meta["reviewer"]["max_ai_credits"], None)
        packet = directory / "packet"
        rows = native_events(packet, packet, "fixture-session")
        raw = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        body, diagnostics = telemetry.capture(raw, packet, packet, meta["review_policy"], "fixture-session")
        review.save_result(directory, meta, body, diagnostics, "2.1.282")
        review.recover_review(self.repo, directory)
        published = review.publication_body(directory)
        self.assertIn(
            "Budget exception: timeout `3600 s`, reference ceiling `$25` (policy defaults 900 s / $10); "
            f"reason: {REASON}. Status:",
            published,
        )
        plain = review.prepare(self.repo, 31, 12, 1234, output=directory.parent / "plain")
        self.assertIsNone(review.verify_packet(plain)["reviewer"]["budget_exception"])
        self.assertEqual(review.exception_line(review.verify_packet(plain)["review_policy"]), "")
