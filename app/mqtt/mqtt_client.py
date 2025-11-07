"""
MQTT Client for JellyJam
Handles MQTT communication including Home Assistant MQTT Discovery
"""
import json
import threading
import time


class MQTTClient:
    def __init__(self, config, nightlight=None):
        """
        Initialize MQTT client.
        
        Args:
            config: MQTT configuration dict with broker, port, username, password, topic, discovery
            nightlight: NightLight instance to control
        """
        self.config = config
        self.nightlight = nightlight
        self.client = None
        self.connected = False
        self._stop_event = threading.Event()
        
        # MQTT topics
        self.base_topic = config.get('topic', 'jellyjam')
        self.light_state_topic = f"{self.base_topic}/light/state"
        self.light_command_topic = f"{self.base_topic}/light/set"
        self.availability_topic = f"{self.base_topic}/status"
        
        # Home Assistant Discovery
        self.discovery_enabled = config.get('discovery', True)
        self.discovery_prefix = 'homeassistant'
        self.device_id = 'jellyjam_nightlight'
        
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
            
            print(f'Connecting to MQTT broker at {broker}:{port}...')
            self.client.connect(broker, port, keepalive=60)
            
            # Start network loop in background thread
            self.client.loop_start()
            
        except ImportError:
            print('Warning: paho-mqtt not installed. MQTT features disabled.')
            print('Install with: pip install paho-mqtt')
        except Exception as e:
            print(f'Error initializing MQTT client: {e}')
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            print('Connected to MQTT broker')
            self.connected = True
            
            # Publish availability
            self.client.publish(
                self.availability_topic,
                payload='online',
                qos=1,
                retain=True
            )
            
            # Subscribe to command topic
            self.client.subscribe(self.light_command_topic)
            print(f'Subscribed to {self.light_command_topic}')
            
            # Send Home Assistant discovery message
            if self.discovery_enabled:
                self._send_discovery()
            
            # Publish current state
            self._publish_state()
        else:
            print(f'Failed to connect to MQTT broker, return code {rc}')
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker."""
        self.connected = False
        if rc != 0:
            print(f'Unexpected MQTT disconnection (rc={rc}). Reconnecting...')
    
    def _on_message(self, client, userdata, msg):
        """Callback when a message is received."""
        try:
            if msg.topic == self.light_command_topic:
                self._handle_light_command(msg.payload.decode('utf-8'))
        except Exception as e:
            print(f'Error handling MQTT message: {e}')
    
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
            
        except Exception as e:
            print(f'Error handling light command: {e}')
    
    def _send_discovery(self):
        """Send Home Assistant MQTT Discovery message."""
        try:
            # Discovery topic format: <discovery_prefix>/<component>/[<node_id>/]<object_id>/config
            discovery_topic = f"{self.discovery_prefix}/light/{self.device_id}/config"
            
            # Build discovery payload
            discovery_payload = {
                "name": "JellyJam Nightlight",
                "unique_id": self.device_id,
                "state_topic": self.light_state_topic,
                "command_topic": self.light_command_topic,
                "availability_topic": self.availability_topic,
                "schema": "json",
                "brightness": True,
                "brightness_scale": 255,
                "rgb": True,
                "device": {
                    "identifiers": ["jellyjam"],
                    "name": "JellyJam NFC Player",
                    "model": "NFC Music Player with Nightlight",
                    "manufacturer": "JellyJam"
                }
            }
            
            self.client.publish(
                discovery_topic,
                payload=json.dumps(discovery_payload),
                qos=1,
                retain=True
            )
            
            print(f'Sent Home Assistant discovery to {discovery_topic}')
            
        except Exception as e:
            print(f'Error sending Home Assistant discovery: {e}')
    
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
            print(f'Error publishing state: {e}')
    
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
                print('Disconnected from MQTT broker')
        except Exception as e:
            print(f'Error disconnecting from MQTT: {e}')


def create_mqtt_client(config, nightlight=None):
    """
    Factory function to create and initialize MQTT client.
    
    Args:
        config: MQTT configuration dict
        nightlight: NightLight instance to control
    
    Returns:
        MQTTClient instance or None if disabled/error
    """
    try:
        if not config.get('enabled'):
            return None
        
        client = MQTTClient(config, nightlight)
        
        # Register state change callback with nightlight
        if nightlight:
            nightlight.register_state_callback(client.publish_state_update)
        
        return client
        
    except Exception as e:
        print(f'Failed to create MQTT client: {e}')
        return None
