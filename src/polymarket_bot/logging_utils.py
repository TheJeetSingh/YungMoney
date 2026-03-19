from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("event", "market", "order_id", "token_id", "mode"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


class PrettyFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        msg = record.getMessage()
        if os.getenv("LOG_COLOR", "true").strip().lower() in {"1", "true", "yes"}:
            msg = _colorize(record.levelname, msg)
        return f"{ts} | {record.levelname:<5} | {msg}"


def _colorize(level: str, msg: str) -> str:
    reset = "\033[0m"
    if "ACTION BUY" in msg:
        return f"\033[92m{msg}{reset}"
    if "ACTION SELL" in msg:
        return f"\033[96m{msg}{reset}"
    if "RISK STOP" in msg:
        return f"\033[91m{msg}{reset}"
    if "STATUS" in msg:
        return f"\033[94m{msg}{reset}"
    if "NO_FILL" in msg:
        return f"\033[93m{msg}{reset}"
    if level == "ERROR":
        return f"\033[91m{msg}{reset}"
    if level == "WARNING":
        return f"\033[93m{msg}{reset}"
    return msg


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    handler = logging.StreamHandler()
    style = os.getenv("LOG_STYLE", "pretty").strip().lower()
    if style == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(PrettyFormatter())
    root.addHandler(handler)
