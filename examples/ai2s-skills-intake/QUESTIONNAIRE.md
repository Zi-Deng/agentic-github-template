# Questionnaire specification

Title: **AI2S — Skills, Experience & Collaboration**

The complete executable specification, including exact titles, help text, options and
stable logical keys, is [Questionnaire.gs](src/Questionnaire.gs). The future setup
adapter must persist each Google item ID against its logical key. Titles and sheet
column positions must never be used as response identifiers.

The introduction explains the purpose, 5–7 minute core target, optional depth,
team-readable profiles, restricted raw responses, later edits, and that no AI
experience is required. Respondents should omit confidential details. Unchecked or
missing answers mean not reported. The timing target still requires the pilot.

## Core

| Key | Question | Type | Required |
| --- | --- | --- | --- |
| `name` | What name should appear on your profile? | Short answer | Yes |
| `role` | What is your current role or main area of work? | Short answer | Yes |
| `strength1` | What is one strength you would like teammates to know about? | Short answer; “Still exploring” accepted | Yes |
| `strength1Experience` | How have you used this strength? | Multiple choice | Yes |
| `strength1Recency` | When did you last use it? | Multiple choice | Yes |
| `strength1Evidence` | What experience best supports this strength? | Multiple choice | No |
| `contributions` | Where could you contribute to a project? | Checkboxes and Other | No |
| `collaboration` | How would you like to collaborate? | Checkboxes | No |
| `learning` | What would you like to learn next? | Checkboxes and Other | No |
| `strength1Example` | Can you share an example of your contribution? | Optional brief paragraph | No |
| `evidenceLink` | Is there a relevant project, portfolio, or other link? | Short answer | No |
| `moreDetail` | Would you like to add more detail? | Finish core / Add detail | Yes |

Ask the self-named strength before category lists. Experience descriptions are:

- Familiar with the concepts; have not applied them yet.
- Have applied them with guidance.
- Can apply them independently in familiar situations.
- Can adapt or design approaches for unfamiliar situations.
- Not sure how to describe my experience yet.

Recency: Within six months; Six–24 months ago; More than two years ago; Not applied yet.

Evidence: Learning/coursework; Personal practice or prototype; Work/research project;
Ongoing operational work; Not yet applicable.

Contribution options: Subject or domain knowledge; Understanding stakeholder needs;
Data collection and analysis; AI/model research and development; Software and data
engineering; Deployment and operations; Responsible AI, privacy, and security;
Design and accessibility; Communication and training; Project coordination;
Finance, grants, procurement, and administration; Other (free text).

Collaboration: Contributing; Learning alongside someone; Reviewing/advising; Mentoring.
These preferences do not imply an availability commitment.

Learning: AI fundamentals; Data/statistics; Models; Software; Deployment;
Responsible AI; Project support; Other (free text).

## Optional depth

`strength2` and `strength3` each have an optional self-named strength, the same
experience descriptions and recency choices, and an optional example. Any professional
skill is welcome; both blocks may be left blank. The only required question on this
page is `technicalDetail`: Skip technical detail / Add technical detail.

Every question on the technical page is optional:

| Key | Type | Choices or prompt |
| --- | --- | --- |
| `technicalAreas` | Checkboxes and Other | Structured-data prediction; Language/LLMs; Images/video; Audio; Multimodal systems; Forecasting; Search/recommendations; Graphs; Causal inference; Reinforcement learning/robotics; Scientific ML |
| `technicalActivities` | Checkboxes and Other | Data preparation; Experimentation; Training/fine-tuning; Evaluation/calibration/robustness; Retrieval/RAG; Agent integration; Deployment/monitoring; Distributed/HPC work; Privacy/security; Responsible-AI evaluation; Reproducibility |
| `tools` | Short answer | Which languages, frameworks, platforms, or specialist tools do you use? |
| `additionalContext` | Paragraph | What additional context would you like to share? |

## Routing and visibility

| From | Answer | Destination |
| --- | --- | --- |
| Core | Finish core | Profile visibility |
| Core | Add detail | Optional strengths |
| Optional strengths | Skip technical detail | Profile visibility |
| Optional strengths | Add technical detail | Optional technical detail |
| Optional technical detail | Continue | Profile visibility |
| Profile visibility | Submit | Submit response |

Route with multiple-choice questions, as supported by
[Google Forms answer-based routing](https://support.google.com/docs/answer/141062?hl=en).
Checkboxes never control routing. A response edited to skip an optional section must
not regenerate old answers retained in that section.

The final required acknowledgement (`visibility`) is:
“I understand my profile is readable by the AI2S team.” Its help explains that
members can edit their document, durable additions belong in Member notes, the
Profile tab is regenerated, and both tabs are visible to the team. Finishing the core
always reaches this acknowledgement without technical questions.

Verified-account email collection, one response per account, response editing,
disabled summaries, responder access and publication are deployment requirements;
this data specification does not configure or verify a live Form.
