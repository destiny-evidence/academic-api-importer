from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from academic_apis.broker.broker import broker
from academic_apis.utils import init_logging, settings

from .progress import route as progress_app
from .queries import route as manage_app
from .tokens import route as token_app
from .users import route as users_app

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:  # noqa: ARG001
    init_logging()

    # Startup
    if broker.is_worker_process:
        await broker.startup()

    yield

    # Shutdown
    if broker.is_worker_process:
        await broker.shutdown()


api = FastAPI(
    lifespan=lifespan,
    openapi_url=settings.OPENAPI_FILE,
    openapi_prefix=settings.OPENAPI_PREFIX,
    root_path=settings.ROOT_PATH,
    separate_input_output_schemas=False,
)

api.add_middleware(
    CORSMiddleware,
    allow_origins=settings.web_origins,  # Explicit origin required (no "*")
    allow_credentials=True,  # Required for cookies
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

if settings.static_files is not None:
    api.mount(
        "/static",
        StaticFiles(directory=settings.static_files),
        name="static",
    )

api.include_router(manage_app, prefix="/queries", tags=["Queries"])
api.include_router(token_app, prefix="/tokens", tags=["Tokens"])
api.include_router(progress_app, prefix="/tasks", tags=["Tasks"])
api.include_router(users_app, prefix="/users", tags=["Users"])
