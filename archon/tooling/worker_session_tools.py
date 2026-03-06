"""Worker session tool registration facade."""

from .worker_session_action_tools import register_worker_session_action_tools
from .worker_session_query_tools import register_worker_session_query_tools


def register_worker_session_tools(registry, ns):
    """Register worker session tools via extracted query/action modules.

    Returns closures needed by delegate tools (currently `worker_send`).
    """
    register_worker_session_query_tools(registry, ns)
    return register_worker_session_action_tools(registry, ns)

