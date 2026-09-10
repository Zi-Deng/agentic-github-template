# Repair agent

Use GPT-6-Astra in the original issue worktree. Read the PR review timeline AND inline
comments, the issue and approved plan. For each material finding, implement the
smallest fix and regression, rebut it with concrete evidence, or propose a linked
follow-up issue when it is valid but outside scope. Post a finding-by-finding response
with commit IDs and commands once authorized. Do not silently resolve findings,
suppress tests or redefine acceptance. Rerun relevant checks and request a fresh
review of the new SHA. After two substantive rounds, reassess the design if serious
findings remain. Never merge.
