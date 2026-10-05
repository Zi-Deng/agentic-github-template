"""Record hosted test provenance and collect bound Actions receipts as review data."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from github_transport import artifact_receipt
from workflow import WorkflowError, sha, write_json

CHECK_NAME = r"[A-Za-z0-9_.-]{1,100}"


def record(output, check, test_status, clean_status, command):
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise WorkflowError("Execution receipt requires the disposable GitHub-hosted CI boundary")
    if not isinstance(check, str) or not re.fullmatch(CHECK_NAME, check):
        raise WorkflowError("Receipt check name must be a short workflow job identifier")
    if not isinstance(command, str) or not command.strip() or len(command) > 1000:
        raise WorkflowError("Receipt requires the exact validation command that ran")
    checkout = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("ascii").strip()
    value = {
        "schema_version": 1,
        "repository": os.environ["GITHUB_REPOSITORY"],
        "pr_head_sha": sha(os.environ["REVIEW_HEAD_SHA"]),
        "pr_base_sha": os.environ.get("REVIEW_BASE_SHA") or None,
        "tested_checkout_sha": sha(checkout),
        "event": os.environ["GITHUB_EVENT_NAME"],
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "check": check,
        "runner_environment": "github-hosted",
        "test_status": test_status,
        "clean_status": clean_status,
        "command": command,
        "validation_scope": "Executable software validation; no model, cloud or scientific claims.",
    }
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    write_json(output, value)
    return value


def collect(repo, head, checks, base=None, required=None):
    """Collect hosted receipts for the named checks; names come from trusted configuration."""
    receipts = []
    for check in checks:
        if required is not None and check.get("name") not in required:
            continue
        url = check.get("details_url", "") or ""
        match = re.fullmatch(
            rf"https://github\.com/{re.escape(repo.name)}/actions/runs/([0-9]+)(?:/job/[0-9]+)?", url
        )
        if not match:
            continue
        run_id = int(match[1])
        row = {
            "check_id": check.get("id"),
            "check": check.get("name"),
            "run_id": run_id,
            "state": "unknown",
            "tested_checkout_sha": None,
        }
        try:
            execution = repo.api(f"actions/runs/{run_id}")
            if not isinstance(execution, dict) or not isinstance(execution.get("repository"), dict):
                raise WorkflowError("Invalid Actions execution record")
            if (
                execution.get("head_sha") != head
                or execution.get("repository", {}).get("full_name") != repo.name
            ):
                row["state"] = "stale_run_association"
                receipts.append(row)
                continue
            artifacts = repo.api(
                f"actions/runs/{run_id}/artifacts?per_page=100", paginate=True, page_key="artifacts"
            )
            name = f"validation-{check['name']}-{head}-{execution.get('run_attempt')}"
            matches = [item for item in artifacts if item.get("name") == name and not item.get("expired")]
            if len(matches) != 1:
                row["state"] = "missing_or_ambiguous_receipt"
            else:
                receipt, archive_hash = artifact_receipt(repo.name, matches[0]["id"], repo.github_token)
                expected = {
                    "schema_version": 1,
                    "repository": repo.name,
                    "pr_head_sha": head,
                    "run_id": run_id,
                    "run_attempt": execution.get("run_attempt"),
                    "check": check["name"],
                    "runner_environment": "github-hosted",
                }
                if not isinstance(receipt, dict) or any(
                    receipt.get(key) != value for key, value in expected.items()
                ):
                    raise WorkflowError("Receipt binding differs")
                sha(receipt.get("tested_checkout_sha", ""))
                if base is not None and receipt.get("pr_base_sha") != base:
                    row["state"] = "stale_base_association"
                    receipts.append(row)
                    continue
                row.update(
                    state="observed",
                    tested_checkout_sha=receipt["tested_checkout_sha"],
                    artifact_id=matches[0]["id"],
                    archive_sha256=archive_hash,
                    test_status=receipt.get("test_status", "unknown"),
                    clean_status=receipt.get("clean_status", "unknown"),
                    pr_head_sha=head,
                    pr_base_sha=receipt.get("pr_base_sha"),
                    run_attempt=receipt["run_attempt"],
                    runner_environment="github-hosted",
                )
        except (WorkflowError, KeyError, TypeError, ValueError):
            row["state"] = "unavailable_or_invalid_receipt"
        receipts.append(row)
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--check", required=True, help="Workflow job name; validated against required_checks at collect time"
    )
    parser.add_argument("--command", required=True, help="The exact validation command that ran")
    parser.add_argument("--test-status", required=True)
    parser.add_argument("--clean-status", required=True)
    args = parser.parse_args()
    record(args.output, args.check, args.test_status, args.clean_status, args.command)
    print(json.dumps({"receipt": args.output, "check": args.check}))


if __name__ == "__main__":
    main()
