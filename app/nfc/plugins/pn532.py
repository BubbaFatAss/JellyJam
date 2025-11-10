"""
PN532 NFC reader plugin using GPIO connection (I2C or SPI).

This plugin supports PN532 NFC modules connected to Raspberry Pi via:
- I2C interface (default)
- SPI interface

Requires py532lib library: pip install py532lib
"""

import threading
import time
import logging
from .base import NFCReaderPlugin

log = logging.getLogger(__name__)


class PN532Plugin(NFCReaderPlugin):
    """
    PN532 NFC reader plugin for GPIO-connected modules.
    
    Supports both I2C and SPI communication modes.
    """
    
    def __init__(self, callback=None, config=None):
        super().__init__(callback, config)
        self.pn532 = None
        self._poll_interval = self.config.get('poll_interval', 0.5)
    
    def start(self):
        """Initialize PN532 hardware and start polling for cards."""
        if self._running:
            log.warning('PN532 reader already running')
            return
        
        try:
            # Import PN532 library
            interface_type = self.config.get('interface', 'i2c')
            
            if interface_type == 'i2c':
                self._init_i2c()
            elif interface_type == 'spi':
                self._init_spi()
            else:
                raise ValueError(f'Unsupported interface type: {interface_type}')
            
            # Configure PN532
            if self.pn532:
                # Get firmware version to verify communication
                ic, ver, rev, support = self.pn532.get_firmware_version()
                log.info('PN532 firmware version: IC=0x%02X, Ver=%d.%d, Support=0x%02X', 
                        ic, ver, rev, support)
                
                # Configure PN532 to read MIFARE cards
                self.pn532.SAM_configuration()
                
                # Start polling thread
                self._running = True
                self._thread = threading.Thread(target=self._poll_loop, daemon=True)
                self._thread.start()
                log.info('PN532 NFC reader started (%s mode)', interface_type)
            else:
                raise RuntimeError('Failed to initialize PN532')
                
        except ImportError as e:
            log.error('Failed to import PN532 library. Install with: pip install py532lib')
            log.error('Error: %s', e)
            raise
        except Exception as e:
            log.error('Failed to start PN532 reader: %s', e, exc_info=True)
            raise
    
    def _init_i2c(self):
        """Initialize PN532 in I2C mode."""
        try:
            from py532lib.i2c import Pn532_i2c
            from py532lib.constants import *
            
            # Get I2C configuration
            i2c_bus = self.config.get('i2c_bus', 1)
            i2c_address = self.config.get('i2c_address', 0x24)
            reset_pin = self.config.get('reset_pin')
            
            log.debug('Initializing PN532 on I2C bus %d, address 0x%02X', i2c_bus, i2c_address)
            
            # Create PN532 instance
            self.pn532 = Pn532_i2c(bus=i2c_bus, address=i2c_address)
            
            # Optionally configure reset pin
            if reset_pin is not None:
                try:
                    import RPi.GPIO as GPIO
                    GPIO.setmode(GPIO.BCM)
                    GPIO.setup(int(reset_pin), GPIO.OUT)
                    # Perform hardware reset
                    GPIO.output(int(reset_pin), GPIO.LOW)
                    time.sleep(0.1)
                    GPIO.output(int(reset_pin), GPIO.HIGH)
                    time.sleep(0.5)
                    log.debug('PN532 hardware reset via GPIO %d', reset_pin)
                except Exception as e:
                    log.warning('Failed to configure reset pin: %s', e)
            
        except ImportError:
            log.error('py532lib not installed. Install with: pip install py532lib')
            raise
    
    def _init_spi(self):
        """Initialize PN532 in SPI mode."""
        try:
            from py532lib.spi import Pn532_spi
            from py532lib.constants import *
            
            # Get SPI configuration
            spi_bus = self.config.get('spi_bus', 0)
            spi_device = self.config.get('spi_device', 0)
            reset_pin = self.config.get('reset_pin')
            
            log.debug('Initializing PN532 on SPI bus %d, device %d', spi_bus, spi_device)
            
            # Create PN532 instance
            self.pn532 = Pn532_spi(bus=spi_bus, device=spi_device)
            
            # Optionally configure reset pin
            if reset_pin is not None:
                try:
                    import RPi.GPIO as GPIO
                    GPIO.setmode(GPIO.BCM)
                    GPIO.setup(int(reset_pin), GPIO.OUT)
                    # Perform hardware reset
                    GPIO.output(int(reset_pin), GPIO.LOW)
                    time.sleep(0.1)
                    GPIO.output(int(reset_pin), GPIO.HIGH)
                    time.sleep(0.5)
                    log.debug('PN532 hardware reset via GPIO %d', reset_pin)
                except Exception as e:
                    log.warning('Failed to configure reset pin: %s', e)
            
        except ImportError:
            log.error('py532lib not installed. Install with: pip install py532lib')
            raise
    
    def _poll_loop(self):
        """Background thread that polls for NFC cards."""
        last_uid = None
        last_scan_time = 0
        debounce_time = self.config.get('debounce_time', 1.0)
        
        log.debug('PN532 poll loop started (interval=%.2fs, debounce=%.2fs)', 
                 self._poll_interval, debounce_time)
        
        while self._running:
            try:
                # Poll for a card (timeout in ms)
                timeout_ms = int(self._poll_interval * 1000)
                uid = self.pn532.read_passive_target(timeout=timeout_ms)
                
                if uid:
                    # Convert UID to hex string
                    card_id = ''.join(['%02X' % b for b in uid])
                    current_time = time.time()
                    
                    # Debounce: ignore same card if scanned too recently
                    if card_id != last_uid or (current_time - last_scan_time) > debounce_time:
                        self._notify_card(card_id)
                        last_uid = card_id
                        last_scan_time = current_time
                else:
                    # No card detected, reset debounce
                    if last_uid is not None:
                        last_uid = None
                
            except Exception as e:
                log.error('Error reading NFC card: %s', e)
                time.sleep(1)  # Back off on error
        
        log.debug('PN532 poll loop stopped')
    
    def stop(self):
        """Stop the PN532 reader and clean up."""
        if not self._running:
            return
        
        log.info('Stopping PN532 NFC reader')
        self._running = False
        
        # Wait for thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        
        # Clean up PN532
        self.pn532 = None
        
        log.info('PN532 NFC reader stopped')
    
    @classmethod
    def get_plugin_name(cls):
        return "PN532 (I2C/SPI)"
    
    @classmethod
    def get_config_schema(cls):
        return {
            'interface': {
                'type': 'select',
                'label': 'Interface Type',
                'default': 'i2c',
                'options': [
                    {'value': 'i2c', 'label': 'I2C'},
                    {'value': 'spi', 'label': 'SPI'}
                ],
                'required': True,
                'description': 'Communication interface (I2C or SPI)'
            },
            'i2c_bus': {
                'type': 'number',
                'label': 'I2C Bus',
                'default': 1,
                'required': False,
                'description': 'I2C bus number (usually 1 on Raspberry Pi)',
                'show_if': {'interface': 'i2c'}
            },
            'i2c_address': {
                'type': 'number',
                'label': 'I2C Address (Hex)',
                'default': 36,  # 0x24
                'required': False,
                'description': 'I2C address (0x24 = 36 decimal, common default)',
                'show_if': {'interface': 'i2c'}
            },
            'spi_bus': {
                'type': 'number',
                'label': 'SPI Bus',
                'default': 0,
                'required': False,
                'description': 'SPI bus number (usually 0)',
                'show_if': {'interface': 'spi'}
            },
            'spi_device': {
                'type': 'number',
                'label': 'SPI Device',
                'default': 0,
                'required': False,
                'description': 'SPI device number (CE0 = 0, CE1 = 1)',
                'show_if': {'interface': 'spi'}
            },
            'reset_pin': {
                'type': 'number',
                'label': 'Reset Pin (Optional)',
                'default': None,
                'required': False,
                'description': 'GPIO pin for hardware reset (BCM numbering, leave empty to disable)'
            },
            'poll_interval': {
                'type': 'number',
                'label': 'Poll Interval (seconds)',
                'default': 0.5,
                'required': False,
                'description': 'How often to check for cards (0.1 - 2.0 seconds)'
            },
            'debounce_time': {
                'type': 'number',
                'label': 'Debounce Time (seconds)',
                'default': 1.0,
                'required': False,
                'description': 'Minimum time between same card scans (0.5 - 5.0 seconds)'
            }
        }
