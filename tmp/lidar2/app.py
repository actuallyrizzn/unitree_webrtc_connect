"""
Animus Go2 LiDAR Visualization Server
=====================================

A Flask-based web server for real-time 3D visualization of Unitree Go2 LiDAR data.
Connects to the robot via WebRTC and streams point cloud data to the browser.

Key improvements from Rerun version:
- Better connection monitoring (pc.connectionState checks)
- Message timeout detection
- Active keepalive every 20 seconds
- Graceful shutdown handling
- Improved error handling

Usage:
    python tmp/lidar2/app.py
    
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
import csv
import argparse
import threading
import time
import numpy as np
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO

from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC
import go2_webrtc_driver.util as _util
import go2_webrtc_driver.webrtc_datachannel as _webrtc_datachannel
import go2_webrtc_driver.unitree_auth as _unitree_auth
import go2_webrtc_driver.webrtc_driver as _webrtc_driver_mod
from aiortc import RTCPeerConnection, RTCSessionDescription

# Increase CSV field size limit for Windows
try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    csv.field_size_limit(2147483647)  # 2^31 - 1

# Flask app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'animus-go2-lidar-viz-secret'
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

# LiDAR processing parameters (in radians)
ROTATE_X_ANGLE = np.pi / 2  # 90 degrees
ROTATE_Z_ANGLE = np.pi       # 180 degrees
MIN_Y_VALUE = 0
MAX_Y_VALUE = 100

# Stats tracking
stats = {
    'messages_received': 0,
    'points_sent': 0,
    'last_message_time': None,
    'start_time': None,
    'frame_counter': 0,
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

# === LiDAR processing functions ===

def rotate_points(points, x_angle, z_angle):
    """Rotate point cloud by given angles (in radians)."""
    if len(points) == 0:
        return points
    
    rotation_matrix_x = np.array([
        [1, 0, 0],
        [0, np.cos(x_angle), -np.sin(x_angle)],
        [0, np.sin(x_angle), np.cos(x_angle)]
    ])
    
    rotation_matrix_z = np.array([
        [np.cos(z_angle), -np.sin(z_angle), 0],
        [np.sin(z_angle), np.cos(z_angle), 0],
        [0, 0, 1]
    ])
    
    points = points @ rotation_matrix_x.T
    points = points @ rotation_matrix_z.T
    return points

# === WebRTC Connection ===

async def lidar_webrtc_connection():
    """Establish WebRTC connection and subscribe to LiDAR data."""
    _builtin_print("=" * 60)
    _builtin_print("Initializing Go2 LiDAR WebRTC Connection")
    _builtin_print("=" * 60)
    
    conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
    
    try:
        _builtin_print("Connecting to Go2 (timeout: 60s)...")
        # Check shutdown flag before attempting connection
        if _shutdown_requested:
            _builtin_print("Shutdown requested before connection attempt")
            raise ConnectionError("Shutdown requested")
        
        await asyncio.wait_for(conn.connect(), timeout=60.0)
        
        # Check again after connection (in case shutdown happened during connect)
        if _shutdown_requested:
            _builtin_print("Shutdown requested during connection")
            raise ConnectionError("Shutdown requested")
        
        _builtin_print("Connected to WebRTC successfully!")
    except asyncio.TimeoutError:
        if _shutdown_requested:
            _builtin_print("Connection timeout (shutdown was requested)")
            raise ConnectionError("Shutdown requested")
        _builtin_print("ERROR: Connection timed out after 60s")
        raise
    except Exception as e:
        if _shutdown_requested:
            _builtin_print(f"Connection failed (shutdown was requested): {e}")
            raise ConnectionError("Shutdown requested")
        _builtin_print(f"ERROR: Failed to connect: {e}")
        import traceback
        traceback.print_exc()
        raise
    
    # Disable traffic saving mode (keepalive)
    try:
        await conn.datachannel.disableTrafficSaving(True)
        _builtin_print("Traffic saving disabled (keepalive enabled)")
    except Exception as e:
        _builtin_print(f"WARNING: Could not disable traffic saving: {e}")
    
    # Enable LiDAR
    conn.datachannel.pub_sub.publish_without_callback("rt/utlidar/switch", "on")
    _builtin_print("LiDAR sensor enabled")
    
    # Initialize stats
    if not stats['start_time']:
        stats['start_time'] = time.time()
    
    # LiDAR message handler
    async def lidar_callback_task(message):
        """Process incoming LiDAR messages and emit to browser."""
        try:
            # Check shutdown flag first
            if _shutdown_requested:
                return
            
            stats['messages_received'] += 1
            stats['last_message_time'] = time.time()
            
            if not isinstance(message, dict) or "data" not in message:
                return
            
            data = message["data"]
            if not isinstance(data, dict):
                return
            
            inner_data = data.get("data", {})
            if not isinstance(inner_data, dict):
                return
                
            positions = inner_data.get("positions", [])
            if positions is None or len(positions) == 0:
                return
            
            # Convert to 3D points
            points = np.array([positions[i:i+3] for i in range(0, len(positions), 3)], dtype=np.float32)
            total_points = len(points)
            
            if total_points == 0:
                return
            
            # Apply rotation
            points = rotate_points(points, ROTATE_X_ANGLE, ROTATE_Z_ANGLE)
            
            # Filter by Y value (height)
            filtered_points = points[(points[:, 1] >= MIN_Y_VALUE) & (points[:, 1] <= MAX_Y_VALUE)]
            
            # Log every 50 messages
            if stats['messages_received'] % 50 == 0:
                elapsed = time.time() - stats['start_time']
                msg_rate = stats['messages_received'] / elapsed
                _builtin_print(f"Message {stats['messages_received']}: {total_points} pts -> {len(filtered_points)} filtered ({msg_rate:.1f} msg/s)")
            
            if len(filtered_points) == 0:
                return
            
            # Downsample for visualization if needed
            viz_points = filtered_points
            if len(filtered_points) > 15000:
                step = len(filtered_points) // 15000
                viz_points = filtered_points[::step]
            
            # Calculate center and offset
            center = np.mean(viz_points, axis=0)
            offset_points = viz_points - center
            
            # Calculate distances for coloring
            distances = np.linalg.norm(offset_points, axis=1)
            
            # Emit to browser using BINARY format for efficiency
            try:
                points_binary = offset_points.astype(np.float32).tobytes()
                distances_binary = distances.astype(np.float32).tobytes()
                
                metadata = {
                    "point_count": len(viz_points),
                    "center": {"x": float(center[0]), "y": float(center[1]), "z": float(center[2])},
                    "stats": {
                        "total_received": total_points,
                        "after_filter": len(viz_points),
                        "message_count": stats['messages_received']
                    },
                    "timestamp": time.time()
                }
                
                socketio.emit("lidar_data_binary", {
                    "points": points_binary,
                    "distances": distances_binary,
                    "metadata": metadata
                })
                
                stats['points_sent'] += len(viz_points)
            except Exception as emit_err:
                _builtin_print(f"ERROR emitting socketio event: {emit_err}")
                
        except Exception as e:
            _builtin_print(f"ERROR in LIDAR callback: {e}")
            import traceback
            traceback.print_exc()
    
    def lidar_message_handler(message):
        asyncio.create_task(lidar_callback_task(message))
    
    conn.datachannel.pub_sub.subscribe(
        "rt/utlidar/voxel_map_compressed",
        lidar_message_handler
    )
    _builtin_print("Subscribed to rt/utlidar/voxel_map_compressed")
    _builtin_print("View visualization at http://127.0.0.1:8080/")
    
    # Keep connection alive with monitoring and active keepalive
    connection_start_time = asyncio.get_event_loop().time()
    last_keepalive = time.time()
    try:
        while not _shutdown_requested:
            await asyncio.sleep(1)  # Check more frequently for shutdown
            
            # Check connection status with protection against hanging
            try:
                is_connected = conn.isConnected
                pc_state = conn.pc.connectionState if hasattr(conn, 'pc') and conn.pc else 'unknown'
            except Exception as e:
                _builtin_print(f"WARNING: Error checking connection status: {e}")
                is_connected = False
                pc_state = 'error'
            
            # Also check if we've stopped receiving messages (connection might be dead)
            current_time = time.time()
            if stats.get('last_message_time'):
                time_since_last_msg = current_time - stats['last_message_time']
                if time_since_last_msg > 10:  # No messages for 10 seconds = dead connection
                    _builtin_print(f"WARNING: No LiDAR messages for {time_since_last_msg:.1f}s")
                    is_connected = False
            
            if not is_connected or pc_state in ['closed', 'failed', 'disconnected']:
                uptime = asyncio.get_event_loop().time() - connection_start_time
                _builtin_print(f"Connection lost after {uptime:.1f}s uptime (state: {pc_state})")
                raise ConnectionError("WebRTC connection lost")
            
            # Send active keepalive every 20 seconds
            if current_time - last_keepalive >= 20:
                try:
                    await conn.datachannel.disableTrafficSaving(True)
                    last_keepalive = current_time
                    uptime = asyncio.get_event_loop().time() - connection_start_time
                    _builtin_print(f"Sent keepalive at {uptime:.0f}s")
                except Exception as e:
                    _builtin_print(f"WARNING: Keepalive failed: {e}")
            
            # Log uptime every 30 seconds
            uptime = asyncio.get_event_loop().time() - connection_start_time
            if int(uptime) % 30 == 0 and uptime > 0:
                _builtin_print(f"Connection stable: {uptime:.0f}s, {stats['messages_received']} messages")
        
        # Shutdown requested
        if _shutdown_requested:
            _builtin_print("Shutdown flag detected, exiting connection loop...")
                
    except asyncio.CancelledError:
        _builtin_print("Connection cancelled")
        raise
    finally:
        try:
            _builtin_print("Closing WebRTC connection...")
            # Try to disconnect with short timeout
            try:
                await asyncio.wait_for(conn.disconnect(), timeout=2.0)
                _builtin_print("WebRTC connection closed")
            except asyncio.TimeoutError:
                _builtin_print("WARNING: Disconnect timed out after 2s (forcing cleanup)")
                # Force close by setting connection state
                try:
                    if hasattr(conn, 'pc') and conn.pc:
                        await asyncio.wait_for(conn.pc.close(), timeout=1.0)
                except:
                    pass
        except Exception as e:
            _builtin_print(f"WARNING: Error during disconnect: {e}")

# === Flask Routes ===

@app.route("/")
def index():
    """Serve the main visualization page."""
    return render_template("index.html")

@app.route("/stats")
def get_stats():
    """Get current statistics."""
    return jsonify(stats)

@socketio.on("connect")
def handle_connect():
    _builtin_print("Browser client connected")

@socketio.on("disconnect")
def handle_disconnect():
    _builtin_print("Browser client disconnected")

def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully"""
    global _shutdown_requested
    if not _shutdown_requested:
        _builtin_print("\n" + "="*60)
        _builtin_print("SHUTDOWN REQUESTED - Cleaning up...")
        _builtin_print("="*60)
        _shutdown_requested = True

def start_webrtc():
    """Run WebRTC connection with auto-reconnect in a separate asyncio loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    retry_count = 0
    consecutive_fast_failures = 0
    
    while not _shutdown_requested:
        connection_start = time.time()
        try:
            _builtin_print(f"\n{'='*60}")
            _builtin_print(f"WebRTC Connection Attempt #{retry_count + 1}")
            _builtin_print(f"{'='*60}")
            loop.run_until_complete(lidar_webrtc_connection())
            retry_count = 0
            consecutive_fast_failures = 0
        except KeyboardInterrupt:
            _builtin_print("\nKeyboard interrupt - shutting down gracefully...")
            break
        except Exception as e:
            # If shutdown was requested, exit immediately
            if _shutdown_requested or "Shutdown requested" in str(e):
                _builtin_print("Shutdown detected in exception handler - exiting")
                break
            
            connection_duration = time.time() - connection_start
            retry_count += 1
            
            if connection_duration < 10:
                consecutive_fast_failures += 1
            else:
                consecutive_fast_failures = 0
            
            if consecutive_fast_failures > 3:
                reconnect_delay = min(30, 10 * consecutive_fast_failures)
                _builtin_print(f"WARNING: {consecutive_fast_failures} fast failures detected")
            else:
                reconnect_delay = min(retry_count * 2, 30)
            
            _builtin_print(f"Connection failed after {connection_duration:.1f}s: {e}")
            _builtin_print(f"Reconnecting in {reconnect_delay}s... (attempt #{retry_count})")
            
            # Sleep in small increments so we can check shutdown flag
            for _ in range(int(reconnect_delay)):
                if _shutdown_requested:
                    break
                time.sleep(1)
    
    _builtin_print("Closing event loop...")
    loop.close()
    _builtin_print("WebRTC thread exited")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Animus Go2 LiDAR Visualization Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()
    
    # Set up signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    
    _builtin_print("=" * 60)
    _builtin_print("Animus Go2 LiDAR Visualization Server")
    _builtin_print("=" * 60)
    _builtin_print(f"\nServer will start at http://{args.host}:{args.port}/")
    _builtin_print("Make sure the Unitree Go2 mobile app is CLOSED\n")
    
    # Only start WebRTC in the reloader child process
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        _builtin_print("Starting WebRTC connection thread...")
        webrtc_thread = threading.Thread(target=start_webrtc, daemon=True)
        webrtc_thread.start()
    else:
        _builtin_print("Parent reloader process - WebRTC will start in child process")
    
    # Start Flask server with auto-reload enabled
    try:
        socketio.run(app, host=args.host, port=args.port, debug=True, use_reloader=True, reloader_type='stat')
    except KeyboardInterrupt:
        _builtin_print("\nShutdown complete")
    finally:
        # Force exit to terminate any lingering threads
        _builtin_print("Forcing process exit...")
        os._exit(0)

