-- Run once as a PostgreSQL superuser. Creates the application role and database and installs pgvector.
-- psql -U postgres -v app_password="'secret'" -f database/bootstrap.sql
SELECT 'CREATE ROLE atta LOGIN PASSWORD ' || :app_password
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atta') \gexec
SELECT 'CREATE DATABASE atta OWNER atta'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'atta') \gexec
\connect atta
CREATE EXTENSION IF NOT EXISTS vector;
ALTER ROLE atta NOSUPERUSER NOBYPASSRLS;
