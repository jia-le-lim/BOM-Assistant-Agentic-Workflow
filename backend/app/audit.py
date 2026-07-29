"""Audit log writes -- PRD section 10: every write is authenticated and audit-logged."""

import json

from .db import Conn


def audit(conn: Conn, actor: dict, method: str, path: str,
          entity: str, entity_id: str, detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (user, role, method, path, entity, entity_id, detail) "
        "VALUES (?,?,?,?,?,?,?)",
        (actor.get("user"), actor.get("role"), method, path, entity, str(entity_id),
         json.dumps(detail or {}, default=str)),
    )
