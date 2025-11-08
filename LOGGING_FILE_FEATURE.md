# File Logging Feature

## Overview
JellyJam now supports optional file logging with automatic log rotation. This feature allows you to persist logs to disk while automatically managing file sizes and counts.

## Features

### Rotating Log Files
- **Location**: `data/logs/jellyjam.log`
- **Maximum File Size**: Configurable (default: 10 MB)
- **Maximum Files**: Configurable (default: 5 files)
- **Rotation**: When `jellyjam.log` reaches the maximum size, it's renamed to `jellyjam.log.1`, and older files are shifted (`.1` → `.2`, `.2` → `.3`, etc.)
- **Automatic Cleanup**: The oldest file is deleted when the maximum number of files is exceeded

### Configuration
File logging can be configured via the Settings → Logging tab in the web UI:

1. **Enable File Logging**: Checkbox to turn file logging on/off
2. **Maximum Log Files**: Number of rotating files to keep (1-100, default: 5)
3. **Max File Size (MB)**: Size limit before rotation (1-1000 MB, default: 10)

### File Naming Convention
```
data/logs/
├── jellyjam.log        (current/active log file)
├── jellyjam.log.1      (most recent rotated file)
├── jellyjam.log.2      (older)
├── jellyjam.log.3      (older)
└── jellyjam.log.4      (oldest, deleted when .5 would be created)
```

## Technical Details

### Implementation
- Uses Python's `logging.handlers.RotatingFileHandler`
- All log levels (DEBUG through CRITICAL) are written to files
- UTF-8 encoding for international character support
- Thread-safe logging operations

### Code Changes
1. **`utils/logging_config.py`**: 
   - Added `enable_file_logging`, `log_file_dir`, `max_log_files`, `max_log_size_mb` parameters to `setup_logging()`
   - New `update_file_logging()` function for dynamic configuration updates
   - New `is_file_logging_enabled()` and `get_file_handler()` helper functions

2. **`app.py`**:
   - Loads file logging settings from config on startup
   - Applies file logging configuration if enabled
   - Updates `/settings/logging` endpoint to save file logging preferences

3. **`templates/settings.html`**:
   - Added File Logging section with controls for:
     - Enable/disable checkbox
     - Maximum log files input
     - Max file size input

### Storage Format
Settings are stored in `data/config.json` under the `logging` key:

```json
{
  "logging": {
    "log_level": "INFO",
    "display_lines": 100,
    "buffer_capacity": 1000,
    "enable_file_logging": true,
    "max_log_files": 5,
    "max_log_size_mb": 10
  }
}
```

## Usage Examples

### Enable via Web UI
1. Navigate to Settings → Logging tab
2. Check "Enable File Logging"
3. Set desired max files (e.g., 5)
4. Set desired max size (e.g., 10 MB)
5. Click "Save Logging Settings"

### Programmatic Configuration
```python
from utils.logging_config import update_file_logging

# Enable with custom settings
update_file_logging(
    enable=True,
    log_file_dir='data/logs',
    max_log_files=10,
    max_log_size_mb=5
)

# Disable
update_file_logging(enable=False)
```

### Manual Setup (on startup)
```python
from utils.logging_config import setup_logging

setup_logging(
    level=logging.INFO,
    buffer_capacity=1000,
    enable_file_logging=True,
    log_file_dir='data/logs',
    max_log_files=5,
    max_log_size_mb=10
)
```

## Benefits
1. **Persistence**: Logs survive application restarts
2. **Debugging**: Historical logs available for troubleshooting
3. **Auditing**: Complete record of application events
4. **Automatic Management**: No manual cleanup required
5. **Disk Space Control**: Configurable limits prevent unbounded growth

## Default Behavior
- File logging is **disabled by default**
- When enabled, uses sensible defaults:
  - 5 rotating files
  - 10 MB maximum per file
  - ~50 MB total maximum storage (5 files × 10 MB)

## Notes
- File logging captures **all** log levels (DEBUG through CRITICAL), regardless of the console log level setting
- Log files use the same format as console output: `YYYY-MM-DD HH:MM:SS - logger.name - LEVEL - message`
- Changes to file logging settings take effect immediately without restart
- The `data/logs` directory is created automatically when file logging is enabled
