"""One JSON object per log line, for log collectors (``log_format: json``)."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Format a record as ``{"time", "level", "logger", "message", "request_id", "exc_info"?}`` on one line."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize the record; an exception goes in ``exc_info`` as text."""
        payload = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)
