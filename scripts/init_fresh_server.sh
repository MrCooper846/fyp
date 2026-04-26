#!/usr/bin/env bash
set -euo pipefail

# Bootstrap a fresh Ubuntu/Debian server for the GC contacts crawler.
#
# What it does:
# - installs system packages for Python and Postgres
# - creates a local virtualenv and installs requirements.txt
# - creates a Postgres role/database for the app
# - applies db/migrations/*.sql in lexical order
# - writes a local .env if one does not already exist
#
# Safe defaults:
# - does not overwrite an existing .env
# - does not drop or recreate an existing database
# - generates a random DB password unless GC_DB_PASSWORD is provided

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${GC_VENV_DIR:-$ROOT_DIR/.venv}"
DB_NAME="${GC_DB_NAME:-gc_contacts}"
DB_USER="${GC_DB_USER:-gc_contacts_app}"
DB_HOST="${GC_DB_HOST:-localhost}"
DB_PORT="${GC_DB_PORT:-5432}"
DB_PASSWORD="${GC_DB_PASSWORD:-}"
MIGRATIONS_DIR="$ROOT_DIR/db/migrations"
REQ_FILE="$ROOT_DIR/requirements.txt"
ENV_FILE="$ROOT_DIR/.env"

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "Required command not found: $name" >&2
    exit 1
  fi
}

sudo_cmd() {
  if [[ "${EUID}" -eq 0 ]]; then
    if [[ "${1:-}" == "-u" ]]; then
      sudo "$@"
    else
      "$@"
    fi
  else
    sudo "$@"
  fi
}

postgres_cmd() {
  sudo_cmd -u postgres psql -v ON_ERROR_STOP=1 "$@"
}

generate_password() {
  python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32), end="")
PY
}

validate_identifier() {
  local label="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "$label must be a simple Postgres identifier: letters, numbers, underscores, not starting with a number." >&2
    echo "Received: $value" >&2
    exit 1
  fi
}

escape_sql_literal() {
  printf "%s" "$1" | sed "s/'/''/g"
}

install_system_packages() {
  echo "Installing system packages..."
  sudo_cmd apt-get update
  sudo_cmd apt-get install -y \
    ca-certificates \
    curl \
    git \
    python3 \
    python3-venv \
    python3-pip \
    postgresql \
    postgresql-contrib
}

install_python_requirements() {
  echo "Creating Python virtualenv at $VENV_DIR..."
  python3 -m venv "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  python -m pip install --upgrade pip
  python -m pip install -r "$REQ_FILE"
}

ensure_postgres_running() {
  echo "Ensuring Postgres is running..."
  if command -v systemctl >/dev/null 2>&1; then
    sudo_cmd systemctl enable postgresql
    sudo_cmd systemctl start postgresql
  else
    sudo_cmd service postgresql start
  fi
}

ensure_database() {
  if [[ -z "$DB_PASSWORD" ]]; then
    DB_PASSWORD="$(generate_password)"
  fi

  local escaped_user
  local escaped_password
  local escaped_db
  escaped_user="$(escape_sql_literal "$DB_USER")"
  escaped_password="$(escape_sql_literal "$DB_PASSWORD")"
  escaped_db="$(escape_sql_literal "$DB_NAME")"

  echo "Creating/updating Postgres role '$DB_USER' and database '$DB_NAME'..."

  postgres_cmd <<SQL
do \$\$
begin
  if not exists (select from pg_roles where rolname = '$escaped_user') then
    create role "$DB_USER" login password '$escaped_password';
  else
    alter role "$DB_USER" login password '$escaped_password';
  end if;
end
\$\$;
SQL

  if ! sudo_cmd -u postgres psql -tAc "select 1 from pg_database where datname = '$escaped_db'" | grep -q 1; then
    sudo_cmd -u postgres createdb -O "$DB_USER" "$DB_NAME"
  fi

  postgres_cmd -d "$DB_NAME" <<SQL
grant all privileges on database "$DB_NAME" to "$DB_USER";
alter schema public owner to "$DB_USER";
grant all on schema public to "$DB_USER";
SQL
}

apply_migrations() {
  echo "Applying migrations from $MIGRATIONS_DIR..."
  require_command psql

  local database_url
  database_url="postgresql://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"

  for migration in "$MIGRATIONS_DIR"/*.sql; do
    echo "  -> $(basename "$migration")"
    PGPASSWORD="$DB_PASSWORD" psql "$database_url" -v ON_ERROR_STOP=1 -f "$migration"
  done
}

write_env_file() {
  local database_url
  database_url="postgresql://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"

  if [[ -f "$ENV_FILE" ]]; then
    echo ".env already exists; not overwriting it."
    echo "Add these manually if needed:"
    echo "  DATABASE_URL=$database_url"
    echo "  POSTGRES_DUAL_WRITE=1"
    return
  fi

  cat > "$ENV_FILE" <<ENV
# Fill this in before running the crawler.
OPENAI_API_KEY=

DATABASE_URL=$database_url
POSTGRES_DUAL_WRITE=1
ENV

  chmod 600 "$ENV_FILE"
  echo "Wrote $ENV_FILE"
}

print_summary() {
  local database_url
  database_url="postgresql://$DB_USER:$DB_PASSWORD@$DB_HOST:$DB_PORT/$DB_NAME"

  cat <<EOF

Fresh server initialization complete.

Activate Python:
  source "$VENV_DIR/bin/activate"

Database:
  DB name: $DB_NAME
  DB user: $DB_USER
  DATABASE_URL=$database_url

Before running the crawler, set OPENAI_API_KEY in:
  $ENV_FILE

Smoke test once OPENAI_API_KEY is set:
  source "$VENV_DIR/bin/activate"
  python gc_contacts_cli.py nafsa IT --limit 1 --outfile smoke_contacts.csv --debug --debug-dir debug_logs/db_smoke_it
EOF
}

main() {
  if [[ ! -f "$REQ_FILE" ]]; then
    echo "requirements.txt not found at $REQ_FILE" >&2
    exit 1
  fi
  if [[ ! -d "$MIGRATIONS_DIR" ]]; then
    echo "Migration directory not found at $MIGRATIONS_DIR" >&2
    exit 1
  fi
  validate_identifier "GC_DB_NAME" "$DB_NAME"
  validate_identifier "GC_DB_USER" "$DB_USER"

  install_system_packages
  install_python_requirements
  ensure_postgres_running
  ensure_database
  apply_migrations
  write_env_file
  print_summary
}

main "$@"
