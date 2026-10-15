# Academic API Importer

A web interface for downloading **large result sets** from academic literature APIs
(Scopus today; PubMed, Web of Science, Dimensions and OpenAlex to follow).

Academic APIs handle big downloads badly: 25 records per request, request quotas that run
out mid-run, and long paginated fetches that have to start over when they fail. This tool
splits a query into many small **sub-queries**, fetches each as an independent **task**
with its own result file, and retries only the pieces that fail — so a restart costs the
tasks that were in flight, not the whole download.

**Features**

* **Query formatter** (one term per line, indented sub-queries) and **syntax checker** that
  pinpoints errors with line, column and a hint — the APIs themselves only return generic
  errors
* **Query test** against the real API before spending budget on the full download
* **Sub-query generation** by publication year and first-author initial, with automatic
  retries
* Per-query **task monitoring**: status, retries, retrieved vs. target counts, logs
* **Result sharing** through time-limited cloud storage links

Documentation: [`docs/`](docs/) — served sources in [`docs/docs/`](docs/docs), including
[Development and operations](docs/docs/development.md) for configuration, logging and
deployment. User-facing pages describe the interface screen by screen.

## Quickstart

Needs [uv](https://docs.astral.sh/uv/) with CPython 3.14 and Node.js 26 (developed with
`nvm use v26`).

```bash
git clone git@github.com:destiny-evidence/academic-api-importer.git
cd academic-api-importer

uv sync --group dev          # backend and dev tooling into .venv/
cd frontend && npm ci && cd ..
```

The application refuses to start without its Azure settings (used for result sharing) —
placeholders are fine for local development:

```bash
mkdir -p .config
cat > .config/secret.env <<'EOF'
AAI_SECRET_KEY=change-me-to-a-long-random-string
AAI_AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=local;AccountKey=local;EndpointSuffix=core.windows.net
AAI_AZURE_STORAGE_ACCOUNT_NAME=local
AAI_AZURE_STORAGE_ACCOUNT_KEY=local
EOF
```

Then start the three processes (each in its own terminal):

```bash
uv run fastapi dev           # API on http://localhost:8000, interactive docs at /docs
uv run taskiq worker academic_apis.broker.broker:broker --workers=3 --max-async-tasks=1
cd frontend && npm run dev   # web UI on http://localhost:5173
```

The dev server proxies the API endpoints, so open **http://localhost:5173**. Accounts can
only be created by an administrator, so the first one is bootstrapped from the database —
the snippet in
[Development and operations](docs/docs/development.md#creating-the-first-administrator)
does it in one paste.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/academic_apis/` | FastAPI API, taskiq workers, wrappers for the academic APIs, Scopus query grammar |
| `frontend/` | Vue 3 + Vite single-page app |
| `docs/` | Documentation sources ([zensical](https://zensical.org)) |
| `tests/` | Backend tests (throwaway SQLite database) |
| `data/` | SQLite database, task queue, and downloaded results at runtime |

## License

AGPL-3.0 — see [LICENSE](LICENSE).
