## Testing
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
