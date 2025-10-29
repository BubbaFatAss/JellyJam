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

    def set_on_update(self, cb):
        self._on_update = cb

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
        raise NotImplementedError()

    def play_wled_json(self, path: str, speed: float = 1.0, loop: bool = True):
        raise NotImplementedError()

    def stop_animation(self):
        return

    def is_animating(self) -> bool:
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
        if self._impl is None:
            return
        return self._impl.play_animation_from_gif(path, speed=speed, loop=loop)

    def play_wled_json(self, path: str, speed: float = 1.0, loop: bool = True):
        if self._impl is None:
            return
        return self._impl.play_wled_json(path, speed=speed, loop=loop)

    def stop_animation(self):
        if self._impl is None:
            return
        return self._impl.stop_animation()

    def is_animating(self) -> bool:
        if self._impl is None:
            return False
        return self._impl.is_animating()

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
        self._buffer = ['#000000'] * (self.width * self.height)
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
            # Fallback: update internal buffer and call on_update so UI can mirror
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
            log.exception('RGBMatrixPlugin show_image failed')

    def set_pixels(self, pixels: Optional[List[str]] = None, bypass_overlay: bool = False):
        if pixels is None:
            return
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
        self._buffer = flat
        if self._on_update:
            try:
                self._on_update(list(self._buffer))
            except Exception:
                log.exception('RGBMatrixPlugin on_update callback failed')

    def get_pixels(self):
        return list(self._buffer)

    def stop_animation(self):
        # animations not supported in this simple plugin; no-op
        return

    def is_animating(self) -> bool:
        return False


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
                return 0
        return 0

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
