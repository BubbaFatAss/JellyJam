"""
MQTT Client for JellyJam
Handles MQTT communication including Home Assistant MQTT Discovery
"""
import json
import threading
import time
from utils.logging_config import get_logger

log = get_logger(__name__)


class MQTTClient:
    def __init__(self, config, nightlight=None, display=None, socketio=None):
        """
        Initialize MQTT client.
        
        Args:
            config: MQTT configuration dict with broker, port, username, password, topic, discovery
            nightlight: NightLight instance to control
            display: Display matrix instance to control
            socketio: Flask-SocketIO instance for broadcasting updates
        """
        self.config = config
        self.nightlight = nightlight
        self.display = display
        self.socketio = socketio
        self.client = None
        self.connected = False
        self._stop_event = threading.Event()
        
        # MQTT topics for nightlight
        self.base_topic = config.get('topic', 'jellyjam')
        self.light_state_topic = f"{self.base_topic}/light/state"
        self.light_command_topic = f"{self.base_topic}/light/set"
        
        # MQTT topics for display
        self.display_state_topic = f"{self.base_topic}/display/state"
        self.display_command_topic = f"{self.base_topic}/display/set"
        
        self.availability_topic = f"{self.base_topic}/status"
        
        # Home Assistant Discovery
        self.discovery_enabled = config.get('discovery', True)
        self.discovery_prefix = 'homeassistant'
        self.nightlight_device_id = 'jellyjam_nightlight'
        self.display_device_id = 'jellyjam_display'
        
        self._init_client()
    
    def _init_client(self):
        """Initialize the MQTT client."""
        try:
            import paho.mqtt.client as mqtt
            
            self.client = mqtt.Client(client_id='jellyjam')
            
            # Set callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message
            
            # Set credentials if provided
            if self.config.get('username'):
                self.client.username_pw_set(
                    self.config['username'],
                    self.config.get('password', '')
                )
            
            # Set last will (for availability)
            self.client.will_set(
                self.availability_topic,
                payload='offline',
                qos=1,
                retain=True
            )
            
            # Connect to broker
            broker = self.config.get('broker', 'localhost')
            port = self.config.get('port', 1883)
            
            log.info('Connecting to MQTT broker at %s:%d...', broker, port)
            self.client.connect(broker, port, keepalive=60)
            
            # Start network loop in background thread
            self.client.loop_start()
            
        except ImportError:
            log.warning('paho-mqtt not installed. MQTT features disabled.')
            log.info('Install with: pip install paho-mqtt')
        except Exception as e:
            log.error('Error initializing MQTT client: %s', e)
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            log.info('Connected to MQTT broker')
            self.connected = True
            
            # Publish availability
            self.client.publish(
                self.availability_topic,
                payload='online',
                qos=1,
                retain=True
            )
            
            # Subscribe to command topics
            if self.nightlight:
                self.client.subscribe(self.light_command_topic)
                log.info('Subscribed to %s', self.light_command_topic)
            
            if self.display:
                self.client.subscribe(self.display_command_topic)
                log.info('Subscribed to %s', self.display_command_topic)
            
            # Send Home Assistant discovery message
            if self.discovery_enabled:
                self._send_discovery()
            
            # Publish current state for both nightlight and display
            self._publish_state()
            self.publish_display_state()
        else:
            log.error('Failed to connect to MQTT broker, return code %d', rc)
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker."""
        self.connected = False
        if rc != 0:
            log.warning('Unexpected MQTT disconnection (rc=%d). Reconnecting...', rc)
    
    def _on_message(self, client, userdata, msg):
        """Callback when a message is received."""
        try:
            if msg.topic == self.light_command_topic:
                self._handle_light_command(msg.payload.decode('utf-8'))
            elif msg.topic == self.display_command_topic:
                self._handle_display_command(msg.payload.decode('utf-8'))
        except Exception as e:
            log.error('Error handling MQTT message: %s', e)
    
    def _handle_light_command(self, payload):
        """Handle incoming light control commands from MQTT."""
        try:
            data = json.loads(payload)
            
            if not self.nightlight:
                return
            
            # Handle state (ON/OFF)
            if 'state' in data:
                on = data['state'].upper() == 'ON'
                self.nightlight.set_state(on=on)
            
            # Handle brightness (0-255)
            if 'brightness' in data:
                brightness = int(data['brightness'])
                self.nightlight.set_state(brightness=brightness)
            
            # Handle color
            if 'color' in data:
                color_data = data['color']
                if 'r' in color_data and 'g' in color_data and 'b' in color_data:
                    # Convert RGB to hex
                    r = int(color_data['r'])
                    g = int(color_data['g'])
                    b = int(color_data['b'])
                    hex_color = f'#{r:02x}{g:02x}{b:02x}'
                    self.nightlight.set_state(color=hex_color)
            
            # Publish updated state
            self._publish_state()
            
            # Emit Socket.IO update for web UI
            if self.socketio and self.nightlight:
                state = self.nightlight.get_state()
                log.debug('Emitting nightlight_update via Socket.IO: %s', state)
                self.socketio.emit('nightlight_update', state)
            
        except Exception as e:
            log.error('Error handling light command: %s', e)
    
    def _handle_display_command(self, payload):
        """Handle incoming display control commands from MQTT."""
        try:
            data = json.loads(payload)
            log.debug('Received display MQTT command: %s', data)
            
            if not self.display:
                log.warning('Cannot handle display command: display is None')
                return
            
            # Handle state (ON/OFF)
            if 'state' in data:
                on = data['state'].upper() == 'ON'
                log.debug('Setting display power to: %s', on)
                self.display.set_power(on)
            
            # Handle brightness (0-100 scale, matches display internal range)
            if 'brightness' in data:
                brightness = int(data['brightness'])
                log.debug('Setting display brightness to: %d', brightness)
                self.display.set_brightness(brightness)
            
            # Publish updated state
            log.debug('Publishing updated display state back to MQTT...')
            self.publish_display_state()
            
            # Emit Socket.IO update for web UI
            if self.socketio and self.display:
                display_state = {
                    'on': self.display.get_power(),
                    'brightness': self.display.get_brightness()
                }
                log.debug('Emitting display_power_update via Socket.IO: %s', display_state)
                self.socketio.emit('display_power_update', display_state)
            else:
                if not self.socketio:
                    log.debug('Cannot emit Socket.IO update: socketio is None')
            
        except Exception as e:
            log.error('Error handling display command: %s', e, exc_info=True)
    
    def _send_discovery(self):
        """Send Home Assistant MQTT Discovery messages."""
        try:
            # Nightlight discovery
            if self.nightlight:
                nightlight_topic = f"{self.discovery_prefix}/light/{self.nightlight_device_id}/config"
                
                nightlight_payload = {
                    "name": "JellyJam Nightlight",
                    "unique_id": self.nightlight_device_id,
                    "state_topic": self.light_state_topic,
                    "command_topic": self.light_command_topic,
                    "availability_topic": self.availability_topic,
                    "schema": "json",
                    "brightness": True,
                    "brightness_scale": 255,
                    "color_mode": True,
                    "supported_color_modes": ["rgb"],
                    "device": {
                        "identifiers": ["jellyjam"],
                        "name": "JellyJam NFC Player",
                        "model": "NFC Music Player with Nightlight",
                        "manufacturer": "JellyJam"
                    }
                }
                
                self.client.publish(
                    nightlight_topic,
                    payload=json.dumps(nightlight_payload),
                    qos=1,
                    retain=True
                )
                
                log.info('Sent Home Assistant discovery for nightlight to %s', nightlight_topic)
            
            # Display discovery
            if self.display:
                display_topic = f"{self.discovery_prefix}/light/{self.display_device_id}/config"
                
                display_payload = {
                    "name": "JellyJam Display",
                    "unique_id": self.display_device_id,
                    "state_topic": self.display_state_topic,
                    "command_topic": self.display_command_topic,
                    "availability_topic": self.availability_topic,
                    "schema": "json",
                    "brightness": True,
                    "brightness_scale": 100,
                    "icon": "mdi:television",
                    "device": {
                        "identifiers": ["jellyjam"],
                        "name": "JellyJam NFC Player",
                        "model": "NFC Music Player with Nightlight",
                        "manufacturer": "JellyJam"
                    }
                }
                
                self.client.publish(
                    display_topic,
                    payload=json.dumps(display_payload),
                    qos=1,
                    retain=True
                )
                
                log.info('Sent Home Assistant discovery for display to %s', display_topic)
            
        except Exception as e:
            log.error('Error sending Home Assistant discovery: %s', e)
    
    def _publish_state(self):
        """Publish current nightlight state to MQTT."""
        try:
            if not self.nightlight or not self.connected:
                return
            
            state = self.nightlight.get_state()
            
            # Convert to Home Assistant format
            r, g, b = self._hex_to_rgb(state['color'])
            
            payload = {
                "state": "ON" if state['on'] else "OFF",
                "brightness": state['brightness'],
                "color_mode": "rgb",
                "color": {
                    "r": r,
                    "g": g,
                    "b": b
                }
            }
            
            self.client.publish(
                self.light_state_topic,
                payload=json.dumps(payload),
                qos=1,
                retain=True
            )
            
        except Exception as e:
            log.error('Error publishing state: %s', e)
    
    def publish_display_state(self):
        """Publish current display state to MQTT."""
        try:
            if not self.display or not self.connected:
                if not self.display:
                    log.debug('Display state not published: display is None')
                if not self.connected:
                    log.debug('Display state not published: not connected to MQTT')
                return
            
            # Get display state
            power_on = self.display.get_power()
            brightness_100 = self.display.get_brightness()  # Already in 0-100 range
            
            payload = {
                "state": "ON" if power_on else "OFF",
                "brightness": brightness_100
            }
            
            log.debug('Publishing display state to %s: %s', self.display_state_topic, payload)
            log.debug('  Display object: %s', self.display)
            log.debug('  Power: %s, Brightness: %d', power_on, brightness_100)
            
            self.client.publish(
                self.display_state_topic,
                payload=json.dumps(payload),
                qos=1,
                retain=True
            )
            
        except Exception as e:
            log.error('Error publishing display state: %s', e, exc_info=True)
    
    def _hex_to_rgb(self, hex_color):
        """Convert hex color to RGB tuple."""
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
    
    def publish_state_update(self, state):
        """
        Callback for nightlight state changes.
        Publishes state to MQTT.
        """
        self._publish_state()
    
    def disconnect(self):
        """Disconnect from MQTT broker."""
        try:
            if self.client and self.connected:
                # Publish offline status
                self.client.publish(
                    self.availability_topic,
                    payload='offline',
                    qos=1,
                    retain=True
                )
                
                self.client.loop_stop()
                self.client.disconnect()
                log.info('Disconnected from MQTT broker')
        except Exception as e:
            log.error('Error disconnecting from MQTT: %s', e)


def create_mqtt_client(config, nightlight=None, display=None, socketio=None):
    """
    Factory function to create and initialize MQTT client.
    
    Args:
        config: MQTT configuration dict
        nightlight: NightLight instance to control
        display: Display matrix instance to control
        socketio: Flask-SocketIO instance for broadcasting updates
    
    Returns:
        MQTTClient instance or None if disabled/error
    """
    try:
        if not config.get('enabled'):
            return None
        
        client = MQTTClient(config, nightlight, display, socketio)
        
        # Register state change callback with nightlight
        if nightlight:
            nightlight.register_state_callback(client.publish_state_update)
        
        # TODO: Register state change callback with display when display has callback support
        
        return client
        
    except Exception as e:
        log.error('Failed to create MQTT client: %s', e)
        return None
