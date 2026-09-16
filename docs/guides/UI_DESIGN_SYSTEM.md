# ArtPM UI design system

This interface applies the design-engineering principles published in
[`emilkowalski/skills`](https://github.com/emilkowalski/skills) to a dense,
professional agent workspace. It borrows principles and motion constraints,
not source code or branding.

## Product character

- Calm and precise: the UI should feel like an operational tool, not a demo.
- Fast by default: repeated chat, navigation, and table actions stay stable.
- Explicit state: show running, complete, warning, and error states where the
  action happens.
- Familiar controls: buttons look pressable, destructive actions require a
  confirmation, and navigation labels describe their destinations.
- Common path first: chat is primary; system and workflow detail remain one
  level deeper in Settings and Observability.

## Visual tokens

The canonical token boundary is `artpm_agent/ui/style_tokens.py`. Page-level
visual work belongs in focused fragments such as `artpm_agent/ui/style_chat.py`;
`artpm_agent/ui_style.py` remains the compatibility injection surface and must
not accumulate new page-specific rules.

- Canvas: `#ffffff`
- Paper: `#ffffff`
- Primary text: `#172231`
- Secondary text: `#334155`
- Muted text: `#5b6b7d`
- Action accent: `#2563eb`
- Focus ring: `#1d4ed8`
- Main radius: `10px`

Chat keeps reading surfaces neutral: user turns use
`--pm-chat-user-bg` and assistant turns remain unframed. Blue is reserved for
focus, primary actions, and explicit state changes rather than every message.

Normal text and action labels must meet WCAG AA contrast. Do not introduce a
new color without testing it against every surface where it is used.

## Motion contract

- Never use `transition: all`.
- Use `cubic-bezier(.23, 1, .32, 1)` for UI response.
- Press feedback is `scale(0.97)` for 140ms.
- Most UI transitions are 200ms or less.
- Do not animate chat history, repeated navigation, metric cards, or data
  visualizations merely for decoration.
- Keep hover affordances behind a fine-pointer media query and remove
  transform motion for reduced-motion users.
- Prefer interruptible transitions. A rerun must not replay page entrance
  animation.

## Streamlit rules

- Use keyed `st.container` elements instead of opening and closing raw HTML
  around widgets.
- Use `width="stretch"` instead of the removed `use_container_width` argument.
- Do not apply card styling to every `st.container`; only approvals, artifacts,
  dialogs, and genuinely framed tools need a surface.
- Keep cloud deployments free of desktop-only controls such as Tk folder
  pickers. Set `ARTPM_DEPLOYMENT_MODE=cloud` for server deployments.
- A visible success message must correspond to a durable backend result.
- A visible model selection must be reapplied when the Agent instance is
  replaced.

## Chat landing contract

- The empty state has one compact product marker, one task-oriented heading,
  one supporting sentence, and the action grid. Do not add a marketing hero.
- Desktop uses three action columns. Viewports at or below `760px` use two
  columns; a single action in a row spans both columns.
- Action controls have a minimum height of `48px`, visible focus treatment,
  stable icon geometry, and no horizontal overflow at `390px`.
- The composer remains fixed to the bottom, reserves content space, and uses
  the native Streamlit attachment, audio, and submit controls.
- Welcome markup and state-independent suggestions live in
  `artpm_agent/views/chat_welcome.py`; the chat host only coordinates state.

## Regression checks

Run:

```powershell
uv run pytest -q tests/test_ui_design_system.py
uv run pytest -q tests/test_streamlit_app.py
uv run ruff check artpm_agent/ui/style_chat.py artpm_agent/views/chat_welcome.py
```

Browser verification covers desktop and mobile layouts, JavaScript console
errors, missing dynamic chunks, focus visibility, and horizontal overflow.

## Sources

- [Design Engineering skill](https://github.com/emilkowalski/skills/blob/main/skills/emil-design-eng/SKILL.md)
- [Apple Design skill](https://github.com/emilkowalski/skills/blob/main/skills/apple-design/SKILL.md)
- [Animation audit standards](https://github.com/emilkowalski/skills/blob/main/skills/improve-animations/AUDIT.md)
