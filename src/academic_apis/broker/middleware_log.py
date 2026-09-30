import io
import logging
import typing
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

from taskiq import TaskiqMessage, TaskiqMiddleware, TaskiqResult


class OutputCaptureMiddleware(TaskiqMiddleware):
    """Captures stdout, stderr, and root logger output into TaskiqResult.log."""

    def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        # Create a string buffer to store prints and logs
        self.buffer = io.StringIO()

        # 1. Capture stdout & stderr (prints)
        self._stdout_redirect = redirect_stdout(self.buffer)
        self._stderr_redirect = redirect_stderr(self.buffer)
        self._stdout_redirect.__enter__()
        self._stderr_redirect.__enter__()

        # 2. Attach a temporary StreamHandler to capture standard logging
        self.log_handler = logging.StreamHandler(self.buffer)
        self.log_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logging.getLogger().addHandler(self.log_handler)

        return message

    @typing.override
    def post_execute(
        self,
        message: TaskiqMessage,
        result: TaskiqResult[Any],
    ) -> None:
        # Clean up logging handler
        logging.getLogger().removeHandler(self.log_handler)

        # Clean up stdout/stderr redirection
        self._stderr_redirect.__exit__(None, None, None)
        self._stdout_redirect.__exit__(None, None, None)

        # Populate result.log with the captured buffer string
        result.log = self.buffer.getvalue()
        self.buffer.close()
