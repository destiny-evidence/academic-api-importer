## Frontend

```bash
# if not default, choose npm/node version
nvm use v26

cd frontend
# update api spec from backend openapi.json
npm run api

# lint, typechecking, code formatting
npm run lint:fix
npm run format
npm run typecheck

# run frontend in development mode
npm run dev
```

See `frontend/README.md` for building the app under a path
(`VITE_AAPI_UI_URL`, e.g. `domain.com/path/`) and pointing the API client at a
configured base URL (`VITE_AAPI_API_URL`).

## Backend

```bash
# Run REST API
uv run fastapi dev

# Run worker backend
uv run taskiq worker academic_apis.broker.broker:broker --workers=3 --max-async-tasks=1

# Testing/formatting
uv run ruff format
uv run ruff check --fix
uv run mypy
uv run pytest
```

## Logging

Logging for both the API and the workers is configured in code at startup via
`init_logging()` (`src/academic_apis/utils/logging.py`), which applies a standard
Python dictConfig read from a JSON logging config file. Both `fastapi dev` (through
the app lifespan) and `taskiq worker` (through the broker's `WORKER_STARTUP` event)
pick it up, so no CLI flags are needed:

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

The default lives in `.config/logging.json` (same format as uvicorn's `--log-config`;
it is applied with `disable_existing_loggers: false`). It logs everything to the console
with a common format and pins the known noisy loggers (`matplotlib`, `urllib3`,
`httpcore`, `httpx`, `taskiq`, `sqlalchemy`, `azure`) to WARNING/INFO. Uvicorn's access
and error loggers get a dedicated `access` handler/formatter (like the default one, minus
the process ID), so request lines such as `127.0.0.1 - "GET /health HTTP/1.1" 404` are
formatted consistently with the rest of the logs instead of uvicorn's own `INFO:` style
(this also replaces `fastapi dev`'s rich formatting; note that the reloader process,
which starts before the app is imported, still logs in uvicorn's format). The task output
capture (`academic_apis.broker.middleware_log.OutputCaptureMiddleware`) uses a dedicated
`task` handler/formatter (held by the otherwise unused `academic_apis.task_capture`
logger) whose format adds the running task's `task_id` and `task_name` to every line of
the per-task `.log` files. The
`academic_apis` loggers follow the root level, which always comes from `AAI_LOGLEVEL`
(default `DEBUG`). To see SQL statements, run with `AAI_DB_VERBOSE=true` (or set the
`sqlalchemy.engine` logger to `INFO` in the config file); the engine never uses
SQLAlchemy's `echo` flag, since that would bypass the configured log levels and print
each statement twice.


```bash
# unit/integration tests (they use a throwaway sqlite database, never data/oracle.db)
uv run pytest

# install the pre-commit hook (runs ruff, mypy and frontend checks on commit)
uv run pre-commit install

# run all pre-commit checks against every file
uv run pre-commit run --all-files

# ...or run one language group at a time (pre-commit takes a single hook-id per run,
# so group with a loop; hooks: ruff-check ruff-format mypy | frontend-lint frontend-format frontend-typecheck)
for h in ruff-check ruff-format mypy; do uv run pre-commit run "$h" --all-files || break; done
for h in frontend-lint frontend-format frontend-typecheck; do uv run pre-commit run "$h" --all-files || break; done
```


```bash
# start api/webui server
# this is also the taskiq scheduler
uv run fastapi dev

# tart taskiq workers
# the async task limit is important, otherwise it will just continue paging without waiting for responses
uv run taskiq worker academic_apis.broker.broker:broker --workers=3 --max-async-tasks=1   

# hacky deploy when github strikes
rsync -avh --progress -e ssh . se164:/data/academic-api --exclude=.venv/ --exclude=.git/ --exclude=.idea/ --exclude=data/ --exclude=.config/

# Access "UI"
ssh -N -J ts01 -L 7050:localhost:7050 se164

# Copy remote DB to local
scp se164:/data/academic-api/data/oracle.db data/oracle.bak.db

# API: http://localhost:7050/docs
# UI: http://localhost:7050/queries/html
```

worker service
```
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

#ExecStop=kill -9 $(ps aux | grep 'dramatiq' | awk '{print $2}')

Restart=always
RestartSec=30s
#PIDFile=/data/nacsos2/dramatiq.pid
#KillMode=process
KillMode=control-group
KillSignal=SIGTERM
TimeoutStopSec=10
FinalKillSignal=SIGKILL

[Install]
WantedBy=multi-user.target
```
