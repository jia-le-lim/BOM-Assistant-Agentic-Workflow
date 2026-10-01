"""Restore a real handover archive into a newly created disposable database.
Production rows are read only. Only the generated verification database is dropped.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "supabase"))
import manage
from check_live_restore import TestDatabase

def fingerprints(deployment):
    names = deployment.sql("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;").splitlines()
    result = {}
    for name in names:
        quoted = '"' + name.replace('"', '""') + '"'
        value = deployment.sql("SELECT count(*)::text || '|' || md5(coalesce(string_agg(h, '' ORDER BY h),'')) "
            "FROM (SELECT md5(row_to_json(t)::text) h FROM public." + quoted + " t) s;")
        count, checksum = value.split("|")
        result[name] = {"rows": int(count), "content_md5": checksum}
    return result

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--package", required=True, type=Path)
    p.add_argument("--runtime", type=Path, default=Path("C:/ProgramData/BOM-Supabase/runtime"))
    args = p.parse_args()
    package = args.package.resolve()
    archive = package / "PRIVATE/database/application-public.dump"
    report = package / "inventory/restore-verification.json"
    if report.exists():
        raise RuntimeError("Existing verification report retained; choose another report/package")
    manage.ERROR_ROOT = package / "PRIVATE/capture-logs"
    source = manage.Deployment(args.runtime)
    name = "bom_handover_verify_" + secrets.token_hex(8)
    target = TestDatabase(args.runtime, name)
    created = False
    result = {"started_utc":datetime.now(timezone.utc).isoformat(),"database":name,"passed":False}
    try:
        manage.verify_manifest(archive)
        before = fingerprints(source)
        source.sql(f'CREATE DATABASE "{name}";')
        created = True
        target.restore(archive)
        restored = fingerprints(target)
        after = fingerprints(source)
        result.update(source_before=before, restored=restored, source_after=after,
                      source_stable=before == after, content_matches=restored == before)
        result["rls_and_ownership_ok"] = target.sql(
            "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND "
            "(NOT rowsecurity OR tableowner<>'service_role');") == "0"
        result["rpc_is_invoker"] = target.sql("SELECT prosecdef FROM pg_proc WHERE "
            "oid='public.exec_sql(text,jsonb,boolean)'::regprocedure;") == "f"
        result["anonymous_rpc_denied"] = all(target.sql(
            f"SELECT has_function_privilege('{role}','public.exec_sql(text,jsonb,boolean)','EXECUTE');") == "f"
            for role in ("anon", "authenticated"))
        result["service_rpc_allowed"] = target.sql(
            "SELECT has_function_privilege('service_role','public.exec_sql(text,jsonb,boolean)','EXECUTE');") == "t"
        try:
            target.restore(archive)
            result["nonempty_restore_refused"] = False
        except RuntimeError as error:
            result["nonempty_restore_refused"] = "EMPTY" in str(error)
        result["passed"] = all(result[k] for k in ("source_stable","content_matches","rls_and_ownership_ok",
            "rpc_is_invoker","anonymous_rpc_denied","service_rpc_allowed","nonempty_restore_refused"))
        if not result["passed"]:
            raise RuntimeError("Restore verification failed; inspect private report")
    finally:
        if created:
            source.sql(f'DROP DATABASE "{name}" WITH (FORCE);')
            result["disposable_database_removed"] = True
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(f"Restore verified: {len(restored)} tables, {sum(t['rows'] for t in restored.values())} rows; "
          "content fingerprints, ownership, RLS and RPC checks passed. Temporary database removed.")

if __name__ == "__main__":
    main()

