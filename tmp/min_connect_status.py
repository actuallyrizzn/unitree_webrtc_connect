"""
Minimal Go2 WebRTC Connection Test Script
==========================================

This script demonstrates a basic working WebRTC connection to the Unitree Go2 robot
in AP mode, including:
- Connection establishment
- Data channel validation
- Sending a simple command (wave)

Usage:
    python tmp/min_connect_status.py

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
import logging
import sys
import time
import json

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
            lines.append("m=application 9 DTLS/SCTP 5000")
            saw_m_application = True
        elif line.startswith("a=sctp-port"):
            lines.append("a=sctpmap:5000 webrtc-datachannel 65535")
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
        # Remove sha-384 and sha-512 fingerprints (forces Go2 to use sha-256)
        filtered = [
            line for line in offer_sdp.splitlines()
            if not line.startswith("a=fingerprint:sha-384")
            and not line.startswith("a=fingerprint:sha-512")
        ]
        payload["sdp"] = _rewrite_sdp_to_legacy("\r\n".join(filtered) + "\r\n")
        sdp = json.dumps(payload)
    except Exception:
        pass
    result = _orig_send_local(ip, sdp)
    if result:
        try:
            # Rewrite remote answer to legacy format
            answer = json.loads(result)
            answer["sdp"] = _rewrite_sdp_to_legacy(answer.get("sdp", ""))
            result = json.dumps(answer)
        except Exception:
            pass
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


async def main():
    """Main test function."""
    logging.basicConfig(level=logging.INFO)

    print("============================================================")
    print("GO2 MINIMAL CONNECT + WAVE TEST")
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
        _dump_connection_state(conn)
        await _safe_disconnect(conn)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Connection failed: {e}")
        _dump_connection_state(conn)
        await _safe_disconnect(conn)
        raise

    _dump_connection_state(conn)

    print("Attempting to send wave command...")
    try:
        response = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"],
            {"api_id": SPORT_CMD["Hello"]}
        )
        print(f"✓ Wave command successful! Response: {response.get('data', {}).get('header', {}).get('status', {})}\n")
    except Exception as e:
        print(f"✗ Wave command failed: {e}")
        import traceback
        traceback.print_exc()

    await _safe_disconnect(conn)
    print("Done.")


async def _safe_disconnect(conn):
    """Safely disconnect from the Go2."""
    try:
        if getattr(conn, 'isConnected', False):
            await conn.disconnect()
    except Exception:
        pass


def _dump_connection_state(conn):
    """Print connection state for debugging."""
    try:
        pc = getattr(conn, "pc", None)
        if not pc:
            return
        
        print("Connection State:")
        print(f"  Peer Connection: {getattr(pc, 'connectionState', 'unknown')}")
        print(f"  ICE Connection: {getattr(pc, 'iceConnectionState', 'unknown')}")
        
        sctp = getattr(pc, "sctp", None)
        if sctp:
            print(f"  SCTP Transport: {getattr(sctp.transport, 'state', 'unknown')}")
            print(f"  SCTP Association: {getattr(sctp, '_association_state', 'unknown')}")
        
        # Show SDP application section
        ld = getattr(pc, "localDescription", None)
        rd = getattr(pc, "remoteDescription", None)
        if ld:
            print("\nLocal SDP (application section):")
            for line in ld.sdp.splitlines():
                if line.startswith("m=application") or "sctp" in line.lower():
                    print(f"    {line}")
        if rd:
            print("Remote SDP (application section):")
            for line in rd.sdp.splitlines():
                if line.startswith("m=application") or "sctp" in line.lower():
                    print(f"    {line}")
        print()
    except Exception as e:
        print(f"Could not dump connection state: {e}\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
