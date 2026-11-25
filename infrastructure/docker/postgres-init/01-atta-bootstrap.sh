#!/bin/sh
# First-start initialisation inside the pgvector container: application role, database, pgvector.
set -eu
# Two roles (DECISIONS D-024): atta = schema owner for migrations, atta_app = least-privileged runtime role.
psql -v ON_ERROR_STOP=1 -U postgres -v migration_password="'${ATTA_DATABASE_MIGRATION_PASSWORD}'" \
     -v app_password="'${ATTA_DATABASE_PASSWORD}'" -f /atta/bootstrap.sql
