# Patient view: layout and display update

## What changes

1. **Loading spinner** – while the patient page loads (documents and summary), show only a simple spinning circle in the middle of the page.

2. **Draggable split** – a thin handle between the left overview and the right document stack. Start at 45% / 55%; dragging limited to about one quarter of the page either way (roughly 20%–70% for the left side). Both sides resize live. Works with mouse, touch and arrow keys. On narrow screens (stacked layout) the handle is hidden.

3. **Less boxy look** – answer entries become plain text rows: no border, no card background. They are separated by spacing and a faint divider line. The coloured number circle stays, and a thin coloured bar on the left appears only on hover or when the entry is active. Missing items become grey italic text ("Not documented") with no dashed box. Source links become small underlined text links, not buttons.

4. **New top bar** – "Task-focused patient facts" is removed. The top bar now shows:
   - left: patient name, ID, number of documents, latest date (plus MOCK DATA tag)
   - right: the task (with role), the "Change task" and "Finish assessment" buttons, then the signed-in user and "Sign out"
   The old second patient bar is removed.

5. **CAVE line moved** – the red allergy warning (or grey "Allergies: not documented") sits at the top of the left column, where the task used to be, spanning the full width of that column with the same height and padding as the former task field.

6. **History for entries found on several dates** (e.g. Testmedikament Alpha) – the entry shows the newest value prominently, followed by a short timeline, newest first:
   ```text
   Testmedikament Alpha
   1000 mg 1-0-1        12 Apr 2016   ¶ Outpatient report
   500 mg 1-0-1         28 Jun 2015   ¶ Discharge letter   (dose changed)
   ```
   Each timeline line links to its own source passage. A "changed" marker appears where the value differs from the next newer one. Single-date entries look unchanged.

7. **Conflicts inside their topic** – the separate "Conflicting information" section is removed. Each conflict appears inside the matching topic (medication conflict under Medication, allergy conflict under Allergies) as a yellow/amber warning line with the conflicting statements and their source links. Topics that have a conflict get a small amber "!" next to their heading. Conflicts in topics with no other entries still show their topic heading. The yellow warning colour and the striped marks in the documents stay as they are.

## Not changed
Sign-in, patient list and task screens; ranking order; fact verification; document stack behaviour.

## Technical details
- `ClinicalApp.tsx`: full-screen spinner while `patient`, `docs` or `summary` loading; new `SplitPane` (pointer events on handle, width state in %, clamp 20–70, keyboard support, `role="separator"`); rework `FactRow` (no card), `MissingCard` → `MissingLine`, `SourceChip` → text link.
- `AppHeader.tsx`: accept optional `left`/`right` slots; patient view passes patient info and task + buttons; other screens keep a simple title.
- Timeline: build from `fact.sources` sorted by `doc_date` desc; per-source value is the source quote with the label prefix stripped (verified quote text, never invented). "Changed" when normalized quotes differ.
- Conflicts: render `ranked.relevantContradictions`/`otherContradictions` within `renderCategory` by `c.category`; drop the show-more toggle; categories that only have conflicts are included in the category list.
- Update tests if any reference the removed conflict section; add a test for the timeline ordering (newest first) and the split clamp.
