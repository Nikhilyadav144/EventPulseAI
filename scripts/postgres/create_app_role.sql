-- EventPulse AI - create the application's own database role (cloud/PostgreSQL only; the desktop
-- app never uses this). Run ONCE per server as an administrator (Supabase: the "postgres" user
-- in the SQL editor). The password is never written here:
--   psql:      psql ... -v app_password="$(cat secret-file)" -f create_app_role.sql
--   Supabase:  paste the statement below, replacing :'app_password' with a quoted strong password.
--
-- The role can log in and owns only the private "eventpulse" schema (setup_private_schema.sql).
-- It is not a superuser and cannot create databases or roles.
CREATE ROLE eventpulse_app LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 20;

-- Every connection of this role uses the private schema first. A role-level default works
-- through connection poolers (no per-session SET needed).
ALTER ROLE eventpulse_app SET search_path = eventpulse;
