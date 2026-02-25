import logging
import sys

def setup_logging():
    """Configures logging to use stdout and a standard format."""
    # Clear existing handlers to avoid duplicates "red logs" in Railway
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        stream=sys.stdout
    )

    # Force common libraries to follow our lead
    for logger_name in ["telegram", "httpx", "nova", "agno"]:
        logger = logging.getLogger(logger_name)
        logger.propagate = True
        logger.setLevel(logging.INFO)
