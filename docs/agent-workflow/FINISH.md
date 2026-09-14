# Human merge, archival and cleanup

The finish skill prepares the decision and command; the maintainer runs the command.
Merge preparation is not a merge approval. The script checks mechanical preconditions,
but the human still decides whether the reviewed change and domain evidence justify
merging.

## Before running the command

Invoke `$agentic-finish PR #456`. The coordinator reads the designated published
review, all material dispositions, current acceptance evidence and required checks.
It records an explicit assessment tied to the repository, PR, reviewed head/base and
review record. Local records cannot prove human approval or the semantic correctness
of a model's assessment.

Preparation must reject a stale or altered review, wrong repository, changed contract,
missing evidence assessment, unresolved material blockers, failed/missing required
checks or conflicting/unknown mergeability. It inventories artifacts and reports
tracked changes or unexpected non-ignored files. A qualifying PR can be marked ready,
and the coordinator supplies the exact `scripts/finish-task.sh` command with its
recorded inputs. Copy that command rather than obtaining a new head SHA and assuming
it was reviewed.

## What the human-run script does

1. Recheck the PR and the designated review against current GitHub state.
2. Request a squash merge pinned with `--match-head-commit`, without administrative
   bypass or the combined `--delete-branch` option.
3. Re-query GitHub after success or error. A queued or otherwise unmerged PR retains
   its worktree and branches. A nonzero CLI exit does not prove the merge failed.
4. Once merge is confirmed, preserve ignored worktree artifacts in a private archive.
5. Re-run the existing identity, registration, exact-tip and cleanliness guards, remove
   the verified worktree and delete its matching local task branch.
6. Remove the remote task branch only if its current tip still matches the merged PR
   head. An already-deleted branch is harmless; an advanced branch is preserved.

`--match-head-commit` protects the head. It does not atomically freeze the base branch;
GitHub's current rules and required checks remain authoritative for merge eligibility.
A merge queue may defer the merge. See [GitHub CLI merge semantics](https://cli.github.com/manual/gh_pr_merge).

The remote deletion uses an explicit expected-SHA lease, so a background fetch cannot
silently change which tip is considered safe to delete. See [Git's lease semantics](https://git-scm.com/docs/git-push).

## How ignored artifacts are preserved

Ordinary `git status` can hide virtual environments, caches, private notes, checkpoints
and experiment outputs. The finishing script archives ignored entries under a unique
directory below the control checkout's `.agentic-local/archives`. It keeps their
relative paths and records a persistent operation journal. Archive records and contents
are private and have no automatic expiry.

Symlinks are preserved as links; their targets are not followed or copied. Same-filesystem
renames avoid duplicating large artifacts. Cross-filesystem transfers copy and verify
content before source removal. Unsupported file types, changed inputs, destination
conflicts or failed verification stop cleanup and retain recovery information.

Archival is for ignored artifacts. Tracked modifications and unexpected non-ignored
files still block cleanup. The script does not turn arbitrary user edits into disposable
outputs. It never uses blanket `git clean`, a forced worktree removal, or an unbounded
branch deletion.

An archived virtual environment preserves files but may contain absolute paths to its
original location; recreating the environment can be more appropriate than running it
from the archive. Keep experiment configuration, package locks and evidence manifests
with research artifacts so that archival supports reproducibility.

## Interrupted or partial completion

Read the reported state before retrying:

| State | Next action |
| --- | --- |
| PR unmerged or queued | Wait for or resolve GitHub requirements; rerun the supplied command when appropriate. No cleanup has occurred. |
| PR merged, archival incomplete | Inspect the reported journal and retained source/destination entries. Rerun the same command to reconcile the interrupted archive; do not overwrite either side manually. |
| PR merged, worktree changed | Preserve and inspect the changes. Reconcile them explicitly before cleanup; the script does not discard new work. |
| Local cleanup complete, remote branch advanced | Preserve the advanced remote branch and investigate its new commits. Do not replace the expected SHA to force deletion. |
| Cleanup complete | Retain the final report and archive location. Any later archive deletion is a separate human action. |

Close the executor and stop task-specific writers before finishing. Local operation
locks coordinate these helpers; they cannot stop unrelated programs from modifying
files. Concurrent writes observed during inventory or verification stop the operation.

The low-level `cleanup-task` helper retains its original refusal to delete ignored
files. The human finishing script archives them first and calls that guarded cleanup
only after the task worktree meets its preconditions. The human owns any decision to
abandon an unmerged PR; abandonment is not successful finishing.
