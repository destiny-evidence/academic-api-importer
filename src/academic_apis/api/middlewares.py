from fastapi import Header, HTTPException, Request
from starlette import status

from academic_apis.utils import settings

# List of paths that bypass authentication
PUBLIC_PATHS = {"/docs", "/redoc", "/openapi.json"}


async def verify_auth_key(
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
