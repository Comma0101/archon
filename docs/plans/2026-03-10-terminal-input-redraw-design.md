# Terminal Input Redraw Design

**Goal:** Fix terminal prompt drift after background activity events and eliminate first-character/input duplication around the live input path.

**Problem**
- `TerminalActivityFeed` redraws from `current_prompt()` plus `current_input()`.
- `current_input()` currently reads only `readline.get_line_buffer()`.
- During the transition from raw first-keystroke capture into readline, and during the live slash palette, the visible user input is not fully represented by the readline buffer.
- When an activity event arrives in those windows, Archon redraws an incomplete prompt state and the terminal cursor/input visually drifts.

**Design**
- Introduce a small shared interactive input state owned by `chat_cmd`.
- `current_input()` should prefer this shared state when present, and fall back to `readline.get_line_buffer()` otherwise.
- Update the live input helpers so they publish transient visible input:
  - first typed non-slash character before readline startup hook finishes
  - live slash palette query as it changes
  - clear the shared state on exit
- Keep `TerminalActivityFeed` simple; it should continue redrawing from `current_prompt()` and `current_input()` without knowing about readline internals.

**Scope**
- `archon/slash_palette.py`
- `archon/cli_interactive_commands.py`
- tests in `tests/test_cli.py` and `tests/test_slash_palette.py`

**Non-goals**
- full TUI rewrite
- changing shell command behavior
- moving the activity feed off its current stream model unless the shared-state fix fails to solve the bug

