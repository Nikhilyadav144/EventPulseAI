-- Reverses 0001_accounts.sql. Removes ONLY the web authentication tables (accounts, sessions,
-- auth audit). Events, students, registrations, attendance, predictions and MLflow history are
-- not touched. Run through:  .venv/bin/python -m webapp.provision_admin --rollback-auth
DROP TRIGGER IF EXISTS trg_users_role_immutable;
DROP INDEX IF EXISTS ux_users_single_admin;
DROP INDEX IF EXISTS ix_auth_sessions_user;
DROP TABLE IF EXISTS auth_audit;
DROP TABLE IF EXISTS auth_sessions;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS web_auth_migrations;          -- 0001 is the first auth migration
