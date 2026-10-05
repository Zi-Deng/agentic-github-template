"""Exercise destructive boundaries using real temporary Git repositories."""

import contextlib
import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE / "scripts/agentic"))
import install  # noqa: E402
import profiles  # noqa: E402
import review  # noqa: E402
import review_policy  # noqa: E402
import workflow  # noqa: E402


def git(path, *args):
    return workflow.run(["git", "-C", path, *args]).stdout.strip()


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agentic-test-")
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = self.parent / "project with spaces"
        self.root.mkdir()
        self.remote = self.parent / "origin.git"
        git(self.parent, "init", "--bare", "--initial-branch=trunk", self.remote)
        git(self.root, "init", "--initial-branch=trunk")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "config", "user.name", "Workflow Test")
        git(self.root, "config", "commit.gpgsign", "false")
        git(self.root, "config", "core.hooksPath", "/dev/null")
        (self.root / "code.py").write_text("value = 1\n")
        (self.root / ".gitignore").write_text("/memory/\n/.agentic-local/\n*.cache\n")
        for item in [".agentic", ".github/agents", "docs/agent-workflow"]:
            if (SOURCE / item).exists():
                shutil.copytree(SOURCE / item, self.root / item)
        shutil.copyfile(SOURCE / "AGENTS.md", self.root / "AGENTS.md")
        # Minimal policy text keeps this fixture independent of documentation wording.
        for name in ["REVIEW.md", "domain-review.md"]:
            p = self.root / "docs/agent-workflow" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text("Static review only.\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        git(self.root, "remote", "add", "origin", self.remote)
        git(self.root, "push", "-u", "origin", "trunk")
        self.repo = workflow.Repo(self.root)
        self.repo._info = {
            "nameWithOwner": "example/project",
            "defaultBranchRef": {"name": "trunk"},
            "isPrivate": False,
        }
        self.base = git(self.root, "rev-parse", "HEAD")
        self.issue = {
            "state": "open",
            "title": "Correct value",
            "url": "https://api.github.com/repos/example/project/issues/12",
        }
        self.repo.api = self.api
        self.reviews = []
        self.posts = []
        self.pr_data = None
        self.environ = patch.dict(os.environ)
        self.environ.start()
        self.addCleanup(self.environ.stop)
        os.environ.pop("WT_ROOT", None)
        # These fixtures simulate coordinator operations even when their caller
        # is a managed executor. Dedicated tests explicitly restore the guard.
        os.environ.pop("AGENTIC_EXECUTOR_ROLE", None)
        os.environ.pop("AGENTIC_PROFILE", None)

    def api(self, suffix, *, data=None, **kwargs):
        if data is not None:
            self.posts.append((suffix, data))
            return {"html_url": "https://github.com/example/project/pull/31#review-1"}
        if suffix == "issues/12":
            return self.issue
        if suffix == "issues/comments/1234":
            return {"issue_url": self.issue["url"], "body": "Approved plan"}
        if suffix == "pulls/31":
            return self.pr_data
        if suffix.endswith("/reviews"):
            return self.reviews
        return []

    def task(self):
        data = workflow.new_task(self.repo, "12", "correct-value")
        self.task_path = Path(data["worktree"])
        return data

    def commit_task(self):
        self.task()
        (self.task_path / "code.py").write_text("value = 2\n")
        git(self.task_path, "add", "code.py")
        git(self.task_path, "commit", "-m", "change")
        self.head = git(self.task_path, "rev-parse", "HEAD")
        git(self.task_path, "push", "origin", "HEAD:refs/pull/31/head")
        self.pr_data = {
            "state": "open",
            "merged": False,
            "draft": False,
            "mergeable": True,
            "head": {
                "sha": self.head,
                "ref": "issue-12-correct-value",
                "repo": {"full_name": self.repo.name},
            },
            "base": {"sha": self.base, "ref": "trunk"},
        }

    def merged(self):
        self.commit_task()
        git(self.root, "merge", "--squash", "issue-12-correct-value")
        git(self.root, "commit", "-m", "squash change")
        self.pr_data["merged"] = True
        self.pr_data["state"] = "closed"


class WorktreeTests(GitFixture):
    def test_new_task_creates_isolated_pushed_branch_from_non_main_default(self):
        result = self.task()
        self.assertEqual(git(self.root, "branch", "--show-current"), "trunk")
        self.assertEqual(git(self.task_path, "rev-parse", "HEAD"), self.base)
        self.assertEqual(
            git(self.task_path, "rev-parse", "--abbrev-ref", "@{u}"), "origin/issue-12-correct-value"
        )
        self.assertEqual(Path(result["worktree"]).parent, self.root.parent / (self.root.name + "-worktrees"))
        (self.task_path / "code.py").write_text("isolated\n")
        self.assertEqual((self.root / "code.py").read_text(), "value = 1\n")

    def test_dirty_main_is_preserved(self):
        (self.root / "code.py").write_text("unsaved work\n")
        with self.assertRaisesRegex(workflow.WorkflowError, "dirty"):
            self.task()
        self.assertEqual((self.root / "code.py").read_text(), "unsaved work\n")

    def test_closed_issue_and_pr_number_are_refused(self):
        for change in [{"state": "closed"}, {"state": "open", "pull_request": {}}]:
            self.issue.update(change)
            with self.assertRaisesRegex(workflow.WorkflowError, "open issue"):
                self.task()

    def test_path_traversal_and_shell_metacharacters_are_refused(self):
        for number, slug in [
            ("0", "thing"),
            ("-1", "thing"),
            ("12", "../x"),
            ("12", "x;touch-pwned"),
            ("12", "a/b"),
        ]:
            with self.assertRaises(workflow.WorkflowError):
                workflow.new_task(self.repo, number, slug)

    def test_duplicate_worktree_is_refused(self):
        self.task()
        with self.assertRaisesRegex(workflow.WorkflowError, "already exists"):
            self.task()

    def test_existing_local_branch_is_reused(self):
        git(self.root, "branch", "issue-12-correct-value")
        self.task()
        self.assertEqual(git(self.task_path, "rev-parse", "HEAD"), self.base)

    def test_existing_remote_branch_is_reused(self):
        git(self.root, "push", "origin", "trunk:issue-12-correct-value")
        self.task()
        self.assertEqual(git(self.task_path, "rev-parse", "HEAD"), self.base)

    def test_nested_worktree_root_is_refused(self):
        os.environ["WT_ROOT"] = str(self.root / "nested")
        with self.assertRaisesRegex(workflow.WorkflowError, "outside"):
            self.task()

    def test_cleanup_requires_merge(self):
        self.commit_task()
        with self.assertRaisesRegex(workflow.WorkflowError, "MERGED"):
            workflow.cleanup_task(self.repo, 31)
        self.assertTrue(self.task_path.exists())

    def test_squash_cleanup_removes_only_verified_branch_and_is_repeatable(self):
        self.merged()
        self.assertNotEqual(git(self.root, "rev-parse", "HEAD"), self.head)
        workflow.cleanup_task(self.repo, 31)
        self.assertFalse(self.task_path.exists())
        self.assertNotIn("issue-12-correct-value", git(self.root, "branch", "--list"))
        workflow.cleanup_task(self.repo, 31)

    def test_cleanup_preserves_post_merge_local_commits(self):
        self.merged()
        (self.task_path / "later.py").write_text("value = 3\n")
        git(self.task_path, "add", ".")
        git(self.task_path, "commit", "-m", "unmerged follow-up")
        with self.assertRaisesRegex(workflow.WorkflowError, "tip differs"):
            workflow.cleanup_task(self.repo, 31)
        self.assertTrue((self.task_path / "later.py").exists())

    def test_cleanup_preserves_ignored_data(self):
        self.merged()
        (self.task_path / "valuable.cache").write_text("experiment result")
        with self.assertRaisesRegex(workflow.WorkflowError, "ignored"):
            workflow.cleanup_task(self.repo, 31)
        self.assertTrue((self.task_path / "valuable.cache").exists())

    def test_cleanup_preserves_dirty_worktree(self):
        self.merged()
        (self.task_path / "code.py").write_text("unsaved")
        with self.assertRaisesRegex(workflow.WorkflowError, "changed"):
            workflow.cleanup_task(self.repo, 31)

    def test_cleanup_rejects_fork_and_wrong_base(self):
        self.merged()
        self.pr_data["head"]["repo"]["full_name"] = "outsider/project"
        with self.assertRaisesRegex(workflow.WorkflowError, "fork"):
            workflow.cleanup_task(self.repo, 31)
        self.pr_data["head"]["repo"]["full_name"] = self.repo.name
        self.pr_data["base"]["ref"] = "release"
        with self.assertRaisesRegex(workflow.WorkflowError, "default branch"):
            workflow.cleanup_task(self.repo, 31)

    def test_cleanup_rejects_moved_worktree(self):
        self.merged()
        git(self.root, "worktree", "move", self.task_path, self.parent / "unexpected")
        with self.assertRaisesRegex(workflow.WorkflowError, "unexpected"):
            workflow.cleanup_task(self.repo, 31)

    def test_main_operation_refuses_task_checkout(self):
        self.task()
        other = workflow.Repo(self.task_path)
        other._info = self.repo.info
        with self.assertRaisesRegex(workflow.WorkflowError, "main checkout"):
            other.assert_main()

    def test_ruleset_uses_discovered_branch_and_concrete_checks(self):
        data = workflow.ruleset(self.repo, ["quality"])
        self.assertEqual(data["conditions"]["ref_name"]["include"], ["refs/heads/trunk"])
        self.assertEqual(data["bypass_actors"], [])
        self.assertEqual(data["rules"][3]["parameters"]["required_approving_review_count"], 0)

    def test_merge_preflight_rejects_stale_head_without_merging(self):
        self.commit_task()
        with self.assertRaisesRegex(workflow.WorkflowError, "head changed"):
            workflow.merge_preflight(self.repo, 31, self.base)

    def test_merge_preflight_requires_recorded_review(self):
        self.commit_task()
        with self.assertRaisesRegex(workflow.WorkflowError, "No published review"):
            workflow.merge_preflight(self.repo, 31, self.head)

    def test_merge_preflight_rejects_no_checks_or_skipped_checks(self):
        self.commit_task()
        self.reviews = [{"commit_id": self.head, "state": "COMMENTED"}]
        original = workflow.run
        for checks in [[], [{"name": "quality", "bucket": "skipping", "state": "SKIPPED"}]]:

            def fake(args, checks=checks, **kwargs):
                if args[:3] == ["gh", "pr", "checks"]:
                    return subprocess.CompletedProcess(args, 0, json.dumps(checks), "")
                return original(args, **kwargs)

            with patch.object(workflow, "run", side_effect=fake), self.assertRaises(workflow.WorkflowError):
                workflow.merge_preflight(self.repo, 31, self.head)

    def test_merge_preflight_emits_pinned_command_only(self):
        self.commit_task()
        self.reviews = [{"commit_id": self.head, "state": "COMMENTED"}]
        original = workflow.run

        def fake(args, **kwargs):
            if args[:3] == ["gh", "pr", "checks"]:
                return subprocess.CompletedProcess(
                    args, 0, json.dumps([{"name": "quality", "bucket": "pass"}]), ""
                )
            return original(args, **kwargs)

        with patch.object(workflow, "run", side_effect=fake):
            result = workflow.merge_preflight(self.repo, 31, self.head)
        self.assertIn("--match-head-commit " + self.head, result["command"])
        self.assertFalse(self.pr_data["merged"])


class WorktreeReportTests(GitFixture):
    PULLS = "pulls?head=example:issue-12-correct-value&state=closed"

    def closed_pulls(self, pulls):
        queries = []

        def api(suffix, **kwargs):
            if not suffix.startswith("pulls?"):
                return self.api(suffix, **kwargs)
            queries.append(suffix)
            if isinstance(pulls, Exception):
                raise pulls
            return pulls

        self.repo.api = api
        return queries

    @staticmethod
    def closed_pull(number=31, repository="example/project", merged_at="2026-10-05T12:00:00Z"):
        return {
            "number": number,
            "merged_at": merged_at,
            "head": {"ref": "issue-12-correct-value", "repo": {"full_name": repository}},
        }

    def registration(self):
        return (git(self.root, "worktree", "list", "--porcelain"), git(self.root, "branch", "--list"))

    def test_merged_clean_worktree_gets_one_warning_with_the_cleanup_command(self):
        self.merged()
        queries = self.closed_pulls([self.closed_pull()])
        before = self.registration()
        report = workflow.worktree_report(self.repo)
        self.assertEqual(queries, [self.PULLS])
        self.assertEqual(
            report,
            [
                {
                    "issue": 12,
                    "branch": "issue-12-correct-value",
                    "path": str(self.task_path),
                    "exists": True,
                    "clean": True,
                    "merged_pr": 31,
                    "note": None,
                }
            ],
        )
        self.assertEqual(
            workflow.worktree_messages(report),
            [
                "warning: issue-12-correct-value was merged as PR #31; "
                "run: python3 scripts/agentic/workflow.py cleanup-task 31"
            ],
        )
        self.assertEqual(self.registration(), before)
        self.assertEqual((self.task_path / "code.py").read_text(), "value = 2\n")

    def test_dirty_merged_worktree_points_to_the_finishing_procedure(self):
        self.merged()
        self.closed_pulls([self.closed_pull()])
        (self.task_path / "valuable.cache").write_text("experiment result")
        report = workflow.worktree_report(self.repo)
        self.assertEqual((report[0]["clean"], report[0]["merged_pr"]), (False, 31))
        [warning] = workflow.worktree_messages(report)
        self.assertTrue(warning.startswith("warning: issue-12-correct-value "))
        self.assertIn("docs/agent-workflow/FINISH.md", warning)
        self.assertNotIn("cleanup-task", warning)
        self.assertTrue((self.task_path / "valuable.cache").exists())

    def test_missing_directory_gets_recovery_guidance_without_a_repository_wide_prune(self):
        self.merged()
        self.closed_pulls([self.closed_pull()])
        # An unrelated worktree on an unmounted volume looks exactly like the vanished task directory.
        unavailable = self.parent / "unavailable"
        git(self.root, "worktree", "add", "-b", "unrelated", str(unavailable), "trunk")
        shutil.rmtree(self.task_path)
        shutil.rmtree(unavailable)
        before = self.registration()
        self.assertIn(str(unavailable), before[0])
        report = workflow.worktree_report(self.repo)
        self.assertEqual(
            (report[0]["path"], report[0]["exists"], report[0]["clean"], report[0]["merged_pr"]),
            (str(self.task_path), False, None, 31),
        )
        [warning] = workflow.worktree_messages(report)
        self.assertTrue(warning.startswith("warning: issue-12-correct-value "))
        self.assertIn("docs/agent-workflow/FINISH.md", warning)
        self.assertIn("python3 scripts/agentic/workflow.py cleanup-task 31", warning)
        self.assertNotIn("prune", warning)
        self.assertNotIn("--force", warning)
        self.assertEqual(self.registration(), before)

    def test_report_does_not_refresh_a_worktree_index(self):
        self.merged()
        self.closed_pulls([self.closed_pull()])
        index = Path(git(self.task_path, "rev-parse", "--path-format=absolute", "--git-path", "index"))
        # Stale stat information with unchanged content is what makes plain `git status` rewrite the index.
        os.utime(self.task_path / "code.py", (946_684_800, 946_684_800))
        before = index.read_bytes()
        report = workflow.worktree_report(self.repo)
        self.assertTrue(report[0]["clean"])
        self.assertEqual(index.read_bytes(), before)

    def test_transport_interruptions_degrade_and_later_worktrees_still_report(self):
        self.commit_task()
        second = self.parent / "issue-13-second"
        git(self.root, "worktree", "add", "-b", "issue-13-second", str(second), "trunk")
        merged_second = self.closed_pull(number=32)
        merged_second["head"]["ref"] = "issue-13-second"

        def api(suffix, **kwargs):
            if suffix.startswith("pulls?head=example:issue-12-"):
                raise http.client.IncompleteRead(b"partial")
            if suffix.startswith("pulls?head=example:issue-13-"):
                return [merged_second]
            return self.api(suffix, **kwargs)

        self.repo.api = api
        report = {entry["branch"]: entry for entry in workflow.worktree_report(self.repo)}
        self.assertEqual(sorted(report), ["issue-12-correct-value", "issue-13-second"])
        self.assertIsNone(report["issue-12-correct-value"]["merged_pr"])
        self.assertIn("IncompleteRead", report["issue-12-correct-value"]["note"])
        self.assertEqual(report["issue-13-second"]["merged_pr"], 32)
        self.assertEqual(
            sorted(workflow.worktree_messages(list(report.values()))),
            [
                f"note: issue-12-correct-value: {report['issue-12-correct-value']['note']}",
                "warning: issue-13-second was merged as PR #32; "
                "run: python3 scripts/agentic/workflow.py cleanup-task 32",
            ],
        )

    def test_merged_pull_request_lookup_follows_pagination(self):
        self.merged()
        observed = []

        def api(suffix, **kwargs):
            if not suffix.startswith("pulls?"):
                return self.api(suffix, **kwargs)
            observed.append((suffix, kwargs.get("paginate")))
            first_page = [self.closed_pull(number=30, merged_at=None)]
            # Only a paginated request reaches the merged PR on the second page.
            return first_page + [self.closed_pull()] if kwargs.get("paginate") else first_page

        self.repo.api = api
        report = workflow.worktree_report(self.repo)
        self.assertEqual(observed, [(self.PULLS, True)])
        self.assertEqual(report[0]["merged_pr"], 31)

    def test_unmerged_fork_and_absent_pull_requests_produce_no_warning(self):
        self.commit_task()
        for pulls in (
            [],
            [self.closed_pull(merged_at=None)],
            [self.closed_pull(repository="outsider/project")],
        ):
            with self.subTest(pulls=pulls):
                self.closed_pulls(pulls)
                report = workflow.worktree_report(self.repo)
                self.assertEqual((report[0]["merged_pr"], report[0]["note"]), (None, None))
                self.assertEqual(workflow.worktree_messages(report), [])

    def test_lookup_failures_degrade_to_null_with_a_note(self):
        self.commit_task()
        self.closed_pulls(workflow.WorkflowError("GitHub API HTTP 403; response body withheld"))
        report = workflow.worktree_report(self.repo)
        self.assertEqual((report[0]["merged_pr"], report[0]["clean"]), (None, True))
        self.assertIn("HTTP 403", report[0]["note"])
        self.assertEqual(
            workflow.worktree_messages(report),
            [f"note: issue-12-correct-value: {report[0]['note']}"],
        )
        with patch.object(
            workflow.Repo, "info", new_callable=PropertyMock, side_effect=workflow.WorkflowError("gh offline")
        ):
            report = workflow.worktree_report(self.repo)
        self.assertIsNone(report[0]["merged_pr"])
        self.assertIn("gh offline", report[0]["note"])

    def test_main_checkout_and_non_task_worktrees_are_not_reported(self):
        queries = self.closed_pulls([self.closed_pull()])
        self.assertEqual(workflow.worktree_report(self.repo), [])
        git(self.root, "worktree", "add", "-b", "scratch", str(self.parent / "scratch"), "trunk")
        self.assertEqual(workflow.worktree_report(self.repo), [])
        self.assertEqual(queries, [])


class ReviewTests(GitFixture):
    def test_apps_script_commonjs_snapshot_preserves_text_and_exclusion_boundaries(self):
        contents = {
            "example.gs": "const title = 'Skills — self-report';\n",
            "test.cjs": "require('node:test');\n",
        }
        for name, text in contents.items():
            (self.root / name).write_text(text, encoding="utf-8")
        (self.root / "invalid.gs").write_bytes(b"\xff")
        (self.root / "binary.cjs").write_bytes(b"a\0b")
        (self.root / "large.gs").write_text("x" * 101)
        (self.root / "linked.cjs").symlink_to("test.cjs")
        (self.root / "memory").mkdir()
        (self.root / "memory/private.gs").write_text("private synthetic fixture")
        git(self.root, "add", "-f", ".")
        git(self.root, "commit", "-m", "script snapshot fixtures")
        head = git(self.root, "rev-parse", "HEAD")
        limits = {**workflow.configuration(self.root), "max_source_file_bytes": 100}
        output = self.parent / "snapshot"
        index = {item["path"]: item for item in review.snapshot(self.repo, head, output, limits)}
        for name, text in contents.items():
            self.assertIn("snapshot", index[name])
            target = output / Path(index[name]["snapshot"]).name
            self.assertEqual(target.read_bytes(), text.encode("utf-8"))
            self.assertEqual(target.suffix, ".txt")
        for name in ["invalid.gs", "binary.cjs"]:
            self.assertEqual(index[name]["omitted"], "not UTF-8 text")
        self.assertEqual(index["large.gs"]["omitted"], "file exceeds configured size limit")
        self.assertEqual(index["memory/private.gs"]["omitted"], "private/data path excluded")
        self.assertIn("symlink", index["linked.cjs"]["omitted"])
        with self.assertRaisesRegex(workflow.WorkflowError, "exceeds budget"):
            review.snapshot(self.repo, head, self.parent / "tiny", {**limits, "max_snapshot_bytes": 1})

    def packet(self):
        self.commit_task()
        return review.prepare(self.repo, 31, 12, 1234)

    def test_packet_records_exact_diff_plan_and_source_mapping(self):
        directory = self.packet()
        meta = review.verify_packet(directory)
        self.assertEqual(meta["head_sha"], self.head)
        self.assertEqual(meta["requested_model"], profiles.active_profile(self.repo)["reviewer"]["model"])
        self.assertIn("+value = 2", (directory / "packet/diff.txt").read_text())
        context = json.loads((directory / "packet/context.json").read_text())
        self.assertEqual(context["designated_plan_comment"]["body"], "Approved plan")
        index = json.loads((directory / "packet/source-index.json").read_text())
        source = next(i for i in index if i["path"] == "code.py")
        self.assertEqual((directory / "packet" / source["snapshot"]).read_text(), "value = 2\n")
        self.assertFalse((directory / "packet/source/AGENTS.md").exists())

    def test_changed_packet_is_rejected(self):
        directory = self.packet()
        (directory / "packet/diff.txt").write_text("tampered")
        with self.assertRaisesRegex(workflow.WorkflowError, "changed"):
            review.verify_packet(directory)

    def test_unlimited_diff_cap_accepts_a_diff_above_the_shipped_cap(self):
        self.commit_task()
        (self.task_path / "large.txt").write_text("é" * 160_000 + "\n", encoding="utf-8")
        git(self.task_path, "add", "large.txt")
        git(self.task_path, "commit", "-m", "large text fixture")
        self.pr_data["head"]["sha"] = git(self.task_path, "rev-parse", "HEAD")
        git(self.task_path, "push", "origin", "HEAD:refs/pull/31/head")
        with self.assertRaisesRegex(workflow.WorkflowError, "max_diff_bytes"):
            review.prepare(self.repo, 31, 12, 1234)
        cfg = workflow.configuration(self.root)
        cfg["max_diff_bytes"] = None
        with patch.object(review, "configuration", return_value=cfg):
            directory = review.prepare(self.repo, 31, 12, 1234)
        self.assertGreater((directory / "packet/diff.txt").stat().st_size, 300_000)
        index = json.loads((directory / "packet/source-index.json").read_text())
        self.assertIn("omitted", next(item for item in index if item["path"] == "large.txt"))

    def test_opt_in_diff_cap_is_inclusive_and_counts_utf8_bytes(self):
        self.commit_task()
        (self.task_path / "code.py").write_text('value = "é"\n', encoding="utf-8")
        git(self.task_path, "commit", "-am", "unicode diff fixture")
        self.pr_data["head"]["sha"] = git(self.task_path, "rev-parse", "HEAD")
        git(self.task_path, "push", "origin", "HEAD:refs/pull/31/head")
        packet = review.prepare(self.repo, 31, 12, 1234)
        diff = (packet / "packet/diff.txt").read_text(encoding="utf-8")
        byte_count = len(diff.encode("utf-8"))
        self.assertGreater(byte_count, len(diff))
        cfg = workflow.configuration(self.root)
        for cap, accepted in [(byte_count, True), (byte_count - 1, False)]:
            cfg["max_diff_bytes"] = cap
            with patch.object(review, "configuration", return_value=cfg):
                if accepted:
                    self.assertTrue(review.prepare(self.repo, 31, 12, 1234).is_dir())
                else:
                    with self.assertRaisesRegex(workflow.WorkflowError, "max_diff_bytes"):
                        review.prepare(self.repo, 31, 12, 1234)

    def test_unlimited_diff_still_rejects_empty_change(self):
        self.commit_task()
        self.pr_data["head"]["sha"] = self.base
        cfg = workflow.configuration(self.root)
        cfg["max_diff_bytes"] = None
        with patch.object(review, "configuration", return_value=cfg):
            with self.assertRaisesRegex(workflow.WorkflowError, "Diff is empty"):
                review.prepare(self.repo, 31, 12, 1234)

    def test_configured_private_prefixes_exclude_adopter_data_trees(self):
        self.commit_task()
        (self.task_path / "files/input").mkdir(parents=True)
        (self.task_path / "files/input/sample.txt").write_text("private dataset row")
        git(self.task_path, "add", "files/input/sample.txt")
        git(self.task_path, "commit", "-m", "data fixture")
        self.pr_data["head"]["sha"] = git(self.task_path, "rev-parse", "HEAD")
        git(self.task_path, "push", "origin", "HEAD:refs/pull/31/head")
        directory = review.prepare(self.repo, 31, 12, 1234)
        index = json.loads((directory / "packet/source-index.json").read_text())
        self.assertIn("snapshot", next(item for item in index if item["path"] == "files/input/sample.txt"))
        cfg = workflow.configuration(self.root)
        cfg["private_paths"] = ["files/input"]
        with patch.object(review, "configuration", return_value=cfg):
            with self.assertRaisesRegex(workflow.WorkflowError, "private/data"):
                review.prepare(self.repo, 31, 12, 1234)
        self.assertTrue(review.private_path("files/input/sample.txt", ["files/input"]))
        self.assertFalse(review.private_path("files/inputs/sample.txt", ["files/input"]))

    def test_prepare_records_overrides_and_refuses_uninstalled_reviewer_backends(self):
        self.commit_task()
        with self.assertRaisesRegex(workflow.WorkflowError, "implementer family"):
            review.prepare(self.repo, 31, 12, 1234, review_model="gpt-6-astra")
        with contextlib.redirect_stderr(io.StringIO()):
            directory = review.prepare(
                self.repo, 31, 12, 1234, review_model="gpt-6-astra", allow_same_family=True
            )
        meta = review.verify_packet(directory)
        self.assertEqual(meta["requested_model"], "gpt-6-astra")
        self.assertEqual(meta["reviewer"]["backend"], "copilot")
        self.assertEqual(meta["review_policy"]["model"], "gpt-6-astra")
        self.assertEqual(
            meta["review_policy"]["cli"]["version"], review_policy.PROVIDERS["copilot"]["cli"]["version"]
        )
        self.assertEqual(meta["selection_sources"]["model"], "per-call")
        self.assertEqual(meta["overrides"]["review_model"], "gpt-6-astra")
        self.assertTrue(meta["provenance"]["same_family_acknowledged"])
        self.assertEqual(meta["provenance"]["profile"], "astra-copilot")
        with patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"}):
            with self.assertRaisesRegex(workflow.WorkflowError, "claude_reviewer_adapter_not_installed"):
                review.prepare(self.repo, 31, 12, 1234)
            with self.assertRaisesRegex(workflow.WorkflowError, "requires a reviewer backend of copilot"):
                review.prepare(self.repo, 31, 12, 1234, require_backend="copilot")
            switched = review.prepare(
                self.repo, 31, 12, 1234, review_provider="copilot", require_backend="copilot"
            )
        meta = review.verify_packet(switched)
        self.assertEqual(meta["provenance"]["profile"], "astra-claude")
        self.assertEqual(meta["selection_sources"]["provider"], "per-call")
        self.assertEqual(meta["review_policy"]["budget"]["ai_credits"], 400)
        self.assertFalse(list((self.root / ".agentic-local/reviews").glob("*/review.md")))

    def test_non_default_effort_is_passed_to_copilot_and_checked_in_help(self):
        self.commit_task()
        directory = review.prepare(self.repo, 31, 12, 1234, review_effort="high")
        meta = review.verify_packet(directory)
        self.assertEqual((meta["reviewer"]["effort"], meta["review_policy"]["effort"]), ("high", "high"))
        original = review.run
        observed = []

        def fake(args, **kwargs):
            if args[0] != "copilot":
                return original(args, **kwargs)
            if args[1] == "--help":
                return subprocess.CompletedProcess(
                    args,
                    0,
                    "--available-tools --no-custom-instructions --disable-builtin-mcps --no-remote-export --no-ask-user --usage-output-file --max-ai-credits",
                    "",
                )
            if args[1] == "--version":
                return subprocess.CompletedProcess(args, 0, "Copilot test double\n", "")
            observed.append(args)
            return subprocess.CompletedProcess(
                args, 0, "No material findings supported by this review.\n", ""
            )

        with (
            patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": "fake-test-token"}),
            patch.object(review, "run", side_effect=fake),
        ):
            with self.assertRaisesRegex(workflow.WorkflowError, "--effort"):
                review.review(self.repo, directory)
        self.assertEqual(observed, [])

        def capable(args, **kwargs):
            if args[0] == "copilot" and args[1] == "--help":
                return subprocess.CompletedProcess(args, 0, fake(args, **kwargs).stdout + " --effort", "")
            return fake(args, **kwargs)

        with (
            patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": "fake-test-token"}),
            patch.object(review, "run", side_effect=capable),
        ):
            review.review(self.repo, directory)
        argv = observed[0]
        self.assertEqual(argv[argv.index("--effort") + 1], "high")
        meta = review.verify_packet(directory)
        meta["reviewer"]["effort"] = "low"
        workflow.write_json(directory / "metadata.json", meta)
        (directory / "review.md").unlink()
        with (
            patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": "fake-test-token"}),
            patch.object(review, "run", side_effect=capable),
        ):
            with self.assertRaisesRegex(workflow.WorkflowError, "immutable review policy"):
                review.review(self.repo, directory)

    def test_symlinks_are_never_dereferenced(self):
        (self.root / "secret-link.py").symlink_to("/etc/passwd")
        git(self.root, "add", "secret-link.py")
        git(self.root, "commit", "-m", "link fixture")
        index = review.snapshot(
            self.repo,
            git(self.root, "rev-parse", "HEAD"),
            self.parent / "snapshot",
            workflow.configuration(self.root),
        )
        self.assertIn("symlink", next(i for i in index if i["path"] == "secret-link.py")["omitted"])

    def test_private_path_diff_is_rejected_before_packet_creation(self):
        self.commit_task()
        (self.task_path / "memory").mkdir()
        (self.task_path / "memory/private.md").write_text("private")
        git(self.task_path, "add", "-f", "memory/private.md")
        git(self.task_path, "commit", "-m", "private fixture")
        self.pr_data["head"]["sha"] = git(self.task_path, "rev-parse", "HEAD")
        git(self.task_path, "push", "origin", "HEAD:refs/pull/31/head")
        with self.assertRaisesRegex(workflow.WorkflowError, "private/data"):
            review.prepare(self.repo, 31, 12, 1234)

    def test_review_rejects_stale_head(self):
        directory = self.packet()
        self.pr_data["head"]["sha"] = "a" * 40
        with self.assertRaisesRegex(workflow.WorkflowError, "changed"):
            review.review(self.repo, directory)

    def test_publish_is_comment_bound_to_sha_and_idempotent(self):
        directory = self.packet()
        (directory / "review.md").write_text("No material findings supported.\n")
        meta = review.verify_packet(directory)
        meta["review_sha256"] = review.digest(directory / "review.md")
        workflow.write_json(directory / "metadata.json", meta)
        review.publish(self.repo, directory)
        body = self.posts[0][1]
        self.assertEqual(body["event"], "COMMENT")
        self.assertEqual(body["commit_id"], self.head)
        self.reviews.append({"body": body["body"], "html_url": "existing"})
        self.assertEqual(review.publish(self.repo, directory), {"existing_review": "existing"})
        self.assertEqual(len(self.posts), 1)

    def test_copilot_run_has_fresh_state_and_read_only_tool_allowlist(self):
        packet_config = workflow.configuration(self.root)
        with patch.object(review, "configuration", return_value=packet_config):
            directory = self.packet()
        requested_model = profiles.active_profile(self.repo, packet_config)["reviewer"]["model"]
        original = review.run
        observed = []

        def fake(args, **kwargs):
            if args[0] != "copilot":
                return original(args, **kwargs)
            if args[1] == "--help":
                return subprocess.CompletedProcess(
                    args,
                    0,
                    "--available-tools --no-custom-instructions --disable-builtin-mcps --no-remote-export --no-ask-user --usage-output-file --max-ai-credits",
                    "",
                )
            if args[1] == "--version":
                return subprocess.CompletedProcess(args, 0, "Copilot test double\n", "")
            observed.append((args, kwargs))
            self.assertFalse(Path(kwargs["cwd"]).is_relative_to(self.root))
            self.assertNotIn("GH_TOKEN", kwargs["env"])
            self.assertNotIn("COPILOT_PROVIDER_BASE_URL", kwargs["env"])
            settings = json.loads((Path(kwargs["env"]["COPILOT_HOME"]) / "settings.json").read_text())
            self.assertTrue(settings["disableAllHooks"])
            return subprocess.CompletedProcess(
                args, 0, "No material findings supported by this review.\n", ""
            )

        with (
            patch.dict(
                os.environ,
                {
                    "COPILOT_GITHUB_TOKEN": "fake-test-token",
                    "COPILOT_PROVIDER_BASE_URL": "https://invalid.test",
                },
            ),
            patch.object(review, "run", side_effect=fake),
            patch.object(
                review,
                "configuration",
                side_effect=AssertionError("Run must use the model and budgets frozen in the review packet"),
            ),
        ):
            report = review.review(self.repo, directory)
        self.assertTrue(report.exists())
        argv = observed[0][0]
        self.assertIn("--available-tools=view,grep,glob", argv)
        self.assertNotIn("--allow-all", argv)
        self.assertNotIn("--continue", argv)
        self.assertEqual(argv[argv.index("--model") + 1], requested_model)
        self.assertEqual(
            argv[argv.index("--max-ai-credits") + 1],
            str(packet_config["review_max_ai_credits"]),
        )
        self.assertIn(f"Requested model: `{requested_model}`", report.read_text())

    def test_gpt_reviewer_packet_runs_and_auto_is_refused(self):
        with patch.dict(os.environ, {profiles.ENV_NAME: "fable-gpt"}):
            directory = self.packet()
        meta = review.verify_packet(directory)
        self.assertEqual(meta["requested_model"], "gpt-6-astra")
        self.assertEqual(meta["reviewer"]["family"], "openai")
        self.assertEqual(meta["provenance"]["profile"], "fable-gpt")
        original = review.run
        observed = []

        def fake(args, **kwargs):
            if args[0] != "copilot":
                return original(args, **kwargs)
            if args[1] == "--help":
                return subprocess.CompletedProcess(
                    args,
                    0,
                    "--available-tools --no-custom-instructions --disable-builtin-mcps --no-remote-export --no-ask-user --usage-output-file --max-ai-credits",
                    "",
                )
            if args[1] == "--version":
                return subprocess.CompletedProcess(args, 0, "Copilot test double\n", "")
            observed.append(args)
            return subprocess.CompletedProcess(
                args, 0, "No material findings supported by this review.\n", ""
            )

        with (
            patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": "fake-test-token"}),
            patch.object(review, "run", side_effect=fake),
            patch.object(review, "configuration", side_effect=AssertionError("frozen packet only")),
        ):
            report = review.review(self.repo, directory)
        argv = observed[0]
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-6-astra")
        self.assertEqual(argv[argv.index("--max-ai-credits") + 1], "400")
        text = report.read_text()
        self.assertIn("Requested model: `gpt-6-astra`", text)
        self.assertIn("Reviewer family: openai", text)
        meta["requested_model"] = "auto"
        workflow.write_json(directory / "metadata.json", meta)
        report.unlink()
        with self.assertRaisesRegex(workflow.WorkflowError, "not auto"):
            review.review(self.repo, directory)


class LaunchTests(GitFixture):
    def test_interactive_launch_follows_the_active_profile(self):
        preview = workflow.launch(self.repo, "draft", "probe")
        self.assertEqual(preview["backend"], "codex")
        self.assertIn("--sandbox read-only", preview["command"])
        self.assertIn("--model gpt-6-astra", preview["command"])
        with self.assertRaisesRegex(workflow.WorkflowError, "managed execution only"):
            workflow.launch(self.repo, "draft", "probe", containment="bypass")
        with patch.dict(os.environ, {profiles.ENV_NAME: "fable-gpt"}):
            planning = workflow.launch(self.repo, "plan", "12")
            self.assertEqual((planning["backend"], planning["profile"]), ("claude", "fable-gpt"))
            self.assertIn("--permission-mode plan", planning["command"])
            self.assertIn("--model claude-fable-5-1", planning["command"])
            with self.assertRaisesRegex(workflow.WorkflowError, "task worktree"):
                workflow.launch(self.repo, "implement", "12")
            self.task()
            implement = workflow.launch(workflow.Repo(self.task_path), "implement", "12")
        self.assertIn("--permission-mode acceptEdits", implement["command"])
        self.assertEqual(implement["cwd"], str(self.task_path))


class InstallerTests(unittest.TestCase):
    def test_parent_component_cannot_redirect_install_into_copied_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            copied_source = parent / "source"
            install.install(SOURCE, copied_source, True)
            manifest = copied_source / ".agentic/template-origin.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source": "agentic-github-template",
                        "files": {"retained-fixture-record": "unchanged"},
                    }
                )
            )
            previous = manifest.read_bytes()
            sibling = parent / "outside"
            sibling.mkdir()
            target = sibling / ".." / copied_source.name
            self.assertFalse(target.is_relative_to(copied_source))
            for apply in (False, True):
                with self.subTest(apply=apply):
                    with self.assertRaisesRegex(workflow.WorkflowError, "components"):
                        install.install(copied_source, target, apply)
                    self.assertEqual(manifest.read_bytes(), previous)
                    self.assertEqual(list(sibling.iterdir()), [])

    def test_preview_writes_nothing_and_conflicts_abort_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new"
            result = install.install(SOURCE, target)
            self.assertFalse(result["applied"])
            self.assertFalse(target.exists())
            target.mkdir()
            (target / "AGENTS.md").write_text("existing policy\n")
            with self.assertRaisesRegex(workflow.WorkflowError, "before any writes"):
                install.install(SOURCE, target, True)
            self.assertFalse((target / ".agentic").exists())
            self.assertEqual((target / "AGENTS.md").read_text(), "existing policy\n")

    def test_install_is_repeatable_and_omits_private_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new"
            install.install(SOURCE, target, True)
            result = install.install(SOURCE, target, True)
            self.assertEqual(result["new_files"], [])
            self.assertFalse((target / "memory").exists())
            self.assertFalse((target / "README.md").exists())
            self.assertFalse((target / "examples").exists())
            self.assertFalse((target / "Makefile").exists())
            self.assertFalse((target / ".github/workflows/ci.yml").exists())
            self.assertTrue((target / "scripts/agentic/workflow.py").exists())
            self.assertTrue((target / "scripts/finish-task.sh").exists())
            self.assertEqual(len(list((target / ".agents/skills").glob("*/SKILL.md"))), 8)
            mirrored = sorted((target / ".claude/skills").glob("*/SKILL.md"))
            self.assertEqual(len(mirrored), 8)
            for copy in mirrored:
                source = target / ".agents/skills" / copy.parent.name / "SKILL.md"
                self.assertEqual(copy.read_bytes(), source.read_bytes())
                self.assertFalse((copy.parent / "agents").exists())
            for forbidden in ("CLAUDE.md", ".claude/settings.json", ".mcp.json"):
                self.assertFalse((target / forbidden).exists(), forbidden)
            self.assertFalse((target / ".agentic-local").exists())

    def test_installer_rejects_non_directory_ancestor_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new"
            target.mkdir()
            (target / "scripts").write_text("existing user file")
            with self.assertRaisesRegex(workflow.WorkflowError, "before any writes"):
                install.install(SOURCE, target, True)
            self.assertFalse((target / ".agentic").exists())
            self.assertEqual((target / "scripts").read_text(), "existing user file")

    def test_installer_preserves_unrecognized_origin_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new"
            (target / ".agentic").mkdir(parents=True)
            manifest = target / ".agentic/template-origin.json"
            manifest.write_text('{"private_user_record": true}')
            with self.assertRaisesRegex(workflow.WorkflowError, "before any writes"):
                install.install(SOURCE, target, True)
            self.assertFalse((target / "scripts").exists())
            self.assertEqual(json.loads(manifest.read_text()), {"private_user_record": True})


if __name__ == "__main__":
    unittest.main()
