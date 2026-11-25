#!/bin/sh
# First-start initialisation inside the pgvector container: application role, database, pgvector.
set -eu
psql -v ON_ERROR_STOP=1 -U postgres -v app_password="'${ATTA_DATABASE_PASSWORD}'" -f /atta/bootstrap.sql
