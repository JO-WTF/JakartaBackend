# Jakarta Backend API

This repository contains the FastAPI service that powers DN (Delivery Note) tracking and Google Sheet synchronisation for the Jakarta operations team. The codebase has been structured around clear domains (routers, services, tasks, schemas, utilities) to keep business logic isolated from transport concerns and to make the synchronisation workflow testable.

## Project layout

| Path | Purpose |
| --- | --- |
| `app/main.py` | FastAPI entry point responsible for wiring middleware, routers, and lifecycle hooks. |
| `app/routers/` | Domain-specific routers that declare HTTP endpoints and orchestrate validation. |
| `app/services/` | Application services that hold business logic (e.g. DN CRUD, Google Sheet sync). |
| `app/tasks/` | Background schedulers and async jobs such as the periodic sheet synchroniser. |
| `app/utils/` | Shared helpers for normalization and file handling. |
| `tests/` | Unit tests covering routers, services, and utilities. |

## Running locally

1. Create a Python virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Provide the required environment variables (see the next section). During local development you can create a `.env` file and rely on your IDE or a process manager such as `honcho` to load it.

3. Start the API with Uvicorn:

   ```bash
   uvicorn app.main:app --reload --port 10000
   ```

4. Visit `http://localhost:10000/docs` to explore the automatically generated OpenAPI documentation.

## Background sheet synchronisation

The Google Sheet synchronisation job runs inside the same process through APScheduler. On application startup a periodic task is registered that invokes `app.services.dn_sync.perform_sync_with_logging()` according to `DN_SHEET_SYNC_INTERVAL_SECONDS`. The last run status and any synchronised DN numbers are persisted to the database and can be queried through the `/api/sync` endpoints.

To trigger a manual sync (for example after rotating credentials) you can call the `/api/sync/manual` endpoint. Logs are written both to standard output and to the file referenced by `DN_SYNC_LOG_PATH`.

## Testing

Unit tests rely on an in-memory SQLite database and can be executed with:

```bash
pytest
```

The fixture bootstrap in `tests/conftest.py` keeps database schema and configuration isolated per test run.

## Environment configuration

The application relies on environment variables that are loaded via `app.settings.Settings`. Configure them through your deployment platform or a local `.env` file that is excluded from version control.

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy connection string (required). |
| `GOOGLE_API_KEY` | API key used to access Google Sheets (required for DN sync). |
| `GOOGLE_SHEET_URL` | URL of the DN tracking Google Sheet (required for DN sync). |
| `DN_SHEET_SYNC_INTERVAL_SECONDS` | Interval, in seconds, for the background sheet sync scheduler (defaults to `300`). |
| `DN_SYNC_LOG_PATH` | Optional path for DN sync log output (defaults to `/tmp/dn_sync.log`). |
| `STORAGE_DRIVER` and related `S3_*` vars | Configure file storage (defaults to disk storage). |

Ensure secrets such as `GOOGLE_API_KEY` are never committed to the repository; instead, provide them via environment configuration in each deployment environment.
