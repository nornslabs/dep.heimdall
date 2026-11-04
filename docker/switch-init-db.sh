#!/bin/sh
# Creates the Switch database and its two roles inside Heimdall's Postgres. Idempotent: it runs on
# every `docker compose up` (unlike postgres-init.sh, which only runs on an empty volume).
#
# switch_owner owns the schema; switch_app is the unprivileged role Switch serves requests as,
# because row-level security does not apply to a table's owner. Switch grants switch_app its table
# rights itself after migrating, so this script only has to make the roles exist.
set -eu

psql -v ON_ERROR_STOP=1 --host postgres --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v owner_password="$SWITCH_DB_OWNER_PASSWORD" -v app_password="$SWITCH_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE switch_owner LOGIN PASSWORD %L', :'owner_password')
  WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'switch_owner') \gexec
SELECT format('CREATE ROLE switch_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS', :'app_password')
  WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'switch_app') \gexec
-- Re-apply passwords so a changed .env takes effect on an existing volume.
ALTER ROLE switch_owner PASSWORD :'owner_password';
ALTER ROLE switch_app PASSWORD :'app_password';
SELECT 'CREATE DATABASE switch OWNER switch_owner'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'switch') \gexec
SQL
