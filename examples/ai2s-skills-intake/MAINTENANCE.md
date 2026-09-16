# Operations, recovery and rollback

Use the same institutional owner account and Apps Script project. Never clear
`AI2S_STATE`, delete processing-register rows, detach assets, or create another project
as a troubleshooting shortcut. The journals hold the identity of existing profiles.

## Entry points

| Function | Behavior |
| --- | --- |
| `ai2sSetup` | Create/recover deployment assets; reuse saved IDs; leave processing paused |
| `ai2sStatus` | Log only deployment asset IDs and paused/setup status |
| `ai2sValidate` | Check owner, folders/ACLs, real Verified mode, questionnaire IDs, template tabs, publication/responders and recorded triggers when active |
| `ai2sInstallTriggers` | While paused, validate, install/recover one submission trigger and one 15-minute trigger, then enable processing |
| `ai2sReconcile` | Reread responses and retry processing, rotating through at most 100 per run with a four-minute work budget |
| `ai2sRetry` | Same behavior as reconciliation; it cannot bypass pause or identity conflicts |
| `ai2sPause` | Persist pause first, then remove this owner's two processing handlers; preserve all assets, responses and unrelated triggers |
| `ai2sRecover` | Apply one explicit recovery request from `AI2S_RECOVERY`, while paused |
| `ai2sUpdateAccess` | While paused, verify the owner's manually added reader/responder access and persist the new lists without Google writes |
| `ai2sOnSubmit` | Installed Forms event handler; do not call it manually to claim a real trigger test |

Pausing stops processing, **not response collection**. The owner may separately stop
accepting responses in Forms. Updates made while paused remain in Forms and are read
after processing resumes. Another account's triggers are not visible to the owner;
no other account should install them. Effective-owner checks reject those executions.

The Forms service handles setup/settings and the submission event. Current response
reads use the Forms API's stable question IDs and `lastSubmittedTime`, ignoring event
answer contents. Verified email is normalized by trimming and lowercasing; aliases are
not merged. [Google's response fields](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms.responses)

`Responses` stores each latest accepted response as literal JSON, keyed by response ID.
`Register` stores `response:ID` ownership/blocked status and `member:EMAIL` profile IDs,
tab IDs, creation token, fingerprint, phase and safe error code. These are coordinator
data, never team-readable. Do not edit these tabs by hand, sort partial column ranges,
add formulas, delete keys, or change their names. Use filters or a separate private
read-only copy for inspection. The script handles literal writes and detects duplicate
keys/corrupt state; it cannot reconstruct deliberately deleted ownership history.

## Routine checks

After deployment and periodically during the pilot, run validation and inspect the
Apps Script Executions view and private Register. Pending/blocked rows are unfinished
work, even if a trigger invocation itself returned normally. The script returns safe
status codes and logs them without response contents or provider error bodies. Expected
errors are caught deliberately; execution “Completed” alone is not processing success.

Check group membership with its institutional administrator, and test permissions as
actual users. API checks cannot attest who belongs to a configured group. Team readers
should see profiles and both tabs but no raw responses; each member should edit only
their own document unless they separately hold coordinator privileges. Do not disable
`writersCanShare=false` on managed files. A later unexpected grant causes processing
to stop; validation does not silently remove it.

Preserve private backups of `AI2S_CONFIG`, `AI2S_STATE`, the spreadsheet and the reviewed
source before operational changes. Store backups with the same restricted access.
Asset IDs and journals must remain together. Set retention and member-departure access
decisions with the institutional owner; the script does not automatically delete data
or infer that a missing response means its profile should be deleted.

## Error disposition

| Code | Owner/coordinator action |
| --- | --- |
| `CONFIG_REQUIRED`, `INVALID_EMAIL`, `CONFIG_DUPLICATE` | Check all private configuration values, primary account emails and explicit principal lists |
| `OWNER_REQUIRED`, `ASSET_OWNERSHIP` | Use the designated owner; inspect ownership/Shared Drive placement, preserving assets |
| `CONFIG_CHANGED` | Restore original `AI2S_CONFIG`; use the paused access-update procedure for reader/responder additions. Other changes need a deliberate migration; pause remains available |
| `ACCESS_UPDATE_REQUIRED`, `ACCESS_REMOVAL_UNSUPPORTED` | Supply exactly the two access lists, retaining every current principal. This operation does not change ownership, destinations, coordinators or remove access |
| `UNEXPECTED_ACCESS`, `MISSING_ACCESS`, `SHARING_ENABLED`, `RESPONDER_ACCESS`, `UNEXPECTED_PARENT` | Inspect actual asset and ancestor ACLs/placement; restore only approved access or location; then validate |
| `EMAIL_NOT_VERIFIED`, `FORM_SETTINGS`, `AUTO_SHEET_LINKED`, `FORM_NOT_PUBLISHED` | Restore documented Form settings/access; if email verification was disabled while accepting responses, pause and investigate those submissions before processing |
| `FORM_DRIFT`, `ANSWER_DRIFT`, `REQUIRED_ANSWER_MISSING` | Inspect stable item IDs, questionnaire and current response; do not remap by matching titles |
| `IDENTITY_MISMATCH`, `UNBOUND_EDITED_RESPONSE` | Investigate original account ownership and use the explicit resolution below |
| `DUPLICATE_MEMBER_RESPONSE` | Retain both records and the original profile; investigate duplicate accounts/responses. There is no implicit merge or rebind operation |
| `CREATION_UNCERTAIN`, `CREATION_AMBIGUOUS` | Reconcile the recorded operation before any new creation; see below |
| `ASSET_UNAVAILABLE`, `TABS_CHANGED`, `DOCUMENT_STRUCTURE` | Inspect/restore the same asset or tabs from owner-controlled history; never replace its ID automatically |
| `RESPONSE_CHANGED` | Response changed during processing; next reconciliation reads the new content |
| `REQUEST_REJECTED`, `REMOTE_CONFLICT` | A Docs/Form revision may have changed; retry after fresh reads. Repeated failure needs source/API inspection, not removal of revision checks |
| `QUOTA_RETRY`, `REMOTE_RETRY`, `REMOTE_FAILURE`, `ACCESS_DENIED` | Check quotas, service/API enablement and authorization. Retry reads/updates later; uncertain creates still follow their journal |
| `REGISTER_DUPLICATE`, `REGISTER_CORRUPT`, `REGISTER_MISSING`, `STATE_CORRUPT`, `STATE_WRITE_FAILED`, `STATE_TOO_LARGE` | Pause, preserve evidence and recover the authoritative private state from a verified backup |
| `RECORD_TOO_LARGE`, `UNSUPPORTED_TEXT` | Ask the member to shorten the intake or replace unsupported control/private-use characters. Do not truncate claims; durable additional context can go in Member notes |
| `TRIGGER_DRIFT` | Pause, inspect the owner's triggers, then reinstall the recorded handlers through the provided function |

## Uncertain creation

Creation intent is saved before a request. New profiles receive an operation token in
Drive `appProperties` as part of the copy; setup assets initially use a unique opaque
title. On an uncertain return, the next run searches for that marker. One match is
reused; multiple matches stop as ambiguous; zero matches remain uncertain. Index lag
or an in-flight request is not proof of absence. Google does not generally support
pre-generated IDs for creating native Workspace files, so this example uses journals
instead of assuming native create calls are idempotent.
[Drive creation behavior](https://developers.google.com/workspace/drive/api/guides/create-file)

First retry lookup later without modifying the journal. If no result appears, pause
and have the owner investigate the execution, Drive trash and marker search. Preserve
any discovered assets. Only after the owner determines that the original request
cannot still complete and that no asset exists, set `AI2S_RECOVERY` to private JSON:

```json
{
  "action": "confirmAbsent",
  "asset": "profile",
  "memberEmail": "OWNER_SUPPLIED_ORIGINAL_MEMBER_EMAIL",
  "token": "EXACT_TOKEN_FROM_REGISTER",
  "confirmedAbsent": true
}
```

For setup assets, `asset` is `sheet`, `form`, `template` or `folder`, and the token
comes from `AI2S_STATE`; omit `memberEmail`. Run `ai2sRecover`. It checks pause, token,
state and a fresh search, records the explicit absence decision, and leaves processing
paused. Run `ai2sSetup` for unfinished setup, or `ai2sInstallTriggers` then `ai2sRetry`
for a profile. `CREATION_FOUND` means lookup found an asset: do not confirm absence;
resume the normal lookup. Ambiguous matches require an owner investigation, not
automatic deletion or arbitrary selection.

## Account conflict

A stored response/account binding is sticky. An edited response encountered for the
first time has no observable original account history, so it is blocked before a
profile is created. Current Verified mode proves the current account, not its past
identity. For a known binding, only its original email can be restored; the recovery
function does not transfer the profile to a new person.

Pause. Investigate with the owner and member, record the evidence privately, and have
the original account restore the response if appropriate. Set `AI2S_RECOVERY`:

```json
{
  "action": "resolveIdentity",
  "responseId": "ACTUAL_RESPONSE_ID",
  "originalEmail": "OWNER_VERIFIED_ORIGINAL_ACCOUNT"
}
```

Run `ai2sRecover`, which requires the latest response email to equal the confirmed
original account and refuses changing any existing binding. It records resolution
time. Then install triggers and retry. If original ownership cannot be established,
leave it blocked; no automated inference or profile reassignment is supported.

## Limits and rollback

Google quotas, network failures and concurrent edits can leave work pending. A script
lock serializes this project's processing; it cannot lock a human editing a Form or
Doc. Required Docs revisions prevent overwriting a newer document revision. Repeated
response reads prevent delayed events from replaying their old answers, but Google
Forms/Docs/Drive/Sheets provide no transaction spanning all four services: a new edit
can arrive immediately after a check and will be reconciled on a later run.
[Docs write control](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate#WriteControl)

This small-intake implementation scans response IDs and register rows, bounds each
reconciliation to 100 responses/four minutes, and keeps a rotating cursor. Large
deployments need measured quota/capacity work before rollout. A serialized Sheet record
above 45,000 characters is held pending rather than truncated. The setup property has
an 8,500-byte guard. These are explicit operational limits, not expertise judgments.

To roll back, run `ai2sPause` and verify its paused state. Preserve forms, responses,
documents and journals. Restore all nine source files and the manifest from the
preceding reviewed version after inspecting state/schema compatibility; time-driven
triggers run saved project code, so restoring only a deployment label is insufficient.
Validate while paused, then reinstall triggers if the owner approves resuming that
reviewed version. Content mapping changes require an explicit version/fingerprint
migration if existing profiles need regeneration. Do not restore an old register over
new live profiles or migrate responses implicitly.

## Pilot-to-team access update

Use this after pilot acceptance when adding explicit users or new group addresses.
Changing membership of the same institutional groups does not change the script's
configuration; pause, arrange the membership change, validate actual users' access,
and resume. For additional addresses, the owner performs these steps:

1. Run `ai2sPause`. Back up `AI2S_CONFIG`, `AI2S_STATE` and the restricted spreadsheet.
   Keep the original `AI2S_CONFIG` unchanged. Read the current lists from `AI2S_STATE.access`
   if present, otherwise from `AI2S_CONFIG`. Decide the additions explicitly.
2. Save a Script Property **`AI2S_ACCESS_UPDATE`** containing exactly `teamReaders` and
   `responders`, each a full nonempty array of `{ "type": "user" or "group", "email":
   "owner-supplied address" }`. Retain every principal from the current lists and add
   the approved users/groups. These have the same shape as the corresponding fields
   in [config.example.json](config.example.json); no other fields are accepted.
3. In Drive, add the new `teamReaders` to the **profiles folder** as Readers, with
   notifications off. Allow those grants to propagate to existing profiles. In the
   Form's responder sharing controls, add the new `responders` as Responders, with
   invitations off. Do not change Form editor access, raw-data/template/private-root
   permissions, or share unfinished profiles in the private root. Manual grants take
   effect immediately, so perform this only after authorizing the recipients.
4. Run **`ai2sUpdateAccess`**. It checks the effective owner, paused state, unchanged
   private assets, actual responder/folder ACLs and every registered profile's access.
   It issues only Google reads. Missing, broader or unexpected access blocks adoption;
   resolve the discrepancy and rerun with the same request. Do not clear journals.
5. On `access-updated`, processing remains paused. The new lists are saved as
   `AI2S_STATE.access`, alongside the original asset IDs/journals, in one property write.
   The original configuration digest remains unchanged. The request property is
   removed after success. Subsequent validation and processing use the saved lists.
   Back up the updated state and record the owner decision and actual access tests.
6. Run `ai2sValidate`, check a previously created profile and its Member notes as the
   intended users, then run `ai2sInstallTriggers`. Reconciliation retains existing
   response ownership and profile URLs; it does not migrate or regenerate responses.

An interrupted property write leaves either the old or new access lists; processing
stays paused. Rerun `ai2sUpdateAccess` with the same request if it remains present;
otherwise inspect `AI2S_STATE.access` and rerun validation. No step creates assets,
sends invitations or grants access on the owner's behalf. During incomplete manual
changes the ordinary validator may report access drift against the previous lists;
the update function validates the proposed lists before adopting them. If abandoning
an update before adoption, the owner must inspect and remove only its manual additions
to restore the old access; the script does not revoke permissions automatically.

The update supports additions only, including deployments using individual user lists
without groups. Removals, role changes, coordinator/owner/destination changes and
oversized configurations require a separately inspected migration. The existing
8,500-byte state guard applies to the additional lists; use institution-managed groups
for larger memberships. Never delete state, overwrite it with a stale backup, or edit
its digest to bypass validation. Rollback to older code must account for this access
overlay before resuming; older code does not understand the new lists.

Ownership/trigger-creator changes require a planned handoff: pause under the old owner,
preserve private state, inspect asset/Cloud-project ownership and group access, and
authorize the replacement owner deliberately. Do not simply copy the project and run
setup. The old owner's triggers must be removed by that owner or an administrator.
