import logging
import os
from pathlib import Path

from pydantic import Field
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

    db_file: Path = Field(default_factory=lambda data: data["data_root"] / "oracle.db")
    db_lock: Path = Field(
        default_factory=lambda data: data["db_file"].with_suffix(".fslock"),
    )
    db_verbose: bool = False

    result_dir: Path = Field(default_factory=lambda data: data["data_root"] / "results")

    loglevel: int = logging.DEBUG
    max_retries: int = 5
    access_tokens: set[str] = set()
    admin_tokens: set[str] = set()

    web_origins: list[str] = []

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
