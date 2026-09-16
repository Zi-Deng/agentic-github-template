# Native Google Docs profile specification

The reusable template and each member document must have two native document tabs.
Google Docs permissions apply to the entire document: members edit their own document,
and team readers can read both tabs. Tab separation is an automation rule, not a
security boundary. The Google adapter is pending at this checkpoint.

| Tab | Contents | Automated changes |
| --- | --- | --- |
| Profile | Generated self-reported member snapshot | Replace only this tab, using its stored tab ID and a required document revision |
| Member notes | Durable member additions, qualifications, context and corrections | Initial instructional text only; never overwrite on updates or retries |

## Profile tab

[Profile.gs](src/Profile.gs) emits paragraphs with native Docs named styles (`TITLE`,
`SUBTITLE`, `HEADING_1`, `HEADING_2`, `NORMAL_TEXT`) in this order:

1. Preferred name; “AI2S member profile — self-reported”; role; latest response time in UTC.
2. Disclosure that claims, examples and references are self-reported and unverified;
   missing fields mean not reported, with no overall expertise score.
3. Contribution overview from selected contributions, including Other.
4. Named strengths, each followed by experience description, recency and example.
   The core strength also carries its supplied evidence context. Extra strengths are
   included only when the respondent chooses additional detail and supplies a field.
5. Respondent-supplied project, portfolio or other reference, explicitly unverified.
6. Optional technical areas, activities, tools and additional context, only on the
   current technical-detail route. Categories do not imply proficiency.
7. Learning interests, separate from reported experience.
8. Collaboration preferences with no implied availability commitment.
9. Reminder to edit the intake for generated changes and use Member notes for durable
   additions. Both tabs are visible to the team.

The adapter must insert respondent content as plain text, never as a template,
formula, request object or script. It may add clickable links only after validating
an explicit `https:` or `http:` URL; other supplied references remain literal text.
The pure mapper never constructs a link operation. Operational email keys, response
IDs and edit-response links are not profile fields.

Use Arial 11 pt body text, 1.15 line spacing and paragraph spacing for readability.
Use 20 pt title, 15 pt first-level headings and 12 pt second-level headings, with a
restrained dark blue accent and high contrast. Use approximately one-inch margins.
A typical core profile targets about two pages; do not truncate longer self-reports
to enforce a page count. Confirm layout and links in the institutional pilot.

## Member notes tab

Initial content:

> Member notes
>
> Add strengths, qualifications, context, or corrections you want teammates to know.
> Both tabs are readable by the AI2S team. The intake updates the Profile tab;
> this Member notes tab is preserved. Avoid confidential information.

Members can format this tab freely. Automation must not replace its text, styles,
tables, links or tab identity. Reading the whole document, selecting the stored
Profile tab and updating against the observed revision are required to avoid blind
replacement across tabs or writing over a concurrent edit. A revision conflict must
retry from a fresh document read.

## Pending live template checks

Inspect the real template and output for tab names and IDs, readable headings,
reasonable pagination, no leftover template placeholders, and valid supplied links.
Add styled text and a table to Member notes, edit a Form response, then confirm the
same profile URL and unchanged notes/formatting. Local mapping tests do not prove
that the Google Docs adapter or live access boundaries work.
