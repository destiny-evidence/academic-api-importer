import logging

from academic_apis.utils.config import settings


def init_logging(default_level: int | str | None = None) -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s (%(process)d): %(message)s",
        level=default_level or settings.loglevel,
        # force=True,  # Overrides any existing handlers set earlier by Taskiq or other libraries
    )
    # logging.getLogger('root').setLevel(logging.WARNING)
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
