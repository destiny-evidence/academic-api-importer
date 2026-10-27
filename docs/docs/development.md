---
icon: lucide/terminal
description: Install, configure, run and deploy the API, the workers and the web UI; tooling, logging and operations notes.
---

# Development and operations

Notes for running the pieces behind the interface from source, changing them, and putting
them on a server. The [quickstart in the repository README](https://github.com/destiny-evidence/academic-api-importer#quickstart)
covers the shortest path to a running instance; this page is the long version.

## What runs

| Piece | Technology | Started with |
| --- | --- | --- |
| REST API | FastAPI + SQLModel, `academic_apis.api:api`; on startup it also re-queues tasks that were left unfinished | `uv run fastapi dev` |
| Retrieval workers | taskiq worker on a SQLite-backed queue | `uv run taskiq worker …` |
| Web interface | Vue 3 + Vite single-page app in `frontend/` | `npm run dev` |
| Database and queue | SQLite in `data/oracle.db` (WAL mode, tables created on first use) | — |
| Result files | `data/results/<query_id>/responses/…` | — |
| Cloud storage | Azure blob storage, for sharing results | configured, not run |

## Prerequisites

* [uv](https://docs.astral.sh/uv/) and CPython 3.14 (pinned in `.python-version`).
* Node.js 26 (the repository is developed with `nvm use v26`).
* A checkout:

```bash
git clone git@github.com:destiny-evidence/academic-api-importer.git
cd academic-api-importer

uv sync --group dev      # backend + dev tooling into .venv/
cd frontend && npm ci    # frontend tooling
```

Some dependencies come from a private index (`nacsos-data`), so a plain
`uv sync` needs credentials for that source; CI uses a `netrc` entry for it.

## Configuration

Settings are read once, at import time, from environment variables prefixed with `AAI_`,
optionally loaded from an env file — `.config/secret.env` by default, overridable with
`AAI_CONF_FILE`. `.config/default.env` shows the shape of the file.

Three settings have **no default and are required** just to import the application:

```bash
AAI_AZURE_STORAGE_CONNECTION_STRING=...
AAI_AZURE_STORAGE_ACCOUNT_NAME=...
AAI_AZURE_STORAGE_ACCOUNT_KEY=...
```

For local development without an Azure account, placeholder values are fine — everything
except the *sync with cloud* buttons works. Alternatively, authenticate through Entra
instead of keys with `AAI_AZURE_STORAGE_USE_ENTRA=true`.

!!! note "Reading `.config/default.env`"

    Only one env file is read (`AAI_CONF_FILE`, default `.config/secret.env`), and
    `.config/default.env` is a template rather than a fallback — copy what you need.
    One of its lines does nothing as written: the data root is set with
    `AAI_DATA_ROOT` (not `AAI_DATA_DIR`).

The settings you are most likely to touch:

| Variable | Default | Meaning |
| --- | --- | --- |
| `AAI_SECRET_KEY` | a placeholder string | Key for signing login tokens — replace it anywhere but locally |
| `AAI_DATA_ROOT` | `data/` | Root for the database and all result files |
| `AAI_DB_FILE` | `<data root>/oracle.db` | SQLite database, which also holds the task queue |
| `AAI_RESULT_DIR` | `<data root>/results` | Where per-query result folders live |
| `AAI_WEB_ORIGINS` | `[]` | JSON list of origins allowed to call the API with credentials, e.g. `["https://aapi.example.org"]`. Only needed when the UI is served from a different origin than the API — the Vite dev server proxies instead |
| `AAI_ROOT_PATH` | `""` | Path prefix, when the API sits behind a reverse proxy under `/aapi-api` or similar |
| `AAI_COOKIE_LIFETIME` | `1440` | Login validity in minutes |
| `AAI_MAX_RETRIES` | `5` | Re-tries per task before it is marked `FAILURE` |
| `AAI_EMPTY_LOOP_SLEEP` | `30` | Seconds a worker waits when the queue is empty |
| `AAI_SCOPUS_PAGE_SIZE` | API maximum (25) | Records per request; lower it to be gentle on a small quota |

## Running it locally

Three processes, three terminals:

```bash
# 1. API on http://localhost:8000 (interactive docs at /docs); on startup it also
#    re-queues tasks that were left unfinished
uv run fastapi dev

# 2. workers — the async task limit matters, without it a worker keeps paging
#    ahead without waiting for responses
uv run taskiq worker academic_apis.broker.broker:broker --workers=3 --max-async-tasks=1

# 3. web UI, on http://localhost:5173
cd frontend && npm run dev
```

The dev server proxies `/users`, `/queries`, `/tasks`, `/tokens` and `/profile` to
`http://localhost:8000`, so requests stay same-origin and the session cookie works
unchanged; set `AAPI_VITE_API_URL` to proxy somewhere else.

### Creating the first administrator

Accounts can only be created by an administrator, so the very first one has to come from
the database. Run this once from the repository root — creating the engine also creates
the database file and the tables:

```bash
uv run python - <<'PY'
from sqlmodel import Session

from academic_apis.api.utils.auth import hash_password
from academic_apis.db import User, make_engine

with Session(make_engine()) as session:
    session.add(
        User(
            email="you@example.org",
            full_name="First Admin",
            institution="Example University",
            is_active=True,
            permission_admin=True,
            permission_submit_query=True,
            password_hashed=hash_password("pick-a-long-one"),
        )
    )
    session.commit()
PY
```

Then log in, and create the remaining accounts (and grant them token access) on the
[Users page](management.md#users-administrators-only).

## Frontend

```bash
nvm use v26            # if not your default

cd frontend
npm run api            # regenerate the API client from the running backend's openapi.json
npm run dev            # development server
npm run build          # production build into dist/
npm run preview        # serve the built app locally

npm run lint           # ESLint (flat config)
npm run lint:fix       # ESLint with autofix
npm run format         # Prettier
npm run typecheck      # vue-tsc
```

`npm run api` reads `http://127.0.0.1:8000/openapi.json` (so start the API first) and
regenerates the typed client into `frontend/src/plugins/api/spec/`; commit the regenerated
client together with the backend change that caused it.

### Serving under a path or a different origin

These are **build-time** variables (env prefix `AAPI_`, also usable as CI/CD
variables, or in a local `frontend/.env.local`):

* `AAPI_VITE_UI_URL` — URL or bare path the UI is served under, e.g. `https://domain.com/aapi/`
  or `/aapi/`. Only the path part is used: it becomes Vite's `base`, so asset URLs and the
  Vue router live under it. Copy `dist/` into that directory on the web server. Defaults to
  the site root.
* `AAPI_VITE_API_URL` — root of the API: absolute (`https://api.domain.com`,
  `https://domain.com/aapi-api`) or relative to the origin (`/aapi-api`). It becomes the
  generated client's base URL; unset means API requests go to the path the UI is served under.
* `AAPI_VITE_HELP_URL` — URL of the help documentation (e.g. this documentation site);
  it is linked as **Help** in the navigation bar. Unset means the navbar shows no help link.

The API endpoints themselves never move: point the backend at the UI's origin with
`AAI_WEB_ORIGINS`. When the API is proxied under a path of its own, give the server a
matching root path — `uv run fastapi run --root-path /aapi-api`.

The backend can also serve the build itself: setting `AAI_STATIC_FILES` mounts it as
`/static`, though in practice the SPA is deployed as static files next to the API
(see [Deployment](#deployment)).

## Backend quality tools

```bash
uv run ruff format             # formatting
uv run ruff check --fix        # linting
uv run mypy                    # type checking
uv run pytest                  # unit/integration tests (throwaway sqlite database, never data/oracle.db)

uv run pre-commit install      # run ruff, mypy and the frontend checks on commit
uv run pre-commit run --all-files

# ...or one hook at a time (pre-commit takes a single hook-id per run, so group with a loop;
# hooks: ruff-check ruff-format mypy | frontend-lint frontend-format frontend-typecheck)
for h in ruff-check ruff-format mypy; do uv run pre-commit run "$h" --all-files || break; done
for h in frontend-lint frontend-format frontend-typecheck; do uv run pre-commit run "$h" --all-files || break; done
```

## Documentation

```bash
uv run zensical serve -f docs/zensical.toml -a localhost:8001   # live preview
uv run zensical build --strict --clean                          # static site into docs/site/
```

The sources are Markdown in `docs/docs/`, the config is `docs/zensical.toml`, and
screenshots for the interface pages live in `docs/docs/screenshots/`. `--strict` fails the
build on warnings, which is what CI runs.

The link to the API's Swagger UI and OpenAPI schema on the front page is baked in at
build time by the `macros` plugin (`docs/main.py`): set `AAPI_DOCS_API_URL` to the root
URL the API is reachable at (e.g. `https://domain.com/aapi/api`, as in the NGINX config
below), and the links become `<url>/docs` and `<url>/openapi.json`. Unset, they default
to a local development API at `http://localhost:8000`.

## Logging

Logging for both the API and the workers is configured in code at startup via
`init_logging()` (`src/academic_apis/utils/logging.py`), which applies a standard Python
dictConfig read from a JSON logging config file. Both `fastapi dev` (through the app
lifespan) and `taskiq worker` (through the broker's `WORKER_STARTUP` event) pick it up, so
no CLI flags are needed:

```bash
# uses .config/logging.json by default
uv run fastapi dev
uv run taskiq worker academic_apis.broker.broker:broker --workers=3 --max-async-tasks=1

# point at a different config file (e.g. a more verbose one for debugging)
AAI_LOG_CONFIG=.config/logging-debug.json uv run fastapi dev
AAI_LOG_CONFIG=.config/logging-debug.json uv run taskiq worker academic_apis.broker.broker:broker --workers=3 --max-async-tasks=1

# override the root log level without editing the file (numeric: 10=DEBUG, 20=INFO, 30=WARNING)
AAI_LOGLEVEL=20 uv run fastapi dev
```

The default lives in `.config/logging.json` (same format as uvicorn's `--log-config`; it is
applied with `disable_existing_loggers: false`). It logs everything to the console with a
common format and pins the known noisy loggers (`matplotlib`, `urllib3`, `httpcore`,
`httpx`, `taskiq`, `sqlalchemy`, `azure`) to WARNING/INFO. Uvicorn's access and error
loggers get a dedicated `access` handler/formatter (like the default one, minus the process
ID), so request lines such as `127.0.0.1 - "GET /health HTTP/1.1" 404` are formatted
consistently with the rest of the logs instead of uvicorn's own `INFO:` style (this also
replaces `fastapi dev`'s rich formatting; note that the reloader process, which starts
before the app is imported, still logs in uvicorn's format). The task output capture
(`academic_apis.broker.middleware_log.OutputCaptureMiddleware`) uses a dedicated `task`
handler/formatter (held by the otherwise unused `academic_apis.task_capture` logger) whose
format adds the running task's `task_id` and `task_name` to every line of the per-task
`.log` files. The `academic_apis` loggers follow the root level, which always comes from
`AAI_LOGLEVEL` (default `DEBUG`). To see SQL statements, set the `sqlalchemy.engine`
logger to `INFO` in the config file (it ships pinned to `WARNING`); the engine never uses
SQLAlchemy's `echo` flag, since that would bypass the configured log levels and print each
statement twice.

## Files on disk

| Path | Contents |
| --- | --- |
| `data/oracle.db` | Users, tokens, queries, tasks — and the task queue itself |
| `data/oracle.fslock` | Cross-process write lock for the database |
| `data/results/<query_id>/responses/<task_id>.jsonl` | Raw records of one task |
| `data/results/<query_id>/responses/<task_id>.log` | That task's captured log output |
| `data/results/<query_id>/responses/<task_id>.err` | The serialized error, for failed tasks |
| `data/results/<query_id>/converted/` | Reserved for converted (translated) records; deleted along with the query |

## Deployment

A production instance is the API process, one or more worker services, and the built SPA as
static files. CI (`.gitlab-ci.yml`) builds `frontend/dist/` and `docs/site/`, then rsyncs
them to `AAPI_WWW_DIR` and `AAPI_DOCS_WWW_DIR`, and deploys the backend by pulling the
branch and re-running `uv sync --frozen --reinstall` around a service restart.

### Worker service

```ini
[Unit]
Description=academic api workers
After=network.target

[Service]
Type=simple
User=nacsos
Group=nacsos
Environment="AAI_CONF_FILE=/data/academic-api/.config/secret.env"
Environment="OPENSSL_CONF=/dev/null"
WorkingDirectory=/data/academic-api

ExecStart=uv run taskiq worker academic_apis.broker.broker:broker --workers=3 --max-async-tasks=1

Restart=always
RestartSec=30s
KillMode=control-group
KillSignal=SIGTERM
TimeoutStopSec=10
FinalKillSignal=SIGKILL

[Install]
WantedBy=multi-user.target
```

The API runs as a matching `academic-api.service`; the two are restarted independently, and
workers are only restarted after a code update (a restart re-queues unfinished tasks).

### NGINX

```
    location /aapi {
        alias /data/academic-api/public_html/;
        index index.html;
        try_files $uri $uri/ /aapi/index.html;
    }
    location /aapi/docs {
        alias /data/academic-api/docs/;
        index index.html;
        try_files $uri $uri/ /aapi/docs/index.html;
    }
    location /aapi/api/ {
        proxy_pass http://127.0.0.1:7050/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Prefix /aapi/api;
    }
```

The API process behind that location must know the prefix it is served under, so its
service starts the server with a matching root path. It is recommended to still keep
the `AAI_ROOT_PATH` in your environment file and read from there for consistency.

```bash
uv run fastapi run --root-path /aapi/api --port 7050

# if you want to read from your config (assuming `.config/secret.env`)
uv run fastapi run --root-path "$(sed -n 's/^AAI_ROOT_PATH=//p' .config/secret.env 2>/dev/null | tr -d '"' | tr -d "'" | grep . || echo '/aapi/api')" --port 7050
```

### Ad-hoc operations

```bash
# push a working tree to the server (when the pipeline is not an option)
rsync -avh --progress -e ssh . se164:/data/academic-api --exclude=.venv/ --exclude=.git/ --exclude=.idea/ --exclude=data/ --exclude=.config/

# reach a UI/API behind the server's firewall
ssh -N -J ts01 -L 7050:localhost:7050 se164

# copy the production database down for debugging
scp se164:/data/academic-api/data/oracle.db data/oracle.bak.db
```

!!! warning "Copying a live database"

    The database runs in WAL mode and the queue lives in it, so a copy made while the API
    is writing can be inconsistent. Prefer copying during a quiet window, and never open
    the copy as if it were the live file.
