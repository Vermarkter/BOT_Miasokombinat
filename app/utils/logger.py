import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(level: str = "INFO") -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    http_failures_logger = logging.getLogger("one_c_http_failures")
    http_failures_logger.setLevel(logging.ERROR)
    http_failures_logger.propagate = False

    handler_exists = any(
        isinstance(handler, RotatingFileHandler)
        and Path(handler.baseFilename).name == "one_c_http_failures.log"
        for handler in http_failures_logger.handlers
    )
    if not handler_exists:
        rotating_handler = RotatingFileHandler(
            logs_dir / "one_c_http_failures.log",
            maxBytes=1_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        rotating_handler.setLevel(logging.ERROR)
        rotating_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"),
        )
        http_failures_logger.addHandler(rotating_handler)
