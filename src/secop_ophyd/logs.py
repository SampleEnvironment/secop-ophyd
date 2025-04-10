import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# Default configuration
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DEFAULT_LOG_DIR = ".secop-ophyd"
DEFAULT_LOG_FILENAME = "secop-ophyd.log"
DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 10 MB
DEFAULT_BACKUP_COUNT = 5

# Create a dictionary of log level names to their values
LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def setup_logging(
    name: str = "secop_ophyd",
    level: int = DEFAULT_LOG_LEVEL,
    log_format: str = DEFAULT_LOG_FORMAT,
    log_dir: Optional[str] = None,
    log_file: Optional[str] = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    console: bool = False,
) -> logging.Logger:
    """
    Set up and configure a logger with rotating file handler.

    Parameters:
    ----------
    name : str
        Name of the logger
    level : int
        Logging level (default: INFO)
    log_format : str
        Format string for log messages
    log_dir : Optional[str]
        Directory to store log files (default: ~/.secop-ophyd/logs)
    log_file : Optional[str]
        Log file name (default: secop-ophyd.log)
    max_bytes : int
        Maximum size of log file before rotation (default: 10 MB)
    backup_count : int
        Number of backup files to keep (default: 5)
    console : bool
        Whether to also log to console (default: True)

    Returns:
    -------
    logging.Logger
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # If handlers already exist, don't add more
    if logger.handlers:
        return logger

    # Create formatter
    formatter = logging.Formatter(log_format)

    # Set up log directory
    if log_dir is None:
        log_dir = DEFAULT_LOG_DIR

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Set up log file path
    if log_file is None:
        log_file = DEFAULT_LOG_FILENAME

    log_file_path = log_path / log_file

    # Create rotating file handler
    file_handler = RotatingFileHandler(
        log_file_path, maxBytes=max_bytes, backupCount=backup_count
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Add console handler if requested
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    logger.info(f"Logging initialized: {log_file_path}")
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger by name. If the logger doesn't exist, it will be created
    with default settings.

    Parameters:
    ----------
    name : str
        Name of the logger

    Returns:
    -------
    logging.Logger
        Logger instance
    """
    logger = logging.getLogger(name)

    # If the logger doesn't have handlers, set it up with defaults
    if not logger.handlers:
        return setup_logging(name)

    return logger
