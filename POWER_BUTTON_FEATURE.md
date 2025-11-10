# Power Button Feature

## Overview
This document describes the implementation of an optional GPIO-based power button that allows users to toggle the display and lighting system on/off with a physical button press.

## Features

### Power Button Behavior

**First Press (Turn OFF):**
1. Stops any currently playing music (pauses playback)
2. Saves current display brightness
3. Saves current lighting/nightlight brightness
4. Turns off the LED matrix display (brightness = 0, clear display)
5. Turns off nightlight/LED strips (brightness = 0)
6. Sets power state to OFF

**Second Press (Turn ON):**
1. Restores LED matrix display to previous brightness level
2. Restores nightlight/LED strips to previous brightness level
3. Sets power state to ON
4. Music remains paused (user can resume manually if desired)

### Hardware Configuration

**Wiring:**
- Power button connects GPIO pin to ground (GND)
- Internal pull-up resistor is enabled in software
- Button press creates a falling edge (HIGH → LOW transition)
- BCM pin numbering (GPIO 0-40)

**Software Debouncing:**
- Default debounce time: 500ms
- Prevents accidental multiple triggers
- Hardware bounce time: 500ms (RPi.GPIO bouncetime parameter)

## Configuration

### Web UI Settings

**Location:** Settings → Controls → Power Button section

**Fields:**
- **Enable Power Button** (checkbox)
  - Enable or disable GPIO power button functionality
  - Default: disabled (false)
  
- **GPIO Pin** (number input)
  - GPIO pin number in BCM numbering mode
  - Range: 0-40
  - Example: 25
  - Required when enabled

**Important:**
- Application restart required for changes to take effect
- GPIO pin must not conflict with other hardware (rotary encoders, LED strips, etc.)

### Configuration Storage

Settings are stored in `data/config.json`:

```json
{
  "controls": {
    "power_button": {
      "enabled": true,
      "pin": 25
    }
  }
}
```

### Environment Variable Fallback

For backward compatibility, the power button can also be configured via environment variable:

```bash
export POWER_BUTTON_PIN=25
```

**Config-First Pattern:**
- Config value takes precedence
- Environment variable used as fallback
- If neither exists and enabled=true in config, pin is required

## Implementation Details

### Files Modified

#### 1. `app/templates/settings.html`
- **Lines 458-495:** Added Power Button section to Controls tab
  - Enable checkbox
  - GPIO pin input field
  - Informational help text about behavior
  - Wiring instructions (pull-up, ground connection)

#### 2. `app/app.py`

**Controls Settings Backend (lines 850-925):**
- Added power button form field extraction
- Validation: pin required when enabled, range 0-40
- Save to `controls.power_button` config section

**Power State Tracking (lines 988-994):**
```python
power_state = {
    'is_on': True,                    # Current power state
    'saved_brightness': initial_b,    # Display brightness to restore
    'saved_lighting_state': None      # Nightlight brightness to restore
}
```

**Power Button Initialization (lines 1251-1359):**
- Load config with environment variable fallback
- Setup GPIO pin with pull-up resistor
- Register falling-edge event detection
- Implement `_power_button_callback()` handler

**Power Button Callback Logic:**
```python
def _power_button_callback(channel):
    # 1. Debounce (500ms)
    # 2. Check current power state
    # 3. If ON → turn OFF (stop music, save state, dim everything)
    # 4. If OFF → turn ON (restore brightness levels)
    # 5. Toggle power_state['is_on']
```

### GPIO Setup Details

**Pin Configuration:**
```python
GPIO.setmode(GPIO.BCM)
GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.add_event_detect(pin, GPIO.FALLING, 
                      callback=_power_button_callback, 
                      bouncetime=500)
```

**Why FALLING edge?**
- Pull-up resistor keeps pin HIGH when button not pressed
- Button press connects pin to ground → LOW
- Falling edge (HIGH → LOW) triggers callback

### Power OFF Sequence

1. **Stop Music:**
   ```python
   if player and player._state.get('playing'):
       player.pause()
   ```

2. **Save Display State:**
   ```python
   power_state['saved_brightness'] = matrix.get_brightness()
   ```

3. **Turn Off Display:**
   ```python
   matrix.set_brightness(0)
   matrix.clear()
   ```

4. **Turn Off Lighting:**
   ```python
   from hardware.nightlight import nightlight
   power_state['saved_lighting_state'] = nightlight.get_brightness()
   nightlight.set_brightness(0)
   ```

5. **Update State:**
   ```python
   power_state['is_on'] = False
   ```

### Power ON Sequence

1. **Restore Display:**
   ```python
   saved_brightness = power_state.get('saved_brightness', 25)
   matrix.set_brightness(saved_brightness)
   ```

2. **Restore Lighting:**
   ```python
   if power_state.get('saved_lighting_state') is not None:
       nightlight.set_brightness(power_state['saved_lighting_state'])
   ```

3. **Update State:**
   ```python
   power_state['is_on'] = True
   ```

### Error Handling

**Graceful Degradation:**
- Failed music stop → log warning, continue
- Failed display control → log warning, continue
- Failed lighting control → log debug (may not exist), continue
- Failed GPIO setup → log warning, app continues without power button

**Logging:**
- Info: Power button enabled, state changes (ON/OFF)
- Warning: Failures to control music, display, lighting, GPIO setup
- Error: Unexpected exceptions in callback (with traceback)

## Usage

### Configuration Steps

1. **Hardware Setup:**
   - Connect momentary push button between GPIO pin and GND
   - Example: GPIO 25 to one terminal, GND to other terminal
   - No external resistor needed (internal pull-up used)

2. **Software Configuration:**
   - Navigate to Settings → Controls
   - Scroll to "Power Button" section
   - Check "Enable Power Button"
   - Enter GPIO pin number (e.g., 25)
   - Click "Save Controls Settings"
   - Restart the application

3. **Testing:**
   - Press button → display and lights turn off, music stops
   - Press button again → display and lights restore to previous brightness
   - Check logs for confirmation messages

### Recommended GPIO Pins

**Safe Pins for Raspberry Pi (BCM):**
- GPIO 2, 3, 4, 17, 27, 22, 10, 9, 11 (commonly used)
- GPIO 5, 6, 13, 19, 26 (also safe)
- GPIO 25 (good choice, easy to remember)

**Avoid:**
- GPIO 0, 1 (reserved for ID EEPROM)
- GPIO 14, 15 (UART, may interfere with console)
- Pins already used for rotary encoders or LED strips

### Wiring Example

```
Raspberry Pi                Push Button
                           ┌──────────┐
GPIO 25 ───────────────────┤  Terminal  │
                           │     1      │
                           └──────────┘
GND ────────────────────────┤  Terminal  │
                           │     2      │
                           └──────────┘
```

**Button Types:**
- Momentary tactile switch (most common)
- Arcade button (good for enclosures)
- Any normally-open (NO) push button

## Configuration Schema

### Complete Controls Config Example

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
    },
    "power_button": {
      "enabled": true,
      "pin": 25
    }
  }
}
```

## Design Decisions

### Why Pause Music Instead of Stopping?

**Rationale:**
- Preserves playback position
- User can manually resume if desired
- Avoids unexpected playlist restart
- More user-friendly for audiobooks/long tracks

**Alternative Considered:** Complete stop (would require `player.stop()`)

### Why Save and Restore Brightness?

**Rationale:**
- Maintains user's preferred brightness levels
- Seamless on/off experience
- Different brightness preferences for day/night use
- No need to reconfigure after power on

**Alternative Considered:** Always restore to default brightness (less user-friendly)

### Why Clear Display on Power Off?

**Rationale:**
- Visual confirmation that system is "off"
- Saves a tiny bit of power (LEDs fully off)
- Prevents ghosting/artifacts when brightness=0

**Implementation:**
```python
matrix.set_brightness(0)
matrix.clear()  # Explicitly clear pixels
```

### Why Software Debouncing?

**Rationale:**
- Mechanical buttons "bounce" (make/break contact rapidly)
- Without debouncing, single press triggers multiple events
- 500ms is sufficient for most tactile switches
- Combines software debounce (time check) + hardware (RPi.GPIO bouncetime)

**Double Protection:**
1. Manual time check in callback: `now - last_press < 0.5s`
2. RPi.GPIO bouncetime parameter: `bouncetime=500`

## Troubleshooting

### Button Not Responding

**Check:**
1. Is power button enabled in config?
2. Is correct GPIO pin configured?
3. Is button wired correctly (pin to GND)?
4. Check logs for "Power button enabled on GPIO pin X"
5. Check for GPIO conflicts with other hardware

**Debug Steps:**
```bash
# Check if GPIO is accessible
gpio readall  # (if gpio command installed)

# Check application logs
tail -f data/logs/jellyjam.log | grep -i power
```

### Multiple Triggers per Press

**Symptoms:**
- Display flickers on/off rapidly
- Logs show multiple "turning ON/OFF" messages in quick succession

**Causes:**
- Button bouncing
- Loose wiring
- Insufficient debounce time

**Solutions:**
- Check button connections (ensure solid contact)
- Increase `_POWER_DEBOUNCE_SEC` in code (currently 0.5s)
- Use higher quality button with less bounce

### Display/Lights Don't Turn Back On

**Possible Causes:**
1. Matrix/nightlight hardware failure
2. Saved brightness was 0 (nothing to restore)
3. Exception during restore (check logs)

**Debug:**
- Check logs for "Failed to restore display brightness"
- Manually set brightness via web UI
- Verify hardware is functional

### Permission Errors

**Error:** `RuntimeError: No access to /dev/mem`

**Solution:**
```bash
# Run application with appropriate permissions
sudo python app.py

# Or add user to gpio group
sudo usermod -a -G gpio $USER
```

## Testing Recommendations

### Unit Testing Approach

**Test Scenarios:**
1. Enable/disable power button via web UI
2. Save config with valid pin (0-40)
3. Save config with invalid pin (should error)
4. Config without pin when enabled (should error)
5. Load config on startup (config-first, then env var)

### Integration Testing

**Hardware Tests:**
1. Press button → verify music stops
2. Press button → verify display dims to 0
3. Press button → verify lights dim to 0
4. Press again → verify display restores brightness
5. Press again → verify lights restore brightness
6. Rapid presses → verify debouncing works

**Edge Cases:**
1. Power off when nothing playing (should not error)
2. Power off when display already dim (should save 0, restore to 0)
3. Power on/off multiple times in succession
4. Restart app while in "off" state (should start in "on" state)

## Backward Compatibility

✅ **Fully backward compatible**
- Feature is opt-in (disabled by default)
- No changes to existing functionality
- Environment variable fallback maintained
- No breaking changes to config schema
- Existing deployments unaffected

## Future Enhancements

- [ ] Add configurable debounce time in web UI
- [ ] Support long-press for shutdown (OS-level)
- [ ] Add LED indicator for power state (external LED)
- [ ] Support double-press for different action
- [ ] Remember power state across restarts (persistent in config)
- [ ] Add power state API endpoint (GET /api/power/state)
- [ ] Add socketio event for power state changes
- [ ] Support multiple power buttons (zones)
- [ ] Add power schedule (auto-off at night, auto-on in morning)

## Related Features

- **Rotary Encoders:** Hardware controls for volume, skip, brightness
- **MQTT Controls:** Software-based power control via MQTT messages
- **Display Manager:** Handles matrix brightness and clearing
- **Nightlight:** LED strip brightness control
- **Player:** Music playback pause/resume functionality

## References

- RPi.GPIO Documentation: https://pypi.org/project/RPi.GPIO/
- Raspberry Pi GPIO Pinout: https://pinout.xyz/
- Button Debouncing Theory: https://www.allaboutcircuits.com/technical-articles/switch-bounce-how-to-deal-with-it/
