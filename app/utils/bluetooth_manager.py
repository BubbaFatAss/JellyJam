"""
Bluetooth audio device management utility.
Provides functions to discover, pair, connect, and manage Bluetooth audio devices.
Supports both Windows (PowerShell) and Linux/Raspberry Pi (BlueZ/bluetoothctl).
"""

import subprocess
import re
import logging
import platform
import time

logger = logging.getLogger(__name__)

# Detect operating system
IS_WINDOWS = platform.system() == 'Windows'
IS_LINUX = platform.system() == 'Linux'


def _get_storage():
    """Get the storage instance from the main app module."""
    try:
        import sys
        import os
        # Add parent directory to path
        parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        import app
        return app.storage
    except Exception as e:
        logger.error(f"Failed to get storage instance: {e}")
        return None


def run_command(cmd, shell=False):
    """Run a shell command and return the output."""
    try:
        if IS_WINDOWS and not shell:
            # Windows PowerShell command
            result = subprocess.run(
                ['powershell', '-Command', cmd],
                capture_output=True,
                text=True,
                timeout=60
            )
        else:
            # Linux/Unix command
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                shell=isinstance(cmd, str)
            )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out: {cmd}")
        return "", "Command timed out", 1
    except Exception as e:
        logger.error(f"Command failed: {cmd}, error: {e}")
        return "", str(e), 1


def run_bluetoothctl_command(command, timeout=10):
    """
    Run a bluetoothctl command on Linux.
    Returns the output as a string.
    """
    try:
        # Use expect-style interaction with bluetoothctl
        process = subprocess.Popen(
            ['bluetoothctl'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        stdout, stderr = process.communicate(input=command + '\nexit\n', timeout=timeout)
        return stdout, stderr, process.returncode
    except subprocess.TimeoutExpired:
        process.kill()
        logger.error(f"bluetoothctl command timed out: {command}")
        return "", "Command timed out", 1
    except Exception as e:
        logger.error(f"bluetoothctl command failed: {command}, error: {e}")
        return "", str(e), 1


def scan_devices_linux():
    """
    Scan for Bluetooth devices on Linux using bluetoothctl.
    Returns a list of dictionaries with device information.
    """
    logger.info("Scanning for Bluetooth devices on Linux...")
    
    # First, start scanning
    run_bluetoothctl_command("power on\nscan on", timeout=2)
    
    # Wait for scan to find devices
    time.sleep(5)
    
    # Get list of devices
    stdout, stderr, returncode = run_bluetoothctl_command("devices", timeout=5)
    
    # Stop scanning
    run_bluetoothctl_command("scan off", timeout=2)
    
    devices = []
    if returncode == 0:
        # Parse output - format: "Device AA:BB:CC:DD:EE:FF Device Name"
        for line in stdout.split('\n'):
            if line.startswith('Device '):
                parts = line.split(None, 2)  # Split into max 3 parts
                if len(parts) >= 2:
                    address = parts[1]
                    name = parts[2] if len(parts) > 2 else 'Unknown Device'
                    
                    # Check if device is paired
                    info_stdout, _, _ = run_bluetoothctl_command(f"info {address}", timeout=3)
                    is_paired = 'Paired: yes' in info_stdout
                    
                    devices.append({
                        'name': name,
                        'address': address,
                        'paired': is_paired
                    })
    
    logger.info(f"Found {len(devices)} Bluetooth devices on Linux")
    return devices


def scan_devices_windows():
    """
    Scan for Bluetooth devices on Windows using PowerShell.
    Returns a list of dictionaries with device information.
    """
    logger.info("Scanning for Bluetooth devices on Windows...")
    
    # PowerShell script using Windows Runtime API to discover Bluetooth devices
    # This matches the working BluetoothDiscoveryWindows.ps1 script
    cmd = """
# Load Windows Runtime types
[Windows.Devices.Enumeration.DeviceInformation,Windows.Devices.Enumeration,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.BluetoothDevice,Windows.Devices.Bluetooth,ContentType=WindowsRuntime] | Out-Null

Add-Type -AssemblyName System.Runtime.WindowsRuntime

# Helper function to await async operations
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { 
    $_.Name -eq 'AsTask' -and 
    $_.GetParameters().Count -eq 1 -and 
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' 
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

# Get Bluetooth device selector
$selector = [Windows.Devices.Bluetooth.BluetoothDevice]::GetDeviceSelector()

# Find all Bluetooth devices
$op = [Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)
$devices = Await $op ([Windows.Devices.Enumeration.DeviceInformationCollection])

$result = @()
foreach ($device in $devices) {
    if ($device.Name -and $device.Id) {
        try {
            # Get detailed Bluetooth device information
            $btDeviceOp = [Windows.Devices.Bluetooth.BluetoothDevice]::FromIdAsync($device.Id)
            $btDevice = Await $btDeviceOp ([Windows.Devices.Bluetooth.BluetoothDevice])
            
            # Check if device is an audio device using Class of Device
            $isAudioDevice = $false
            
            if ($btDevice.ClassOfDevice) {
                $deviceClass = $btDevice.ClassOfDevice.RawValue
                # Major Device Class: bits 12-8
                $majorClass = ($deviceClass -shr 8) -band 0x1F
                # Minor Device Class: bits 7-2
                $minorClass = ($deviceClass -shr 2) -band 0x3F
                
                # Major class 4 = Audio/Video
                if ($majorClass -eq 4) {
                    $isAudioDevice = $true
                }
                
                # Major class 2 (Phone) with audio minor classes
                if ($majorClass -eq 2 -and ($minorClass -eq 1 -or $minorClass -eq 2 -or $minorClass -eq 3)) {
                    $isAudioDevice = $true
                }
            }
            
            # Fallback to name matching if no class info
            if (-not $isAudioDevice) {
                if ($device.Name -match 'speaker|headphone|headset|earbuds|earbud|soundbar|audio|bose|sony|jbl|beats|airpods|galaxy buds|sennheiser|skullcandy|anker soundcore') {
                    $isAudioDevice = $true
                }
            }
            
            # Only include audio devices
            if ($isAudioDevice) {
                # Extract MAC address from Device ID
                # Format: Bluetooth#Bluetooth08:71:90:f5:ae:fa-08:7c:39:4d:8c:c4
                # The second MAC address (after the dash) is the device's MAC
                $macAddress = "Unknown"
                if ($device.Id -match 'Bluetooth#Bluetooth[0-9A-Fa-f:]+[-]([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})') {
                    $macAddress = $matches[1]
                } elseif ($device.Id -match '([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})') {
                    # Fallback: grab the first properly formatted MAC address
                    $macAddress = $matches[1]
                }
                
                $isPaired = $device.Pairing.IsPaired
                $isConnected = $btDevice.ConnectionStatus -eq 'Connected'
                
                $result += @{
                    Name = $device.Name
                    Address = $macAddress
                    Paired = $isPaired
                    Connected = $isConnected
                    DeviceId = $device.Id
                }
            }
            
            $btDevice.Dispose()
        } catch {
            # Skip devices that can't be queried
        }
    }
}

# Also search for unpaired devices in pairing mode
try {
    $unpairedSelector = [Windows.Devices.Bluetooth.BluetoothDevice]::GetDeviceSelectorFromPairingState($false)
    $op = [Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($unpairedSelector)
    $unpairedDevices = Await $op ([Windows.Devices.Enumeration.DeviceInformationCollection])
    
    foreach ($device in $unpairedDevices) {
        if ($device.Name -and $device.Id) {
            try {
                $btDeviceOp = [Windows.Devices.Bluetooth.BluetoothDevice]::FromIdAsync($device.Id)
                $btDevice = Await $btDeviceOp ([Windows.Devices.Bluetooth.BluetoothDevice])
                
                # Check if device is an audio device
                $isAudioDevice = $false
                
                if ($btDevice.ClassOfDevice) {
                    $deviceClass = $btDevice.ClassOfDevice.RawValue
                    $majorClass = ($deviceClass -shr 8) -band 0x1F
                    $minorClass = ($deviceClass -shr 2) -band 0x3F
                    
                    if ($majorClass -eq 4) {
                        $isAudioDevice = $true
                    }
                    
                    if ($majorClass -eq 2 -and ($minorClass -eq 1 -or $minorClass -eq 2 -or $minorClass -eq 3)) {
                        $isAudioDevice = $true
                    }
                }
                
                if (-not $isAudioDevice) {
                    if ($device.Name -match 'speaker|headphone|headset|earbuds|earbud|soundbar|audio|bose|sony|jbl|beats|airpods|galaxy buds|sennheiser|skullcandy|anker soundcore') {
                        $isAudioDevice = $true
                    }
                }
                
                if ($isAudioDevice) {
                    # Extract MAC address from Device ID
                    $macAddress = "Unknown"
                    if ($device.Id -match 'Bluetooth#Bluetooth[0-9A-Fa-f:]+[-]([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})') {
                        $macAddress = $matches[1]
                    } elseif ($device.Id -match '([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})') {
                        $macAddress = $matches[1]
                    }
                    
                    # Check if already in results
                    $exists = $result | Where-Object { $_.Address -eq $macAddress }
                    if (-not $exists) {
                        $result += @{
                            Name = $device.Name
                            Address = $macAddress
                            Paired = $false
                            Connected = $false
                            DeviceId = $device.Id
                        }
                    }
                }
                
                $btDevice.Dispose()
            } catch {
                # Fallback to name-only matching for unpaired devices
                $isAudioDevice = $device.Name -match 'speaker|headphone|headset|earbuds|earbud|soundbar|audio|bose|sony|jbl|beats|airpods|galaxy buds|sennheiser|skullcandy|anker soundcore'
                
                if ($isAudioDevice) {
                    # Extract MAC address from Device ID
                    $macAddress = "Unknown"
                    if ($device.Id -match 'Bluetooth#Bluetooth[0-9A-Fa-f:]+[-]([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})') {
                        $macAddress = $matches[1]
                    } elseif ($device.Id -match '([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})') {
                        $macAddress = $matches[1]
                    }
                    
                    $exists = $result | Where-Object { $_.Address -eq $macAddress }
                    if (-not $exists) {
                        $result += @{
                            Name = $device.Name
                            Address = $macAddress
                            Paired = $false
                            Connected = $false
                            DeviceId = $device.Id
                        }
                    }
                }
            }
        }
    }
} catch {
    # Unpaired device search failed, just use paired devices
}

$result | ConvertTo-Json -Depth 3
"""
    
    stdout, stderr, returncode = run_command(cmd)
    
    if returncode != 0:
        logger.error(f"Failed to scan devices: {stderr}")
        return []
    
    devices = []
    try:
        import json
        raw_devices = json.loads(stdout) if stdout.strip() else []
        
        # Ensure it's a list
        if isinstance(raw_devices, dict):
            raw_devices = [raw_devices]
        
        for device in raw_devices:
            devices.append({
                'name': device.get('Name', 'Unknown Device'),
                'address': device.get('Address', ''),
                'paired': bool(device.get('Paired', False)),
                'device_id': device.get('DeviceId', '')
            })
    except Exception as e:
        logger.error(f"Failed to parse device list: {e}")
    
    logger.info(f"Found {len(devices)} Bluetooth devices on Windows")
    return devices


def scan_devices():
    """
    Scan for available Bluetooth devices.
    Returns a list of dictionaries with device information.
    """
    if IS_LINUX:
        return scan_devices_linux()
    elif IS_WINDOWS:
        return scan_devices_windows()
    else:
        logger.error(f"Unsupported operating system: {platform.system()}")
        return []


def get_status_linux():
    """
    Get current Bluetooth connection status on Linux.
    Returns a dictionary with connection info.
    """
    status = {
        'connected': False,
        'device_name': None,
        'device_address': None,
        'last_device': None
    }
    
    # Get list of paired devices and check their connection status
    stdout, stderr, returncode = run_bluetoothctl_command("devices", timeout=5)
    
    if returncode == 0:
        for line in stdout.split('\n'):
            if line.startswith('Device '):
                parts = line.split(None, 2)
                if len(parts) >= 2:
                    address = parts[1]
                    
                    # Check if this device is connected
                    info_stdout, _, _ = run_bluetoothctl_command(f"info {address}", timeout=3)
                    if 'Connected: yes' in info_stdout:
                        name = parts[2] if len(parts) > 2 else 'Unknown Device'
                        status['connected'] = True
                        status['device_name'] = name
                        status['device_address'] = address
                        break  # Take the first connected device
    
    # Try to load last connected device from config
    try:
        storage = _get_storage()
        if storage:
            config = storage.load()
            if 'bluetooth' in config and 'last_device' in config['bluetooth']:
                status['last_device'] = config['bluetooth']['last_device']
    except Exception as e:
        logger.debug(f"Could not load last device from config: {e}")
    
    return status


def get_status_windows():
    """
    Get current Bluetooth connection status on Windows.
    Returns a dictionary with connection info.
    """
    status = {
        'connected': False,
        'device_name': None,
        'device_address': None,
        'last_device': None
    }
    
    # Use Windows Runtime API to check for connected Bluetooth devices
    cmd = """
[Windows.Devices.Enumeration.DeviceInformation,Windows.Devices.Enumeration,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.BluetoothDevice,Windows.Devices.Bluetooth,ContentType=WindowsRuntime] | Out-Null

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

$selector = [Windows.Devices.Bluetooth.BluetoothDevice]::GetDeviceSelector()
$op = [Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)
$devices = Await $op ([Windows.Devices.Enumeration.DeviceInformationCollection])

$connectedDevice = $null
foreach ($device in $devices) {
    if ($device.Id) {
        try {
            $btDeviceOp = [Windows.Devices.Bluetooth.BluetoothDevice]::FromIdAsync($device.Id)
            $btDevice = Await $btDeviceOp ([Windows.Devices.Bluetooth.BluetoothDevice])
            
            if ($btDevice.ConnectionStatus -eq 'Connected') {
                $macAddress = if ($device.Id -match '([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}') { $matches[0] } else { $device.Id }
                $connectedDevice = @{
                    Name = $device.Name
                    Address = $macAddress
                }
                $btDevice.Dispose()
                break
            }
            $btDevice.Dispose()
        } catch {
            # Skip devices that can't be queried
        }
    }
}

if ($connectedDevice) {
    $connectedDevice | ConvertTo-Json
} else {
    Write-Output "{}"
}
"""
    
    stdout, stderr, returncode = run_command(cmd)
    
    if returncode == 0 and stdout.strip() and stdout.strip() != "{}":
        try:
            import json
            device = json.loads(stdout)
            
            if device and device.get('Name'):
                status['connected'] = True
                status['device_name'] = device.get('Name', 'Unknown Device')
                status['device_address'] = device.get('Address', '')
        except Exception as e:
            logger.error(f"Failed to parse status: {e}")
    
    # Try to load last connected device from config
    try:
        storage = _get_storage()
        if storage:
            config = storage.load()
            if 'bluetooth' in config and 'last_device' in config['bluetooth']:
                status['last_device'] = config['bluetooth']['last_device']
    except Exception as e:
        logger.debug(f"Could not load last device from config: {e}")
    
    return status


def get_status():
    """
    Get current Bluetooth connection status.
    Returns a dictionary with connection info.
    """
    if IS_LINUX:
        return get_status_linux()
    elif IS_WINDOWS:
        return get_status_windows()
    else:
        logger.error(f"Unsupported operating system: {platform.system()}")
        return {
            'connected': False,
            'device_name': None,
            'device_address': None,
            'last_device': None
        }


def pair_device_linux(address, pin=None):
    """
    Pair with a Bluetooth device by address on Linux.
    
    Args:
        address: MAC address of the device
        pin: Optional PIN code for pairing (e.g., "0000", "1234")
    
    Returns True if successful, False otherwise.
    """
    logger.info(f"Attempting to pair with device on Linux: {address}")
    
    # Remove device first if already paired (to force re-pairing)
    run_bluetoothctl_command(f"remove {address}", timeout=5)
    time.sleep(1)
    
    # If PIN provided, use agent with PIN
    if pin:
        logger.info(f"Pairing with PIN: {pin}")
        # Start pairing with agent that provides the PIN
        pair_cmd = f"agent on\ndefault-agent\npair {address}\n{pin}\nexit"
        stdout, stderr, returncode = run_bluetoothctl_command(pair_cmd, timeout=15)
    else:
        # Pair with device (no PIN)
        stdout, stderr, returncode = run_bluetoothctl_command(f"pair {address}", timeout=15)
    
    success = returncode == 0 and ('Pairing successful' in stdout or 'already paired' in stdout.lower())
    
    if success:
        # Trust the device so it auto-connects
        run_bluetoothctl_command(f"trust {address}", timeout=5)
        logger.info(f"Successfully paired with {address}")
    else:
        logger.error(f"Failed to pair with {address}: {stderr}")
    
    return success


def pair_device_windows(address, device_id=None, pin=None):
    """
    Pair with a Bluetooth device by address on Windows.
    
    Args:
        address: MAC address of the device
        device_id: Optional Windows device ID. If not provided, will scan to find it.
        pin: Optional PIN code for pairing (e.g., "0000", "1234")
    
    Returns True if successful, False otherwise.
    """
    logger.info(f"Attempting to pair with device on Windows: {address}")
    if pin:
        logger.info(f"Using PIN for pairing: {pin}")
    
    # If device_id not provided, scan to find it
    if not device_id:
        logger.info("No device_id provided, scanning for devices...")
        devices = scan_devices_windows()
        logger.info(f"Found {len(devices)} devices during pairing scan")
        device_info = next((d for d in devices if d['address'].lower() == address.lower()), None)
        
        if not device_info:
            logger.error(f"Device not found with address: {address}")
            logger.debug(f"Available devices: {[d['address'] for d in devices]}")
            return False
        
        if not device_info.get('device_id'):
            logger.error(f"Device found but has no device_id: {device_info}")
            return False
        
        device_id = device_info['device_id']
    
    logger.info(f"Using device ID: {device_id}")
    
    # Use Windows Runtime API to pair the device
    # We'll use custom pairing to handle the pairing ceremony
    cmd = f"""
[Windows.Devices.Enumeration.DeviceInformation,Windows.Devices.Enumeration,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.BluetoothDevice,Windows.Devices.Bluetooth,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Enumeration.DevicePairingKinds,Windows.Devices.Enumeration,ContentType=WindowsRuntime] | Out-Null

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{ $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' }})[0]

function Await($WinRtTask, $ResultType) {{
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}}

try {{
    # Get the device
    $deviceId = "{device_id}"
    $btDeviceOp = [Windows.Devices.Bluetooth.BluetoothDevice]::FromIdAsync($deviceId)
    $btDevice = Await $btDeviceOp ([Windows.Devices.Bluetooth.BluetoothDevice])
    
    # Get device information for pairing
    $deviceInfoOp = [Windows.Devices.Enumeration.DeviceInformation]::CreateFromIdAsync($deviceId)
    $deviceInfo = Await $deviceInfoOp ([Windows.Devices.Enumeration.DeviceInformation])
    
    # Check if already paired
    if ($deviceInfo.Pairing.IsPaired) {{
        Write-Output "Success:AlreadyPaired"
        $btDevice.Dispose()
        exit 0
    }}
    
    # Check if device can be paired
    if (-not $deviceInfo.Pairing.CanPair) {{
        Write-Output "Failed:CannotPair - Device does not support pairing"
        $btDevice.Dispose()
        exit 1
    }}
    
    # Create custom pairing with specific kinds
    $customPairing = $deviceInfo.Pairing.Custom
    
    # Output diagnostic info
    Write-Output "DEBUG:CanPair=$($deviceInfo.Pairing.CanPair)"
    Write-Output "DEBUG:IsPaired=$($deviceInfo.Pairing.IsPaired)"
    
    # Register event handler for pairing requested
    $pin = "{pin if pin else '0000'}"
    $pairingRequestedHandler = {{
        param($sender, $args)
        
        # Handle different pairing kinds - no logging inside the handler
        switch ($args.PairingKind) {{
            'ConfirmOnly' {{
                $args.Accept()
            }}
            'ConfirmPinMatch' {{
                $args.Accept()
            }}
            'ProvidePin' {{
                $args.AcceptWithPin($pin)
            }}
            'DisplayPin' {{
                # User should see the PIN on their device and confirm it
                $args.Accept($pin)
            }}
            default {{
                $args.Accept()
            }}
        }}
    }}
    
    # Register the event handler
    $pairingRequestedToken = $customPairing.add_PairingRequested($pairingRequestedHandler)
    
    try {{
        # Try pairing with multiple kinds - ConfirmOnly + ProvidePin is most common for audio devices
        $pairingKinds = [Windows.Devices.Enumeration.DevicePairingKinds]::ConfirmOnly -bor 
                       [Windows.Devices.Enumeration.DevicePairingKinds]::ProvidePin -bor
                       [Windows.Devices.Enumeration.DevicePairingKinds]::ConfirmPinMatch
        
        $pairingOp = $customPairing.PairAsync($pairingKinds)
        $pairingResult = Await $pairingOp ([Windows.Devices.Enumeration.DevicePairingResult])
        
        if ($pairingResult.Status -eq 'Paired' -or $pairingResult.Status -eq 'AlreadyPaired') {{
            Write-Output "Success"
        }} else {{
            # Output detailed error information
            Write-Output "Failed:$($pairingResult.Status)"
            Write-Output "ProtectionLevel: $($pairingResult.ProtectionLevelUsed)"
        }}
    }} finally {{
        # Unregister event handler
        $customPairing.remove_PairingRequested($pairingRequestedToken)
    }}
    
    $btDevice.Dispose()
}} catch {{
    Write-Output "Error: $_"
    Write-Output "Exception: $($_.Exception.Message)"
    if ($_.Exception.InnerException) {{
        Write-Output "InnerException: $($_.Exception.InnerException.Message)"
    }}
}}
"""
    
    stdout, stderr, returncode = run_command(cmd)
    
    logger.info(f"Pairing command return code: {returncode}")
    logger.info(f"Pairing stdout: {stdout}")
    if stderr:
        logger.error(f"Pairing stderr: {stderr}")
    
    # Check for success or already paired
    success = "Success" in stdout
    
    if success:
        logger.info(f"Successfully paired with {address}")
    else:
        # Extract the actual pairing status from the output
        status_match = re.search(r'Failed:(\w+)', stdout)
        if status_match:
            pairing_status = status_match.group(1)
            logger.error(f"Pairing failed with status: {pairing_status}")
            
            # If custom pairing failed, suggest using Windows Settings
            if pairing_status == "Failed":
                logger.warning(f"Custom pairing failed. Device may require manual pairing via Windows Settings.")
                logger.info(f"To pair manually: Settings > Bluetooth & devices > Add device")
            
            # Log additional context if available
            if "ProtectionLevel" in stdout:
                logger.info(f"Additional pairing info: {stdout}")
        else:
            logger.error(f"Failed to pair with {address}: {stdout} {stderr}")
    
    return success


def pair_device(address, device_id=None, pin=None):
    """
    Pair with a Bluetooth device by address.
    
    Args:
        address: MAC address of the device
        device_id: Optional device ID (Windows only). If not provided, will scan to find it.
        pin: Optional PIN code for pairing (e.g., "0000", "1234")
    
    Returns True if successful, False otherwise.
    """
    if IS_LINUX:
        return pair_device_linux(address, pin=pin)
    elif IS_WINDOWS:
        return pair_device_windows(address, device_id=device_id, pin=pin)
    else:
        logger.error(f"Unsupported operating system: {platform.system()}")
        return False


def connect_device_linux(address):
    """
    Connect to a paired Bluetooth device by address on Linux.
    Returns True if successful, False otherwise.
    """
    logger.info(f"Attempting to connect to device on Linux: {address}")
    
    # Connect to device
    stdout, stderr, returncode = run_bluetoothctl_command(f"connect {address}", timeout=15)
    
    success = returncode == 0 and ('Connection successful' in stdout or 'already connected' in stdout.lower())
    
    if success:
        logger.info(f"Successfully connected to {address}")
        # Save as last connected device
        try:
            storage = _get_storage()
            if storage:
                config = storage.load()
                if 'bluetooth' not in config:
                    config['bluetooth'] = {}
                
                # Get device name
                devices = scan_devices_linux()
                device_name = next((d['name'] for d in devices if d['address'] == address), 'Unknown Device')
                
                config['bluetooth']['last_device'] = {
                    'address': address,
                    'name': device_name
                }
                storage.save(config)
        except Exception as e:
            logger.error(f"Failed to save last device: {e}")
    else:
        logger.error(f"Failed to connect to {address}: {stderr}")
    
    return success


def connect_device_windows(address, device_id=None):
    """
    Connect to a paired Bluetooth device by address on Windows.
    
    Args:
        address: MAC address of the device
        device_id: Optional Windows device ID. If not provided, will scan to find it.
    
    Returns True if successful, False otherwise.
    """
    logger.info(f"Attempting to connect to device on Windows: {address}")
    
    # If device_id not provided, scan to find it
    if not device_id:
        logger.info("No device_id provided, scanning for devices...")
        devices = scan_devices_windows()
        device_info = next((d for d in devices if d['address'].lower() == address.lower()), None)
        
        if not device_info or not device_info.get('device_id'):
            logger.error(f"Device not found: {address}")
            return False
        
        device_id = device_info['device_id']
    
    logger.info(f"Using device ID: {device_id}")
    
    # Note: Windows doesn't have a direct "connect" API for Bluetooth audio devices
    # The connection happens automatically when the device is paired and in range
    # We can verify if the device is connectable/reachable
    cmd = f"""
[Windows.Devices.Bluetooth.BluetoothDevice,Windows.Devices.Bluetooth,ContentType=WindowsRuntime] | Out-Null

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{ $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' }})[0]

function Await($WinRtTask, $ResultType) {{
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}}

try {{
    $deviceId = "{device_id}"
    $btDeviceOp = [Windows.Devices.Bluetooth.BluetoothDevice]::FromIdAsync($deviceId)
    $btDevice = Await $btDeviceOp ([Windows.Devices.Bluetooth.BluetoothDevice])
    
    # Check connection status
    if ($btDevice.ConnectionStatus -eq 'Connected') {{
        Write-Output "AlreadyConnected"
    }} else {{
        # For audio devices, Windows automatically connects when they're set as default
        # We'll just verify the device is paired and available
        if ($btDevice.DeviceInformation.Pairing.IsPaired) {{
            Write-Output "Success"
        }} else {{
            Write-Output "NotPaired"
        }}
    }}
    
    $btDevice.Dispose()
}} catch {{
    Write-Output "Error: $_"
}}
"""
    
    stdout, stderr, returncode = run_command(cmd)
    
    success = "Success" in stdout or "AlreadyConnected" in stdout
    
    if success:
        logger.info(f"Successfully connected to {address}")
        # Save as last connected device
        try:
            storage = _get_storage()
            if storage:
                config = storage.load()
                if 'bluetooth' not in config:
                    config['bluetooth'] = {}
                
                # Get device name
                devices = scan_devices_windows()
                device_name = next((d['name'] for d in devices if d['address'] == address), 'Unknown Device')
                
                config['bluetooth']['last_device'] = {
                    'address': address,
                    'name': device_name
                }
                storage.save(config)
        except Exception as e:
            logger.error(f"Failed to save last device: {e}")
    else:
        logger.error(f"Failed to connect to {address}: {stdout} {stderr}")
    
    return success


def connect_device(address, device_id=None):
    """
    Connect to a paired Bluetooth device by address.
    
    Args:
        address: MAC address of the device
        device_id: Optional device ID (Windows only). If not provided, will scan to find it.
    
    Returns True if successful, False otherwise.
    """
    if IS_LINUX:
        return connect_device_linux(address)
    elif IS_WINDOWS:
        return connect_device_windows(address, device_id=device_id)
    else:
        logger.error(f"Unsupported operating system: {platform.system()}")
        return False


def disconnect_device_linux():
    """
    Disconnect the currently connected Bluetooth audio device on Linux.
    Returns True if successful, False otherwise.
    """
    logger.info("Attempting to disconnect Bluetooth device on Linux")
    
    # Get currently connected audio device
    status = get_status_linux()
    if not status['connected']:
        logger.warning("No device currently connected")
        return True  # Already disconnected
    
    address = status['device_address']
    
    # Disconnect the device
    stdout, stderr, returncode = run_bluetoothctl_command(f"disconnect {address}", timeout=10)
    
    success = returncode == 0 or 'Successful disconnected' in stdout
    if success:
        logger.info(f"Successfully disconnected from {address}")
    else:
        logger.error(f"Failed to disconnect: {stderr}")
    
    return success


def disconnect_device_windows():
    """
    Disconnect the currently connected Bluetooth audio device on Windows.
    Returns True if successful, False otherwise.
    """
    logger.info("Attempting to disconnect Bluetooth device on Windows")
    
    # Get currently connected audio device
    status = get_status_windows()
    if not status['connected']:
        logger.warning("No device currently connected")
        return True  # Already disconnected
    
    address = status['device_address']
    
    # Disable the device (disconnects it)
    cmd = f"""
    $device = Get-PnpDevice | Where-Object {{$_.InstanceId -like '*{address}*'}}
    if ($device) {{
        Disable-PnpDevice -InstanceId $device.InstanceId -Confirm:$false
        Write-Output "Success"
    }} else {{
        Write-Output "Device not found"
    }}
    """
    
    stdout, stderr, returncode = run_command(cmd)
    
    success = "Success" in stdout
    if success:
        logger.info(f"Successfully disconnected from {address}")
    else:
        logger.error(f"Failed to disconnect: {stderr}")
    
    return success


def disconnect_device():
    """
    Disconnect the currently connected Bluetooth audio device.
    Returns True if successful, False otherwise.
    """
    if IS_LINUX:
        return disconnect_device_linux()
    elif IS_WINDOWS:
        return disconnect_device_windows()
    else:
        logger.error(f"Unsupported operating system: {platform.system()}")
        return False
