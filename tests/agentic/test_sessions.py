"""Managed-model doubles use local Python processes, never a model service."""

import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from test_workflow import GitFixture, git, workflow

# The shared fixture establishes the scripts import path.
# isort: split
import profiles
import sessions
import tasks

IDENTITY = "12345678-1234-4234-8234-123456789abc"
OTHER_IDENTITY = "22345678-1234-4234-8234-123456789abc"
HELP = (
    "SESSION_ID --ask-for-approval --sandbox --cd --add-dir --json "
    "--model --output-schema --output-last-message"
)
MODEL_DOUBLE = r"""
import json
import pathlib
import sys
import time

events = json.loads(sys.argv[1])
result_path = pathlib.Path(sys.argv[2])
state_path = pathlib.Path(sys.argv[3])
result = json.loads(sys.argv[4])
verify_saved = sys.argv[5] == "yes"
stderr_bytes = int(sys.argv[6])
for event in events:
    print(json.dumps(event), flush=True)
    if verify_saved and event["type"] == "thread.started":
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = json.loads(state_path.read_text())
            if state["executor"].get("uuid") == event["thread_id"]:
                break
            time.sleep(0.01)
        else:
            sys.exit(7)
        if stderr_bytes:
            sys.stderr.write("x" * stderr_bytes)
            sys.stderr.flush()
result_path.write_text(json.dumps(result))
sys.stdout.write(sys.argv[7])
sys.stdout.flush()
"""
CLAUDE_HELP = " ".join(sessions.CLAUDE_REQUIRED_FLAGS)
CLAUDE_DOUBLE = r"""
import json
import pathlib
import sys
import time

spec = json.loads(sys.argv[1])
argv = spec["claude_argv"]
if "--session-id" in argv:
    identity = argv[argv.index("--session-id") + 1]
elif "--resume" in argv:
    identity = argv[argv.index("--resume") + 1]
else:
    identity = ""
reported = spec.get("identity_override") or identity
mode = argv[argv.index("--permission-mode") + 1] if "--permission-mode" in argv else ""
for event in spec["events"]:
    print(json.dumps(event).replace("__ID__", reported).replace("__MODE__", mode), flush=True)
    if spec["verify_saved"] and event.get("type") == "system" and event.get("subtype") == "init":
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = json.loads(pathlib.Path(spec["state_path"]).read_text())
            if state["executor"].get("uuid") == reported:
                break
            time.sleep(0.01)
        else:
            sys.exit(7)
sys.stdout.flush()
sys.exit(spec.get("exit_code", 0))
"""


class SessionTests(GitFixture):
    def setUp(self):
        super().setUp()
        tasks.approve_plan(self.repo, 12, 1234, "Explicit task authorization")
        prepared = tasks.prepare(self.repo, 12, "correct-value")
        self.task_path = Path(prepared["worktree"])
        self.model_calls = []

    def invoke(
        self,
        *,
        role="implement",
        task=12,
        identity=IDENTITY,
        status="completed",
        no_thread=False,
        verify_saved=True,
        stderr_bytes=0,
        output_budget=12_000_000,
        final_event="turn.completed",
        trailing_stdout="",
    ):
        original_run = sessions.run
        original_popen = subprocess.Popen
        events = [] if no_thread else [{"type": "thread.started", "thread_id": identity}]
        events.append({"type": final_event, "usage": {}})
        config = workflow.configuration(self.root)
        config["managed_max_output_bytes"] = output_budget
        result = {
            "status": status,
            "summary": "Local model-process double",
            "checks": ["fixture validation only"],
            "blockers": ["Deliberate blocker"] if status == "blocked" else [],
        }

        def fake_run(args, **kwargs):
            if args[0] == "codex":
                return subprocess.CompletedProcess(args, 0, HELP, "")
            return original_run(args, **kwargs)

        def fake_popen(args, **kwargs):
            if args[0] != "codex":
                return original_popen(args, **kwargs)
            self.model_calls.append((args, kwargs))
            result_path = args[args.index("--output-last-message") + 1]
            state_path = self.root / ".agentic-local/tasks/issue-12.json"
            return original_popen(
                [
                    sys.executable,
                    "-c",
                    MODEL_DOUBLE,
                    json.dumps(events),
                    result_path,
                    str(state_path),
                    json.dumps(result),
                    "yes" if verify_saved else "no",
                    str(stderr_bytes),
                    trailing_stdout,
                ],
                **kwargs,
            )

        with (
            patch.object(sessions, "run", side_effect=fake_run),
            patch.object(sessions, "Popen", side_effect=fake_popen),
            patch.object(sessions, "configuration", return_value=config),
        ):
            return sessions.managed_launch(self.repo, role, task, execute=True)

    def test_thread_id_is_durable_before_next_event(self):
        result = self.invoke()
        self.assertEqual(result["executor_uuid"], IDENTITY)
        self.assertFalse(result["incomplete"])
        self.assertEqual(result["status"], "completed")
        args, options = self.model_calls[0]
        self.assertEqual(options["cwd"], self.task_path)
        self.assertEqual(options["env"]["AGENTIC_EXECUTOR_ROLE"], "implement")
        self.assertEqual(args[args.index("--model") + 1], "gpt-6-astra")
        self.assertEqual(args[args.index("--sandbox") + 1], "workspace-write")
        self.assertEqual(args[args.index("--add-dir") + 1], str(self.repo.common))
        self.assertEqual(args[args.index("--ask-for-approval") + 1], "never")

    def test_resume_uses_original_uuid_even_with_a_newer_record(self):
        self.invoke()
        (self.root / ".agentic-local/newer-session.json").write_text(json.dumps({"uuid": OTHER_IDENTITY}))
        self.invoke()
        args = self.model_calls[-1][0]
        self.assertEqual(args[args.index("resume") + 1], IDENTITY)
        self.assertNotIn("--last", args)
        self.assertNotIn("--ephemeral", args)

    def test_managed_repair_resumes_original_executor_with_all_feedback_surfaces(self):
        self.invoke()
        self.pr_data = {
            "state": "open",
            "merged": False,
            "head": {
                "sha": self.base,
                "ref": "issue-12-correct-value",
                "repo": {"full_name": self.repo.name},
            },
            "base": {"sha": self.base, "ref": "trunk"},
        }
        feedback = {
            "reviews": [{"id": 101, "body": "Review finding", "commit_id": self.base}],
            "inline": [{"id": 102, "body": "Inline finding", "path": "code.py", "line": 1}],
            "conversation": [{"id": 103, "body": "Public disposition context"}],
        }
        routes = {
            "pulls/31/reviews?per_page=100": feedback["reviews"],
            "pulls/31/comments?per_page=100": feedback["inline"],
            "issues/31/comments?per_page=100": feedback["conversation"],
        }
        original_api = self.repo.api

        def api(suffix, **kwargs):
            if suffix in routes:
                self.assertTrue(kwargs.get("paginate"))
                return routes[suffix]
            return original_api(suffix, **kwargs)

        with patch.object(self.repo, "api", side_effect=api):
            result = self.invoke(role="repair", task=31)
        self.assertFalse(result["incomplete"])
        self.assertEqual(result["executor_uuid"], IDENTITY)
        args, options = self.model_calls[-1]
        self.assertEqual(args[args.index("resume") + 1], IDENTITY)
        self.assertNotIn("--last", args)
        self.assertEqual(options["cwd"], self.task_path)
        self.assertEqual(options["env"]["AGENTIC_EXECUTOR_ROLE"], "repair")
        prompt = (Path(result["directory"]) / "prompt.txt").read_text()
        public = json.loads(prompt.split("Public task data:\n", 1)[1])
        self.assertEqual(public["feedback"], feedback)
        state = tasks.TaskStore(self.repo).read("issue-12")
        self.assertEqual(state["pr"], 31)
        self.assertEqual(state["executor"]["uuid"], IDENTITY)
        self.assertEqual(
            [record["role"] for record in state["executor"]["runs"]],
            ["implement", "repair"],
        )

    def test_returned_replacement_uuid_is_refused(self):
        self.invoke()
        result = self.invoke(identity=OTHER_IDENTITY, verify_saved=False)
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(tasks.TaskStore(self.repo).read("issue-12")["executor"]["uuid"], IDENTITY)

    def test_blocked_turn_remains_incomplete(self):
        result = self.invoke(status="blocked")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["executor_uuid"], IDENTITY)

    def test_missing_thread_id_cannot_trigger_a_replacement_start(self):
        first = self.invoke(no_thread=True)
        self.assertTrue(first["incomplete"])
        with self.assertRaisesRegex(workflow.WorkflowError, "UUID is missing"):
            self.invoke()
        self.assertEqual(len(self.model_calls), 1)

    def test_recovery_requires_matching_saved_uuid_and_worktree(self):
        record = self.parent / "saved-session.jsonl"
        record.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"id": IDENTITY, "cwd": str(self.task_path)},
                }
            )
            + "\n"
        )
        with self.assertRaises(workflow.WorkflowError):
            sessions.recover_executor(self.repo, 12, OTHER_IDENTITY, record, "Saved executor record", True)
        recovered = sessions.recover_executor(
            self.repo, 12, IDENTITY, record, "Original executor verified by coordinator", True
        )
        self.assertEqual(recovered["executor_uuid"], IDENTITY)
        self.invoke()
        self.assertIn("resume", self.model_calls[-1][0])

    def test_recovery_does_not_replace_an_existing_identity(self):
        self.invoke()
        record = self.parent / "different-session.jsonl"
        record.write_text(json.dumps({"type": "thread.started", "thread_id": OTHER_IDENTITY}) + "\n")
        with self.assertRaisesRegex(workflow.WorkflowError, "cannot replace"):
            sessions.recover_executor(self.repo, 12, OTHER_IDENTITY, record, "Different record", True)

    def test_preview_does_not_start_or_register_a_session(self):
        with patch.object(sessions, "check_cli"), patch.object(sessions, "Popen") as process:
            result = sessions.managed_launch(self.repo, "implement", 12)
        process.assert_not_called()
        self.assertIsNone(result["executor_uuid"])
        self.assertNotIn("executor", tasks.TaskStore(self.repo).read("issue-12"))

    def test_stderr_is_bounded_and_preserves_the_saved_uuid(self):
        result = self.invoke(stderr_bytes=100_000, output_budget=4096)
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["executor_uuid"], IDENTITY)
        directory = Path(result["directory"])
        self.assertLessEqual(
            (directory / "stdout.jsonl").stat().st_size + (directory / "stderr.log").stat().st_size,
            4096,
        )

    def test_zero_exit_interrupted_event_remains_incomplete(self):
        result = self.invoke(final_event="turn.interrupted")
        self.assertEqual(result["status"], "interrupted")
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["executor_uuid"], IDENTITY)

    def test_truncated_final_json_records_failure_and_preserves_resume_identity(self):
        truncated = '{"type":'
        with self.assertRaises(json.JSONDecodeError) as decoding:
            json.loads(truncated)
        result = self.invoke(trailing_stdout=truncated)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["executor_uuid"], IDENTITY)
        self.assertEqual(result["error"], str(decoding.exception))
        self.assertIsNone(result["result"])
        output = (Path(result["directory"]) / "stdout.jsonl").read_bytes()
        self.assertTrue(output.endswith(truncated.encode()))
        state = tasks.TaskStore(self.repo).read("issue-12")
        record = state["executor"]["runs"][-1]
        self.assertEqual(state["executor"]["uuid"], IDENTITY)
        self.assertEqual(record["status"], "failed")
        self.assertTrue(record["incomplete"])
        self.assertEqual(record["error"], result["error"])
        self.assertEqual(record["head_after"], self.base)

        resumed = self.invoke()
        self.assertFalse(resumed["incomplete"])
        args = self.model_calls[-1][0]
        self.assertEqual(args[args.index("resume") + 1], IDENTITY)
        self.assertNotIn("--last", args)

    def test_early_checkpoint_is_not_implementation_completion(self):
        result = self.invoke(status="checkpoint")
        self.assertEqual(result["status"], "checkpoint")
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["executor_uuid"], IDENTITY)


class LegacyPinTests(SessionTests):
    def test_record_without_a_pin_is_treated_as_codex_and_refused_under_claude(self):
        store = tasks.TaskStore(self.repo)
        with store.locked("issue-12") as state:
            state["executor"] = {
                "uuid": IDENTITY,
                "runs": [
                    {
                        "role": "implement",
                        "status": "completed",
                        "incomplete": False,
                        "contract_digest": tasks.digest(state["approval"]["contract"]),
                        "head_before": self.base,
                        "head_after": self.base,
                    }
                ],
            }
            store.save(state)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = self.invoke()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.model_calls[-1][0][self.model_calls[-1][0].index("resume") + 1], IDENTITY)
        executor = store.read("issue-12")["executor"]
        self.assertEqual((executor["backend"], executor["model"]), ("codex", "gpt-6-astra"))
        self.assertIn("pin_note", executor)
        self.assertIn("note: executor record", stderr.getvalue())
        with patch.dict(os.environ, {profiles.ENV_NAME: "fable-gpt"}):
            with self.assertRaisesRegex(workflow.WorkflowError, "bound to backend codex"):
                self.invoke()
        self.assertEqual(len(self.model_calls), 1)

    def test_legacy_pin_adopts_the_configured_codex_model_not_a_template_constant(self):
        cfg = workflow.configuration(self.root)
        cfg["profiles"]["astra-claude"]["implementer"]["model"] = "gpt-6-sol"
        workflow.write_json(self.root / ".agentic/config.json", cfg)
        git(self.root, "add", ".agentic/config.json")
        git(self.root, "commit", "-m", "adopter model")
        store = tasks.TaskStore(self.repo)
        with store.locked("issue-12") as state:
            state["executor"] = {"uuid": IDENTITY, "runs": []}
            store.save(state)
        with contextlib.redirect_stderr(io.StringIO()):
            result = self.invoke()
        self.assertEqual(result["status"], "completed")
        args = self.model_calls[-1][0]
        self.assertEqual(args[args.index("--model") + 1], "gpt-6-sol")
        executor = store.read("issue-12")["executor"]
        self.assertEqual((executor["backend"], executor["model"]), ("codex", "gpt-6-sol"))


class ClaudeSessionTests(GitFixture):
    def setUp(self):
        super().setUp()
        tasks.approve_plan(self.repo, 12, 1234, "Explicit task authorization")
        prepared = tasks.prepare(self.repo, 12, "correct-value")
        self.task_path = Path(prepared["worktree"])
        self.model_calls = []
        os.environ[profiles.ENV_NAME] = "fable-gpt"

    def invoke(
        self,
        *,
        role="implement",
        task=12,
        status="completed",
        identity_override=None,
        permission_mode=None,
        model_override=None,
        tools=None,
        result_subtype="success",
        is_error=False,
        include_result=True,
        containment="restricted",
        containment_reason=None,
        verify_saved=True,
    ):
        original_run = sessions.run
        original_popen = subprocess.Popen
        structured = {
            "status": status,
            "summary": "Claude process double",
            "checks": ["fixture validation only"],
            "blockers": ["Deliberate blocker"] if status == "blocked" else [],
        }
        events = [
            {
                "type": "system",
                "subtype": "init",
                "session_id": "__ID__",
                "model": model_override or "claude-fable-5-1",
                "permissionMode": permission_mode or "__MODE__",
                "tools": tools or ["Read", "Edit", "Bash"],
                "cwd": str(self.task_path),
            }
        ]
        if include_result:
            events.append(
                {
                    "type": "result",
                    "subtype": result_subtype,
                    "is_error": is_error,
                    "session_id": "__ID__",
                    "result": "executor turn",
                    "structured_output": structured,
                    "num_turns": 2,
                    "total_cost_usd": 0.25,
                    "duration_ms": 5,
                    "permission_denials": [],
                }
            )
        config = workflow.configuration(self.root)

        def fake_run(args, **kwargs):
            if args[0] == "claude":
                return subprocess.CompletedProcess(args, 0, CLAUDE_HELP, "")
            if args[0] == "codex":
                return subprocess.CompletedProcess(args, 0, HELP, "")
            return original_run(args, **kwargs)

        def fake_popen(args, **kwargs):
            if args[0] != "claude":
                return original_popen(args, **kwargs)
            self.model_calls.append((args, kwargs))
            spec = {
                "claude_argv": list(args),
                "events": events,
                "state_path": str(self.root / ".agentic-local/tasks/issue-12.json"),
                "verify_saved": verify_saved,
                "identity_override": identity_override,
            }
            return original_popen([sys.executable, "-c", CLAUDE_DOUBLE, json.dumps(spec)], **kwargs)

        with (
            patch.object(sessions, "run", side_effect=fake_run),
            patch.object(sessions, "Popen", side_effect=fake_popen),
            patch.object(sessions, "configuration", return_value=config),
        ):
            return sessions.managed_launch(
                self.repo,
                role,
                task,
                execute=True,
                containment=containment,
                containment_reason=containment_reason,
            )

    def test_claude_command_and_successful_turn_pin_the_task(self):
        result = self.invoke()
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["incomplete"])
        self.assertEqual(result["backend"], "claude")
        args, options = self.model_calls[0]
        session_id = args[args.index("--session-id") + 1]
        self.assertEqual(result["executor_uuid"], session_id)
        self.assertEqual(options["cwd"], self.task_path)
        self.assertEqual(options["env"]["AGENTIC_EXECUTOR_ROLE"], "implement")
        for flag in ("-p", "--verbose", "--strict-mcp-config"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
        self.assertEqual(args[args.index("--model") + 1], "claude-fable-5-1")
        self.assertEqual(args[args.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(args[args.index("--permission-prompts") + 1], "none")
        self.assertEqual(args[args.index("--setting-sources") + 1], "user,project")
        self.assertEqual(args[args.index("--tools") + 1], ",".join(sessions.CLAUDE_TOOLSET))
        schema = json.loads((self.root / ".agentic/schemas/executor-result.json").read_text())
        self.assertEqual(json.loads(args[args.index("--json-schema") + 1]), schema)
        denied = args[args.index("--disallowedTools") + 1].split(",")
        for rule in sessions.CLAUDE_DENIED_TOOLS:
            self.assertIn(rule, denied)
        self.assertIn("Bash(git commit *)", args[args.index("--allowedTools") + 1].split(","))
        for flag in (
            "--bare",
            "--fallback-model",
            "--dangerously-skip-permissions",
            "--no-session-persistence",
            "--continue",
            "--add-dir",
            "--resume",
            "--last",
        ):
            self.assertNotIn(flag, args)
        state = tasks.TaskStore(self.repo).read("issue-12")
        executor = state["executor"]
        self.assertEqual(
            (executor["backend"], executor["model"], executor["profile"]),
            ("claude", "claude-fable-5-1", "fable-gpt"),
        )
        run = executor["runs"][-1]
        self.assertTrue(run["session_confirmed"])
        self.assertEqual(run["containment"], "restricted")
        self.assertEqual(run["metrics"]["subtype"], "success")
        self.assertEqual(run["command"].split()[0], "claude")
        written = json.loads((Path(result["directory"]) / "result.json").read_text())
        self.assertEqual(written["status"], "completed")
        prompt = (Path(result["directory"]) / "prompt.txt").read_text()
        self.assertIn("Backend: claude\nModel: claude-fable-5-1", prompt)

    def test_later_runs_and_repair_resume_the_confirmed_session(self):
        first = self.invoke()
        second = self.invoke()
        self.assertEqual(second["executor_uuid"], first["executor_uuid"])
        args = self.model_calls[-1][0]
        self.assertEqual(args[args.index("--resume") + 1], first["executor_uuid"])
        self.assertNotIn("--session-id", args)
        self.pr_data = {
            "state": "open",
            "merged": False,
            "head": {
                "sha": self.base,
                "ref": "issue-12-correct-value",
                "repo": {"full_name": self.repo.name},
            },
            "base": {"sha": self.base, "ref": "trunk"},
        }
        routes = {
            "pulls/31/reviews?per_page=100": [{"id": 101, "body": "Review finding", "commit_id": self.base}],
            "pulls/31/comments?per_page=100": [],
            "issues/31/comments?per_page=100": [],
        }
        original_api = self.repo.api

        def api(suffix, **kwargs):
            if suffix in routes:
                return routes[suffix]
            return original_api(suffix, **kwargs)

        with patch.object(self.repo, "api", side_effect=api):
            repair = self.invoke(role="repair", task=31)
        self.assertFalse(repair["incomplete"])
        args, options = self.model_calls[-1]
        self.assertEqual(args[args.index("--resume") + 1], first["executor_uuid"])
        self.assertEqual(options["env"]["AGENTIC_EXECUTOR_ROLE"], "repair")
        runs = tasks.TaskStore(self.repo).read("issue-12")["executor"]["runs"]
        self.assertEqual([run["role"] for run in runs], ["implement", "implement", "repair"])

    def test_foreign_session_id_on_first_run_leaves_identity_unconfirmed(self):
        result = self.invoke(identity_override=OTHER_IDENTITY, verify_saved=False)
        self.assertEqual(result["status"], "failed")
        self.assertIn("different session identity", result["error"])
        self.assertIsNone(result["executor_uuid"])
        with self.assertRaisesRegex(workflow.WorkflowError, "UUID is missing.*pre-assigned session"):
            self.invoke()
        self.assertEqual(len(self.model_calls), 1)

    def test_confirmed_identity_survives_a_foreign_resume(self):
        first = self.invoke()
        result = self.invoke(identity_override=OTHER_IDENTITY, verify_saved=False)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            tasks.TaskStore(self.repo).read("issue-12")["executor"]["uuid"], first["executor_uuid"]
        )

    def test_error_results_and_missing_results_remain_incomplete(self):
        failed = self.invoke(result_subtype="error_max_turns", is_error=True)
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["incomplete"])
        self.assertIn("error_max_turns", failed["error"])
        run = tasks.TaskStore(self.repo).read("issue-12")["executor"]["runs"][-1]
        self.assertEqual(run["metrics"]["subtype"], "error_max_turns")
        missing = self.invoke(include_result=False)
        self.assertEqual(missing["status"], "failed")
        self.assertIn("did not complete", missing["error"])
        self.assertEqual(missing["executor_uuid"], failed["executor_uuid"])

    def test_blocked_result_remains_incomplete(self):
        result = self.invoke(status="blocked")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["incomplete"])

    def test_permission_mode_mismatch_fails_the_run(self):
        result = self.invoke(permission_mode="acceptEdits", verify_saved=False)
        self.assertEqual(result["status"], "failed")
        self.assertIn("permission mode", result["error"])
        self.assertIsNone(result["executor_uuid"])

    def test_reported_model_mismatch_fails_the_run(self):
        result = self.invoke(model_override="claude-sonnet-5", verify_saved=False)
        self.assertEqual(result["status"], "failed")
        self.assertIn("started model 'claude-sonnet-5'", result["error"])
        self.assertIsNone(result["executor_uuid"])

    def test_missing_recovery_record_is_a_workflow_error(self):
        with self.assertRaisesRegex(workflow.WorkflowError, "does not exist"):
            sessions.recover_executor(
                self.repo, 12, IDENTITY, self.parent / "absent.jsonl", "Saved record", True
            )

    def test_exposed_tool_outside_the_toolset_fails_the_run(self):
        result = self.invoke(tools=["Read", "StructuredOutput", "Workflow"], verify_saved=False)
        self.assertEqual(result["status"], "failed")
        self.assertIn("outside the executor toolset", result["error"])
        self.assertIn("Workflow", result["error"])

    def test_bypass_is_explicit_recorded_and_not_the_default(self):
        with self.assertRaisesRegex(workflow.WorkflowError, "containment-reason"):
            self.invoke(containment="bypass")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = self.invoke(containment="bypass", containment_reason="one-off setup verification")
        self.assertEqual(result["status"], "completed")
        args = self.model_calls[-1][0]
        self.assertEqual(args[args.index("--permission-mode") + 1], "bypassPermissions")
        self.assertNotIn("--allowedTools", args)
        self.assertIn("--disallowedTools", args)
        self.assertNotIn("--dangerously-skip-permissions", args)
        run = tasks.TaskStore(self.repo).read("issue-12")["executor"]["runs"][-1]
        self.assertEqual(run["containment"], "bypass")
        self.assertEqual(run["containment_reason"], "one-off setup verification")
        self.assertIn("BYPASS", stderr.getvalue())
        with patch.object(sessions, "check_cli"), patch.object(sessions, "Popen") as process:
            preview = sessions.managed_launch(self.repo, "implement", 12)
        process.assert_not_called()
        self.assertEqual(preview["containment"], "restricted")
        self.assertIn("--permission-mode dontAsk", preview["command"])

    def test_bypass_is_refused_for_the_codex_backend(self):
        os.environ[profiles.ENV_NAME] = "astra-claude"
        with self.assertRaisesRegex(workflow.WorkflowError, "Claude backend only"):
            self.invoke(containment="bypass", containment_reason="misapplied")
        self.assertEqual(self.model_calls, [])

    def test_pinned_backend_mismatch_is_refused_before_any_process(self):
        self.invoke()
        os.environ[profiles.ENV_NAME] = "astra-claude"
        with self.assertRaisesRegex(workflow.WorkflowError, "bound to backend claude"):
            self.invoke()
        with patch.object(sessions, "check_cli"), patch.object(sessions, "Popen") as process:
            with self.assertRaisesRegex(workflow.WorkflowError, "bound to backend claude"):
                sessions.managed_launch(self.repo, "implement", 12)
        process.assert_not_called()
        self.assertEqual(len(self.model_calls), 1)

    def test_preview_shows_the_claude_command_without_writing(self):
        with patch.object(sessions, "check_cli"), patch.object(sessions, "Popen") as process:
            preview = sessions.managed_launch(self.repo, "implement", 12)
        process.assert_not_called()
        self.assertEqual(
            (preview["backend"], preview["model"], preview["profile"]),
            ("claude", "claude-fable-5-1", "fable-gpt"),
        )
        self.assertIn("--session-id", preview["command"])
        self.assertNotIn("executor", tasks.TaskStore(self.repo).read("issue-12"))

    def test_recovery_from_a_claude_transcript(self):
        record = self.parent / "claude-session.jsonl"
        lines = [
            {"type": "mode", "sessionId": IDENTITY},
            {"type": "user", "sessionId": IDENTITY, "cwd": str(self.task_path), "uuid": "entry"},
            {"type": "file-history-snapshot", "snapshot": {}},
        ]
        record.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
        with self.assertRaisesRegex(workflow.WorkflowError, "uniquely identify"):
            sessions.recover_executor(self.repo, 12, OTHER_IDENTITY, record, "Saved record", True)
        with self.assertRaisesRegex(workflow.WorkflowError, "Activate the profile"):
            sessions.recover_executor(self.repo, 12, IDENTITY, record, "Saved record", True, backend="codex")
        codex_record = self.parent / "codex-session.jsonl"
        codex_record.write_text(json.dumps({"type": "thread.started", "thread_id": IDENTITY}) + "\n")
        with self.assertRaisesRegex(workflow.WorkflowError, "does not look like a claude"):
            sessions.recover_executor(self.repo, 12, IDENTITY, codex_record, "Saved record", True)
        recovered = sessions.recover_executor(
            self.repo, 12, IDENTITY, record, "Original Claude session verified by coordinator", True
        )
        self.assertEqual((recovered["backend"], recovered["executor_uuid"]), ("claude", IDENTITY))
        executor = tasks.TaskStore(self.repo).read("issue-12")["executor"]
        self.assertEqual((executor["backend"], executor["uuid"]), ("claude", IDENTITY))
        result = self.invoke()
        self.assertEqual(result["executor_uuid"], IDENTITY)
        args = self.model_calls[-1][0]
        self.assertEqual(args[args.index("--resume") + 1], IDENTITY)
        os.environ[profiles.ENV_NAME] = "astra-claude"
        with self.assertRaisesRegex(workflow.WorkflowError, "pinned backend"):
            sessions.recover_executor(self.repo, 12, IDENTITY, record, "Saved record", True, backend="codex")
