"""Private settings, initialized once from the pre-isolation shared baseline.

The legacy tables remain read-only migration templates. All runtime reads and
writes use user_* tables. Initialization is atomic even on the REST transport,
whose calls each commit independently. Deleting a personal rule never restores
it from the template on the next request.
"""

from __future__ import annotations


def ensure_settings(conn, owner: str) -> None:
    if not owner or not owner.strip() or owner == "anonymous":
        raise ValueError("An account is required to access settings")
    if conn.execute("SELECT 1 FROM user_settings WHERE owner_user=?",
                    (owner,)).fetchone():
        return

    copies = [
        ("user_rule_config", "rule_version, config_json, active, updated_by, updated_at",
         "SELECT ?, rule_version, config_json, active, updated_by, updated_at "
         "FROM rule_config WHERE active=1 ORDER BY config_id DESC LIMIT 1"),
        ("user_machine_criticality_config",
         "pattern, criticality, service_level_target, set_by, confirmed_by, confirmed, updated_at",
         "SELECT ?, pattern, criticality, service_level_target, set_by, confirmed_by, "
         "confirmed, updated_at FROM machine_criticality_config"),
        ("user_part_category_config",
         "pattern, category, priority, set_by, confirmed_by, confirmed, updated_at",
         "SELECT ?, pattern, category, priority, set_by, confirmed_by, confirmed, "
         "updated_at FROM part_category_config"),
        ("user_dormant_rule_config",
         "scope, match_key, policy, fixed_qty, set_by, confirmed_by, confirmed, updated_at",
         "SELECT ?, scope, match_key, policy, fixed_qty, set_by, confirmed_by, "
         "confirmed, updated_at FROM dormant_rule_config"),
    ]
    claim = ("INSERT INTO user_settings (owner_user) VALUES (?) "
             "ON CONFLICT(owner_user) DO NOTHING RETURNING owner_user")
    if conn.is_postgres:
        # Claim and all copies either commit together or leave no account.
        ctes = [f"claimed AS ({claim})"]
        for index, (table, columns, source) in enumerate(copies):
            ctes.append(
                f"copy_{index} AS (INSERT INTO {table} (owner_user, {columns}) "
                f"SELECT baseline.* FROM ({source}) baseline "
                "WHERE EXISTS (SELECT 1 FROM claimed) RETURNING owner_user)")
        conn.execute("WITH " + ", ".join(ctes) + " SELECT owner_user FROM claimed",
                     (owner,) * (len(copies) + 1))
    else:
        if conn.execute(claim, (owner,)).fetchone():
            for table, columns, source in copies:
                conn.execute(f"INSERT INTO {table} (owner_user, {columns}) {source}",
                             (owner,))
    conn.commit()


def batch_owner(conn, batch_id: int) -> str:
    row = conn.execute("SELECT uploaded_by FROM batches WHERE batch_id=?",
                       (batch_id,)).fetchone()
    if row is None or not row["uploaded_by"]:
        raise ValueError(f"batch {batch_id} has no account owner")
    return row["uploaded_by"]
