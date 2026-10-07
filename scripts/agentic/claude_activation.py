"""Native Claude capability activation: diagnostic assessment and the review-time gate.

A review may run through the native Claude reviewer only after two narrow diagnostics
(``native-tools-and-source`` and ``isolation-refusal``) qualified under the exact binding
in force. This module assesses a diagnostic capture; the activation ledger that records
qualified attempts and ``require`` that gates ordinary reviews land with it.
"""

from __future__ import annotations

import copy

import diagnostic_tool_contract as tool_contract
import review_coverage as coverage
from tasks import plain_path
from workflow import WorkflowError

PURPOSES = ("native-tools-and-source", "isolation-refusal")


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


def require(repo, policy, *, login_root=None):
    """Fail closed until the activation ledger records both qualified diagnostics."""
    raise WorkflowError(
        "Matching native capability diagnostics are unavailable: the activation ledger is not installed"
    )
