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
    """Get the current robot state (standing/sitting) by subscribing to sport mode state."""
    state_received = asyncio.Event()
    current_mode = None
    full_message = None
    
    def sportmodestate_callback(message):
        nonlocal current_mode, full_message
        try:
            # The message structure is: message['data'] contains the sport mode state
            state_data = message.get('data', {})
            mode = state_data.get('mode', None)
            body_height = state_data.get('body_height', None)
            current_mode = mode
            full_message = message
            _builtin_print(f"Received sport mode state: mode={mode}, body_height={body_height}")
            state_received.set()
        except Exception as e:
            _builtin_print(f"Error parsing sport mode state: {e}")
            import traceback
            traceback.print_exc()
            state_received.set()
    
    try:
        # Subscribe to sport mode state
        conn.datachannel.pub_sub.subscribe(RTC_TOPIC['LF_SPORT_MOD_STATE'], sportmodestate_callback)
        
        # Give the subscription a moment to register
        await asyncio.sleep(0.5)
        
        # Wait for a state message (with timeout)
        try:
            await asyncio.wait_for(state_received.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            _builtin_print("Warning: Timeout waiting for sport mode state")
            try:
                conn.datachannel.pub_sub.unsubscribe(RTC_TOPIC['LF_SPORT_MOD_STATE'], sportmodestate_callback)
            except:
                pass
            return None
        
        # Unsubscribe
        try:
            conn.datachannel.pub_sub.unsubscribe(RTC_TOPIC['LF_SPORT_MOD_STATE'], sportmodestate_callback)
        except:
            pass
        
        # Check robot mode and body height
        # From actual data: body_height around 0.24 = sitting, standing would be higher
        # Use body_height as primary indicator
        state_data = full_message.get('data', {}) if full_message else {}
        body_height = state_data.get('body_height', None)
        
        # Based on observed data: body_height ~0.24 = crouched (StandDown)
        # Standing would have higher body_height (likely 0.3+)
        if body_height is not None:
            # Use body_height as primary indicator
            # Crouched is around 0.24, standing is higher (0.3+)
            is_standing = body_height > 0.27  # Standing is higher than crouched
            is_sitting = body_height <= 0.27  # Crouched (StandDown)
        else:
            # Fallback to mode if body_height not available
            # Mode 0 might mean sitting based on observations
            is_standing = current_mode != 0
            is_sitting = current_mode == 0
        
        
        return {
            'mode': current_mode,
            'is_standing': is_standing,
            'is_sitting': is_sitting,
            'full_message': full_message
        }
    except Exception as e:
        _builtin_print(f"Warning: Could not get robot state: {e}")
        import traceback
        traceback.print_exc()
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

    conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)

    try:
        print("Connecting (timeout: 60s)...")
        await asyncio.wait_for(conn.connect(), timeout=60.0)
        print("\n✓ Connection established!\n")
    except asyncio.TimeoutError:
        print("ERROR: Connection timed out after 60s")
        await _safe_disconnect(conn)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Connection failed: {e}")
        await _safe_disconnect(conn)
        raise

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
        mode = state['mode']
        is_standing = state['is_standing']
        is_sitting = state['is_sitting']
        
        print(f"\nCurrent state - Mode: {mode}, Standing: {is_standing}, Sitting: {is_sitting}")
        
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
                    print(f"New state - Mode: {new_state['mode']}, Standing: {new_state['is_standing']}, Sitting: {new_state['is_sitting']}")
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
                    print(f"New state - Mode: {new_state['mode']}, Standing: {new_state['is_standing']}, Sitting: {new_state['is_sitting']}")
            except Exception as e:
                print(f"✗ StandUp command failed: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"\n>>> Unknown state (mode: {mode}). Attempting to stand up...")
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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
