#!/usr/bin/env bash
# Prepare a Postgres database for agentic-memory: the database, the login user the service
# connects as (owner of the database), the pgvector and pg_trgm extensions, and the
# NOLOGIN role that query_deep_memory_sql runs as. Run it once per database as an admin;
# migrations then run as the new user and create no roles.
#
#   ADMIN_URL=postgresql://postgres:<password>@db.example.com:5432/postgres bin/create-db-user.sh
#
# Environment:
#   ADMIN_URL        admin connection: a superuser, or a role with CREATEROLE and CREATEDB
#                    where pgvector is installed. Empty: libpq defaults (PGHOST, PGUSER, …),
#                    which is how the Docker image's init step runs it.
#   APP_DB_NAME      database to create                  (default agentic_memory)
#   APP_DB_USER      login user of the service           (default agentic_memory_app)
#   APP_DB_PASSWORD  its password. Empty: a new user gets a generated one, printed once;
#                    an existing user keeps its password.
#   SQL_READER_ROLE  role of query_deep_memory_sql       (default agentic_memory_sql_reader)
#
# Safe to re-run: existing objects are kept; a given APP_DB_PASSWORD is applied.
set -euo pipefail

APP_DB_NAME="${APP_DB_NAME:-agentic_memory}"
APP_DB_USER="${APP_DB_USER:-agentic_memory_app}"
APP_DB_PASSWORD="${APP_DB_PASSWORD:-}"
SQL_READER_ROLE="${SQL_READER_ROLE:-agentic_memory_sql_reader}"

fail() { echo "✗ $*" >&2; exit 1; }

for name in "$APP_DB_NAME" "$APP_DB_USER" "$SQL_READER_ROLE"; do
  [[ "$name" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || fail "'$name' is not a plain identifier ([a-z_][a-z0-9_]*)"
done
command -v psql >/dev/null || fail "psql not found"

psql_admin() {
  PGOPTIONS="${PGOPTIONS:-} -c client_min_messages=warning" psql ${ADMIN_URL:+"$ADMIN_URL"} -X -q -v ON_ERROR_STOP=1 -At \
    -v app_db="$APP_DB_NAME" -v app_user="$APP_DB_USER" -v reader="$SQL_READER_ROLE" "$@"
}

user_exists=$(psql_admin -c "SELECT 1 FROM pg_roles WHERE rolname = '$APP_DB_USER'")
db_owner=$(psql_admin -c "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '$APP_DB_NAME'")
if [ -n "$db_owner" ] && [ "$db_owner" != "$APP_DB_USER" ]; then
  fail "database $APP_DB_NAME exists and belongs to $db_owner; pick another APP_DB_NAME"
fi

generated=0
if [ -z "$APP_DB_PASSWORD" ] && [ -z "$user_exists" ]; then
  if command -v openssl >/dev/null; then
    APP_DB_PASSWORD=$(openssl rand -base64 36 | tr -d '/+=\n')
  elif command -v python3 >/dev/null; then
    APP_DB_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  else
    fail "set APP_DB_PASSWORD (no openssl or python3 to generate one)"
  fi
  generated=1
fi

# Passwords travel as psql variables, quoted by format(%L); they never reach a shell command.
psql_admin -v app_pw="$APP_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_pw')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'app_user', :'app_pw')
 WHERE :'app_pw' <> '' \gexec
SELECT format('CREATE ROLE %I NOLOGIN', :'reader')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'reader') \gexec
SELECT format('GRANT %I TO %I', :'reader', :'app_user') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_user')
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'app_db') \gexec
\connect :"app_db"
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'reader') \gexec
SQL

echo "✓ database $APP_DB_NAME · user $APP_DB_USER · reader role $SQL_READER_ROLE"
if [ "$generated" = 1 ]; then
  password="$APP_DB_PASSWORD"
  echo "! New password for $APP_DB_USER, shown only now. Keep it in your secret store."
elif [ -n "$APP_DB_PASSWORD" ]; then
  password="<APP_DB_PASSWORD>"
else
  password="<existing password>"
fi
echo "Set in the service's .env.local (replace HOST:PORT with how the service reaches the database):"
echo "  DATABASE_URL=postgresql+asyncpg://$APP_DB_USER:$password@HOST:PORT/$APP_DB_NAME"
echo "  DATABASE_URL_SYNC=postgresql+psycopg://$APP_DB_USER:$password@HOST:PORT/$APP_DB_NAME"
if [ "$SQL_READER_ROLE" != "agentic_memory_sql_reader" ]; then
  echo "  SQL_READER_ROLE=$SQL_READER_ROLE"
fi
