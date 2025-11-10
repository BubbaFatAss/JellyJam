# NFC Card Registration with Friendly Names

## Overview

The NFC card registration system allows users to assign friendly, human-readable names to NFC cards instead of working with raw card IDs (like "A1B2C3D4"). This makes the system much more user-friendly when creating mappings and testing cards.

## Features

### Card Management Page (`/nfc-cards`)
- **View all registered cards** in a clean table format showing:
  - Friendly name (e.g., "John's Card", "Playlist 1")
  - Card ID (for reference)
  - Delete button
- **Add new cards** via two methods:
  1. **Scan card**: Click "Add New Card" → "Start Scanning" → scan your NFC card → enter friendly name
  2. **Manual entry**: Click "Add New Card" → "Enter Card ID Manually" → type card ID → enter friendly name

### Enhanced Mappings Page
- **Dropdown selection** of registered cards instead of typing card IDs
- Shows friendly names with card IDs in parentheses for clarity
- **Manual entry option** still available for unregistered cards
- **Existing mappings** display friendly names when available

### Enhanced Simulate Page
- **Dropdown selection** of registered cards for easy testing
- Auto-populates card ID field when selected
- Manual card ID entry still available as fallback

## Usage Workflow

### 1. Register a Card

1. Navigate to **NFC Cards** page (from home navigation)
2. Click **"➕ Add New Card"** button
3. Choose registration method:
   - **Scan card**: Click "Start Scanning", hold card to NFC reader, wait for detection
   - **Manual entry**: Click "Enter Card ID Manually", type the card ID
4. Enter a **friendly name** (e.g., "Kids Playlist", "Mom's Favorites")
5. Click **"💾 Save Card"**

### 2. Create a Mapping Using Friendly Name

1. Navigate to **Home** page
2. In the **Mappings** section:
   - Select your card from the dropdown (shows friendly name)
   - Choose playlist type (Local or Spotify)
   - Enter playlist ID/path
   - Click **"Add Mapping"**

### 3. Test with Simulation

1. In the **Simulate NFC** section:
   - Select your card from the dropdown
   - Click **"Simulate"**
   - Verify the correct playlist plays

## Technical Details

### Data Storage

Cards are stored in `data/config.json` under the `nfc_cards` key:

```json
{
  "nfc_cards": {
    "A1B2C3D4": "John's Card",
    "E5F6G7H8": "Playlist 1",
    "I9J0K1L2": "Kids Music"
  }
}
```

### API Endpoints

- **GET `/nfc-cards`**: Display card management page
- **POST `/nfc-cards/add`**: Register new card
  - Form data: `card_id`, `friendly_name`
  - Validates uniqueness of friendly names
- **POST `/nfc-cards/delete`**: Remove registered card
  - Form data: `card_id`
- **GET `/api/nfc/last-scan`**: Get last scanned card (for registration flow)
  - Returns: `{card_id, timestamp}`

### Scan Detection

When a card is scanned during normal operation:
1. `player.handle_nfc()` is called with the card ID
2. Last scan is tracked in `config['_last_nfc_scan']` with timestamp
3. Registration page polls `/api/nfc/last-scan` every 500ms during "scanning" mode
4. If a recent scan (< 5 seconds old) is detected, card ID is auto-filled

### Backward Compatibility

- Unregistered cards can still be used with manual ID entry
- Existing mappings for unregistered cards continue to work
- Card IDs are always shown alongside friendly names for clarity
- System gracefully handles missing `nfc_cards` configuration

## User Interface

### Navigation
Added to all main pages:
```
🏠 Home | 📱 NFC Cards | ⚙️ Settings
```

### Card Registration Modal
- Clean two-step flow: scan/enter → name
- Visual feedback for scan detection
- 30-second timeout for scanning
- Manual entry fallback always available

### Dropdown Styling
- Shows: `Friendly Name (CARD1234)`
- Includes "Enter Card ID Manually" option
- Clear visual separation from settings dropdowns

## Benefits

1. **Easier management**: "Mom's Playlist" is more memorable than "A1B2C3D4"
2. **Fewer errors**: Dropdown selection prevents typos
3. **Better organization**: See all registered cards at a glance
4. **Flexible**: Manual entry still available when needed
5. **User-friendly**: Non-technical users can manage their own cards

## Future Enhancements

Possible future improvements:
- Edit card friendly names in-place
- Bulk card registration
- Card usage statistics (scan count, last used)
- Export/import card registry
- Card groups or categories
- WebSocket-based real-time scan detection (instead of polling)

## Related Documentation

- [NFC Plugin Architecture](NFC_PLUGIN_ARCHITECTURE.md) - Plugin-based NFC reader system
- [Startup and Music Settings](STARTUP_AND_MUSIC_SETTINGS.md) - Display and music configuration
- [Power Button Feature](POWER_BUTTON_FEATURE.md) - GPIO power button control
