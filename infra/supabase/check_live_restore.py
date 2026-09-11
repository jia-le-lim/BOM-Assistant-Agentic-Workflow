"""Opt-in restore rehearsal using isolated, disposable local databases.

Runs against an already started deployment. Never connects to Cloud or changes
the postgres application's schema. Requires Python 3.11+ and Docker access.
"""
import argparse
from pathlib import Path
import secrets

from manage import Deployment, HERE, TABLES


class TestDatabase(Deployment):
    def __init__(self, root, database):
        super().__init__(root)
        self.database = database

    def compose(self, *args, **kwargs):
        args = list(args)
        for i in range(len(args) - 1):
            if args[i] == "-d" and args[i + 1] == "postgres":
                args[i + 1] = self.database
        return super().compose(*args, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=HERE / "runtime")
    args = parser.parse_args()
    base = Deployment(args.root)
    suffix = secrets.token_hex(5)
    source = TestDatabase(args.root, "bom_test_source_" + suffix)
    target = TestDatabase(args.root, "bom_test_target_" + suffix)
    created = []
    try:
        for deployment in (source, target):
            base.sql(f'CREATE DATABASE "{deployment.database}";')
            created.append(deployment.database)
        source.sql("CREATE SCHEMA extensions; CREATE EXTENSION vector WITH SCHEMA extensions;")
        for table in sorted(TABLES):
            source.sql(f'CREATE TABLE public."{table}" '
                       '(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, '
                       'payload jsonb NOT NULL, embedding extensions.vector(3));')
        source.sql("INSERT INTO public.batches(payload, embedding) "
                   "VALUES ('{\"note\":\"quote: '' dollar: $1\"}', '[1,2,3]');")
        source.sql("CREATE FUNCTION public.exec_sql(q text, params jsonb DEFAULT '[]', "
                   "want_rows boolean DEFAULT true) RETURNS jsonb LANGUAGE plpgsql "
                   "SECURITY INVOKER AS $$ DECLARE result jsonb; BEGIN "
                   "EXECUTE 'SELECT jsonb_agg(t) FROM (' || q || ') t' INTO result; "
                   "RETURN result; END $$;")
        archive = base.root / "rehearsals" / f"synthetic-{suffix}.dump"
        source.backup(archive)
        target.restore(archive)
        assert target.sql("SELECT count(*) FROM public.batches;") == "1"
        assert target.sql("SELECT embedding::text FROM public.batches;") == "[1,2,3]"
        assert target.sql("SELECT payload->>'note' FROM public.batches;") == "quote: ' dollar: $1"
        assert target.sql("INSERT INTO public.batches(payload) VALUES ('{}') RETURNING id;").splitlines()[0] == "2"
        assert target.sql("SELECT count(*) FROM pg_tables WHERE schemaname='public' "
                          "AND (NOT rowsecurity OR tableowner <> 'service_role');") == "0"
        for role in ("anon", "authenticated"):
            assert target.sql(f"SELECT has_function_privilege('{role}', "
                              "'public.exec_sql(text,jsonb,boolean)', 'EXECUTE');") == "f"
        assert target.sql("SELECT has_function_privilege('service_role', "
                          "'public.exec_sql(text,jsonb,boolean)', 'EXECUTE');") == "t"
        assert target.sql("SELECT prosecdef FROM pg_proc WHERE oid="
                          "'public.exec_sql(text,jsonb,boolean)'::regprocedure;") == "f"
        try:
            target.restore(archive)
        except RuntimeError as exc:
            assert "EMPTY" in str(exc)
        else:
            raise AssertionError("Restore unexpectedly accepted an existing application database.")
        print("Live restore rehearsal passed: data, vectors, sequence continuity, ownership, "
              "RLS, RPC permissions, and populated-target refusal.")
    finally:
        # Only drop databases whose successful creation was recorded in this run.
        for database in reversed(created):
            base.sql(f'DROP DATABASE "{database}" WITH (FORCE);')
        print("Disposable rehearsal databases removed; application database unchanged.")


if __name__ == "__main__":
    main()
