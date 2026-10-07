#!/usr/bin/env python3
"""Prepare a bound PR snapshot, run the selected reviewer under the coverage gate, publish on request."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath

import ci_evidence
import review_coverage as coverage
import review_packet
import review_policy
from profiles import family, review_selection
from tasks import atomic_json, atomic_text, plain_path, private_directory
from tasks import digest as value_digest
from workflow import Repo, WorkflowError, configuration, positive, run, sha, write_json

TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".sh",
    ".js",
    ".gs",
    ".cjs",
    ".mjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".sql",
    ".rs",
    ".go",
    ".c",
    ".h",
    ".cpp",
    ".java",
    ".xml",
    ".tex",
    ".r",
    ".R",
}
PRIVATE_PARTS = {"memory", ".agentic-local", ".env", ".ssh", "data", "checkpoints", "weights", "wandb"}
PACKET_SCHEMA = 7
# 1: pre-coverage packets (publishable and verifiable, never qualified); 5: FLOW-DC coverage
# packets (inspectable, verifiable); 7: packets this harness prepares. Schemas 2, 3, 4 and 6
# belong to frozen adapters that are not shipped here and are refused.
SUPPORTED_SCHEMAS = {1, 5, PACKET_SCHEMA}
COVERAGE_SCHEMAS = {5, PACKET_SCHEMA}
PACKET_KINDS = {"single"}
INSTALLED_REVIEWERS = ("copilot", "claude-code")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def private_path(name, private_paths=()):
    """Component names are fixed; repository-relative prefixes come from configuration."""
    path = PurePosixPath(name)
    return (
        bool(PRIVATE_PARTS.intersection(path.parts))
        or any(path.is_relative_to(prefix) for prefix in private_paths)
        or path.name.startswith(".env")
        or path.suffix in {".pem", ".key"}
    )


def tree(repo, commit):
    result = run(["git", "-C", repo.root, "ls-tree", "-r", "-l", "-z", commit]).stdout
    entries = []
    for record in result.rstrip("\0").split("\0"):
        if not record:
            continue
        meta, name = record.split("\t", 1)
        mode, kind, oid, size = meta.split()
        entries.append(
            {"path": name, "mode": mode, "kind": kind, "oid": oid, "size": int(size) if size != "-" else 0}
        )
    return entries


def snapshot(repo, commit, output, limits):
    """Read Git blobs directly. Never checkout, follow symlinks, or load PR settings."""
    output.mkdir()
    manifest = []
    total = 0
    for index, item in enumerate(tree(repo, commit)):
        name = item["path"]
        reason = None
        if private_path(name, limits.get("private_paths", ())):
            reason = "private/data path excluded"
        elif item["kind"] != "blob" or item["mode"] not in {"100644", "100755"}:
            reason = "symlink/submodule/nonregular entry excluded"
        elif Path(name).suffix not in TEXT_SUFFIXES and Path(name).name not in {
            "Makefile",
            "Dockerfile",
            ".gitignore",
        }:
            reason = "unsupported text type/binary excluded"
        elif item["size"] > limits["max_source_file_bytes"]:
            reason = "file exceeds configured size limit"
        if reason:
            manifest.append({"path": name, "omitted": reason})
            continue
        total += item["size"]
        if total > limits["max_snapshot_bytes"]:
            raise WorkflowError(
                "Source snapshot exceeds budget; reduce scope or explicitly revise the size limit"
            )
        blob = subprocess.run(
            ["git", "-C", str(repo.root), "cat-file", "blob", item["oid"]], capture_output=True, check=True
        ).stdout
        try:
            text = blob.decode("utf-8")
            if "\0" in text:
                raise UnicodeError("binary")
        except UnicodeError:
            manifest.append({"path": name, "omitted": "not UTF-8 text"})
            continue
        # Numeric filenames neutralize discovery of AGENTS, hooks, skills and MCP config.
        target = output / f"{index:06d}.txt"
        target.write_text(text, encoding="utf-8")
        manifest.append({"path": name, "snapshot": f"{output.name}/{target.name}", "blob": item["oid"]})
    return manifest


def current_pr(repo, number, head, base):
    pr = repo.pr(number)
    if pr["state"] != "open" or pr.get("merged"):
        raise WorkflowError("Review requires an open PR")
    if pr["head"]["sha"] != head or pr["base"]["sha"] != base:
        raise WorkflowError("PR head or base changed; prepare and review a fresh snapshot")
    return pr


def bind_selection(selection):
    """Bind a Claude selection to the current native login so the packet carries it."""
    if selection["policy"]["provider"] != "claude-code":
        return selection
    import claude_native_auth

    policy = claude_native_auth.bind(selection["policy"], root=selection.get("login_root"))
    return {**selection, "policy": policy}


def packet_login_root(repo, meta):
    """The login store for a packet's profile, resolved on this machine at run time."""
    from profiles import load_profiles, login_root_resolution

    cfg = configuration(repo.root)
    declared = load_profiles(cfg)["profiles"].get(meta.get("profile"))
    reviewer = declared["reviewer"] if declared else {"backend": None}
    return login_root_resolution(cfg, reviewer, "claude-code", repo)["login_root"]


def prepare(
    repo,
    number,
    issue_number,
    plan_comment,
    expected_head=None,
    output=None,
    prior_review=None,
    selection=None,
    *,
    review_provider=None,
    review_model=None,
    review_effort=None,
    allow_same_family=False,
    require_backend=None,
    review_exception=None,
):
    number, issue_number, plan_comment = map(positive, (number, issue_number, plan_comment))
    cfg = configuration(repo.root)
    if selection is None:
        # The CLI and Actions paths resolve the active profile from the trusted checkout;
        # explicit per-call overrides are recorded in the packet and never persisted.
        selection = review_selection(
            repo,
            cfg,
            review_provider=review_provider,
            review_model=review_model,
            review_effort=review_effort,
            allow_same_family=allow_same_family,
            require_backend=require_backend,
            review_exception=review_exception,
        )
    elif require_backend is not None and selection["policy"]["provider"] != require_backend:
        raise WorkflowError(f"This path requires a reviewer backend of {require_backend}")
    policy = review_policy.validate_policy(selection["policy"])
    if policy["provider"] not in INSTALLED_REVIEWERS:
        raise WorkflowError(
            f"The {policy['provider']} reviewer adapter is not installed in this harness "
            f"(profile {selection['profile']!r}; blocker claude_reviewer_adapter_not_installed)"
        )
    selection = bind_selection(selection)
    policy = selection["policy"]
    pr = repo.pr(number)
    head, base = sha(pr["head"]["sha"]), sha(pr["base"]["sha"])
    if expected_head and head != sha(expected_head):
        raise WorkflowError("Supplied reviewed SHA is not the current PR head")
    current_pr(repo, number, head, base)
    if not pr["head"].get("repo") or pr["head"]["repo"]["full_name"] != repo.name:
        raise WorkflowError(
            "Automated review supports same-repository PRs only; use a separately isolated review for forks"
        )
    if pr["base"]["ref"] != repo.base:
        raise WorkflowError("PR must target the default branch")
    issue = repo.api(f"issues/{issue_number}")
    if "pull_request" in issue:
        raise WorkflowError("Issue number refers to a PR")
    plan = repo.api(f"issues/comments/{plan_comment}")
    if plan["issue_url"].rstrip("/") != issue["url"].rstrip("/"):
        raise WorkflowError("The plan comment does not belong to the specified issue")
    comments = repo.api(f"issues/{issue_number}/comments", paginate=True)
    # Fetch full ancestry: GitHub's PR diff is the merge-base-to-head comparison.
    repo.fetch(f"refs/heads/{repo.base}", f"refs/pull/{number}/head")
    repo.git("cat-file", "-e", f"{head}^{{commit}}")
    repo.git("cat-file", "-e", f"{base}^{{commit}}")
    ancestor = repo.git("merge-base", base, head)
    names = (
        run(["git", "-C", repo.root, "diff", "--no-renames", "--name-only", "-z", ancestor, head])
        .stdout.rstrip("\0")
        .split("\0")
    )
    if any(private_path(name, cfg["private_paths"]) for name in names):
        raise WorkflowError("Diff touches excluded private/data paths; do not transmit it to the reviewer")
    diff = run(
        ["git", "-C", repo.root, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", ancestor, head]
    ).stdout
    if not diff.strip():
        raise WorkflowError("Diff is empty; there is no committed change to review")
    diff_limit = cfg["max_diff_bytes"]
    if diff_limit is not None and len(diff.encode("utf-8")) > diff_limit:
        raise WorkflowError("Diff exceeds max_diff_bytes; split the PR or explicitly adjust the cap")
    prior = None
    if prior_review:
        previous = plain_path(prior_review)
        old = verify_packet(previous)
        if any(
            old.get(key) != value
            for key, value in {
                "repository": repo.name,
                "pr": number,
                "issue": issue_number,
                "plan_comment": plan_comment,
            }.items()
        ):
            raise WorkflowError("Prior review repository/PR/contract binding differs")
        old_context = coverage.read_json(previous / "packet/context.json")
        if old_context["issue"].get("body") != issue.get("body") or old_context[
            "designated_plan_comment"
        ].get("body") != plan.get("body"):
            raise WorkflowError("Prior review contract changed; prepare a full fresh review")
        if run(
            ["git", "-C", repo.root, "merge-base", "--is-ancestor", old["head_sha"], head], check=False
        ).returncode:
            raise WorkflowError("Prior review head is not an ancestor of current head")
        if old.get("schema_version") not in COVERAGE_SCHEMAS:
            raise WorkflowError(
                "Legacy prior review has no required-material inventory; use a full fresh packet"
            )
        prior = (previous, old, qualification(previous))
    directory = (
        plain_path(output)
        if output
        else repo.main / ".agentic-local/reviews" / f"pr-{number}-{head[:12]}-{uuid.uuid4().hex[:8]}"
    )
    private_directory(repo.main / ".agentic-local")
    private_directory(directory, exist_ok=False)
    packet = directory / "packet"
    packet.mkdir()
    manifest = snapshot(repo, head, packet / "source", cfg)
    base_manifest = snapshot(repo, ancestor, packet / "base-source", cfg)
    write_json(packet / "source-index.json", manifest)
    write_json(packet / "base-source-index.json", base_manifest)
    (packet / "diff.txt").write_text(diff, encoding="utf-8")
    context = {
        "pull_request": pr,
        "issue": issue,
        "designated_plan_comment": plan,
        "issue_comments": comments,
        "pr_comments": repo.api(f"issues/{number}/comments", paginate=True),
        "inline_comments": repo.api(f"pulls/{number}/comments", paginate=True),
        "reviews": repo.api(f"pulls/{number}/reviews", paginate=True),
        "check_runs": repo.api(
            f"commits/{head}/check-runs?per_page=100", paginate=True, page_key="check_runs"
        ),
        "note": "Plan designation is supplied by the operator. Independently check its approval and scope. Check data is a point-in-time snapshot.",
    }
    context["commit_statuses"] = repo.api(f"commits/{head}/statuses?per_page=100", paginate=True)
    context["hosted_receipts"] = ci_evidence.collect(
        repo, head, context["check_runs"], base, required=cfg["required_checks"]
    )
    write_json(packet / "context.json", context)
    # Trusted policy comes from the caller's clean default-branch checkout, never PR code.
    for source, target in [
        ("AGENTS.md", "repository-policy.txt"),
        ("docs/agent-workflow/REVIEW.md", "review-policy.txt"),
        (cfg["domain_rubric"], "domain-policy.txt"),
    ]:
        (packet / target).write_text((repo.root / source).read_text(encoding="utf-8"), encoding="utf-8")
    if policy["provider"] == "copilot":
        agents = packet / ".github/agents"
        agents.mkdir(parents=True)
        shutil.copyfile(
            repo.root / ".github/agents/independent-reviewer.agent.md",
            agents / "independent-reviewer.agent.md",
        )
    shutil.copyfile(repo.root / ".agentic/schemas/review-report.json", packet / "report-schema.json")
    review_packet.build(
        repo,
        packet,
        head,
        ancestor,
        manifest,
        base_manifest,
        context,
        cfg,
        prior,
        provider=policy["provider"],
    )
    files = {str(p.relative_to(packet)): digest(p) for p in packet.rglob("*") if p.is_file()}
    metadata = {
        "schema_version": PACKET_SCHEMA,
        "kind": "single",
        # The immutable policy binds provider, exact model, effort, CLI pin and budget for this round.
        "review_policy": policy,
        "selection_sources": selection["sources"],
        "overrides": selection["overrides"],
        "profile": selection["profile"],
        "provenance": selection["provenance"],
        "repository": repo.name,
        "pr": number,
        "issue": issue_number,
        "plan_comment": plan_comment,
        "head_sha": head,
        "base_sha": base,
        "merge_base_sha": ancestor,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "files": files,
        "requested_model": policy["model"],
        "reviewer": {
            "backend": policy["provider"],
            "model": policy["model"],
            "effort": policy["effort"],
            "family": family(policy["model"]),
            "max_ai_credits": policy["budget"].get("ai_credits"),
            "max_estimated_usd": policy["budget"].get("estimated_usd"),
            "timeout_seconds": policy["budget"]["timeout_seconds"],
            "budget_exception": policy["budget"].get("exception"),
        },
        "config": cfg,
    }
    write_json(directory / "metadata.json", metadata)
    current_pr(repo, number, head, base)
    return directory


def verify_packet(directory):
    directory = plain_path(directory)
    metadata = coverage.read_json(plain_path(directory / "metadata.json"))
    version = metadata.get("schema_version")
    if type(version) is not int or version not in SUPPORTED_SCHEMAS:
        raise WorkflowError(
            "Unsupported review packet schema; 2, 3, 4 and 6 belong to frozen adapters this harness does not ship"
        )
    if version in COVERAGE_SCHEMAS or "review_policy" in metadata:
        review_policy.validate_policy(metadata.get("review_policy"))
        if metadata.get("requested_model") != metadata["review_policy"]["model"]:
            raise WorkflowError("Packet model differs from immutable review policy")
    if version == PACKET_SCHEMA and metadata.get("kind") not in PACKET_KINDS:
        raise WorkflowError("Unsupported review packet kind")
    packet = directory / "packet"
    actual = {str(p.relative_to(packet)): digest(p) for p in packet.rglob("*") if p.is_file()}
    if any(p.is_symlink() for p in packet.rglob("*")) or actual != metadata["files"]:
        raise WorkflowError("Review packet changed after preparation")
    return metadata


RESULT_FIELDS = {
    "review_sha256",
    "copilot_version",
    "provider_version",
    "diagnostics_sha256",
    "coverage_sha256",
}


def version_field(meta):
    return "provider_version" if meta["schema_version"] in COVERAGE_SCHEMAS else "copilot_version"


def assess_result(directory, meta, body, diagnostics):
    if meta["schema_version"] not in COVERAGE_SCHEMAS:
        raise WorkflowError("Pre-coverage review packets cannot be assessed; prepare a fresh packet")
    return coverage.assess(Path(directory) / "packet", body, diagnostics, policy=meta["review_policy"])


def stored_result(directory, meta):
    result = coverage.read_json(plain_path(Path(directory) / "review-result.json"))
    inputs = {key: value for key, value in meta.items() if key not in RESULT_FIELDS}
    if (
        not isinstance(result, dict)
        or type(result.get("schema_version")) is not int
        or result["schema_version"] != meta.get("schema_version")
        or result["schema_version"] not in COVERAGE_SCHEMAS
        or result.get("input_digest") != value_digest(inputs)
        or not isinstance(result.get("body"), str)
        or not result["body"].strip()
        or not isinstance(result.get(version_field(meta)), str)
        or not result[version_field(meta)].strip()
        or coverage.checksum(result["body"]) != result.get("review_sha256")
    ):
        raise WorkflowError("Saved review result changed or belongs to another packet")
    if result.get("policy_digest") != value_digest(meta["review_policy"]):
        raise WorkflowError("Saved review policy binding changed")
    diagnostics = result.get("diagnostics")
    if value_digest(diagnostics) != result.get("diagnostics_sha256"):
        raise WorkflowError("Saved diagnostics changed")
    assessment = assess_result(directory, meta, result["body"], diagnostics)
    if value_digest(assessment) != result.get("coverage_sha256"):
        raise WorkflowError("Saved coverage changed")
    capture = coverage.read_json(plain_path(Path(directory) / "review-capture.json"))
    if capture != {key: value for key, value in result.items() if key != "coverage_sha256"}:
        raise WorkflowError("Exact review capture changed or is missing")
    return result, assessment


def qualification(directory, *, require=False):
    """Shared gate used by recovery, publication, managed designation and preflight."""
    directory = plain_path(directory)
    meta = verify_packet(directory)
    if meta["schema_version"] not in COVERAGE_SCHEMAS:
        raise WorkflowError(
            "Pre-coverage review packet has no coverage record; prepare a fresh packet to qualify"
        )
    result, assessment = stored_result(directory, meta)
    if not (directory / "review.md").is_file() or digest(plain_path(directory / "review.md")) != meta.get(
        "review_sha256"
    ):
        raise WorkflowError("Review report changed or is incomplete")
    for name, key in (("diagnostics.json", "diagnostics_sha256"), ("coverage.json", "coverage_sha256")):
        value = coverage.read_json(plain_path(directory / name))
        if value_digest(value) != result[key] or meta.get(key) != result[key]:
            raise WorkflowError("Coverage or diagnostics changed or are missing")
    if require:
        review_policy.require_current_adapter(meta["review_policy"])
    if require and meta["review_policy"]["provider"] not in INSTALLED_REVIEWERS:
        raise WorkflowError("The reviewer adapter that produced this packet is not installed in this harness")
    if require and not assessment["qualified"]:
        raise WorkflowError(
            "Review coverage is incomplete; observed capability and every required material are necessary"
        )
    return assessment


def coverage_ready(directory):
    """Current-policy readiness, distinct from an immutable historical assessment."""
    meta = verify_packet(directory)
    if meta["schema_version"] not in COVERAGE_SCHEMAS:
        return False
    assessment = qualification(directory)
    try:
        review_policy.require_current_adapter(meta["review_policy"])
    except WorkflowError:
        return False
    return meta["schema_version"] == PACKET_SCHEMA and assessment["qualified"]


def recover_review(repo, directory):
    """Finalize a durably saved exact result without another model request."""
    directory = plain_path(directory)
    result_path = plain_path(directory / "review-result.json")
    capture_path = plain_path(directory / "review-capture.json")
    if not result_path.exists() and not capture_path.exists():
        return None
    meta = verify_packet(directory)
    if repo.name != meta["repository"]:
        raise WorkflowError("Review packet belongs to another repository")
    current_pr(repo, meta["pr"], meta["head_sha"], meta["base_sha"])
    if not result_path.exists():
        inputs = {key: value for key, value in meta.items() if key not in RESULT_FIELDS}
        capture = coverage.read_json(capture_path)
        if (
            meta.get("schema_version") not in COVERAGE_SCHEMAS
            or not isinstance(capture, dict)
            or capture.get("input_digest") != value_digest(inputs)
        ):
            raise WorkflowError("Pending review capture belongs to another packet")
        save_result(
            directory,
            inputs,
            capture.get("body"),
            capture.get("diagnostics"),
            capture.get(version_field(meta)),
        )
    result, assessment = stored_result(directory, meta)
    report = plain_path(directory / "review.md")
    if meta.get("review_sha256"):
        if (
            meta["review_sha256"] != result["review_sha256"]
            or meta.get(version_field(meta)) != result[version_field(meta)]
        ):
            raise WorkflowError("Completed review report or metadata changed")
        qualification(directory)
        return report
    atomic_text(report, result["body"])
    atomic_json(directory / "diagnostics.json", result["diagnostics"])
    atomic_json(directory / "coverage.json", assessment)
    meta.update(
        review_sha256=result["review_sha256"],
        diagnostics_sha256=result["diagnostics_sha256"],
        coverage_sha256=result["coverage_sha256"],
    )
    meta[version_field(meta)] = result[version_field(meta)]
    atomic_json(directory / "metadata.json", meta)
    return report


def save_result(directory, meta, body, diagnostics, version):
    directory = Path(directory)
    if meta.get("schema_version") not in COVERAGE_SCHEMAS or not isinstance(body, str) or not body.strip():
        raise WorkflowError("New results require a current packet and exact nonempty report")
    capture = {
        "schema_version": meta["schema_version"],
        "input_digest": value_digest(meta),
        "body": body,
        "review_sha256": coverage.checksum(body),
        version_field(meta): version,
        "diagnostics": diagnostics,
        "diagnostics_sha256": value_digest(diagnostics),
        "policy_digest": value_digest(meta["review_policy"]),
    }
    capture_path = plain_path(directory / "review-capture.json")
    if capture_path.exists():
        if coverage.read_json(capture_path) != capture:
            raise WorkflowError("Pending exact review capture changed")
    else:
        # Durable before assessment reads any packet file. A transient storage
        # failure can be recovered without another paid provider invocation.
        atomic_json(capture_path, capture)
    assessment = assess_result(directory, meta, body, diagnostics)
    atomic_json(directory / "review-result.json", {**capture, "coverage_sha256": value_digest(assessment)})


def review(repo, directory):
    try:
        return run_review(repo, directory)
    except BaseException:
        # Failures before inference still leave bounded diagnostic reasons. Never
        # replace a journal or already-captured attempt with a generic failure.
        directory = plain_path(directory)
        if not (directory / "diagnostics.json").exists() and (directory / "packet/capability.json").is_file():
            meta = coverage.read_json(directory / "metadata.json")
            policy = meta.get("review_policy") if isinstance(meta, dict) else None
            if (
                isinstance(policy, dict)
                and policy.get("provider") == "claude-code"
                and policy.get("adapter") == coverage.CLAUDE_ADAPTER
            ):
                import claude_telemetry

                _, diagnostics = claude_telemetry.capture(
                    b"",
                    directory / "packet",
                    directory / "packet",
                    policy,
                    "",
                    exit_code=None,
                    failure="preflight_or_storage_failure",
                )
            else:
                _, diagnostics = coverage.parse_events(
                    "",
                    directory / "packet",
                    directory / "packet",
                    exit_code=None,
                    failure="preflight_or_storage_failure",
                )
            atomic_json(directory / "diagnostics.json", diagnostics)
        raise


def run_review(repo, directory):
    directory = plain_path(directory)
    meta = verify_packet(directory)
    if repo.name != meta["repository"]:
        raise WorkflowError("Review packet belongs to another repository")
    current_pr(repo, meta["pr"], meta["head_sha"], meta["base_sha"])
    recovered = recover_review(repo, directory)
    if recovered is not None:
        return recovered
    if meta.get("schema_version") != PACKET_SCHEMA:
        raise WorkflowError("Legacy packets cannot run a coverage review; prepare a fresh packet")
    if (directory / "attempt.json").exists():
        raise WorkflowError(
            "Prior review attempt has no recoverable result; do not automatically spend another request"
        )
    provider = meta["review_policy"]["provider"]
    if provider not in INSTALLED_REVIEWERS:
        raise WorkflowError(
            f"The {provider} reviewer adapter is not installed in this harness "
            "(blocker claude_reviewer_adapter_not_installed)"
        )
    if provider == "copilot":
        from review_copilot import execute

        body, diagnostics, version = execute(repo, directory, meta)
    else:
        from review_claude import execute

        body, diagnostics, version = execute(repo, directory, meta, login_root=packet_login_root(repo, meta))
    # Save sanitized diagnostics on failure too. Provider homes and raw stdout /
    # stderr are discarded; only exact final model output survives separately.
    if body.strip():
        save_result(directory, meta, body, diagnostics, version)
    atomic_json(directory / "diagnostics.json", diagnostics)
    atomic_json(directory / "usage.json", diagnostics["usage"])
    atomic_json(
        directory / "attempt.json",
        {
            "schema_version": 5,
            "input_digest": value_digest(meta),
            "policy_digest": value_digest(meta["review_policy"]),
            "cli_version": version,
            "status": "finished",
            "requests": 1,
            "reasons": diagnostics["reasons"],
        },
    )
    if not body.strip():
        raise WorkflowError(
            "Reviewer returned no recoverable final report; see sanitized diagnostics.json (no automatic retry)"
        )
    verify_packet(directory)
    current_pr(repo, meta["pr"], meta["head_sha"], meta["base_sha"])
    return recover_review(repo, directory)


PROVIDER_LABELS = {"copilot": "Copilot CLI", "claude-code": "Claude Code"}


def exception_line(policy):
    """The recorded budget exception, bound into the published bytes."""
    limits = policy["budget"]
    exception = limits.get("exception")
    if not exception:
        return ""
    reason = " ".join(exception["reason"].split())
    return (
        f"Budget exception: timeout `{limits['timeout_seconds']} s`, reference ceiling "
        f"`${limits['estimated_usd']}` (policy defaults {exception['default_timeout_seconds']} s / "
        f"${exception['default_estimated_usd']}); reason: {reason}. "
    )


def publication_body(directory):
    meta = verify_packet(directory)
    body = (Path(directory) / "review.md").read_bytes().decode("utf-8")
    marker = f"<!-- agentic-review:{meta['head_sha']}:{meta['review_sha256']} -->"
    if meta["schema_version"] not in COVERAGE_SCHEMAS:
        if coverage.checksum(body) != meta.get("review_sha256"):
            raise WorkflowError("Legacy report bytes changed")
        return body + "\n" + marker
    assessment = qualification(directory)
    label = (
        "coverage-qualified static inspection"
        if assessment["qualified"]
        else "INCOMPLETE static inspection — not ready"
    )
    provider_label = PROVIDER_LABELS[meta["review_policy"]["provider"]]
    header = (
        f"## Independent {provider_label} review\n\nPR #{meta['pr']} · reviewed head `{meta['head_sha']}` "
        f"· base `{meta['base_sha']}`\n\nRequested model: `{meta['requested_model']}`. "
        + exception_line(meta["review_policy"])
        + f"Status: **{label}**. Model output below is preserved exactly. "
        "This is not human approval. The reviewer executed no tests. CI association and tested checkout "
        "are separately recorded in validation.json; unknown execution details remain unknown.\n\n"
    )
    binding = (
        f"<!-- agentic-coverage:v2:{value_digest(assessment)}:{meta.get('diagnostics_sha256', 'legacy')} -->"
    )
    return header + body + "\n\n" + marker + "\n" + binding


def verified_published(repo, directory, number, head, base):
    meta = verify_packet(directory)
    if any(
        meta.get(key) != value
        for key, value in {
            "repository": repo.name,
            "pr": number,
            "head_sha": head,
            "base_sha": base,
        }.items()
    ):
        raise WorkflowError("Review coverage record is stale or belongs to another PR")
    qualification(directory, require=True)
    expected = publication_body(directory)
    matching = [
        item
        for item in repo.api(f"pulls/{number}/reviews", paginate=True)
        if item.get("commit_id") == head and item.get("state") == "COMMENTED" and item.get("body") == expected
    ]
    if len(matching) != 1:
        raise WorkflowError("No unique published coverage-qualified review matches the exact saved report")
    return matching[0]


def verify_publication(repo, directory):
    """Read-only byte comparison, including historical reports; never inference."""
    meta = verify_packet(directory)
    if meta["repository"] != repo.name:
        raise WorkflowError("Review packet belongs to another repository")
    report = plain_path(Path(directory) / "review.md")
    if digest(report) != meta.get("review_sha256"):
        raise WorkflowError("Saved review bytes changed")
    expected = publication_body(directory)
    marker = f"<!-- agentic-review:{meta['head_sha']}:{meta['review_sha256']} -->"
    matches = [
        item
        for item in repo.api(f"pulls/{meta['pr']}/reviews", paginate=True)
        if marker in (item.get("body") or "") and item.get("commit_id") == meta["head_sha"]
    ]
    if len(matches) != 1 or matches[0].get("body") != expected:
        raise WorkflowError(
            "Exact saved/published review comparison failed; no model rerun or record rewrite"
        )
    return {
        "exact_match": True,
        "review_id": matches[0].get("id"),
        "head_sha": meta["head_sha"],
        "body_sha256": coverage.checksum(expected),
        "body_bytes": len(expected.encode("utf-8")),
        "coverage_qualified": meta["schema_version"] in COVERAGE_SCHEMAS and coverage_ready(directory),
    }


def publish(repo, directory):
    directory = plain_path(directory)
    meta = verify_packet(directory)
    body = publication_body(directory)
    if repo.name != meta["repository"]:
        raise WorkflowError("Wrong repository for review publication")
    if len(body.encode("utf-8")) > 60000:
        raise WorkflowError("Review exceeds the publication budget; summarize separately with attribution")
    current_pr(repo, meta["pr"], meta["head_sha"], meta["base_sha"])
    marker = f"<!-- agentic-review:{meta['head_sha']}:{meta['review_sha256']} -->"
    reviews = repo.api(f"pulls/{meta['pr']}/reviews", paginate=True)
    for existing in reviews:
        if marker in (existing.get("body") or ""):
            if (
                existing.get("body") != body
                or existing.get("commit_id") != meta["head_sha"]
                or existing.get("state") != "COMMENTED"
            ):
                raise WorkflowError("Existing publication marker has different content, head or state")
            return {"existing_review": existing["html_url"]}
    posted = repo.api(
        f"pulls/{meta['pr']}/reviews",
        data={
            "commit_id": meta["head_sha"],
            "event": "COMMENT",
            "body": body,
        },
    )
    return {"review": posted["html_url"], "commit_id": meta["head_sha"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("pr")
    prep.add_argument("--issue", required=True)
    prep.add_argument("--plan-comment", required=True)
    prep.add_argument("--expected-head")
    prep.add_argument("--output")
    prep.add_argument("--prior-review")
    review_policy.add_arguments(prep)
    prep.add_argument("--allow-same-family", action="store_true")
    prep.add_argument("--require-reviewer-backend", choices=["copilot", "claude-code"])
    for name in ["run", "publish", "qualify", "verify-publication"]:
        p = sub.add_parser(name)
        p.add_argument("directory")
    args = parser.parse_args()
    try:
        repo = Repo()
        repo.assert_main()
        if args.command == "prepare":
            result = prepare(
                repo,
                args.pr,
                args.issue,
                args.plan_comment,
                args.expected_head,
                args.output,
                args.prior_review,
                review_provider=args.review_provider,
                review_model=args.review_model,
                review_effort=args.review_effort,
                allow_same_family=args.allow_same_family,
                require_backend=args.require_reviewer_backend,
                review_exception=review_policy.exception_from_args(args),
            )
        elif args.command == "run":
            result = review(repo, args.directory)
        elif args.command == "publish":
            result = publish(repo, args.directory)
        elif args.command == "verify-publication":
            result = verify_publication(repo, args.directory)
        else:
            result = qualification(args.directory, require=True)
        print(json.dumps(result, indent=2) if isinstance(result, dict) else result)
        if args.command == "run" and not coverage_ready(args.directory):
            return 2
        return 0
    except (WorkflowError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
