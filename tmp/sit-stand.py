"""
Go2 Sit/Stand Toggle Script
============================

This script toggles the Unitree Go2 robot between sitting and standing modes.

Usage:
    python tmp/sit-stand.py

Prerequisites:
    - Go2 robot in AP mode
    - Connected to Go2 WiFi network (192.168.12.1)
    - Unitree mobile app NOT connected (only one WebRTC connection allowed)
"""

import builtins as _builtins
import re as _re

# Remove emojis from output for Windows terminal compatibility
_builtin_print = _builtins.print
emoji_pattern = _re.compile('[\U0001F300-\U0001FAD6\U0001FAE0-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF]+', flags=_re.UNICODE)


def _no_emoji_print(*args, **kwargs):
    args = tuple(emoji_pattern.sub('', str(a)) for a in args)
    return _builtin_print(*args, **kwargs)


_builtins.print = _no_emoji_print

import asyncio
import json
import logging
import sys
import time

from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC, SPORT_CMD
from aiortc import RTCPeerConnection, RTCSessionDescription

import go2_webrtc_driver.util as _util
import go2_webrtc_driver.webrtc_datachannel as _webrtc_datachannel
import go2_webrtc_driver.unitree_auth as _unitree_auth
import go2_webrtc_driver.webrtc_driver as _webrtc_driver_mod


# Monkey-patch print_status to remove emojis
def _patched_print_status(status_type, status_message):
    current_time = time.strftime("%H:%M:%S")
    _builtin_print(f"[{current_time}] {status_type}: {status_message}")


_util.print_status = _patched_print_status


# Extended timeout for data channel (gives more time for SCTP association)
_orig_wait_datachannel_open = _webrtc_datachannel.WebRTCDataChannel.wait_datachannel_open


async def _patched_wait_datachannel_open(self, timeout=5):
    """Extended wait for data channel with better logging."""
    deadline = time.time() + 30.0
    last_log = 0
    while time.time() < deadline:
        if getattr(self, "data_channel_opened", False):
            return
        channel = getattr(self, "channel", None)
        state = getattr(channel, "readyState", None)
        if state == "open":
            return
        if time.time() - last_log >= 2.0:
            _builtin_print(f"Waiting for datachannel readyState= {state}")
            last_log = time.time()
        await asyncio.sleep(0.1)
    _builtin_print("Warning: data channel did not report open within 30s; continuing anyway")


_webrtc_datachannel.WebRTCDataChannel.wait_datachannel_open = _patched_wait_datachannel_open


# SDP patches for aiortc/Go2 compatibility (RFC 8841 interop)
# These patches ensure the Go2 responds with legacy SDP format that aiortc can handle
_orig_send_local = _unitree_auth.send_sdp_to_local_peer


def _rewrite_sdp_to_legacy(sdp: str) -> str:
    """Rewrite SDP from RFC 8841 format to legacy format for aiortc compatibility."""
    if not isinstance(sdp, str):
        return sdp
    lines = []
    saw_m_application = False
    saw_sctpmap = False
    for line in sdp.splitlines():
        if line.startswith("m=application"):
            # Preserve port but use legacy DTLS/SCTP format
            parts = line.split()
            port = parts[1] if len(parts) > 1 else "9"
            # Handle both "UDP/DTLS/SCTP" and "DTLS/SCTP" formats
            lines.append(f"m=application {port} UDP/DTLS/SCTP 5000")
            saw_m_application = True
        elif line.startswith("a=sctp-port"):
            # Replace RFC 8841 sctp-port with legacy sctpmap
            lines.append("a=sctpmap:5000 webrtc-datachannel 65535")
            saw_sctpmap = True
        elif line.startswith("a=sctpmap"):
            # Already in legacy format, keep it
            lines.append(line)
            saw_sctpmap = True
        else:
            lines.append(line)
    if saw_m_application and not saw_sctpmap:
        lines.append("a=sctpmap:5000 webrtc-datachannel 65535")
    return "\r\n".join(lines) + "\r\n"


def _patched_send_sdp(ip, sdp):
    """Patch SDP exchange to strip problematic fingerprints and rewrite to legacy format."""
    try:
        payload = json.loads(sdp)
        offer_sdp = payload.get("sdp", "")
        _builtin_print(f"\n=== OFFER (before rewrite) ===")
        for line in offer_sdp.splitlines():
            if line.startswith("m=") or line.startswith("a=sctp"):
                _builtin_print(f"  {line}")
        # Remove sha-384 and sha-512 fingerprints (forces Go2 to use sha-256)
        filtered = [
            line for line in offer_sdp.splitlines()
            if not line.startswith("a=fingerprint:sha-384")
            and not line.startswith("a=fingerprint:sha-512")
        ]
        rewritten_offer = _rewrite_sdp_to_legacy("\r\n".join(filtered) + "\r\n")
        _builtin_print(f"\n=== OFFER (after rewrite) ===")
        for line in rewritten_offer.splitlines():
            if line.startswith("m=") or line.startswith("a=sctp"):
                _builtin_print(f"  {line}")
        _builtin_print()
        payload["sdp"] = rewritten_offer
        sdp = json.dumps(payload)
    except Exception:
        pass
    result = _orig_send_local(ip, sdp)
    if result:
        try:
            # Log the answer but DON'T rewrite it - use as-is
            answer = json.loads(result)
            original_sdp = answer.get("sdp", "")
            _builtin_print(f"\n=== REMOTE ANSWER (NOT rewriting) ===")
            for line in original_sdp.splitlines():
                if line.startswith("m=") or line.startswith("a=sctp"):
                    _builtin_print(f"  {line}")
            _builtin_print()
            # Return the answer AS-IS, no rewriting
            # answer["sdp"] = _rewrite_sdp_to_legacy(original_sdp)
            # result = json.dumps(answer)
        except Exception as e:
            _builtin_print(f"ERROR logging answer SDP: {e}")
    return result


_unitree_auth.send_sdp_to_local_peer = _patched_send_sdp
_webrtc_driver_mod.send_sdp_to_local_peer = _patched_send_sdp

_orig_set_local_description = RTCPeerConnection.setLocalDescription


async def _patched_setLocalDescription(self, description):
    """Ensure local SDP uses legacy format."""
    try:
        if description and isinstance(description, RTCSessionDescription) and description.type == "offer":
            description = RTCSessionDescription(
                sdp=_rewrite_sdp_to_legacy(description.sdp),
                type=description.type
            )
    except Exception:
        pass
    return await _orig_set_local_description(self, description)


RTCPeerConnection.setLocalDescription = _patched_setLocalDescription

_orig_get_answer_from_local_peer = _webrtc_driver_mod.Go2WebRTCConnection.get_answer_from_local_peer


async def _patched_get_answer_from_local_peer(self, pc, ip):
    """Ensure SDP exchange uses legacy format."""
    if pc and pc.localDescription:
        offer_dict = {
            "id": "STA_localNetwork" if self.connectionMethod == WebRTCConnectionMethod.LocalSTA else "",
            "sdp": _rewrite_sdp_to_legacy(pc.localDescription.sdp),
            "type": pc.localDescription.type,
            "token": self.token
        }
        peer_answer_json = _patched_send_sdp(ip, json.dumps(offer_dict))
        return peer_answer_json
    return await _orig_get_answer_from_local_peer(self, pc, ip)


_webrtc_driver_mod.Go2WebRTCConnection.get_answer_from_local_peer = _patched_get_answer_from_local_peer


async def get_robot_state(conn):
    """Get the current robot state (standing/crouched) by subscribing to sportmodestate.
    
    Note: GetState (1034) and GetBodyHeight (1024) APIs don't work reliably,
    so we subscribe to LF_SPORT_MOD_STATE topic and extract body_height from one message.
    """
    state_received = asyncio.Event()
    body_height = None
    
    def sportmodestate_callback(message):
        nonlocal body_height
        try:
            # Extract body_height from the message - that's all we need
            state_data = message.get('data', {})
            body_height = state_data.get('body_height', None)
            if body_height is not None:
                state_received.set()
        except Exception as e:
            _builtin_print(f"Error parsing sport mode state: {e}")
            state_received.set()
    
    try:
        # Subscribe, wait for one message with body_height, then unsubscribe
        conn.datachannel.pub_sub.subscribe(RTC_TOPIC['LF_SPORT_MOD_STATE'], sportmodestate_callback)
        await asyncio.sleep(0.3)  # Brief delay for subscription to register
        
        try:
            await asyncio.wait_for(state_received.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            _builtin_print("Warning: Timeout waiting for sport mode state")
            body_height = None
        finally:
            # Always unsubscribe
            try:
                conn.datachannel.pub_sub.unsubscribe(RTC_TOPIC['LF_SPORT_MOD_STATE'], sportmodestate_callback)
            except:
                pass
        
        if body_height is None:
            return None
        
        # Based on observed data:
        # - body_height ~0.074 = crouched (StandDown, flush with floor)
        # - body_height ~0.315 = standing
        # Use threshold of 0.15 to distinguish
        is_standing = body_height > 0.15
        is_crouched = body_height <= 0.15
        
        return {
            'body_height': body_height,
            'is_standing': is_standing,
            'is_sitting': is_crouched
        }
    except Exception as e:
        _builtin_print(f"Warning: Could not get robot state: {e}")
        return None


async def main():
    """Main function to toggle between sit and stand."""
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("aiortc").setLevel(logging.WARNING)

    print("============================================================")
    print("GO2 SIT/STAND TOGGLE")
    print("============================================================")
    print("Direct AP mode: 192.168.12.1")
    print("\nIMPORTANT: Make sure the Unitree Go2 mobile app is CLOSED")
    print("           The Go2 can only handle one WebRTC connection.\n")

    # Flag to track if we detected the background task error
    connection_error_detected = {"value": False}

    # Set up exception handler to catch background task errors
    def exception_handler(loop, context):
        """Handle unhandled exceptions in background tasks."""
        exception = context.get('exception')
        if exception and isinstance(exception, AttributeError):
            msg = str(exception)
            if "'NoneType' object has no attribute 'media'" in msg:
                # This is the known intermittent error - log as warning and set flag
                _builtin_print(f"WARNING: Intermittent connection error detected (background task): {msg}")
                _builtin_print("         This usually indicates a stale connection. Retrying...")
                connection_error_detected["value"] = True
                return  # Don't propagate, we'll handle via retry logic
        # For other exceptions, use default handler
        loop.default_exception_handler(context)

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(exception_handler)

    conn = None
    max_attempts = 3
    last_error = None

    for attempt in range(1, max_attempts + 1):
        conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
        try:
            print(f"Connecting (attempt {attempt}/{max_attempts}, timeout: 60s)...")
            # Reset flag before connecting
            connection_error_detected["value"] = False
            
            # Wrap connect() with error monitoring
            async def connect_with_monitoring():
                connect_task = asyncio.create_task(conn.connect())
                # Monitor for errors while connecting
                while not connect_task.done():
                    if connection_error_detected["value"]:
                        connect_task.cancel()
                        raise RuntimeError("Background task error detected during connect - stale connection")
                    await asyncio.sleep(0.05)  # Check every 50ms
                return await connect_task
            
            await asyncio.wait_for(connect_with_monitoring(), timeout=60.0)
            
            # IMMEDIATELY check if error was detected during connect
            if connection_error_detected["value"]:
                raise RuntimeError("Background task error detected during connect - stale connection")

            # Quick validation - check connection state with aggressive timeout
            pc = getattr(conn, "pc", None)
            if not pc:
                raise RuntimeError("Peer connection not created")
            
            # Poll for connected state, but bail immediately if error flag is set
            start_time = asyncio.get_event_loop().time()
            while True:
                # Check error flag FIRST - highest priority
                if connection_error_detected["value"]:
                    raise RuntimeError("Background task error detected - stale connection")
                
                # Check connection state
                state = getattr(pc, "connectionState", None)
                if state == "connected":
                    # Verify we have remote description
                    if pc.remoteDescription:
                        break  # Success!
                    else:
                        raise RuntimeError("Connected but no remote description")
                
                if state in {"failed", "disconnected", "closed"}:
                    raise RuntimeError(f"Connection state is {state} - cannot proceed")
                
                # Timeout after 2 seconds
                if asyncio.get_event_loop().time() - start_time > 2.0:
                    raise RuntimeError("Connection state not progressing to 'connected' - possible stale connection")
                
                await asyncio.sleep(0.05)  # Check every 50ms

            print("\n✓ Connection established!\n")
            last_error = None
            break
        except asyncio.TimeoutError as exc:
            print("ERROR: Connection timed out after 60s")
            last_error = exc
        except (RuntimeError, AttributeError) as exc:
            # Catch the specific error pattern
            if "'NoneType' object has no attribute 'media'" in str(exc):
                print(f"WARNING: Intermittent connection error (attempt {attempt}): stale connection detected")
                print("         This usually means another connection is still active. Retrying...")
            else:
                print(f"ERROR: Connection failed (attempt {attempt}): {exc}")
            last_error = exc
        except Exception as exc:
            print(f"ERROR: Connection failed (attempt {attempt}): {exc}")
            last_error = exc
        finally:
            if last_error is not None:
                await _safe_disconnect(conn)
                # Reset error flag for next attempt
                connection_error_detected["value"] = False

        if attempt < max_attempts:
            print("Retrying connection...")
            await asyncio.sleep(1.0)
    else:
        # Exhausted retries
        if last_error:
            print(f"Failed to connect after {max_attempts} attempts: {last_error}")
        else:
            print(f"Failed to connect after {max_attempts} attempts.")
        sys.exit(1)

    # Get current state
    print("Checking current robot state...")
    state = await get_robot_state(conn)
    
    if state is None:
        print("Could not determine current state. Attempting to stand up...")
        try:
            response = await conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["SPORT_MOD"],
                {"api_id": SPORT_CMD["StandUp"]}
            )
            status = response.get('data', {}).get('header', {}).get('status', {})
            print(f"StandUp command sent. Response: {status}")
            print("Waiting 5 seconds...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Error sending StandUp command: {e}")
            import traceback
            traceback.print_exc()
    else:
        body_height = state.get('body_height', None)
        is_standing = state['is_standing']
        is_sitting = state['is_sitting']
        
        print(f"\nCurrent state - Body Height: {body_height}, Standing: {is_standing}, Crouched: {is_sitting}")
        
        # Toggle based on current state
        if is_standing:
            print("\n>>> Robot is standing. Switching to crouch mode...")
            try:
                response = await conn.datachannel.pub_sub.publish_request_new(
                    RTC_TOPIC["SPORT_MOD"],
                    {"api_id": SPORT_CMD["StandDown"]}
                )
                status = response.get('data', {}).get('header', {}).get('status', {})
                print(f"✓ StandDown (crouch) command sent. Response: {status}")
                print("Waiting 5 seconds for robot to crouch...")
                await asyncio.sleep(5)
                
                # Verify the state changed
                print("\nVerifying state change...")
                new_state = await get_robot_state(conn)
                if new_state:
                    print(f"New state - Body Height: {new_state.get('body_height', 'N/A')}, Standing: {new_state['is_standing']}, Crouched: {new_state['is_sitting']}")
            except Exception as e:
                print(f"✗ Sit command failed: {e}")
                import traceback
                traceback.print_exc()
        elif is_sitting:
            print("\n>>> Robot is crouched. Standing up...")
            try:
                response = await conn.datachannel.pub_sub.publish_request_new(
                    RTC_TOPIC["SPORT_MOD"],
                    {"api_id": SPORT_CMD["StandUp"]}
                )
                status = response.get('data', {}).get('header', {}).get('status', {})
                print(f"✓ StandUp command sent. Response: {status}")
                print("Waiting 5 seconds for robot to stand...")
                await asyncio.sleep(5)
                
                # Verify the state changed
                print("\nVerifying state change...")
                new_state = await get_robot_state(conn)
                if new_state:
                    print(f"New state - Body Height: {new_state.get('body_height', 'N/A')}, Standing: {new_state['is_standing']}, Crouched: {new_state['is_sitting']}")
            except Exception as e:
                print(f"✗ StandUp command failed: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"\n>>> Unknown state. Attempting to stand up...")
            try:
                response = await conn.datachannel.pub_sub.publish_request_new(
                    RTC_TOPIC["SPORT_MOD"],
                    {"api_id": SPORT_CMD["StandUp"]}
                )
                status = response.get('data', {}).get('header', {}).get('status', {})
                print(f"StandUp command sent. Response: {status}")
                print("Waiting 5 seconds...")
                await asyncio.sleep(5)
            except Exception as e:
                print(f"Error sending StandUp command: {e}")
                import traceback
                traceback.print_exc()

    await _safe_disconnect(conn)
    print("\nDone.")


async def _safe_disconnect(conn):
    """Safely disconnect from the Go2."""
    try:
        if getattr(conn, 'isConnected', False):
            await conn.disconnect()
    except Exception:
        pass


async def _wait_for_peer_connected(conn):
    """Wait for the RTCPeerConnection to reach the 'connected' state."""
    pc = getattr(conn, "pc", None)
    if not pc:
        raise RuntimeError("Peer connection not created.")

    while True:
        state = getattr(pc, "connectionState", None)
        if state == "connected":
            return
        if state in {"failed", "disconnected", "closed"}:
            raise RuntimeError(f"Peer connection state is '{state}'.")
        await asyncio.sleep(0.2)


async def _ensure_remote_description(conn):
    """Wait until the RTCPeerConnection has a remote description."""
    pc = getattr(conn, "pc", None)
    if not pc:
        raise RuntimeError("Peer connection not created.")

    while getattr(pc, "remoteDescription", None) is None:
        state = getattr(pc, "connectionState", None)
        if state in {"failed", "disconnected", "closed"}:
            raise RuntimeError(f"Remote description missing (state: {state}).")
        await asyncio.sleep(0.2)
    return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
