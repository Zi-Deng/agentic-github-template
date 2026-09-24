# Adopt the workflow in SpiderML

Inventory on 2026-09-10 found `/mnt/storage/github/SpiderML` clean on `main` at
`080da43`, with origin `https://github.com/zkdeng-uofa/SpiderML.git` and 28 tracked
files. Memory is already ignored and not tracked. No root AGENTS.md, pytest suite
or GitHub workflow was found in the tracked project. Recheck before adoption.

The active training scripts are `huggingFaceTemplate.py`, `huggingFacePartialFreeze.py`
and `huggingFaceDistillation.py`; evaluation and plotting live beside them in
`scripts/`. The project has both `pixi.toml`/`pixi.lock` and `environment.yml`. The local
CLAUDE.md references some historical paths and commands absent from the tracked file
set, so it should be reconciled against the actual project before becoming shared policy.
That `CLAUDE.md` is untracked and ignored, but any `CLAUDE.md` at or above the working
directory stops Claude Code from reading `AGENTS.md`. After installing `AGENTS.md`, add
`@AGENTS.md` as the first line of that local `CLAUDE.md`, and add
`/.claude/settings.local.json` to the project's ignore rules.

## Recommended sequence

1. Verify access to `zkdeng-uofa/SpiderML` explicitly; the authenticated `Zi-Deng`
   account may have different rights there. Preserve the current license and README
   disclaimers. Do not infer organizational or data rights from filesystem access.
2. Create a T4 adoption issue and approved plan. Add the workflow in a sibling
   `SpiderML-worktrees/issue-N-agentic-workflow` directory after confirming main is clean.
3. Preview and install the template's portable files. Preserve root README, license,
   Pixi configuration and environment file. Add `/.agentic-local/` to the ignore policy
   and verify the existing memory ignore rule remains effective.
4. Choose one canonical project environment. If using Pixi, keep its existing lock and
   define fast check tasks there. The current Pixi tasks are training tasks; they are
   not appropriate CI validation commands. Do not silently replace the environment
   management approach with NICME's shared micromamba environment.
5. Write AGENTS.md from the current tracked architecture. Document external dataset
   loading, model loading, upload/logging effects and output paths. Select a
   profile with `workflow.py profile use` and keep the independent Copilot reviewer from
   this template.
6. Build a real offline CPU test baseline before making project CI required. Existing
   training and evaluation scripts load models/datasets; do not run their normal main
   functions as a smoke test. Extract testable pure helpers where justified by a small
   scoped PR, and test transforms, collation, label order and metric aggregation with
   synthetic inputs. AST/compile checks alone are only an interim baseline.
7. Add deterministic hosted project checks plus portable `agentic-quality`. Observe
   actual job names, then configure rulesets and optional manual Copilot review. Keep
   GPU/SLURM execution and Hugging Face/W&B publication outside ordinary PR checks.
8. Pilot one genuine issue through draft PR, static independent review, repair, human
   merge and guarded cleanup before considering further automation.

## Use the skills after adoption

Install the eight `.agents/skills` directories, the generated `.claude/skills` mirror,
and the matching helpers and operating documents. Verify discovery in the actual Codex host and retain any existing project
skills. Each new project starts with its own private task records; never transfer an
executor UUID or `.agentic-local` directory from the template or NICME.

Invoke `$agentic-workflow` for a genuine small issue or use the individual phase
skills in [SKILLS.md](../agent-workflow/SKILLS.md). Confirm the issue, proposed plan and
PR belong to `zkdeng-uofa/SpiderML`. Approve the plan before implementation. The
registered sibling worktree must use the selected SpiderML environment and offline
CPU checks; skill invocation does not authorize training, downloads or publication.

The pinned implementer session handles implementation and subsequent repairs using the
same saved UUID. `$agentic-review PR #P` obtains a new snapshot review by the profile's
reviewer, and `$agentic-repair PR #P` reads both review and inline comments before
resuming that session.
Include split/label-map/processor/checkpoint evidence relevant to the actual change.

Use `$agentic-finish PR #P` to assess readiness and obtain the human-run command.
The human script archives ignored artifacts before guarded cleanup. Preserve dataset
symlinks without following them, stop task-specific writers, and retain checkpoint
provenance and the archive journal. The shared dataset, model cache and external
services are not isolated merely because implementation used a worktree. See
[FINISH.md](../agent-workflow/FINISH.md) for recovery and partial completion states.

## Scientific review focus

Preserve the relationship between training and evaluation splits, including seed,
stratification and dataset revision. The inspected evaluation code uses a two-stage
80/10/10 split with fixed seed 42; changing a CLI seed does not automatically imply
that split construction uses it. Verify actual code behavior before documenting or
changing that contract. Audit duplicate images and species/collection grouping where
the scientific question requires independence beyond sample-level random splitting.

Require consistent label maps, image processor and validation transforms, teacher/
student settings for distillation, frozen-layer definitions and checkpoint selection.
Distinguish macro and prevalence-weighted metrics and validate per-class report order.
Keep model/dataset revisions, seeds and configs with the evidence. New accuracy claims
need the domain gate, not just a software test pass.

## First adoption PR acceptance

- No changed training semantics, published numbers or dataset contents.
- Updated shared instructions correspond to tracked scripts and real environment tasks.
- Offline CPU tests execute without downloads, uploads, model tokens or GPUs.
- Required check names match observed runs and private memory remains untracked.
- All eight skills are discoverable, and implementation/repair use the same saved UUID.
- A fresh Copilot review identifies its exact SHA and limitations.
- The maintainer completes a real pilot merge; cleanup preserves local run artifacts.

Use [SETUP.md](../agent-workflow/SETUP.md#existing-projects) for copy commands and
[the operating guide](../agent-workflow/OPERATING-GUIDE.md) for day-to-day operation.
This task leaves SpiderML unchanged; the steps above belong in its adoption PR.
