# Institutional deployment

The implementation executor prepares and tests these files. The coordinator and
institutional owner perform the following steps after independent review. All live
results currently remain [pending](LIVE-VALIDATION.md).

## Inputs the owner must supply

Use an institutional account authorized to own the Form, Sheet, template and member
documents. This example supports **that account's My Drive**, with one explicit
private destination folder. It rejects Shared Drives and assets owned by another
account. No owner, domain, reader group or coordinator identity is guessed.

| Configuration property | Required value |
| --- | --- |
| `ownerEmail` | Owner's primary signed-in institutional Google account email, not an alias |
| `coordinatorEmails` | Explicit list of individual coordinator emails; `[]` deliberately means owner-only administration |
| `privateRootId` | Existing restricted My Drive folder owned by that account |
| `teamReaders` | Nonempty list of `{ "type": "group" or "user", "email": "supplied address" }` for profile readers |
| `responders` | Nonempty list of the exact groups/users to receive published Form responder access |

Create the private folder in a location whose entire ancestor chain is owned by the
owner and has no access beyond the owner and listed coordinators. Owner must have
owner access; any coordinator access must be writer. The validator refuses broad
domain/link access, unexpected users, stronger-than-configured roles, expiring access
and pending ownership transfers. It validates effective file permissions and ancestors.
It never removes unexpected permissions automatically. Use a different restricted
folder or have the owner inspect and fix the access deliberately.

Use institution-managed groups whose membership the owner can verify. For the pilot,
these configured groups should contain only the intended pilot participants. After
successful acceptance, the institutional group administrator may expand the same
groups to the authorized AI2S membership. The code checks the configured group's
file role, not group membership, nesting or institutional sharing rules. Record those
checks privately. Choose direct user lists when that better matches institutional policy.

## Install the reviewed source

1. Record the reviewed repository commit from PR #8. Open the example directory at
   that exact commit, not a moving branch. Its files are
   [here on the issue branch](https://github.com/Zi-Deng/agentic-github-template/tree/issue-7-ai2s-skills-intake/examples/ai2s-skills-intake);
   use GitHub's commit selector to pin the reviewed version.
2. Sign in as the selected institutional owner and open
   [Google Apps Script](https://script.google.com/). Create a standalone project.
   Keep project access restricted to the owner and approved coordinators. Respondents
   need access to the Form and their own document, not project code or Script Properties.
3. Create one Apps Script file for each source below, using the same basename and
   copying its complete contents. All nine files share the project's V8 global context;
   no npm packages, bundler or external library is required.

   | File | Purpose |
   | --- | --- |
   | [App.gs](src/App.gs) | Owner entrypoints, setup, validation, triggers and recovery |
   | [Documents.gs](src/Documents.gs) | Native tab targeting, revisions and formatting |
   | [FormSchema.gs](src/FormSchema.gs) | Stable form item/question IDs and answer decoding |
   | [Google.gs](src/Google.gs) | Google REST adapter, literal Sheets values and access checks |
   | [Pipeline.gs](src/Pipeline.gs) | Identity binding and member-processing state machine |
   | [Profile.gs](src/Profile.gs) | Deterministic profile content |
   | [Questionnaire.gs](src/Questionnaire.gs) | Core and optional questionnaire |
   | [Recovery.gs](src/Recovery.gs) | Persisted creation operations |
   | [Safety.gs](src/Safety.gs) | Configuration, principals and safe errors |

4. In **Project Settings**, enable display of `appsscript.json`. Replace its content
   with the supplied [manifest](appsscript.json). It selects V8 and UTC, disables
   automatic exception logging, lists OAuth scopes and limits fetches to four Google
   API origins. No web app or API executable deployment is needed.
5. Associate an institution-approved **standard Google Cloud project** through Project
   Settings and its project number. Configure the OAuth consent audience for the
   institution and enable **Google Drive API, Google Docs API, Google Sheets API and
   Google Forms API** in that Cloud project. The adapter uses REST with the owner's
   Apps Script OAuth token; there are no Advanced Services to add in the editor.
   Follow [Google's Cloud project guidance](https://developers.google.com/apps-script/guides/cloud-platform-projects)
   if an administrator must create or associate the project. This example requires
   no paid API, paid infrastructure or billing upgrade.
6. Copy [config.example.json](config.example.json), fill the supplied values privately,
   and save the entire JSON as a Script Property named **`AI2S_CONFIG`**. The blank
   example deliberately fails validation. Do not commit the populated file. Local
   private working copies belong in the repository's ignored `memory/` directory.
7. Select **`ai2sSetup`**, click Run and complete Google's authorization flow directly
   as the owner. The requested Forms/Drive/Docs/Sheets scopes cover asset creation,
   current response reads, content updates and ACL inspection; `script.scriptapp`
   manages triggers, `script.external_request` calls the allowlisted APIs, and
   `userinfo.email` verifies the executing owner. Drive access must cover the existing
   destination and its inherited permissions. The script has no email-sending scope.
   Consult [Google's authorization guidance](https://developers.google.com/apps-script/guides/services/authorization).
8. Run **`ai2sStatus`** and read the execution log for the asset IDs. Build their owner
   URLs as `https://docs.google.com/forms/d/FORM_ID/edit`,
   `https://docs.google.com/spreadsheets/d/SHEET_ID/edit`,
   `https://docs.google.com/document/d/TEMPLATE_ID/edit` and
   `https://drive.google.com/drive/folders/FOLDER_ID`. Keep the state and private URLs
   in the owner's deployment record. Do not put response-edit links in shared profiles.

Setup persists asset creation journals before requests. A rerun reuses those assets
and their item IDs. `CREATION_UNCERTAIN` requires the recovery procedure; deleting
`AI2S_STATE` or starting a second project is not a retry. Initial assets may retain
an opaque operation title until setup is complete. The completed Form starts unpublished
and processing stays paused.

## Verify and publish the pilot

1. Open the generated Form. In **Settings → Responses**, confirm **Verified** email
   collection, **Limit to 1 response**, and **Allow response editing**. Setup requests
   these settings; the validator independently reads the actual Forms API enum and
   rejects responder-entered email collection. The API distinguishes these modes in
   [FormSettings](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms#EmailCollectionType).
2. Under presentation settings, confirm response summaries are disabled. Keep question
   shuffling and quiz mode off. Do **not** click “Link to Sheets”: the script maintains
   its own restricted `Responses` and `Register` tabs using literal RAW writes. A
   built-in response destination causes validation to stop.
3. Preview the core-only route and optional routes. Confirm the final visibility
   acknowledgement appears on every route. Compare wording to [QUESTIONNAIRE.md](QUESTIONNAIRE.md).
4. In the Form's responder sharing controls, remove any broad “anyone with the link”
   or domain access and add precisely the configured `responders` groups/users as
   **Responders**, not editors. Leave notification/invitation boxes unchecked. Publish
   the pilot Form and enable acceptance of responses. Publishing and responder access
   are separate controls; see [Google's publishing instructions](https://support.google.com/docs/answer/2839588?hl=en).
   The validator requires the actual published ACL to match the configuration exactly.
5. Inspect asset sharing: the owner/coordinators alone administer the Form, raw Sheet
   and template. The profiles folder adds configured team readers. New profile copies
   are initially in the private folder, populated, and only then shared and moved to
   the profiles folder. All script-added ACLs set `sendNotificationEmail=false`.
6. Run **`ai2sValidate`**, resolve any reported code using [MAINTENANCE.md](MAINTENANCE.md),
   and run it again. Then run **`ai2sInstallTriggers`** as the owner. This creates the
   Forms submission trigger and 15-minute reconciliation trigger and enables processing.
   The interval is a schedule, not a delivery deadline.
7. Run the [live acceptance procedure](LIVE-VALIDATION.md) with a **real browser
   submission**. Apps Script documents that programmatic `FormResponse.submit()` does
   not fire the submission trigger. Triggers execute as their creator; only the owner
   should install them. [Installable trigger restrictions](https://developers.google.com/apps-script/guides/triggers/installable)
8. After the user-arranged pilot succeeds, have the owner apply the authorized AI2S
   memberships to the configured responder/reader groups. Recheck actual access as
   separate users, run `ai2sValidate`, and record the scope change in the private record.
   Changing the configured addresses/root/owner requires a separately inspected
   migration; a digest prevents silent repointing of an existing deployment.

If institution policy rejects authorization, API enablement, group resolution or
sharing, leave processing paused and preserve the assets. Record the safe error code,
the affected operation and the required administrator decision privately. Ask the
administrator to enable the named API/OAuth scopes or approve the specific sharing
relationship; do not broaden access or substitute a personal owner to get a pass.
