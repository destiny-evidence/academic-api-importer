# ruff: noqa: INP001  # build-time hook, not an importable package
# Zensical macros hook (plugins.macros): exposes deployment URLs as Jinja
# variables to the Markdown sources. The URLs differ per deployment, so they
# are baked in at build time from the environment (set as CI/CD variables);
# the defaults point at a local development API.

import os
from typing import Any

# Root the API is reachable at, e.g. https://domain.com/aapi/api (no trailing
# slash). Swagger UI and the OpenAPI schema are served below it at the paths
# configured on the backend (AAI_ROOT_PATH and AAI_OPENAPI_FILE there).
API_URL = os.environ.get("AAPI_DOCS_API_URL", "http://localhost:8000").rstrip("/")


def define_env(env: Any) -> None:  # noqa: ANN401  # zensical's macro environment
    """Register the variables that docs/*.md templates can reference."""
    env.variables["swagger_ui_url"] = f"{API_URL}/docs"
    env.variables["swagger_json_url"] = f"{API_URL}/openapi.json"
