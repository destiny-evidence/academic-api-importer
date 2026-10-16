---
icon: lucide/rocket
description: What the Academic API Importer does, why it exists, and where to start.
---

# Academic API Importer

A web interface for downloading **large result sets** from academic literature APIs
(Scopus today, with PubMed, Web of Science, Dimensions and OpenAlex to follow). Instead
of asking an API for 100,000 records in one go, the tool breaks your query into many
small sub-queries, fetches each of them as an independent job, and re-assembles the
pieces for you.

## Why it exists

Academic APIs are not good at handling large result sets:

- **Paging is slow and capped.** The Scopus search API returns 25 records per request
  in the `COMPLETE` view, so a 100,000-record query means 4,000 requests.
- **Budgets run out.** Keys have a request quota; when it is exhausted, every further
  request fails until the quota resets — typically days later.
- **Long runs fail mid-way.** A paginated download that dies after 3 hours usually has
  to start over, wasting the requests that already succeeded.

The solution is to **split the work into small sub-tasks**, so that restarting is cheap:

- each sub-query is fetched and stored as its own file,
- a failing sub-query is retried on its own (up to five times),
- finished sub-queries are never fetched again, even if the run is restarted.

## How it works

``` mermaid
flowchart LR
    Q[Query] --> G[Sub-query generator]
    G --> T[Task queue]
    T --> W[Workers]
    P[(API token pool)] --> W
    W -->|one file per sub-query| F[(result files on disk)]
    F -->|sync + share links| C[(Cloud storage)]
    C -.->|optional next step| I[Ingest robot]
    I -.-> O[ODS search]
```

| Term | Meaning |
| --- | --- |
| **Query** | What you type in, e.g. `TITLE-ABS(climate AND health)`. Gets a numeric `query_id`. |
| **Sub-query** | A slice of the query produced by the generator, e.g. `... AND FIRSTAUTH (ab*) AND ( PUBYEAR AFT 2011 ) AND ( PUBYEAR BEF 2014 )`. |
| **Task** | One sub-query as a queued job, with its own status, retries, log file and result file. |

## Features

* **Token pool** — Manage API access tokens, and track remaining quota, pause a token
  without deleting it. See [Managing account, tokens and users](management.md).
* **Query check** — the search grammar is parsed on the server, so syntax errors are
  reported with line, column and a hint, instead of the generic error the API returns.
* **Query formatter** — one term per line with indentation for sub-queries, which makes
  big queries readable and diffable (e.g. in a git commit or pull request).
* **Query test** — run the first page of a query through the real API before spending
  budget on the whole download.
* **Automatic retries** — failed sub-queries are re-queued individually, and a token is
  locked out while its quota is used up.
* **Custom sub-queries via the API** — submit your own list of slices (`POST /queries`
  with `queries=[...]`) when the built-in year/author splitting is not what you need.

Also on the roadmap: a *quasi-sampling* download mode that fetches only a slice of the
results first, and query analysis to show which terms drive the hit count (see
[Checking and submitting queries](query-submit.md)).

## Related projects

This repository covers the **collection** side: it fetches records from scholarly search
APIs and leaves the result files in cloud storage. Downstream, the
[bulk abstract ingest robot](https://github.com/destiny-evidence/bulk-abstract-ingest-robot)
can pick up those files from the cloud storage and feed the records into the ODS search —
turning a downloaded result set into a searchable corpus.

## Getting started

1. **Log in** with the e-mail and password you were given. Accounts are created by an
   administrator — there is no self-registration. Sessions expire after 24 hours by
   default.
2. **Add an API token** on the **Tokens** page (or ask someone who owns one to grant you
   access). Nothing can be fetched without an active token you are allowed to use.
3. **Check your query** under *Advanced → Query utils*: format it, then test it.
4. **Submit it** under *Advanced → Submit query* and pick how it should be split.
5. **Watch the progress** on the **Tasks** page and download the results from the
   **Queries** page once the tasks are through.

!!! tip "Confirmation buttons"

    Risky actions (deleting a query, syncing to cloud storage, submitting) use a
    two-stage button: click it once and the action runs only if you then click
    **Yes**. Every result, error and permission change is reported as a toast message.

!!! note "API access"

    The web UI talks to a REST API that is documented with OpenAPI. On this
    deployment the schema is served at [openapi.json]({{ swagger_json_url }}),
    and the same schema is rendered as an interactive Swagger UI at
    [{{ swagger_ui_url }}]({{ swagger_ui_url }}). (The URLs are baked in when
    the documentation is built; see
    [Development and operations](development.md#documentation).)

## Where to go next

| Page | Contents |
| --- | --- |
| [Managing account, tokens and users](management.md) | Profile, the API token pool, permissions, user administration |
| [Checking and submitting queries](query-submit.md) | Formatter, grammar check, test button, sub-query settings |
| [Monitoring queries and tasks](query-monitor.md) | Query list, access rights, task states, logs, downloads |
| [Development and operations](development.md) | Setup, configuration, running the API and workers, tooling, logging, deployment |
