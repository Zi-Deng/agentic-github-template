"""PR and independent-review integration using Git fixtures and model doubles."""

import contextlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

from review_fixtures import store as store_review
from test_workflow import GitFixture, git, workflow

# The shared fixture establishes the scripts import path.
# isort: split
import pipeline
import profiles
import review
import tasks


class PipelineFixture(GitFixture):
    def setUp(self):
        super().setUp()
        self.inline = []
        self.conversation = []
        self.commit_task()
        self.pr_data.update(
            {
                "number": 31,
                "id": 3100,
                "html_url": "https://github.com/example/project/pull/31",
                "title": "Earlier title",
                "body": "Earlier evidence\n\nFixes #12\n",
            }
        )
        tasks.approve_plan(self.repo, 12, 1234, "Explicit task authorization")
        tasks.prepare(self.repo, 12, "correct-value")
        pipeline.bind_pr(self.repo, 12, 31)
        self.body_file = self.parent / "pr-body.md"
        self.body_file.write_text("Current validation evidence\n\nFixes #12\n")
        self.model_runs = 0

    def api(self, suffix, *, data=None, **kwargs):
        if data is not None:
            self.posts.append((suffix, data))
            if suffix == "pulls/31":
                self.pr_data.update(data)
                return self.pr_data
            if suffix == "pulls/31/reviews":
                item = {
                    "id": 9000 + len(self.reviews),
                    "body": data["body"],
                    "commit_id": data["commit_id"],
                    "state": "COMMENTED",
                    "html_url": f"https://github.com/example/project/pull/31#review-{len(self.reviews)}",
                }
                self.reviews.append(item)
                return item
            if suffix == "issues/31/comments":
                item = {
                    "id": 8000 + len(self.conversation),
                    "body": data["body"],
                    "html_url": "https://github.com/example/project/pull/31#response",
                }
                self.conversation.append(item)
                return item
        if suffix.startswith("pulls?"):
            return [self.pr_data]
        if suffix.startswith("pulls/31/reviews"):
            return self.reviews
        if suffix.startswith("pulls/31/comments"):
            return self.inline
        if suffix.startswith("issues/31/comments"):
            return self.conversation
        return super().api(suffix, data=data, **kwargs)

    def model_double(self, repo, directory):
        # A synthetic session that views every required range: qualifies without inference.
        self.model_runs += 1
        return store_review(repo, directory)


class PipelineTests(PipelineFixture):
    def test_prepared_model_compatibility_change_requires_fresh_packet(self):
        entry = {
            "provider": "copilot",
            "model": "claude-fixture-99",
            "efforts": ["default"],
            "cli_version": "1.0.83",
            "adapter": "copilot-session-events-v2",
            "evidence": [
                "https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference"
            ],
        }
        cfg = workflow.configuration(self.root)
        cfg["review_model_extensions"] = [entry]
        workflow.write_json(self.root / ".agentic/config.json", cfg)
        git(self.root, "commit", "-qam", "declare fixture model compatibility")
        first = pipeline.review_task(self.repo, 12, review_model=entry["model"])
        packet = Path(first["directory"]) / "metadata.json"
        original = packet.read_bytes()
        entry["efforts"].append("high")
        workflow.write_json(self.root / ".agentic/config.json", cfg)
        git(self.root, "commit", "-qam", "extend fixture model compatibility")
        with self.assertRaisesRegex(workflow.WorkflowError, "--fresh"):
            pipeline.review_task(self.repo, 12, review_model=entry["model"])
        self.assertEqual(packet.read_bytes(), original)
        fresh = pipeline.review_task(self.repo, 12, review_model=entry["model"], fresh=True)
        self.assertNotEqual(fresh["directory"], first["directory"])
        self.assertEqual(self.model_runs, 0)

    def test_prepared_selection_is_immutable_and_fresh_override_packet_is_explicit(self):
        result = pipeline.review_task(self.repo, 12, review_model="claude-sonnet-5")
        directory = Path(result["directory"])
        original = (directory / "metadata.json").read_bytes()
        for overrides in (
            {"review_model": "claude-opus-5.5"},
            {"review_model": "claude-sonnet-5", "review_effort": "high"},
            {},
        ):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(workflow.WorkflowError, "--fresh"):
                pipeline.review_task(self.repo, 12, **overrides)
            self.assertEqual((directory / "metadata.json").read_bytes(), original)
        fresh = pipeline.review_task(self.repo, 12, fresh=True)
        self.assertNotEqual(fresh["directory"], result["directory"])
        self.assertEqual(fresh["review_policy"]["model"], "claude-opus-5")
        self.assertEqual(fresh["model"], "claude-opus-5")

    def test_attempted_recovery_uses_bound_policy_and_override_needs_continuation(self):
        with patch.object(review, "review", side_effect=self.model_double):
            first = pipeline.review_task(self.repo, 12, execute=True)
        profiles.use_profile(self.repo, "fable-gpt")
        with patch.object(review, "review", side_effect=AssertionError("Recovery cannot infer again")):
            recovered = pipeline.review_task(self.repo, 12, execute=True, publish=True)
            self.assertEqual(recovered["directory"], first["directory"])
            self.assertEqual(recovered["review_policy"]["model"], "claude-opus-5")
            self.assertEqual(recovered["status"], "published")
            with self.assertRaisesRegex(workflow.WorkflowError, "--fresh"):
                pipeline.review_task(self.repo, 12, execute=True, review_model="gpt-6-astra")
            with self.assertRaisesRegex(workflow.WorkflowError, "continuation"):
                pipeline.review_task(self.repo, 12, execute=True, fresh=True)
        self.assertEqual(self.model_runs, 1)

    def test_attempted_recovery_does_not_resolve_the_current_selection(self):
        with patch.object(review, "review", side_effect=self.model_double):
            first = pipeline.review_task(self.repo, 12, execute=True)
        with (
            patch.object(
                pipeline, "review_selection", side_effect=workflow.WorkflowError("Obsolete selection")
            ),
            patch.object(review, "review", side_effect=AssertionError("Recovery cannot infer")),
        ):
            recovered = pipeline.review_task(self.repo, 12, execute=True, publish=True)
            self.assertEqual(recovered["review_policy"], first["review_policy"])
            with self.assertRaisesRegex(workflow.WorkflowError, "Obsolete selection"):
                pipeline.review_task(self.repo, 12, fresh=True)
        self.assertEqual(self.model_runs, 1)

    def test_partial_report_is_published_without_readiness_designation(self):
        with patch.object(
            review,
            "review",
            side_effect=lambda repo, directory: store_review(
                repo, directory, "Incomplete source inspection.\n"
            ),
        ):
            result = pipeline.review_task(self.repo, 12, execute=True, publish=True)
        self.assertEqual(result["status"], "published-incomplete")
        self.assertTrue(result["incomplete"])
        self.assertIsNone(result["designated_review"])
        self.assertEqual(result["attempted_rounds"], 1)
        self.assertIn("INCOMPLETE", self.reviews[0]["body"])
        with self.assertRaises(workflow.WorkflowError):
            pipeline.validate_designated(self.repo, tasks.TaskStore(self.repo).read("issue-12"))

    def test_pre_coverage_round_publishes_but_cannot_be_designated(self):
        # A round whose packet predates the coverage gate (schema 1, report written directly)
        # is still published exactly, but never designated or finished.
        with patch.object(review, "review", side_effect=self.model_double):
            result = pipeline.review_task(self.repo, 12, execute=True)
        directory = Path(result["directory"])
        meta = review.verify_packet(directory)
        for name in (
            "review-result.json",
            "review-capture.json",
            "diagnostics.json",
            "coverage.json",
            "attempt.json",
            "usage.json",
        ):
            (directory / name).unlink(missing_ok=True)
        legacy = {
            key: value
            for key, value in meta.items()
            if key
            not in {
                "kind",
                "overrides",
                "selection_sources",
                "diagnostics_sha256",
                "coverage_sha256",
                "provider_version",
            }
        }
        legacy.update(schema_version=1, copilot_version="pre-coverage double")
        review.atomic_json(directory / "metadata.json", legacy)
        with patch.object(review, "review", side_effect=AssertionError("No rerun")):
            published = pipeline.review_task(self.repo, 12, execute=True, publish=True)
        self.assertEqual(published["status"], "published-incomplete")
        self.assertIsNone(published["designated_review"])
        self.assertTrue(
            self.reviews[0]["body"].endswith(f"<!-- agentic-review:{self.head}:{legacy['review_sha256']} -->")
        )
        with self.assertRaises(workflow.WorkflowError):
            pipeline.validate_designated(self.repo, tasks.TaskStore(self.repo).read("issue-12"))

    def test_missing_diagnostics_refuse_even_a_manually_changed_designation(self):
        with patch.object(review, "review", side_effect=self.model_double):
            result = pipeline.review_task(self.repo, 12, execute=True, publish=True)
        self.assertTrue(result["coverage_qualified"])
        (Path(result["directory"]) / "diagnostics.json").unlink()
        with self.assertRaises(workflow.WorkflowError):
            pipeline.validate_designated(self.repo, tasks.TaskStore(self.repo).read("issue-12"))

    def test_existing_pr_receives_push_and_updated_evidence(self):
        result = pipeline.publish_pr(self.repo, 12, "Current title", self.body_file)
        self.assertEqual(result["pr"], 31)
        self.assertEqual(self.pr_data["title"], "Current title")
        self.assertEqual(self.pr_data["body"], self.body_file.read_text())
        self.assertEqual(
            git(self.root, "ls-remote", "--heads", "origin", "issue-12-correct-value").split()[0],
            self.head,
        )
        self.assertTrue(any(endpoint == "pulls/31" for endpoint, _ in self.posts))

    def test_accepted_pr_patch_is_reconciled_after_transport_error(self):
        original = self.repo.api

        def interrupted(suffix, **kwargs):
            result = original(suffix, **kwargs)
            if suffix == "pulls/31" and kwargs.get("data") is not None:
                raise workflow.WorkflowError("connection lost after PATCH")
            return result

        with patch.object(self.repo, "api", side_effect=interrupted):
            result = pipeline.publish_pr(self.repo, 12, "Updated title", self.body_file)
        self.assertTrue(result["updated"])
        self.assertEqual(self.pr_data["title"], "Updated title")

    def test_feedback_covers_reviews_inline_and_conversation(self):
        self.reviews.append(
            {"id": 1, "commit_id": self.head, "state": "COMMENTED", "body": "No action needed"}
        )
        self.inline.append({"id": 2, "commit_id": self.head, "body": "Inspect this edge"})
        self.conversation.append({"id": 3, "body": "Public context"})
        result = pipeline.feedback(self.repo, 12)
        self.assertEqual(set(result["records"]), {"review:1", "inline:2"})
        self.assertEqual(result["feedback"]["conversation"], self.conversation)
        self.assertTrue(result["records"]["review:1"]["digest"])

    def test_response_publication_is_idempotent(self):
        first = pipeline.respond(self.repo, 12, "round-one", self.body_file)
        second = pipeline.respond(self.repo, 12, "round-one", self.body_file)
        self.assertEqual(first, second)
        self.assertEqual(len(self.conversation), 1)

    def test_pipeline_designates_only_its_published_current_report(self):
        with patch.object(review, "review", side_effect=self.model_double):
            result = pipeline.review_task(self.repo, 12, execute=True, publish=True)
        self.assertEqual(result["status"], "published")
        store = tasks.TaskStore(self.repo)
        verified = pipeline.validate_designated(self.repo, store.read("issue-12"))
        self.assertEqual(verified["review"]["commit_id"], self.head)
        self.assertEqual(verified["review"]["state"], "COMMENTED")
        self.pr_data["base"]["sha"] = "a" * 40
        with self.assertRaisesRegex(workflow.WorkflowError, "stale"):
            pipeline.validate_designated(self.repo, store.read("issue-12"))

    def test_arbitrary_current_comment_does_not_establish_pipeline_readiness(self):
        self.reviews.append({"id": 1, "commit_id": self.head, "state": "COMMENTED", "body": "Looks fine"})
        state = tasks.TaskStore(self.repo).read("issue-12")
        with self.assertRaisesRegex(workflow.WorkflowError, "No designated"):
            pipeline.validate_designated(self.repo, state)

    def test_second_model_round_requires_explicit_reason_and_continuation(self):
        store = tasks.TaskStore(self.repo)
        reason = "Fixture operator explicitly approved another review of the recovery change"
        with patch.object(review, "review", side_effect=self.model_double):
            pipeline.review_task(self.repo, 12, execute=True)
            original_rounds = store.read("issue-12")["review_rounds"]
            for approved, supplied_reason in (
                (False, None),
                (True, None),
                (True, ""),
                (True, " \n\t "),
                (False, reason),
            ):
                with self.subTest(approved=approved, reason=supplied_reason):
                    with self.assertRaisesRegex(workflow.WorkflowError, "explicit continuation"):
                        pipeline.review_task(
                            self.repo,
                            12,
                            execute=True,
                            fresh=True,
                            continue_reason=supplied_reason,
                            approved_continuation=approved,
                        )
                    self.assertEqual(self.model_runs, 1)
                    self.assertEqual(store.read("issue-12")["review_rounds"], original_rounds)
            result = pipeline.review_task(
                self.repo,
                12,
                execute=True,
                fresh=True,
                continue_reason=reason,
                approved_continuation=True,
            )
            self.assertEqual(result["attempted_rounds"], 2)
            saved_rounds = store.read("issue-12")["review_rounds"]
            self.assertEqual(saved_rounds[:-1], original_rounds)
            self.assertEqual(
                saved_rounds[-1]["continuation"],
                {"approved": True, "reason": reason},
            )
            with self.assertRaisesRegex(workflow.WorkflowError, "explicit continuation"):
                pipeline.review_task(self.repo, 12, execute=True, fresh=True)
            self.assertEqual(store.read("issue-12")["review_rounds"], saved_rounds)
        self.assertEqual(self.model_runs, 2)

    def test_failed_model_run_is_not_silently_restarted(self):
        with patch.object(review, "review", side_effect=workflow.WorkflowError("model unavailable")):
            with self.assertRaisesRegex(workflow.WorkflowError, "model unavailable"):
                pipeline.review_task(self.repo, 12, execute=True)
        with patch.object(review, "review") as model:
            with self.assertRaises((workflow.WorkflowError, OSError)):
                pipeline.review_task(self.repo, 12, execute=True)
        model.assert_not_called()
        state = tasks.TaskStore(self.repo).read("issue-12")
        self.assertEqual(sum(r["run_attempted"] for r in state["review_rounds"]), 1)

    def test_report_changes_invalidate_designated_review(self):
        with patch.object(review, "review", side_effect=self.model_double):
            result = pipeline.review_task(self.repo, 12, execute=True, publish=True)
        (Path(result["directory"]) / "review.md").write_text("Edited report")
        with self.assertRaisesRegex(workflow.WorkflowError, "changed"):
            pipeline.validate_designated(self.repo, tasks.TaskStore(self.repo).read("issue-12"))

    def test_review_round_freezes_the_reviewer_and_survives_a_profile_switch(self):
        with patch.object(review, "review", side_effect=self.model_double):
            result = pipeline.review_task(self.repo, 12, execute=True, publish=True)
        self.assertEqual((result["model"], result["profile"]), ("claude-opus-5", "astra-copilot"))
        store = tasks.TaskStore(self.repo)
        state = store.read("issue-12")
        self.assertEqual(state["review_rounds"][-1]["reviewer_model"], "claude-opus-5")
        self.assertFalse(state["review_rounds"][-1]["same_family"])
        directory = Path(result["directory"])
        meta = json.loads((directory / "metadata.json").read_text())
        self.assertEqual(meta["reviewer"]["model"], "claude-opus-5")
        self.assertEqual(meta["provenance"]["profile"], "astra-copilot")
        with patch.dict(os.environ, {profiles.ENV_NAME: "fable-gpt"}):
            verified = pipeline.validate_designated(self.repo, store.read("issue-12"))
        self.assertEqual(verified["review"]["commit_id"], self.head)
        meta["requested_model"] = "gpt-6-astra"
        workflow.write_json(directory / "metadata.json", meta)
        with self.assertRaisesRegex(workflow.WorkflowError, "differs"):
            pipeline.validate_designated(self.repo, store.read("issue-12"))

    def test_same_family_round_requires_acknowledgement(self):
        store = tasks.TaskStore(self.repo)
        with store.locked("issue-12") as state:
            state["executor"] = {
                "uuid": "12345678-1234-4234-8234-123456789abc",
                "runs": [],
                "backend": "claude",
                "model": "claude-fable-5-1",
                "profile": "fable-gpt",
            }
            store.save(state)
        with patch.object(review, "review", side_effect=self.model_double):
            with self.assertRaisesRegex(workflow.WorkflowError, "implementer family"):
                pipeline.review_task(self.repo, 12, execute=True)
            # An allowance recorded for the profile's own (codex) pairing does not transfer
            # to this task's pinned Claude implementer.
            profiles.use_profile(self.repo, "astra-copilot", allow_same_family=True, reason="other pairing")
            with self.assertRaisesRegex(workflow.WorkflowError, "implementer family"):
                pipeline.review_task(self.repo, 12, execute=True)
            profiles.clear_profile(self.repo)
            self.assertEqual(self.model_runs, 0)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = pipeline.review_task(self.repo, 12, execute=True, allow_same_family=True)
        self.assertEqual(result["status"], "reviewed")
        self.assertIn("warning:", stderr.getvalue())
        record = store.read("issue-12")["review_rounds"][-1]
        self.assertTrue(record["same_family"])
        self.assertTrue(record["same_family_acknowledged"])
        meta = json.loads((Path(result["directory"]) / "metadata.json").read_text())
        self.assertTrue(meta["provenance"]["same_family"])
        self.assertEqual(meta["provenance"]["implementer"]["family"], "anthropic")

    def test_round_without_reviewer_model_trusts_its_own_packet(self):
        with patch.dict(os.environ, {profiles.ENV_NAME: "fable-gpt"}):
            with patch.object(review, "review", side_effect=self.model_double):
                result = pipeline.review_task(self.repo, 12, execute=True, publish=True)
        store = tasks.TaskStore(self.repo)
        with store.locked("issue-12") as state:
            for field in ("reviewer_model", "reviewer_backend", "reviewer_family", "profile"):
                state["review_rounds"][-1].pop(field, None)
            store.save(state)
        with contextlib.redirect_stderr(io.StringIO()):
            verified = pipeline.validate_designated(self.repo, store.read("issue-12"))
        self.assertEqual(verified["review"]["commit_id"], self.head)
        meta = json.loads((Path(result["directory"]) / "metadata.json").read_text())
        self.assertEqual(meta["requested_model"], "gpt-6-astra")

    def test_prepared_round_for_another_reviewer_needs_fresh(self):
        prepared = pipeline.review_task(self.repo, 12)
        self.assertEqual(prepared["status"], "prepared")
        with (
            patch.dict(os.environ, {profiles.ENV_NAME: "fable-gpt"}),
            patch.object(review, "review", side_effect=self.model_double),
        ):
            with self.assertRaisesRegex(workflow.WorkflowError, "--fresh"):
                pipeline.review_task(self.repo, 12, execute=True)
            self.assertEqual(self.model_runs, 0)
            result = pipeline.review_task(self.repo, 12, execute=True, fresh=True)
        self.assertEqual((result["model"], result["profile"]), ("gpt-6-astra", "fable-gpt"))
        self.assertEqual(self.model_runs, 1)
