"""
Go2 LiDAR Visualization Server
===============================

A Flask-based web server for real-time 3D visualization of Unitree Go2 LiDAR data.
Connects to the robot via WebRTC and streams point cloud data to the browser.

Usage:
    python tmp/lidar/app.py
    
Then open http://127.0.0.1:8080/ in your browser.
"""

import builtins as _builtins
import re as _re
import sys
import os

# Remove emojis from output for Windows terminal compatibility
_builtin_print = _builtins.print
# Extended emoji pattern to catch all unicode symbols including geometric shapes
emoji_pattern = _re.compile(r'[\U0001F300-\U0001FAD6\U0001FAE0-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U000025A0-\U000026FF]+', flags=_re.UNICODE)

def _no_emoji_print(*args, **kwargs):
    # Convert all args to strings and remove emojis
    cleaned_args = []
    for a in args:
        s = str(a)
        # Remove unicode characters that can't be encoded in cp1252
        s = s.encode('cp1252', errors='ignore').decode('cp1252')
        cleaned_args.append(s)
    return _builtin_print(*cleaned_args, **kwargs)

_builtins.print = _no_emoji_print

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
app.config['SECRET_KEY'] = 'go2-lidar-viz-secret'
socketio = SocketIO(
    app, 
    async_mode='threading',
    cors_allowed_origins="*",
    ping_timeout=120,           # Longer timeout before considering client dead
    ping_interval=25,            # Send keepalive pings every 25s
    max_http_buffer_size=10000000,  # 10MB buffer (default is 1MB)
    engineio_logger=False,       # Reduce logging overhead
    logger=False
)

# LiDAR processing parameters (in radians!)
ROTATE_X_ANGLE = np.pi / 2  # 90 degrees in radians
ROTATE_Z_ANGLE = np.pi       # 180 degrees in radians
# Y-filter (after rotation, Y is "up" - floor to ceiling)
minYValue = 0    # Floor level
maxYValue = 100  # Ceiling level

# Stats tracking
stats = {
    'messages_received': 0,
    'points_sent': 0,
    'last_message_time': None,
    'frame_counter': 0,  # For throttling
}

# === Monkey patches for WebRTC connection ===

def _patched_print_status(status_type, status_message):
    import time
    current_time = time.strftime("%H:%M:%S")
    _builtin_print(f"[{current_time}] {status_type}: {status_message}")

_util.print_status = _patched_print_status

_orig_wait_datachannel_open = _webrtc_datachannel.WebRTCDataChannel.wait_datachannel_open

async def _patched_wait_datachannel_open(self, timeout=5):
    """Extended wait for data channel with better logging."""
    import time
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if getattr(self, "data_channel_opened", False):
            return
        channel = getattr(self, "channel", None)
        state = getattr(channel, "readyState", None)
        if state == "open":
            return
        if int((deadline - time.time())) % 5 == 0:
            _builtin_print(f"Waiting for datachannel readyState={state}")
        await asyncio.sleep(0.1)
    _builtin_print("Warning: data channel did not report open within 30s; continuing anyway")
    channel = getattr(self, "channel", None)
    if channel and hasattr(channel, "_setReadyState"):
        channel._setReadyState("open")
    self.data_channel_opened = True

_webrtc_datachannel.WebRTCDataChannel.wait_datachannel_open = _patched_wait_datachannel_open

_orig_send_local = _unitree_auth.send_sdp_to_local_peer

def _rewrite_sdp_to_legacy(sdp: str) -> str:
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
    try:
        payload = json.loads(sdp)
        offer_sdp = payload.get("sdp", "")
        filtered = [line for line in offer_sdp.splitlines() 
                   if not line.startswith("a=fingerprint:sha-384") 
                   and not line.startswith("a=fingerprint:sha-512")]
        payload["sdp"] = _rewrite_sdp_to_legacy("\r\n".join(filtered) + "\r\n")
        sdp = json.dumps(payload)
    except Exception:
        pass
    result = _orig_send_local(ip, sdp)
    if result:
        try:
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
    """Rotate point cloud by given angles (already in radians)."""
    if len(points) == 0:
        return points
    
    # Rotation matrix around X-axis
    rotation_matrix_x = np.array([
        [1, 0, 0],
        [0, np.cos(x_angle), -np.sin(x_angle)],
        [0, np.sin(x_angle), np.cos(x_angle)]
    ])
    
    # Rotation matrix around Z-axis
    rotation_matrix_z = np.array([
        [np.cos(z_angle), -np.sin(z_angle), 0],
        [np.sin(z_angle), np.cos(z_angle), 0],
        [0, 0, 1]
    ])
    
    # Apply rotations
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
        await asyncio.wait_for(conn.connect(), timeout=60.0)
        _builtin_print("Connected to WebRTC successfully!")
    except Exception as e:
        _builtin_print(f"ERROR: Failed to connect: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Disable traffic saving mode (helps keep connection alive)
    try:
        await conn.datachannel.disableTrafficSaving(True)
        _builtin_print("Traffic saving disabled (keepalive enabled)")
    except Exception as e:
        _builtin_print(f"WARNING: Could not disable traffic saving: {e}")
    
    # Enable LiDAR
    conn.datachannel.pub_sub.publish_without_callback("rt/utlidar/switch", "on")
    _builtin_print("LiDAR sensor enabled")
    
    # LiDAR message handler
    async def lidar_callback_task(message):
        """Process incoming LiDAR messages and emit to browser."""
        import time
        
        try:
            stats['messages_received'] += 1
            stats['last_message_time'] = time.time()
            
            if not isinstance(message, dict) or "data" not in message:
                return
            
            data = message["data"]
            if not isinstance(data, dict):
                return
            
            # Get position data (already decoded format from Go2)
            inner_data = data.get("data", {})
            if not isinstance(inner_data, dict):
                return
                
            positions = inner_data.get("positions", [])
            if positions is None or len(positions) == 0:
                return
            
            # Log raw data to understand the format
            if stats['messages_received'] <= 2:
                _builtin_print(f"DEBUG: positions type={type(positions)}, len={len(positions) if hasattr(positions, '__len__') else 'N/A'}")
                if hasattr(positions, '__len__') and len(positions) > 0:
                    _builtin_print(f"DEBUG: First few positions: {positions[:12]}")
            
            # Convert flat position array to 3D points
            points = np.array([positions[i:i+3] for i in range(0, len(positions), 3)], dtype=np.float32)
            total_points = len(points)
            
            _builtin_print(f"Message {stats['messages_received']}: Raw positions={len(positions)}, Parsed points={total_points}")
            
            if total_points == 0:
                return
            
            # Don't remove duplicates yet - we want to see all the data first
            # points = np.unique(points, axis=0)
            
            # Apply rotation transformation
            points = rotate_points(points, ROTATE_X_ANGLE, ROTATE_Z_ANGLE)
            
            # Filter by Y value (height) 
            filtered_points = points[(points[:, 1] >= minYValue) & (points[:, 1] <= maxYValue)]
            
            _builtin_print(f"  After rotation: {len(points)} points, After Y-filter [{minYValue},{maxYValue}]: {len(filtered_points)} points")
            
            if len(filtered_points) == 0:
                return
            
            # Skip unique() to see all points (can add back later if needed)
            # unique_points = np.unique(filtered_points, axis=0)
            # _builtin_print(f"  After unique: {len(unique_points)} points")
            
            # Use filtered points directly - downsample if too many
            if len(filtered_points) > 15000:
                # Downsample to ~15k points for performance
                step = len(filtered_points) // 15000
                unique_points = filtered_points[::step]
                _builtin_print(f"  Downsampled: {len(filtered_points)} -> {len(unique_points)} points")
            else:
                unique_points = filtered_points
                _builtin_print(f"  Using all {len(unique_points)} filtered points")
            
            # Calculate center
            center_x = float(np.mean(unique_points[:, 0]))
            center_y = float(np.mean(unique_points[:, 1]))
            center_z = float(np.mean(unique_points[:, 2]))
            
            # Offset points by center to position at origin (matches original script)
            offset_points = unique_points - np.array([center_x, center_y, center_z])
            
            # Calculate distances for coloring (from origin after centering)
            distances = np.linalg.norm(offset_points, axis=1)
            
            # Send EVERY frame (no throttling) - let's see what the real bottleneck is
            stats['frame_counter'] += 1
            
            # Emit to browser using BINARY format for efficiency
            try:
                import time
                emit_start = time.perf_counter()
                
                # Convert to binary (much faster and smaller than JSON)
                # Format: float32 arrays
                points_binary = offset_points.astype(np.float32).tobytes()
                distances_binary = distances.astype(np.float32).tobytes()
                
                # Metadata as small JSON
                metadata = {
                    "point_count": len(unique_points),
                    "center": {"x": center_x, "y": center_y, "z": center_z},
                    "stats": {
                        "total_received": total_points,
                        "after_filter": len(unique_points),
                        "message_count": stats['messages_received']
                    },
                    "timestamp": time.time()  # Add timestamp to measure lag
                }
                
                # Send binary data with metadata
                socketio.emit("lidar_data_binary", {
                    "points": points_binary,
                    "distances": distances_binary,
                    "metadata": metadata
                })
                
                emit_time = (time.perf_counter() - emit_start) * 1000
                if stats['messages_received'] % 20 == 0:  # Log every 20 messages
                    payload_kb = (len(points_binary) + len(distances_binary)) / 1024
                    _builtin_print(f"  Emit: {emit_time:.1f}ms, Payload: {payload_kb:.1f}KB")
                    
                stats['points_sent'] += len(unique_points)
            except Exception as emit_err:
                _builtin_print(f"ERROR emitting socketio event: {emit_err}")
                import traceback
                traceback.print_exc()
                
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
    
    # Keep connection alive with monitoring
    connection_start_time = asyncio.get_event_loop().time()
    try:
        while True:
            await asyncio.sleep(5)  # Check every 5 seconds
            
            # Check if still connected
            if not conn.isConnected:
                uptime = asyncio.get_event_loop().time() - connection_start_time
                _builtin_print(f"Connection lost after {uptime:.1f}s uptime")
                raise ConnectionError("WebRTC connection lost")
            
            # Log uptime every 30 seconds
            uptime = asyncio.get_event_loop().time() - connection_start_time
            if int(uptime) % 30 == 0 and uptime > 0:
                _builtin_print(f"Connection stable: {uptime:.0f}s, {stats['messages_received']} messages")
                
    except asyncio.CancelledError:
        _builtin_print("Connection cancelled")
        raise
    finally:
        try:
            _builtin_print("Closing WebRTC connection...")
            # Use timeout to prevent hanging
            await asyncio.wait_for(conn.disconnect(), timeout=5.0)
            _builtin_print("WebRTC connection closed")
        except asyncio.TimeoutError:
            _builtin_print("WARNING: Disconnect timed out after 5s (forcing cleanup)")
        except Exception as e:
            _builtin_print(f"WARNING: Error during disconnect: {e}")
        # Always continue to allow reconnect

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

def start_webrtc():
    """Run WebRTC connection with auto-reconnect in a separate asyncio loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    retry_count = 0
    consecutive_fast_failures = 0
    
    while True:  # Infinite retry loop
        connection_start = time.time()
        try:
            _builtin_print(f"\n{'='*60}")
            _builtin_print(f"WebRTC Connection Attempt #{retry_count + 1}")
            _builtin_print(f"{'='*60}")
            loop.run_until_complete(lidar_webrtc_connection())
            # If we get here, connection ended gracefully
            retry_count = 0  # Reset on clean exit
            consecutive_fast_failures = 0
        except KeyboardInterrupt:
            _builtin_print("\nKeyboard interrupt - shutting down gracefully...")
            break
        except Exception as e:
            connection_duration = time.time() - connection_start
            retry_count += 1
            
            # Track fast failures (< 10 seconds)
            if connection_duration < 10:
                consecutive_fast_failures += 1
            else:
                consecutive_fast_failures = 0  # Reset if we lasted a while
            
            # Calculate backoff
            if consecutive_fast_failures > 3:
                # If failing quickly multiple times, use longer backoff
                reconnect_delay = min(30, 10 * consecutive_fast_failures)
                _builtin_print(f"WARNING: {consecutive_fast_failures} fast failures detected")
            else:
                reconnect_delay = min(retry_count * 2, 30)
            
            _builtin_print(f"Connection failed after {connection_duration:.1f}s: {e}")
            _builtin_print(f"Reconnecting in {reconnect_delay}s... (attempt #{retry_count})")
            
            # Sleep before retry
            time.sleep(reconnect_delay)
    
    _builtin_print("Closing event loop...")
    loop.close()
    _builtin_print("WebRTC thread exited")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Go2 LiDAR Visualization Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()
    
    _builtin_print("=" * 60)
    _builtin_print("Go2 LiDAR Visualization Server")
    _builtin_print("=" * 60)
    _builtin_print(f"\nServer will start at http://{args.host}:{args.port}/")
    _builtin_print("Make sure the Unitree Go2 mobile app is CLOSED\n")
    
    # Only start WebRTC in the reloader child process (not the parent watcher process)
    import os
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        # This is the child process that actually runs the server
        _builtin_print("Starting WebRTC connection thread...")
        webrtc_thread = threading.Thread(target=start_webrtc, daemon=True)
        webrtc_thread.start()
    else:
        _builtin_print("Parent reloader process - WebRTC will start in child process")
    
    # Start Flask server with auto-reload enabled
    socketio.run(app, host=args.host, port=args.port, debug=True, use_reloader=True, reloader_type='stat')

