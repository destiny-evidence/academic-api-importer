```bash
# start api/webui server
# this is also the taskiq scheduler
uv run fastapi dev

# tart taskiq workers
# the async task limit is important, otherwise it will just continue paging without waiting for responses
uv run taskiq worker academic_apis.broker.broker:broker --workers=3 --max-async-tasks=1   


# hacky deploy when github strikes
rsync -avh --progress -e ssh . se164:/data/academic-api --exclude=.venv/ --exclude=.git/ --exclude=.idea/ --exclude=data/ --exclude=.config/
```