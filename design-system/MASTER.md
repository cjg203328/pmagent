# ArtPM Agent Design System

## Product Pattern

Quiet operations workspace for project managers who repeatedly inspect work,
ask for an action, review generated output, and continue the same thread.
The interface prioritizes scanability, task state, and safe delivery over
marketing-style decoration.

## Visual Direction

- Style: restrained utility with editorial reading comfort.
- Density: compact navigation, generous but bounded reading width.
- Surfaces: `#f7f7f8` chat canvas, `#f6f7f9` navigation rail, white composer
  and plan surfaces, thin neutral dividers, purple execution state.
- Avoid: giant empty hero areas, nested cards, gradients, decorative blobs,
  emoji as interface icons, and full-width prose that is difficult to scan.

## Color Tokens

| Role | Token | Value |
| --- | --- | --- |
| Canvas | `--pm-canvas` | `#f7f7f8` |
| Surface | `--pm-paper` | `#ffffff` |
| Navigation | `--pm-sidebar-bg` | `#f6f7f9` |
| Ink | `--pm-ink` | `#24232b` |
| Secondary text | `--pm-ink-secondary` | `#55545f` |
| Muted text | `--pm-muted` | `#686771` |
| Divider | `--pm-line` | `#dedee4` |
| Primary action | `--pm-accent` | `#635bff` |
| Primary hover | `--pm-accent-hover` | `#554be9` |
| Warning | `--pm-warning` | `#b7791f` |
| Danger | `--pm-danger` | `#c24141` |

Normal text must maintain at least 4.5:1 contrast. Color never acts as the
only status signal: status labels, icons, and explanatory text travel with it.

## Type and Layout

- Body: system UI stack with Chinese fallbacks already used by the app.
- Body size: 14px desktop, 16px minimum for mobile input text.
- Body line-height: 1.6 to 1.72 for assistant content.
- Reading width: 760px for conversation content; utility controls sit outside
  the reading measure where possible.
- Sidebar: 240px desktop, collapsible through Streamlit's native control.
- Spacing scale: 4 / 8 / 12 / 16 / 24 / 32 / 48px.
- Controls: minimum 44px height for primary actions and touch targets.

## Interaction Rules

- Every button has hover, pressed, disabled, and visible keyboard-focus states.
- Async actions must show a state change and preserve the result across reruns.
- Destructive actions require an explicit confirmation step.
- Conversation switching preserves the current workspace scope.
- Errors appear next to the affected action and include a recovery path.
- Motion uses 150–250ms transitions and respects `prefers-reduced-motion`.

## Page Overrides

- Chat: optimize for one next action. Keep the assistant answer readable,
  user prompts compact, and the composer visually anchored to the bottom.
- Workbench: show task, process, and artifact as three equal-priority areas;
  do not bury the downloadable result behind prose.
- Settings: group configuration by decision, with a persistent summary and a
  single clear save action.

## Responsive Rules

- At 1024px and below, reduce gutters before reducing text size.
- At 768px and below, stack utility columns and keep controls at 44px minimum.
- At 480px and below, allow chips and long labels to wrap rather than clip;
  conversation content remains within the viewport with no horizontal scroll.

## Review Checklist

- [ ] Main content is bounded to a readable line length.
- [ ] Active navigation is obvious without relying on color alone.
- [ ] Buttons and inputs have visible focus styles.
- [ ] Empty, loading, error, and success states are all legible.
- [ ] No layout shift occurs when feedback or attachments appear.
- [ ] Reduced-motion users receive the same state feedback without animation.
