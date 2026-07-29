```bash
# start api/webui server
# this is also the taskiq scheduler
uv run fastapi dev

# tart taskiq workers
# the async task limit is important, otherwise it will just continue paging without waiting for responses
uv run taskiq worker academic_apis.broker.broker:broker --workers=3 --max-async-tasks=1   
```