-- EventPulse AI - private schema for the cloud database (PostgreSQL only; the desktop app's
-- SQLite database is not affected). Run ONCE per database, as an administrator, after
-- create_app_role.sql (Supabase: the "postgres" user in the SQL editor).
--
-- Why: Supabase's Data API exposes tables in the "public" schema to its anon/authenticated
-- roles. EventPulse keeps every table (including password hashes and session hashes) in its
-- own schema, owned by eventpulse_app, which no API role can use. The application refuses to
-- run in "public" on a non-local server (core/db/postgres.check_schema).
--
-- The application creates its tables itself on first start (migrations), inside this schema.

CREATE SCHEMA IF NOT EXISTS eventpulse AUTHORIZATION eventpulse_app;

-- Nobody but the owner may use the schema.
REVOKE ALL ON SCHEMA eventpulse FROM PUBLIC;

-- Supabase API roles (and any similar ones), if they exist on this server: no access at all,
-- now or to objects created later.
DO $$
DECLARE r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role', 'authenticator'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA eventpulse FROM %I', r);
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE eventpulse_app IN SCHEMA eventpulse '
                           'REVOKE ALL ON TABLES FROM %I', r);
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE eventpulse_app IN SCHEMA eventpulse '
                           'REVOKE ALL ON SEQUENCES FROM %I', r);
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE eventpulse_app IN SCHEMA eventpulse '
                           'REVOKE ALL ON FUNCTIONS FROM %I', r);
        END IF;
    END LOOP;
END $$;

-- Functions are executable by PUBLIC by default in PostgreSQL. That default is global, so it can
-- only be removed WITHOUT "IN SCHEMA" (a per-schema REVOKE has no effect on it). The real barrier
-- is the schema itself: without USAGE on "eventpulse" no other role can reach any function or
-- table in it. Skipped with a notice where the administrator may not change eventpulse_app's
-- defaults (some hosted providers).
DO $$
BEGIN
    ALTER DEFAULT PRIVILEGES FOR ROLE eventpulse_app REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'Could not change default function privileges of eventpulse_app (%); '
                 'schema USAGE still blocks other roles.', SQLERRM;
END $$;

-- Note: the application never creates anything in "public". PostgreSQL 15+ does not grant
-- CREATE on "public" to ordinary roles; check that with
--   SELECT has_schema_privilege('eventpulse_app', 'public', 'CREATE');   -- expected: false
