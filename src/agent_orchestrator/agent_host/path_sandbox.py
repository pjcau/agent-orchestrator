"""Strict path containment for the agent-host client.

The existing :func:`agent_orchestrator.skills.filesystem._confine` is
intentionally *permissive* — it silently remaps escapes back under the
workspace. That's fine for trusted local agents but wrong when the path
arrives over the wire from a remote agent that the user does not control:
silent remapping hides a tampered request as a successful op and the
user never sees the attempted escape.

This module is the strict variant. ``enforce_workspace`` resolves
symlinks, refuses anything that crosses the workspace boundary, and
optionally refuses any symlink on the path at all (``follow_symlinks=False``,
the default).

Threats mitigated:

* Path traversal — ``../`` segments, absolute paths outside the workspace,
  Unicode/encoding tricks (the ``Path.resolve()`` normalises before
  the containment check).
* Symlink-out-of-workspace — a symlink in a workspace dir pointing at
  ``/etc/passwd`` is rejected at lookup time, not at I/O time.
* TOCTOU race on directory traversal — single ``resolve()`` snapshot is
  used for both the containment check and the returned path; subsequent
  I/O still has a window, mitigated at the I/O layer by ``O_NOFOLLOW``
  / file-descriptor reuse.
"""

from __future__ import annotations

from pathlib import Path


class PathOutsideWorkspaceError(ValueError):
    """Raised when a path resolves outside the configured workspace root.

    Carries the (sanitised) attempted path so the caller can log it
    without leaking the resolved absolute target.
    """


def enforce_workspace(
    workspace: Path,
    raw: str,
    *,
    follow_symlinks: bool = False,
) -> Path:
    """Resolve ``raw`` under ``workspace`` and ensure containment.

    Returns the resolved :class:`Path` if it is strictly under
    ``workspace`` (or equal to it). Raises
    :class:`PathOutsideWorkspaceError` on any escape. The workspace root
    must itself exist; if not the workspace is invalid and any operation
    against it would be wrong.

    ``follow_symlinks=False`` (default) refuses if *any* component on
    the path is a symlink — strict, but matches the agent-host threat
    model. Set to ``True`` only if the calling tool genuinely needs to
    follow symlinks (e.g. project layouts that pin generated dirs).
    """
    if not workspace.is_absolute():
        workspace = workspace.resolve()
    if not workspace.exists():
        raise PathOutsideWorkspaceError(f"workspace does not exist: {workspace}")

    raw_path = Path(raw)
    candidate = (workspace / raw_path) if not raw_path.is_absolute() else raw_path

    if not follow_symlinks:
        # Walk parents and reject any symlink component. We test on the
        # ``candidate`` rather than ``resolve()`` so the rejection
        # happens BEFORE the symlink could redirect us.
        cur = candidate
        while True:
            if cur == workspace or cur == cur.parent:
                break
            if cur.is_symlink():
                raise PathOutsideWorkspaceError(f"symlink on path is not allowed: {raw}")
            cur = cur.parent

    resolved = candidate.resolve()
    workspace_resolved = workspace.resolve()
    try:
        resolved.relative_to(workspace_resolved)
    except ValueError as e:
        raise PathOutsideWorkspaceError(f"path escapes workspace: {raw}") from e
    return resolved
