# Startup Animation and Local Music Settings Feature

## Overview
This document describes the implementation of UI settings for startup animations and local music directory configuration, replacing environment variable configuration with user-friendly web interface controls.

## Features Added

### 1. Startup Animation Settings (Display → Advanced Tab)

**Location:** Settings page → Display tab → Advanced sub-tab

**Configuration Options:**
- **Play Startup Animation** (checkbox)
  - Enable/disable the startup animation
  - Default: enabled (true)
  - Config key: `display.play_startup_animation`
  - Env fallback: `PLAY_STARTUP_ANIMATION`

- **Animation File** (text input)
  - Filename of the animation to play
  - Must exist in animations directory
  - Default: `startup.gif`
  - Config key: `display.startup_animation_name`
  - Env fallback: `STARTUP_ANIMATION_NAME`

- **Animation Speed** (number input)
  - Playback speed multiplier
  - Range: 0.1 (slower) to 10.0 (faster)
  - Step: 0.1
  - Default: 1.0
  - Config key: `display.startup_animation_speed`
  - Env fallback: `STARTUP_ANIMATION_SPEED`

- **Duplicate Suppression** (number input)
  - Time in seconds to suppress duplicate animation triggers
  - Range: 0.0 to 10.0 seconds
  - Step: 0.1
  - Default: 0.5
  - Config key: `display.animation_dup_suppress_sec`
  - Env fallback: `ANIMATION_DUP_SUPPRESS_SEC`

**Important Notes:**
- Application restart required for changes to take effect
- Settings are saved to `data/config.json` under the `display` key
- Environment variables serve as fallback if config values are not set

### 2. Local Music Directory Settings (New Tab)

**Location:** Settings page → Local Music tab

**Configuration Options:**
- **Music Directory** (text input)
  - Absolute path to the directory containing music files
  - Default: `/music`
  - Config key: `local_music.music_directory`
  - Env fallback: `MUSIC_BASE` or `MUSIC_DIR`

**Features:**
- Path validation (cannot be empty)
- Help text with tips for configuration
- Supported audio formats: MP3, FLAC, WAV, OGG, M4A, AAC
- Subdirectories are scanned recursively

**Important Notes:**
- Application restart required for changes to take effect
- Settings are saved to `data/config.json` under the `local_music` key
- Directory must be readable by the application

## Implementation Details

### Files Modified

#### 1. `app/templates/settings.html`
- Added "Local Music" tab button to main navigation (line 13)
- Added startup animation form fields to Display → Advanced tab (lines 127-166)
- Added Local Music tab content with music directory form (lines 471-517)
- Updated JavaScript to handle new tab switching (lines 518-590)

#### 2. `app/app.py`
- **Settings Route Updates (lines 604-650):**
  - Added `local_music_cfg` loading from config
  - Pass `local_music_cfg` to template context

- **Display Settings POST Handler (lines 567-604):**
  - Extract startup animation settings from form
  - Validate and convert settings (speed 0.1-10, suppression 0-10)
  - Save to `display` config section

- **New POST Endpoint (lines 889-917):**
  - `@app.route('/settings/local-music', methods=['POST'])`
  - Validates music directory (non-empty)
  - Saves to `local_music` config section
  - Returns to settings with success/error message

- **Startup Animation Initialization (lines 993-1007):**
  - Load settings from config with env fallback
  - Support for `play_startup_animation`, `startup_animation_name`, `startup_animation_speed`
  - Maintains backward compatibility with environment variables

- **Animation Duplicate Suppression (lines 179-187):**
  - Load `animation_dup_suppress_sec` from config
  - Fallback to environment variable if config not available

#### 3. `app/player/local_player.py`
- **Constructor Update (lines 7-38):**
  - Added optional `storage` parameter to `__init__`
  - Load music directory from config first
  - Fallback to `MUSIC_BASE` or `MUSIC_DIR` environment variables
  - Fallback to default `music/` directory in repo root

#### 4. `app/player/player.py`
- **LocalPlayer Instantiation (line 10):**
  - Pass `storage` object to `LocalPlayer(storage)` constructor
  - Enables config-first loading of music directory

## Configuration Schema

### config.json Structure
```json
{
  "display": {
    "active": "ws2812",
    "plugins": { ... },
    "play_startup_animation": true,
    "startup_animation_name": "startup.gif",
    "startup_animation_speed": 1.0,
    "animation_dup_suppress_sec": 0.5
  },
  "local_music": {
    "music_directory": "/music"
  }
}
```

## Usage

### Configuring Startup Animation
1. Navigate to Settings → Display → Advanced
2. Scroll to "Startup Animation" section
3. Configure desired settings:
   - Enable/disable with checkbox
   - Set animation filename
   - Adjust playback speed (0.1-10.0)
   - Set duplicate suppression time (0-10 seconds)
4. Click "Save"
5. Restart application for changes to take effect

### Configuring Music Directory
1. Navigate to Settings → Local Music
2. Enter absolute path to music directory (e.g., `/music`, `/home/user/music`)
3. Click "Save Local Music Settings"
4. Restart application for changes to take effect

## Migration Notes

### From Environment Variables
Users previously using environment variables can migrate to UI settings:

**Before:**
```bash
export PLAY_STARTUP_ANIMATION=1
export STARTUP_ANIMATION_NAME=startup.gif
export STARTUP_ANIMATION_SPEED=1.5
export ANIMATION_DUP_SUPPRESS_SEC=0.5
export MUSIC_DIR=/home/pi/music
```

**After:**
1. Configure via Settings UI
2. Environment variables still work as fallback
3. UI settings take precedence over environment variables
4. Restart application after saving

## Backward Compatibility

✅ **Fully backward compatible**
- Environment variables continue to work
- Config values take precedence when available
- Graceful fallback chain: Config → Environment → Defaults
- No breaking changes to existing deployments

## Technical Notes

### Config-First Pattern
```python
# Example from app.py
try:
    cfg = storage.load() or {}
    display_cfg = cfg.get('display', {})
    play_startup = display_cfg.get('play_startup_animation', 
                                   os.environ.get('PLAY_STARTUP_ANIMATION', '1'))
except Exception:
    play_startup = os.environ.get('PLAY_STARTUP_ANIMATION', '1')
```

### Storage Parameter Passing
```python
# Player class passes storage to LocalPlayer
class Player:
    def __init__(self, storage):
        self.storage = storage
        self.local = LocalPlayer(storage)  # Pass storage for config loading
```

### Form Validation
- Music directory: non-empty string
- Animation speed: float 0.1-10.0, default 1.0
- Duplicate suppression: float 0.0-10.0, default 0.5
- Invalid values fall back to defaults

## Testing Recommendations

1. **UI Testing:**
   - Verify all form fields render correctly
   - Test tab switching (Display, Local Music)
   - Confirm save/cancel buttons work
   - Check validation messages appear for invalid input

2. **Config Testing:**
   - Save settings and verify `data/config.json` updates
   - Restart app and confirm settings persist
   - Test config-first pattern with various combinations:
     - Config only (no env vars)
     - Env vars only (no config)
     - Both (config should win)
     - Neither (defaults should apply)

3. **Functional Testing:**
   - Verify startup animation plays with configured settings
   - Test different animation speeds
   - Confirm duplicate suppression works
   - Verify music directory path is used by local player
   - Test with various music file formats

4. **Edge Cases:**
   - Empty music directory (should show validation error)
   - Non-existent animation file (should handle gracefully)
   - Invalid speed values (should fall back to 1.0)
   - Storage load failures (should fall back to env vars)

## Future Enhancements

- [ ] Add file browser for music directory selection
- [ ] Preview animation before saving
- [ ] Real-time speed adjustment preview
- [ ] Music directory validation (check if path exists and is readable)
- [ ] Animation library browser/uploader
- [ ] Per-animation speed presets
- [ ] Custom animation playlists for different events
