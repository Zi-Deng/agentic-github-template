# Research record and implementation decisions

Checked on **2026-09-10**. This is a technical implementation audit, not a claim that
independent model review guarantees correctness. The supplied guide predates this
audit; its product-status statements are background, not unquestioned requirements.

## Method

Read the complete local PDF and TeX, recover the specifically requested earlier chat,
inventory the two named repositories without editing them, consult official product
documentation, inspect installed CLI help, and test the resulting implementation.
Local Git tests deliberately mock GitHub/model responses, and those limits are recorded
separately from live tests. A limited NICME CPU baseline runs in a temporary copy.

No broad web search result, secondary blog, recalled future feature or unavailable
account capability is treated as sufficient implementation evidence. Versions and
API behavior may change after this date; a maintained template needs periodic checks.

## Primary sources and decisions

| Source | Decision supported |
| --- | --- |
| [Git worktrees](https://git-scm.com/docs/git-worktree) | Separate task directories share repository data; use registered worktrees and detached review/test checkouts deliberately |
| [GitHub Flow](https://docs.github.com/en/get-started/using-github/github-flow) | Short branches, PRs, checks, review and merge form the stable collaboration layer |
| [Codex CLI reference](https://developers.openai.com/codex/cli/reference) | Explicit model, directory, sandbox and approval options for role launches |
| [Astra workspace availability](https://learn.chatgpt.com/docs/enterprise/workspace-model-availability#gpt-6-astra-in-enterprise) | Model configuration does not grant account access; verify the client and account used |
| [Copilot CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference) | Explicit model selection, allowed tools, no custom instructions, fresh prompt mode and authentication |
| [Copilot custom agent configuration](https://docs.github.com/en/copilot/reference/custom-agents-configuration) | A custom reviewer can expose read/search tools without edit, execution or delegation |
| [Copilot configuration directory](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference) | Isolated configuration/state and hook settings rather than inherited personal agent setup |
| [Copilot-supported models](https://docs.github.com/en/copilot/reference/ai-models/supported-models) | Claude Sonnet 5 is listed; access remains plan/client dependent |
| [Native Copilot code review](https://docs.github.com/en/copilot/concepts/agents/code-review) | Native review chooses its model mix; it is distinct from a selectable Claude CLI review |
| [Copilot CLI in Actions](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/automate-with-actions) | Fine-grained Copilot Requests token path for manual hosted review |
| [Copilot Actions eligibility](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/copilot-cli-in-github-actions) | Built-in-token eligibility is a separate organization/account policy question |
| [PR review REST API](https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request) | COMMENT reviews can bind `commit_id`; publication need not imply approval |
| [GitHub CLI merge](https://cli.github.com/manual/gh_pr_merge) | Use `--match-head-commit`; inspect state after an ambiguous failure |
| [Ruleset REST API](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset) | Explicit PR parameters and required checks, including integration binding |
| [Secure Actions use](https://docs.github.com/en/actions/reference/security/secure-use) | Minimum token permissions, trusted control code, pinned actions and cautious handling of PR input |
| [Repository templates](https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-repository-from-a-template) | New-project file generation is separate from configuring repository governance |
| [Large files](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github) | NICME's very large staged artifacts need a separate storage/index plan |

## Versions inspected

| Component | Observed/pinned version | Evidence |
| --- | --- | --- |
| Codex | 0.146.0 installed | Local `--help`, `--version` and model catalog contain the chosen capabilities and `gpt-6-astra` |
| GitHub CLI | 2.100.0 | [Official release](https://github.com/cli/cli/releases/tag/v2.100.0); Linux archive digest checked before install |
| Copilot CLI | 1.0.83 | [Official release](https://github.com/github/copilot-cli/releases/tag/v1.0.83); digest checked; actual local flags inspected |
| actions/checkout | 7.0.1 | [Official release](https://github.com/actions/checkout/releases/tag/v7.0.1); full SHA pinned |
| actions/setup-python | 7.0.0 | [Official release](https://github.com/actions/setup-python/releases/tag/v7.0.0); full SHA pinned |
| actions/upload-artifact | 7.0.1 | [Official release](https://github.com/actions/upload-artifact/releases/tag/v7.0.1); full SHA pinned |
| actions/download-artifact | 8.0.1 | [Official release](https://github.com/actions/download-artifact/releases/tag/v8.0.1); full SHA pinned |
| Ruff | 0.16.7 | PyPI package metadata checked; dev dependency pinned |
| PyYAML | 6.0.3 | PyPI package metadata checked; dev dependency pinned |
| actionlint | 1.7.12 | [Official release](https://github.com/rhysd/actionlint/releases/tag/v1.7.12); Linux archive digest checked before local validation |

The installed Codex version is not presented as the latest release. The implementation
uses flags verified in that installed version and does not depend on newer managed
worktree previews or a provider SDK. Package pins are reproducibility choices, not
claims of permanent support or freedom from vulnerabilities.

## Model and cost policy

The user's recovered choices start with Astra for every non-review role. The template
does not implement automatic low-risk routing to another OpenAI model. If measured
cost later warrants a change, compare task completion, repair rounds, false findings
and total spend before routing mechanical drafting or summaries to a cheaper model.

Claude through Copilot is the independent reviewer. Selecting a different provider
may offer useful diversity, but the fresh role, independent evidence and clear rubric
matter more than provider identity alone. No ranking, accuracy improvement or dollar
estimate is asserted without a measured basis. The selected CLI model is recorded as
requested; provider-side identity is not cryptographically attested by this harness.

## Deliberate exclusions

The baseline does not depend on GitHub Agentic Workflows, native AI approval previews,
stacked PR previews, automatic issue triage, autonomous repairs, merge queues or GPU
Actions runners. These can be added after observing real project use. No claim is
made that those products are unavailable; they are simply unnecessary dependencies
for the requested initial process.

The full implementation is traceable in [TRACEABILITY.md](TRACEABILITY.md). Recorded
results and remaining external checks live in [VERIFICATION.md](VERIFICATION.md).
