"""Human finishing is exercised only against temporary Git and mocked GitHub."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from test_pipeline import PipelineFixture
from test_workflow import git, workflow

# The shared fixtures establish the scripts import path.
# isort: split
import finish
import pipeline
import review
import tasks


class FinishTests(PipelineFixture):
    def setUp(self):
        super().setUp()
        git(self.task_path, "push", "origin", "HEAD:issue-12-correct-value")
        self.queued = False
        self.threads = []
        self.merge_calls = []
        self.server_patch = patch.object(finish, "server_view", side_effect=self.server)
        self.server_patch.start()
        self.addCleanup(self.server_patch.stop)
        self.check_patch = patch.object(
            finish, "required_checks", return_value=[{"name": "quality", "bucket": "pass"}]
        )
        self.check_patch.start()
        self.addCleanup(self.check_patch.stop)
        with patch.object(review, "review", side_effect=self.model_double):
            pipeline.review_task(self.repo, 12, execute=True, publish=True)
        self.assessment_file = self.parent / "assessment.json"
        self.assess()

    def server(self, repo, number):
        return {
            "head_sha": self.pr_data["head"]["sha"],
            "base_sha": self.pr_data["base"]["sha"],
            "queued": self.queued,
            "threads": self.threads,
        }

    def assess(self):
        observed = pipeline.feedback(self.repo, 12)
        designation = tasks.TaskStore(self.repo).read("issue-12")["designated_review"]
        self.assessment = {
            "head_sha": self.head,
            "base_sha": self.base,
            "review_id": designation["review_id"],
            "feedback_digest": observed["feedback_digest"],
            "pr_digest": observed["pr_digest"],
            "summary": "All findings assessed individually; no unresolved material blocker.",
            "limitations": "Mocked GitHub and model execution; no live account validation.",
            "domain_evidence": "No scientific claim is made by this fixture.",
            "acceptance": [
                {
                    "criterion": "Correct the fixture value",
                    "supported": True,
                    "evidence": [self.pr_data["html_url"]],
                }
            ],
            "records": [
                {
                    "id": identifier,
                    "digest": record["digest"],
                    "disposition": "no-action",
                    "rationale": "The supplied record supports no remaining material defect.",
                    "evidence": [record["url"] or self.pr_data["html_url"]],
                }
                for identifier, record in observed["records"].items()
            ],
        }
        self.assessment_file.write_text(json.dumps(self.assessment))

    def prepare(self):
        return finish.prepare_finish(self.repo, 12, self.assessment_file)

    def mark_merged(self):
        git(self.root, "merge", "--squash", "issue-12-correct-value")
        git(self.root, "commit", "-m", "fixture squash merge")
        self.issue["state"] = "closed"
        self.pr_data.update(
            merged=True,
            state="closed",
            merge_commit_sha=git(self.root, "rev-parse", "HEAD"),
        )
        self.pr_data["base"]["sha"] = self.pr_data["merge_commit_sha"]

    def human(self, outcome="merged"):
        original = finish.run

        def fake(args, **kwargs):
            if args[:3] == ["gh", "pr", "merge"]:
                self.merge_calls.append(args)
                if outcome in {"merged", "merged-error"}:
                    self.mark_merged()
                elif outcome == "queued":
                    self.queued = True
                return subprocess.CompletedProcess(args, 1 if outcome == "merged-error" else 0, "", "")
            return original(args, **kwargs)

        with patch.object(finish, "run", side_effect=fake):
            return finish.finish_task(self.repo, 12, self.head, self.base)

    def test_preparation_does_not_merge_or_move_ignored_artifacts(self):
        (self.task_path / "valuable.cache").write_text("result")
        result = self.prepare()
        self.assertTrue(result["qualified"])
        self.assertIn("--head " + self.head, result["command"])
        self.assertIn("--base " + self.base, result["command"])
        self.assertFalse(Path(result["archive"]).exists())
        self.assertTrue((self.task_path / "valuable.cache").exists())
        self.assertFalse(self.pr_data["merged"])

    def test_later_feedback_requires_reassessment_before_merge(self):
        self.prepare()
        self.conversation.append({"id": 8000, "body": "New material evidence"})
        with self.assertRaisesRegex(workflow.WorkflowError, "feedback changed"):
            self.human()
        self.assertEqual(self.merge_calls, [])
        self.assertTrue(self.task_path.exists())

    def test_old_head_findings_must_be_explicitly_assessed(self):
        self.reviews.append(
            {
                "id": 777,
                "commit_id": self.base,
                "state": "COMMENTED",
                "body": "Old-head finding still needs a disposition",
                "html_url": self.pr_data["html_url"] + "#review-777",
            }
        )
        self.assess()
        self.assessment["records"] = [
            record for record in self.assessment["records"] if record["id"] != "review:777"
        ]
        self.assessment_file.write_text(json.dumps(self.assessment))
        with self.assertRaisesRegex(workflow.WorkflowError, "old heads"):
            self.prepare()

    def test_accepted_out_of_scope_follow_up_can_qualify(self):
        record = self.assessment["records"][0]
        record.update(
            disposition="deferred",
            rationale="Valid out-of-scope concern explicitly accepted for separate work.",
            follow_up="https://github.com/example/project/issues/99",
            acceptance_source="Maintainer accepted this disposition in the coordinating conversation",
        )
        self.assessment_file.write_text(json.dumps(self.assessment))
        original = finish.run

        def fake(args, **kwargs):
            if args[:3] == ["gh", "api", "repos/example/project/issues/99"]:
                return subprocess.CompletedProcess(args, 0, '{"state":"open"}', "")
            return original(args, **kwargs)

        with patch.object(finish, "run", side_effect=fake):
            self.assertTrue(self.prepare()["qualified"])
        del record["acceptance_source"]
        self.assessment_file.write_text(json.dumps(self.assessment))
        with self.assertRaisesRegex(workflow.WorkflowError, "acceptance source"):
            self.prepare()

    def test_unresolved_threads_block_preparation(self):
        self.threads = [{"id": "thread-1", "isResolved": False}]
        with self.assertRaisesRegex(workflow.WorkflowError, "unresolved"):
            self.prepare()

    def test_queued_merge_never_archives_or_removes_worktree(self):
        (self.task_path / "valuable.cache").write_text("result")
        prepared = self.prepare()
        result = self.human("queued")
        self.assertTrue(result["incomplete"])
        self.assertTrue(result["queued"])
        self.assertFalse(result["archived"])
        self.assertTrue((self.task_path / "valuable.cache").exists())
        self.assertFalse(Path(prepared["archive"]).exists())
        self.assertEqual(len(self.merge_calls), 1)
        repeated = self.human("queued")
        self.assertTrue(repeated["incomplete"])
        self.assertEqual(len(self.merge_calls), 1)

    def test_merge_error_is_requeried_and_closed_issue_does_not_block_cleanup(self):
        (self.task_path / "valuable.cache").write_text("result")
        self.prepare()
        result = self.human("merged-error")
        self.assertTrue(result["merged"])
        self.assertTrue(result["archived"])
        self.assertTrue(result["local_cleanup"])
        self.assertFalse(result["incomplete"])
        self.assertEqual(self.issue["state"], "closed")
        self.assertFalse(self.task_path.exists())
        self.assertEqual((Path(result["archive"]) / "payload/valuable.cache").read_text(), "result")
        args = self.merge_calls[0]
        self.assertNotIn("--admin", args)
        self.assertNotIn("--delete-branch", args)
        self.assertEqual(args[args.index("--match-head-commit") + 1], self.head)

    def test_remote_deletion_retry_does_not_require_worktree_or_open_issue(self):
        self.prepare()
        with patch.object(finish, "delete_remote", side_effect=workflow.WorkflowError("network down")):
            first = self.human()
        self.assertTrue(first["local_cleanup"])
        self.assertTrue(first["incomplete"])
        self.assertFalse(self.task_path.exists())
        second = self.human()
        self.assertFalse(second["incomplete"])
        self.assertIn(second["remote_branch"], {"deleted", "absent"})
        self.assertEqual(len(self.merge_calls), 1)

    def test_advanced_remote_branch_is_preserved(self):
        self.prepare()
        original = finish.delete_remote

        def advance(repo, state, head):
            git(self.root, "push", "--force", "origin", "trunk:issue-12-correct-value")
            return original(repo, state, head)

        with patch.object(finish, "delete_remote", side_effect=advance):
            result = self.human()
        self.assertEqual(result["remote_branch"], "advanced")
        self.assertTrue(result["incomplete"])
        self.assertTrue(git(self.root, "ls-remote", "--heads", "origin", "issue-12-correct-value"))

    def test_changed_local_tip_blocks_final_cleanup(self):
        self.prepare()
        original = finish.cleanup_task

        def changed(repo, number, expected_sha=None):
            (self.task_path / "later.py").write_text("valuable follow-up\n")
            git(self.task_path, "add", "later.py")
            git(self.task_path, "commit", "-m", "unmerged follow-up")
            return original(repo, number, expected_sha=expected_sha)

        with patch.object(finish, "cleanup_task", side_effect=changed):
            result = self.human()
        self.assertTrue(result["merged"])
        self.assertFalse(result["local_cleanup"])
        self.assertTrue((self.task_path / "later.py").exists())

    def test_required_checks_fail_closed(self):
        self.check_patch.stop()
        for checks in ([], [{"name": "quality", "bucket": "skipping"}]):
            with patch.object(
                finish,
                "run",
                return_value=subprocess.CompletedProcess([], 0, json.dumps(checks), ""),
            ):
                with self.assertRaises(workflow.WorkflowError):
                    finish.required_checks(self.repo, 31)
