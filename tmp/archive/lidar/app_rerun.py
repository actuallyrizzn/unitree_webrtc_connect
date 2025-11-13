"""
Go2 LiDAR Visualization with Rerun
==================================

A clean, production-ready LiDAR visualization using Rerun.io
Includes all necessary WebRTC patches for Go2 compatibility.

Usage:
    python tmp/lidar/app_rerun.py
    
Features:
- Real-time 3D point cloud visualization
- Auto-reconnect on disconnect
- Optional video stream
- Built-in recording/playback
- Clean separation for AI processing
"""

import builtins as _builtins
import re as _re
import sys
import os
import asyncio
import json
import argparse
import threading
import time
import numpy as np
import rerun as rr
import signal
import atexit
import socket

# Global shutdown flag
_shutdown_requested = False

def is_port_in_use(port, host='127.0.0.1'):
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True

# Remove emojis for Windows terminal
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

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC
import go2_webrtc_driver.util as _util
import go2_webrtc_driver.webrtc_datachannel as _webrtc_datachannel
import go2_webrtc_driver.unitree_auth as _unitree_auth
import go2_webrtc_driver.webrtc_driver as _webrtc_driver_mod
from aiortc import RTCPeerConnection, RTCSessionDescription

# === WebRTC Patches for Go2 Compatibility ===

def _patched_print_status(status_type, status_message):
    current_time = time.strftime("%H:%M:%S")
    _builtin_print(f"[{current_time}] {status_type}: {status_message}")

_util.print_status = _patched_print_status

_orig_wait_datachannel_open = _webrtc_datachannel.WebRTCDataChannel.wait_datachannel_open

async def _patched_wait_datachannel_open(self, timeout=5):
    """Extended wait for data channel with better logging."""
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
    """Rewrite SDP from RFC 8841 format to legacy format for aiortc compatibility."""
    if not isinstance(sdp, str):
        return sdp
    lines = []
    saw_m_application = False
    saw_sctpmap = False
    for line in sdp.splitlines():
        if line.startswith("m=application"):
            # Preserve port and use UDP/DTLS/SCTP format
            parts = line.split()
            port = parts[1] if len(parts) > 1 else "9"
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
    # Don't rewrite the answer - use it as-is from the Go2
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

# === LiDAR Processing ===

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

# Configuration
ROTATE_X_ANGLE = np.pi / 2  # 90 degrees
ROTATE_Z_ANGLE = np.pi       # 180 degrees
MIN_Y_VALUE = 0
MAX_Y_VALUE = 100

# Stats
stats = {
    'messages_received': 0,
    'start_time': None,
    'last_message_time': None,
}

async def lidar_webrtc_connection(enable_viz=True, ai_callback=None, record_file=None, state=None):
    """
    Establish WebRTC connection and process LiDAR data.
    
    Args:
        enable_viz: If True, log to Rerun for visualization
        ai_callback: Optional callback(points) for AI processing
        record_file: Optional file path to record to
        state: Dict with 'rerun_initialized' flag to track Rerun server state
    """
    if state is None:
        state = {'rerun_initialized': False}
    
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
        raise
    
    # Initialize Rerun ONLY on first connection
    # Also check if ports are already in use (from previous auto-reload)
    rerun_ports_in_use = is_port_in_use(9090) or is_port_in_use(9876)
    
    if enable_viz and not state['rerun_initialized']:
        if rerun_ports_in_use:
            _builtin_print("\n" + "="*60)
            _builtin_print("RERUN SERVERS ALREADY RUNNING (from previous session)")
            _builtin_print("="*60)
            _builtin_print("Web Viewer: http://localhost:9090")
            _builtin_print("gRPC Server: rerun+http://127.0.0.1:9876/proxy")
            _builtin_print("Skipping Rerun initialization...")
            _builtin_print("="*60 + "\n")
        elif record_file:
            rr.init("go2_lidar", recording_id="go2_session")
            rr.save(record_file)
            _builtin_print(f"Recording to: {record_file}")
        else:
            # Initialize recording stream first
            rec = rr.init("go2_lidar", spawn=False)
            # Serve gRPC server and web viewer separately (new API)
            server_uri = rr.serve_grpc(grpc_port=9876, recording=rec)
            rr.serve_web_viewer(web_port=9090, open_browser=True, connect_to=server_uri)
            _builtin_print("\n" + "="*60)
            _builtin_print("RERUN WEB VIEWER STARTED")
            _builtin_print("="*60)
            _builtin_print("Web Viewer: http://localhost:9090")
            _builtin_print("gRPC Server: " + server_uri)
            _builtin_print("Full URL:   http://localhost:9090/?url=" + server_uri.replace(':', '%3A').replace('/', '%2F'))
            _builtin_print("="*60 + "\n")
        
        # Set up coordinate system
        rr.log("lidar", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)
        
        # Set default camera view for better initial orientation
        rr.log(
            "lidar",
            rr.Transform3D(
                translation=[0, 0, 0],
                relation=rr.TransformRelation.ChildFromParent
            ),
            static=True
        )
        
        # Mark as initialized immediately after Rerun setup (modifies dict in-place)
        state['rerun_initialized'] = True
    
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
        """Process incoming LiDAR messages."""
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
            
            # Downsample for visualization (AI gets full resolution)
            viz_points = filtered_points
            if len(filtered_points) > 15000:
                step = len(filtered_points) // 15000
                viz_points = filtered_points[::step]
            
            # Calculate center and offset
            center = np.mean(viz_points, axis=0)
            offset_points = viz_points - center
            
            # AI processing (full resolution, always runs)
            if ai_callback:
                try:
                    ai_callback(filtered_points)  # Full resolution to AI
                except Exception as e:
                    _builtin_print(f"ERROR in AI callback: {e}")
            
            # Visualization (downsampled, optional)
            if enable_viz:
                # Color by height (Z axis after rotation)
                z = offset_points[:, 2]
                z_min, z_max = z.min(), z.max()
                if z_max > z_min:
                    norm_z = (z - z_min) / (z_max - z_min)
                else:
                    norm_z = np.zeros_like(z)
                
                # HSV color gradient (blue -> green -> red)
                hue = 0.6 - (norm_z * 0.6)  # 0.6 (blue) to 0.0 (red)
                colors = np.zeros((len(offset_points), 3), dtype=np.uint8)
                
                # Simple HSV to RGB conversion
                for i in range(len(hue)):
                    h = hue[i] * 6.0
                    x = 1.0 - abs((h % 2.0) - 1.0)
                    
                    if h < 1:
                        r, g, b = 0, x, 1
                    elif h < 2:
                        r, g, b = 0, 1, x
                    elif h < 3:
                        r, g, b = x, 1, 0
                    elif h < 4:
                        r, g, b = 1, x, 0
                    elif h < 5:
                        r, g, b = 1, 0, x
                    else:
                        r, g, b = x, 0, 1
                    
                    colors[i] = [int(r * 255), int(g * 255), int(b * 255)]
                
                # Log to Rerun with larger points for better visibility
                rr.log("lidar/points", rr.Points3D(
                    offset_points,
                    colors=colors,
                    radii=0.05  # Larger point size for better visibility
                ))
                
                # Log stats (using Scalars, not Scalar)
                rr.log("stats/message_count", rr.Scalars(scalars=stats['messages_received']))
                rr.log("stats/point_count", rr.Scalars(scalars=len(offset_points)))
                rr.log("stats/message_rate", rr.Scalars(scalars=stats['messages_received'] / (time.time() - stats['start_time'])))
                
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
    
    if enable_viz:
        _builtin_print("\nRerun viewer should open automatically.")
        _builtin_print("If not, run: rerun")
        _builtin_print("Controls: Mouse drag to rotate, scroll to zoom\n")
    
    # Keep connection alive with monitoring and active keepalive
    connection_start_time = asyncio.get_event_loop().time()
    last_keepalive = time.time()
    last_message_check = time.time()
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
            
            # Send active keepalive every 20 seconds by toggling traffic saving
            # This ensures the data channel stays active
            current_time = time.time()
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

def start_webrtc(enable_viz=True, ai_callback=None, record_file=None):
    """Run WebRTC connection with auto-reconnect."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    retry_count = 0
    consecutive_fast_failures = 0
    # Use a dict to track state across function calls (mutable object)
    state = {'rerun_initialized': False}
    
    while not _shutdown_requested:
        connection_start = time.time()
        try:
            _builtin_print(f"\n{'='*60}")
            _builtin_print(f"WebRTC Connection Attempt #{retry_count + 1}")
            _builtin_print(f"{'='*60}")
            loop.run_until_complete(lidar_webrtc_connection(enable_viz, ai_callback, record_file, state))
            retry_count = 0
            consecutive_fast_failures = 0
        except KeyboardInterrupt:
            _builtin_print("\nKeyboard interrupt - shutting down gracefully...")
            break
        except Exception as e:
            if _shutdown_requested:
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

# === Main ===

def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully"""
    global _shutdown_requested
    if not _shutdown_requested:  # Only print once
        _builtin_print("\n" + "="*60)
        _builtin_print("SHUTDOWN REQUESTED - Cleaning up...")
        _builtin_print("="*60)
        _shutdown_requested = True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Go2 LiDAR Visualization with Rerun")
    parser.add_argument("--no-viz", action="store_true", help="Disable visualization (data only)")
    parser.add_argument("--record", type=str, help="Record to .rrd file for playback")
    parser.add_argument("--no-reload", action="store_true", help="Disable auto-reload on file changes")
    args = parser.parse_args()
    
    # Set up signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    
    _builtin_print("=" * 60)
    _builtin_print("Go2 LiDAR Visualization (Rerun)")
    _builtin_print("=" * 60)
    _builtin_print("Make sure the Unitree Go2 mobile app is CLOSED")
    if not args.no_reload:
        _builtin_print("Auto-reload: ENABLED (changes to this file will restart)")
    _builtin_print()
    
    # Optional: Define AI processing callback
    def ai_processing_callback(points):
        """
        This function receives FULL RESOLUTION point cloud data.
        Use this for navigation, obstacle detection, SLAM, etc.
        """
        # Example: Just log point count
        # In production, this would feed your AI algorithms
        pass
    
    # Auto-reload functionality using watchdog
    if not args.no_reload:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        
        class ReloadHandler(FileSystemEventHandler):
            def __init__(self):
                self.should_reload = False
                self.last_modified = 0
            
            def on_modified(self, event):
                if event.src_path.endswith('.py'):
                    current_time = time.time()
                    # Debounce: only trigger once per 2 seconds
                    if current_time - self.last_modified > 2:
                        self.last_modified = current_time
                        if not self.should_reload:  # Only trigger once
                            _builtin_print(f"\n{'='*60}")
                            _builtin_print(f"FILE CHANGED: {os.path.basename(event.src_path)}")
                            _builtin_print(f"Restarting...")
                            _builtin_print(f"{'='*60}\n")
                            self.should_reload = True
                            # Don't call signal_handler - just set flag
                            global _shutdown_requested
                            _shutdown_requested = True
        
        reload_handler = ReloadHandler()
        observer = Observer()
        observer.schedule(reload_handler, path=os.path.dirname(os.path.abspath(__file__)), recursive=False)
        observer.start()
        
        def run_with_reload():
            global _shutdown_requested
            keyboard_interrupt = False
            
            while True:
                # Reset flags for this iteration
                _shutdown_requested = False
                reload_handler.should_reload = False
                
                try:
                    start_webrtc(
                        enable_viz=not args.no_viz,
                        ai_callback=ai_processing_callback if not args.no_viz else None,
                        record_file=args.record if hasattr(args, 'record') else None
                    )
                except KeyboardInterrupt:
                    keyboard_interrupt = True
                    break
                
                # Check if we should restart or exit
                if keyboard_interrupt or not reload_handler.should_reload:
                    # User hit Ctrl+C or connection ended without reload request
                    break
                
                # If we get here, it's a reload - wait a moment then restart
                _builtin_print("Waiting 2s before restart...\n")
                time.sleep(2)
            
            observer.stop()
            observer.join()
            _builtin_print("\nShutdown complete")
        
        run_with_reload()
    else:
        # No auto-reload
        try:
            start_webrtc(
                enable_viz=not args.no_viz,
                ai_callback=ai_processing_callback if not args.no_viz else None,
                record_file=args.record if hasattr(args, 'record') else None
            )
        except KeyboardInterrupt:
            _builtin_print("\nShutdown complete")
    
    # Force exit to terminate any lingering threads (Rerun web server, etc.)
    # Use os._exit(0) instead of sys.exit(0) to immediately terminate
    # all threads without waiting for cleanup
    _builtin_print("Forcing process exit...")
    os._exit(0)

