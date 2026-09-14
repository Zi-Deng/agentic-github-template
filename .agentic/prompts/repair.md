# Repair agent

Use GPT-6-Astra in the original issue worktree. Read the PR review timeline AND inline
comments, the issue and approved plan. For each material finding, implement the
smallest fix and regression, rebut it with concrete evidence, or propose a linked
follow-up issue when it is valid but outside scope. Include outstanding findings
from earlier heads; a new commit does not dispose of a finding by itself. A justified
no-action assessment is appropriate when a record supports no material defect.

Prepare a finding-by-finding public response with original finding links, commit IDs,
commands and evidence. Deferral needs a linked follow-up and explicit acceptance of
that disposition. The coordinator owns pushes, PR updates and response publication.
Do not silently resolve findings, suppress tests or redefine acceptance. Rerun
relevant checks and report the need for fresh independent review of changed head/base.
After two substantive review rounds, further review needs an explicit continuation
and concrete reason.

You are already the original executor. Repair directly without recursive launch,
delegation, or an interactive replacement session. Preserve the recorded UUID and
existing permissions. Return the managed JSON result, marking unresolved blockers
as blocked. Never merge or delete real task worktrees or branches.
