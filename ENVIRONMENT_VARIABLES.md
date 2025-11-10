# Environment Variables in JellyJam

This document lists all environment variables used in JellyJam and their current status regarding UI configuration.

## ✅ Configured via UI (Web Settings)

### Rotary Encoders (Settings → Controls)
- `ROTARY_A_PIN` - Rotary encoder 1, channel A pin (fallback if not in config)
- `ROTARY_B_PIN` - Rotary encoder 1, channel B pin (fallback if not in config)
- `ROTARY_BUTTON_PIN` - Rotary encoder 1, button pin (fallback if not in config)
- `ROTARY2_A_PIN` - Rotary encoder 2, channel A pin (fallback if not in config)
- `ROTARY2_B_PIN` - Rotary encoder 2, channel B pin (fallback if not in config)
- `ROTARY2_BUTTON_PIN` - Rotary encoder 2, button pin (fallback if not in config)

### LED Display (Settings → Display)
Most LED settings are configurable via the Display settings page:
- `LED_WIDTH` - Display width (**configurable via UI**)
- `LED_HEIGHT` - Display height (**configurable via UI**)
- `LED_PIN` - GPIO pin for WS2812 LEDs (**configurable via UI**)
- `LED_BRIGHTNESS` - Brightness level 0-255 (**configurable via UI**)
- `LED_BRIGHTNESS_PERCENT` - Brightness as percentage 0-100 (**configurable via UI**)
- `LED_FREQ_HZ` - PWM frequency in Hz (**configurable via UI**)
- `LED_DMA` - DMA channel (**configurable via UI**)
- `LED_INVERT` - Invert signal (**configurable via UI**)
- `LED_CHANNEL` - PWM channel (**configurable via UI**)
- `LED_SERPENTINE` - Serpentine wiring pattern (**configurable via UI**)

### Lighting (Settings → Lighting)
- `LED_NIGHTLIGHT_COUNT` - Number of LEDs in nightlight strip (**configurable via UI**)
- `LED_NIGHTLIGHT_PIN` - GPIO pin for nightlight (**configurable via UI**)

---

## ⚠️ NOT Yet Configurable via UI

### Application/Server Settings
**Location**: `app/app.py`
- **`FLASK_SECRET`** (line 34)
  - Default: `'dev-secret'`
  - Purpose: Flask session encryption key
  - **Should remain environment variable** (security sensitive)

- **`SSL_CERT_PATH`** or **`SSL_CERT`** (line 2519)
  - Purpose: Path to SSL certificate file
  - **Should remain environment variable** (deployment specific)

- **`SSL_KEY_PATH`** or **`SSL_KEY`** (line 2520)
  - Purpose: Path to SSL private key file
  - **Should remain environment variable** (deployment specific)

### Animation Settings
**Location**: `app/app.py`
- **`ANIMATION_DUP_SUPPRESS_SEC`** (line 183)
  - Default: `'0.5'`
  - Purpose: Seconds to suppress duplicate animation triggers
  - **Candidate for UI**: Could add to Display → Advanced settings

- **`PLAY_STARTUP_ANIMATION`** (line 925)
  - Default: `'1'`
  - Purpose: Whether to play animation on startup (1=yes, 0=no)
  - **Candidate for UI**: Could add to Display → Advanced settings

- **`STARTUP_ANIMATION_NAME`** (line 927)
  - Default: `'startup.gif'`
  - Purpose: Name of the startup animation file
  - **Candidate for UI**: Could add to Display → Advanced settings

- **`STARTUP_ANIMATION_SPEED`** (line 928)
  - Default: `'1.0'`
  - Purpose: Playback speed multiplier for startup animation
  - **Candidate for UI**: Could add to Display → Advanced settings

### Music/Media Settings
**Location**: `app/player/local_player.py`
- **`MUSIC_BASE`** or **`MUSIC_DIR`** (line 17)
  - Purpose: Base directory for local music files
  - **Candidate for UI**: Could add to new "Local Music" settings page

---

## Summary by Category

### Security (Keep as ENV vars)
- `FLASK_SECRET`
- `SSL_CERT_PATH` / `SSL_CERT`
- `SSL_KEY_PATH` / `SSL_KEY`

### Already in UI
- All LED display settings (`LED_*`)
- Nightlight settings (`LED_NIGHTLIGHT_*`)
- Rotary encoder settings (`ROTARY*_*_PIN`)

### Good Candidates for UI
1. **Display/Animation Settings** (could add to Display → Advanced tab):
   - `ANIMATION_DUP_SUPPRESS_SEC`
   - `PLAY_STARTUP_ANIMATION`
   - `STARTUP_ANIMATION_NAME`
   - `STARTUP_ANIMATION_SPEED`

2. **Music Settings** (could add new Settings tab):
   - `MUSIC_BASE` / `MUSIC_DIR`

---

## Recommendations

### Keep as Environment Variables
- Security-sensitive settings (secrets, certificates)
- Deployment-specific paths (SSL certs)

### Consider Adding to UI
1. **Startup Animation Settings** - Add to Display settings tab
2. **Music Directory** - Add to new "Media" or "Local Music" settings page
3. **Animation Suppression** - Add to Display → Advanced settings

### Already Complete
- ✅ Display hardware configuration
- ✅ Lighting configuration
- ✅ Rotary encoder configuration
- ✅ MQTT configuration
- ✅ Logging configuration

---

## Environment Variable Fallback Pattern

JellyJam uses a consistent pattern for backward compatibility:

```python
# Config first, environment variable as fallback
setting = config.get('setting_name') or os.environ.get('ENV_VAR_NAME', 'default')
```

This allows:
- **New users**: Configure everything via UI
- **Existing users**: Keep using environment variables
- **Docker/Container deployments**: Override via environment
- **Hybrid approach**: Mix config and environment variables
