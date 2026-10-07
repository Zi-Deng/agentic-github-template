"""The activation ledger: real packets, synthetic native streams, never live evidence."""

import contextlib
import io
import json
import os
import re
from unittest.mock import patch

from test_workflow import GitFixture, workflow

# isort: split
import claude_activation as activation
import claude_native_auth
import claude_telemetry
import profiles
import review_claude
import review_policy
from claude_fixtures import AUTHENTICATION, native_stream


class ActivationTests(GitFixture):
    def setUp(self):
        super().setUp()
        self.env = patch.dict(os.environ, {profiles.ENV_NAME: "astra-claude"})
        self.env.start()
        self.addCleanup(self.env.stop)
        binding = patch.object(claude_native_auth, "current_binding", return_value=AUTHENTICATION)
        binding.start()
        self.addCleanup(binding.stop)
        self.cfg = workflow.configuration(self.root)

    def selection(self, **overrides):
        return profiles.review_selection(self.repo, self.cfg, warn_same_family=False, **overrides)

    def policy(self, **changes):
        policy = {**self.selection()["policy"], "authentication": AUTHENTICATION}
        policy.update(changes)
        return policy

    def execute(self, repo, directory, meta, *, diagnostic, login_root=None):
        self.assertTrue(diagnostic)
        packet = directory / "packet"
        self.assertFalse((packet / "source").exists())
        self.assertEqual(meta["review_policy"]["budget"]["timeout_seconds"], 300)
        self.assertEqual(meta["review_policy"]["budget"]["estimated_usd"], 2)
        self.assertEqual(meta["review_policy"]["authentication"], AUTHENTICATION)
        body, diag = claude_telemetry.capture(
            native_stream(packet, packet, "fixture"), packet, packet, meta["review_policy"], "fixture"
        )
        if meta["diagnostic_purpose"] == "isolation-refusal":
            # This double tests ledger policy only. The v6 suite separately exercises
            # correlated real-shaped refusals; neither is live evidence.
            diag["telemetry"]["controlled_refusals"] = 1
            diag["reasons"].append("controlled_refusal_diagnostic_only")
        return body, diag, "2.1.282"

    def run_diagnostic(self, **kwargs):
        return activation.run(self.repo, self.cfg, self.selection(), **kwargs)

    def qualify_both(self):
        with (
            patch.object(review_claude, "preflight"),
            patch.object(review_claude, "execute", side_effect=self.execute) as execute,
        ):
            first = self.run_diagnostic()
            second = self.run_diagnostic()
        self.assertEqual(execute.call_count, 2)
        return first, second

    def test_purposes_run_in_order_and_activation_needs_both_under_one_binding(self):
        with (
            patch.object(review_claude, "preflight"),
            patch.object(review_claude, "execute", side_effect=self.execute),
        ):
            first = self.run_diagnostic()
            self.assertEqual(
                (first["purpose"], first["status"], first["activation_complete"]),
                (
                    "native-tools-and-source",
                    "qualified",
                    False,
                ),
            )
            with self.assertRaisesRegex(workflow.WorkflowError, "isolation-refusal"):
                activation.require(self.repo, self.policy())
            second = self.run_diagnostic()
            self.assertEqual((second["purpose"], second["activation_complete"]), ("isolation-refusal", True))
            with self.assertRaisesRegex(workflow.WorkflowError, "--purpose and --reason"):
                self.run_diagnostic()
            with self.assertRaisesRegex(workflow.WorkflowError, "recorded reason"):
                self.run_diagnostic(purpose="isolation-refusal")
            third = self.run_diagnostic(purpose="isolation-refusal", reason="re-check after a kernel update")
        self.assertEqual((third["attempt"], third["status"]), (3, "qualified"))
        result = activation.require(self.repo, self.policy())
        self.assertEqual(result["qualified_attempts"], {"native-tools-and-source": 1, "isolation-refusal": 3})
        self.assertTrue(result["current_generation_live_tested"])
        self.assertEqual(result["binding_digest"], first["binding_digest"])
        self.assertEqual(
            activation.ledger(self.repo)["attempts"][2]["reason"], "re-check after a kernel update"
        )
        status = review_policy.status(self.repo, self.cfg, self.selection())
        self.assertNotIn("matching_native_capability_diagnostic_unavailable", status["activation_blockers"])
        self.assertEqual(status["native_capability"]["binding_digest"], first["binding_digest"])

    def test_binding_digest_tracks_what_the_diagnostics_proved(self):
        self.qualify_both()
        base = self.policy()
        reference = activation.binding_digest(self.repo, base)
        unchanged = (
            {"budget": {**base["budget"], "timeout_seconds": 7200, "estimated_usd": 60}},
            {"authentication": {**AUTHENTICATION, "generation_id": "33333333-3333-4333-8333-333333333333"}},
        )
        for change in unchanged:
            with self.subTest(change=change):
                self.assertEqual(activation.binding_digest(self.repo, {**base, **change}), reference)
        changed = (
            {"model": "claude-sonnet-5"},
            {"effort": "high"},
            {"adapter": "claude-stream-json-2.1.282-v7"},
            {"cli": {**base["cli"], "version": "2.1.283"}},
            {"billing_mode": "api-key"},
            {"authentication": {**AUTHENTICATION, "registration_id": "44444444-4444-4444-8444-444444444444"}},
        )
        for change in changed:
            with self.subTest(change=change):
                self.assertNotEqual(activation.binding_digest(self.repo, {**base, **change}), reference)
        with self.assertRaises(workflow.WorkflowError):
            activation.require(self.repo, self.policy(effort="high"))
        with patch.dict(tool_contract_grep(), head_limit=5):
            self.assertNotEqual(activation.binding_digest(self.repo, base), reference)
            with self.assertRaises(workflow.WorkflowError):
                activation.require(self.repo, base)

    def test_renewed_generation_is_accepted_only_through_lineage(self):
        self.qualify_both()
        root = activation.state_directory(self.repo)
        original = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        current = {**AUTHENTICATION, "generation_id": "33333333-3333-4333-8333-333333333333"}
        policy = self.policy(authentication=current)
        with patch.object(claude_native_auth, "capability_lineage", return_value=False):
            with self.assertRaisesRegex(workflow.WorkflowError, "lineage|diagnostics"):
                activation.require(self.repo, policy)
        with patch.object(claude_native_auth, "capability_lineage", return_value=True) as lineage:
            result = activation.require(self.repo, policy, login_root="/srv/login")
        lineage.assert_called_with(AUTHENTICATION, current, 900, root="/srv/login")
        self.assertFalse(result["current_generation_live_tested"])
        self.assertEqual(result["observed_authentication"], [AUTHENTICATION, AUTHENTICATION])
        self.assertEqual(result["current_authentication"], current)
        self.assertEqual(original, {p: p.read_bytes() for p in original})
        self.assertEqual(len(activation.ledger(self.repo)["attempts"]), 2)

    def test_failed_preflight_spends_nothing_and_failed_calls_stay_accounted(self):
        with (
            patch.object(review_claude, "preflight", side_effect=workflow.WorkflowError("missing receipt")),
            patch.object(review_claude, "execute") as execute,
        ):
            with self.assertRaisesRegex(workflow.WorkflowError, "missing receipt"):
                self.run_diagnostic()
        execute.assert_not_called()
        self.assertEqual(activation.ledger(self.repo)["attempts"], [])
        with (
            patch.object(review_claude, "preflight"),
            patch.object(review_claude, "execute", side_effect=OSError("synthetic interruption")),
        ):
            with self.assertRaises(OSError):
                self.run_diagnostic()
        entry = activation.ledger(self.repo)["attempts"][0]
        self.assertEqual(
            (entry["number"], entry["purpose"], entry["status"]), (1, "native-tools-and-source", "incomplete")
        )
        with self.assertRaises(workflow.WorkflowError):
            activation.require(self.repo, self.policy())
        # The second purpose still runs automatically; repeating the failed one needs a reason.
        with (
            patch.object(review_claude, "preflight"),
            patch.object(review_claude, "execute", side_effect=self.execute),
        ):
            self.assertEqual(self.run_diagnostic()["purpose"], "isolation-refusal")
            with self.assertRaisesRegex(workflow.WorkflowError, "--purpose and --reason"):
                self.run_diagnostic()
            retry = self.run_diagnostic(purpose="native-tools-and-source", reason="network restored")
        self.assertEqual((retry["attempt"], retry["activation_complete"]), (3, True))
        activation.require(self.repo, self.policy())

    def test_modified_evidence_or_ledger_invalidates_activation(self):
        self.qualify_both()
        directory = activation.state_directory(self.repo) / "attempt-1"
        report = directory / "report.txt"
        saved = report.read_bytes()
        report.write_bytes(saved + b"\n")
        with self.assertRaises(workflow.WorkflowError):
            activation.require(self.repo, self.policy())
        report.write_bytes(saved)
        activation.require(self.repo, self.policy())
        fixture = directory / "packet/capability/fixture.txt"
        fixture.write_text(fixture.read_text() + "tampered\n")
        with self.assertRaises(workflow.WorkflowError):
            activation.require(self.repo, self.policy())
        ledger = activation.ledger_path(self.repo)
        state = json.loads(ledger.read_text())
        state["attempts"][0]["status"] = "qualified"
        state["attempts"].append({**state["attempts"][1], "number": 4})
        ledger.write_text(json.dumps(state))
        with self.assertRaisesRegex(workflow.WorkflowError, "Invalid Claude activation"):
            activation.ledger(self.repo)

    def test_packet_inventory_ids_obey_the_supplied_report_schema_and_never_touch_sources(self):
        def inspect(repo, directory, meta, *, diagnostic, login_root=None):
            packet = directory / "packet"
            schema = json.loads((packet / "report-schema.json").read_text())
            pattern = schema["properties"]["reviewed"]["items"]["pattern"]
            required = json.loads((packet / "required-material.json").read_text())["required"]
            self.assertEqual(len({item["id"] for item in required}), len(required))
            for item in required:
                self.assertIsNotNone(re.fullmatch(pattern, item["id"]))
            self.assertEqual(
                sorted(meta["files"]),
                sorted(p.relative_to(packet).as_posix() for p in packet.rglob("*") if p.is_file()),
            )
            self.assertIn(
                json.dumps(meta["diagnostic_tool_contract"]["grep_canary"], separators=(",", ":")),
                (packet / "START.txt").read_text(),
            )
            return self.execute(repo, directory, meta, diagnostic=diagnostic)

        with (
            patch.object(review_claude, "preflight"),
            patch.object(review_claude, "execute", side_effect=inspect),
        ):
            self.run_diagnostic()
        with patch.dict(os.environ, {profiles.ENV_NAME: "astra-copilot"}):
            with self.assertRaisesRegex(workflow.WorkflowError, "Claude Code reviewer selection"):
                self.run_diagnostic()
        exception = review_policy.exception_record(7200, 60, "never for diagnostics")
        with self.assertRaisesRegex(workflow.WorkflowError, "never carry"):
            activation.run(self.repo, self.cfg, self.selection(review_exception=exception))

    def test_cli_exit_codes_and_flags(self):
        def cli(*argv):
            out, err = io.StringIO(), io.StringIO()
            previous = os.getcwd()
            os.chdir(self.root)
            try:
                with (
                    patch("sys.argv", ["workflow.py", "diagnose-claude", *argv]),
                    patch.object(workflow, "Repo", return_value=self.repo),
                    contextlib.redirect_stdout(out),
                    contextlib.redirect_stderr(err),
                ):
                    code = workflow.main()
            finally:
                os.chdir(previous)
            return code, out.getvalue(), err.getvalue()

        with (
            patch.object(review_claude, "preflight"),
            patch.object(review_claude, "execute", side_effect=self.execute),
        ):
            code, out, err = cli()
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["purpose"], "native-tools-and-source")
            code, _, err = cli(
                "--review-timeout-seconds",
                "7200",
                "--review-max-estimated-usd",
                "60",
                "--review-exception-reason",
                "x",
            )
            self.assertEqual(code, 1)
            self.assertIn("never carry", err)
        with (
            patch.object(review_claude, "preflight"),
            patch.object(review_claude, "execute", side_effect=OSError("synthetic")),
        ):
            code, _, err = cli("--purpose", "isolation-refusal", "--reason", "explicit")
            self.assertEqual(code, 1)
            self.assertIn("synthetic", err)
        incomplete = lambda repo, directory, meta, *, diagnostic, login_root=None: (  # noqa: E731
            "not json",
            self.execute(repo, directory, meta, diagnostic=diagnostic)[1],
            "2.1.282",
        )
        with (
            patch.object(review_claude, "preflight"),
            patch.object(review_claude, "execute", side_effect=incomplete),
        ):
            code, out, _ = cli("--purpose", "isolation-refusal", "--reason", "explicit retry")
        self.assertEqual((code, json.loads(out)["status"]), (2, "incomplete"))


def tool_contract_grep():
    import diagnostic_tool_contract

    return diagnostic_tool_contract.GREP
