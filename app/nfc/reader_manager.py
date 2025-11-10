"""
NFC Reader Manager - loads and manages NFC reader plugins.

Similar to display_manager, this module handles plugin selection,
instantiation, and lifecycle management for NFC readers.
"""

import logging
from .plugins.base import NFCReaderPlugin
from .plugins.mock import MockNFCPlugin
from .plugins.pn532 import PN532Plugin

log = logging.getLogger(__name__)

# Registry of available NFC reader plugins
AVAILABLE_PLUGINS = {
    'mock': MockNFCPlugin,
    'pn532': PN532Plugin,
}


class NFCReaderManager:
    """
    Manages NFC reader plugin lifecycle.
    
    Loads the active plugin from configuration, instantiates it,
    and provides a unified interface for the application.
    """
    
    def __init__(self, callback=None, storage=None):
        """
        Initialize the NFC reader manager.
        
        Args:
            callback: Function to call when a card is scanned.
            storage: Storage instance for loading configuration.
        """
        self.callback = callback
        self.storage = storage
        self.active_plugin = None
        self.plugin_instance = None
        
        # Load configuration and initialize plugin
        self._load_and_init()
    
    def _load_and_init(self):
        """Load configuration and initialize the active plugin."""
        try:
            # Load NFC config from storage
            config = {}
            plugin_name = 'mock'  # Default
            plugin_config = {}
            
            if self.storage:
                try:
                    cfg = self.storage.load() or {}
                    nfc_cfg = cfg.get('nfc', {})
                    plugin_name = nfc_cfg.get('active', 'mock')
                    plugin_config = nfc_cfg.get('plugins', {}).get(plugin_name, {})
                    log.debug('Loaded NFC config: plugin=%s, config=%s', plugin_name, plugin_config)
                except Exception as e:
                    log.warning('Failed to load NFC config from storage: %s', e)
            
            # Get plugin class
            plugin_class = AVAILABLE_PLUGINS.get(plugin_name)
            if not plugin_class:
                log.warning('Unknown NFC plugin: %s, falling back to mock', plugin_name)
                plugin_class = MockNFCPlugin
                plugin_name = 'mock'
            
            # Validate plugin configuration
            errors = plugin_class.validate_config(plugin_config)
            if errors:
                log.warning('Invalid config for %s plugin: %s, using defaults', 
                           plugin_name, ', '.join(errors))
                # Continue with defaults from schema
            
            # Instantiate plugin
            self.active_plugin = plugin_name
            self.plugin_instance = plugin_class(callback=self.callback, config=plugin_config)
            
            log.info('NFC reader initialized: %s', plugin_class.get_plugin_name())
            
        except Exception as e:
            log.error('Failed to initialize NFC reader plugin: %s', e, exc_info=True)
            # Fall back to mock plugin
            log.warning('Falling back to mock NFC reader')
            self.active_plugin = 'mock'
            self.plugin_instance = MockNFCPlugin(callback=self.callback)
    
    def start(self):
        """Start the active NFC reader plugin."""
        if self.plugin_instance:
            try:
                self.plugin_instance.start()
                log.info('NFC reader started: %s', self.get_plugin_name())
            except Exception as e:
                log.error('Failed to start NFC reader: %s', e, exc_info=True)
        else:
            log.warning('No NFC reader plugin available to start')
    
    def stop(self):
        """Stop the active NFC reader plugin."""
        if self.plugin_instance:
            try:
                self.plugin_instance.stop()
                log.info('NFC reader stopped')
            except Exception as e:
                log.error('Failed to stop NFC reader: %s', e, exc_info=True)
    
    def simulate_scan(self, card_id):
        """
        Simulate a card scan (for testing/web UI).
        
        Args:
            card_id: The card ID to simulate.
        """
        if self.plugin_instance:
            self.plugin_instance.simulate_scan(card_id)
        else:
            log.warning('No NFC reader plugin available for simulation')
    
    def set_active_plugin(self, plugin_name, plugin_config=None):
        """
        Switch to a different NFC reader plugin.
        
        Args:
            plugin_name: Name of the plugin to activate.
            plugin_config: Configuration dictionary for the plugin.
        """
        try:
            # Stop current plugin
            if self.plugin_instance:
                self.plugin_instance.stop()
            
            # Get new plugin class
            plugin_class = AVAILABLE_PLUGINS.get(plugin_name)
            if not plugin_class:
                raise ValueError(f'Unknown NFC plugin: {plugin_name}')
            
            # Validate configuration
            if plugin_config:
                errors = plugin_class.validate_config(plugin_config)
                if errors:
                    raise ValueError(f'Invalid configuration: {", ".join(errors)}')
            
            # Instantiate and start new plugin
            self.active_plugin = plugin_name
            self.plugin_instance = plugin_class(callback=self.callback, config=plugin_config or {})
            self.plugin_instance.start()
            
            log.info('Switched to NFC plugin: %s', plugin_class.get_plugin_name())
            
        except Exception as e:
            log.error('Failed to switch NFC plugin: %s', e, exc_info=True)
            raise
    
    def get_plugin_name(self):
        """Get the human-readable name of the active plugin."""
        if self.plugin_instance:
            return self.plugin_instance.get_plugin_name()
        return "None"
    
    def get_active_plugin(self):
        """Get the active plugin identifier."""
        return self.active_plugin
    
    @staticmethod
    def get_available_plugins():
        """
        Get a dictionary of all available NFC reader plugins.
        
        Returns:
            dict: {plugin_id: plugin_class}
        """
        return AVAILABLE_PLUGINS.copy()
    
    @staticmethod
    def get_plugin_info(plugin_name):
        """
        Get information about a specific plugin.
        
        Args:
            plugin_name: Plugin identifier.
            
        Returns:
            dict: Plugin information including name and config schema.
        """
        plugin_class = AVAILABLE_PLUGINS.get(plugin_name)
        if not plugin_class:
            return None
        
        return {
            'id': plugin_name,
            'name': plugin_class.get_plugin_name(),
            'config_schema': plugin_class.get_config_schema()
        }


def create_nfc_reader(callback=None, storage=None):
    """
    Factory function to create an NFC reader manager.
    
    Args:
        callback: Function to call when a card is scanned.
        storage: Storage instance for loading configuration.
        
    Returns:
        NFCReaderManager: Configured NFC reader manager instance.
    """
    return NFCReaderManager(callback=callback, storage=storage)
