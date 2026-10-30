# Step-by-step flow: sign-in, patient selection, task, patient view

## What changes for the physician
1. **Sign-in screen** (first thing you see): pick or type your profession (same list as now: Anaesthetist, Surgeon, ...) and an optional name, then "Continue". No password; this is a prototype.
2. **Top-right user badge** on every later screen: shows the profession (and name if given) plus a "Sign out" button. The profession is remembered until sign-out or the browser tab is closed.
3. **Patient selection screen**: a list of patients (name, ID, number of documents, latest document date). For now only "Test Patient 0000-0001". Click to choose.
4. **Task screen**: shows the chosen patient's name at the top, a task field plus the quick-fill examples (filtered to your profession where possible), and a "Open patient" button. The role field disappears, since it comes from sign-in.
5. **Patient view**: the current results screen (banner, facts, contradictions, source panel), already loaded for your task. A clear **"Finish assessment"** button returns to the patient selection screen; "Change task" goes back one step.

If you're not signed in and open a later screen directly, you get sent back to sign-in. If no patient or task is chosen, you get sent to the matching earlier step.

The patient view layout stays as it is for now, since you want to plan it next.

## Technical details
- Routes: `/` sign-in, `/patients` list, `/patients/$patientId/task`, `/patients/$patientId?task=...` view. Each has its own head() metadata.
- Session: small `src/lib/session.ts` storing `{ role, name }` in sessionStorage plus a React hook. Guards in `beforeLoad` on the client side (`ssr: false` on the gated routes) redirect to `/` when there's no session.
- `src/lib/api.ts`: add `listPatients()`; mock list in `src/lib/mock.ts` (one patient). The USE_MOCK switch stays.
- `ClinicalApp` becomes `PatientView` taking `patientId`, `role`, `task` as props and running the summary automatically; the form at the top is removed and replaced by the "Change task" / "Finish assessment" buttons.
- Shared header component with app title and the user badge.
- Tests: guard redirect without session; role in the summary request comes from the session.
