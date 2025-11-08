# Logging Tab Feature Implementation

## Summary

Added a comprehensive logging management interface to the JellyJam settings page with configurable log levels and a live log viewer.

## Features Added

### 1. Enhanced Logging Configuration (`utils/logging_config.py`)

- **CircularBufferHandler**: New handler class that stores log records in a thread-safe circular buffer
  - Configurable capacity (default: 1000 entries)
  - Stores formatted log messages with metadata (timestamp, level, name, message)
  - `get_logs(n, min_level)`: Retrieve recent logs filtered by level
  - `clear()`: Clear all buffered logs
  - `set_capacity()`: Dynamically adjust buffer size

- **Helper Functions**:
  - `get_log_buffer()`: Access the global buffer handler
  - `get_recent_logs(n, min_level)`: Convenience function to retrieve logs
  - `set_log_level(level)`: Dynamically change the logging level

### 2. API Endpoints (`app.py`)

#### GET `/api/logs`
Returns recent log entries as JSON.

**Query Parameters:**
- `n` (int, default: 100): Number of log entries to return
- `level` (string, default: 'DEBUG'): Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

**Response:**
```json
{
  "logs": [
    {
      "timestamp": 1699392000.123,
      "level": "INFO",
      "levelno": 20,
      "name": "app",
      "message": "Server started",
      "formatted": "2025-11-07 12:00:00 - app - INFO - Server started"
    }
  ],
  "count": 1,
  "requested": 100,
  "min_level": "DEBUG"
}
```

#### POST `/settings/logging`
Save logging configuration.

**Form Parameters:**
- `log_level`: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `display_lines`: Number of lines to display in viewer (10-10000)
- `buffer_capacity`: Maximum log entries in memory (100-100000)

### 3. Settings Page - Logging Tab

#### Configuration Section
- **Log Level Selector**: Choose minimum log level to record
  - DEBUG (most verbose) - detailed debugging information
  - INFO (normal) - general informational messages
  - WARNING - potential issues
  - ERROR - operation failures
  - CRITICAL (least verbose) - serious system failures

- **Display Lines**: Number of recent log entries to show (10-10000)

- **Buffer Capacity**: Maximum log entries kept in memory (100-100000)

#### Live Log Viewer
- **Auto-Refresh**: Updates every 2 seconds when tab is active
- **Color-Coded Levels**:
  - DEBUG: Gray (#888)
  - INFO: Blue (#5bc0de)
  - WARNING: Orange (#f0ad4e)
  - ERROR: Red (#d9534f)
  - CRITICAL: Bright Red (#ff0000)

- **Controls**:
  - Auto-scroll checkbox: Automatically scroll to newest logs
  - Filter dropdown: Show only logs at or above selected level
  - Refresh button: Manually reload logs
  - Clear button: Clear all logs from memory (coming soon)

- **Display Format**:
  ```
  12:34:56 INFO     app.player - Track loaded successfully
  12:34:57 WARNING  mqtt.client - Connection timeout, retrying...
  12:34:58 ERROR    hardware.display - Failed to initialize hardware
  ```

## Configuration Storage

Logging settings are stored in `data/config.json`:

```json
{
  "logging": {
    "log_level": "INFO",
    "display_lines": 100,
    "buffer_capacity": 1000
  }
}
```

## Startup Behavior

1. On application startup, logging configuration is read from `config.json`
2. If no configuration exists, defaults are used (INFO level, 1000 buffer capacity)
3. The logging system is initialized before any other modules
4. Log level can be changed dynamically through the settings page
5. Changes to log level take effect immediately without restart
6. Changes to buffer capacity require restart for optimal performance

## Implementation Details

### Thread Safety
- CircularBufferHandler uses threading.Lock() for thread-safe operations
- Safe for concurrent access from multiple request threads

### Memory Management
- Circular buffer automatically discards oldest entries when capacity is reached
- Memory usage is bounded: ~1KB per log entry × capacity
- Default 1000 entry capacity ≈ 1MB memory

### Performance
- Log formatting is done once when emitted
- Filtering by level is efficient (simple integer comparison)
- No disk I/O during normal operation (all in-memory)

## Usage Example

1. Navigate to Settings page
2. Click "Logging" tab
3. Set log level to "DEBUG" for troubleshooting
4. Set display lines to 200 to see more history
5. Click "Save Logging Settings"
6. View live logs in the viewer below
7. Use filter dropdown to show only WARNING+ or ERROR+ logs
8. Check "Auto-scroll" to always see newest logs

## Future Enhancements

- [ ] Implement clear logs button functionality
- [ ] Add log export to file
- [ ] Add log search/filter by module name or message text
- [ ] Add log level statistics (count by level)
- [ ] Add time range filtering
- [ ] Persist logs to disk with rotation
- [ ] Add log download as text file
