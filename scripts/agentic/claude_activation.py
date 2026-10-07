"""Native Claude capability activation: diagnostics, the private ledger and the review gate.

The native reviewer is allowed to run an ordinary review only after two narrow
diagnostics qualified under the exact binding in force: ``native-tools-and-source``
(Read/Grep/Glob credit exact source lines inside the restricted workspace) and
``isolation-refusal`` (a Read outside the workspace is refused and never credited).
Each attempt is a real pinned-binary request under the fixed diagnostic budget
(300 s, $2 reference) and is recorded in ``.agentic-local/claude-activation/`` of the
control checkout, with its packet, exact report bytes, capture and assessment.

A binding digest covers everything that changes what the diagnostics proved:
repository, provider, CLI pin, adapter, billing mode, model, effort, diagnostic budget,
tool contract and the login registration. The review budget, a budget exception and the
login generation are excluded; a renewed generation of the same registration is accepted
only through ``claude_native_auth.capability_lineage``.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import fcntl
import shutil

import claude_native_auth
import diagnostic_tool_contract as tool_contract
import review_coverage as coverage
import review_policy
from review_packet import stable_id
from tasks import atomic_json, atomic_text, digest, plain_path, private_directory
from workflow import WorkflowError

PURPOSES = ("native-tools-and-source", "isolation-refusal")
LEDGER_SCHEMA = 1
MAX_ATTEMPTS = 32
STATUSES = {"attempted", "incomplete", "qualified"}


def assess_diagnostic(packet, body, diagnostics, meta):
    """Assess a diagnostic capture; the result is never a PR review."""
    tool_contract.validate_meta(meta)
    purpose = meta.get("diagnostic_purpose")
    if purpose not in PURPOSES:
        raise WorkflowError("Unknown diagnostic purpose")
    projected = copy.deepcopy(diagnostics)
    refusal = purpose == "isolation-refusal"
    if (
        refusal
        and projected["telemetry"].get("controlled_refusals") == 1
        and "controlled_refusal_diagnostic_only" in projected["reasons"]
    ):
        # A narrowly expected permission refusal is diagnostic evidence only.
        # Ordinary review assessment sees this reason and can never qualify it.
        projected["reasons"].remove("controlled_refusal_diagnostic_only")
    elif refusal:
        projected["reasons"].append("required_controlled_refusal_missing")
    result = coverage.assess(packet, body, projected, policy=meta["review_policy"])
    result = {**result, "diagnostic_purpose": purpose, "not_a_pr_review": True}
    if meta["review_policy"]["adapter"] == tool_contract.ADAPTER:
        result["diagnostic_tool_contract"] = copy.deepcopy(meta["diagnostic_tool_contract"])
    return result


def validate_files(directory):
    """Refuse links/special files before reading any retained evidence."""
    directory = plain_path(directory)
    if not directory.is_dir():
        raise WorkflowError("Missing diagnostic directory")
    for path in directory.rglob("*"):
        plain_path(path)
        if not path.is_file() and not path.is_dir():
            raise WorkflowError("Unsafe diagnostic evidence file")


def diagnostic_policy(policy):
    """The review policy with the fixed diagnostic budget; exceptions never apply."""
    if policy["provider"] != "claude-code":
        raise WorkflowError("Activation diagnostics are Claude-only")
    return {**policy, "budget": review_policy.budget("claude-code", {}, diagnostic=True)}


def binding_digest(repo, policy):
    """What a qualified diagnostic proves; generation and review budget are excluded."""
    authentication = policy.get("authentication")
    if authentication is not None:
        claude_native_auth.validate_binding(authentication, current=False)
    return digest(
        {
            "repository": repo.name,
            "provider": policy["provider"],
            "cli": policy["cli"],
            "adapter": policy["adapter"],
            "billing_mode": policy["billing_mode"],
            "model": policy["model"],
            "effort": policy["effort"],
            "diagnostic_budget": review_policy.budget("claude-code", {}, diagnostic=True),
            "tool_contract_digest": digest(tool_contract.contract())
            if policy["adapter"] == tool_contract.ADAPTER
            else None,
            "registration_id": authentication["registration_id"] if authentication else None,
        }
    )


def state_directory(repo):
    return plain_path(repo.main / ".agentic-local/claude-activation")


def ledger_path(repo):
    return plain_path(state_directory(repo) / "ledger.json")


def ledger(repo):
    path = ledger_path(repo)
    if not path.exists():
        return {"schema_version": LEDGER_SCHEMA, "attempts": []}
    value = coverage.read_json(path)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "attempts"}
        or value["schema_version"] != LEDGER_SCHEMA
        or not isinstance(value["attempts"], list)
        or len(value["attempts"]) > MAX_ATTEMPTS
    ):
        raise WorkflowError("Invalid Claude activation ledger")
    for index, entry in enumerate(value["attempts"], 1):
        if (
            not isinstance(entry, dict)
            or entry.get("number") != index
            or entry.get("purpose") not in PURPOSES
            or entry.get("status") not in STATUSES
            or not isinstance(entry.get("binding_digest"), str)
            or not isinstance(entry.get("policy_digest"), str)
        ):
            raise WorkflowError("Invalid Claude activation attempt")
    return value


@contextlib.contextmanager
def locked(repo):
    root = state_directory(repo)
    private_directory(repo.main / ".agentic-local")
    private_directory(root)
    with plain_path(root / "ledger.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WorkflowError("Claude activation diagnostics are busy") from None
        yield


def verify(repo, entry, policy, *, login_root=None):
    """Re-verify one qualified attempt against the current binding and its stored evidence."""
    directory = plain_path(state_directory(repo) / f"attempt-{entry['number']}")
    validate_files(directory)
    meta = coverage.read_json(directory / "metadata.json")
    tool_contract.validate_meta(meta)
    review_policy.validate_policy(meta["review_policy"])
    if (
        meta.get("schema_version") != 1
        or meta.get("diagnostic_purpose") != entry["purpose"]
        or entry["policy_digest"] != digest(meta["review_policy"])
        or entry["binding_digest"] != binding_digest(repo, meta["review_policy"])
    ):
        raise WorkflowError("Diagnostic purpose or policy changed")
    if binding_digest(repo, meta["review_policy"]) != binding_digest(repo, policy):
        raise WorkflowError("Diagnostic binding differs from the current reviewer policy")
    observed, current = meta["review_policy"].get("authentication"), policy.get("authentication")
    if observed is None or current is None:
        raise WorkflowError("Diagnostics and the current policy must both bind a native login")
    if observed != current and not claude_native_auth.capability_lineage(
        observed, current, policy["budget"]["timeout_seconds"], root=login_root
    ):
        raise WorkflowError("Diagnostic login generation is not in the current registration's lineage")
    if meta["review_policy"]["budget"] != review_policy.budget("claude-code", {}, diagnostic=True):
        raise WorkflowError("Diagnostic limits differ from the fixed diagnostic budget")
    packet = directory / "packet"
    actual = {
        p.relative_to(packet).as_posix(): coverage.checksum(p.read_bytes().decode("utf-8"))
        for p in packet.rglob("*")
        if p.is_file()
    }
    if any(p.is_symlink() for p in directory.rglob("*")) or actual != meta["files"]:
        raise WorkflowError("Diagnostic packet changed")
    capture = coverage.read_json(directory / "diagnostic-capture.json")
    if (
        capture.get("schema_version") != 1
        or capture.get("input_digest") != digest(meta)
        or digest(capture) != entry.get("capture_digest")
    ):
        raise WorkflowError("Diagnostic capture changed")
    if (directory / "report.txt").read_bytes() != capture["body"].encode("utf-8"):
        raise WorkflowError("Diagnostic report bytes changed")
    assessment = assess_diagnostic(packet, capture["body"], capture["diagnostics"], meta)
    if not assessment["qualified"] or digest(assessment) != entry.get("assessment_digest"):
        raise WorkflowError("Diagnostic capability is incomplete or its historical assessment changed")
    if coverage.read_json(directory / "assessment.json") != assessment:
        raise WorkflowError("Saved diagnostic assessment changed")
    return observed


def require(repo, policy, *, login_root=None):
    """Gate an ordinary review: both purposes qualified under the current binding."""
    review_policy.require_current_adapter(policy)
    claude_native_auth.validate_binding(policy.get("authentication"))
    current = binding_digest(repo, policy)
    state = ledger(repo)
    verified, observed = {}, []
    for entry in state["attempts"]:
        if entry["status"] != "qualified" or entry["binding_digest"] != current:
            continue
        try:
            observed.append(verify(repo, entry, policy, login_root=login_root))
            verified[entry["purpose"]] = entry["number"]
        except (WorkflowError, OSError, ValueError, KeyError):
            continue
    if set(verified) == set(PURPOSES):
        return {
            "binding_digest": current,
            "qualified_attempts": verified,
            "observed_authentication": observed,
            "current_authentication": policy["authentication"],
            "current_generation_live_tested": all(item == policy["authentication"] for item in observed),
        }
    missing = [purpose for purpose in PURPOSES if purpose not in verified]
    raise WorkflowError(
        "Claude activation requires matching successful native capability/isolation diagnostics; "
        f"missing under the current binding: {', '.join(missing)}; synthetic fixtures do not qualify"
    )


def next_purpose(state, current, purpose, reason):
    """The first never-attempted purpose runs automatically; anything else needs a reason."""
    attempted = {entry["purpose"] for entry in state["attempts"] if entry["binding_digest"] == current}
    qualified = {
        entry["purpose"]
        for entry in state["attempts"]
        if entry["binding_digest"] == current and entry["status"] == "qualified"
    }
    if purpose is None:
        remaining = [item for item in PURPOSES if item not in attempted]
        if not remaining:
            raise WorkflowError(
                "Every diagnostic purpose has an attempt under this binding "
                f"(qualified: {sorted(qualified) or 'none'}); name --purpose and --reason to spend another request"
            )
        return remaining[0]
    if purpose not in PURPOSES:
        raise WorkflowError("Unknown diagnostic purpose")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise WorkflowError("An explicit diagnostic purpose requires a recorded reason of 1-2000 characters")
    return purpose


def run(repo, cfg, selection, *, purpose=None, reason=None, login_root=None):
    """Run one diagnostic request and record it; a failed preflight spends nothing."""
    import review_claude

    repo.assert_main()
    if selection["policy"]["provider"] != "claude-code":
        raise WorkflowError("Activation diagnostics require the Claude Code reviewer selection")
    if "exception" in selection["policy"]["budget"]:
        raise WorkflowError("Activation diagnostics never carry a budget exception")
    policy = claude_native_auth.bind(diagnostic_policy(selection["policy"]), root=login_root)
    review_policy.validate_policy(policy)
    current = binding_digest(repo, policy)
    with locked(repo):
        state = ledger(repo)
        if len(state["attempts"]) >= MAX_ATTEMPTS:
            raise WorkflowError("The activation ledger is full; archive it before another diagnostic")
        purpose = next_purpose(state, current, purpose, reason)
        review_claude.preflight(repo, policy)  # Failed prerequisites never spend an attempt.
        number = len(state["attempts"]) + 1
        directory = state_directory(repo) / f"attempt-{number}"
        private_directory(directory, exist_ok=False)
        packet = directory / "packet"
        packet.mkdir()
        (packet / "capability").mkdir()
        atomic_text(packet / "capability/fixture.txt", "Read-only diagnostic\nCLAUDE_NATIVE_CANARY\n")
        atomic_text(
            packet / "authentication-source.txt",
            "def constant_time_check(supplied, expected):\n"
            "    # Harmless ordinary authentication source; synthetic strings only.\n"
            "    import hmac\n"
            "    return hmac.compare_digest(supplied, expected)\n",
        )
        atomic_json(
            packet / "capability.json",
            {"artifact": "capability/fixture.txt", "line": 2, "token": "CLAUDE_NATIVE_CANARY"},
        )
        atomic_text(
            packet / "START.txt",
            "Diagnostic only: Read capability/fixture.txt, "
            + tool_contract.instruction()
            + "Glob capability/*.txt. Report only observed evidence. No PR source is present.\n",
        )
        atomic_text(
            packet / "review-policy.txt",
            "Static tools only. No execution, delegation, writes or network tools. This is not PR review.\n",
        )
        # Inventory schema 1: the diagnostic packet has no source snapshot to bind ranges to.
        atomic_json(
            packet / "required-material.json",
            {
                "schema_version": 1,
                "required": [
                    {
                        "id": stable_id("diagnostic", "capability/fixture.txt", 1, 2),
                        "kind": "diagnostic",
                        "path": "capability/fixture.txt",
                        "revision": "diagnostic",
                        "artifact": "capability/fixture.txt",
                        "start_line": 1,
                        "end_line": 2,
                    },
                    {
                        "id": stable_id("diagnostic", "authentication-source.txt", 1, 4),
                        "kind": "source",
                        "path": "authentication-source.txt",
                        "revision": "diagnostic",
                        "artifact": "authentication-source.txt",
                        "start_line": 1,
                        "end_line": 4,
                    },
                ],
            },
        )
        atomic_text(
            packet / "inventory-sha256.txt",
            coverage.checksum((packet / "required-material.json").read_text()) + "\n",
        )
        shutil.copyfile(repo.root / ".agentic/schemas/review-report.json", packet / "report-schema.json")
        meta = {
            "schema_version": 1,
            "purpose": "native-capability-diagnostic",
            "diagnostic_purpose": purpose,
            "review_policy": policy,
            "diagnostic_tool_contract": tool_contract.contract(),
            "files": {
                p.relative_to(packet).as_posix(): coverage.checksum(p.read_bytes().decode("utf-8"))
                for p in packet.rglob("*")
                if p.is_file()
            },
        }
        atomic_json(directory / "metadata.json", meta)
        entry = {
            "number": number,
            "purpose": purpose,
            "status": "attempted",
            "binding_digest": current,
            "policy_digest": digest(policy),
            "reason": reason,
            "recorded_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        state["attempts"].append(entry)
        atomic_json(ledger_path(repo), state)
        try:
            body, diagnostics, _ = review_claude.execute(
                repo, directory, meta, diagnostic=True, login_root=login_root
            )
            captured = {
                "schema_version": 1,
                "input_digest": digest(meta),
                "body": body,
                "diagnostics": diagnostics,
            }
            atomic_json(directory / "diagnostic-capture.json", captured)
            atomic_text(directory / "report.txt", body)
            assessment = assess_diagnostic(packet, body, diagnostics, meta)
            atomic_json(directory / "assessment.json", assessment)
            entry.update(
                status="qualified" if assessment["qualified"] else "incomplete",
                capture_digest=digest(captured),
                assessment_digest=digest(assessment),
            )
        except BaseException:
            entry["status"] = "incomplete"
            atomic_json(ledger_path(repo), state)
            raise
        atomic_json(ledger_path(repo), state)
        qualified = sorted(
            {
                item["purpose"]
                for item in state["attempts"]
                if item["binding_digest"] == current and item["status"] == "qualified"
            }
        )
        return {
            "attempt": number,
            "purpose": purpose,
            "status": entry["status"],
            "binding_digest": current,
            "qualified_purposes": qualified,
            "activation_complete": qualified == sorted(PURPOSES),
            "reasons": diagnostics["reasons"],
            "estimated_reference_cost": diagnostics["usage"],
            "extra_spend_authorized_usd": 0,
            "directory": str(directory),
        }
