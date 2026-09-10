# Adopt the workflow in NICME

This is an implementation plan grounded in the workstation inventory of **2026-09-10**.
It does not modify NICME. Recheck the inventory when starting; the project has active
uncommitted research work, so its current working tree is not a reproducible release.

Read this plan together with [NICME validation design](NICME-VALIDATION.md), the
[generic operating guide](../agent-workflow/OPERATING-GUIDE.md) and
[setup procedure](../agent-workflow/SETUP.md).

## Observed starting state

| Item | Observed value | Implication |
| --- | --- | --- |
| Workstation checkout | `/mnt/storage/github/NICME` | Keep implementation worktrees as siblings |
| Origin | `git@github.com:Zi-Deng/UDC-Model.git` | Use the verified remote identity; do not invent `Zi-Deng/NICME` |
| Branch / HEAD | `main` / `e0df0a4` | HEAD dated 2026-05-06; working files have later changes |
| Pending state | 2,518 porcelain records; 2,440 staged paths | Reconcile deliberately before adoption |
| Staged artifacts | 2,331 paths under `results/` | Separate outputs from the workflow PR |
| Large staged files | An optimizer file ~701 MB and model file ~350 MB | Ordinary GitHub pushes will reject files this large |
| Memory | 14 paths already tracked in the index; no memory ignore rule | Adding `.gitignore` alone does not make them private |
| Packaging | `pyproject.toml`, Python ≥3.12, setuptools, `nicme-*` entry points | Preserve the existing package and command interfaces |
| Environment | `environment.yml` with shared `ml`; unpinned dependencies and CUDA package source | Do not mutate it per worktree; create a CPU CI environment separately |
| Checks | Ruff, pytest, compile and CLI smoke commands in Makefile | Build on real checks rather than adding placeholders |
| Tests | 30 test modules in the current working tree | Audit collection, skips and dependencies before requiring all of them |
| Agent instructions / CI | No tracked root `AGENTS.md` or `.github` workflow found | Add explicit project contract and hosted checks |

GitHub blocks regular files over 100 MiB; adding an ignore rule does not remove
objects already committed to history. Review the
[large-file documentation](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)
before choosing an artifact policy.

## Phase 0 — Preserve research and establish a baseline

Create a dedicated hygiene task before copying workflow files. This is T4 because it
touches staged research, private memory and potentially large Git objects. Keep this
phase separate from numerical or experiment changes.

1. Inventory staged, unstaged, untracked and ignored files. Check the remote identity
   and current default branch with `gh repo view`. Do not rename the GitHub repository
   as part of workflow adoption.
2. Back up the working tree, index state and untracked research artifacts to an approved
   location with enough space. A Git bundle alone excludes uncommitted/untracked files;
   a patch alone excludes many binary/ignored artifacts. Record what each backup covers
   and verify that representative files can be restored before modifying the index.
3. Review the 2,440 staged paths. Separate research source, small evidence manifests,
   publication material, raw outputs, checkpoints and private memory. Never `git add .`
   over this working tree or stash everything without inspecting size and contents.
4. For unwanted **new staged** artifacts, use a reviewed path list with
   `git restore --staged -- <paths>`; this preserves working files. For unwanted
   **previously tracked** paths, a separately approved `git rm --cached -- <paths>`
   removes tracking in a future commit while preserving local files. These are different
   operations; decide which applies from `git ls-files` and `git log -- <path>`.
5. Add `/memory/`, `/.agentic-local/` and a deliberate outputs policy to `.gitignore`.
   Check the existing `archives/` ignore entry against the actual `archive/` directory.
   Do not ignore all `results/` blindly if small historical evidence intentionally lives
   there; define explicit allowlisted manifests or move new evidence to a small directory.
6. Determine whether private memory or large objects have reached commits and remote
   branches. Removing tracking only affects future commits. If history repair is needed,
   make a separate plan with backups and coordination; do not force-push a rewritten
   research history during template adoption.
7. Commit coherent research changes separately, or preserve unfinished work on a clearly
   named branch. Leave a clean, understandable default-branch baseline for adoption.

Useful **read-only** inventory commands:

```bash
git -C /mnt/storage/github/NICME status --short
git -C /mnt/storage/github/NICME diff --stat
git -C /mnt/storage/github/NICME diff --cached --stat
git -C /mnt/storage/github/NICME ls-files memory
git -C /mnt/storage/github/NICME log --all --oneline -- memory
git -C /mnt/storage/github/NICME remote -v
```

**Exit criteria:** verified recovery material; staged/unstaged changes intentionally
resolved; no unreviewed private or oversized files in the pending publication set;
clean main checkout; working GitHub authentication; agreed artifact policy. The
template's `new-task` command intentionally refuses to bypass a dirty main checkout.

## Phase 1 — Install the collaboration contract

Open a T4 issue such as “Add isolated issue-to-PR workflow and deterministic CPU CI”.
Scope it to automation, instructions, selected CI dependencies and fixtures. Non-goals:
changing loss formulas, rerunning rebuttal campaigns, modifying data splits, renaming
the repository or updating published numbers.

Post an approved plan mapping each criterion to an artifact. Because NICME does not
yet have the helper, bootstrap the first adoption worktree with plain Git after Phase 0:

```bash
cd /mnt/storage/github/NICME
git fetch --prune origin
git worktree add -b issue-123-agentic-workflow \
  ../NICME-worktrees/issue-123-agentic-workflow origin/main
```

Replace `123` with the actual issue. Verify that `origin/main` is still the selected
baseline. From the template checkout:

```bash
python3 scripts/agentic/install.py \
  /mnt/storage/github/NICME-worktrees/issue-123-agentic-workflow
# Read the preview; then apply when conflict-free.
python3 scripts/agentic/install.py \
  /mnt/storage/github/NICME-worktrees/issue-123-agentic-workflow --apply
```

The installer does not replace NICME's Makefile, pyproject, root README, dependency
files or `.gitignore`. Reconcile those deliberately. If files now conflict, install
to a fresh staging directory and merge their contents instead of forcing a copy.

### Adapt `AGENTS.md`

Use [the prepared NICME instruction draft](../../examples/nicme/AGENTS.md.example)
and reconcile it with the live checkout. It must identify:

- `nicme/costs.py`: cost orientation, expected-cost decisions and metric contracts.
- `nicme/calibration.py`: temperature/Platt calibration and threshold selection.
- `utils/loss_functions.py` and `nicme/losses.py`: loss implementation and package API.
- `nicme/data_prep.py` and `nicme/dataset_profiles.py`: splits, class mappings and profiles.
- `nicme/modeling.py`, `nicme/training.py`, `model/` and `utils/`: model/training integration.
- `scripts/`: experiment planners, runners, analysis and backward-compatible entry points.
- `config/`, `paper_figures/`, `docs/`: experimental configuration and published evidence.

Record invariants rather than treating historical chat as authority. In particular,
the active cost convention is **`C[true_label][predicted_label]`**. Expected-cost
decisions minimize the candidate-column sum over true classes; this is not generally
the same as probability argmax. Preserve declared class order, normalizations and
the zero/limiting behavior documented by current tests.

Require explicit scope for workflow changes, cost and loss semantics, split creation,
calibration fitting/selection, metric reporting, campaign launchers and paper outputs.
Set Astra for all non-review roles. Keep Copilot review static and independent.

### Add project documentation and templates

Retain the imported generic protocol and customize its domain rubric with the NICME
validation design. Add a short workflow entry to NICME's README and `docs/README.md`.
Use the issue form's risk and validation fields. The PR body must separate software
checks from scientific evidence and record whether a change affects existing numbers.

Memory may hold private restart notes and local paths, but the public issue, plan, PR
and evidence manifest must be enough for an independent reviewer. Never copy the
private implementation conversation into the reviewer snapshot.

**Exit criteria:** reviewed operating contract; consistent public links; ignored local
memory; unchanged scientific behavior; portable workflow tests pass; an early draft
PR exists with the approved plan and explicit non-goals.

## Phase 2 — Establish real CPU CI

Follow [NICME-VALIDATION.md](NICME-VALIDATION.md). Preserve the existing local research
environment while introducing a separate locked CPU dependency set for CI. Do not
invoke `make setup` in Actions: it updates the named shared `ml` environment and
installs an editable project into it.

Add a project `quality` job on GitHub-hosted Linux with no model tokens, dataset
credentials or GPU requirements. Require offline fixtures, explicit imports for
required packages, Ruff against active code, collection accounting, CPU tests and
safe CLI smoke checks. The existing `agentic-quality` job verifies the portable
workflow only. Neither job by itself validates new paper claims.

Promote tests to the required gate only after they are demonstrated reproducible in
a clean environment. Fix pre-existing failures in separate small PRs where possible;
do not hide them with broad exclusions, `continue-on-error` or empty checks.

**Exit criteria:** stable test list and dependency lock; no accidental downloads,
uploads or GPU training; zero unexplained skips; clean checkout after tests;
observed GitHub check names captured in the PR.

## Phase 3 — Configure review and branch rules

Set the project's `required_checks` to its observed `quality` and `agentic-quality`
contexts, then generate and review the default-branch ruleset. In a solo repository,
require zero outside approvals but retain PR, checks, conversation resolution,
stale-review handling, linear history and human merge. Introduce CODEOWNERS only when
another eligible collaborator can review the protected paths.

Run one local Copilot review using the approved issue plan and exact head. The
snapshot exporter excludes `data/`, `weights/`, `checkpoints/`, `wandb/`, `memory/`
and runtime state. NICME's large tracked result history may contain additional
restricted material: inspect the source index and extend `private_path` or move to
a configurable project exclusion list before enabling review on those paths.

If the diff exceeds the review budget, split the PR; do not omit a portion of the
diff and call the review complete. If a source file exceeds the per-file limit,
record that omission and decide whether to raise the limit or obtain additional
human review. Static review has no credential to retrieve restricted datasets.

After the local loop works, optionally enable manual Actions review with the two
protected environments and its Copilot token. Do not turn the ML workstation into
a public PR runner. A costly experiment remains a human-triggered V2/V3 operation
against an exact recorded commit.

**Exit criteria:** a live SHA-bound COMMENT review; material findings disposed of;
required checks enforced on the real default branch; manual review eligibility and
spend tested separately if enabled.

## Phase 4 — Pilot an ordinary issue

Choose a genuine, small correctness issue from the backlog. A good candidate is a
missing boundary assertion for an existing metric, **if inspection confirms it is
missing**; the current suite already covers many NaN, class-order and cost-orientation
cases. Do not invent a defect to demonstrate the pipeline.

Use the full issue → plan → worktree → draft PR → CPU CI → Copilot review → Astra
repair loop. The maintainer reviews the scientific implications and merges only the
recorded reviewed head. Cleanup should reject a worktree that still contains valuable
ignored outputs or new local commits; archive them explicitly before removal.

Measure time to first draft PR, CI duration/failure rate, review findings accepted
versus rejected, repair rounds, model usage and unresolved evidence. Start with one
high-risk task at a time. If Astra cost becomes disproportionate, propose a measured
change to use cheaper OpenAI helpers for mechanical drafting or summaries; keep the
initial all-Astra policy until that decision is made.

## Rollback

Keep all adoption changes in a dedicated PR. Before merge, close the draft and preserve
its branch if the design needs revision. After merge, revert the adoption commit using
a normal PR while retaining any useful evidence. Disable the opt-in review variable
to stop future hosted model runs. Inspect rulesets before changing required names so
you do not leave a required check that no workflow can emit. Never roll back by
deleting research data or rewriting the existing project history.

## Completion checklist

- [ ] Phase 0 preservation and baseline work is complete.
- [ ] Installer origin hashes and template commit are recorded.
- [ ] NICME-specific instructions, domains and commands are reviewed.
- [ ] CPU software checks run in a clean environment without secrets or GPU use.
- [ ] Check names are observed, required and matched by merge preflight.
- [ ] Independent review uses a fresh Copilot session and recorded head SHA.
- [ ] Human domain and merge decisions appear in the durable PR record.
- [ ] A genuine pilot PR completes the loop; cleanup preserves unrelated work.
- [ ] Private memory and large artifacts remain outside new public commits.
