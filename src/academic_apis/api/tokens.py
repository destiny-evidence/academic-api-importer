import logging
from collections.abc import Sequence

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from academic_apis.db import AccessToken, db_session
from academic_apis.db.schemas import AccessTokenPublic
from academic_apis.utils import settings

logger = logging.getLogger(__name__)

route = APIRouter()

templates = Jinja2Templates(directory="src/academic_apis/api/templates")


@route.get("/")
async def list_tokens() -> Sequence[AccessTokenPublic]:
    async with db_session() as session:
        return session.exec(
            select(  # type: ignore[call-overload, misc]
                AccessToken.token_id,
                AccessToken.wrapper,
                AccessToken.comment,
                AccessToken.last_used,
                AccessToken.last_feedback,
                AccessToken.locked_until,
            ).where(AccessToken.active == True),
        ).all()


@route.get("/html", response_class=HTMLResponse)
async def list_tokens_html(request: Request) -> HTMLResponse:
    tokens = await list_tokens()

    return templates.TemplateResponse(
        request,
        "tokens.html",
        {
            "request": request,
            "tokens": tokens,
            "settings": settings,
        },
    )
