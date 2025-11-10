# NFC Reader Plugin Architecture

## Overview
This document describes the plugin-based NFC reader architecture that allows JellyJam to support multiple NFC hardware devices through a unified interface. The system is designed to be extensible, making it easy to add support for new NFC reader hardware.

## Architecture

### Plugin-Based Design
The NFC reader system follows a plugin architecture similar to the display manager:
- **Base Plugin Class**: Defines the interface all NFC readers must implement
- **Plugin Registry**: Central registration of available NFC reader plugins
- **Reader Manager**: Loads and manages the active plugin based on configuration
- **Configuration Storage**: Plugin selection and settings stored in `data/config.json`

### Components

#### 1. Base Plugin Class (`nfc/plugins/base.py`)
Abstract base class that all NFC reader plugins must inherit from.

**Required Methods:**
- `start()`: Initialize hardware and begin listening for cards
- `stop()`: Clean up resources and stop listening
- `simulate_scan(card_id)`: Simulate a card scan for testing

**Optional Methods:**
- `get_config_schema()`: Return configuration field definitions
- `get_plugin_name()`: Return human-readable plugin name
- `validate_config(config)`: Validate plugin configuration

**Key Features:**
- Standardized callback interface
- Built-in error handling
- Configuration validation framework
- Logging integration

#### 2. NFC Reader Manager (`nfc/reader_manager.py`)
Manages plugin lifecycle and provides unified interface to the application.

**Responsibilities:**
- Load active plugin from configuration
- Instantiate plugin with proper configuration
- Handle plugin switching at runtime
- Provide fallback behavior on errors
- Expose plugin registry and metadata

**Key Methods:**
- `start()`: Start the active NFC reader plugin
- `stop()`: Stop the active plugin
- `simulate_scan(card_id)`: Trigger simulated scan
- `set_active_plugin(name, config)`: Switch plugins at runtime
- `get_available_plugins()`: List all registered plugins
- `get_plugin_info(name)`: Get metadata for specific plugin

#### 3. Available Plugins

##### Mock Plugin (`nfc/plugins/mock.py`)
- **Purpose**: Development and testing without hardware
- **Features**: 
  - No hardware requirements
  - Only responds to `simulate_scan()` calls
  - Default plugin if no configuration exists
- **Configuration**: None required

##### PN532 Plugin (`nfc/plugins/pn532.py`)
- **Purpose**: Support for PN532 NFC modules via I2C or SPI
- **Features**:
  - I2C and SPI communication modes
  - Configurable polling and debouncing
  - Optional hardware reset via GPIO
  - Automatic firmware version detection
  - MIFARE card support
- **Requirements**: 
  - `py532lib` Python library
  - I2C or SPI enabled on Raspberry Pi
  - PN532 hardware module

## Configuration

### Storage Format
Configuration is stored in `data/config.json`:

```json
{
  "nfc": {
    "active": "pn532",
    "plugins": {
      "pn532": {
        "interface": "i2c",
        "i2c_bus": 1,
        "i2c_address": 36,
        "reset_pin": 6,
        "poll_interval": 0.5,
        "debounce_time": 1.0
      }
    }
  }
}
```

### Configuration Schema

**Top Level:**
- `active` (string): Identifier of the active plugin (`"mock"` or `"pn532"`)
- `plugins` (object): Plugin-specific configuration objects

**PN532 Plugin Configuration:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `interface` | select | `"i2c"` | Communication interface: `"i2c"` or `"spi"` |
| `i2c_bus` | number | `1` | I2C bus number (I2C mode only) |
| `i2c_address` | number | `36` | I2C address in decimal (0x24 hex) |
| `spi_bus` | number | `0` | SPI bus number (SPI mode only) |
| `spi_device` | number | `0` | SPI chip select (0=CE0, 1=CE1) |
| `reset_pin` | number | `null` | GPIO pin for hardware reset (BCM, optional) |
| `poll_interval` | number | `0.5` | Card polling interval in seconds (0.1-2.0) |
| `debounce_time` | number | `1.0` | Minimum time between same card scans (0.5-5.0) |

## Web UI Settings

### NFC Settings Page
**Location:** Settings → NFC tab

**Features:**
1. **Plugin Selector**
   - Dropdown to choose active NFC reader type
   - Options: Mock (Simulation Only), PN532 (I2C/SPI)

2. **Dynamic Configuration**
   - Form fields change based on selected plugin
   - Plugin-specific help text and validation
   - Real-time interface switching (I2C ↔ SPI)

3. **PN532 Configuration**
   - Interface type selector (I2C or SPI)
   - I2C settings (bus, address) when I2C selected
   - SPI settings (bus, device) when SPI selected
   - Common settings (reset pin, polling, debouncing)
   - Setup instructions and wiring diagrams

4. **Validation**
   - Client-side field validation
   - Server-side configuration validation
   - Plugin-specific validation rules

## PN532 Hardware Setup

### I2C Mode (Recommended)

**Wiring:**
```
PN532 Module    Raspberry Pi
VCC             3.3V (Pin 1)
GND             GND (Pin 6)
SDA             GPIO 2 (Pin 3) - I2C SDA
SCL             GPIO 3 (Pin 5) - I2C SCL
RSTPDN (opt)    Any GPIO (e.g., GPIO 6)
```

**Enable I2C:**
```bash
sudo raspi-config
# Interface Options → I2C → Enable
sudo reboot
```

**Verify I2C:**
```bash
sudo i2cdetect -y 1
# Should show device at address 0x24
```

**Configuration:**
- Interface: I2C
- I2C Bus: 1
- I2C Address: 36 (0x24 hex)
- Reset Pin: 6 (optional)

### SPI Mode

**Wiring:**
```
PN532 Module    Raspberry Pi
VCC             3.3V (Pin 1)
GND             GND (Pin 6)
MOSI            GPIO 10 (Pin 19) - SPI MOSI
MISO            GPIO 9 (Pin 21) - SPI MISO
SCK             GPIO 11 (Pin 23) - SPI SCLK
SS/CS           GPIO 8 (Pin 24) - SPI CE0 or GPIO 7 (Pin 26) - SPI CE1
RSTPDN (opt)    Any GPIO (e.g., GPIO 6)
```

**Enable SPI:**
```bash
sudo raspi-config
# Interface Options → SPI → Enable
sudo reboot
```

**Configuration:**
- Interface: SPI
- SPI Bus: 0
- SPI Device: 0 (for CE0) or 1 (for CE1)
- Reset Pin: 6 (optional)

### PN532 Module DIP Switches
The PN532 module has DIP switches to set the communication mode:

**I2C Mode:**
- Switch 1: ON
- Switch 2: OFF

**SPI Mode:**
- Switch 1: OFF
- Switch 2: ON

## Dependencies

### Python Libraries

**Required for PN532:**
```bash
pip install py532lib
```

**Optional (for enhanced features):**
```bash
pip install RPi.GPIO  # For reset pin control
```

### System Requirements
- Raspberry Pi (any model with GPIO)
- I2C or SPI enabled in `raspi-config`
- Python 3.7+

## API Reference

### NFCReaderPlugin Base Class

```python
class NFCReaderPlugin(ABC):
    def __init__(self, callback=None, config=None):
        """
        Initialize plugin.
        
        Args:
            callback: Function(card_id) called when card detected
            config: Plugin-specific configuration dict
        """
    
    @abstractmethod
    def start(self):
        """Start NFC reader and begin listening."""
        pass
    
    @abstractmethod
    def stop(self):
        """Stop reader and clean up resources."""
        pass
    
    def simulate_scan(self, card_id):
        """Simulate card scan for testing."""
        pass
    
    @classmethod
    def get_config_schema(cls):
        """Return configuration field schema."""
        return {}
    
    @classmethod
    def validate_config(cls, config):
        """Validate configuration, return list of errors."""
        return []
```

### NFCReaderManager

```python
class NFCReaderManager:
    def __init__(self, callback=None, storage=None):
        """Initialize manager with callback and storage."""
        pass
    
    def start(self):
        """Start active NFC plugin."""
        pass
    
    def stop(self):
        """Stop active plugin."""
        pass
    
    def simulate_scan(self, card_id):
        """Trigger simulated scan."""
        pass
    
    def set_active_plugin(self, plugin_name, plugin_config):
        """Switch to different plugin."""
        pass
    
    def get_plugin_name(self):
        """Get human-readable name of active plugin."""
        return "Plugin Name"
    
    @staticmethod
    def get_available_plugins():
        """Get dict of all available plugins."""
        return {"mock": MockNFCPlugin, "pn532": PN532Plugin}
```

## Creating a New Plugin

### Step 1: Create Plugin File
Create `app/nfc/plugins/myplugin.py`:

```python
import logging
from .base import NFCReaderPlugin

log = logging.getLogger(__name__)

class MyPlugin(NFCReaderPlugin):
    def start(self):
        """Initialize your hardware."""
        self._running = True
        # Setup hardware, start polling thread
        log.info('MyPlugin started')
    
    def stop(self):
        """Clean up your hardware."""
        self._running = False
        # Stop threads, close connections
        log.info('MyPlugin stopped')
    
    @classmethod
    def get_plugin_name(cls):
        return "My Custom NFC Reader"
    
    @classmethod
    def get_config_schema(cls):
        return {
            'port': {
                'type': 'string',
                'label': 'Serial Port',
                'default': '/dev/ttyUSB0',
                'required': True,
                'description': 'USB serial port for reader'
            }
        }
```

### Step 2: Register Plugin
Edit `app/nfc/reader_manager.py`:

```python
from .plugins.myplugin import MyPlugin

AVAILABLE_PLUGINS = {
    'mock': MockNFCPlugin,
    'pn532': PN532Plugin,
    'myplugin': MyPlugin,  # Add your plugin
}
```

### Step 3: Add UI Support
Edit `app/templates/settings.html`:

```html
<select id="nfc_active" name="nfc_active">
  <option value="mock">Mock</option>
  <option value="pn532">PN532</option>
  <option value="myplugin">My Custom Reader</option>
</select>

<div id="nfc-config-myplugin" class="nfc-plugin-config">
  <!-- Add configuration fields for your plugin -->
</div>
```

### Step 4: Add Backend Support
Edit `app/app.py` in `settings_nfc()` route:

```python
if active_plugin == 'myplugin':
    port = request.form.get('myplugin_port', '').strip()
    if not port:
        return redirect(url_for('settings', error='Port required'))
    plugin_config['port'] = port
```

## Backend Implementation

### Settings Route Integration

**GET `/settings`:**
- Loads NFC configuration from storage
- Passes `nfc_cfg` to template
- Default to `{'active': 'mock', 'plugins': {}}` if not configured

**POST `/settings/nfc`:**
1. Extract form data (active plugin, plugin-specific fields)
2. Validate plugin exists in registry
3. Build plugin configuration object
4. Run plugin-specific validation
5. Save to `data/config.json` under `nfc` key
6. Log configuration change
7. Redirect with success/error message

**Validation:**
- Plugin exists in registry
- Required fields present
- Type validation (numbers, strings)
- Range validation (e.g., 0.1-2.0 for intervals)
- Plugin-specific rules via `validate_config()`

## Application Integration

### Initialization Sequence

1. **Create Player**: `player = Player(storage)`
2. **Import Manager**: `from nfc.reader_manager import create_nfc_reader`
3. **Create Reader**: `nfc = create_nfc_reader(callback=player.handle_nfc, storage=storage)`
4. **Start Reader**: `nfc.start()`
5. **Log Status**: `log.info('NFC reader started: %s', nfc.get_plugin_name())`

### Backward Compatibility

If reader manager fails to initialize:
```python
try:
    nfc = create_nfc_reader(callback=player.handle_nfc, storage=storage)
    nfc.start()
except Exception as e:
    log.warning('Falling back to legacy NFCReader')
    from nfc.reader import NFCReader
    nfc = NFCReader(callback=player.handle_nfc)
    nfc.start()
```

### Runtime Plugin Switching

**Via Settings UI:**
1. User selects new plugin and configures settings
2. Saves via POST `/settings/nfc`
3. Configuration written to storage
4. **Application restart required** for changes to take effect

**Programmatically:**
```python
nfc.set_active_plugin('pn532', {
    'interface': 'i2c',
    'i2c_bus': 1,
    'i2c_address': 36
})
```

## Error Handling

### Plugin Initialization Errors
- Logged with traceback
- Fallback to mock plugin
- Application continues running
- User notified via logs

### Hardware Communication Errors
- PN532 connection failures logged as errors
- Polling continues with backoff
- Firmware version check validates communication
- GPIO errors logged as warnings

### Configuration Errors
- Validation errors displayed in settings UI
- Invalid values rejected before save
- Safe defaults used when possible
- Plugin-specific validation ensures correctness

## Troubleshooting

### PN532 Not Detected

**I2C Mode:**
```bash
# Check if I2C is enabled
sudo raspi-config  # Interface Options → I2C

# Scan I2C bus
sudo i2cdetect -y 1
# Should show 24 if PN532 is connected and configured for I2C

# Check logs
tail -f data/logs/jellyjam.log | grep -i nfc
```

**Common Issues:**
- DIP switches not set correctly (Switch 1 ON, Switch 2 OFF for I2C)
- Wiring incorrect (check SDA/SCL connections)
- I2C address mismatch (try 0x24 or 0x48)
- Power supply issues (ensure 3.3V, not 5V)

**SPI Mode:**
```bash
# Check if SPI is enabled
sudo raspi-config  # Interface Options → SPI

# Check SPI devices exist
ls -l /dev/spidev*

# Check logs
tail -f data/logs/jellyjam.log | grep -i nfc
```

### py532lib Not Installed
```bash
# Install library
pip install py532lib

# Verify installation
python3 -c "import py532lib; print('OK')"
```

### Permissions Issues
```bash
# Add user to I2C/SPI groups
sudo usermod -a -G i2c $USER
sudo usermod -a -G spi $USER
sudo reboot

# Or run with sudo (not recommended)
sudo python3 app.py
```

### Cards Not Detected
- Check polling interval (try 0.5s)
- Check debounce time (try 1.0s)
- Verify card type (PN532 supports MIFARE cards)
- Check antenna connection on module
- Test with multiple cards

## Performance Considerations

### Polling Interval
- **Lower (0.1-0.3s)**: Faster detection, higher CPU usage
- **Higher (0.5-1.0s)**: Lower CPU, slight detection delay
- **Recommended**: 0.5s for good balance

### Debounce Time
- **Lower (0.5s)**: Faster re-scans, risk of duplicates
- **Higher (2.0s+)**: Prevents duplicates, slower re-scan
- **Recommended**: 1.0s for typical use

### I2C vs SPI
- **I2C**: Fewer wires, shared bus, slightly slower
- **SPI**: More wires, dedicated bus, faster
- **Recommendation**: I2C for simplicity, SPI if performance critical

## Security Considerations

### Card ID Storage
- Card IDs stored in plain text in configuration
- Consider encryption for sensitive deployments
- Limit access to `data/config.json`

### GPIO Permissions
- Reset pin control requires GPIO access
- Run as appropriate user or use GPIO groups
- Avoid running as root when possible

### Network Exposure
- Settings page requires authentication (if implemented)
- Consider SSL/TLS for remote access
- Limit network access to settings endpoints

## Future Enhancements

- [ ] USB NFC reader plugin (ACR122U, etc.)
- [ ] PN532 UART mode support
- [ ] NFC tag writing capabilities
- [ ] Card ID encryption in configuration
- [ ] Live card detection status in UI
- [ ] Plugin hot-swap without restart
- [ ] Card type detection and filtering
- [ ] Multi-reader support (multiple readers simultaneously)
- [ ] NFC emulation mode
- [ ] Advanced MIFARE features (sectors, authentication)

## References

- **py532lib Documentation**: https://github.com/HubertD/py532lib
- **PN532 Datasheet**: https://www.nxp.com/docs/en/user-guide/141520.pdf
- **Raspberry Pi I2C**: https://www.raspberrypi.com/documentation/computers/config_txt.html#i2c
- **Raspberry Pi SPI**: https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#serial-peripheral-interface-spi
- **MIFARE Card Types**: https://www.nxp.com/products/rfid-nfc/mifare-hf/mifare-classic:MC_41863
