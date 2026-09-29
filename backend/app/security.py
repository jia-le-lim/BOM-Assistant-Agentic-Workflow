"""Role and workspace ownership authorization behind the trusted gateway.

The frontend verifies the pilot session and replaces browser identity headers:

    X-User: <username>       X-Role: <role>

These headers are not authentication on their own. Keep the backend private
(the pilot binds it to loopback); browsers must use the authenticated frontend.
Production SSO / Entra ID remains a separate authentication integration.
"""

import os

from fastapi import Header, HTTPException

ROLES = {"engineer", "senior", "planner", "admin", "it", "auditor", "viewer"}

REVIEW_ROLES = {"engineer", "senior", "admin"}
APPROVE_ROLES = {"senior", "admin"}
UPLOAD_ROLES = {"engineer", "senior", "admin", "it"}
CONFIG_WRITE_ROLES = {"admin"}
EXPORT_ROLES = {"engineer", "senior", "planner", "admin", "it"}


def require_role(*allowed: str):
    allowed_set = set(allowed)

    def dep(x_user: str = Header(default="anonymous"),
            x_role: str = Header(default="viewer")):
        if x_role not in ROLES:
            raise HTTPException(status_code=400, detail=f"Unknown role '{x_role}'")
        if x_role not in allowed_set:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{x_role}' not permitted; needs one of {sorted(allowed_set)}",
            )
        if not x_user.strip() or x_user == "anonymous":
            raise HTTPException(401, "Sign in to continue.")
        return {"user": x_user, "role": x_role}

    return dep


def any_role():
    """Role gate only; workspace routes must also check ownership."""
    return require_role(*ROLES)


def can_read_all_workspaces(actor: dict) -> bool:
    """Explicit server-managed read access; an admin role alone is insufficient."""
    user = actor.get("user")
    readers = {name.strip() for name in
               os.environ.get("BOM_WORKSPACE_READ_ALL_USERS", "").split(",") if name.strip()}
    return bool(user and user != "anonymous" and user in readers)


def require_workspace(conn, batch_id: int | None, actor: dict, *, allow_shared: bool = False):
    """Return an authorized workspace, hiding both foreign and missing IDs.

    Administrator is a capability role, not an ownership bypass: pilot users
    all have that role. Read paths explicitly opt into shared viewing; writes
    retain ownership checks. The gateway supplies the authenticated user.
    """
    user = actor.get("user")
    if not user or user == "anonymous":
        raise HTTPException(401, "Sign in to continue.")
    workspace = conn.execute(
        "SELECT * FROM batches WHERE batch_id=? AND (uploaded_by=? OR ?=1)",
        (batch_id, user, int(allow_shared and can_read_all_workspaces(actor))),
    ).fetchone()
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    return workspace
