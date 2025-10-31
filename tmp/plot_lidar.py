"""
LiDAR Visualization Script for Go2 Robot
=========================================

This script connects to the Go2 robot in AP mode and visualizes LiDAR data in real-time
using a web-based 3D viewer. Open http://127.0.0.1:8080/ in your browser to view.

Usage:
    python tmp/plot_lidar.py [options]

Options:
    --cam-center          Put camera at the center
    --type-voxel          Use voxel view instead of point cloud
    --csv-read FILE       Read from CSV file instead of WebRTC
    --csv-write           Write lidar data to CSV file
    --skip-mod N          Skip N-1 messages (default: 1, no skipping)
    --minYValue N         Minimum Y value filter (default: 0)
    --maxYValue N         Maximum Y value filter (default: 100)

Prerequisites:
    - Go2 robot in AP mode
    - Connected to Go2 WiFi network (192.168.12.1)
    - Unitree mobile app NOT connected
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
import csv
import numpy as np
from flask import Flask, render_template_string
from flask_socketio import SocketIO
from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
import argparse
from datetime import datetime
import os
import sys
import ast
import time
import json

from aiortc import RTCPeerConnection, RTCSessionDescription
import go2_webrtc_driver.util as _util
import go2_webrtc_driver.webrtc_datachannel as _webrtc_datachannel
import go2_webrtc_driver.unitree_auth as _unitree_auth
import go2_webrtc_driver.webrtc_driver as _webrtc_driver_mod

# Increase the field size limit for CSV reading (Windows has a smaller limit)
try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:
    # Windows limitation - use a large but safe value
    csv.field_size_limit(2147483647)  # 2^31 - 1, max 32-bit signed int

# Flask app and SocketIO setup
app = Flask(__name__)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

logging.basicConfig(level=logging.FATAL)

# Constants to enable/disable features
ENABLE_POINT_CLOUD = True
SAVE_LIDAR_DATA = True

# File paths
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
LIDAR_CSV_FILE = f"lidar_data_{timestamp}.csv"

# Global variables
lidar_csv_file = None
lidar_csv_writer = None

lidar_buffer = []
message_count = 0  # Counter for processed LIDAR messages
reconnect_interval = 5  # Time (seconds) before retrying connection

# Constants
MAX_RETRY_ATTEMPTS = 10

ROTATE_X_ANGLE = np.pi / 2  # 90 degrees
ROTATE_Z_ANGLE = np.pi      # 180 degrees

minYValue = 0
maxYValue = 100

# Parse command-line arguments
parser = argparse.ArgumentParser(description="LIDAR Viz for Go2")
parser.add_argument("--cam-center", action="store_true", help="Put Camera at the Center")
parser.add_argument("--type-voxel", action="store_true", help="Voxel View")
parser.add_argument("--csv-read", type=str, help="Read from CSV files instead of WebRTC")
parser.add_argument("--csv-write", action="store_true", help="Write CSV data file")
parser.add_argument("--skip-mod", type=int, default=1, help="Skip messages using modulus (default: 1, no skipping)")
parser.add_argument('--minYValue', type=int, default=0, help='Minimum Y value for the plot')
parser.add_argument('--maxYValue', type=int, default=100, help='Maximum Y value for the plot')
args = parser.parse_args()

minYValue = args.minYValue
maxYValue = args.maxYValue
SAVE_LIDAR_DATA = args.csv_write

# Apply patches from min_connect_status.py
def _patched_print_status(status_type, status_message):
    current_time = time.strftime("%H:%M:%S")
    _builtin_print(f"[{current_time}] {status_type}: {status_message}")


_util.print_status = _patched_print_status

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
            last_log = time.time()
        await asyncio.sleep(0.1)
    _builtin_print("Warning: data channel did not report open within 30s; continuing anyway")


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

@socketio.on('check_args')
def handle_check_args():
    typeFlag = 0b0101  # default iso cam & point cloud
    if args.cam_center:
        typeFlag |= 0b0010
    if args.type_voxel:
        typeFlag &= ~0b1000  # disable point cloud
        typeFlag |= 0b1000   # Set voxel flag
    typeFlagBinary = format(typeFlag, "04b")
    socketio.emit("check_args_ack", {"type": typeFlagBinary})

def setup_csv_output():
    """Set up CSV files for LIDAR output."""
    global lidar_csv_file, lidar_csv_writer

    if SAVE_LIDAR_DATA:
        lidar_csv_file = open(LIDAR_CSV_FILE, mode='w', newline='', encoding='utf-8')
        lidar_csv_writer = csv.writer(lidar_csv_file)
        lidar_csv_writer.writerow(['stamp', 'frame_id', 'resolution', 'src_size', 'origin', 'width', 
                                   'point_count', 'positions'])
        lidar_csv_file.flush()

def close_csv_output():
    """Close CSV files."""
    global lidar_csv_file

    if lidar_csv_file:
        lidar_csv_file.close()
        lidar_csv_file = None

def filter_points(points, percentage):
    """Filter points to skip plotting points within a certain percentage of distance to each other."""
    return points  # No filtering

def rotate_points(points, x_angle, z_angle):
    """Rotate points around the x and z axes by given angles."""
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

async def lidar_webrtc_connection():
    """Connect to WebRTC and process LIDAR data."""
    global lidar_buffer, message_count
    retry_attempts = 0

    while retry_attempts < MAX_RETRY_ATTEMPTS:
        try:
            # Use LocalAP mode (same as min_connect_status.py)
            conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)

            # Connect to WebRTC
            _builtin_print("Connecting to Go2 (AP mode)...")
            await asyncio.wait_for(conn.connect(), timeout=60.0)
            _builtin_print("Connected to WebRTC successfully!")
            retry_attempts = 0  # Reset retry attempts on successful connection

            # Disable traffic saving mode
            await conn.datachannel.disableTrafficSaving(True)

            # Set decoder type
            conn.datachannel.set_decoder(decoder_type='libvoxel')

            # Turn LIDAR sensor on
            conn.datachannel.pub_sub.publish_without_callback("rt/utlidar/switch", "on")
            _builtin_print("LiDAR sensor enabled")

            # Set up CSV outputs
            setup_csv_output()

            async def lidar_callback_task(message):
                """Task to process incoming LIDAR data."""
                if not ENABLE_POINT_CLOUD:
                    return

                try:
                    global message_count
                    if message_count % args.skip_mod != 0:
                        message_count += 1
                        return

                    # Validate message structure
                    if not isinstance(message, dict) or "data" not in message:
                        _builtin_print(f"WARNING: Invalid message format: {type(message)}")
                        return
                    
                    if "data" not in message["data"]:
                        _builtin_print(f"WARNING: Message missing 'data' field. Keys: {list(message['data'].keys())}")
                        return

                    positions = message["data"]["data"].get("positions", [])
                    origin = message["data"].get("origin", [])
                    points = np.array([positions[i:i+3] for i in range(0, len(positions), 3)], dtype=np.float32)
                    total_points = len(points)
                    unique_points = np.unique(points, axis=0)

                    # Save to CSV
                    if SAVE_LIDAR_DATA and lidar_csv_writer:
                        lidar_csv_writer.writerow([
                            message["data"]["stamp"],
                            message["data"]["frame_id"],
                            message["data"]["resolution"],
                            message["data"]["src_size"],
                            message["data"]["origin"],
                            message["data"]["width"],
                            len(unique_points),
                            unique_points.tolist()
                        ])
                        lidar_csv_file.flush()

                    points = rotate_points(unique_points, ROTATE_X_ANGLE, ROTATE_Z_ANGLE)
                    points = points[(points[:, 1] >= minYValue) & (points[:, 1] <= maxYValue)]

                    # Calculate center coordinates (handle empty arrays)
                    if len(points) == 0:
                        _builtin_print("WARNING: No points after filtering, skipping message")
                        return
                    center_x = float(np.mean(points[:, 0]))
                    center_y = float(np.mean(points[:, 1]))
                    center_z = float(np.mean(points[:, 2]))

                    # Offset points by center coordinates
                    offset_points = points - np.array([center_x, center_y, center_z])

                    # Count and log points
                    message_count += 1
                    _builtin_print(f"LIDAR Message {message_count}: Total={total_points}, Unique={len(unique_points)}, Filtered={len(offset_points)}")

                    # Emit data to Socket.IO (thread-safe emit)
                    scalars = np.linalg.norm(offset_points, axis=1)
                    try:
                        # Convert to lists and emit - socketio.emit works from background threads in threading mode
                        points_list = offset_points.tolist()
                        scalars_list = scalars.tolist()
                        socketio.emit("lidar_data", {
                            "points": points_list,
                            "scalars": scalars_list,
                            "center": {"x": center_x, "y": center_y, "z": center_z}
                        })
                        _builtin_print(f"  -> Emitted {len(points_list)} points to browser")
                    except Exception as emit_err:
                        _builtin_print(f"ERROR emitting socketio event: {emit_err}")
                        import traceback
                        traceback.print_exc()

                except Exception as e:
                    import traceback
                    _builtin_print(f"ERROR in LIDAR callback: {e}")
                    _builtin_print(traceback.format_exc())
                    # Don't let errors break the subscription - just skip this message
                    return

            # Subscribe to LIDAR voxel map messages
            def lidar_message_handler(message):
                """Handle incoming lidar messages."""
                _builtin_print(f"DEBUG: Received lidar message, keys: {list(message.keys()) if isinstance(message, dict) else type(message)}")
                if isinstance(message, dict) and "data" in message:
                    _builtin_print(f"DEBUG: Message data keys: {list(message['data'].keys()) if isinstance(message['data'], dict) else type(message['data'])}")
                asyncio.create_task(lidar_callback_task(message))
            
            conn.datachannel.pub_sub.subscribe(
                "rt/utlidar/voxel_map_compressed",
                lidar_message_handler
            )
            _builtin_print("Subscribed to rt/utlidar/voxel_map_compressed")
            _builtin_print("Subscribed to LiDAR data. View at http://127.0.0.1:8080/")

            # Keep the connection active - prevent disconnection
            _builtin_print("Connection active, waiting for LiDAR data...")
            try:
                while True:
                    await asyncio.sleep(1)
                    # Check connection status periodically
                    if not conn.isConnected:
                        _builtin_print("WARNING: Connection lost, attempting to reconnect...")
                        break
            except asyncio.CancelledError:
                _builtin_print("Connection cancelled")
                raise

        except asyncio.TimeoutError:
            _builtin_print(f"Connection timed out. Retrying in {reconnect_interval} seconds... (Attempt {retry_attempts + 1}/{MAX_RETRY_ATTEMPTS})")
            close_csv_output()
            await asyncio.sleep(reconnect_interval)
            retry_attempts += 1
        except Exception as e:
            _builtin_print(f"Error: {e}")
            _builtin_print(f"Reconnecting in {reconnect_interval} seconds... (Attempt {retry_attempts + 1}/{MAX_RETRY_ATTEMPTS})")
            close_csv_output()
            try:
                if 'conn' in locals():
                    await conn.disconnect()
            except Exception:
                pass
            await asyncio.sleep(reconnect_interval)
            retry_attempts += 1

    _builtin_print("Max retry attempts reached. Exiting.")

async def read_csv_and_emit(csv_file):
    """Continuously read CSV files and emit data without delay."""
    global message_count

    while True:
        try:
            total_messages = sum(1 for _ in open(csv_file)) - 1

            with open(csv_file, mode='r', newline='', encoding='utf-8') as lidar_file:
                lidar_reader = csv.DictReader(lidar_file)

                for lidar_row in lidar_reader:
                    if message_count % args.skip_mod == 0:
                        try:
                            positions = ast.literal_eval(lidar_row.get("positions", "[]"))
                            if isinstance(positions, list) and all(isinstance(item, list) and len(item) == 3 for item in positions):
                                points = np.array(positions, dtype=np.float32)
                            else:
                                points = np.array([item for item in positions if isinstance(item, list) and len(item) == 3], dtype=np.float32)

                            origin = np.array(eval(lidar_row.get("origin", "[]")), dtype=np.float32)
                            resolution = float(lidar_row.get("resolution", 0.05))
                            width = np.array(eval(lidar_row.get("width", "[128, 128, 38]")), dtype=np.float32)
                            center = origin + (width * resolution) / 2

                            if points.size > 0:
                                points = rotate_points(points, ROTATE_X_ANGLE, ROTATE_Z_ANGLE)
                                points = points[(points[:, 1] >= minYValue) & (points[:, 1] <= maxYValue)]
                                unique_points = np.unique(points, axis=0)
                                center_x = float(np.mean(unique_points[:, 0]))
                                center_y = float(np.mean(unique_points[:, 1]))
                                center_z = float(np.mean(unique_points[:, 2]))
                                offset_points = unique_points - np.array([center_x, center_y, center_z])
                            else:
                                unique_points = np.empty((0, 3), dtype=np.float32)
                                offset_points = unique_points

                            scalars = np.linalg.norm(offset_points, axis=1)
                            socketio.emit("lidar_data", {
                                "points": offset_points.tolist(),
                                "scalars": scalars.tolist(),
                                "center": {"x": center_x, "y": center_y, "z": center_z}
                            })

                            _builtin_print(f"LIDAR Message {message_count}/{total_messages}: Unique points={len(unique_points)}")

                        except Exception as e:
                            logging.error(f"Exception during processing: {e}")

                    message_count += 1

            message_count = 0

        except Exception as e:
            logging.error(f"Error reading CSV file: {e}")

@app.route("/")
def index():
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Go2 LiDAR Visualization</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.min.js"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
        <style> 
            body { margin: 0; display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100vh; background: #333; color: #fff; font-family: Arial, sans-serif; } 
            canvas { display: block; } 
            #status { position: absolute; top: 10px; left: 10px; padding: 10px; background: rgba(0,0,0,0.7); border-radius: 5px; }
        </style>
    </head>
    <body>
        <div id="status">Connecting...</div>
        <script>
            let scene, camera, renderer, controls, pointCloud, voxelMesh;
            let voxelSize = 1.0;
            let transparency = .5;
            let wireframe = false;
            let lightIntensity = .5;
            let pointCloudEnable = 1;
            let pollingInterval;
            const socket = io();
            document.addEventListener("DOMContentLoaded", () => {                                 
                function init() {
                    scene = new THREE.Scene();
                    scene.background = new THREE.Color(0x333333);

                    const sceneRotationDegrees = -90;
                    scene.rotation.y = THREE.MathUtils.degToRad(sceneRotationDegrees);

                    camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 10000);
                    camera.position.set(0, 50, 100);
                    camera.lookAt(0, 0, 0);

                    renderer = new THREE.WebGLRenderer({ antialias: true });
                    renderer.setSize(window.innerWidth, window.innerHeight);
                    document.body.appendChild(renderer.domElement);

                    controls = new THREE.OrbitControls(camera, renderer.domElement);
                    controls.target.set(0, 0, 0);
                    controls.enableDamping = true;
                    controls.dampingFactor = 0.05;
                    controls.maxPolarAngle = Math.PI;
                    controls.screenSpacePanning = true;
                    controls.update();

                    const ambientLight = new THREE.AmbientLight(0x555555, 0.5);
                    scene.add(ambientLight);

                    const directionalLight = new THREE.DirectionalLight(0xffffff, 1);
                    directionalLight.position.set(0, 100, 0);
                    directionalLight.castShadow = true;
                    scene.add(directionalLight);

                    const axesHelper = new THREE.AxesHelper(5);
                    scene.add(axesHelper);
                                                                                                                              
                    const statusDiv = document.getElementById("status");
                    
                    socket.on("connect", () => {
                        console.log("Socket connected to server");
                        statusDiv.textContent = "Connected - Waiting for LiDAR data...";
                        window.socketConnected = true;
                        pollArgs();
                    });
                    
                    socket.on("disconnect", (reason) => {
                        console.warn("Socket disconnected:", reason);
                        statusDiv.textContent = "Disconnected: " + reason;
                        window.socketConnected = false;
                    });
                    
                    socket.on("connect_error", (error) => {
                        console.error("Socket connection error:", error);
                        statusDiv.textContent = "Connection error: " + error;
                    });
                                  
                    socket.on("check_args_ack", (data) => {
                        console.log("Received check_args event:", data);
                         const typeFlag = parseInt(data.type, 2);                         
                        if (typeFlag & 0b0001) {  
                            camera.position.set(-100, 100, -100);
                            camera.lookAt(0, 0, 0);
                        }
                        if (typeFlag & 0b0010) {  
                            camera.position.set(0, 0, 10);
                            camera.lookAt(0, 0, -1);       
                         }
                        if (typeFlag & 0b0100) {  
                            pointCloudEnable = 1;
                            console.log("ptcloud:", pointCloudEnable);
                        }
                        if (typeFlag & 0b1000) {  
                            pointCloudEnable = 0;
                            console.log("ptcloud:", pointCloudEnable);
                        }
                        controls.update();
                        clearInterval(pollingInterval);
                     });
                                  
                    socket.on("lidar_data", (data) => {
                        console.log("Received LIDAR data event", data);
                        const points = data.points || [];
                        const scalars = data.scalars || [];
                        console.log("Processing", points.length, "points");
                        statusDiv.textContent = `Receiving LiDAR: ${points.length} points`;
                        
                        if (points.length === 0) {
                            console.warn("Received empty point array");
                            return;
                        }

                        if (pointCloudEnable > 0) {
                            if (pointCloud) scene.remove(pointCloud);
                            if (voxelMesh) {
                                scene.remove(voxelMesh);
                                voxelMesh = null;
                            }

                            const geometry = new THREE.BufferGeometry();
                            const vertices = new Float32Array(points.flat());
                            geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));

                            const colors = new Float32Array(scalars.length * 3);
                            const maxScalar = Math.max.apply(null, scalars);
                            scalars.forEach((scalar, i) => {
                                const color = new THREE.Color();
                                color.setHSL(scalar / maxScalar, 1.0, 0.5);
                                colors.set([color.r, color.g, color.b], i * 3);
                            });

                            geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
                            geometry.computeBoundingBox();

                            const material = new THREE.PointsMaterial({ size: 3.0, vertexColors: true });
                            pointCloud = new THREE.Points(geometry, material);

                            scene.add(pointCloud);
                            
                            // Adjust camera to view the point cloud
                            if (geometry.boundingBox) {
                                const center = new THREE.Vector3();
                                geometry.boundingBox.getCenter(center);
                                const size = new THREE.Vector3();
                                geometry.boundingBox.getSize(size);
                                const maxDim = Math.max(size.x, size.y, size.z);
                                camera.position.set(center.x + maxDim, center.y + maxDim, center.z + maxDim);
                                camera.lookAt(center);
                                controls.target.copy(center);
                                controls.update();
                            }
                        } else {
                            if (voxelMesh) scene.remove(voxelMesh);
                            voxelMesh = createVoxelMesh(points, scalars, voxelSize, Infinity);
                            if (voxelMesh instanceof THREE.Object3D) {
                                scene.add(voxelMesh);
                            }
                            if (pointCloud) {
                                scene.remove(pointCloud);
                                pointCloud = null;
                            }
                        }
                    });

                    function animate() {
                        requestAnimationFrame(animate);
                        controls.update();
                        renderer.render(scene, camera);
                    }

                    animate();
                }

                init();
            });

            function pollArgs() {
                pollingInterval = setInterval(() => {
                    socket.emit('check_args');
                }, 1000);
            }                     
                                     
            function createVoxelMesh(points, scalars, voxelSize, maxVoxelsToShow = Infinity) {
                const geometry = new THREE.BufferGeometry();

                try {
                    const halfSize = voxelSize / 2;
                    const cubeVertexOffsets = [
                        [-halfSize, -halfSize, -halfSize],
                        [halfSize, -halfSize, -halfSize],
                        [halfSize, halfSize, -halfSize],
                        [-halfSize, halfSize, -halfSize],
                        [-halfSize, -halfSize, halfSize],
                        [halfSize, -halfSize, halfSize],
                        [halfSize, halfSize, halfSize],
                        [-halfSize, halfSize, halfSize]
                    ];

                    const cubeIndices = [
                        0, 1, 2, 2, 3, 0,
                        4, 5, 6, 6, 7, 4,
                        0, 1, 5, 5, 4, 0,
                        2, 3, 7, 7, 6, 2,
                        0, 3, 7, 7, 4, 0,
                        1, 2, 6, 6, 5, 1
                    ];

                    const maxVoxels = Math.min(maxVoxelsToShow, points.length);
                    const maxScalar = Math.max(...scalars);

                    const positions = new Float32Array(maxVoxels * 8 * 3);
                    const colors = new Float32Array(maxVoxels * 8 * 3);
                    const indices = new Uint32Array(maxVoxels * 36);

                    let positionOffset = 0;
                    let colorOffset = 0;
                    let indexOffset = 0;

                    for (let i = 0; i < maxVoxels; i++) {
                        const centerX = points[i][0];
                        const centerY = points[i][1];
                        const centerZ = points[i][2];

                        const normalizedScalar = scalars[i] / maxScalar;
                        const color = new THREE.Color();
                        color.setHSL(normalizedScalar * 0.7, 1.0, 0.5);

                        for (let j = 0; j < 8; j++) {
                            const [dx, dy, dz] = cubeVertexOffsets[j];
                            positions[positionOffset++] = centerX + dx;
                            positions[positionOffset++] = centerY + dy;
                            positions[positionOffset++] = centerZ + dz;

                            colors[colorOffset++] = color.r;
                            colors[colorOffset++] = color.g;
                            colors[colorOffset++] = color.b;
                        }

                        for (let j = 0; j < cubeIndices.length; j++) {
                            indices[indexOffset++] = cubeIndices[j] + i * 8;
                        }
                    }

                    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
                    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
                    geometry.setIndex(new THREE.BufferAttribute(indices, 1));

                    } catch (error) {
                        THREE.Cache.clear();
                        if (error instanceof RangeError) {
                            console.error("Array buffer allocation failed:", error);
                            THREE.Cache.clear();
                            if (window.gc) window.gc();
                            return new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
                        } else {
                            throw error;
                        }
                    }

                const material = new THREE.MeshBasicMaterial({
                    vertexColors: true,
                    side: THREE.DoubleSide,
                    transparent: true,
                    opacity: transparency,
                    wireframe: wireframe
                });

                return new THREE.Mesh(geometry, material);
            }
        </script>
    </body>
    </html>
    """)

def start_webrtc():
    """Run WebRTC connection in a separate asyncio loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(lidar_webrtc_connection())

if __name__ == "__main__":
    import threading
    _builtin_print("=" * 60)
    _builtin_print("Go2 LiDAR Visualization")
    _builtin_print("=" * 60)
    _builtin_print("\nIMPORTANT: Make sure the Unitree Go2 mobile app is CLOSED")
    _builtin_print("           Open http://127.0.0.1:8080/ in your browser\n")
    
    if args.csv_read:
        csv_thread = threading.Thread(target=lambda: asyncio.run(read_csv_and_emit(args.csv_read)), daemon=True)
        csv_thread.start()
    else:
        webrtc_thread = threading.Thread(target=start_webrtc, daemon=True)
        webrtc_thread.start()

    socketio.run(app, host="127.0.0.1", port=8080, debug=True, use_reloader=True)

