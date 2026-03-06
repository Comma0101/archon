"""Best-effort worker-summary -> memory inbox capture helpers."""

from pathlib import Path

from archon.workers.base import WorkerResult, WorkerTask
from archon.workers.session_store_models import WorkerSessionRecord


_GENERIC_REPO_NAMES = {
    "app",
    "apps",
    "backend",
    "client",
    "frontend",
    "repo",
    "server",
    "service",
    "services",
    "site",
    "web",
}


def maybe_queue_worker_summary_candidate(
    record: WorkerSessionRecord,
    task: WorkerTask,
    result: WorkerResult,
) -> None:
    """Best-effort queue of useful worker completion summaries into the memory inbox."""
    if result.status != "ok":
        return
    summary = (result.summary or "").strip()
    if not summary:
        return
    lowered = summary.lower()
    if lowered in {
        "worker session reserved",
        "completed delegated task",
        "delegated opencode task completed.",
    }:
        return
    if lowered.startswith("delegated ") and lowered.endswith(" task completed."):
        return

    repo_name = Path(record.repo_path or task.repo_path or ".").name.strip().lower()
    if not repo_name:
        return

    resolved = resolve_worker_summary_target(record.repo_path or task.repo_path or "", repo_name)
    if resolved is None:
        return
    target_path, scope = resolved
    mode_value = (task.mode or record.mode or "").strip().lower() or "review"
    worker_name = (result.worker or record.selected_worker or record.requested_worker or "worker").strip()
    candidate_summary = f"{worker_name}/{mode_value}: {summary}"
    content = f"- Worker ({worker_name}/{mode_value}): {summary}\\n"

    try:
        from archon import memory as memory_store  # local import to keep session_store decoupled

        memory_store.inbox_add(
            kind="worker_summary",
            scope=scope,
            summary=candidate_summary,
            source=f"worker_session:{record.session_id}",
            confidence="medium",
            target_path=target_path,
            content=content,
        )
    except Exception:
        return


def resolve_worker_summary_target(repo_path: str, repo_name: str) -> tuple[str, str] | None:
    """Resolve a project memory target conservatively to avoid misfiling summaries."""
    normalized_repo = (repo_name or "").strip().lower()
    if not normalized_repo:
        return None
    default_target = (f"projects/{normalized_repo}.md", f"project:{normalized_repo}")

    try:
        from archon import memory as memory_store

        hits = memory_store.lookup(normalized_repo, limit=10)
    except Exception:
        hits = []

    project_hits = [h for h in hits if str(h.get("kind", "")).lower() == "project"]
    if project_hits:
        exact_stem_hits = []
        for hit in project_hits:
            hit_path = str(hit.get("path", "")).strip()
            if Path(hit_path).stem.lower() == normalized_repo:
                exact_stem_hits.append(hit)
        if len(exact_stem_hits) == 1:
            hit = exact_stem_hits[0]
            return str(hit.get("path", "")), str(hit.get("scope", f"project:{normalized_repo}"))
        if len(project_hits) == 1:
            hit = project_hits[0]
            return str(hit.get("path", "")), str(hit.get("scope", f"project:{normalized_repo}"))
        # Ambiguous project mapping: skip capture instead of writing to the wrong canonical file.
        return None

    if normalized_repo in _GENERIC_REPO_NAMES:
        return None
    return default_target

