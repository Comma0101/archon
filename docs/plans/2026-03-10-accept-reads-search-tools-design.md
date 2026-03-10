# Accept Reads And Search Tools Design

**Problem**
Archon exposes `accept_reads` as a permission mode, but current behavior does not distinguish it from `confirm_all` in filesystem tool confirmation flow. Archon also lacks first-class read-only search tools comparable to Claude Code's file search and content search, forcing users through `shell` for common discovery tasks.

**Goals**
- Make `accept_reads` a real runtime behavior.
- Add first-class `glob` and `grep` tools as read-only filesystem tools.
- Keep the change small and local to the existing filesystem/tool model.
- Preserve current write, self-modification, and dangerous shell confirmation behavior.

**Non-Goals**
- No registry-wide permission redesign.
- No shell safety redesign.
- No full-text indexing or advanced search engine.
- No UX redesign beyond surfacing the new tools through the existing registry/CLI paths.

## Approach

Implement the slice inside `archon/tooling/filesystem_tools.py`.

- Add a small permission helper for read-only filesystem tools.
- Treat `read_file`, `list_dir`, `glob`, and `grep` as read-only for `accept_reads`.
- Keep `write_file`, `edit_file`, and `shell` behavior unchanged.
- Register `glob` and `grep` in the filesystem tool group so they appear everywhere the registry is consumed.

This keeps the behavior close to current code, minimizes regression risk, and avoids widening the permission architecture in the same change.

## Permission Semantics

- `confirm_all`
  - Existing behavior: read tools and write tools still follow current confirmation behavior.
- `accept_reads`
  - `read_file`, `list_dir`, `glob`, `grep` run without confirmation.
  - `write_file`, `edit_file`, own-source writes/edits, and dangerous shell continue to confirm.
- `auto`
  - Existing behavior remains: current write/edit confirmation logic is preserved.

## Tool Behavior

### `glob`
- Inputs:
  - `pattern`: glob pattern such as `**/*.py`
  - `root`: optional root path, default `.`
  - `limit`: max matches, bounded
- Output:
  - absolute resolved file paths, one per line
- Rules:
  - read-only
  - ignore nonexistent root with a clear error
  - do not return more than the bounded limit

### `grep`
- Inputs:
  - `pattern`: substring or regex
  - `root`: optional root path, default `.`
  - `glob`: optional filename filter such as `*.py`
  - `limit`: max matches, bounded
- Output:
  - `path:line:text`
- Rules:
  - read-only
  - traverse text files only best-effort
  - skip unreadable/binary files without failing the whole tool
  - bounded results

## Testing Strategy

TDD-first.

1. Add failing tests proving `accept_reads` is distinct from `confirm_all`.
2. Add failing tests for `glob` registration and matching.
3. Add failing tests for `grep` registration and matching.
4. Implement minimal code in `filesystem_tools.py`.
5. Run focused tests, then full suite.

## Risks

- `grep` can accidentally read large/binary trees if left unconstrained.
  - Mitigation: cap matches, best-effort text reads, optional filename filter.
- Changing permission semantics can break assumptions in existing tests.
  - Mitigation: keep scope limited to new read-only tools and explicit `accept_reads` mode tests.

## Success Criteria

- `accept_reads` is observably different at runtime.
- `glob` and `grep` are registered and usable via the normal tool registry.
- Existing write/self-modification protections still hold.
- Full test suite remains green.
