# Logging System Refactoring

## Summary

Refactored the entire JellyJam application to use a centralized logging system with proper log levels and stdout/stderr routing.

## Changes Made

### 1. Created Centralized Logging Configuration (`app/utils/logging_config.py`)

- **`StdoutFilter`**: Routes DEBUG, INFO, and WARNING messages to stdout
- **`StderrFilter`**: Routes ERROR and CRITICAL messages to stderr
- **`setup_logging(level)`**: Configures the root logger with dual stream handlers
- **`get_logger(name)`**: Factory function for module-specific loggers

### 2. Updated Application Modules

#### `app/app.py`
- Added logging initialization at startup with `setup_logging(level=logging.INFO)`
- Replaced 5 print() statements with appropriate log levels:
  - Nightlight init failure: `log.warning()`
  - MQTT init success: `log.info()`
  - MQTT init failure: `log.warning()`
  - MQTT restart error: `log.error()`
  - Audiobooks module load failure: `log.exception()`

#### `app/hardware/nightlight.py`
- Added `get_logger(__name__)` import
- Replaced 5 print() statements:
  - Initialization success: `log.info()`
  - Hardware init failure: `log.warning()` + `log.info()` (simulation mode)
  - Strip init error: `log.error()`
  - Callback error: `log.error()`

#### `app/mqtt/mqtt_client.py`
- Added `get_logger(__name__)` import
- Replaced 24 print() statements with appropriate log levels:
  - Connection messages: `log.info()`
  - Debug messages (Socket.IO events, display state): `log.debug()`
  - Warnings (missing libraries, disconnection): `log.warning()`
  - Errors (connection failures, command handling): `log.error()`
  - Used `exc_info=True` parameter for exception context

#### `app/hardware/display_manager.py`
- Changed from `logging.getLogger(__name__)` to `get_logger(__name__)`
- Already had good logging practices (using log.exception, log.warning, log.info)

#### `app/hardware/ledmatrix.py`
- Changed from `logging.getLogger(__name__)` to `get_logger(__name__)`
- Already had good logging practices (using log.exception, log.info)

## Log Level Guidelines Applied

- **DEBUG**: Detailed debugging information (Socket.IO events, MQTT payloads)
- **INFO**: General informational messages (connections, initialization)
- **WARNING**: Potential issues (missing libraries, optional failures)
- **ERROR**: Operation failures (command handling errors, hardware issues)
- **CRITICAL**: Not currently used (reserved for application-critical failures)

## Benefits

1. **Structured Logging**: All logging uses Python's built-in logging module
2. **Proper Severity Levels**: Messages are categorized appropriately
3. **Stream Routing**: Errors go to stderr, everything else to stdout
4. **Consistent Format**: All logs include timestamp, module name, level, and message
5. **Better Production Monitoring**: Easy to filter errors in production logs
6. **Improved Debugging**: Debug-level messages available when needed without cluttering normal output

## Testing

To verify the logging system works correctly:

1. Run the application normally (INFO level)
2. Check stdout for normal operation messages
3. Check stderr for any error messages
4. To enable debug logging, modify `app.py` line 12:
   ```python
   setup_logging(level=logging.DEBUG)
   ```

## Future Improvements

- Add log file rotation for persistent logging
- Make log level configurable via environment variable or config file
- Add structured logging with JSON format for easier parsing
- Implement per-module log level configuration
