from typing import Annotated

from fastapi import Header, HTTPException, Request, Cookie
from starlette import status

from academic_apis.utils import settings

# List of paths that bypass authentication
PUBLIC_PATHS = {"/docs", "/redoc", "/openapi.json", "/set-cookie"}


async def verify_auth_key_header(
    request: Request, x_auth_key: str | None = Header(None, alias="x-auth-key"),
) -> str | None:
    if request.url.path in PUBLIC_PATHS:
        return None

    if not x_auth_key or x_auth_key not in settings.access_tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid or missing x-auth-key",
        )
    return x_auth_key


async def verify_auth_key_cookie(
    request: Request, aai_auth_token: Annotated[str | None, Cookie()] = None,
) -> str | None:
    if request.url.path in PUBLIC_PATHS:
        return None

    print(aai_auth_token)

    if not aai_auth_token or aai_auth_token not in settings.access_tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid or missing auth cookie",
        )
    return aai_auth_token
