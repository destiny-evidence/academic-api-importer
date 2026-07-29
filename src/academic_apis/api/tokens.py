import logging
from typing import TYPE_CHECKING

import sqlalchemy as sa
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from academic_apis.db import AccessToken, db_session
from academic_apis.db.schemas import AccessTokenPublic

if TYPE_CHECKING:
    from sqlmodel import Session  # noqa: F401

logger = logging.getLogger(__name__)

route = APIRouter()

templates = Jinja2Templates(directory="src/academic_apis/api/templates")


@route.get("/")
async def list_tokens() -> list[AccessTokenPublic]:
    async with db_session() as session:  # type: Session
        return (
            session.exec(
                sa.select(
                    AccessToken.token_id,
                    AccessToken.wrapper,
                    AccessToken.comment,
                    AccessToken.last_used,
                    AccessToken.last_feedback,
                    AccessToken.locked_until,
                ).where(AccessToken.active == True),
            )
            .mappings()
            .all()
        )


@route.get("/html", response_class=HTMLResponse)
async def list_tokens_html(request: Request) -> HTMLResponse:
    tokens = await list_tokens()

    return templates.TemplateResponse(
        request, "tokens.html", {"request": request, "tokens": tokens}
    )
