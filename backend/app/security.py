"""RBAC stub.

Production requires Intel SSO / Entra ID (PRD section 10). A local scaffold
cannot integrate that, so identity arrives as plain headers:

    X-User: <username>       X-Role: <role>

This is NOT authentication -- it is a role-shaped placeholder so that route
authorization, audit attribution and the approval workflow could be built and
tested now and rewired to Entra ID later. Flagged in Backend_Scaffold_Notes.md.
"""

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
        return {"user": x_user, "role": x_role}

    return dep


def any_role():
    """Reads are open to every known role in the MVP (all data is internal)."""
    return require_role(*ROLES)
