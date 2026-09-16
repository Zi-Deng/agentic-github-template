# AI2S skills intake and member profiles

This opt-in example implements the contract in [issue #7](https://github.com/Zi-Deng/agentic-github-template/issues/7)
and its [approved plan](https://github.com/Zi-Deng/agentic-github-template/issues/7#issuecomment-5702482761).
It is excluded from the portable workflow installer.

**Status: source prepared and locally tested; institutional validation pending.**
The example includes Apps Script setup, Google service adapters, account binding,
recovery, permissions, revision-checked tab updates, triggers and operating guides.
Independent review, hosted CI and the institutional owner must establish readiness
before wider use. No institutional assets or live responses were used in local tests.

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
| [Deployment](DEPLOYMENT.md) | Required institutional inputs, installation, authorization and pilot publishing |
| [Maintenance](MAINTENANCE.md) | Validation, pause, reconciliation, recovery and rollback |
| [Live validation record](LIVE-VALIDATION.md) | Pending owner-observed acceptance and timed pilot |
| [Configuration example](config.example.json) and [manifest](appsscript.json) | Blank identities and explicit OAuth/runtime configuration |
| [Integration tests](tests/pipeline.test.cjs) | Real entrypoints/adapters against a synthetic API model |
| [API contract tests](tests/contracts.test.cjs) | IDs, permissions, revisions, pagination, literal values and error boundaries |

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

Local tests establish software behavior against synthetic inputs and an in-memory
Google API model. They include identity binding, current-response reads, retries,
uncertain creation, concurrency, permissions and preservation of Member notes.
Institutional validation must observe a real-browser submission, creation and update
of the same profile URL, preserved notes and formatting, and actual access boundaries.

The institutional owner, coordinator identities, destination folders and team readers
are still unsupplied. Keep them and all live responses outside Git. A user-arranged
pilot must include at least one nontechnical and two differently technical members,
record core completion times and clarity feedback, and target a median at most seven
minutes. Timing and institutional access are pending, not implied by the fixtures.

## Implementation boundaries

`App.gs` owns locked operations and deployment checks. `Google.gs` makes allowlisted
Google REST calls and writes operational Sheet cells with `valueInputOption=RAW`.
`FormSchema.gs` persists stable item/question IDs and maps responses without titles or
column positions. `Pipeline.gs` binds response ownership, rereads current responses,
fingerprints content, and advances a persistent member register. `Recovery.gs` journals
creation intent before requests. `Documents.gs` builds explicit tab-targeted requests
with required revisions. The source has no external LLM, mail sender, ranking or score.

The owner-managed My Drive destination and its ancestors must be restricted; Shared
Drives are rejected by this example. The script does not use an automatically linked
Forms response sheet: it maintains its own restricted Responses and Register tabs,
so respondent text is always written as literal values. See deployment and maintenance
for quotas, state backups, access assumptions and recovery limits.
