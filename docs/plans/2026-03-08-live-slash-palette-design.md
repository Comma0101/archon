# Live Slash Palette Design

## Problem

Archon's terminal slash surface is still partially legacy. The current shell improves completion and filtered picking, but typing `/` does not immediately show commands. The user must still press `Enter` or `Tab` to see options, which is not the Claude Code / Codex interaction model.

## Goal

Make `/` open a live inline command palette that filters as the user keeps typing, while preserving the existing non-slash chat path and reusing the current command registry.

## Approaches

### 1. Recommended: slash-only inline overlay

Keep normal chat input on the current readline path. When the line starts with `/`, switch into a lightweight raw-key slash mode that renders filtered suggestions below the prompt and resolves back into the existing REPL command handlers.

Pros:
- removes the visible legacy slash behavior
- limits risk to slash commands only
- reuses existing command data and handlers

Cons:
- adds a second input path
- needs careful prompt redraw and key handling

### 2. Full custom line editor

Replace the whole input loop with a custom editor.

Pros:
- maximum control
- ideal long-term UX

Cons:
- much higher regression risk
- unnecessary for the current user goal

### 3. More readline hacks

Stay on the old picker/completer model and try to make it feel live.

Pros:
- smallest patch

Cons:
- does not actually solve the UX problem
- leaves legacy behavior in place

## Recommended Design

Implement a dedicated slash palette mode that activates only when the first typed character is `/`.

### Behavior

- `/` immediately opens the palette
- continued typing filters suggestions live
- `Up` / `Down` changes the highlighted item
- `Tab` accepts the current token or highlighted item
- `Enter` executes the highlighted command
- `Esc` closes the palette and preserves the typed text
- normal chat input still uses the current path

### Data Model

Reuse the existing command sources:

- slash command list
- dynamic subvalues for profiles, skills, plugins, MCP servers, jobs, permissions, and models

No second registry and no duplicate command logic.

### Technical Shape

- add a small `slash_palette` module for:
  - raw key reading
  - token-aware filtering
  - inline rendering
  - selection state
- integrate it into the interactive chat loop before the normal `input()` call
- keep the existing picker as a fallback only

### Safety

- slash mode is local shell UI only
- no agent call should happen until a resolved command string is returned
- bracketed paste, multiline paste, and normal message input should remain unchanged

### Testing

- unit tests for filtering and selection behavior
- CLI tests for slash-mode activation and command resolution
- regression tests to ensure normal non-slash input still bypasses the palette

