"""
Script to attempt clearing faults on the Go2 robot.

⚠️ WARNING: This script previously had a --reboot option that BRICKED a robot.
   The reboot functionality has been COMPLETELY REMOVED.
   DO NOT attempt to add it back - it uses undocumented APIs that can break the robot.

This script tries several methods:
1. RecoveryStand command (1006) - Attempts to recover from a fault state
2. Damp command (1001) - Puts robot in safe/damped mode
3. StandUp command (1004) - Attempts to stand up normally

NOTE: Soft reboot functionality was REMOVED after it bricked a robot.
      Use physical power cycle only - NEVER use software reboot commands.

IMPORTANT LIMITATIONS:
- Software commands may only work for certain fault types (software faults, pose faults)
- Hardware/thermal/motor-driver faults may require physical power cycle
- BASH_REQ API may be restricted or unavailable in newer firmware versions (≥1.1.2)
- API IDs (1006=RecoveryStand, 1001=Damp, 1004=StandUp, etc.) are NOT officially documented
- These mappings come from community reverse-engineering and may vary by firmware version
- Command effectiveness depends on firmware version, model (AIR/PRO/EDU), and fault type
- Some models (PRO) may have restrictions compared to EDU versions

Note: Some faults may require a hard reset (power cycle) of the robot.
      Soft reboot will disconnect the WebRTC connection.

Usage:
    python clear_fault.py              # Try all fault clearing methods
    # --reboot flag is DISABLED - do not use (causes connectivity issues)
"""

import asyncio
import logging
import sys
import argparse
from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC, SPORT_CMD

# Enable logging
logging.basicConfig(level=logging.WARNING)  # Reduce verbosity
logging.getLogger("aiortc").setLevel(logging.WARNING)

# Import connection patches from sit-stand.py
import builtins as _builtins
import re as _re
from go2_webrtc_driver import util as _util

# Monkey patch print_status to strip emojis for Windows
_orig_print_status = _util.print_status
def _patched_print_status(msg):
    # Strip emojis and other non-ASCII characters that break Windows terminals
    cleaned = _re.sub(r'[^\x00-\x7F]+', '', msg)
    _builtins.print(cleaned)
_util.print_status = _patched_print_status

# Import SDP patching from min_connect_status
from go2_webrtc_driver import unitree_auth as _unitree_auth

def _rewrite_sdp_to_legacy(sdp: str) -> str:
    """Rewrite SDP to legacy format compatible with Go2."""
    lines = sdp.split("\r\n")
    new_lines = []
    for line in lines:
        if line.startswith("m=application"):
            # Preserve the port but ensure UDP/DTLS/SCTP format
            if "UDP/DTLS/SCTP" not in line:
                # Extract port if present
                parts = line.split()
                if len(parts) >= 2:
                    port = parts[1]
                    new_lines.append(f"m=application {port} UDP/DTLS/SCTP webrtc-datachannel")
                else:
                    new_lines.append("m=application 9 UDP/DTLS/SCTP webrtc-datachannel")
            else:
                new_lines.append(line)
        elif line.startswith("a=fingerprint:"):
            # Only keep sha-256 fingerprints, remove sha-384 and sha-512
            if "sha-256" in line.lower():
                new_lines.append(line)
            # Skip sha-384 and sha-512
        else:
            new_lines.append(line)
    return "\r\n".join(new_lines) + "\r\n"

_orig_send_sdp = _unitree_auth.send_sdp_to_local_peer
async def _patched_send_sdp(ip, sdp):
    """Patched SDP sender that rewrites to legacy format."""
    rewritten = _rewrite_sdp_to_legacy(sdp)
    return await _orig_send_sdp(ip, rewritten)
_unitree_auth.send_sdp_to_local_peer = _patched_send_sdp

# Connection error handling
connection_error_detected = {"value": False}

def exception_handler(loop, context):
    """Handle unhandled exceptions in background tasks."""
    exception = context.get('exception')
    if exception and isinstance(exception, AttributeError):
        msg = str(exception)
        if "'NoneType' object has no attribute 'media'" in msg:
            logging.warning(f"WARNING: Intermittent connection error detected: {msg}")
            logging.warning("         This usually indicates a stale connection. Retrying...")
            connection_error_detected["value"] = True
            return
    loop.default_exception_handler(context)

async def _wait_for_peer_connected(conn, timeout=3.0):
    """Wait for peer connection state to be 'connected'."""
    start_time = asyncio.get_event_loop().time()
    while True:
        if connection_error_detected["value"]:
            raise RuntimeError("Connection error detected in background task")
        
        if conn.pc.connectionState == "connected":
            return True
        
        if asyncio.get_event_loop().time() - start_time > timeout:
            raise RuntimeError(f"Peer connection state '{conn.pc.connectionState}' not 'connected' after {timeout}s")
        
        await asyncio.sleep(0.1)

async def _ensure_remote_description(conn, timeout=2.0):
    """Ensure remote SDP description is present."""
    start_time = asyncio.get_event_loop().time()
    while True:
        if connection_error_detected["value"]:
            raise RuntimeError("Connection error detected in background task")
        
        if conn.pc.remoteDescription is not None:
            return True
        
        if asyncio.get_event_loop().time() - start_time > timeout:
            raise RuntimeError("Remote SDP description not set after connection")
        
        await asyncio.sleep(0.1)

async def connect_with_monitoring(conn):
    """Connect with monitoring for background errors."""
    connection_error_detected["value"] = False
    connect_task = asyncio.create_task(conn.connect())
    
    # Monitor for errors while connecting
    while not connect_task.done():
        if connection_error_detected["value"]:
            connect_task.cancel()
            raise RuntimeError("Connection error detected during connect()")
        await asyncio.sleep(0.1)
    
    await connect_task
    
    # Verify connection state
    if conn.pc.connectionState == "failed":
        raise RuntimeError("Connection state is 'failed'")
    
    # Wait for peer connection
    await _wait_for_peer_connected(conn, timeout=3.0)
    await _ensure_remote_description(conn, timeout=2.0)
    
    # Wait for datachannel to open (validation completes)
    start_time = asyncio.get_event_loop().time()
    while not conn.datachannel.data_channel_opened:
        if connection_error_detected["value"]:
            raise RuntimeError("Connection error detected while waiting for data channel")
        
        if asyncio.get_event_loop().time() - start_time > 10.0:
            channel_state = getattr(conn.datachannel.channel, "readyState", "unknown")
            raise RuntimeError(f"Data channel did not open within 10s. Channel state: '{channel_state}'")
        
        await asyncio.sleep(0.1)

# Global error tracking
current_errors = []
error_received = asyncio.Event()
error_callback_registered = False

async def check_errors(conn, timeout=3.0):
    """Check for current errors by waiting for error messages."""
    global current_errors, error_received, error_callback_registered
    from go2_webrtc_driver.constants import DATA_CHANNEL_TYPE
    from go2_webrtc_driver.msgs import error_handler as _error_handler
    
    # Register callback if not already registered
    if not error_callback_registered:
        # Patch handle_response to capture errors
        original_handle_response = conn.datachannel.handle_response
        async def patched_handle_response(msg):
            # Check if this is an error message and capture it
            msg_type = msg.get("type")
            if msg_type in {DATA_CHANNEL_TYPE["ERRORS"], DATA_CHANNEL_TYPE["ADD_ERROR"], DATA_CHANNEL_TYPE["RM_ERROR"]}:
                global current_errors, error_received
                data = msg.get("data", [])
                current_errors = data.copy()
                error_received.set()
            # Call original handler
            return await original_handle_response(msg)
        conn.datachannel.handle_response = patched_handle_response
        error_callback_registered = True
    
    current_errors = []
    error_received.clear()
    
    # Wait for error message or timeout
    try:
        await asyncio.wait_for(error_received.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass  # No errors received within timeout
    
    return current_errors

async def clear_fault(conn, method="recovery"):
    """
    Attempt to clear faults using various methods.
    
    Args:
        conn: WebRTC connection
        method: "recovery" (RecoveryStand), "damp" (Damp), "standup" (StandUp), 
                "balancestand" (BalanceStand), "stop" (StopMove), or "reboot" (Soft reboot)
    """
    print(f"\n{'='*60}")
    print(f"Attempting to clear fault using: {method.upper()}")
    print(f"{'='*60}\n")
    
    # Check errors before
    print("Checking current errors...")
    errors_before = await check_errors(conn)
    if errors_before:
        print(f"  Found {len(errors_before)} active error(s)")
        for err in errors_before:
            if len(err) >= 3:
                print(f"    Error: {err[1]}-{err[2]}")
    else:
        print("  No errors detected")
    
    try:
        if method == "recovery":
            print("\nSending RecoveryStand command (api_id: 1006)...")
            print("   Note: API ID mapping is unverified - may vary by firmware")
            response = await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["SPORT_MOD"],
                {"api_id": SPORT_CMD["RecoveryStand"]}
            )
            print(f"Response status: {response.get('data', {}).get('header', {}).get('status', {})}")
            print("\nWaiting 8 seconds for recovery...")
            await asyncio.sleep(8)
            
        elif method == "damp":
            print("\nSending Damp command (api_id: 1001)...")
            print("   Note: API ID mapping is unverified - may vary by firmware")
            response = await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["SPORT_MOD"],
                {"api_id": SPORT_CMD["Damp"]}
            )
            print(f"Response status: {response.get('data', {}).get('header', {}).get('status', {})}")
            print("\nWaiting 5 seconds...")
            await asyncio.sleep(5)
            
            # Then try to stand up
            print("Sending StandUp command (1004)...")
            response = await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["SPORT_MOD"],
                {"api_id": SPORT_CMD["StandUp"]}
            )
            print(f"Response status: {response.get('data', {}).get('header', {}).get('status', {})}")
            print("\nWaiting 8 seconds for robot to stand...")
            await asyncio.sleep(8)
            
        elif method == "standup":
            print("\nSending StandUp command (api_id: 1004)...")
            print("   Note: API ID mapping is unverified - may vary by firmware")
            response = await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["SPORT_MOD"],
                {"api_id": SPORT_CMD["StandUp"]}
            )
            print(f"Response status: {response.get('data', {}).get('header', {}).get('status', {})}")
            print("\nWaiting 8 seconds for robot to stand...")
            await asyncio.sleep(8)
            
        elif method == "balancestand":
            print("\nSending BalanceStand command (api_id: 1002)...")
            print("   Note: API ID mapping is unverified - may vary by firmware")
            response = await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["SPORT_MOD"],
                {"api_id": SPORT_CMD["BalanceStand"]}
            )
            print(f"Response status: {response.get('data', {}).get('header', {}).get('status', {})}")
            print("\nWaiting 5 seconds...")
            await asyncio.sleep(5)
            
        elif method == "stop":
            print("\nSending StopMove command (api_id: 1003)...")
            print("   Note: API ID mapping is unverified - may vary by firmware")
            response = await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["SPORT_MOD"],
                {"api_id": SPORT_CMD["StopMove"]}
            )
            print(f"Response status: {response.get('data', {}).get('header', {}).get('status', {})}")
            print("\nWaiting 3 seconds...")
            await asyncio.sleep(3)
            
            # Then try recovery
            print("Sending RecoveryStand command (1006)...")
            response = await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["SPORT_MOD"],
                {"api_id": SPORT_CMD["RecoveryStand"]}
            )
            print(f"Response status: {response.get('data', {}).get('header', {}).get('status', {})}")
            print("\nWaiting 8 seconds for recovery...")
            await asyncio.sleep(8)
            
        elif method == "reboot":
            print("\n⚠ REBOOT FUNCTIONALITY DISABLED")
            print("   This feature has been disabled due to connectivity issues.")
            print("   The BASH_REQ reboot command may have caused connection problems.")
            print("\n   If you need to reboot the robot:")
            print("     1. Physical power cycle: Remove battery, wait 10-15 min, reinsert")
            print("     2. Use SSH (if accessible): ssh unitree@192.168.12.1")
            print("        Then run: sudo reboot")
            print("\n   DO NOT use the software reboot - it may break connectivity.")
            return False
        
        # Check errors after
        print("\nChecking errors after command...")
        await asyncio.sleep(1)  # Give time for error updates
        errors_after = await check_errors(conn)
        
        if errors_after:
            print(f"  Still have {len(errors_after)} active error(s)")
            for err in errors_after:
                if len(err) >= 3:
                    print(f"    Error: {err[1]}-{err[2]}")
            errors_cleared = len(errors_before) - len(errors_after)
            if errors_cleared > 0:
                print(f"  ✓ Cleared {errors_cleared} error(s)")
            return len(errors_after) == 0
        else:
            if errors_before:
                print(f"  ✓ All errors cleared!")
            else:
                print("  No errors detected")
            return True
            
        print(f"\n✓ {method.upper()} command completed")
        return True
        
    except Exception as e:
        print(f"\n✗ Error executing {method} command: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Main function."""
    parser = argparse.ArgumentParser(description='Go2 Fault Clearing Utility')
    # Reboot functionality REMOVED - it bricked a robot
    # parser.add_argument('--reboot', action='store_true', 
    #                    help='Only attempt soft reboot (will disconnect)')
    args = parser.parse_args()
    
    print("="*60)
    print("GO2 FAULT CLEARING UTILITY")
    print("="*60)
    print("Direct AP mode: 192.168.12.1")
    print("\nIMPORTANT: Make sure the Unitree Go2 mobile app is CLOSED")
    print("           The Go2 can only handle one WebRTC connection.\n")
    
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(exception_handler)
    
    conn = None
    max_attempts = 3
    
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"\n{'='*60}")
            print(f"Connection attempt {attempt}/{max_attempts}")
            print(f"{'='*60}\n")
            
            conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
            
            # Connect with monitoring
            await connect_with_monitoring(conn)
            
            # Disable traffic saving for keepalive
            await conn.datachannel.disableTrafficSaving(True)
            
            print("\n✓ Connected successfully!")
            break
            
        except Exception as e:
            print(f"\n✗ Connection attempt {attempt} failed: {e}")
            if conn:
                try:
                    await asyncio.wait_for(conn.disconnect(), timeout=5.0)
                except:
                    pass
            conn = None
            
            if attempt < max_attempts:
                print(f"Retrying in 1 second...\n")
                await asyncio.sleep(1)
            else:
                print("\n✗ Failed to connect after 3 attempts")
                return
    
    if not conn:
        print("\n✗ Could not establish connection")
        return
    
    # Reboot functionality completely removed - do not attempt
    # This was causing robots to become bricked/unresponsive
    
    # Try different fault clearing methods
    # Note: "reboot" will disconnect, so it should be last or used separately
    methods = ["recovery", "stop", "damp", "balancestand", "standup"]
    
    for method in methods:
        success = await clear_fault(conn, method)
        if success:
            print(f"\n✓ {method.upper()} method executed successfully")
            # If errors were cleared, we can stop
            final_errors = await check_errors(conn, timeout=2.0)
            if not final_errors:
                print("\n✓ All faults cleared! Stopping early.")
                break
        else:
            print(f"\n✗ {method.upper()} method failed")
        
        # Small delay between methods
        if method != methods[-1]:
            await asyncio.sleep(2)
    
    # Final error check
    print("\n" + "="*60)
    print("FINAL ERROR STATUS")
    print("="*60)
    final_errors = await check_errors(conn, timeout=3.0)
    if final_errors:
        print(f"\n⚠ Still have {len(final_errors)} active error(s):")
        for err in final_errors:
            if len(err) >= 3:
                error_source = err[1]
                error_code = err[2]
                print(f"  Error: {error_source}-{error_code}")
                
                # Provide specific guidance based on error
                if error_source == 309 or error_source == 300:
                    if error_code == 4:
                        print("    → This is a MOTOR DRIVER OVERHEATING error")
                        print("    → REQUIRED ACTIONS:")
                        print("      1. POWER OFF the robot completely")
                        print("      2. Remove battery and wait 10-15 minutes for cooling")
                        print("      3. Check for blocked cooling fans or vents")
                        print("      4. Ensure operating environment is within temperature limits")
                        print("      5. Power cycle (hard reset) after cooldown period")
                        print("      6. If error persists, motor driver may need replacement")
                    elif error_code == 10:
                        print("    → This is a MOTOR WINDING OVERHEATING error")
                        print("    → REQUIRED ACTIONS:")
                        print("      1. Allow robot to cool down completely (30+ minutes)")
                        print("      2. Check for excessive load or continuous operation")
                        print("      3. Power cycle after cooldown")
                elif error_source == 600 and error_code == 4:
                    print("    → This is OVERHEATING SOFTWARE PROTECTION")
                    print("    → REQUIRED ACTIONS:")
                    print("      1. Allow robot to cool down")
                    print("      2. Power cycle after cooldown")
    else:
        print("\n✓ No active errors detected!")
    
    print("\n" + "="*60)
    print("Fault clearing attempts completed")
    print("="*60)
    print("\n" + "="*60)
    print("IMPORTANT NOTES:")
    print("="*60)
    print("• Software commands may only work for certain fault types")
    print("• Hardware/thermal/motor-driver faults often require physical intervention")
    print("• API ID mappings (1006=RecoveryStand, 1001=Damp, etc.) are UNVERIFIED")
    print("  - These come from community reverse-engineering, not official Unitree docs")
    print("  - Mappings may vary by firmware version (e.g., 1.1.x vs newer)")
    print("  - No public official source confirms these exact numeric IDs")
    print("• BASH_REQ interface may be restricted/removed in firmware ≥1.1.2")
    print("• Some models (PRO) may have restrictions compared to EDU")
    print("• Command effectiveness depends on firmware version and model")
    print("\nIf faults persist, you may need to:")
    print("  1. Power cycle the robot (hard reset) - TURN OFF, REMOVE BATTERY, WAIT 10-15 MIN, REINSERT")
    print("  2. Check motor temperatures (overheating may require 15-30 min cooldown)")
    print("  3. Check for physical obstructions or damage")
    print("  4. Review error logs in sensor_monitor.py")
    print("  5. Check cooling fans and ventilation")
    print("  6. Contact Unitree support if hardware issue suspected")
    print("  7. Consider firmware version - older firmware may have more API access")
    
    # Disconnect
    print("\nDisconnecting...")
    try:
        await asyncio.wait_for(conn.disconnect(), timeout=5.0)
        print("✓ Disconnected")
    except Exception as e:
        print(f"⚠ Warning during disconnect: {e}")
    
    print("\nDone.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)

