"""
Simple rotary encoder reader that works on Raspberry Pi using RPi.GPIO.

The module provides RotaryEncoderReader which polls the two GPIO pins and
decodes quadrature transitions. It calls a user-provided callback with an
integer delta (positive for clockwise, negative for counter-clockwise)
measured in detents. The reader accumulates raw transitions and only calls
the callback once a full detent has been observed (configurable steps_per_detent).

If RPi.GPIO is not available this module becomes a no-op stub so the app
can run on non-Pi systems.
"""
from __future__ import annotations

import threading
import time
import logging
from typing import Callable, Optional

log = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
    _HAVE_RPI = True
except Exception:
    GPIO = None
    _HAVE_RPI = False


class RotaryEncoderReader:
    def __init__(self, pin_a: int, pin_b: int, callback: Callable[[int], None], *, poll_interval: float = 0.01, reverse: bool = False, steps_per_detent: int = 4, button_pin: Optional[int] = None, button_callback: Optional[Callable[[], None]] = None, button_debounce: float = 0.15):
        """Create a new reader.

        - pin_a, pin_b: BCM pin numbers for the encoder A/B channels.
        - callback: function called with integer detent delta (e.g. +1 / -1).
        - poll_interval: seconds between polls (default 10ms).
        - reverse: if True, invert direction.
        - steps_per_detent: number of quadrature transitions that make one detent.
        """
        self.pin_a = int(pin_a)
        self.pin_b = int(pin_b)
        self.callback = callback
        self.poll_interval = float(poll_interval)
        self.reverse = bool(reverse)
        self.steps_per_detent = max(1, int(steps_per_detent))

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._accumulator = 0

        # optional button pin
        self.button_pin = int(button_pin) if button_pin is not None else None
        self.button_callback = button_callback
        self.button_debounce = float(button_debounce)
        self._last_button_state = 1
        self._last_button_time = 0.0

        # last state is a 2-bit number (A<<1 | B)
        self._last_state = 0

        if _HAVE_RPI:
            try:
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(self.pin_a, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                GPIO.setup(self.pin_b, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                # optional button
                if self.button_pin is not None:
                    GPIO.setup(self.button_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                    self._last_button_state = 1 if GPIO.input(self.button_pin) else 0
                    self._last_button_time = 0.0
                # initialize last state
                a = GPIO.input(self.pin_a)
                b = GPIO.input(self.pin_b)
                self._last_state = ((1 if a else 0) << 1) | (1 if b else 0)
            except Exception:
                log.exception('Failed to initialize GPIO for rotary encoder')
                raise
        else:
            log.info('RPi.GPIO not available — rotary encoder will be a no-op')

    def start(self):
        if not _HAVE_RPI:
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        if not _HAVE_RPI:
            return
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        try:
            to_clean = [self.pin_a, self.pin_b]
            if self.button_pin is not None:
                to_clean.append(self.button_pin)
            GPIO.cleanup(tuple(to_clean))
        except Exception:
            pass

    def _poll_loop(self):
        # Quadrature decoding: convert transitions into +1/-1 steps
        while self._running:
            try:
                a = GPIO.input(self.pin_a)
                b = GPIO.input(self.pin_b)
                state = ((1 if a else 0) << 1) | (1 if b else 0)
                if state != self._last_state:
                    delta = self._decode_transition(self._last_state, state)
                    self._last_state = state
                    if delta != 0:
                        if self.reverse:
                            delta = -delta
                        self._accumulator += delta
                        # when we've seen a full detent (N transitions), emit callback
                        if abs(self._accumulator) >= self.steps_per_detent:
                            detents = int(self._accumulator / self.steps_per_detent)
                            self._accumulator -= detents * self.steps_per_detent
                            try:
                                self.callback(detents)
                            except Exception:
                                log.exception('Rotary callback raised')

                # poll button if present (active-low)
                if self.button_pin is not None:
                    try:
                        btn = GPIO.input(self.button_pin)
                        # btn == 0 when pressed (assuming pull-up)
                        if btn != self._last_button_state:
                            now = time.time()
                            # detect press transition (1 -> 0)
                            if self._last_button_state == 1 and btn == 0:
                                # debounce: ensure some time passed since last press
                                if now - self._last_button_time > self.button_debounce:
                                    self._last_button_time = now
                                    try:
                                        if self.button_callback:
                                            self.button_callback()
                                    except Exception:
                                        log.exception('Rotary button callback raised')
                            self._last_button_state = btn
                    except Exception:
                        log.exception('Error polling rotary button')

                time.sleep(self.poll_interval)
            except Exception:
                log.exception('Error in rotary poll loop')
                time.sleep(0.2)

    @staticmethod
    def _decode_transition(prev: int, new: int) -> int:
        """Return +1 for clockwise-ish, -1 for counter-ish, 0 for invalid/bounce."""
        # Using common Gray-code transitions
        if prev == 0b00:
            if new == 0b01:
                return 1
            if new == 0b10:
                return -1
        elif prev == 0b01:
            if new == 0b11:
                return 1
            if new == 0b00:
                return -1
        elif prev == 0b11:
            if new == 0b10:
                return 1
            if new == 0b01:
                return -1
        elif prev == 0b10:
            if new == 0b00:
                return 1
            if new == 0b11:
                return -1
        return 0


class DummyRotary:
    def __init__(self, *args, **kwargs):
        log.info('DummyRotary created (no GPIO support)')

    def start(self):
        pass

    def stop(self):
        pass


def create_rotary(pin_a: Optional[int], pin_b: Optional[int], callback: Callable[[int], None], **kwargs):
    """Factory that returns a working rotary reader or a dummy if GPIO unavailable or pins not provided."""
    if pin_a is None or pin_b is None:
        return DummyRotary()
    if not _HAVE_RPI:
        return DummyRotary()
    try:
        return RotaryEncoderReader(pin_a, pin_b, callback, **kwargs)
    except Exception:
        log.exception('Failed to create RotaryEncoderReader — falling back to dummy')
        return DummyRotary()
