"""
Centralized logging configuration for JellyJam.

Sets up Python's logging module to:
- Use appropriate log levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- Send ERROR and above to stderr
- Send everything else to stdout
- Use consistent formatting across the application
- Store recent log entries in memory for web UI display
- Optionally write to rotating log files
"""
import logging
import logging.handlers
import sys
import os
from collections import deque
import threading


class StdoutFilter(logging.Filter):
    """Filter that allows only records below ERROR level."""
    def filter(self, record):
        return record.levelno < logging.ERROR


class StderrFilter(logging.Filter):
    """Filter that allows only ERROR and above."""
    def filter(self, record):
        return record.levelno >= logging.ERROR


class CircularBufferHandler(logging.Handler):
    """Handler that stores log records in a circular buffer for web UI display."""
    
    def __init__(self, capacity=1000):
        """
        Initialize the circular buffer handler.
        
        Args:
            capacity: Maximum number of log records to store
        """
        super().__init__()
        self.capacity = capacity
        self.buffer = deque(maxlen=capacity)
        # Use RLock to allow re-entrant calls from the same thread
        self.lock = threading.RLock()
    
    def emit(self, record):
        """Add a log record to the buffer."""
        try:
            # Format the record OUTSIDE the lock to avoid potential recursion issues
            # if the formatter or any code it calls tries to log something
            msg = self.format(record)
            
            # Now acquire lock just for buffer modification
            with self.lock:
                # Store formatted message with metadata
                self.buffer.append({
                    'timestamp': record.created,
                    'level': record.levelname,
                    'levelno': record.levelno,
                    'name': record.name,
                    'message': record.getMessage(),
                    'formatted': msg
                })
        except Exception:
            self.handleError(record)
    
    def get_logs(self, n=None, min_level=logging.DEBUG):
        """
        Get recent log entries.
        
        Args:
            n: Number of entries to return (None for all)
            min_level: Minimum log level to include
            
        Returns:
            List of log entry dicts
        """
        with self.lock:
            # Filter by level
            filtered = [entry for entry in self.buffer if entry['levelno'] >= min_level]
            # Return last n entries
            if n is not None and n > 0:
                return list(filtered[-n:])
            return list(filtered)
    
    def clear(self):
        """Clear all log entries from the buffer."""
        with self.lock:
            self.buffer.clear()
    
    def set_capacity(self, capacity):
        """Change the buffer capacity."""
        with self.lock:
            # Create new deque with new capacity
            new_buffer = deque(self.buffer, maxlen=capacity)
            self.buffer = new_buffer
            self.capacity = capacity


# Global buffer handler instance
_buffer_handler = None
_file_handler = None


def setup_logging(level=logging.INFO, buffer_capacity=1000, 
                  enable_file_logging=False, log_file_dir=None, 
                  max_log_files=5, max_log_size_mb=10):
    """
    Configure logging for the entire application.
    
    Args:
        level: The logging level (default: INFO)
        buffer_capacity: Number of log entries to keep in memory (default: 1000)
        enable_file_logging: Whether to write logs to files (default: False)
        log_file_dir: Directory for log files (default: None, uses data/logs)
        max_log_files: Maximum number of rotating log files to keep (default: 5)
        max_log_size_mb: Maximum size of each log file in MB (default: 10)
    """
    global _buffer_handler, _file_handler
    
    # Create formatters
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Create stdout handler for INFO, DEBUG, WARNING
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.DEBUG)
    stdout_handler.addFilter(StdoutFilter())
    stdout_handler.setFormatter(formatter)
    
    # Create stderr handler for ERROR, CRITICAL
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.addFilter(StderrFilter())
    stderr_handler.setFormatter(formatter)
    
    # Create circular buffer handler for web UI
    _buffer_handler = CircularBufferHandler(capacity=buffer_capacity)
    _buffer_handler.setLevel(logging.DEBUG)  # Capture everything
    _buffer_handler.setFormatter(formatter)
    
    # Create rotating file handler if enabled
    if enable_file_logging:
        if log_file_dir is None:
            # Default to data/logs directory relative to app directory
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            log_file_dir = os.path.join(app_dir, '..', 'data', 'logs')
        
        # Ensure log directory exists
        os.makedirs(log_file_dir, exist_ok=True)
        
        # Create rotating file handler
        log_file_path = os.path.join(log_file_dir, 'jellyjam.log')
        max_bytes = max_log_size_mb * 1024 * 1024  # Convert MB to bytes
        
        _file_handler = logging.handlers.RotatingFileHandler(
            log_file_path,
            maxBytes=max_bytes,
            backupCount=max_log_files - 1,  # backupCount doesn't include the main file
            encoding='utf-8'
        )
        _file_handler.setLevel(logging.DEBUG)  # Capture everything to file
        _file_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove any existing handlers
    root_logger.handlers.clear()
    
    # Add our handlers
    root_logger.addHandler(stdout_handler)
    root_logger.addHandler(stderr_handler)
    root_logger.addHandler(_buffer_handler)
    
    if _file_handler:
        root_logger.addHandler(_file_handler)
    
    return root_logger


def get_logger(name):
    """
    Get a logger instance for a module.
    
    Args:
        name: Name of the module (usually __name__)
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)


def get_log_buffer():
    """
    Get the global log buffer handler.
    
    Returns:
        CircularBufferHandler instance or None if not initialized
    """
    return _buffer_handler


def get_recent_logs(n=100, min_level=logging.DEBUG):
    """
    Get recent log entries from the buffer.
    
    Args:
        n: Number of entries to return
        min_level: Minimum log level to include
        
    Returns:
        List of log entry dicts or empty list if buffer not initialized
    """
    if _buffer_handler:
        return _buffer_handler.get_logs(n, min_level)
    return []


def set_log_level(level):
    """
    Change the log level dynamically.
    
    Args:
        level: New log level (logging.DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    logging.getLogger().setLevel(level)


def get_file_handler():
    """
    Get the global file handler.
    
    Returns:
        RotatingFileHandler instance or None if file logging not enabled
    """
    return _file_handler


def is_file_logging_enabled():
    """
    Check if file logging is currently enabled.
    
    Returns:
        True if file logging is active, False otherwise
    """
    return _file_handler is not None


def update_file_logging(enable=None, log_file_dir=None, max_log_files=None, max_log_size_mb=None):
    """
    Update file logging configuration dynamically.
    
    Args:
        enable: Enable or disable file logging (None = no change)
        log_file_dir: Directory for log files (None = no change)
        max_log_files: Maximum number of rotating files (None = no change)
        max_log_size_mb: Maximum size per file in MB (None = no change)
    
    Note: Changing directory or size requires reinitializing the handler.
    """
    global _file_handler
    
    root_logger = logging.getLogger()
    
    # If disabling, remove the handler
    if enable is False and _file_handler:
        root_logger.removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None
        return
    
    # If enabling or updating settings, recreate the handler
    if enable is True or (enable is None and _file_handler and (log_file_dir or max_log_files or max_log_size_mb)):
        # Remove old handler if it exists
        if _file_handler:
            root_logger.removeHandler(_file_handler)
            old_dir = os.path.dirname(_file_handler.baseFilename)
            old_backup_count = _file_handler.backupCount
            old_max_bytes = _file_handler.maxBytes
            _file_handler.close()
        else:
            # Use defaults
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            old_dir = os.path.join(app_dir, '..', 'data', 'logs')
            old_backup_count = 4  # 5 files total - 1
            old_max_bytes = 10 * 1024 * 1024  # 10 MB
        
        # Apply new settings
        new_dir = log_file_dir if log_file_dir else old_dir
        new_backup_count = (max_log_files - 1) if max_log_files else old_backup_count
        new_max_bytes = (max_log_size_mb * 1024 * 1024) if max_log_size_mb else old_max_bytes
        
        # Ensure directory exists
        os.makedirs(new_dir, exist_ok=True)
        
        # Create new handler
        log_file_path = os.path.join(new_dir, 'jellyjam.log')
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        _file_handler = logging.handlers.RotatingFileHandler(
            log_file_path,
            maxBytes=new_max_bytes,
            backupCount=new_backup_count,
            encoding='utf-8'
        )
        _file_handler.setLevel(logging.DEBUG)
        _file_handler.setFormatter(formatter)
        root_logger.addHandler(_file_handler)
