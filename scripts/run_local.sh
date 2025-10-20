#!/usr/bin/env bash
# run_local.sh - start FastAPI app against a local SQLite database

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_DB_FILE="$ROOT_DIR/tmp/local_dev.db"
DEFAULT_ENV_FILE="$ROOT_DIR/.env.local"
HOST="127.0.0.1"
PORT="8000"
RELOAD=1
DB_FILE="$DEFAULT_DB_FILE"
ENV_FILE="$DEFAULT_ENV_FILE"
ALLOWED_ORIGINS_OVERRIDE="*"

usage() {
    cat <<'EOF'
Usage: scripts/run_local.sh [options]

Options:
  --db PATH         Path to the SQLite database file (default: tmp/local_dev.db)
  --host HOST       Host interface for uvicorn (default: 127.0.0.1)
  --port PORT       Port for uvicorn (default: 8000)
  --env-file PATH   Optional dotenv file to source before starting (default: .env.local if present)
  --origins CSV     Override CORS origins, e.g. "http://localhost:3000,http://localhost:5173"
  --no-reload       Disable uvicorn auto-reload
  -h, --help        Show this help message

Any variables already defined in the environment take precedence over defaults.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db)
            DB_FILE="$2"
            shift 2
            ;;
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        --origins)
            ALLOWED_ORIGINS_OVERRIDE="$2"
            shift 2
            ;;
        --no-reload)
            RELOAD=0
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            exit 1
            ;;
    esac
done

# Resolve DB file to absolute path
DB_FILE="$(python - <<'PY' "$DB_FILE"
import pathlib, sys
path = pathlib.Path(sys.argv[1]).expanduser().resolve()
print(path.as_posix())
PY
)"

# Load extra environment variables from dotenv file if it exists
if [[ -f "$ENV_FILE" ]]; then
    echo "Loading environment variables from $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

UPLOADS_DIR="${STORAGE_DISK_PATH:-$ROOT_DIR/tmp/uploads}"
mkdir -p "$(dirname "$DB_FILE")"
mkdir -p "$UPLOADS_DIR"

# Compute SQLite URL (SQLAlchemy expects sqlite:///absolute/path notation)
DATABASE_URL="sqlite:///$DB_FILE"
APP_ENV="${APP_ENV:-local}"
STORAGE_DRIVER="${STORAGE_DRIVER:-disk}"
STORAGE_DISK_PATH="${STORAGE_DISK_PATH:-$UPLOADS_DIR}"
DEFAULT_ALLOWED_ORIGINS="*"
if [[ -n "$ALLOWED_ORIGINS_OVERRIDE" ]]; then
    ALLOWED_ORIGINS="$ALLOWED_ORIGINS_OVERRIDE"
fi
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-$DEFAULT_ALLOWED_ORIGINS}"
PYTHONPATH="$ROOT_DIR:${PYTHONPATH:-}"

export DATABASE_URL APP_ENV STORAGE_DRIVER STORAGE_DISK_PATH ALLOWED_ORIGINS PYTHONPATH

echo "Starting JakartaBackend locally"
echo "  Database file : $DB_FILE"
echo "  DATABASE_URL  : $DATABASE_URL"
echo "  Storage path  : $STORAGE_DISK_PATH"
echo "  Host/port     : $HOST:$PORT"
echo "  Allowed CORS  : $ALLOWED_ORIGINS"
echo "  Reload        : $([ "$RELOAD" -eq 1 ] && echo enabled || echo disabled)"
echo ""

cmd=(uvicorn app.main:app --host "$HOST" --port "$PORT")
if [[ $RELOAD -eq 1 ]]; then
    cmd+=(--reload)
fi

exec "${cmd[@]}"
