# Controls Settings Feature

## Overview
Rotary encoder pin configuration has been moved from environment variables to a dedicated **Controls** settings page in the web UI. This provides a more user-friendly way to configure hardware input devices.

## Changes Made

### 1. New Settings Tab
Added a "Controls" tab to the Settings page (`/settings`) alongside Display, Lighting, Artwork, MQTT, and Logging tabs.

### 2. Controls Configuration UI
The Controls tab provides configuration for two rotary encoders:

#### Rotary Encoder 1 (Volume Control)
- **Enable checkbox**: Turn encoder on/off
- **Pin A (CLK)**: GPIO pin for encoder channel A
- **Pin B (DT)**: GPIO pin for encoder channel B  
- **Button Pin (Optional)**: GPIO pin for push button (play/pause toggle)

#### Rotary Encoder 2 (Skip/Brightness Control)
- **Enable checkbox**: Turn encoder on/off
- **Pin A (CLK)**: GPIO pin for encoder channel A
- **Pin B (DT)**: GPIO pin for encoder channel B
- **Button Pin (Optional)**: GPIO pin for push button (toggles between skip mode and brightness mode)

### 3. Configuration Storage
Settings are stored in `data/config.json` under the `controls` key:

```json
{
  "controls": {
    "rotary1": {
      "enabled": true,
      "a_pin": 17,
      "b_pin": 18,
      "button_pin": 27
    },
    "rotary2": {
      "enabled": true,
      "a_pin": 22,
      "b_pin": 23,
      "button_pin": 24
    }
  }
}
```

### 4. Backward Compatibility
The system maintains backward compatibility with environment variables:
- `ROTARY_A_PIN`, `ROTARY_B_PIN`, `ROTARY_BUTTON_PIN` for encoder 1
- `ROTARY2_A_PIN`, `ROTARY2_B_PIN`, `ROTARY2_BUTTON_PIN` for encoder 2

If both config and environment variables exist, config takes precedence. If only environment variables are set, they will be used.

### 5. Validation
The settings endpoint validates:
- Pin numbers must be between 0-40 (BCM GPIO numbering)
- Both A and B pins are required if encoder is enabled
- Button pin is optional

### 6. Application Restart Required
**Important**: Changes to rotary encoder settings require an application restart to take effect. The UI displays a warning message about this requirement.

## Files Modified

### Backend (`app/app.py`)
1. **Updated `settings()` route** (line ~613):
   - Added `controls_cfg` to template context
   
2. **New route `/settings/controls`** (POST):
   - Handles form submission for rotary encoder settings
   - Validates pin numbers
   - Saves configuration to storage
   
3. **Updated rotary encoder initialization** (lines ~1020-1080):
   - Now reads from config first, falls back to environment variables
   - Added logging for encoder startup
   - Improved error handling with specific log messages

### Frontend (`templates/settings.html`)
1. **Added Controls tab button** to main navigation
2. **Created Controls tab content** with form fields for both encoders
3. **Updated JavaScript** to handle Controls tab switching
4. **Added warning message** about restart requirement

## Usage

### Via Web UI
1. Navigate to **Settings** → **Controls** tab
2. Enable the encoders you want to use
3. Enter the GPIO pin numbers (BCM numbering)
4. Optionally configure button pins
5. Click "Save Controls Settings"
6. **Restart the application** for changes to take effect

### Via Environment Variables (Legacy)
You can still use environment variables if preferred:

```bash
# Encoder 1 (Volume)
export ROTARY_A_PIN=17
export ROTARY_B_PIN=18
export ROTARY_BUTTON_PIN=27

# Encoder 2 (Skip/Brightness)
export ROTARY2_A_PIN=22
export ROTARY2_B_PIN=23
export ROTARY2_BUTTON_PIN=24
```

## Benefits
1. **User-Friendly**: No need to edit environment variables or config files
2. **Documented**: UI includes descriptions of what each pin does
3. **Validated**: Invalid pin numbers are rejected with clear error messages
4. **Persistent**: Settings saved to config.json survive restarts
5. **Discoverable**: All controls in one place in the Settings UI

## GPIO Pin Reference (BCM Numbering)
Common GPIO pins on Raspberry Pi:
- GPIO 2-27 are generally safe to use
- Avoid GPIO 0, 1 (I2C), 14, 15 (UART) if those interfaces are needed
- Refer to your Raspberry Pi's pinout diagram for specific pin locations

## Troubleshooting
- **Encoder not responding**: Check that pins are correct and encoder is enabled in settings
- **Settings not applied**: Remember to restart the application after saving
- **Pin conflicts**: Ensure pins aren't used by other hardware (displays, buttons, etc.)
- **Check logs**: Startup logs will indicate if encoders started successfully
