# AI2S skills intake and member profiles

This opt-in example implements the contract in [issue #7](https://github.com/Zi-Deng/agentic-github-template/issues/7)
and its [approved plan](https://github.com/Zi-Deng/agentic-github-template/issues/7#issuecomment-5702482761).
It is excluded from the portable workflow installer.

**Status: first implementation checkpoint; not deployable yet.** The questionnaire,
profile mapping, synthetic fixtures, and local test gate are implemented. Google
asset setup, account verification, processing/recovery, permissions, tab updates,
triggers, manifest/configuration, and deployment/maintenance guides remain to be
implemented after the early draft PR is published. No institutional assets or live
responses have been used.

The questionnaire is designed for a 5–7 minute core without requiring AI experience.
Optional sections accept additional professional strengths and technical details.
The deterministic profile mapper preserves the respondent's claims, examples,
recency, learning interests and collaboration preferences. It neither assigns an
expertise score nor interprets an unchecked option as a lack of skill.

| Artifact | Purpose |
| --- | --- |
| [Questionnaire specification](QUESTIONNAIRE.md) | Core, optional routes and complete option sets |
| [Questionnaire source](src/Questionnaire.gs) | Shared questionnaire data with stable logical keys |
| [Profile specification](PROFILE-TEMPLATE.md) | Native two-tab layout and preservation contract |
| [Profile mapper](src/Profile.gs) | Pure conversion of logical answers to styled paragraphs |
| [Synthetic members](fixtures/members.json) | Accountant, CV, NLP, MLOps and unlisted-specialty cases |
| [Tests](tests/profile.test.cjs) | Mapping, omitted fields, edited answers, routing and data boundaries |

## Development

Install Node.js 24 LTS, then run from the repository root:

```bash
make test-ai2s
make check
git diff --check
# After committing the intended changes:
make check-clean
```

`make check` also needs the repository's Python development environment described in
the [root README](../../README.md). `NODE=/absolute/path/to/node` selects an existing
Node.js 24 installation. No npm install, package tree, Google credentials or network
access is needed to run the JavaScript tests. The tests load the actual `.gs` source
into an isolated JavaScript context, without Google services.

The repository `quality` job installs Node.js 24 with
[actions/setup-node v7.0.0](https://github.com/actions/setup-node/releases/tag/v7.0.0),
pinned to [820762786026740c76f36085b0efc47a31fe5020](https://github.com/actions/setup-node/commit/820762786026740c76f36085b0efc47a31fe5020).
The release-to-commit association was checked on 2026-09-16. Permissions remain
`contents: read`; automatic package-manager caching is disabled.

## Evidence still required

Local tests establish only deterministic software behavior. The remainder of the
implementation must test identity binding, stale-event handling, retries, uncertain
creation, concurrency, permissions and preservation of Member notes. Institutional
validation must then observe a real-browser submission, creation and update of the
same profile URL, preserved notes and formatting, and actual access boundaries.

The institutional owner, coordinator identities, destination folders and team readers
are still unsupplied. Keep them and all live responses outside Git. A user-arranged
pilot must include at least one nontechnical and two differently technical members,
record core completion times and clarity feedback, and target a median at most seven
minutes. Timing and institutional access are pending, not implied by the fixtures.
