# Verification record

Recorded 2026-09-10. This file distinguishes implemented behavior, tests actually
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

Review preparation succeeded against the exact pilot head, collecting issue/plan,
diff, source and check evidence. Live model review **did not complete**: Copilot CLI
rejected the configured Claude model as unavailable under the tested credentials.
The wrapper returned nonzero, produced no `review.md`, and posted no model review.
No automatic model substitution occurred. Account/model access is therefore an
outstanding prerequisite, not a verified success.

Protected environments `copilot-review` and `copilot-review-publish` were configured
with maintainer approval and protected-branch deployment restrictions. Hosted model
review remains disabled through the opt-in variable until its separate credential
is supplied and validated. No hosted model generation or publication run is claimed.

The pilot remains a draft pending eligible Claude access and the human merge decision.
The effective branch rules and remote tree inventory were read back through GitHub;
no private memory/runtime path appeared in the remote tree.

Manual Actions review additionally requires the Copilot token, protected environments
and opt-in variable. The workflow file alone does not establish these settings.
Final merge remains the maintainer's action; local regression tests cover cleanup
without requiring an unattended production merge.

## Limits

No test suite proves the absence of all defects. Model findings need assessment,
credentials and account entitlements can expire, and new projects need real project
checks beyond the portable workflow tests. Keep this record current as live stages
complete and after substantive harness changes.
