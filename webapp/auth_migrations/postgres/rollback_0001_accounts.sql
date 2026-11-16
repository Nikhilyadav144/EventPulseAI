-- Reverses postgres/0001_accounts.sql. Removes ONLY the web authentication tables.
-- Events, students, registrations, attendance and predictions are not touched.
DROP TABLE IF EXISTS auth_audit;
DROP TABLE IF EXISTS auth_sessions;
DROP TABLE IF EXISTS users;
DROP FUNCTION IF EXISTS ep_users_role_immutable();
DROP TABLE IF EXISTS web_auth_migrations;
