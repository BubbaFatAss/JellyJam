"""
WS2812B Nightlight Controller
Controls an LED strip for ambient lighting (separate from the display matrix)
"""
import os
import threading
import time
from utils.logging_config import get_logger

log = get_logger(__name__)


class NightLight:
    def __init__(self, num_leds=None, gpio_pin=None):
        """
        Initialize the nightlight controller.
        
        Args:
            num_leds: Number of LEDs in the strip (defaults to LED_NIGHTLIGHT_COUNT env var or 30)
            gpio_pin: GPIO pin number (BCM) for data line (defaults to LED_NIGHTLIGHT_PIN env var or 18)
        """
        self.num_leds = num_leds or int(os.environ.get('LED_NIGHTLIGHT_COUNT', '30'))
        self.gpio_pin = gpio_pin or int(os.environ.get('LED_NIGHTLIGHT_PIN', '18'))
        
        self._state = {
            'on': False,
            'color': '#ff6b35',  # Default warm orange
            'brightness': 128    # 0-255
        }
        
        self._strip = None
        self._lock = threading.RLock()  # Use RLock to allow recursive locking
        self._state_callbacks = []
        
        # Try to initialize the LED strip
        try:
            self._init_strip()
            log.info('Nightlight initialized: %d LEDs on GPIO %d', self.num_leds, self.gpio_pin)
        except Exception as e:
            log.warning('Could not initialize nightlight hardware: %s', e)
            log.info('Nightlight will run in simulation mode')
    
    def _init_strip(self):
        """Initialize the WS2812B LED strip using rpi_ws281x library."""
        try:
            from rpi_ws281x import PixelStrip, Color
            
            # LED strip configuration
            LED_FREQ_HZ = 800000  # LED signal frequency in hertz
            LED_DMA = 10          # DMA channel
            LED_INVERT = False    # True to invert the signal
            LED_CHANNEL = 0       # PWM channel (0 or 1)
            
            self._strip = PixelStrip(
                self.num_leds,
                self.gpio_pin,
                LED_FREQ_HZ,
                LED_DMA,
                LED_INVERT,
                self._state['brightness'],
                LED_CHANNEL
            )
            
            self._strip.begin()
            self._Color = Color  # Store Color constructor for later use
            
            # Turn off all LEDs initially
            self._update_strip()
            
        except ImportError:
            # rpi_ws281x not available, run in simulation mode
            pass
        except Exception as e:
            log.error('Error initializing WS2812B strip: %s', e)
            raise
    
    def _hex_to_rgb(self, hex_color):
        """Convert hex color string to RGB tuple."""
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    
    def _update_strip(self):
        """Update the physical LED strip with current state."""
        if not self._strip:
            return  # Simulation mode
        
        with self._lock:
            if self._state['on']:
                r, g, b = self._hex_to_rgb(self._state['color'])
                brightness = self._state['brightness']
                
                # Apply brightness scaling
                r = int(r * brightness / 255)
                g = int(g * brightness / 255)
                b = int(b * brightness / 255)
                
                color = self._Color(r, g, b)
                
                # Set all LEDs to the same color
                for i in range(self.num_leds):
                    self._strip.setPixelColor(i, color)
            else:
                # Turn off all LEDs
                for i in range(self.num_leds):
                    self._strip.setPixelColor(i, 0)
            
            self._strip.show()
    
    def get_state(self):
        """Get current nightlight state."""
        with self._lock:
            return self._state.copy()
    
    def set_state(self, on=None, color=None, brightness=None):
        """
        Update nightlight state.
        
        Args:
            on: Boolean to turn on/off (None to leave unchanged)
            color: Hex color string like '#ff6b35' (None to leave unchanged)
            brightness: Integer 0-255 (None to leave unchanged)
        
        Returns:
            dict: Updated state
        """
        with self._lock:
            changed = False
            
            if on is not None and self._state['on'] != on:
                self._state['on'] = on
                changed = True
            
            if color is not None and self._state['color'] != color:
                # Validate hex color format
                if color.startswith('#') and len(color) == 7:
                    self._state['color'] = color.lower()
                    changed = True
            
            if brightness is not None:
                brightness = max(0, min(255, int(brightness)))
                if self._state['brightness'] != brightness:
                    self._state['brightness'] = brightness
                    changed = True
            
            if changed:
                self._update_strip()
                self._notify_callbacks()
            
            return self._state.copy()
    
    def turn_on(self):
        """Turn on the nightlight."""
        return self.set_state(on=True)
    
    def turn_off(self):
        """Turn off the nightlight."""
        return self.set_state(on=False)
    
    def set_color(self, hex_color):
        """Set the color (hex format like '#ff6b35')."""
        return self.set_state(color=hex_color)
    
    def set_brightness(self, brightness):
        """Set brightness (0-255)."""
        return self.set_state(brightness=brightness)
    
    def register_state_callback(self, callback):
        """
        Register a callback to be called when state changes.
        Callback will be called with the new state dict as argument.
        """
        self._state_callbacks.append(callback)
    
    def _notify_callbacks(self):
        """Notify all registered callbacks of state change."""
        state = self._state.copy()
        for callback in self._state_callbacks:
            try:
                callback(state)
            except Exception as e:
                log.error('Error in nightlight state callback: %s', e)
    
    def cleanup(self):
        """Clean up resources."""
        try:
            self.turn_off()
        except Exception:
            pass
