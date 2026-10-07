# Independent provider review

The reviewer runs in a new provider process (the pinned Copilot CLI or the pinned native
Claude Code binary, as the active profile selects; see [PROVIDERS.md](PROVIDERS.md)) and
receives committed artifacts, not the implementation conversation. Its model-facing tools are read-only `view`, `grep` and `glob`. The wrapper
performs Git/GitHub operations outside that model process and qualifies a review only
when it observed the reviewer read every required material; see [COVERAGE.md](COVERAGE.md).

## Managed skill procedure

Use `$agentic-review PR #456` to coordinate the existing snapshot/run/publication
steps with a recorded task and approved plan. Use `$agentic-repair PR #456` afterwards
to retrieve both submitted reviews and inline comments and resume the exact implementer
session UUID. The standalone review skill completes review; the complete
workflow skill coordinates subsequent repair and re-review. See [SKILLS.md](SKILLS.md).

One attempted round per task is the default. A supported critical P0/P1 finding permits
a further round to verify its repair; the coordinator records the public finding and
concrete reason through the continuation flags. Other extra rounds need an explicit
user request. P2/P3 findings, uncertain questions and incomplete coverage do not by
themselves permit another round. Every additional invocation needs its own recorded
basis; see [the continuation procedure](SKILLS.md). A failed or INCOMPLETE run does not
establish readiness; [FINISH.md](FINISH.md#when-the-review-is-incomplete) lists the
maintainer's options. Keep the original report intact and publish dispositions separately.

## Local procedure

From the clean main checkout on the default branch:

```bash
python3 scripts/agentic/review.py prepare 456 --issue 123 --plan-comment 987654321
```

The command prints a private directory under `.agentic-local/reviews/`. Use that exact
path in the next commands:

```bash
python3 scripts/agentic/review.py run /absolute/path/printed/by/prepare
# Read review.md, coverage.json and diagnostics.json before publication.
python3 scripts/agentic/review.py publish /absolute/path/printed/by/prepare
python3 scripts/agentic/review.py qualify /absolute/path/printed/by/prepare
python3 scripts/agentic/review.py verify-publication /absolute/path/printed/by/prepare
```

`run` exits 2 when the saved report is INCOMPLETE; `qualify` fails for incomplete or
pre-coverage evidence; `verify-publication` compares the published body byte for byte.
A repair round passes `--prior-review` with the previous directory so unread material
and prior findings stay accountable.

`prepare` checks the plan's issue association and the current PR head/base. It fetches
the PR and base refs, computes the merge base, collects the complete textual diff,
issue and PR comments, inline findings, reviews, status contexts and check runs.
Pagination covers all timeline/review/check pages. Source files are read as Git blobs.
No PR checkout, Git filter, project hook or project command is executed.

The snapshot contains an index mapping original paths to numbered `.txt` files, so
repository agent settings, hooks and skills cannot become active configuration.
Git symlinks and submodules are listed as omissions and never followed. Known private
and data directories are excluded; a diff touching such paths is refused before it is
transmitted. This path policy is a baseline, not a content-based secret detector.
Inspect your own source and augment exclusions for a project's restricted paths.

The default budgets are 250 KB per source file, 12 MB of total text, 15 minutes and
400 Copilot AI credits (Copilot CLI 1.0.83 refuses an allowance below 30); `max_diff_bytes`
is `null` because the required-material inventory bounds each item at 120 lines or
16 KB and each navigation scope at 12 items, 800 lines or 64 KB. These are operational
choices, not claims about model capacity or price. Per-file omissions are recorded
explicitly and remain unsupported obligations; a change the reviewer cannot read in one
request comes back INCOMPLETE rather than silently presenting a partial review as
complete. Credit limits are provider controls and may overshoot by a request already
in flight. Split broad changes before raising budgets.

`run` copies the packet to a fresh workspace outside the repository, then uses fresh Copilot state, disabled hooks, no built-in MCP server, no inherited
provider override and no permission to execute, edit or delegate. Prompt-mode memory
and resume are not enabled. Repository policy and domain rubric come from the trusted
main checkout and are explicitly supplied as text. The requested model is recorded;
the template does not claim it independently attests to the provider's internal model
identity. Inspect `usage.json` and provider session metadata when that matters.
The tool controls follow the [Copilot CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
and [custom agent configuration](https://docs.github.com/en/copilot/reference/custom-agents-configuration).

`publish` rechecks head and base, verifies snapshot, report, diagnostics and coverage
hashes, and creates a COMMENT review with an explicit `commit_id` whose header states
**coverage-qualified static inspection** or **INCOMPLETE static inspection — not ready**.
Repeated publication of the same report returns its existing review URL. Any new
head/base requires a new snapshot. Only a qualified published report is designated;
`merge-preflight PR --reviewed-sha SHA --review-directory DIR` and the finishing gates
require that designation plus an exact match with the published body. Findings do not
automatically become approval, a green required check, or resolved threads.

A broad change that one request cannot cover may run as an explicit batch:
`review.py batch-preview DIR` plans component and integration units without inference,
`batch-run DIR` executes them sequentially under typed bounds and a named authorization,
and `task-review ISSUE --batch` is the managed equivalent. Every unit performs the three
probes and inspects its assigned IDs; the aggregate qualifies only when every unit,
including integration, is complete. See
[bounded batches](COVERAGE.md#provider-aware-bounded-batches).

The review directory is private working state, not a cryptographic attestation against
its own owner. Its hashes catch accidental edits. A user able to rewrite the manifest
can rewrite the record, so GitHub permissions and human assessment remain necessary.

## Why the snapshot differs from the PDF's detached checkout

A detached worktree reduces branch mistakes but is not a read-only security boundary:
an agent with shell access can still edit files, push a SHA or inspect shared credentials.
This implementation strengthens the review boundary by exporting committed text and
limiting tools. It also prevents a PR from installing its own reviewer hooks.

For independent execution tests, a human can create a detached worktree in a suitable
environment and run inspected commands without giving the model execution permission:

```bash
git fetch origin refs/pull/456/head
git worktree add --detach ../PROJECT-worktrees/review-pr-456 FULL_REVIEWED_HEAD_SHA
```

Confirm the fetched SHA matches the record. Test environments write caches and may
execute arbitrary project code; use disposable infrastructure for unfamiliar code.
Attach command and commit evidence to the PR. The static reviewer must never describe
those tests as having executed them itself.

## Report contract

| Severity | Meaning |
| --- | --- |
| P0 | Catastrophic, concrete merge blocker |
| P1 | Likely major correctness or security failure in supported use |
| P2 | Reachable edge-case defect or substantive evidence gap |
| P3 | Minor optional improvement; suppress tooling-covered style remarks |

Every finding needs **location, claim, trigger, impact, evidence and minimal fix
direction**. Use original repository paths and line numbers from the mapped source,
not the numbered snapshot filenames. Demonstrate the reachable code path or provide
a reproducible test proposal. Do not invent executed commands.

The final response is one bare JSON object matching the packet's `report-schema.json`
(`schema_version: 2`, `inventory_sha256` copied from `inventory-sha256.txt`, `findings`,
`reviewed` listing positively inspected required IDs, `incomplete` grouping specific
unread or unsupported IDs with a reason, and `limitations`), with no introductory prose or
Markdown fences; capability and scope notes belong inside `limitations`. Single-request
prompts require the full inventory, batch unit prompts the assigned IDs. These are prompt
constraints, not a guarantee of compliance: one complete outer `json` fence is still
accepted without changing saved bytes, while surrounding prose stays malformed. Omitted IDs stay unread and
block qualification; general limitations appear once. The wrapper credits a `reviewed`
claim only when the literal tool results covered its complete range.

Limitations must state that the reviewer executed no tests, list omitted source and
inaccessible artifacts, and separate residual risks from supported findings.
Explicitly state when no material finding is supported. Treat uncertain concerns as
questions, not proven defects. Check negative inputs, missing data, ties, NaNs,
interruption, compatibility, concurrency and numerical boundaries where relevant.
For scientific changes apply [the domain rubric](domain-review.md).

## Manual Actions procedure

Complete the protected-environment setup in [SETUP.md](SETUP.md#6-enable-manual-actions-review).
Then use **Actions → manual Copilot review → Run workflow**, on the default branch.
Select the PR, issue, plan comment ID and exact head, and optionally a profile name
(empty means the repository `hosted_profile`; for example `-f profile=fable-gpt`). The
hosted path is Copilot-only: a profile whose reviewer backend is `claude-code` is refused
by `--require-reviewer-backend copilot`, never converted.
Choose publication only when you intend to post the generated report; otherwise download
and inspect the artifact.

```bash
gh workflow run copilot-review.yml --ref main \
  -f pr=456 -f issue=123 -f plan_comment=987654321 \
  -f head_sha=FULL_CURRENT_PR_HEAD_SHA -F publish=false
```

Generation and publication are separate jobs. The model job has only read permissions;
the publication job has PR write permission and does not run a model. Neither checks
out or executes PR code. Same-repository PRs only are supported. Forks and non-default
base branches need a separate reviewed isolation procedure, not a bypass flag.

The workflow is deliberately opt-in and manual. It has no issue-comment trigger,
automatic repair loop, model token on ordinary PR checks, or self-hosted runner.
Changing the workflow or its credentials is T4 work.

## Model selection and repair

The reviewer comes from the active profile: Copilot `claude-opus-5` under `astra-copilot`,
Copilot `gpt-6-astra` under `fable-gpt`, Claude Code native `claude-opus-5-5` under
`astra-claude` (its adapter arrives in a later PR; until then preparation reports
`claude_reviewer_adapter_not_installed` and runs nothing). Each review round freezes the
resolved policy (provider, exact model, effort, CLI pin, budget) into its record and packet
metadata; a profile switch afterwards changes only future rounds. Explicit per-call
`--review-provider`, `--review-model` and `--review-effort` flags on `review.py prepare`
override one round and are recorded as `overrides` with their sources; naming only the
provider resets model and effort to that provider's defaults. Other model versions are
separate choices, not automatic substitutes. Choose another explicit Claude or GPT ID only
after checking account availability and deciding the cost is justified.
Do not use `auto`, a built-in agent that silently selects another family, or the
implementation conversation as a review session. A reviewer from the implementer's model
family is refused unless recorded with `--allow-same-family`.
Native GitHub Copilot code review is a separate service and does not let you pin this
Claude choice; see [GitHub's code review description](https://docs.github.com/en/copilot/concepts/agents/code-review).

Local preparation reads configuration from the caller's clean main checkout;
Actions reads it from the trusted default branch. A model-change PR takes effect
for the standard review procedure after the maintainer merges it and the local
main checkout is updated. The normal procedure reviews the introducing PR under the existing trusted configuration.
An explicit maintainer instruction to use the new model/budget for that PR permits
a narrowly scoped override in the trusted invocation. Record the authorization and
exact values in fresh packet metadata; do not activate unreviewed PR instructions or
rewrite existing packets. This is how the Opus/400-credit transition was authorized. Changing an interactive Copilot model preference
does not override this wrapper's explicit `--model` argument.

Prepare a fresh packet after a model change. Each packet records its requested
model and budgets at preparation; changing `.agentic/config.json` afterwards does
not retarget that packet. Check `metadata.json`, `review.md`, and CLI-reported
`usage.json` for the actual run. Configuration and mocked tests do not prove live
account access. Historical Sonnet pilot results in `VERIFICATION.md` remain
evidence for those earlier runs, not evidence of Opus inference. Historical Fable
reports are likewise retained under their original model and budgets.

Managed repair remains a task of the original implementer session on the original branch
**and original session UUID**. A missing UUID must be recovered rather than replaced or selected with
`--last`. The legacy interactive launcher remains a manual alternative that starts
a separate session. Post a finding-by-finding
response with commits and evidence. One attempted round remains the default; additional
review follows the critical-finding or explicit-continuation policy above. A larger
per-review credit allowance does not authorize more rounds or establish completeness.

### Claude Opus 5 access

Checked on 2026-09-14. GitHub lists Claude Opus 5 as generally available and supports
it in Copilot CLI for eligible plans. Business and Enterprise administrators may need
to enable its model policy. Check the actual authenticated account rather than infer
availability from a template setting. See [GitHub's Opus 5 announcement](https://github.blog/changelog/2026-07-24-claude-opus-5-is-now-available-in-github-copilot/)
and [current supported models](https://docs.github.com/en/copilot/reference/ai-models/supported-models).

The 400-credit setting is a provider-enforced soft allowance for this single-prompt
review invocation, not a price quote or guarantee of complete inspection. Actual usage
may overshoot at a request boundary. Preserve `usage.json` and report any incomplete
coverage. See [GitHub's credit-limit reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference#command-line-options).

Private-path exclusions and static tool restrictions remain in force. The snapshot's
ordinary source and public comments are sent to Copilot for the authorized review.

### GPT-6 Astra access

Checked on 2026-09-24. GitHub announced GPT-6 Astra as generally available in Copilot,
including Copilot CLI, on 2026-09-04 for Copilot Pro+, Max, Business and Enterprise;
Business and Enterprise administrators must enable its model policy. GitHub's documentation
lists the display name; the CLI ID `gpt-6-astra` follows the lowercase convention shown in
`copilot --help` and is confirmed by the first real review recorded in VERIFICATION.md.
See [GitHub's GPT-6 Astra announcement](https://github.blog/changelog/2026-09-04-gpt-6-astra-is-generally-available-in-github-copilot/)
and [current supported models](https://docs.github.com/en/copilot/reference/ai-models/supported-models).
