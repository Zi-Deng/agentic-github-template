# Reviewer providers: selection, binaries, native login, budgets and activation

Independent review runs through one of two pinned provider binaries, chosen by the active
profile in `.agentic/config.json`: the Copilot CLI (`copilot`, archive 1.0.83) or the
native Claude Code binary (`claude-code`, signed release 2.1.282, included Max billing
only). Both receive the same immutable packet and are held to the same
[coverage contract](COVERAGE.md). This guide covers what each provider needs before a
review can run, what the wrapper enforces around the model process, and what every
blocker reported by `profile show` means. Procedure for a review itself is in
[REVIEW.md](REVIEW.md).

## Select and inspect

```bash
python3 scripts/agentic/workflow.py profile show
AGENTIC_PROFILE=astra-claude python3 scripts/agentic/workflow.py profile show
python3 scripts/agentic/workflow.py profile use astra-claude --reason "Max-plan review"
python3 scripts/agentic/review.py prepare 456 --issue 123 --plan-comment 987654321 \
  --review-provider claude-code --review-model claude-opus-5-5 --review-effort medium
```

`profile show` prints the resolved policy (provider, exact model, effort, CLI pin,
adapter, billing mode, typed budget), the login root in force and its source, and
`activation_blockers`. Per-call `--review-*` flags override one round and are recorded in
the packet as `overrides`; naming only the provider resets model and effort to that
provider's defaults. Models come from the closed catalog in
`scripts/agentic/review_policy.py` (provider spellings differ and are never translated)
or from a `review_model_extensions` declaration in trusted configuration. A reviewer from
the implementer's model family is refused unless recorded with `--allow-same-family`.

| Blocker | Meaning | Clears when |
| --- | --- | --- |
| `verified_pinned_cli_unavailable` | No registered binary for the provider | `register-reviewer` succeeds |
| `guarded_native_login_and_current_account_bound_receipt_required` | No dedicated Max registration, or its token or 7-day receipt lapsed | `claude-login-setup` (or `--renew`) completes in a real terminal |
| `managed_controls_require_verification` | `/etc/claude-code/managed-*` exists on this machine | A human inspects and removes or accepts the endpoint policy; the wrapper never runs under it |
| `matching_native_capability_diagnostic_unavailable` | Both activation diagnostics have not qualified under the current binding | `diagnose-claude` qualifies both purposes |

## Register binaries

Only Linux x64 glibc is supported. Installation is explicit; a review never upgrades a CLI.

```bash
python3 scripts/agentic/install_tool.py claude-code --directory /absolute/staging/claude-2.1.282
python3 scripts/agentic/workflow.py register-reviewer claude-code \
  --binary /absolute/staging/claude-2.1.282/claude --proof-directory /absolute/staging/claude-2.1.282
python3 scripts/agentic/install_tool.py copilot --directory /absolute/staging/copilot-1.0.83
python3 scripts/agentic/workflow.py register-reviewer copilot \
  --binary /absolute/staging/copilot-1.0.83/copilot --proof-directory /absolute/staging/copilot-1.0.83
```

Registration copies verified files into a versioned private bundle under
`.agentic-local/provider-clis/` of the control checkout; execution uses that absolute
path and revalidates the proof before every launch. Claude verification pins the release
manifest and binary SHA-256, checks version and platform, and verifies the detached GPG
signature in a temporary keyring against fingerprint
`31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE`
([official procedure](https://code.claude.com/docs/en/setup#verify-the-manifest-signature)).
Copilot verification keeps its archive digest and compares the executable with the
archive member. The locally installed `claude` on `PATH` is never used for review.

## Dedicated native login and the per-machine override

The native reviewer uses its own login store, never the workstation's ordinary Claude
configuration. The store lives outside every Git checkout, mode 0700, owned by the
operator, and holds one registration (account identity, pinned CLI, lineage of
generations), a 7-day paid-usage receipt and, per generation, the access-only projection
of the credentials. Refresh tokens, history and customization are never copied into a
review snapshot; the account must be a genuine Max subscription with extra usage disabled.

```bash
python3 -B scripts/agentic/workflow.py claude-login-setup --paid-usage-disabled
python3 -B scripts/agentic/workflow.py claude-login-setup --renew --paid-usage-disabled
```

Run these yourself in a private real terminal; an agent must not run the browser login in
a tool terminal or capture its output. Setup launches the registered binary with
`--safe-mode --restricted --setting-sources '' auth login --claudeai`, then checks the Max
metadata, account, token lifetime, billing assertion and endpoint controls before writing
the registration. Access tokens last about eight hours; `--renew` records a new
generation of the same registration (the account must match) and keeps earlier
generations in the lineage, so qualified diagnostics carry forward without being repeated.
Renew right before a long review: the wrapper refuses a run whose token would expire
before `timeout + 6 minutes` at binding, at activation verification and at the snapshot
taken just before launch. Under the full 7200 s exception that means more than about 2.1 h
of remaining token lifetime, so renew immediately before such a request.

The store is resolved per machine, in this order:

1. the ignored local file `.agentic-local/claude-login-root.json` in the control checkout
   (`workflow.py claude-login-root show|use PATH --reason TEXT|clear`);
2. the profile's `reviewer.login_root`;
3. the top-level `claude_review_login_root`;
4. the default `~/.config/agentic-workflow/claude-review-login`.

The committed configuration never names a login path (`check_repository.py` refuses one);
use the local override to point several checkouts or a second machine at one dedicated
store. Managed executors cannot change the profile, the override or the store.

## Isolation boundary of the model process

Every Claude review runs the registered binary with a fresh temporary `HOME` and
`CLAUDE_CONFIG_DIR`, a minimal fixed environment (no `ANTHROPIC_API_KEY`, no Bedrock or
proxy variables, retries and model fallback disabled), an explicit settings file
(`disableAllHooks`, no fallback model, no model switching, empty MCP configuration) and
these flags: `-p --safe-mode --restricted --tools Read,Grep,Glob --allowedTools
Read,Grep,Glob --permission-mode dontAsk --permission-prompts none --setting-sources ""
--strict-mcp-config --no-session-persistence --disable-slash-commands --output-format
stream-json --model M --effort E --session-id UUID --max-budget-usd B`. Preflight checks
the exact `--version` output and that `--help` declares every control.

The telemetry adapter (`claude-stream-json-2.1.282-v6`) enforces the stream's `system/init`
event: the requested model, exactly the three read-only tools, empty MCP, plugin, skill,
agent and slash-command catalogs, `dontAsk`, the packet workspace as cwd and the pinned
version. Source credit comes only from Read, Grep and Glob results that match the packet's
own line inventory; assistant text, listings and percentages earn nothing. Delegated or
MCP events, hooks, unexpected system events, permission denials and any trace of the
outside-workspace canary mark the run INCOMPLETE. Raw provider text, account data and
paths are never retained in diagnostics; bounded shapes and hashes are.

## Budgets and the per-call exception

Defaults are 900 s and $10 reference cost per Claude request (`extra_spend_authorized_usd`
is always 0; the Max subscription's included usage is the only billing mode, and
`autoContinueAtUsageLimit` is off so a usage limit ends the run INCOMPLETE rather than
spending). Profiles may lower these; nothing in configuration can raise them.

One request may exceed the defaults only through a recorded exception passed on the
command line, all three flags together, on `review.py prepare` or `workflow.py task-review`:

```bash
AGENTIC_PROFILE=astra-claude python3 scripts/agentic/workflow.py task-review 123 --execute --publish \
  --review-timeout-seconds 7200 --review-max-estimated-usd 60 \
  --review-exception-reason "Single native review of the whole update, maintainer approval 2026-10-07"
```

Ceilings are 7200 s and $60. The record (`reason`, the defaults it raised from,
`recorded_at`) sits inside the policy `budget`, so every round and packet digest covers
it; the published header carries a `Budget exception:` line; `merge-preflight` echoes it.
Copilot policies, activation diagnostics and the hosted workflow never accept one.

## Activation diagnostics and the ledger

Before an ordinary native review, two narrow diagnostics must qualify under the exact
binding in force, each a real request under a fixed 300 s / $2 budget:

```bash
AGENTIC_PROFILE=astra-claude python3 scripts/agentic/workflow.py diagnose-claude
AGENTIC_PROFILE=astra-claude python3 scripts/agentic/workflow.py diagnose-claude
```

The first run performs `native-tools-and-source` (Read, one exact Grep, Glob credit the
diagnostic packet's own lines), the second `isolation-refusal` (a Read outside the
workspace is refused and earns nothing). Exit status 2 means the attempt is recorded but
INCOMPLETE; a failed preflight spends nothing. Repeating a purpose needs
`--purpose … --reason …`. Attempts, packets, exact report bytes, captures and assessments
live in `.agentic-local/claude-activation/` and are re-verified at every review.

The binding digest covers the repository, provider, CLI pin, adapter, billing mode, model,
effort, diagnostic budget, tool contract and the login registration. Changing any of them
(a model or effort switch, a pin bump, a new registration) invalidates activation until
both purposes qualify again. The review budget, an exception and the login generation do
not; a renewed generation is accepted through the registration's lineage.

## Failure semantics

A review ends in one of three states: qualified, INCOMPLETE (published with its reasons,
never ready), or refused before inference (a blocker above, a changed executable, a
changed packet, a lapsed token). The wrapper never retries a request, never resumes a
provider session, never substitutes a model and never raises a budget on its own. Recovery
of a durably saved result is storage-only. [FINISH.md](FINISH.md#when-the-review-is-incomplete)
lists the maintainer's options after an INCOMPLETE round.

## Pin-bump re-audit checklist

Moving the Claude pin from 2.1.282 is an implementation change, not configuration:

1. Verify the new release's manifest signature and record the binary and manifest digests
   in `review_policy.PROVIDERS` together with a new adapter identifier.
2. Re-check the `--help` controls, the `--version` string and the settings keys the
   trusted settings rely on against the new binary.
3. Re-extract the native Read, Grep and status fixtures (`tests/agentic/fixtures/claude-*`)
   and update the renderer expectations the telemetry tests pin.
4. Update `claude_native_auth` if the credential or configuration file layout changed;
   re-run `claude-login-setup --renew`.
5. Run both activation diagnostics again; the digest change makes that mandatory.
6. Record the audit in [RESEARCH.md](RESEARCH.md) and [VERIFICATION.md](VERIFICATION.md).
