# Track 1: Clinical Documentation

## What to build

An app where a clinician enters their role and task and gets one screen with the patient facts that task needs.

Example: "anaesthetist, pre-operative check before breast surgery" shows her age, blood thinners, allergies and heart function. The tumour's receptor status is left out, because it does not matter for anaesthesia.

- Every fact links to the sentence or table row it comes from.
- If the task needs something the documents do not contain, the screen says "not documented".
- The app points out contradictions in the documents.
- The screen shows only facts from the documents. Translations, generic drug names and calculated values such as a weight change are fine.
- You may hard-code which kinds of facts a role or task needs. Your app has to find the facts, passages and contradictions itself.

## What you get

- `data/patient-0000-0001/documents/`: eight German clinical documents from 2015 and 2016 for one synthetic patient, Anna Musterfrau.
- `data/patient-0000-0001/*.json`: diagnoses, procedures, medication and measurements that an earlier version of IBM's Medical Cockpit pipeline extracted from the documents. They say which document a fact comes from. Where they and the documents differ, the documents count.
- You can open IBM's Medical Cockpit for this patient at https://route-patient-summary-mla-demo.ohpdemo01-990e7b837f1894cb67c000b2ea26e354-0000.eu-de.containers.appdomain.cloud/#/cp-dashboard/0000-0001 to see where the JSON files come from. It lists everything about the patient, while your app should show only what one role needs for one task. If the page does not load, turn off your VPN. IBM switches the Cockpit off after the hackathon. Everything you need is in this repository.
- `cases/situations.md`: 5 situations with 3 questions each. `cases/expected.json` has the answers.
- `data/README.md` describes every file.

Clone this repository to get the data.

## How to test

Enter a situation's title and description into your app. Then check whether the screen answers its three questions.

## Metrics

Measure these on the 5 situations. The jury also tests your app on new situations for the same patient, written in English like the five here, possibly with other roles.

| Metric | How to measure | Target |
|---|---|---|
| Correct answers | The screen shows the expected answer without clicking (scrolling is fine), with a link to one of its source documents. Wording can differ. A "not documented" answer needs no link | 15 of 15 |
| Traceability | Share of facts on screen whose link shows the right passage | 100% |
| Invented facts | Facts on the 5 screens that are not in the documents | 0 |
| Contradictions | Real contradictions your app shows, minus false alarms. Each counts once | 5 or more |
| Load time | Slowest of the 5, from submitting to the complete screen | Under 10 s |

A contradiction is two or more passages that cannot all be true, for example two dates for the same event or a BMI that does not match the height and weight in the same letter. Each one must link all its passages. Small measurement differences, values that changed over time and differences from the JSON files are false alarms. We know of at least 5.

## You decide

- How the situation is entered: free text, a role picker, voice.
- Rules, an LLM or both.
- IBM's extraction or your own.
- The language of your interface, English or German.
- Your stack. Cloud AI models are fine, because the patient is made up.

## Rules

Use only synthetic data. Do not publish IBM's data: keep the `data` folder out of your repository.

## Judging

| Criterion | Weight | Question |
|---|---|---|
| Clinical approach and impact | 33.3% | Would a clinician trust and use this screen in that situation? |
| Technical quality and AI | 33.3% | Does the screen change with the role and task? Is every fact linked to its source? If you use AI, does it help? |
| Execution and prototype | 33.3% | Does the app work end to end, on the five situations and on new ones? |

The jury scores each criterion from 1 to 10 and takes your metric results into account.

## Submission

1. A video of up to 2 minutes showing your demo. Say what works and what does not.
2. A link to your public repository with instructions to run it, without the `data` folder. Its README has to explain your technical architecture: which components you built, how they work together and which models or services you use.
3. Your metric results on the 5 situations.
