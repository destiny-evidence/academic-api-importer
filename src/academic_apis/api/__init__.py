from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI
from starlette.staticfiles import StaticFiles

from academic_apis.broker.broker import broker
from academic_apis.utils import init_logging, settings

from .manage import route as manage_app
from .middlewares import verify_auth_key
from .tokens import route as token_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:  # noqa: ARG001
    init_logging()

    # Startup
    if not broker.is_worker_process:
        await broker.startup()

    yield

    # Shutdown
    if not broker.is_worker_process:
        await broker.shutdown()


api = FastAPI(lifespan=lifespan)

if len(settings.access_tokens) > 0:
    api.router.dependencies.append(Depends(verify_auth_key))

api.mount(
    "/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static"
)

api.include_router(manage_app, prefix="/manage")
api.include_router(token_app, prefix="/tokens")
