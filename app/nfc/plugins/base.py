"""
Base class for NFC reader plugins.

All NFC reader implementations should inherit from NFCReaderPlugin
and implement the required methods.
"""

import threading
import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class NFCReaderPlugin(ABC):
    """
    Abstract base class for NFC reader plugins.
    
    Plugins must implement start(), stop(), and optionally simulate_scan().
    """
    
    def __init__(self, callback=None, config=None):
        """
        Initialize the NFC reader plugin.
        
        Args:
            callback: Function to call when a card is scanned. 
                     Receives card_id as argument.
            config: Plugin-specific configuration dictionary.
        """
        self.callback = callback
        self.config = config or {}
        self._running = False
        self._thread = None
        log.debug('Initializing %s with config: %s', self.__class__.__name__, config)
    
    @abstractmethod
    def start(self):
        """
        Start the NFC reader and begin listening for cards.
        
        Should spawn a background thread if needed and set self._running = True.
        """
        pass
    
    @abstractmethod
    def stop(self):
        """
        Stop the NFC reader and clean up resources.
        
        Should set self._running = False and join any background threads.
        """
        pass
    
    def simulate_scan(self, card_id):
        """
        Simulate a card scan (for testing/web UI).
        
        Args:
            card_id: The card ID to simulate.
        """
        if self.callback:
            log.info('Simulating NFC scan: %s', card_id)
            self.callback(card_id)
    
    def _notify_card(self, card_id):
        """
        Internal helper to notify callback of a card scan.
        
        Args:
            card_id: The scanned card ID.
        """
        if self.callback:
            try:
                log.info('NFC card detected: %s', card_id)
                self.callback(card_id)
            except Exception as e:
                log.error('Error in NFC callback: %s', e, exc_info=True)
    
    @classmethod
    def get_config_schema(cls):
        """
        Return a dictionary describing the configuration options for this plugin.
        
        Returns:
            dict: Schema with format:
                {
                    'field_name': {
                        'type': 'string|number|boolean',
                        'label': 'Human readable label',
                        'default': default_value,
                        'required': True/False,
                        'description': 'Help text'
                    }
                }
        """
        return {}
    
    @classmethod
    def get_plugin_name(cls):
        """Return human-readable plugin name."""
        return cls.__name__
    
    @classmethod
    def validate_config(cls, config):
        """
        Validate plugin configuration.
        
        Args:
            config: Configuration dictionary to validate.
            
        Returns:
            list: List of error messages (empty if valid).
        """
        errors = []
        schema = cls.get_config_schema()
        
        for field, field_schema in schema.items():
            value = config.get(field)
            
            # Check required fields
            if field_schema.get('required') and value is None:
                errors.append(f"{field_schema['label']} is required")
                continue
            
            # Type validation
            if value is not None:
                expected_type = field_schema.get('type')
                if expected_type == 'number':
                    try:
                        int(value)
                    except (ValueError, TypeError):
                        errors.append(f"{field_schema['label']} must be a number")
                elif expected_type == 'boolean':
                    if not isinstance(value, bool):
                        errors.append(f"{field_schema['label']} must be true/false")
        
        return errors
