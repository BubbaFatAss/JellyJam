"""
Display manager and plugin system for multiple LED matrix backends.

This module provides a unified `create_matrix(width,height,storage=None)`
factory which returns an object with the same surface methods used by
the rest of the app (get_pixels, set_pixels, set_brightness, play_animation_from_gif,
play_wled_json, stop_animation, is_animating, show_volume_bar, width, height,
and an `_on_update` callback).

Plugins are responsible for resizing a provided PIL Image to their native
output size when `show_image()` is called. The manager accepts traditional
flat pixel lists as before and converts them into a PIL Image before
delegating to the active plugin.
"""
from __future__ import annotations

import logging
import json
import threading
import time
from typing import Optional, Dict, Any, List
from pathlib import Path

log = logging.getLogger(__name__)

try:
    from PIL import Image
    _HAVE_PIL = True
except Exception:
    Image = None
    _HAVE_PIL = False


class BasePlugin:
    """Base plugin interface to implement."""

    def __init__(self, width: int, height: int, cfg: Optional[Dict[str, Any]] = None):
        self.width = int(width)
        self.height = int(height)
        self.cfg = cfg or {}
        # callback to notify when pixels change: cb(list_of_hex)
        self._on_update = None
        # animation playback state
        self._anim_thread = None
        self._anim_stop = threading.Event()
        # pause flag: when set, animation runner should wait until cleared
        self._anim_paused = threading.Event()
        self._anim_lock = threading.Lock()

    def set_on_update(self, cb):
        self._on_update = cb

    def fast_write_flat(self, flat_pixels: Optional[List[str]]):
        """Optional fast-path for writing a flat row-major list of hex colors.

        Default implementation falls back to set_pixels. Plugins that have
        direct hardware access can override this to perform a faster write.
        """
        if flat_pixels is None:
            return
        return self.set_pixels(flat_pixels)

    # New preferred API: accept a PIL Image and render it to the matrix.
    def show_image(self, img: 'Image.Image'):
        raise NotImplementedError()

    # Backwards compatibility APIs used by the rest of the app
    def set_pixels(self, pixels: Optional[List[str]] = None, bypass_overlay: bool = False):
        # default: convert pixels to PIL image and call show_image
        if not _HAVE_PIL:
            return
        if pixels is None:
            return
        # pixels may be nested rows or flat
        flat = []
        if isinstance(pixels, list) and pixels and isinstance(pixels[0], list):
            for row in pixels:
                for v in row:
                    flat.append(self._coerce_color(v))
        else:
            for v in pixels:
                flat.append(self._coerce_color(v))
        # clamp/pad
        expected = self.width * self.height
        if len(flat) < expected:
            flat.extend(['#000000'] * (expected - len(flat)))
        if len(flat) > expected:
            flat = flat[:expected]

        # build PIL image
        img = Image.new('RGB', (self.width, self.height))
        for y in range(self.height):
            for x in range(self.width):
                hexc = flat[y * self.width + x].lstrip('#')
                try:
                    r = int(hexc[0:2], 16); g = int(hexc[2:4], 16); b = int(hexc[4:6], 16)
                except Exception:
                    r, g, b = 0, 0, 0
                img.putpixel((x, y), (r, g, b))
        try:
            self.show_image(img)
        except Exception:
            log.exception('Plugin show_image failed')

    def get_pixels(self):
        # best-effort: not all plugins expose readable buffer; return black buffer
        return ['#000000'] * (self.width * self.height)

    def set_brightness(self, percent: int):
        return

    def get_brightness(self) -> int:
        return 0

    def play_animation_from_gif(self, path: str, speed: float = 1.0, loop: bool = True):
        # Generic GIF playback implementation using PIL. Runs in a background thread.
        if not _HAVE_PIL:
            log.warning('PIL not available; cannot play GIF')
            return
        # stop any existing animation
        try:
            self.stop_animation()
        except Exception:
            pass

        def _runner():
            try:
                from PIL import Image, ImageSequence
                im = Image.open(path)
                frames = []
                durations = []
                for frame in ImageSequence.Iterator(im):
                    f = frame.convert('RGB').resize((self.width, self.height), Image.NEAREST)
                    frames.append(f.copy())
                    dur = frame.info.get('duration', 100)
                    durations.append(dur / 1000.0)
                if not frames:
                    return
                self._anim_stop.clear()
                self._anim_paused.clear()
                while not self._anim_stop.is_set():
                    for idx, f in enumerate(frames):
                        # honor pause flag: block here until resumed or stopped
                        while self._anim_paused.is_set() and not self._anim_stop.is_set():
                            time.sleep(0.05)
                        if self._anim_stop.is_set():
                            break
                        try:
                            # prefer plugin image API
                            try:
                                self.show_image(f)
                            except Exception:
                                # fallback: build flat pixels and use plugin fast-write when available
                                flat = []
                                for y in range(self.height):
                                    for x in range(self.width):
                                        r, g, b = f.getpixel((x, y))
                                        flat.append('#%02X%02X%02X' % (r, g, b))
                                try:
                                    self.fast_write_flat(flat)
                                except Exception:
                                    try:
                                        self.set_pixels(flat)
                                    except Exception:
                                        pass
                        except Exception:
                            log.exception('Error applying animation frame')
                        # wait honoring speed and stop flag
                        delay = max(0.01, durations[idx] / max(0.001, float(speed)))
                        waited = 0.0
                        while waited < delay and not self._anim_stop.is_set():
                            # also respect pause during frame delay
                            if self._anim_paused.is_set():
                                time.sleep(min(0.05, delay - waited))
                            else:
                                time.sleep(min(0.05, delay - waited))
                            waited += min(0.05, delay - waited)
                    if not loop:
                        break
            except Exception:
                log.exception('GIF animation playback failed')
            finally:
                # mark finished
                try:
                    with self._anim_lock:
                        self._anim_thread = None
                        self._anim_stop.clear()
                except Exception:
                    pass

        t = threading.Thread(target=_runner, daemon=True)
        with self._anim_lock:
            self._anim_thread = t
            self._anim_stop.clear()
            self._anim_paused.clear()
            t.start()

    def play_wled_json(self, path: str, speed: float = 1.0, loop: bool = True):
        # Generic WLED-style JSON playback. Supports simple lists of frames
        # where each frame is either a flat list of hex colors or a nested
        # list-of-rows. Runs in a background thread similar to GIF playback.
        try:
            self.stop_animation()
        except Exception:
            pass
        def _runner():
            try:
                text = Path(path).read_text()
                # try to parse one or more JSON objects
                objs = []
                try:
                    objs = json.loads(text)
                except Exception:
                    # attempt to parse multiple concatenated JSON objects
                    decoder = json.JSONDecoder()
                    idx = 0
                    text_len = len(text)
                    while True:
                        while idx < text_len and text[idx].isspace():
                            idx += 1
                        if idx >= text_len:
                            break
                        try:
                            obj, end = decoder.raw_decode(text, idx)
                            objs.append(obj)
                            idx = end
                        except ValueError:
                            break
                frames = []
                # normalize various shapes
                if isinstance(objs, dict):
                    # some WLED exports wrap frames in a key
                    if 'frames' in objs and isinstance(objs['frames'], list):
                        frames = objs['frames']
                    else:
                        # try to treat dict as single frame
                        frames = [objs]
                elif isinstance(objs, list):
                    frames = objs
                else:
                    frames = [objs]

                if not frames:
                    return
                self._anim_stop.clear()
                while not self._anim_stop.is_set():
                    for fr in frames:
                        # honor pause flag
                        while self._anim_paused.is_set() and not self._anim_stop.is_set():
                            time.sleep(0.05)
                        if self._anim_stop.is_set():
                            break
                        # each frame may be a list of colors or a dict containing 'pixels' and optional 'duration'
                        duration = 0.1
                        payload = fr
                        if isinstance(fr, dict):
                            duration = float(fr.get('duration', duration))
                            payload = fr.get('pixels', fr.get('frame', fr))
                        # apply pixels
                        try:
                            if isinstance(payload, list):
                                # payload may be nested or flat
                                # prefer fast write when available
                                try:
                                    self.fast_write_flat(payload)
                                except Exception:
                                    self.set_pixels(payload)
                            else:
                                log.warning('Unsupported frame payload type for WLED JSON: %s', type(payload))
                        except Exception:
                            log.exception('Failed to apply WLED frame')
                        # wait
                        delay = max(0.01, float(duration) / max(0.001, float(speed)))
                        waited = 0.0
                        while waited < delay and not self._anim_stop.is_set():
                            if self._anim_paused.is_set():
                                time.sleep(min(0.05, delay - waited))
                            else:
                                time.sleep(min(0.05, delay - waited))
                            waited += min(0.05, delay - waited)
                    if not loop:
                        break
            except Exception:
                log.exception('WLED JSON animation playback failed')
            finally:
                try:
                    with self._anim_lock:
                        self._anim_thread = None
                        self._anim_stop.clear()
                except Exception:
                    pass

        t = threading.Thread(target=_runner, daemon=True)
        with self._anim_lock:
            self._anim_thread = t
            self._anim_stop.clear()
            self._anim_paused.clear()
            t.start()

    def stop_animation(self):
        try:
            with self._anim_lock:
                if self._anim_thread and self._anim_thread.is_alive():
                    self._anim_stop.set()
                    self._anim_thread.join(timeout=2.0)
                    self._anim_thread = None
                    self._anim_stop.clear()
                    # ensure paused flag is cleared when stopping
                    try:
                        self._anim_paused.clear()
                    except Exception:
                        pass
        except Exception:
            log.exception('Failed to stop animation')

    def pause_animation(self):
        """Pause a running animation. The animation thread remains alive but will
        block until resumed. If no animation is running this is a no-op."""
        try:
            with self._anim_lock:
                if self._anim_thread and self._anim_thread.is_alive():
                    self._anim_paused.set()
        except Exception:
            log.exception('Failed to pause animation')

    def resume_animation(self):
        """Resume a paused animation. If no animation is paused this is a no-op."""
        try:
            with self._anim_lock:
                if self._anim_thread and self._anim_thread.is_alive():
                    self._anim_paused.clear()
        except Exception:
            log.exception('Failed to resume animation')

    def is_animating(self) -> bool:
        try:
            with self._anim_lock:
                return bool(self._anim_thread and self._anim_thread.is_alive())
        except Exception:
            return False

    def show_volume_bar(self, volume: int, duration_ms: int = 1500, color: str = '#00FF00', mode: str = 'overlay'):
        return

    @staticmethod
    def _coerce_color(v) -> str:
        try:
            if v is None:
                return '#000000'
            s = str(v).strip()
            if s.startswith('#') and (len(s) == 7 or len(s) == 4):
                if len(s) == 4:
                    r = s[1]*2; g = s[2]*2; b = s[3]*2
                    return f'#{r}{g}{b}'.upper()
                return s.upper()
            if ',' in s:
                parts = [int(p.strip()) for p in s.split(',')]
                return '#%02X%02X%02X' % (parts[0], parts[1], parts[2])
            return '#000000'
        except Exception:
            return '#000000'


class WS2812Plugin(BasePlugin):
    """Adapter that wraps the existing legacy LEDMatrix implementation so
    the rest of the project can use a plugin model without changing behavior.
    """

    def __init__(self, width: int, height: int, cfg: Optional[Dict[str, Any]] = None):
        super().__init__(width, height, cfg)
        # lazy import of legacy LEDMatrix
        try:
            from .ledmatrix import LEDMatrix as LegacyLEDMatrix
            self._impl = LegacyLEDMatrix(self.width, self.height)
        except Exception:
            self._impl = None

    def set_on_update(self, cb):
        super().set_on_update(cb)
        if self._impl is not None:
            try:
                self._impl._on_update = cb
            except Exception:
                pass

    def show_image(self, img: 'Image.Image'):
        if not _HAVE_PIL:
            return
        if self._impl is None:
            return
        # resize into plugin native size
        try:
            im = img.convert('RGB').resize((self.width, self.height), Image.NEAREST)
            flat = []
            for y in range(self.height):
                for x in range(self.width):
                    r, g, b = im.getpixel((x, y))
                    flat.append('#%02X%02X%02X' % (r, g, b))
            # delegate to legacy implementation
            self._impl.set_pixels(flat)
        except Exception:
            log.exception('WS2812Plugin failed to show_image')

    def fast_write_flat(self, flat_pixels: Optional[List[str]]):
        # If legacy impl has hardware handle, use fast helper; otherwise fallback
        try:
            if getattr(self, '_impl', None) is not None and getattr(self._impl, '_hw', None) is not None:
                try:
                    from .ledmatrix import write_hw_buffer
                    write_hw_buffer(getattr(self._impl, '_hw'), flat_pixels, self.width, self.height, getattr(self._impl, '_serpentine', False))
                    return
                except Exception:
                    pass
        except Exception:
            pass
        return super().fast_write_flat(flat_pixels)

    def set_pixels(self, pixels: Optional[List[str]] = None, bypass_overlay: bool = False):
        if self._impl is None:
            return
        self._impl.set_pixels(pixels, bypass_overlay=bypass_overlay)

    def get_pixels(self):
        if self._impl is None:
            return super().get_pixels()
        return self._impl.get_pixels()

    def set_brightness(self, percent: int):
        if self._impl is None:
            return
        try:
            self._impl.set_brightness(percent)
        except Exception:
            log.exception('set_brightness failed for WS2812Plugin')

    def get_brightness(self) -> int:
        if self._impl is None:
            return 0
        try:
            return int(self._impl.get_brightness())
        except Exception:
            return 0

    def play_animation_from_gif(self, path: str, speed: float = 1.0, loop: bool = True):
        # prefer legacy implementation when available, otherwise use BasePlugin
        if self._impl is not None and hasattr(self._impl, 'play_animation_from_gif'):
            try:
                return self._impl.play_animation_from_gif(path, speed=speed, loop=loop)
            except Exception:
                log.exception('legacy play_animation_from_gif failed; falling back')
        return super().play_animation_from_gif(path, speed=speed, loop=loop)

    def play_wled_json(self, path: str, speed: float = 1.0, loop: bool = True):
        if self._impl is not None and hasattr(self._impl, 'play_wled_json'):
            try:
                return self._impl.play_wled_json(path, speed=speed, loop=loop)
            except Exception:
                log.exception('legacy play_wled_json failed; falling back')
        return super().play_wled_json(path, speed=speed, loop=loop)

    def stop_animation(self):
        if self._impl is not None and hasattr(self._impl, 'stop_animation'):
            try:
                return self._impl.stop_animation()
            except Exception:
                log.exception('legacy stop_animation failed; falling back')
        return super().stop_animation()

    def is_animating(self) -> bool:
        if self._impl is not None and hasattr(self._impl, 'is_animating'):
            try:
                return bool(self._impl.is_animating())
            except Exception:
                log.exception('legacy is_animating failed; falling back')
        return super().is_animating()

    def show_volume_bar(self, volume: int, duration_ms: int = 1500, color: str = '#00FF00', mode: str = 'overlay'):
        if self._impl is None:
            return
        return self._impl.show_volume_bar(volume, duration_ms=duration_ms, color=color, mode=mode)


class RGBMatrixPlugin(BasePlugin):
    """Plugin that attempts to use the rpi-rgb-led-matrix Python bindings.

    If the bindings are unavailable this plugin gracefully falls back to
    an in-memory buffer so the web UI can still mirror the image.
    """

    def __init__(self, width: int, height: int, cfg: Optional[Dict[str, Any]] = None):
        super().__init__(width, height, cfg)
        self._hardware = None
        # use a PIL image buffer when possible; fall back to list of hex strings
        if _HAVE_PIL:
            try:
                self._buffer_img = Image.new('RGB', (self.width, self.height))
            except Exception:
                self._buffer_img = None
        else:
            self._buffer_img = None
        self._buffer = ['#000000'] * (self.width * self.height) if self._buffer_img is None else None
        # try to initialize rgbmatrix
        try:
            from rgbmatrix import RGBMatrix, RGBMatrixOptions
            opts = RGBMatrixOptions()
            # common options that may be provided in cfg
            if cfg:
                for k, v in cfg.items():
                    try:
                        setattr(opts, k, v)
                    except Exception:
                        pass
            # ensure rows/cols match requested size when present
            try:
                opts.rows = int(cfg.get('rows', self.height)) if cfg else self.height
                opts.cols = int(cfg.get('cols', self.width)) if cfg else self.width
            except Exception:
                pass
            self._hardware = RGBMatrix(options=opts)
        except Exception:
            log.info('rpi-rgb-led-matrix bindings not available; RGBMatrixPlugin will use in-memory buffer')
            self._hardware = None

    def show_image(self, img: 'Image.Image'):
        if not _HAVE_PIL:
            return
        try:
            im = img.convert('RGB').resize((self.width, self.height), Image.NEAREST)
            # If hardware available, try to use the binding's SetImage or equivalent
            if self._hardware is not None:
                try:
                    # Many examples accept a PIL image directly via SetImage
                    # Use best-effort call; if it fails we fall back to buffer update
                    if hasattr(self._hardware, 'SetImage'):
                        self._hardware.SetImage(im)
                        return
                    # try other common method names
                    if hasattr(self._hardware, 'SetFramebuffer'):
                        self._hardware.SetFramebuffer(im)
                        return
                except Exception:
                    log.exception('RGBMatrix hardware image set failed; falling back to buffer')
            # Fallback: store PIL image in buffer and call on_update so UI can mirror
            try:
                # prefer storing the PIL image buffer
                self._buffer_img = im
                # also update flat buffer for older consumers
                flat = []
                for y in range(self.height):
                    for x in range(self.width):
                        r, g, b = im.getpixel((x, y))
                        flat.append('#%02X%02X%02X' % (r, g, b))
                self._buffer = flat
                if self._on_update:
                    try:
                        self._on_update(list(self._buffer))
                    except Exception:
                        log.exception('RGBMatrixPlugin on_update callback failed')
            except Exception:
                log.exception('RGBMatrixPlugin buffering failed')
        except Exception:
            log.exception('RGBMatrixPlugin show_image failed')

    def fast_write_flat(self, flat_pixels: Optional[List[str]]):
        """Fast write for rgbmatrix: build a PIL image and push via hardware if available."""
        if flat_pixels is None:
            return
        # normalize length
        expected = self.width * self.height
        fp = list(flat_pixels)
        if len(fp) < expected:
            fp.extend(['#000000'] * (expected - len(fp)))
        if len(fp) > expected:
            fp = fp[:expected]
        # write into PIL image
        if _HAVE_PIL:
            try:
                img = Image.new('RGB', (self.width, self.height))
                for y in range(self.height):
                    for x in range(self.width):
                        hexc = fp[y * self.width + x].lstrip('#')
                        try:
                            r = int(hexc[0:2], 16); g = int(hexc[2:4], 16); b = int(hexc[4:6], 16)
                        except Exception:
                            r, g, b = 0, 0, 0
                        img.putpixel((x, y), (r, g, b))
                # attempt hardware push
                if self._hardware is not None:
                    try:
                        if hasattr(self._hardware, 'SetImage'):
                            self._hardware.SetImage(img); return
                        if hasattr(self._hardware, 'SetFramebuffer'):
                            self._hardware.SetFramebuffer(img); return
                    except Exception:
                        log.exception('RGBMatrix hardware fast write failed; falling back')
                # update internal buffer and notify
                self._buffer_img = img
                self._buffer = fp
                if self._on_update:
                    try:
                        self._on_update(list(self._buffer))
                    except Exception:
                        log.exception('RGBMatrixPlugin on_update callback failed')
                return
            except Exception:
                log.exception('RGBMatrixPlugin fast_write_flat failed')
        # fallback
        return super().fast_write_flat(fp)

    def set_pixels(self, pixels: Optional[List[str]] = None, bypass_overlay: bool = False):
        # If a volume overlay is active in 'pause' mode, block writes unless bypass_overlay is True
        try:
            if getattr(self, '_vol_overlay_active', False) and not bypass_overlay and getattr(self, '_vol_overlay_mode', 'overlay') == 'pause':
                return
        except Exception:
            pass
        if pixels is None:
            return
        # normalize to flat list of hex colors
        flat = []
        if isinstance(pixels, list) and pixels and isinstance(pixels[0], list):
            for row in pixels:
                for v in row:
                    flat.append(self._coerce_color(v))
        else:
            for v in pixels:
                flat.append(self._coerce_color(v))
        expected = self.width * self.height
        if len(flat) < expected:
            flat.extend(['#000000'] * (expected - len(flat)))
        if len(flat) > expected:
            flat = flat[:expected]

        # write into PIL buffer if available
        if self._buffer_img is not None:
            try:
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

                for y in range(self.height):
                    for x in range(self.width):
                        hexc = flat[y * self.width + x].lstrip('#')
                        try:
                            r = int(hexc[0:2], 16); g = int(hexc[2:4], 16); b = int(hexc[4:6], 16)
                        except Exception:
                            r, g, b = 0, 0, 0
                        self._buffer_img.putpixel((x, y), (r, g, b))
                # update fallback flat buffer too
                self._buffer = flat
                # if hardware available, try to push the PIL image
                if self._hardware is not None:
                    try:
                        if hasattr(self._hardware, 'SetImage'):
                            self._hardware.SetImage(self._buffer_img)
                        elif hasattr(self._hardware, 'SetFramebuffer'):
                            self._hardware.SetFramebuffer(self._buffer_img)
                    except Exception:
                        log.exception('RGBMatrix hardware image set failed during set_pixels')
                # notify UI
                if self._on_update:
                    try:
                        self._on_update(list(self._buffer))
                    except Exception:
                        log.exception('RGBMatrixPlugin on_update callback failed')
                return
            except Exception:
                log.exception('RGBMatrixPlugin failed to write to image buffer')

        # fallback: keep flat buffer and notify
        self._buffer = flat
        if self._on_update:
            try:
                self._on_update(list(self._buffer))
            except Exception:
                log.exception('RGBMatrixPlugin on_update callback failed')

    def get_pixels(self):
        # prefer reading from PIL image buffer when present
        if self._buffer_img is not None:
            try:
                flat = []
                for y in range(self.height):
                    for x in range(self.width):
                        r, g, b = self._buffer_img.getpixel((x, y))
                        flat.append('#%02X%02X%02X' % (r, g, b))
                return flat
            except Exception:
                log.exception('Failed to read pixels from image buffer')
        # fallback
        return list(self._buffer) if self._buffer is not None else ['#000000'] * (self.width * self.height)

    def set_brightness(self, percent: int):
        if self._hardware is not None:
            try:
                self._hardware.brightness = max(0, min(100, int(percent)))
            except Exception:
                log.exception('RGBMatrixPlugin set_brightness failed')

    def get_brightness(self) -> int:
        if self._hardware is not None:
            try:
                return self._hardware.brightness
            except Exception:
                log.exception('RGBMatrixPlugin get_brightness failed')
        return 100

    def show_volume_bar(self, volume: int, duration_ms: int = 1500, color: str = '#00FF00', mode: str = 'overlay'):
        """Display a temporary volume bar on the bottom row similar to legacy LEDMatrix.

        This implementation snapshots the current buffer, writes an overlay for
        the requested duration, and then restores the snapshot (unless cancelled).
        """
        try:
            v = int(volume)
        except Exception:
            return
        v = max(0, min(100, v))

        # cancel previous overlay if present
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
            if len(c) == 4:
                c = '#' + c[1]*2 + c[2]*2 + c[3]*2
            c = c.upper()
        except Exception:
            c = '#00FF00'

        m = str(mode or 'overlay').lower()
        if m not in ('overlay', 'pause'):
            m = 'overlay'

        stop_ev = threading.Event()
        self._vol_overlay_stop = stop_ev
        try:
            self._vol_overlay_active = True
            self._vol_overlay_mode = m
        except Exception:
            pass

        def _runner():
            try:
                # snapshot current buffer
                snap = self.get_pixels()
                w = self.width
                h = self.height
                filled = int(round((v / 100.0) * w))
                over = list(snap)
                row_start = (h - 1) * w
                try:
                    hexc = c.lstrip('#')
                    fr = int(hexc[0:2], 16); fg = int(hexc[2:4], 16); fb = int(hexc[4:6], 16)
                except Exception:
                    fr, fg, fb = 0, 255, 0
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
                    # store overlay pixels for merging by set_pixels
                    try:
                        self._vol_overlay_pixels = list(over)
                    except Exception:
                        self._vol_overlay_pixels = None
                    # write overlay bypassing overlay check so it always appears
                    try:
                        self.set_pixels(over, bypass_overlay=True)
                    except Exception:
                        pass
                except Exception:
                    pass

                # wait for duration or stop
                waited = 0.0
                interval = 0.05
                total = max(0, int(duration_ms) / 1000.0)
                while not stop_ev.is_set() and waited < total:
                    time.sleep(interval)
                    waited += interval

                # restore snapshot if not cancelled
                if not stop_ev.is_set():
                    try:
                        try:
                            self.set_pixels(snap, bypass_overlay=True)
                        except Exception:
                            pass
                    except Exception:
                        pass
            finally:
                try:
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


class DisplayManager:
    """Manager that holds the active plugin and delegates the common APIs."""

    def __init__(self, width: int, height: int, storage=None):
        self.width = int(width)
        self.height = int(height)
        self.storage = storage
        self._plugin = None
        # load config from storage if available
        cfg = {}
        try:
            if storage is not None:
                cfg = storage.load() or {}
        except Exception:
            cfg = {}
        disp = cfg.get('display', {}) if isinstance(cfg, dict) else {}
        active = disp.get('active', 'ws2812')
        plugins_cfg = disp.get('plugins', {}) if isinstance(disp, dict) else {}
        self.set_active_plugin(active, plugins_cfg.get(active, {}))

    def set_on_update(self, cb):
        if self._plugin is not None:
            try:
                self._plugin.set_on_update(cb)
            except Exception:
                pass

    def set_active_plugin(self, name: str, cfg: Optional[Dict[str, Any]] = None):
        name = (name or 'ws2812')
        cfg = cfg or {}
        # Determine plugin-native size from cfg when available so the
        # Display page and API reflect the actual matrix dimensions.
        try:
            if name == 'ws2812':
                pw = int(cfg.get('width')) if cfg.get('width') is not None else self.width
                ph = int(cfg.get('height')) if cfg.get('height') is not None else self.height
                plugin = WS2812Plugin(pw, ph, cfg)
            elif name == 'rgbmatrix':
                # rgbmatrix commonly uses 'rows' and 'cols'
                pw = int(cfg.get('cols')) if cfg.get('cols') is not None else self.width
                ph = int(cfg.get('rows')) if cfg.get('rows') is not None else self.height
                plugin = RGBMatrixPlugin(pw, ph, cfg)
            else:
                pw = int(cfg.get('width')) if cfg.get('width') is not None else self.width
                ph = int(cfg.get('height')) if cfg.get('height') is not None else self.height
                plugin = WS2812Plugin(pw, ph, cfg)
        except Exception:
            # fallback to manager dimensions on any error parsing cfg
            plugin = WS2812Plugin(self.width, self.height, cfg)
        self._plugin = plugin

    # delegate APIs
    def show_image(self, img: 'Image.Image'):
        if self._plugin:
            return self._plugin.show_image(img)

    def set_pixels(self, pixels: Optional[List[str]] = None, bypass_overlay: bool = False):
        if self._plugin:
            return self._plugin.set_pixels(pixels, bypass_overlay=bypass_overlay)

    def get_pixels(self):
        if self._plugin:
            return self._plugin.get_pixels()
        return ['#000000'] * (self.width * self.height)

    def set_brightness(self, p: int):
        if self._plugin:
            try:
                return self._plugin.set_brightness(p)
            except Exception:
                pass

    def get_brightness(self):
        if self._plugin:
            try:
                return self._plugin.get_brightness()
            except Exception:
                return 100
        return 100

    def play_animation_from_gif(self, path: str, speed: float = 1.0, loop: bool = True):
        if self._plugin:
            try:
                return self._plugin.play_animation_from_gif(path, speed=speed, loop=loop)
            except Exception:
                log.exception('play_animation_from_gif failed on plugin')

    def play_wled_json(self, path: str, speed: float = 1.0, loop: bool = True):
        if self._plugin:
            try:
                return self._plugin.play_wled_json(path, speed=speed, loop=loop)
            except Exception:
                log.exception('play_wled_json failed on plugin')

    def stop_animation(self):
        if self._plugin:
            try:
                return self._plugin.stop_animation()
            except Exception:
                log.exception('stop_animation failed on plugin')

    def pause_animation(self):
        if self._plugin:
            try:
                if hasattr(self._plugin, 'pause_animation'):
                    return self._plugin.pause_animation()
            except Exception:
                log.exception('pause_animation failed on plugin')

    def resume_animation(self):
        if self._plugin:
            try:
                if hasattr(self._plugin, 'resume_animation'):
                    return self._plugin.resume_animation()
            except Exception:
                log.exception('resume_animation failed on plugin')

    def is_animating(self) -> bool:
        if self._plugin:
            try:
                return self._plugin.is_animating()
            except Exception:
                return False
        return False

    def show_volume_bar(self, volume: int, duration_ms: int = 1500, color: str = '#00FF00', mode: str = 'overlay'):
        if self._plugin:
            try:
                return self._plugin.show_volume_bar(volume, duration_ms=duration_ms, color=color, mode=mode)
            except Exception:
                pass

    # expose plugin for advanced uses/tests
    @property
    def plugin(self):
        return self._plugin


def create_matrix(width: int = 16, height: int = 16, storage=None):
    return DisplayManager(width, height, storage=storage)
