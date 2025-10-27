"""
LED matrix helper for a 2D WS2812b matrix.

This provides a simple in-memory 16x16 pixel buffer and optional hardware
backing using common Raspberry Pi LED libraries if available. If the
hardware library is not present the module provides a dummy that keeps the
buffer in memory so the web UI can still mirror the display.

API:
 - create_matrix(width=16,height=16) -> matrix object with get_pixels()/set_pixels()
 - get_pixels() returns list of hex strings length width*height (row-major)
 - set_pixels(list_of_hex) accepts row-major list or a nested list
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional
import threading
import time
from pathlib import Path

try:
    from PIL import Image, ImageSequence
    _HAVE_PIL = True
except Exception:
    Image = None
    ImageSequence = None
    _HAVE_PIL = False

log = logging.getLogger(__name__)

try:
    # Try rpi_ws281x if available (best-effort). We won't rely on any read
    # capability as many libraries are write-only; we keep an in-process buffer.
    import rpi_ws281x as ws
    _HAVE_WS = True
except Exception:
    ws = None
    _HAVE_WS = False


class LEDMatrix:
    def __init__(self, width: int = 16, height: int = 16):
        self.width = int(width)
        self.height = int(height)
        # row-major buffer, initial black
        self._buf: List[str] = ['#000000'] * (self.width * self.height)
        # hardware handle if available
        self._hw = None
        # If rpi_ws281x is available, attempt to initialize hardware using env vars.
        if _HAVE_WS:
            try:
                # Environment-driven configuration — keep safe defaults
                num = self.width * self.height
                pin = int(os.environ.get('LED_PIN', '18'))
                freq = int(os.environ.get('LED_FREQ_HZ', '800000'))
                dma = int(os.environ.get('LED_DMA', '10'))
                invert = bool(int(os.environ.get('LED_INVERT', '0')))
                brightness = int(os.environ.get('LED_BRIGHTNESS', '64'))
                channel = int(os.environ.get('LED_CHANNEL', '0'))
                serpentine = os.environ.get('LED_SERPENTINE', '0') in ('1', 'true', 'True')
                self._serpentine = serpentine
                # Optionally provide strip type name (not required)
                strip_type = None
                try:
                    # PixelStrip signature: PixelStrip(num, pin, freq_hz, dma, invert, brightness, channel, strip_type)
                    strip = ws.PixelStrip(num, pin, freq, dma, invert, brightness, channel, strip_type)
                    strip.begin()
                    self._hw = strip
                    log.info('LED matrix hardware initialized: %dx%d on pin %s', self.width, self.height, pin)
                except Exception:
                    log.exception('Failed to initialize LED PixelStrip hardware')
                    self._hw = None
            except Exception:
                log.exception('LED hardware init failed')
                self._hw = None

    def get_pixels(self) -> List[str]:
        """Return row-major list of hex colors (#RRGGBB)."""
        return list(self._buf)

    def set_brightness(self, percent: int):
        """Set brightness as a percentage 0-100. Applies to hardware if present."""
        try:
            p = int(percent)
        except Exception:
            return
        p = max(0, min(100, p))
        # map to 0-255 for rpi_ws281x
        bri = int(p * 255 / 100)
        self._brightness_percent = p
        self._brightness = bri
        if self._hw is not None:
            try:
                # PixelStrip exposes setBrightness
                if hasattr(self._hw, 'setBrightness'):
                    self._hw.setBrightness(bri)
                    try:
                        self._hw.show()
                    except Exception:
                        pass
            except Exception:
                log.exception('Failed to set hardware brightness')

    def get_brightness(self) -> int:
        """Return brightness as percentage 0-100."""
        return int(getattr(self, '_brightness_percent', int(os.environ.get('LED_BRIGHTNESS', '64')) * 100 / 255))

    # --- Animation support ---
    def play_animation_from_gif(self, path: str, speed: float = 1.0, loop: bool = True):
        """Load a GIF and play it on the matrix in background.

        speed: multiplier for frame durations (1.0 = normal, 2.0 = twice as fast)
        loop: whether to loop indefinitely
        """
        if not _HAVE_PIL:
            raise RuntimeError('Pillow not available')
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(path)
        # stop existing animation
        self.stop_animation()
        frames = []
        durations = []
        try:
            img = Image.open(str(p))
            for frame in ImageSequence.Iterator(img):
                f = frame.convert('RGB').resize((self.width, self.height), Image.NEAREST)
                # convert to row-major hex list
                pix = []
                for y in range(self.height):
                    for x in range(self.width):
                        r, g, b = f.getpixel((x, y))
                        pix.append('#%02X%02X%02X' % (r, g, b))
                frames.append(pix)
                # frame.info['duration'] in ms; default to 100ms when missing
                dur = frame.info.get('duration', 100)
                durations.append(max(20, int(dur / (speed or 1.0))))
        except Exception:
            raise

        if not frames:
            raise RuntimeError('No frames in GIF')

        # start thread
        stop_event = threading.Event()
        self._anim_stop_event = stop_event

        def _runner():
            try:
                while not stop_event.is_set():
                    for i, pix in enumerate(frames):
                        if stop_event.is_set():
                            break
                        # write frame to buffer and hardware
                        try:
                            self.set_pixels(pix)
                        except Exception:
                            pass
                        # sleep for duration (ms)
                        time.sleep(durations[i] / 1000.0)
                    if not loop:
                        break
            finally:
                self._anim_stop_event = None

        t = threading.Thread(target=_runner, daemon=True)
        self._anim_thread = t
        t.start()

    def stop_animation(self):
        try:
            ev = getattr(self, '_anim_stop_event', None)
            if ev is not None:
                ev.set()
            th = getattr(self, '_anim_thread', None)
            if th is not None and th.is_alive():
                th.join(timeout=5.0)
        finally:
            self._anim_thread = None
            self._anim_stop_event = None

    def play_wled_json(self, path: str, speed: float = 1.0, loop: bool = True, default_frame_ms: int = 200):
        """Parse a WLED JSON export (single object or multiple concatenated objects) and play them as frames.

        The parser will extract `seg.i` arrays from each object and convert them into flat pixel frames.
        If a frame defines a `bri` value it will be applied before showing that frame. Frame durations are taken
        from an optional `dur` or `duration` field (ms) in the object; otherwise `default_frame_ms` is used.
        """
        # ensure Pillow isn't required for this path
        from pathlib import Path
        import json, re

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(path)

        # stop any existing animation
        self.stop_animation()

        raw = p.read_text(encoding='utf-8')

        # Try to parse as a single JSON value first
        objs = []
        try:
            data = json.loads(raw)
            # If it's a list, treat each element as a frame; if dict, single frame
            if isinstance(data, list):
                objs = data
            elif isinstance(data, dict):
                objs = [data]
        except Exception:
            # Fallback: extract top-level JSON objects with regex and try to parse each
            matches = re.findall(r"\{.*?\}", raw, flags=re.DOTALL)
            for m in matches:
                try:
                    objs.append(json.loads(m))
                except Exception:
                    continue

        if not objs:
            raise RuntimeError('No JSON objects parsed from WLED file')

        frames = []
        durations = []
        bris = []
        num_pixels = self.width * self.height

        for obj in objs:
            seg = None
            if isinstance(obj, dict) and 'seg' in obj:
                seg = obj['seg']
            elif isinstance(obj, dict) and 'presets' in obj and isinstance(obj['presets'], list) and obj['presets']:
                maybe = obj['presets'][0]
                if isinstance(maybe, dict) and 'seg' in maybe:
                    seg = maybe['seg']

            if seg is None:
                # skip objects without seg data
                continue

            i_array = None
            if isinstance(seg, dict) and 'i' in seg:
                i_array = seg['i']
            elif isinstance(seg, list):
                parts = []
                for s in seg:
                    if isinstance(s, dict) and 'i' in s:
                        parts.extend(s['i'])
                i_array = parts

            if not i_array:
                continue

            pix = parse_wled_i_array(i_array, num_pixels)
            frames.append(pix)

            # brightness
            bri = None
            if isinstance(obj, dict) and 'bri' in obj:
                try:
                    b = int(obj.get('bri', 0))
                    bri = int(max(0, min(100, b * 100 // 255)))
                except Exception:
                    bri = None
            bris.append(bri)

            # duration (ms)
            dur = None
            if isinstance(obj, dict):
                for k in ('dur', 'duration', 'ms'):
                    if k in obj:
                        try:
                            dur = int(obj[k])
                            break
                        except Exception:
                            dur = None
            if dur is None:
                dur = default_frame_ms
            # adjust by speed (speed>1 -> faster -> shorter durations)
            if speed and speed > 0:
                dur = max(10, int(dur / speed))
            durations.append(dur)

        if not frames:
            raise RuntimeError('No frames extracted from WLED JSON')

        # Start animation thread
        stop_event = threading.Event()
        self._anim_stop_event = stop_event

        def _runner():
            try:
                while not stop_event.is_set():
                    for idx, pix in enumerate(frames):
                        if stop_event.is_set():
                            break
                        try:
                            # apply brightness for this frame if present
                            if bris[idx] is not None:
                                try:
                                    self.set_brightness(int(bris[idx]))
                                except Exception:
                                    pass
                            self.set_pixels(pix)
                        except Exception:
                            pass
                        time.sleep(durations[idx] / 1000.0)
                    if not loop:
                        break
            finally:
                self._anim_stop_event = None

        t = threading.Thread(target=_runner, daemon=True)
        self._anim_thread = t
        t.start()

    def is_animating(self) -> bool:
        return getattr(self, '_anim_stop_event', None) is not None

    def show_volume_bar(self, volume: int, duration_ms: int = 1500, color: str = '#00FF00', mode: str = 'overlay'):
        """Temporarily overlay a green volume bar on the bottom row of the matrix.

        volume: 0-100 percent
        duration_ms: how long to show the bar in milliseconds

        Overlapping requests cancel the previous overlay and replace it. If an
        animation is running when the overlay ends, the overlay will not restore
        the snapshot (to avoid clobbering the running animation thread).
        """
        try:
            v = int(volume)
        except Exception:
            return
        v = max(0, min(100, v))

        # stop any existing overlay
        try:
            prev_ev = getattr(self, '_vol_overlay_stop', None)
            if prev_ev is not None:
                try:
                    prev_ev.set()
                except Exception:
                    pass
            prev_th = getattr(self, '_vol_overlay_thread', None)
            if prev_th is not None and prev_th.is_alive():
                try:
                    prev_th.join(timeout=0.2)
                except Exception:
                    pass
        except Exception:
            pass

        # normalize color
        try:
            c = str(color or '#00FF00').strip()
            if not c.startswith('#'):
                c = '#' + c
            # normalize 3-char to 6-char
            if len(c) == 4:
                c = '#' + c[1]*2 + c[2]*2 + c[3]*2
            c = c.upper()
        except Exception:
            c = '#00FF00'

        # normalize mode
        m = str(mode or 'overlay').lower()
        if m not in ('overlay', 'pause'):
            m = 'overlay'

        stop_ev = threading.Event()
        self._vol_overlay_stop = stop_ev
        # mark overlay active and mode so other writes can respect 'pause'
        try:
            self._vol_overlay_active = True
            self._vol_overlay_mode = m
        except Exception:
            pass

        def _runner():
            try:
                # snapshot current buffer
                snap = list(self._buf)
                w = self.width
                h = self.height
                filled = int(round((v / 100.0) * w))
                # build overlay copy
                over = list(snap)
                row_start = (h - 1) * w
                # compute fill color and remainder color
                try:
                    hexc = c.lstrip('#')
                    fr = int(hexc[0:2], 16); fg = int(hexc[2:4], 16); fb = int(hexc[4:6], 16)
                except Exception:
                    fr, fg, fb = 0, 255, 0
                # darker remainder color
                dr = max(0, int(fr * 0.12)); dg = max(0, int(fg * 0.12)); db = max(0, int(fb * 0.12))
                filled_color = '#%02X%02X%02X' % (fr, fg, fb)
                empty_color = '#%02X%02X%02X' % (dr, dg, db)
                for x in range(w):
                    idx = row_start + x
                    if x < filled:
                        over[idx] = filled_color
                    else:
                        over[idx] = empty_color
                try:
                    # write overlay
                    # bypass overlay blocking so overlay can always write
                    # store overlay pixels so other writers can merge when overlay is active
                    try:
                        self._vol_overlay_pixels = list(over)
                    except Exception:
                        self._vol_overlay_pixels = None
                    self.set_pixels(over, bypass_overlay=True)
                except Exception:
                    pass

                # wait for duration or stop
                waited = 0
                interval = 0.05
                total = max(0, int(duration_ms) / 1000.0)
                while not stop_ev.is_set() and waited < total:
                    time.sleep(interval)
                    waited += interval

                # restore snapshot if no animation started in the meantime and not stopped
                if not stop_ev.is_set():
                    try:
                        # if mode is 'pause' and there was an animation running, we allowed writes to be blocked
                        # so we only restore if no animation is running
                        if not self.is_animating():
                                try:
                                    # ensure we can restore snapshot even if pause-mode blocking is active
                                    self.set_pixels(snap, bypass_overlay=True)
                                except Exception:
                                    pass
                    except Exception:
                        pass
            finally:
                try:
                    # clear active flag and thread refs
                    self._vol_overlay_thread = None
                    self._vol_overlay_stop = None
                    self._vol_overlay_active = False
                    self._vol_overlay_mode = 'overlay'
                    try:
                        self._vol_overlay_pixels = None
                    except Exception:
                        pass
                except Exception:
                    pass

        t = threading.Thread(target=_runner, daemon=True)
        self._vol_overlay_thread = t
        t.start()

    def set_pixels(self, pixels: Optional[List[str]] = None, bypass_overlay: bool = False):
        """Set the buffer. Accepts flat row-major list or nested list of rows.

        Values should be hex strings like '#RRGGBB'. Non-conforming values
        will be coerced where possible; missing cells are left black.
        """
        # If a volume overlay is active in 'pause' mode, block writes unless bypass_overlay is True
        try:
            if getattr(self, '_vol_overlay_active', False) and not bypass_overlay and getattr(self, '_vol_overlay_mode', 'overlay') == 'pause':
                return
        except Exception:
            pass
        if pixels is None:
            return
        flat: List[str] = []
        # nested list
        if isinstance(pixels, list) and len(pixels) and isinstance(pixels[0], list):
            for row in pixels:
                for v in row:
                    flat.append(self._coerce_color(v))
        else:
            flat = [self._coerce_color(v) for v in pixels]

        # resize or clamp
        expected = self.width * self.height
        # Ensure buffer length matches expected size by padding or truncating
        if len(flat) < expected:
            flat.extend(['#000000'] * (expected - len(flat)))
        if len(flat) > expected:
            flat = flat[:expected]

        # If an overlay is active in 'overlay' mode and this write is not explicitly bypassing,
        # merge the overlay pixels onto the new buffer so the overlay remains visible.
        try:
            if getattr(self, '_vol_overlay_active', False) and not bypass_overlay and getattr(self, '_vol_overlay_mode', 'overlay') == 'overlay':
                overpix = getattr(self, '_vol_overlay_pixels', None)
                if isinstance(overpix, list) and len(overpix) == expected:
                    for i, v in enumerate(overpix):
                        try:
                            if v and v != '#000000':
                                flat[i] = v
                        except Exception:
                            continue
        except Exception:
            pass

        self._buf = flat

        # If hardware backing is present, attempt to write; ignore errors.
        if self._hw is not None:
            try:
                # Map row-major buffer to physical LED indices. Support simple
                # serpentine wiring if requested via env LED_SERPENTINE.
                for y in range(self.height):
                    row_start = y * self.width
                    row = self._buf[row_start:row_start + self.width]
                    if getattr(self, '_serpentine', False) and (y % 2 == 1):
                        row_iter = list(reversed(row))
                    else:
                        row_iter = row
                    for x, col in enumerate(row_iter):
                        # compute physical index
                        phys_x = x if not (getattr(self, '_serpentine', False) and (y % 2 == 1)) else (self.width - 1 - x)
                        idx = y * self.width + phys_x
                        # parse hex color
                        try:
                            hexc = col.lstrip('#')
                            r = int(hexc[0:2], 16)
                            g = int(hexc[2:4], 16)
                            b = int(hexc[4:6], 16)
                        except Exception:
                            r, g, b = 0, 0, 0
                        try:
                            color = ws.Color(r, g, b) if ws is not None else ((r << 16) | (g << 8) | b)
                            self._hw.setPixelColor(idx, color)
                        except Exception:
                            # Some libraries expect different ordering; ignore per-pixel errors
                            pass
                try:
                    self._hw.show()
                except Exception:
                    log.exception('Error calling show() on LED strip')
            except Exception:
                log.exception('Error writing to LED matrix hardware')
        # Notify any listener (e.g., websocket broadcaster) about the new buffer
        try:
            cb = getattr(self, '_on_update', None)
            if cb:
                try:
                    cb(list(self._buf))
                except Exception:
                    log.exception('LEDMatrix on_update callback raised')
        except Exception:
            pass

    @staticmethod
    def _coerce_color(v) -> str:
        try:
            if v is None:
                return '#000000'
            s = str(v).strip()
            if s.startswith('#') and (len(s) == 7 or len(s) == 4):
                # normalize 3-char to 6-char
                if len(s) == 4:
                    r = s[1]*2; g = s[2]*2; b = s[3]*2
                    return f'#{r}{g}{b}'.upper()
                return s.upper()
            # allow integer RGB tuple like '255,0,0' or (255,0,0)
            if ',' in s:
                parts = [int(p.strip()) for p in s.split(',')]
                return '#%02X%02X%02X' % (parts[0], parts[1], parts[2])
            return '#000000'
        except Exception:
            return '#000000'


def create_matrix(width: int = 16, height: int = 16) -> LEDMatrix:
    return LEDMatrix(width=width, height=height)


# --- WLED JSON support helper ---
def parse_wled_i_array(i_array, num_pixels):
    """Parse WLED 'seg.i' style array into a flat row-major pixel list of length num_pixels.

    This attempts to handle common encodings found in WLED presets where the array
    contains integer indices and hex color strings. Strategy:
      - Read triples/groups of (start, end, color...)
      - If multiple colors are present for a range, assign them sequentially (repeating if fewer colors than pixels)
      - Ignore out-of-range indices
    """
    out = ['#000000'] * num_pixels
    import re

    # Normalize: if string colors include leading '#', strip later
    i = 0
    n = len(i_array)
    while i < n:
        item = i_array[i]
        if isinstance(item, int):
            start = int(item)
            i += 1
            # next expected is an int end index (exclusive)
            if i < n and isinstance(i_array[i], int):
                end = int(i_array[i]); i += 1
            else:
                end = start + 1
            # collect following color strings
            colors = []
            while i < n and isinstance(i_array[i], str):
                colors.append(i_array[i])
                i += 1
            # normalize colors
            cols = []
            for c in colors:
                s = str(c).strip()
                if s.startswith('#'):
                    cols.append(s.upper())
                else:
                    cols.append(('#' + s).upper())
            # clamp range
            a = max(0, start)
            b = min(num_pixels, end)
            length = max(0, b - a)
            if length <= 0 or not cols:
                continue
            if len(cols) == 1:
                for p in range(a, b):
                    out[p] = cols[0]
            elif len(cols) >= length:
                # assign one color per pixel
                for k, p in enumerate(range(a, b)):
                    out[p] = cols[k] if k < len(cols) else cols[-1]
            else:
                # fewer colors than pixels: repeat colors cyclically across the range
                k = 0
                for p in range(a, b):
                    out[p] = cols[k % len(cols)]
                    k += 1
        else:
            # If encountering a stray color string without explicit index, try to place it sequentially
            # Find first empty slot
            if isinstance(item, str):
                # find first index that's still black
                try:
                    idx = out.index('#000000')
                    val = item.strip()
                    out[idx] = val if val.startswith('#') else ('#' + val).upper()
                except ValueError:
                    pass
            i += 1
    return out


def parse_wled_json_from_file(path, num_pixels):
    """Load a WLED JSON preset file and return a pixel list length num_pixels and optional brightness percent."""
    import json, re
    raw = Path(path).read_text(encoding='utf-8')
    data = None
    try:
        data = json.loads(raw)
    except Exception:
        # Try splitting on object boundaries like '}
# {' (some WLED exports concatenate objects). This avoids naive regex which
# can't handle nested braces.
        parts = re.split(r"\}\s*\n\s*\{", raw)
        for i, part in enumerate(parts):
            piece = part
            if not piece.strip().startswith('{'):
                piece = '{' + piece
            if not piece.strip().endswith('}'):
                piece = piece + '}'
            try:
                data = json.loads(piece)
                break
            except Exception:
                continue
    # Some normalized exports may be a JSON array of objects (we saved that
    # format for uploads). If so, pick the first object as the active preset
    # (matching the previous behavior of selecting the first concatenated object).
    if isinstance(data, list) and data:
        data = data[0]
    if data is None:
        raise RuntimeError('Failed to parse WLED JSON')

    # Default brightness percent from 'bri' (0-255)
    bri = None
    if isinstance(data, dict) and 'bri' in data:
        try:
            b = int(data.get('bri', 0))
            bri = int(max(0, min(100, b * 100 // 255)))
        except Exception:
            bri = None

    # seg can be dict or list
    seg = None
    if isinstance(data, dict) and 'seg' in data:
        seg = data['seg']
    elif isinstance(data, dict) and 'presets' in data and isinstance(data['presets'], list) and data['presets']:
        # pick first preset
        p = data['presets'][0]
        seg = p.get('seg') if isinstance(p, dict) else None

    if seg is None:
        raise RuntimeError('No segment data in WLED JSON')

    # seg may be a dict with 'i' array, or a list of segments
    i_array = None
    if isinstance(seg, dict) and 'i' in seg:
        i_array = seg['i']
    elif isinstance(seg, list):
        # concatenate 'i' arrays from segments
        parts = []
        for s in seg:
            if isinstance(s, dict) and 'i' in s:
                parts.extend(s['i'])
        i_array = parts

    if not i_array:
        raise RuntimeError('WLED JSON contains no index color data')

    pixels = parse_wled_i_array(i_array, num_pixels)
    return pixels, bri
    
