# Chat Page Override

This page follows the EvoFlow-compatible control-plane interaction: conversation
first, plan authorization above the composer, and inline deliverables.

The chat page is a working session, not a landing page. The first viewport
must make three things obvious: the current conversation, the latest answer,
and where to send the next request.

- Keep the conversation thread at `--pm-chat-thread-width` and do not stretch
  prose to the full desktop viewport.
- Keep the page header compact: title, model state, and clear action share one
  aligned row.
- Use a neutral gray user bubble only as a role cue; assistant output remains an
  unframed reading surface.
- The composer uses the purple `#635bff` focus state, visible keyboard ring, and
  an 80px text area; the mode pills sit directly above it.
- Plan confirmation appears above the composer, with one primary action and a
  separate view-plan action; changing a draft revises the same plan ID.
- Welcome suggestions are grouped by the working chain, not a flat button grid;
  desktop columns collapse to one column on narrow screens.
