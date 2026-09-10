# NICME validation design

This companion to [the migration plan](NICME.md) defines a proposed evidence ladder.
It is a plan for NICME, not a claim that its full current research tree passes clean
CI. See the template's [verification record](../agent-workflow/VERIFICATION.md) for
the limited CPU baseline actually measured during this task.

## Preserve the existing scientific contracts

The active implementation and tests define the behavior to protect. As inspected on
2026-09-10:

| Contract | Existing implementation / tests | Review obligation |
| --- | --- | --- |
| Rows are true labels; columns are predictions | `nicme/costs.py`; `tests/test_costs.py`; `tests/test_loss_functions.py` | Hand-computed asymmetric example must detect transposition |
| Bayes action minimizes expected cost | `cost_min_predictions`; calibration decision tests | Check candidate actions against `probabilities @ C`, not argmax |
| Metric denominators and class handling are deliberate | Cost, normalized ATC, macro class-conditional cost, probability metric tests | Preserve normalizers; distinguish observed-class averaging from declared classes |
| Calibration and thresholds use valid inputs | `nicme/calibration.py`; `test_calibration.py`; reporting tests | Validate finite values, class support, identifiability and held-out fitting |
| NICME limits and gradients match the intended formulation | `utils/loss_functions.py`; `test_loss_functions.py` | Test limiting cases and gradient direction; preserve legacy aliases deliberately |
| Data profiles and split provenance stay consistent | `dataset_profiles.py`, `data_prep.py`; their tests | Stable label map, grouping, disjoint IDs and leakage checks |
| Model adapters match the selected backbone | `nicme/modeling.py`; `test_modeling.py` | Confirm adapter targets and output shapes without downloading weights in CI |
| Campaigns separate planning from execution | `scripts/run_*`; runner tests | CI may test command construction; it must not launch the real campaign |

The current cost-minimum implementation uses `np.argmin`; tie-breaking behavior is
therefore part of the existing observable result unless explicitly changed. Normalized
ATC and macro class-conditional cost are different statistics. The exact implementation
and test fixture, not an acronym alone, must determine the reported formula.

## V0 — Focused feedback

Use the active worktree's environment. Run only the relevant test module while editing,
then widen to the required CPU suite. For a regression, capture failure on the base
and success on the head with identical dependencies and fixtures.

Do not point an editable install in the shared `ml` environment at a task worktree;
another terminal could then import changing code unexpectedly. Prefer a per-worktree
venv or a separately named environment. Shared datasets/checkpoints may be read-only,
with task-specific output directories.

## V1 — CPU gate on every PR

Introduce a checked-in, resolved CPU dependency lock after testing actual compatibility.
Do not treat the current `requirements.txt` as a lock: it has broad lower bounds and
a CUDA-specific extra index. Do not invent exact package versions before resolving
and testing them. The adoption issue should name the approved resolver, Python version,
CPU Torch distribution and update policy.

A first dependency audit should account for NumPy, SciPy, pandas, scikit-learn,
PyTorch CPU, torchvision and pytest. The wider test suite may additionally need
transformers, timm, peft, datasets, accelerate, plotting and runner dependencies.
Inspect imports and collection rather than assuming every training dependency belongs
in the fast gate.

The following are **candidate commands**, to be validated during adoption:

```bash
export CUDA_VISIBLE_DEVICES=''
export WANDB_MODE=offline
export HF_HUB_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export PYTHONDONTWRITEBYTECODE=1

python -c 'import torch, numpy, scipy, pytest; assert not torch.cuda.is_available()'
ruff check nicme utils model scripts tests
python -m pytest --collect-only -q
python -m pytest -p no:cacheprovider -q \
  tests/test_costs.py tests/test_calibration.py tests/test_loss_functions.py
```

After auditing the remaining tests, run the full agreed CPU suite, not just the
three-module seed suite. Missing optional imports currently trigger
`pytest.importorskip` in some modules, including loss/modeling tests. A green result
with a missing required module is insufficient: require dependency imports and inspect
the collected/passed/skipped totals. If an optional module is intentionally excluded,
name the gap and assign it to an explicit later check.

Ruff is configured in NICME's pyproject for E/F/W/I/UP/B with line length 120. Existing
Makefile lint targets scan the entire tree; legacy examples and generated material may
surface unrelated failures. Establish a reviewed active-code scope, inventory the
baseline failures and fix them deliberately. Do not adopt a blanket ignore or shrink
coverage to claim a pass.

Compile checks do not establish functional correctness. CLI `--help` can still execute
top-level imports and initialization; inspect each entry point before running it in
offline CI. The existing `make validate-release` depends on release-view assumptions;
audit those and keep it outside V1 if it requires private ignored release files.

### Suggested hosted job shape

Use the template's pinned checkout and Python setup pattern, `ubuntu-24.04`, a bounded
timeout, `contents: read` and `persist-credentials: false`. Install the resolved CPU
lock, verify required imports, run lint and the agreed CPU suite, then fail on unexpected
repository changes. Keep datasets, model tokens, Hugging Face tokens and W&B tokens
out of the job. The existing portable `agentic-quality` job remains separate.

Require **the observed job names** after a successful real PR run. Configure branch
rules with those names and match `.agentic/config.json`. No placeholder success job,
`continue-on-error`, empty matrix or skipped required test may satisfy acceptance.

## V2 — Targeted integration evidence

Use tiny synthetic images and explicit class maps where possible. Validate preprocessing,
collation, output shapes, a short CPU forward/backward step, checkpoint reload and
one end-to-end metric report. Disable external publication and loggers. Keep the
expected-cost matrix small enough to verify by hand.

When a change affects DINOv3 adapters or accelerator behavior, use a separate
human-authorized smoke run on the appropriate hardware. Record backbone/revision,
adapter targets, dtype, device, driver/runtime, peak memory, config and seed. A CPU
test using a fake backbone cannot establish real accelerator or pretrained-weight
compatibility.

Do not register this workstation as a general public PR runner. Run GPU validation
from an inspected exact commit with a bounded budget and a dedicated output directory.
The agent may prepare the command; existing explicit user authorization governs
whether execution is allowed.

## V3 — Evidence supporting scientific claims

Changes to cost orientation, losses, calibration, split construction, selection rules,
baseline budgets or reported metrics can invalidate comparisons even when V1 passes.
The owner must review the experimental design before launch.

The run plan should state:

- Fixed dataset/input manifests and the independence/grouping unit.
- Train/validation/test roles, with no test-based fitting or selection.
- Stable label order, cost matrix orientation, normalization and decision rule.
- Equivalent data, backbone, compute/tuning budgets and selection protocol across baselines.
- Prespecified seeds, paired comparison design where justified and uncertainty method.
- Failure/restart policy and treatment of incomplete or interrupted runs.
- Supported conclusion, exclusions and any exploratory analyses.

Record exact formulas with the results. Do not conflate nATC, macro conditional cost,
accuracy, recall, calibration error or cost-reduction ratio. State when an undefined
denominator produces an explicit missing value rather than an invented improvement.
Review plots/tables against their source manifests and resist selecting favorable
seeds or thresholds after viewing test outcomes.

## V4 — Release or external reproducibility

Rebuild from a clean clone and locked environment using only the documented inputs
and authorized artifact access. Verify hashes, class maps, configs, commands and
reported tables. Mark the reproduction as exact, tolerance-equivalent, statistically
supported or procedural, according to the evidence actually obtained.

Use NICME's release-view validator only after confirming its current expected files
and exclusions. Preserve the project's existing unresolved license decision; workflow
adoption does not license datasets, pretrained weights or the research repository.

## Provenance and artifact policy

Use the template's `evidence.py` wrapper around the inspected project validation
command. Its manifest records the Git SHA, clean starting state, input/config/environment
hashes, seeds, argv, timestamps, exit status and artifact hashes. Use new per-run output
directories, then attach an immutable evidence link to the PR.

The manifest is a record, not proof that an experiment was well designed or that an
artifact was freshly generated. Reviewer checks must confirm the command's behavior,
the output-to-run association and the scientific interpretation. Keep small manifests
and checksums in Git; place checkpoints, optimizer state, raw images and large outputs
in approved storage with retrieval instructions.

## Proposed adoption acceptance criteria

| Criterion | Required evidence |
| --- | --- |
| No scientific behavior changes in the adoption PR | Diff inspection; existing contract tests with unchanged expectations |
| CPU dependencies are reproducible | Resolved lock and clean hosted installation |
| Required tests actually execute | Collection count, pass/skip report and explicit imports |
| No hidden data/model/network dependency | Offline fixture run and no secret-bearing job |
| Worktree isolation is usable | Pilot task leaves main clean and uses its own import environment |
| Independent review is honest | Fresh Copilot record, exact SHA, known source omissions and no claimed test execution |
| Domain claims remain gated | PR domain section, evidence tier and owner interpretation |
| Cleanup is safe | Merged-state verification and no loss of ignored artifacts or later commits |
