"""
Mock NFC reader plugin for testing and simulation.

This plugin doesn't connect to real hardware - it only responds
to simulated scans triggered via the web UI or API.
"""

import threading
import time
import logging
from .base import NFCReaderPlugin

log = logging.getLogger(__name__)


class MockNFCPlugin(NFCReaderPlugin):
    """
    Mock NFC reader that only responds to simulate_scan() calls.
    
    Useful for development and testing without hardware.
    """
    
    def start(self):
        """Start the mock reader (just sets running flag)."""
        self._running = True
        log.info('Mock NFC reader started (simulation only)')
    
    def stop(self):
        """Stop the mock reader."""
        self._running = False
        log.info('Mock NFC reader stopped')
    
    @classmethod
    def get_plugin_name(cls):
        return "Mock (Simulation Only)"
    
    @classmethod
    def get_config_schema(cls):
        return {}  # No configuration needed
