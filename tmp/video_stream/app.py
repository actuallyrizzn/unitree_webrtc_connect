"""
Go2 Video Stream Flask App
==========================

A Flask-based web server for real-time video streaming from the Unitree Go2 robot.
Connects to the robot via WebRTC and streams video frames to the browser.

Usage:
    python tmp/video_stream/app.py

Then open http://127.0.0.1:8080/ in your browser.
"""

import builtins as _builtins
import re as _re
import sys
import os
import signal

# Remove emojis from output for Windows terminal compatibility
_builtin_print = _builtins.print
emoji_pattern = _re.compile(r'[\U0001F300-\U0001FAD6\U0001FAE0-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U000025A0-\U000026FF]+', flags=_re.UNICODE)

def _no_emoji_print(*args, **kwargs):
    cleaned_args = []
    for a in args:
        s = str(a)
        s = s.encode('cp1252', errors='ignore').decode('cp1252')
        cleaned_args.append(s)
    return _builtin_print(*cleaned_args, **kwargs)

_builtins.print = _no_emoji_print

# Global shutdown flag
_shutdown_requested = False

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import asyncio
import json
import threading
import time
import base64
import cv2
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO

from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC
import go2_webrtc_driver.util as _util
import go2_webrtc_driver.webrtc_datachannel as _webrtc_datachannel
import go2_webrtc_driver.unitree_auth as _unitree_auth
import go2_webrtc_driver.webrtc_driver as _webrtc_driver_mod
from aiortc import RTCPeerConnection, RTCSessionDescription

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'animus-go2-video-stream-secret'
socketio = SocketIO(
    app,
    async_mode='threading',
    cors_allowed_origins="*",
    ping_timeout=120,
    ping_interval=25,
    max_http_buffer_size=10000000,  # 10MB buffer
    engineio_logger=False,
    logger=False
)

# Video frame storage
latest_frame = None
frame_lock = threading.Lock()

# Stats tracking
stats = {
    'frames_received': 0,
    'frames_sent': 0,
    'last_frame_time': None,
    'start_time': None,
    'connection_state': 'disconnected'
}

# === Monkey patches for WebRTC connection ===

def _patched_print_status(status_type, status_message):
    current_time = time.strftime("%H:%M:%S")
    _builtin_print(f"[{current_time}] {status_type}: {status_message}")

_util.print_status = _patched_print_status

# Extended timeout for data channel
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
            _builtin_print(f"Waiting for datachannel readyState={state}")
            last_log = time.time()
        await asyncio.sleep(0.1)
    _builtin_print("Warning: data channel did not report open within 30s; continuing anyway")
    channel = getattr(self, "channel", None)
    if channel and hasattr(channel, "_setReadyState"):
        channel._setReadyState("open")
    self.data_channel_opened = True

_webrtc_datachannel.WebRTCDataChannel.wait_datachannel_open = _patched_wait_datachannel_open

# SDP patches for aiortc/Go2 compatibility
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
            parts = line.split()
            port = parts[1] if len(parts) > 1 else "9"
            lines.append(f"m=application {port} UDP/DTLS/SCTP 5000")
            saw_m_application = True
        elif line.startswith("a=sctp-port"):
            lines.append("a=sctpmap:5000 webrtc-datachannel 65535")
            saw_sctpmap = True
        elif line.startswith("a=sctpmap"):
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
    # Don't rewrite the answer - use it as-is from the Go2
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

# === Video handling ===

async def video_track_handler(track):
    """Read frames from the video track and store them for streaming."""
    global latest_frame

    _builtin_print("Video track handler started")

    try:
        while not _shutdown_requested:
            try:
                frame = await track.recv()
            except Exception as recv_error:
                _builtin_print(f"Error receiving video frame: {recv_error}")
                break

            try:
                img_array = frame.to_ndarray(format='bgr24')
                success, buffer = cv2.imencode('.jpg', img_array)
                if not success:
                    continue
                jpeg_bytes = buffer.tobytes()

                with frame_lock:
                    latest_frame = jpeg_bytes
                    stats['frames_received'] += 1
                    stats['last_frame_time'] = time.time()
            except Exception as process_error:
                _builtin_print(f"Error processing video frame: {process_error}")
                continue
    finally:
        _builtin_print("Video track handler exiting")

# === WebRTC connection management ===

async def run_webrtc_connection():
    """Run the WebRTC connection in a separate thread."""
    global _shutdown_requested

    while not _shutdown_requested:
        conn = None
        try:
            _builtin_print("Starting WebRTC connection...")

            # Create connection
            conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)

            # Connect
            await asyncio.wait_for(conn.connect(), timeout=60.0)
            _builtin_print("✓ Connection established!")

            stats['connection_state'] = 'connected'
            stats['start_time'] = time.time()

            # Register video track handler and enable video
            if getattr(conn, "video", None):
                conn.video.add_track_callback(video_track_handler)
                _builtin_print("✓ Video track handler registered")
                _builtin_print("Enabling video channel...")
                conn.video.switchVideoChannel(True)
            else:
                _builtin_print("Warning: Video channel not available on connection")

            # Keepalive loop
            last_keepalive = time.time()
            while not _shutdown_requested:
                current_time = time.time()

                # Send keepalive every 20 seconds
                if current_time - last_keepalive > 20.0:
                    try:
                        await conn.datachannel.disableTrafficSaving(True)
                        last_keepalive = current_time
                    except Exception as e:
                        _builtin_print(f"Keepalive failed: {e}")

                # Check connection state
                if hasattr(conn, 'pc') and conn.pc:
                    pc_state = getattr(conn.pc, 'connectionState', 'unknown')
                    if pc_state != 'connected':
                        _builtin_print(f"Peer connection state: {pc_state}")
                        break

                # Check for message timeout (10 seconds)
                if stats['last_frame_time'] and (current_time - stats['last_frame_time'] > 10.0):
                    _builtin_print("No video frames received for 10+ seconds - connection may be dead")
                    break

                await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            _builtin_print("WebRTC connection cancelled")
            break
        except Exception as e:
            _builtin_print(f"WebRTC connection failed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            stats['connection_state'] = 'disconnected'
            if conn:
                try:
                    await asyncio.wait_for(conn.disconnect(), timeout=5.0)
                except Exception:
                    pass

            if not _shutdown_requested:
                _builtin_print("Retrying WebRTC connection in 5 seconds...")
                await asyncio.sleep(5.0)

    _builtin_print("WebRTC connection thread exiting")

def start_webrtc_thread():
    """Start WebRTC connection in a background thread."""
    def run_async():
        asyncio.run(run_webrtc_connection())

    thread = threading.Thread(target=run_async, daemon=True)
    thread.start()
    return thread

# === Flask routes ===

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    _builtin_print("Client connected")

@socketio.on('disconnect')
def handle_disconnect():
    _builtin_print("Client disconnected")

@socketio.on('get_stats')
def handle_get_stats():
    """Send current stats to client."""
    current_time = time.time()
    uptime = current_time - (stats['start_time'] or current_time)

    socketio.emit('stats', {
        'connection_state': stats['connection_state'],
        'frames_received': stats['frames_received'],
        'frames_sent': stats['frames_sent'],
        'uptime': uptime,
        'fps': stats['frames_received'] / max(uptime, 1.0)
    })

def video_stream_thread():
    """Thread to periodically send video frames to connected clients."""
    while not _shutdown_requested:
        try:
            with frame_lock:
                frame_data = latest_frame
                if frame_data:
                    # Convert to base64 for transmission
                    b64_frame = base64.b64encode(frame_data).decode('ascii')
                    socketio.emit('video_frame', {'data': b64_frame})
                    stats['frames_sent'] += 1

            time.sleep(1.0 / 10.0)  # 10 FPS max

        except Exception as e:
            _builtin_print(f"Video streaming error: {e}")
            time.sleep(1.0)

# === Signal handling ===

def signal_handler(signum, frame):
    """Handle shutdown signals."""
    global _shutdown_requested
    _builtin_print(f"Received signal {signum}, shutting down...")
    _shutdown_requested = True

    # Give threads time to shut down
    def force_exit():
        time.sleep(2.0)
        os._exit(0)

    thread = threading.Thread(target=force_exit, daemon=True)
    thread.start()

if __name__ == "__main__":
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start WebRTC connection thread
    webrtc_thread = start_webrtc_thread()

    # Start video streaming thread
    stream_thread = threading.Thread(target=video_stream_thread, daemon=True)
    stream_thread.start()

    # Start Flask app
    try:
        _builtin_print("Starting Flask app on http://127.0.0.1:8080/")
        socketio.run(app, host='127.0.0.1', port=8080, debug=True, use_reloader=False)
    except KeyboardInterrupt:
        _builtin_print("Flask app interrupted")
    finally:
        _shutdown_requested = True
