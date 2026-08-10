import logging


def setup_logging(name: str, filename: str = "var/output.log", level: int = logging.INFO) -> logging.Logger:
    """Configure the root logger once, at program startup, and return a named logger"""
    logging.basicConfig(
        filename=filename,
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    return logging.getLogger(name)


logger: logging.Logger = logging.getLogger(__name__)
