import logging
import os
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_ENV_VAR = "AAI_CONF_FILE"
DEFAULT_CONFIG_FILE = ".config/secret.env"
CONFIG_FILE = os.getenv(CONFIG_ENV_VAR, DEFAULT_CONFIG_FILE)


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=CONFIG_FILE,
        extra="allow",
        env_nested_delimiter="__",
        env_prefix="AAI_",
        case_sensitive=False,
    )
    data_root: Path = Path("data/")
    static_files: Path | None = None  # Path("fronend/dist/")

    db_file: Path = Field(default_factory=lambda data: data["data_root"] / "oracle.db")
    db_lock: Path = Field(
        default_factory=lambda data: data["db_file"].with_suffix(".fslock"),
    )

    result_dir: Path = Field(default_factory=lambda data: data["data_root"] / "results")

    log_config: Path = Path(".config/logging.json")
    loglevel: int = logging.DEBUG
    max_retries: int = 5
    secret_key: str = Field(
        default="password_secret_password_secret_password_secret_password_secret",
        description="Secret used for creating JWT",
    )
    jwt_algorithm: str = Field(
        default="HS256", description="Algorithm used for creating JWT"
    )
    cookie_name: str = Field(default="aai_user_token", description="Name of the cookie")
    cookie_lifetime: int = Field(
        default=60 * 24,
        description="Lifetime of user login tokens in minutes.",
    )

    web_origins: list[str] = []

    empty_loop_sleep: int = Field(
        default=30,
        description="Number of seconds to sleep before trying again if there were no tasks in the queue.",
    )
    wait_loop_timeout: int = Field(
        default=10,
        description="Number of seconds to sleep when the current worker loop iteration found no tasks.",
    )
    listen_loop_timeout: int = Field(
        default=5,
        description="Number of seconds to sleep between worker loop iterations.",
    )

    azure_storage_connection_string: str = Field(
        description="Connection string for storage account",
    )
    azure_storage_account_name: str = Field(
        description="Name of azure storage account",
    )
    azure_storage_account_key: str = Field(
        description="Authentication key for azure storage account"
    )
    azure_storage_use_entra: bool = Field(
        default=False,
        description="When true, using Entra authentication, otherwise using connection string only.",
    )
    azure_storage_container_name: str = Field(
        default="query_results",
        description="Name of the container",
    )
    azure_storage_container_path: Annotated[
        str, AfterValidator(lambda v: v if not v or v.endswith("/") else v + "/")
    ] = Field(
        default="",
        description="Base folder in the container",
    )
    azure_storage_key_lifetime: int = Field(
        default=1440,
        description="Lifetime of azure download tokens in minutes.",
    )
    azure_storage_concurrency: int = Field(
        default=10,
        description="Maximum number of parallel uploads.",
    )

    OPENAPI_FILE: str = "/openapi.json"  # absolute URL path to openapi.json file
    # see https://fastapi.tiangolo.com/advanced/behind-a-proxy/
    OPENAPI_PREFIX: str = ""
    ROOT_PATH: str = ""

    def responses_dir(self, query_id: int) -> Path:
        target = self.result_dir / str(query_id) / "responses"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def converted_dir(self, query_id: int) -> Path:
        target = self.result_dir / str(query_id) / "responses"
        target.mkdir(parents=True, exist_ok=True)
        return target


settings = Config()
