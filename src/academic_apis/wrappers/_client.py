from __future__ import annotations

import logging
from time import perf_counter, sleep
from typing import TYPE_CHECKING, Any, override

from httpx import URL, USE_CLIENT_DEFAULT, Client, HTTPError, Response, codes

if TYPE_CHECKING:
    from collections.abc import Callable

    from httpx._client import UseClientDefault
    from httpx._types import (
        AuthTypes,
        CookieTypes,
        HeaderTypes,
        QueryParamTypes,
        RequestContent,
        RequestData,
        RequestExtensions,
        RequestFiles,
        TimeoutTypes,
    )


def response_logger(logger_: logging.Logger) -> Callable[[Response], dict[str, Any]]:
    def inner(response: Response) -> dict[str, Any]:
        # nonlocal logger
        logger_.warning(response.text)
        return {}

    return inner


class RequestClient(Client):
    def __init__(  # type: ignore[no-untyped-def]
        self,
        *,
        max_req_per_sec: int = 5,
        max_retries: int = 5,
        backoff_rate: float = 120.0,
        retry_on_status: list[int] | None = None,
        logger: logging.Logger | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        super().__init__(**kwargs)

        self.max_req_per_sec = max_req_per_sec
        self.time_per_request = 1 / max_req_per_sec
        self.max_retries = max_retries
        self.backoff_rate = backoff_rate
        self.last_request: float | None = None
        self.retry_on_status = retry_on_status or [
            codes.INTERNAL_SERVER_ERROR,  # 500
            codes.BAD_GATEWAY,  # 502
            codes.SERVICE_UNAVAILABLE,  # 503
            codes.GATEWAY_TIMEOUT,  # 504
        ]
        self.kwargs = kwargs
        self.callbacks: dict[int, Callable[..., Any]] = {}
        self.logger = logger or logging.getLogger(__name__)

    def on(self, status: int, func: Callable[[Response], dict[str, Any]]) -> None:
        self.callbacks[status] = func

    @override
    def request(
        self,
        method: str,
        url: URL | str,
        *,
        content: RequestContent | None = None,
        data: RequestData | None = None,
        files: RequestFiles | None = None,
        json: Any | None = None,
        params: QueryParamTypes | None = None,
        headers: HeaderTypes | None = None,
        cookies: CookieTypes | None = None,
        auth: AuthTypes | UseClientDefault | None = None,
        follow_redirects: bool | UseClientDefault | None = True,
        timeout: TimeoutTypes | UseClientDefault = 120,
        extensions: RequestExtensions | None = None,
    ) -> Response:
        for retry in range(self.max_retries):
            # Check if we need to wait before the next request so we are staying below the rate limit
            time = perf_counter() - (self.last_request or 0)
            if time < self.time_per_request:
                self.logger.debug(
                    f"Sleeping to keep rate limit: {self.time_per_request - time:.4f} seconds",
                )
                sleep(self.time_per_request - time)

            if auth == USE_CLIENT_DEFAULT:
                auth = None
            if follow_redirects == USE_CLIENT_DEFAULT:
                follow_redirects = None
            if timeout == USE_CLIENT_DEFAULT:
                timeout = None

            try:
                # Log latest request
                self.last_request = perf_counter()
                response = super().request(
                    method=method or self.kwargs.get("method"),  # type: ignore[arg-type]
                    url=url or self.kwargs.get("url"),  # type: ignore[arg-type]
                    content=content or self.kwargs.get("content"),
                    data=data or self.kwargs.get("data"),
                    files=files or self.kwargs.get("files"),
                    json=json or self.kwargs.get("json"),
                    params=params or self.kwargs.get("params"),
                    headers=self.kwargs.get("headers", {}) | (headers or {}),
                    cookies=self.kwargs.get("cookies", {}) | (cookies or {}),
                    auth=auth or self.kwargs.get("auth", USE_CLIENT_DEFAULT),
                    follow_redirects=follow_redirects
                    or self.kwargs.get("follow_redirects", True),
                    timeout=timeout or self.kwargs.get("timeout", 120),
                    extensions=extensions or self.kwargs.get("extensions"),
                )

                response.raise_for_status()

                # reset counters after successful request
                self.time_per_request = 1 / self.max_req_per_sec

                return response

            except HTTPError as e:
                self.logger.warning(f"Encountered HTTP error: {e.response.status_code}")  # type: ignore[attr-defined]
                self.logger.warning(e.response.text)  # type: ignore[attr-defined]

                if e.response.status_code in self.callbacks:  # type: ignore[attr-defined]
                    self.logger.debug(
                        f"Found status handler for {e.response.status_code}"
                    )  # type: ignore[attr-defined]
                    update = self.callbacks[e.response.status_code](e.response)  # type: ignore[attr-defined]
                    if update and update.get("content"):
                        content = update.get("content")
                    if update and update.get("data"):
                        data = update.get("data")
                    if update and update.get("json"):
                        if not json:
                            json = update.get("json", None)
                        else:
                            json.update(update.get("json", {}))
                    if update and update.get("params"):
                        if not params:
                            params = update.get("params", None)
                        else:
                            params.update(update.get("params", {}))  # type: ignore[union-attr]
                    if update and update.get("headers"):
                        if not headers:
                            headers = update.get("headers", None)
                        else:
                            headers.update(update.get("headers", {}))  # type: ignore[union-attr]

                # if this error is not on the list, pass on error right away; otherwise log and retry
                elif (
                    e.response.status_code not in self.retry_on_status
                    and len(self.retry_on_status) > 0
                ):  # type: ignore[attr-defined]
                    self.logger.warning("No handler found for error, raising.")
                    raise e

                else:
                    self.logger.warning(
                        f"Retry {retry} after failing to retrieve from {url}: {e}",
                    )
                    self.logger.exception(e)

                    # grow the sleep time between requests
                    self.time_per_request = (
                        self.time_per_request + 1
                    ) * self.backoff_rate
        raise RuntimeError("Maximum number of retries reached")
