import logging
import sys


def setup_logging():
    """Configures logging to use stdout and a standard format."""
    # Clear existing handlers to avoid duplicates "red logs" in Railway
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
    )

    # Force common libraries to follow our lead
    for logger_name in ["nova", "agno", "telegram", "httpx"]:
        logger = logging.getLogger(logger_name)
        logger.propagate = True
        if logger_name in ["telegram", "httpx"]:
            logger.setLevel(logging.WARNING)
        else:
            logger.setLevel(logging.INFO)

    # Initialize Error Bus by default to capture all errors for Nova self-healing
    try:
        from nova.tools.core.error_bus import start_error_bus

        start_error_bus()
    except Exception as e:
        print(f"Failed to auto-start error bus: {e}")
