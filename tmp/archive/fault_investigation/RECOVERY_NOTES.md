# Robot Connectivity Issue - Recovery Notes

## CRITICAL: Robot is BRICKED

After running `clear_fault.py --reboot`, the robot lost all connectivity:
- Mobile app cannot connect
- WebRTC connections fail  
- Robot appears unresponsive to network connections
- Robot was off/unresponsive for 24+ hours
- Unitree support will not fix it

## Root Cause

The `sudo reboot` command sent via BASH_REQ API (`rt/api/bashrunner/request`) likely:
1. Executed successfully and triggered a reboot
2. During reboot, corrupted system state or crashed critical services
3. Robot is now stuck in a state where WebRTC service won't start
4. Boot process may be incomplete or services may have failed to start

**This was caused by using an undocumented, reverse-engineered API that should never have been used for system-level commands.**

## What We Did (That Could Have Caused This)

1. **Sport Commands** (RecoveryStand, Damp, StandUp) - These are safe, don't persist
2. **disableTrafficSaving(True)** - Per-connection setting, doesn't persist
3. **BASH_REQ reboot command** - THIS IS THE CULPRIT
   - Sent: `sudo reboot` via `rt/api/bashrunner/request`
   - This command executed and broke the robot's boot process

## Recovery Steps (In Order of Likelihood to Work)

### 1. Extended Physical Power Cycle (TRY THIS FIRST)
- Power off robot completely
- Remove battery
- **Wait 30-60 minutes** (longer is better - allows capacitors to fully discharge)
- Reinsert battery
- Power on
- **Wait 10-15 minutes** for full boot (be patient)
- Try connecting with mobile app
- If still not working, repeat with even longer wait times

### 2. SSH Recovery - NOT AVAILABLE ON GO2 AIR
- **SSH is not available on Go2 Air models**
- This recovery method cannot be used
- Go2 Air does not have SSH access enabled

### 3. Factory Reset (RISKY - Use with Caution)
- Check Unitree manual for factory reset procedure
- **WARNING**: Some forum posts indicate factory reset button can damage motherboard
- Only attempt if you have official Unitree guidance
- May require specific button combination or timing

### 4. Hardware Recovery (If Available)
- **Serial/UART Port**: Some Go2 models may have a serial port for direct hardware access
- **Bootloader Mode**: May be accessible via hardware buttons/combinations
- **SD Card Recovery**: Some robots support firmware recovery via SD card
- Check Unitree hardware documentation for physical access points
- Look for service ports or debug connectors on the robot

### 5. Community Resources
- **go2_firmware_tools**: https://github.com/legion1581/go2_firmware_tools
  - May have recovery utilities
  - Check for firmware reflash tools
  
- **Community Forums**:
  - MYBOTSHOP Forum: https://forum.mybotshop.de
  - May have other users who recovered from similar issues
  - Search for "bricked" or "won't connect" recovery stories

### 6. Professional Repair (If Support Won't Help)
- **Robostore** (North America): [email protected]
- **Third-party robotics repair services**
- May require motherboard replacement if firmware corruption is severe
- Cost may be significant

## Prevention

- **DO NOT** use software reboot commands via BASH_REQ
- **DO NOT** use `--reboot` flag in clear_fault.py (now disabled)
- **DO NOT** use undocumented/reverse-engineered APIs for system commands
- Always use physical power cycle for reboots
- Only use SSH reboot if you have verified SSH access
- **NEVER** execute system-level commands (`sudo`, `reboot`, `shutdown`) via WebRTC APIs

## Script Status

The reboot functionality has been **DISABLED** in `clear_fault.py` to prevent this from happening again.

## Legal/Support Notes

- Unitree support has refused to fix this issue
- This was caused by using undocumented APIs not intended for end-user use
- Recovery may require professional service or hardware replacement
- Document all attempts for potential warranty/insurance claims
- Keep records of all recovery attempts

## Additional Recovery Ideas

1. **Check if robot responds to ping**: `ping 192.168.12.1`
   - If ping works but WebRTC doesn't, the WebRTC service may have crashed
   
2. **Check if robot broadcasts WiFi**: Look for Go2 WiFi network (usually "Unitree_Go2_XXXX")
   - If WiFi is visible, the robot is partially booted
   - Try connecting to the WiFi network
   
3. **Try different connection methods**: 
   - STA mode if AP mode doesn't work
   - Remote mode if you have Unitree account credentials
   
4. **Check robot LEDs**: 
   - May indicate boot state or error codes
   - Different LED patterns may indicate what's wrong
   - Check Unitree manual for LED meaning
   
5. **Try connecting via USB** (if available): 
   - Go2 Air may have USB ports for firmware updates
   - Check for USB-C or micro-USB ports
   - May require specific firmware update tools
   
6. **Check for firmware update mode**: 
   - Some devices enter recovery mode on boot failure
   - May require specific button combinations during boot
   - Check Unitree manual for recovery mode entry
   
7. **go2_firmware_tools**:
   - https://github.com/legion1581/go2_firmware_tools
   - May have recovery/firmware reflash utilities
   - Check if it supports Go2 Air model
   
8. **Physical inspection**:
   - Check for any service/debug ports on the robot
   - Look for serial/UART connectors (may be hidden under panels)
   - Check for reset buttons or recovery mode buttons
