"""
Go2 Audio Stream Flask App
==========================

A Flask-based web server for real-time audio streaming from the Unitree Go2 robot.
Connects to the robot via WebRTC and streams audio chunks to the browser for playback.

Usage:
    python tmp/audio_stream/app.py

Then open http://127.0.0.1:8090/ in your browser.
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
from collections import deque

import numpy as np
from flask import Flask, render_template
from flask_socketio import SocketIO

from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
import go2_webrtc_driver.util as _util
import go2_webrtc_driver.webrtc_datachannel as _webrtc_datachannel
import go2_webrtc_driver.unitree_auth as _unitree_auth
import go2_webrtc_driver.webrtc_driver as _webrtc_driver_mod
from aiortc import RTCPeerConnection, RTCSessionDescription

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'animus-go2-audio-stream-secret'
socketio = SocketIO(
    app,
    async_mode='threading',
    cors_allowed_origins="*",
    ping_timeout=120,
    ping_interval=25,
    max_http_buffer_size=1000000,
    engineio_logger=False,
    logger=False
)

# Audio chunk storage
audio_queue = deque(maxlen=200)
audio_queue_lock = threading.Lock()

# Stats tracking
stats = {
    'chunks_received': 0,
    'chunks_sent': 0,
    'last_chunk_time': None,
    'start_time': None,
    'connection_state': 'disconnected',
    'sample_rate': None,
    'channels': None
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

# === Audio handling ===

async def audio_track_handler(track):
    """Read audio frames from the track and enqueue them for streaming."""
    global stats

    _builtin_print("Audio track handler started")

    try:
        while not _shutdown_requested:
            try:
                frame = await track.recv()
            except Exception as recv_error:
                _builtin_print(f"Error receiving audio frame: {recv_error}")
                break

            try:
                samples = frame.to_ndarray()

                if samples.ndim == 1:
                    interleaved = samples
                    channels = 1
                elif samples.ndim == 2:
                    channels = samples.shape[0]
                    interleaved = samples.T.reshape(-1)
                else:
                    continue

                pcm16 = interleaved.astype(np.int16, copy=False).tobytes()
                sample_rate = frame.sample_rate

                with audio_queue_lock:
                    audio_queue.append((pcm16, sample_rate, channels))

                stats['chunks_received'] += 1
                stats['last_chunk_time'] = time.time()
                stats['sample_rate'] = sample_rate
                stats['channels'] = channels

            except Exception as process_error:
                _builtin_print(f"Error processing audio frame: {process_error}")
                continue
    finally:
        _builtin_print("Audio track handler exiting")

# === WebRTC connection management ===

async def run_webrtc_connection():
    """Run the WebRTC connection in a separate thread."""
    global _shutdown_requested

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

    while not _shutdown_requested:
        conn = None
        max_attempts = 3
        last_error = None

        for attempt in range(1, max_attempts + 1):
            if _shutdown_requested:
                break

            try:
                _builtin_print(f"Starting WebRTC connection (attempt {attempt}/{max_attempts})...")

                conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
                
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

                _builtin_print("✓ Connection established!")

                stats['connection_state'] = 'connected'
                stats['start_time'] = time.time()

                if getattr(conn, "audio", None):
                    conn.audio.add_track_callback(audio_track_handler)
                    _builtin_print("✓ Audio track handler registered")
                    _builtin_print("Enabling audio channel...")
                    conn.audio.switchAudioChannel(True)
                else:
                    _builtin_print("Warning: Audio channel not available on connection")

                last_error = None
                break  # Success, exit retry loop

            except asyncio.TimeoutError as exc:
                _builtin_print(f"ERROR: Connection timed out after 60s (attempt {attempt})")
                last_error = exc
            except (RuntimeError, AttributeError) as exc:
                # Catch the specific error pattern
                if "'NoneType' object has no attribute 'media'" in str(exc):
                    _builtin_print(f"WARNING: Intermittent connection error (attempt {attempt}): stale connection detected")
                    _builtin_print("         This usually means another connection is still active. Retrying...")
                else:
                    _builtin_print(f"ERROR: Connection failed (attempt {attempt}): {exc}")
                last_error = exc
            except Exception as exc:
                _builtin_print(f"ERROR: Connection failed (attempt {attempt}): {exc}")
                last_error = exc
            finally:
                if last_error is not None:
                    # Disconnect and reset error flag for next attempt
                    if conn:
                        try:
                            await asyncio.wait_for(conn.disconnect(), timeout=5.0)
                        except Exception:
                            pass
                    connection_error_detected["value"] = False

            if attempt < max_attempts and not _shutdown_requested:
                _builtin_print("Retrying connection in 1 second...")
                await asyncio.sleep(1.0)

        if last_error is not None:
            # Exhausted retries
            _builtin_print(f"Failed to connect after {max_attempts} attempts. Retrying in 5 seconds...")
            await asyncio.sleep(5.0)
            continue

        # Connection successful - run main loop
        try:

            last_keepalive = time.time()
            while not _shutdown_requested:
                current_time = time.time()

                # Check for background task errors during operation
                if connection_error_detected["value"]:
                    _builtin_print("Background task error detected during operation - reconnecting...")
                    break

                if current_time - last_keepalive > 20.0:
                    try:
                        await conn.datachannel.disableTrafficSaving(True)
                        last_keepalive = current_time
                    except Exception as e:
                        _builtin_print(f"Keepalive failed: {e}")

                if hasattr(conn, 'pc') and conn.pc:
                    pc_state = getattr(conn.pc, 'connectionState', 'unknown')
                    if pc_state != 'connected':
                        _builtin_print(f"Peer connection state: {pc_state}")
                        break

                if stats['last_chunk_time'] and (current_time - stats['last_chunk_time'] > 10.0):
                    _builtin_print("No audio chunks received for 10+ seconds - connection may be dead")
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
        'chunks_received': stats['chunks_received'],
        'chunks_sent': stats['chunks_sent'],
        'uptime': uptime,
        'sample_rate': stats['sample_rate'],
        'channels': stats['channels']
    })

def audio_stream_thread():
    """Thread to send audio chunks to connected clients."""
    while not _shutdown_requested:
        try:
            chunk = None
            with audio_queue_lock:
                if audio_queue:
                    chunk = audio_queue.popleft()

            if chunk:
                pcm_bytes, sample_rate, channels = chunk
                if sample_rate and channels:
                    duration = len(pcm_bytes) / (sample_rate * channels * 2)
                else:
                    duration = 0.02

                b64_audio = base64.b64encode(pcm_bytes).decode('ascii')
                socketio.emit('audio_chunk', {
                    'data': b64_audio,
                    'sample_rate': sample_rate,
                    'channels': channels
                })

                stats['chunks_sent'] += 1

                time.sleep(max(duration, 0.01))
            else:
                time.sleep(0.01)
        except Exception as e:
            _builtin_print(f"Audio streaming error: {e}")
            time.sleep(0.5)

# === Signal handling ===

def signal_handler(signum, frame):
    """Handle shutdown signals."""
    global _shutdown_requested
    _builtin_print(f"Received signal {signum}, shutting down...")
    _shutdown_requested = True

    def force_exit():
        time.sleep(2.0)
        os._exit(0)

    thread = threading.Thread(target=force_exit, daemon=True)
    thread.start()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    webrtc_thread = start_webrtc_thread()

    stream_thread = threading.Thread(target=audio_stream_thread, daemon=True)
    stream_thread.start()

    try:
        _builtin_print("Starting Flask app on http://127.0.0.1:8090/")
        socketio.run(app, host='127.0.0.1', port=8090, debug=True, use_reloader=False)
    except KeyboardInterrupt:
        _builtin_print("Flask app interrupted")
    finally:
        _shutdown_requested = True
