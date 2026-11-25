# Database

PostgreSQL 16 + pgvector. `migrations/` holds ordered, checksummed SQL migrations applied by
`python -m backend.database migrate`. `policies/` holds row-level-security policies, applied after migrations.

`bootstrap.sql` must be run once by a database superuser (pgvector is not a trusted extension):

    psql -U postgres -v app_password="'...'" -f database/bootstrap.sql

The Docker Compose stack (`infrastructure/docker/compose.yaml`) does this automatically on first start.
An applied migration is never edited: the runner refuses to continue if a recorded checksum changes.
