# Bluetooth Device Discovery Script for Windows
# Discovers both paired and unpaired Bluetooth devices

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

Write-Host "=== Scanning for Bluetooth Devices ===" -ForegroundColor Cyan
Write-Host ""

# Method 1: Get paired Bluetooth devices from PnP
Write-Host "--- Paired Bluetooth Devices (PnP) ---" -ForegroundColor Yellow
$pairedDevices = Get-PnpDevice -Class Bluetooth | Where-Object {
    ($_.Status -eq 'OK' -or $_.Status -eq 'Error' -or $_.Status -eq 'Unknown') -and
    $_.FriendlyName -notmatch 'Bluetooth Adapter|Bluetooth Radio|Generic Bluetooth|Microsoft Bluetooth|Intel.*Bluetooth|Realtek.*Bluetooth|Broadcom.*Bluetooth|Qualcomm.*Bluetooth'
}

foreach ($device in $pairedDevices) {
    $name = $device.FriendlyName
    $instanceId = $device.InstanceId
    $status = $device.Status
    
    # Extract MAC address from InstanceId
    $macAddress = "Unknown"
    if ($instanceId -match 'DEV_([0-9A-Fa-f]{12})') {
        $mac = $matches[1]
        $macAddress = ($mac -replace '(..)','$1:').TrimEnd(':')
    } elseif ($instanceId -match '([0-9A-Fa-f]{2}[_:-]){5}[0-9A-Fa-f]{2}') {
        $macAddress = $matches[0] -replace '_',':'
    }
    
    Write-Host "  Device: $name" -ForegroundColor Green
    Write-Host "    MAC: $macAddress"
    Write-Host "    Status: $status"
    Write-Host "    Instance ID: $instanceId"
    Write-Host ""
}

# Method 2: Use Windows Runtime API for all Bluetooth devices
Write-Host "--- All Bluetooth Devices (Windows Runtime API) ---" -ForegroundColor Yellow

try {
    # Get Bluetooth device selector
    $selector = [Windows.Devices.Bluetooth.BluetoothDevice]::GetDeviceSelector()
    
    # Find all Bluetooth devices
    Write-Host "Searching for devices..." -ForegroundColor Gray
    $op = [Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)
    $devices = Await $op ([Windows.Devices.Enumeration.DeviceInformationCollection])
    
    Write-Host "Found $($devices.Count) device(s), filtering for audio devices..." -ForegroundColor Gray
    Write-Host ""
    
    $audioDeviceCount = 0
    foreach ($device in $devices) {
        if ($device.Name -and $device.Id) {
            try {
                # Get detailed Bluetooth device information
                $btDeviceOp = [Windows.Devices.Bluetooth.BluetoothDevice]::FromIdAsync($device.Id)
                $btDevice = Await $btDeviceOp ([Windows.Devices.Bluetooth.BluetoothDevice])
                
                # Check if device is an audio device using multiple methods
                $isAudioDevice = $false
                $detectionMethod = ""
                
                # Method 1: Check Bluetooth Class of Device
                if ($btDevice.ClassOfDevice) {
                    $deviceClass = $btDevice.ClassOfDevice.RawValue
                    # Major Device Class: bits 12-8
                    $majorClass = ($deviceClass -shr 8) -band 0x1F
                    # Minor Device Class: bits 7-2
                    $minorClass = ($deviceClass -shr 2) -band 0x3F
                    
                    # Major class 4 = Audio/Video
                    if ($majorClass -eq 4) {
                        $isAudioDevice = $true
                        $detectionMethod = "Device Class (Audio/Video)"
                    }
                    
                    # Major class 2 (Phone) with audio minor classes
                    if ($majorClass -eq 2 -and ($minorClass -eq 1 -or $minorClass -eq 2 -or $minorClass -eq 3)) {
                        $isAudioDevice = $true
                        $detectionMethod = "Device Class (Phone/Audio)"
                    }
                }
                
                # Method 2: Check for Audio UUIDs in device services
                # Common Bluetooth Audio Service UUIDs
                $audioServiceUUIDs = @(
                    "0000110b-0000-1000-8000-00805f9b34fb",  # Audio Sink
                    "0000110a-0000-1000-8000-00805f9b34fb",  # Audio Source
                    "0000110c-0000-1000-8000-00805f9b34fb",  # Remote Control Target
                    "0000110e-0000-1000-8000-00805f9b34fb",  # A/V Remote Control
                    "00001108-0000-1000-8000-00805f9b34fb",  # Headset
                    "0000111e-0000-1000-8000-00805f9b34fb",  # Handsfree
                    "0000110d-0000-1000-8000-00805f9b34fb"   # Advanced Audio Distribution
                )
                
                # Get GATT services
                try {
                    $gattServicesOp = $btDevice.GetGattServicesAsync()
                    $gattResult = Await $gattServicesOp ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult])
                    
                    if ($gattResult.Status -eq 0) {  # Success
                        foreach ($service in $gattResult.Services) {
                            $serviceUuid = $service.Uuid.ToString().ToLower()
                            if ($audioServiceUUIDs -contains $serviceUuid) {
                                $isAudioDevice = $true
                                $detectionMethod = "Audio Service UUID"
                                break
                            }
                        }
                    }
                } catch {
                    # GATT services might not be available for classic Bluetooth devices
                }
                
                # Method 3: Check Bluetooth LE Advertisement data
                # (This is more for BLE devices)
                
                # Method 4: Fallback to name matching (least reliable but sometimes necessary)
                if (-not $isAudioDevice) {
                    if ($device.Name -match 'speaker|headphone|headset|earbuds|earbud|soundbar|audio|bose|sony|jbl|beats|airpods|galaxy buds|sennheiser|skullcandy|anker soundcore') {
                        $isAudioDevice = $true
                        $detectionMethod = "Name Pattern"
                    }
                }
                
                # Always show device info for debugging, but highlight audio devices
                if ($isAudioDevice) {
                    $audioDeviceCount++
                    Write-Host "  [AUDIO] Device: $($device.Name)" -ForegroundColor Green
                } else {
                    Write-Host "  [OTHER] Device: $($device.Name)" -ForegroundColor DarkGray
                }
                
                # Extract MAC address
                $macAddress = "Unknown"
                if ($device.Id -match '([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}') {
                    $macAddress = $matches[0]
                }
                
                $isPaired = $device.Pairing.IsPaired
                $isConnected = $btDevice.ConnectionStatus -eq 'Connected'
                
                Write-Host "    MAC: $macAddress"
                Write-Host "    Paired: $isPaired"
                Write-Host "    Connected: $isConnected"
                
                if ($btDevice.ClassOfDevice) {
                    $deviceClass = $btDevice.ClassOfDevice.RawValue
                    $majorClass = ($deviceClass -shr 8) -band 0x1F
                    $minorClass = ($deviceClass -shr 2) -band 0x3F
                    Write-Host "    Device Class: 0x$($deviceClass.ToString('X6')) (Major: $majorClass, Minor: $minorClass)"
                }
                
                if ($isAudioDevice -and $detectionMethod) {
                    Write-Host "    Detection Method: $detectionMethod" -ForegroundColor Cyan
                }
                
                Write-Host "    Device ID: $($device.Id)"
                Write-Host ""
                
                $btDevice.Dispose()
            } catch {
                Write-Host "  Device: $($device.Name)" -ForegroundColor Yellow
                Write-Host "    Error getting details: $_"
                Write-Host ""
            }
        }
    }
    
    Write-Host "Found $audioDeviceCount audio device(s) total" -ForegroundColor Cyan
    Write-Host ""
} catch {
    Write-Host "Error using Windows Runtime API: $_" -ForegroundColor Red
}

# Method 3: Specifically search for unpaired devices
Write-Host "--- Unpaired Bluetooth Audio Devices ---" -ForegroundColor Yellow

try {
    # Get selector for unpaired devices only
    $unpairedSelector = [Windows.Devices.Bluetooth.BluetoothDevice]::GetDeviceSelectorFromPairingState($false)
    
    Write-Host "Searching for unpaired devices..." -ForegroundColor Gray
    $op = [Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($unpairedSelector)
    $unpairedDevices = Await $op ([Windows.Devices.Enumeration.DeviceInformationCollection])
    
    Write-Host "Found $($unpairedDevices.Count) unpaired device(s), filtering for audio devices..." -ForegroundColor Gray
    Write-Host ""
    
    if ($unpairedDevices.Count -eq 0) {
        Write-Host "  No unpaired devices found (devices must be in pairing mode to be discovered)" -ForegroundColor Gray
        Write-Host ""
    } else {
        $unpairedAudioCount = 0
        foreach ($device in $unpairedDevices) {
            if ($device.Name) {
                try {
                    # Try to get Bluetooth device details for unpaired devices
                    $btDeviceOp = [Windows.Devices.Bluetooth.BluetoothDevice]::FromIdAsync($device.Id)
                    $btDevice = Await $btDeviceOp ([Windows.Devices.Bluetooth.BluetoothDevice])
                    
                    # Check if device is an audio device using same methods as Method 2
                    $isAudioDevice = $false
                    $detectionMethod = ""
                    
                    # Method 1: Check Bluetooth Class of Device
                    if ($btDevice.ClassOfDevice) {
                        $deviceClass = $btDevice.ClassOfDevice.RawValue
                        $majorClass = ($deviceClass -shr 8) -band 0x1F
                        $minorClass = ($deviceClass -shr 2) -band 0x3F
                        
                        # Major class 4 = Audio/Video
                        if ($majorClass -eq 4) {
                            $isAudioDevice = $true
                            $detectionMethod = "Device Class (Audio/Video)"
                        }
                        
                        # Major class 2 (Phone) with audio minor classes
                        if ($majorClass -eq 2 -and ($minorClass -eq 1 -or $minorClass -eq 2 -or $minorClass -eq 3)) {
                            $isAudioDevice = $true
                            $detectionMethod = "Device Class (Phone/Audio)"
                        }
                    }
                    
                    # Method 2: Fallback to name matching if no class info
                    if (-not $isAudioDevice) {
                        if ($device.Name -match 'speaker|headphone|headset|earbuds|earbud|soundbar|audio|bose|sony|jbl|beats|airpods|galaxy buds|sennheiser|skullcandy|anker soundcore') {
                            $isAudioDevice = $true
                            $detectionMethod = "Name Pattern"
                        }
                    }
                    
                    if ($isAudioDevice) {
                        $unpairedAudioCount++
                        
                        $macAddress = "Unknown"
                        if ($device.Id -match '([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}') {
                            $macAddress = $matches[0]
                        }
                        
                        Write-Host "  [AUDIO] Device: $($device.Name)" -ForegroundColor Cyan
                        Write-Host "    MAC: $macAddress"
                        if ($btDevice.ClassOfDevice) {
                            $deviceClass = $btDevice.ClassOfDevice.RawValue
                            $majorClass = ($deviceClass -shr 8) -band 0x1F
                            $minorClass = ($deviceClass -shr 2) -band 0x3F
                            Write-Host "    Device Class: 0x$($deviceClass.ToString('X6')) (Major: $majorClass, Minor: $minorClass)"
                        }
                        Write-Host "    Detection Method: $detectionMethod" -ForegroundColor Cyan
                        Write-Host "    Device ID: $($device.Id)"
                        Write-Host ""
                    }
                    
                    $btDevice.Dispose()
                } catch {
                    # If we can't get device details, fall back to name-only matching
                    $isAudioDevice = $device.Name -match 'speaker|headphone|headset|earbuds|earbud|soundbar|audio|bose|sony|jbl|beats|airpods|galaxy buds|sennheiser|skullcandy|anker soundcore'
                    
                    if ($isAudioDevice) {
                        $unpairedAudioCount++
                        
                        $macAddress = "Unknown"
                        if ($device.Id -match '([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}') {
                            $macAddress = $matches[0]
                        }
                        
                        Write-Host "  [AUDIO] Device: $($device.Name)" -ForegroundColor Cyan
                        Write-Host "    MAC: $macAddress"
                        Write-Host "    Detection Method: Name Pattern (fallback)" -ForegroundColor Yellow
                        Write-Host "    Device ID: $($device.Id)"
                        Write-Host ""
                    }
                }
            }
        }
        
        if ($unpairedAudioCount -eq 0) {
            Write-Host "  No unpaired audio devices found" -ForegroundColor Gray
            Write-Host ""
        } else {
            Write-Host "Found $unpairedAudioCount unpaired audio device(s)" -ForegroundColor Cyan
            Write-Host ""
        }
    }
} catch {
    Write-Host "Error searching for unpaired devices: $_" -ForegroundColor Red
}

Write-Host "=== Scan Complete ===" -ForegroundColor Cyan
