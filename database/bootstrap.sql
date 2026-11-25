-- Run once as a PostgreSQL superuser. Creates TWO roles (DECISIONS D-024):
--   atta      migration/owner role: owns the database and every schema object; used only by `backend.database migrate`
--   atta_app  runtime role used by the API and service manager: data access only (no DDL, no ownership,
--             no BYPASSRLS, cannot touch migration records). Privileges are granted table by table in migrations.
-- psql -U postgres -v migration_password=... -v app_password=... -f database/bootstrap.sql   (raw values; quoted safely below)
SELECT 'CREATE ROLE atta LOGIN PASSWORD ' || quote_literal(:'migration_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atta') \gexec
SELECT 'CREATE ROLE atta_app LOGIN PASSWORD ' || quote_literal(:'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atta_app') \gexec
ALTER ROLE atta NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS;
ALTER ROLE atta_app NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS NOINHERIT;
SELECT 'CREATE DATABASE atta OWNER atta'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'atta') \gexec
REVOKE CREATE, TEMPORARY ON DATABASE atta FROM PUBLIC;
GRANT CONNECT ON DATABASE atta TO atta_app;
\connect atta
CREATE EXTENSION IF NOT EXISTS vector;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
