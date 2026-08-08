# WhisperTray onboarding design QA

- Source visual truth: `artifacts/design/onboarding-option-1.png`
- Implementation capture: `artifacts/design/onboarding-implementation-step1.png`
- Combined comparison: `artifacts/design/onboarding-design-qa-comparison.png`
- State: first-run onboarding, step 1, Privacy selected
- Source pixels: 1175 × 1338
- Implementation pixels: 716 × 739, including native Windows frame
- Application content size: 700 × 700 CSS-independent Qt pixels at Windows density 1
- Normalization: both frames are shown at native pixel density in the combined comparison; the source is an intentionally larger concept render and the implementation is the production desktop window.

## Full-view comparison

The implementation preserves the selected direction: warm dark surface, centered brand mark and welcome copy, coral accent, two large profile cards, strong selected state, one primary action, and a deliberately limited first-step information hierarchy. The production window is more compact than the concept render but keeps the same proportions and reading order.

Focused region comparison was not needed: the logo, headings, card outlines, icons, selected check, subtitles, and button copy are legible at full-view size in the combined comparison.

## Required fidelity surfaces

- Fonts and typography: native Segoe UI provides a close Windows product equivalent to the concept's humanist sans-serif. Heading, card title, body, and helper-text weights remain distinct with no clipping or truncation.
- Spacing and layout rhythm: the final 700 × 700 content area avoids the earlier excess vertical gap and keeps the profile decision above the fold. Cards and the primary action align consistently.
- Colors and visual tokens: charcoal `#1a1c20`/`#202329`, coral `#ff765d`, cream foreground, and muted secondary text match the source direction and retain readable contrast.
- Image and asset fidelity: the production UI uses the supplied WhisperTray logo asset. The shield and lightning symbols are native Qt-painted interactive card icons and remain crisp at display density.
- Copy and content: Russian welcome, profile names, descriptions, and primary action match the selected concept's intent. The step label is expressed as localized text (`Шаг 1 из 3`) instead of decorative numbered circles to improve accessibility and translation.

## Comparison history

### Pass 1

- P2: onboarding was 560 px wide and looked compressed compared with the selected concept.
- P2: selected checkmark was drawn partly outside its coral circle.
- P2: primary action was only as wide as its text.

Fixes: increased the onboarding frame and cards, corrected checkmark geometry, and gave the primary action a stable width.

### Pass 2

- P2: the 760 px-high frame left excessive unused space below the primary action.
- P2: vertical layout distributed too much space between the step label and profile prompt.

Fixes: aligned the profile page from the top, tightened spacing, and reduced the production height to 700 px.

### Final pass

Post-fix evidence is saved in `artifacts/design/onboarding-design-qa-comparison.png`. No actionable P0, P1, or P2 visual differences remain.

## Follow-up polish

- P3: a future iteration may replace the localized step text with a custom accessible three-node progress control if the additional visual complexity proves useful in user testing.

## Interaction checks

- Selecting Privacy and Speed is mutually exclusive.
- Continue advances to the correct backend-specific page.
- Privacy exposes only local model controls.
- Speed exposes only Groq key, key help, test, and explicit cloud explanation.
- The final step contains microphone and hotkey validation.
- Reduced-motion mode stops the recording pulse timer.

final result: passed
