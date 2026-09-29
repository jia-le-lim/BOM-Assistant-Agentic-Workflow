-- Local BOM deployment only. Run after importing public objects as service_role.
-- The current backend performs additive DDL at startup, so it must own its tables.
-- Browser roles must never execute the application's arbitrary-SQL RPC.
REVOKE ALL ON SCHEMA public FROM PUBLIC, anon, authenticated;
GRANT USAGE, CREATE ON SCHEMA public TO service_role;
GRANT USAGE ON SCHEMA extensions TO service_role;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
GRANT EXECUTE ON FUNCTION public.exec_sql(text, jsonb, boolean) TO service_role;
ALTER FUNCTION public.exec_sql(text, jsonb, boolean) SECURITY INVOKER;
ALTER FUNCTION public.exec_sql(text, jsonb, boolean) SET search_path = public, extensions;
ALTER DEFAULT PRIVILEGES FOR ROLE service_role IN SCHEMA public
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE service_role IN SCHEMA public
    REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE service_role IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM anon, authenticated;
DO $block$
DECLARE t record;
BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.tablename);
    END LOOP;
END
$block$;
NOTIFY pgrst, 'reload schema';
