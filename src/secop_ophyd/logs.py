import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Dict, Optional

# Default configuration
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DEFAULT_LOG_DIR = ".secop-ophyd"
DEFAULT_LOG_FILENAME = "secop-ophyd.log"
DEFAULT_ROTATION_WHEN = "H"  # Rotate every hour
DEFAULT_ROTATION_INTERVAL = 1  # Every 1 hour
DEFAULT_BACKUP_COUNT = 48  # Keep logs for 48 hours

# Create a dictionary of log level names to their values
LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


log_file_handlers: Dict[str, TimedRotatingFileHandler] = {}

console_handler: logging.StreamHandler | None = None


def setup_logging(
    name: str = "secop_ophyd",
    level: int = DEFAULT_LOG_LEVEL,
    log_dir: Optional[str] = None,
) -> logging.Logger:
    """
    Set up and configure a logger with timed rotating file handler.

    Parameters:
    ----------
    name : str
        Name of the logger
    level : int
        Logging level (default: INFO)
    log_format : str
        Format string for log messages
    log_dir : Optional[str]
        Directory to store log files (default: .secop-ophyd in current directory)
    log_file : Optional[str]
        Log file name (default: secop-ophyd.log)
    when : str
        Type of interval - can be 'S' (seconds), 'M' (minutes), 'H' (hours),
        'D' (days), 'W0'-'W6' (weekday, 0=Monday), 'midnight'
    interval : int
        How many units between rotations (default: 1 hour)
    backup_count : int
        Number of backup files to keep (default: 48 - for 48 hours of logs)
    console : bool
        Whether to also log to console (default: False)

    Returns:
    -------
    logging.Logger
        Configured logger instance
    """

    log_format: str = DEFAULT_LOG_FORMAT

    log_file: Optional[str] = None
    when: str = DEFAULT_ROTATION_WHEN
    interval: int = DEFAULT_ROTATION_INTERVAL
    backup_count: int = DEFAULT_BACKUP_COUNT

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
    # Use resolved string path as key to ensure consistency
    handler_key = str(log_file_path.resolve())

    if handler_key not in log_file_handlers:
        # Create timed rotating file handler
        file_handler = TimedRotatingFileHandler(
            log_file_path,
            when=when,
            interval=interval,
            backupCount=backup_count,
            encoding="utf-8",
            utc=True,  # Use UTC time for consistency
        )
        file_handler.setFormatter(formatter)
        log_file_handlers[handler_key] = file_handler
        is_new_handler = True

    else:
        # Use existing handler (don't overwrite formatter)
        file_handler = log_file_handlers[handler_key]
        is_new_handler = False

    # Add console handler if requested
    # global console_handler

    # if not console_handler:
    #     console_handler = logging.StreamHandler(sys.stdout)
    #     console_handler.setFormatter(formatter)
    #     logger.addHandler(console_handler)
    # else:
    #     logger.addHandler(console_handler)

    logger.addHandler(file_handler)

    # Log initialization message
    if is_new_handler:
        logger.info(f"Logger '{name}' initialized with new log file: {log_file_path}")
    else:
        logger.info(f"Logger '{name}' added to shared log file: {log_file_path}")

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger by name. If the logger doesn't have handlers, it will be created
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
