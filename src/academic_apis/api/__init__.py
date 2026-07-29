from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from academic_apis.broker.broker import broker
from academic_apis.utils import init_logging, settings

from .queries import route as manage_app
from .middlewares import verify_auth_key_cookie
from .tokens import route as token_app
from .progress import route as progress_app

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
    # api.router.dependencies.append(Depends(verify_auth_key_header))
    api.router.dependencies.append(Depends(verify_auth_key_cookie))

api.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origins],  # Explicit origin required (no "*")
    allow_credentials=True,  # Required for cookies
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@api.post("/set-cookie")
async def set_cookie(response: Response, token: Annotated[str, Body()]) -> dict[str, str]:
    response.set_cookie(
        key="aai_auth_token",
        value=token,
        path="/",
        samesite="lax",  # Default behavior for same-origin or top-level navigation
        httponly=False,  # Set to True if you don't need frontend JS to read it directly
        secure=False,  # Set to True if using HTTPS
    )
    return {"message": "Cookie set successfully"}


api.mount(
    "/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static",
)

api.include_router(manage_app, prefix="/queries")
api.include_router(token_app, prefix="/tokens")
api.include_router(progress_app, prefix="/tasks")
