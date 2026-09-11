# Verification record

Recorded 2026-09-10 (America/Los_Angeles; the later Actions timestamps are September 11 UTC).
This file distinguishes implemented behavior, tests actually
executed, and account-dependent verification. A green local suite does not establish
that a model account or GitHub environment has been configured.

## Local template validation

- **30 regression tests passed** using real temporary Git repositories and bare
  remotes. GitHub and Copilot calls in that suite are controlled test doubles.
- Ruff 0.16.7 lint and formatting checks passed.
- actionlint 1.7.12 accepted the three GitHub Actions workflows.
- The suite exercises sibling isolation, branch reuse, non-`main` default branches,
  dirty-checkout rejection, malformed input rejection, SHA-bound review publication,
  snapshot mutation detection, private-path exclusion and symlink handling.
- Cleanup tests prove that squash-merged work can be removed while dirty/ignored
  work, fork branches, wrong bases and post-merge local commits are preserved.
- Merge preflight tests reject stale heads, missing review records and missing or
  skipped checks; the successful path prints a pinned command without merging.
- Installer previews for NICME and SpiderML found no conflicting workflow-owned
  paths at inspection time and wrote nothing to either project.

Reproduce the template gate:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make check
```

CI additionally checks that tests did not dirty a committed checkout. Local private
memory and virtual environments are ignored; absence from the index is checked
separately before publication.

## Limited NICME baseline

Copied the current `nicme/`, `utils/`, `model/` directories and three test modules to
a temporary directory. Ran them with the existing `ml` environment, GPU visibility
disabled, Hugging Face offline variables, W&B offline and pytest's cache provider
disabled:

```text
test_costs.py + test_calibration.py + test_loss_functions.py
56 passed in 2.42 seconds
```

This establishes a passing CPU subset for the copied working files and the installed
environment. It is not a clean-clone or locked-environment result, not a pass for all
NICME tests, and not reproduction of the research findings. The working tree differed
from HEAD, so the test result must not be attributed to HEAD `e0df0a4` alone. NICME
itself was not edited and no GPU campaign was launched.

## Live GitHub bootstrap verification

On 2026-09-10, published [Zi-Deng/agentic-github-template](https://github.com/Zi-Deng/agentic-github-template)
as a public repository and enabled the template flag. The initial commit is
`fc2556e19445b694b8c76f3462843587ee317fb0`.

Both initial hosted workflows succeeded:

- [ci / quality run](https://github.com/Zi-Deng/agentic-github-template/actions/runs/34541182069).
- [agentic workflow tests run](https://github.com/Zi-Deng/agentic-github-template/actions/runs/34541182063).

Created [pilot issue #1](https://github.com/Zi-Deng/agentic-github-template/issues/1)
and its [designated plan comment](https://github.com/Zi-Deng/agentic-github-template/issues/1#issuecomment-5626696118).
The new-task command created and pushed `issue-1-live-verification` in a sibling
worktree while main stayed clean. The initial publication included 41 tracked files,
with no memory, runtime state or virtual environment files.

The [pilot PR #2](https://github.com/Zi-Deng/agentic-github-template/pull/2) was created
in draft using the helper. Its hosted `quality` and `agentic-quality` checks passed.
The effective `main` rules include PR review-thread resolution, strict required
`quality` from GitHub Actions integration 15368, linear history, and blocks on force
pushes/deletion. Ruleset ID: `22841485`; no bypass actors are configured.

### Local review after the plan upgrade

The first review attempt failed because the credential could not select the configured
Claude model. It returned nonzero, produced no `review.md`, and posted no model review.
After the maintainer enabled an eligible Copilot plan, the existing `gh` OAuth
credential successfully ran the requested model, `claude-sonnet-5`. The CLI usage
artifact also reported that model. No automatic model substitution was used.

The [local COMMENT review](https://github.com/Zi-Deng/agentic-github-template/pull/2#pullrequestreview-5173711278)
was published against `bd322735cc4c3140aae7ffe7264548e4a962e12b`, with base
`fc2556e19445b694b8c76f3462843587ee317fb0`. It reported no P0/P1/P2 findings but
inspected only part of the supplied evidence. Its optional observations and
limitations remain in the report; this is a static review, not human approval.

### Hosted generation and artifact verification

Protected environments `copilot-review` and `copilot-review-publish` were configured
with maintainer approval and protected-branch deployment restrictions. The maintainer
stored a fine-grained Copilot Requests token, the opt-in variable was enabled, and
the first run paused until the maintainer approved `copilot-review`.

[Actions run 34548086695](https://github.com/Zi-Deng/agentic-github-template/actions/runs/34548086695)
then succeeded: pinned CLI installation, packet preparation, Claude generation and
artifact upload all passed. It used trusted control code from the initial main
commit and reviewed head `bd322735cc4c3140aae7ffe7264548e4a962e12b`. The downloaded
artifact's packet and report hashes were verified locally; its requested and
CLI-reported model were both `claude-sonnet-5`.

That run used `publish=false`, so the separate Actions publication job was correctly
skipped. After inspection, the downloaded report was published using the local
wrapper as a [SHA-bound COMMENT review](https://github.com/Zi-Deng/agentic-github-template/pull/2#pullrequestreview-5173913692).
This verifies hosted generation and local publication of its artifact. It does not
claim that the separate Actions publication job has been exercised.

The hosted report identified the obsolete statement that Claude access remained
blocked. This follow-up record fixes that P2 documentation finding. It also clarifies
the two credential identities in the setup guide. The report's claim that check-run
evidence was absent is inaccurate: the downloaded `context.json` contains successful
`quality` and two `agentic-quality` runs plus the preceding local review. The reviewer
disclosed incomplete inspection of those arrays. Branch-rule read-back remains an
operator verification, since the packet does not include the ruleset object.

### Remaining acceptance boundary

Account access and hosted inference are now verified. The pilot remains subject to
review of its latest head, maintainer assessment and a human merge decision. Each
review above is evidence for its recorded commit, not for later documentation edits.
The effective branch rules and remote tree inventory were read back through GitHub;
no private memory/runtime path appeared in the remote tree.

Future adopters must configure their own token, protected environments and opt-in
variable. This repository's settings are not copied by GitHub's template mechanism.
Local regression tests cover cleanup without an unattended production merge.

## Limits

No test suite proves the absence of all defects. Model findings need assessment,
credentials and account entitlements can expire, and new projects need real project
checks beyond the portable workflow tests. Keep this record current as live stages
complete and after substantive harness changes.
