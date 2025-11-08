# JellyJam Utilities

## logging_config.py

Centralized logging configuration for the entire JellyJam application.

### Features

- **Dual Stream Routing**: Automatically routes log messages to appropriate streams
  - stdout: DEBUG, INFO, WARNING
  - stderr: ERROR, CRITICAL
- **Consistent Formatting**: All logs include timestamp, logger name, level, and message
- **Easy Integration**: Simple factory function for creating module-specific loggers

### Usage

#### Initialize Logging (once at application startup)

```python
from utils.logging_config import setup_logging
import logging

# Initialize with desired log level
setup_logging(level=logging.INFO)
```

#### Use Logging in Modules

```python
from utils.logging_config import get_logger

log = get_logger(__name__)

# Use standard logging methods
log.debug('Detailed debugging information')
log.info('General informational messages')
log.warning('Warning messages for potential issues')
log.error('Error messages for failures')
log.critical('Critical failures requiring immediate attention')

# Include exception traceback
try:
    risky_operation()
except Exception as e:
    log.exception('Operation failed')  # Automatically includes traceback
    # OR
    log.error('Operation failed: %s', e, exc_info=True)
```

### Configuration

The logging system is configured with:

- **Format**: `'%(asctime)s - %(name)s - %(levelname)s - %(message)s'`
- **Date Format**: Default ISO-8601 format
- **Handlers**: Two StreamHandlers (stdout and stderr) with custom filters

### Log Levels

- **DEBUG (10)**: Detailed diagnostic information for development and troubleshooting
- **INFO (20)**: Confirmation that things are working as expected
- **WARNING (30)**: An indication of potential problems or unexpected situations
- **ERROR (40)**: A more serious problem that prevented a function from completing
- **CRITICAL (50)**: A serious error that may prevent the application from continuing

### Advanced Usage

#### Change Log Level Dynamically

```python
import logging
logging.getLogger().setLevel(logging.DEBUG)
```

#### Get Logger for Specific Module

```python
log = get_logger('my_module_name')
log.info('This will show as my_module_name in logs')
```

### Implementation Details

#### StdoutFilter
Filters log records to only pass DEBUG, INFO, and WARNING levels to stdout.

#### StderrFilter
Filters log records to only pass ERROR and CRITICAL levels to stderr.

#### setup_logging(level)
Configures the root logger with:
- Two StreamHandlers (one for stdout, one for stderr)
- Custom filters for proper routing
- Consistent formatting
- Specified log level (default: INFO)

#### get_logger(name)
Factory function that returns a logger instance for the specified module name.
