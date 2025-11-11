"""
Go2 Sensor Monitor - Fault Investigation (Textual TUI)
========================================================

This script connects to the Go2 robot and monitors all sensor feeds to diagnose faults.
Uses Textual for a proper htop-style interface.

Usage:
    python tmp/fault_investigation/sensor_monitor.py
"""

import builtins as _builtins
import re as _re
import os
import sys
from datetime import datetime
from typing import Optional

# Remove emojis from output for Windows terminal compatibility (for logging only)
_builtin_print = _builtins.print
emoji_pattern = _re.compile('[\U0001F300-\U0001FAD6\U0001FAE0-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF]+', flags=_re.UNICODE)

def _no_emoji_print(*args, **kwargs):
    args = tuple(emoji_pattern.sub('', str(a)) for a in args)
    return _builtin_print(*args, **kwargs)

_builtins.print = _no_emoji_print

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import asyncio
import json
import logging
import time
import signal
import sqlite3
import threading
import argparse
from collections import defaultdict, deque
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Static, Header, Footer, DataTable, Label
from textual.reactive import reactive

from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC
from aiortc import RTCPeerConnection, RTCSessionDescription

import go2_webrtc_driver.util as _util
import go2_webrtc_driver.webrtc_datachannel as _webrtc_datachannel
import go2_webrtc_driver.unitree_auth as _unitree_auth
import go2_webrtc_driver.webrtc_driver as _webrtc_driver_mod
from go2_webrtc_driver.msgs.error_handler import handle_error

# File logging (optional)
log_file = os.path.join(os.path.dirname(__file__), 'sensor_log.txt')
log_file_handle = None
LOG_TO_FILE_ENABLED = False

# In-memory log buffer (stores last few seconds to avoid accumulation)
LOG_HISTORY_SECONDS = 3.0
LOG_HISTORY_MAX_ENTRIES = 1000
log_buffer = deque()

def _append_log(message: str, already_formatted: bool = False) -> None:
    """Append log message to in-memory buffer and optionally to disk."""
    timestamp = time.time()
    
    if already_formatted:
        log_entry = message
    else:
        timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        log_entry = f"[{timestamp_str}] {message}"
    
    # Append to buffer
    log_buffer.append((timestamp, log_entry))
    
    # Prune old entries by time
    cutoff = timestamp - LOG_HISTORY_SECONDS
    while log_buffer and log_buffer[0][0] < cutoff:
        log_buffer.popleft()
    
    # Safety: cap total entries
    while len(log_buffer) > LOG_HISTORY_MAX_ENTRIES:
        log_buffer.popleft()
    
    # Write to disk if enabled
    if LOG_TO_FILE_ENABLED and log_file_handle:
        try:
            log_file_handle.write(log_entry + '\n')
            log_file_handle.flush()
        except Exception:
            pass


# Suppress all console output - redirect to file only
class FileOnlyHandler(logging.Handler):
    """Logging handler that writes only to file, not console."""
    def emit(self, record):
        try:
            msg = self.format(record)
            _append_log(msg, already_formatted=True)
        except Exception:
            pass

# Set up logging to file only (no console)
logging.basicConfig(
    level=logging.INFO,
    handlers=[FileOnlyHandler()],
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger("aiortc").setLevel(logging.WARNING)

def log_to_file(message):
    """Write message to log file."""
    _append_log(message, already_formatted=False)


def enable_file_logging():
    """Enable persistent file logging (opt-in)."""
    global log_file_handle, LOG_TO_FILE_ENABLED
    if LOG_TO_FILE_ENABLED and log_file_handle:
        return
    try:
        log_file_handle = open(log_file, 'w', encoding='utf-8')
        LOG_TO_FILE_ENABLED = True
        # Dump current buffered logs to the file for continuity
        for _, buffered_entry in log_buffer:
            log_file_handle.write(buffered_entry + '\n')
        log_file_handle.flush()
    except Exception as exc:
        LOG_TO_FILE_ENABLED = False
        log_file_handle = None
        _append_log(f"WARNING: Failed to enable file logging: {exc}", already_formatted=False)

# Monkey-patch print_status to only log to file (no console output)
def _patched_print_status(status_type, status_message):
    current_time = time.strftime("%H:%M:%S")
    msg = f"[{current_time}] {status_type}: {status_message}"
    log_to_file(msg)

_util.print_status = _patched_print_status

# Suppress builtin print for library code
_original_print = _builtins.print
def _silent_print(*args, **kwargs):
    """Silent print that only logs to file."""
    msg = ' '.join(str(a) for a in args)
    log_to_file(f"LIBRARY_PRINT: {msg}")

# We'll selectively suppress print only during connection
_connection_print = _silent_print

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
            log_to_file(f"Waiting for datachannel readyState= {state}")
            last_log = time.time()
        await asyncio.sleep(0.1)
    log_to_file("Warning: data channel did not report open within 30s; continuing anyway")

_webrtc_datachannel.WebRTCDataChannel.wait_datachannel_open = _patched_wait_datachannel_open

# SDP patches
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
    return _orig_send_local(ip, sdp)

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

# Global connection
conn: Optional[Go2WebRTCConnection] = None

# Global flags
TEST_MODE = False
ENABLE_DB = False  # Database logging disabled by default for performance
ENABLE_VERBOSE_LOGGING = False  # Verbose file logging disabled by default

# In-memory data store (thread-safe with lock)
memory_store = {
    'lowstate': None,
    'sportmodestate': None,
    'errors': [],
    'connection_state': 'Disconnected',
    'connection_details': {
        'ice_connection_state': 'unknown',
        'ice_gathering_state': 'unknown',
        'signaling_state': 'unknown',
        'connection_state': 'unknown',
        'datachannel_state': 'unknown',
        'last_keepalive': None,
        'connection_uptime': 0.0,
        'lowstate_msg_rate': 0.0,
        'sportmode_msg_rate': 0.0,
        'last_lowstate_time': None,
        'last_sportmode_time': None,
    },
    'error_count': 0
}
memory_lock = threading.Lock()

# Bandwidth tracking (thread-safe with lock)
bandwidth_data = {
    'samples': []  # List of (timestamp, bytes) tuples - keep only recent samples
}
bandwidth_lock = threading.Lock()
MAX_BANDWIDTH_SAMPLES = 50  # Keep last 50 samples (~5 seconds) - reduced to prevent buffer bloat

# SQLite database setup (only used if ENABLE_DB is True)
db_path = os.path.join(os.path.dirname(__file__), 'sensor_data.db')
db_lock = threading.Lock()

def init_database():
    """Initialize SQLite database (only if ENABLE_DB is True)."""
    if not ENABLE_DB:
        return None
    
    conn_db = sqlite3.connect(db_path, check_same_thread=False)
    cursor = conn_db.cursor()
    
    # Create tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lowstate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            motor_state TEXT,
            bms_state TEXT,
            imu_state TEXT,
            foot_force TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sportmodestate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            mode INTEGER,
            progress REAL,
            body_height REAL,
            position TEXT,
            velocity TEXT,
            imu_state TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            error_source INTEGER,
            error_code INTEGER,
            error_data TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS connection_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            state TEXT
        )
    ''')
    
    conn_db.commit()
    return conn_db

def write_lowstate(data):
    """Write lowstate data to in-memory store and optionally to database."""
    # Estimate data size (fast approximation without full JSON serialization)
    # Rough estimate: count dict/list items and multiply by average size
    def estimate_size(obj):
        if isinstance(obj, dict):
            return sum(estimate_size(v) for v in obj.values()) + len(obj) * 10
        elif isinstance(obj, list):
            return sum(estimate_size(item) for item in obj) + len(obj) * 5
        elif isinstance(obj, (int, float)):
            return 8
        elif isinstance(obj, str):
            return len(obj)
        else:
            return 20  # Default estimate
    data_size = estimate_size(data)
    
    # Track bandwidth (minimal lock time, efficient cleanup)
    current_time = time.time()
    with bandwidth_lock:
        bandwidth_data['samples'].append((current_time, data_size))
        # Clean old samples efficiently - only clean if we're over limit
        # This avoids expensive list comprehension on every write
        if len(bandwidth_data['samples']) > MAX_BANDWIDTH_SAMPLES:
            cutoff_time = current_time - 2.0
            # Remove old samples from front (they're in chronological order)
            while bandwidth_data['samples'] and bandwidth_data['samples'][0][0] < cutoff_time:
                bandwidth_data['samples'].pop(0)
            # Also enforce max length as safety
            if len(bandwidth_data['samples']) > MAX_BANDWIDTH_SAMPLES:
                bandwidth_data['samples'] = bandwidth_data['samples'][-MAX_BANDWIDTH_SAMPLES:]
    
    # Always update in-memory store (best-effort, non-blocking)
    try:
        with memory_lock:
            # Only store essential data, don't accumulate
            memory_store['lowstate'] = {
                'motor_state': data.get('motor_state', [])[:20],  # Limit motor count
                'bms_state': data.get('bms_state', {}),
                'imu_state': data.get('imu_state', {}),
                'foot_force': data.get('foot_force', [])[:4],  # Limit foot force count
                'timestamp': time.time()
            }
    except Exception:
        pass  # Skip update if lock is held too long or data is bad
    
    # Optionally write to database
    if ENABLE_DB:
        try:
            conn_db = sqlite3.connect(db_path, check_same_thread=False)
            cursor = conn_db.cursor()
            
            cursor.execute('''
                INSERT INTO lowstate (timestamp, motor_state, bms_state, imu_state, foot_force)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                time.time(),
                json.dumps(data.get('motor_state', [])),
                json.dumps(data.get('bms_state', {})),
                json.dumps(data.get('imu_state', {})),
                json.dumps(data.get('foot_force', []))
            ))
            
            conn_db.commit()
            conn_db.close()
        except Exception as e:
            log_to_file(f"ERROR writing lowstate to DB: {e}")

def write_sportmodestate(data):
    """Write sport mode state to in-memory store and optionally to database."""
    # Estimate data size (fast approximation without full JSON serialization)
    def estimate_size(obj):
        if isinstance(obj, dict):
            return sum(estimate_size(v) for v in obj.values()) + len(obj) * 10
        elif isinstance(obj, list):
            return sum(estimate_size(item) for item in obj) + len(obj) * 5
        elif isinstance(obj, (int, float)):
            return 8
        elif isinstance(obj, str):
            return len(obj)
        else:
            return 20  # Default estimate
    data_size = estimate_size(data)
    
    # Track bandwidth (minimal lock time, efficient cleanup)
    current_time = time.time()
    with bandwidth_lock:
        bandwidth_data['samples'].append((current_time, data_size))
        # Clean old samples efficiently - only clean if we're over limit
        # This avoids expensive list comprehension on every write
        if len(bandwidth_data['samples']) > MAX_BANDWIDTH_SAMPLES:
            cutoff_time = current_time - 2.0
            # Remove old samples from front (they're in chronological order)
            while bandwidth_data['samples'] and bandwidth_data['samples'][0][0] < cutoff_time:
                bandwidth_data['samples'].pop(0)
            # Also enforce max length as safety
            if len(bandwidth_data['samples']) > MAX_BANDWIDTH_SAMPLES:
                bandwidth_data['samples'] = bandwidth_data['samples'][-MAX_BANDWIDTH_SAMPLES:]
    
    # Always update in-memory store (best-effort, non-blocking)
    try:
        with memory_lock:
            # Only store essential data, limit array sizes
            memory_store['sportmodestate'] = {
                'mode': data.get('mode'),
                'progress': data.get('progress'),
                'body_height': data.get('body_height'),
                'position': (data.get('position', []) or [])[:3],  # Limit to 3 elements
                'velocity': (data.get('velocity', []) or [])[:3],  # Limit to 3 elements
                'imu_state': data.get('imu_state', {}),
                'timestamp': time.time()
            }
    except Exception:
        pass  # Skip update if lock is held too long or data is bad
    
    # Optionally write to database
    if ENABLE_DB:
        try:
            conn_db = sqlite3.connect(db_path, check_same_thread=False)
            cursor = conn_db.cursor()
            
            cursor.execute('''
                INSERT INTO sportmodestate (timestamp, mode, progress, body_height, position, velocity, imu_state)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                time.time(),
                data.get('mode'),
                data.get('progress'),
                data.get('body_height'),
                json.dumps(data.get('position', [])),
                json.dumps(data.get('velocity', [])),
                json.dumps(data.get('imu_state', {}))
            ))
            
            conn_db.commit()
            conn_db.close()
        except Exception as e:
            log_to_file(f"ERROR writing sportmodestate to DB: {e}")

def write_error(error_source, error_code, error_data):
    """Write error to in-memory store and optionally to database."""
    # Always update in-memory store (limit error list to prevent unbounded growth)
    with memory_lock:
        memory_store['errors'].append({
            'timestamp': time.time(),
            'error_source': error_source,
            'error_code': error_code,
            'error_data': error_data
        })
        # Limit error list to last 100 errors to prevent memory bloat
        if len(memory_store['errors']) > 100:
            memory_store['errors'] = memory_store['errors'][-100:]
        memory_store['error_count'] = len(memory_store['errors'])
    
    # Optionally write to database
    if ENABLE_DB:
        try:
            conn_db = sqlite3.connect(db_path, check_same_thread=False)
            cursor = conn_db.cursor()
            
            cursor.execute('''
                INSERT INTO errors (timestamp, error_source, error_code, error_data)
                VALUES (?, ?, ?, ?)
            ''', (time.time(), error_source, error_code, json.dumps(error_data)))
            
            conn_db.commit()
            conn_db.close()
        except Exception as e:
            log_to_file(f"ERROR writing error to DB: {e}")

def write_connection_state(state):
    """Write connection state to in-memory store and optionally to database."""
    # Always update in-memory store
    with memory_lock:
        memory_store['connection_state'] = state
    
    # Optionally write to database
    if ENABLE_DB:
        try:
            conn_db = sqlite3.connect(db_path, check_same_thread=False)
            cursor = conn_db.cursor()
            
            cursor.execute('''
                INSERT INTO connection_state (timestamp, state)
                VALUES (?, ?)
            ''', (time.time(), state))
            
            conn_db.commit()
            conn_db.close()
        except Exception as e:
            log_to_file(f"ERROR writing connection state to DB: {e}")

def get_latest_lowstate():
    """Get latest lowstate data from in-memory store."""
    with memory_lock:
        return memory_store['lowstate']

def get_latest_sportmodestate():
    """Get latest sport mode state from in-memory store."""
    with memory_lock:
        return memory_store['sportmodestate']

def get_error_count():
    """Get count of errors from in-memory store."""
    with memory_lock:
        return memory_store['error_count']

def get_latest_connection_state():
    """Get latest connection state from in-memory store."""
    with memory_lock:
        return memory_store['connection_state']

def get_connection_details():
    """Get detailed connection information from in-memory store."""
    with memory_lock:
        return memory_store['connection_details'].copy()

def update_connection_details(**kwargs):
    """Update connection details in memory store."""
    try:
        with memory_lock:
            memory_store['connection_details'].update(kwargs)
    except Exception:
        pass  # Non-blocking

def get_datachannel_state(conn) -> str:
    """Best-effort helper to determine the RTCDataChannel ready state."""
    try:
        datachannel = getattr(conn, "datachannel", None)
        if not datachannel:
            return "missing"
        
        channel = getattr(datachannel, "channel", None)
        if channel:
            state = getattr(channel, "readyState", None)
            if state:
                return state
        
        # Fallback to library's boolean flag
        if getattr(datachannel, "data_channel_opened", False):
            return "open"
        return "closed"
    except Exception:
        return "error"

def get_bandwidth_kbps():
    """Calculate current bandwidth in kb/s based on recent samples (accurate, no caps)."""
    try:
        # Make a quick copy of samples to minimize lock time
        with bandwidth_lock:
            if len(bandwidth_data['samples']) < 2:
                return 0.0
            # Copy only recent samples (last 1 second worth)
            current_time = time.time()
            one_second_ago = current_time - 1.0
            samples = [(t, b) for t, b in bandwidth_data['samples'] if t >= one_second_ago]
        
        # Calculate outside the lock
        if len(samples) < 2:
            return 0.0
        
        # Calculate bandwidth from recent samples
        total_bytes = sum(b for _, b in samples)
        time_span = samples[-1][0] - samples[0][0] if len(samples) > 1 else 1.0
        
        # Only reject if time_span is invalid (negative or zero)
        if time_span <= 0:
            return 0.0
        
        bytes_per_second = total_bytes / time_span
        
        # Convert to kb/s (1 kb = 1024 bytes)
        kbps = (bytes_per_second * 8) / 1024.0  # *8 for bits, /1024 for kb
        
        return kbps
    except Exception:
        # If anything goes wrong, return 0 instead of crashing
        return 0.0

class MotorTable(DataTable):
    """Table showing motor temperatures and states."""
    
    # Motor labels: [Leg][Joint] - FL=Front Left, FR=Front Right, RL=Rear Left, RR=Rear Right
    # Joints: Hip, Thigh, Calf
    MOTOR_LABELS = [
        "FL-Hip", "FL-Thigh", "FL-Calf",      # 0-2: Front Left
        "FR-Hip", "FR-Thigh", "FR-Calf",      # 3-5: Front Right
        "RL-Hip", "RL-Thigh", "RL-Calf",      # 6-8: Rear Left
        "RR-Hip", "RR-Thigh", "RR-Calf",      # 9-11: Rear Right
    ]
    
    def on_mount(self) -> None:
        self.add_columns("Motor", "Temp (°C)", "Position", "Lost")
        for i in range(12):
            label = self.MOTOR_LABELS[i] if i < len(self.MOTOR_LABELS) else f"M{i+1}"
            self.add_row(label, "—", "—", "—")
    
    def update_motors(self, motor_state):
        """Update motor data."""
        # Only show first 12 motors (Go2 has 12 leg motors)
        motors_to_show = motor_state[:12] if len(motor_state) > 12 else motor_state
        
        try:
            # Clear existing rows and re-add with updated data
            # Note: This is necessary because Textual's update_cell doesn't work reliably
            # The clear/re-add is fast enough for 12 rows at 20 Hz
            self.clear()
            self.add_columns("Motor", "Temp (°C)", "Position", "Lost")
            
            for i, motor in enumerate(motors_to_show):
                if i >= 12:  # Safety check
                    break
                try:
                    temp = motor.get('temperature', 0)
                    q = motor.get('q', 0)
                    lost = motor.get('lost', 0)
                    
                    # Get motor label
                    label = self.MOTOR_LABELS[i] if i < len(self.MOTOR_LABELS) else f"M{i+1}"
                    
                    # Color code temperature
                    if temp >= 80:
                        temp_str = f"[red]{temp}°C[/red]"
                    elif temp >= 70:
                        temp_str = f"[yellow]{temp}°C[/yellow]"
                    elif temp >= 60:
                        temp_str = f"[bright_yellow]{temp}°C[/bright_yellow]"
                    else:
                        temp_str = f"{temp}°C"
                    
                    lost_str = f"[red]{lost}[/red]" if lost > 0 else str(lost)
                    
                    self.add_row(label, temp_str, f"{q:.4f}", lost_str)
                except Exception as e:
                    log_to_file(f"ERROR adding motor row {i}: {e}")
                    # Add row with error indicator
                    label = self.MOTOR_LABELS[i] if i < len(self.MOTOR_LABELS) else f"M{i+1}"
                    self.add_row(label, "ERROR", "—", "—")
        except Exception as e:
            log_to_file(f"ERROR in update_motors: {e}")
            import traceback
            log_to_file(traceback.format_exc())

class SensorMonitorApp(App):
    """Main Textual application for sensor monitoring."""
    
    CSS = """
    Screen {
        background: $surface;
    }
    
    #header {
        background: $primary;
        color: $text;
        text-align: center;
        height: 3;
    }
    
    #status-bar {
        background: $panel;
        height: 3;
        padding: 1;
    }
    
    #motors-panel {
        height: 18;
        border: solid $primary;
        padding: 1;
    }
    
    #info-panel {
        height: 18;
        border: solid $primary;
        padding: 1;
    }
    
    Label {
        margin: 1;
    }
    
    .error {
        color: $error;
    }
    
    .warning {
        color: $warning;
    }
    """
    
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]
    
    connection_state = reactive("Disconnected")
    last_update = reactive("—")
    error_count = reactive(0)
    bandwidth_kbps = reactive(0.0)
    
    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header(show_clock=True)
        yield Container(
            Static("GO2 SENSOR MONITOR - FAULT INVESTIGATION", id="header"),
            Horizontal(
                Vertical(
                    Static("MOTOR TEMPERATURES", classes="section-title"),
                    MotorTable(id="motors"),
                    id="motors-panel",
                ),
                Vertical(
                    Static("SYSTEM STATUS", classes="section-title"),
                    Label("Connection: [bold]Disconnected[/bold]", id="conn-status"),
                    Label("Last Update: —", id="last-update"),
                    Label("Errors: 0", id="error-count"),
                    Label("Bandwidth: —", id="bandwidth"),
                    Static("", id="spacer-conn"),
                    Static("CONNECTION DETAILS", classes="section-title"),
                    Label("ICE State: —", id="ice-state"),
                    Label("Signaling: —", id="signaling-state"),
                    Label("PC State: —", id="pc-state"),
                    Label("DataChannel: —", id="dc-state"),
                    Label("Uptime: —", id="uptime"),
                    Label("Last Keepalive: —", id="keepalive"),
                    Label("LowState Rate: —", id="lowstate-rate"),
                    Label("SportMode Rate: —", id="sportmode-rate"),
                    Static("", id="spacer1"),
                    Static("BATTERY (BMS)", classes="section-title"),
                    Label("SOC: —", id="bms-soc"),
                    Label("Current: —", id="bms-current"),
                    Label("BQ NTC: —", id="bms-bq"),
                    Label("MCU NTC: —", id="bms-mcu"),
                    Static("", id="spacer2"),
                    Static("IMU", classes="section-title"),
                    Label("Roll: —", id="imu-roll"),
                    Label("Pitch: —", id="imu-pitch"),
                    Label("Yaw: —", id="imu-yaw"),
                    Static("", id="spacer3"),
                    Static("SPORT MODE", classes="section-title"),
                    Label("Mode: —", id="sport-mode"),
                    Label("Body Height: —", id="sport-height"),
                    Label("Position: —", id="sport-pos"),
                    id="info-panel",
                ),
            ),
            Static(f"Log file: {log_file}", id="status-bar"),
        )
        yield Footer()
    
    def on_mount(self) -> None:
        """Called when app is mounted."""
        # Clean up any existing database from previous run (unless in test mode)
        if not TEST_MODE:
            try:
                if os.path.exists(db_path):
                    os.remove(db_path)
            except Exception:
                pass
        # Initialize database
        init_database()
        # Start connection
        self.set_timer(0.1, self.start_connection)
        # Start polling in-memory store for updates (every 100ms = 10 Hz)
        # Reduced frequency to avoid overwhelming the UI with table rebuilds
        # Reduce polling frequency from 10 Hz to 2 Hz to prevent event loop blocking
        # CLI version works fine with 1 Hz status updates, so 2 Hz should be plenty for UI
        self.set_interval(0.5, self.poll_database)
    
    async def start_connection(self) -> None:
        """Start WebRTC connection in background."""
        asyncio.create_task(self.run_webrtc_connection())
    
    def watch_connection_state(self, state: str) -> None:
        """Update connection state display."""
        widget = self.query_one("#conn-status", Label)
        if state == "Connected":
            widget.update(f"Connection: [bold green]{state}[/bold green]")
        else:
            widget.update(f"Connection: [bold red]{state}[/bold red]")
    
    def watch_last_update(self, update_time: str) -> None:
        """Update last update time."""
        self.query_one("#last-update", Label).update(f"Last Update: {update_time}")
    
    def watch_error_count(self, count: int) -> None:
        """Update error count."""
        widget = self.query_one("#error-count", Label)
        if count > 0:
            widget.update(f"Errors: [red]{count}[/red]")
        else:
            widget.update(f"Errors: {count}")
    
    def watch_bandwidth_kbps(self, kbps: float) -> None:
        """Update bandwidth display."""
        widget = self.query_one("#bandwidth", Label)
        if kbps > 0:
            # Format with appropriate units
            if kbps >= 1000:
                mbps = kbps / 1000.0
                widget.update(f"Bandwidth: [green]{mbps:.2f} Mb/s[/green]")
            else:
                widget.update(f"Bandwidth: [green]{kbps:.2f} kb/s[/green]")
        else:
            widget.update("Bandwidth: —")
    
    def update_motors(self, motor_state):
        """Update motor table."""
        table = self.query_one("#motors", MotorTable)
        table.update_motors(motor_state)
    
    def update_bms(self, bms):
        """Update BMS display."""
        self.query_one("#bms-soc", Label).update(f"SOC: {bms.get('soc', '—')}%")
        self.query_one("#bms-current", Label).update(f"Current: {bms.get('current', '—')} mA")
        self.query_one("#bms-bq", Label).update(f"BQ NTC: {bms.get('bq_ntc', '—')}°C")
        self.query_one("#bms-mcu", Label).update(f"MCU NTC: {bms.get('mcu_ntc', '—')}°C")
    
    def update_imu(self, imu):
        """Update IMU display."""
        rpy = imu.get('rpy', [0, 0, 0])
        self.query_one("#imu-roll", Label).update(f"Roll: {rpy[0]:.4f}")
        self.query_one("#imu-pitch", Label).update(f"Pitch: {rpy[1]:.4f}")
        self.query_one("#imu-yaw", Label).update(f"Yaw: {rpy[2]:.4f}")
    
    def update_sport_mode(self, sms):
        """Update sport mode display."""
        self.query_one("#sport-mode", Label).update(f"Mode: {sms.get('mode', '—')}")
        self.query_one("#sport-height", Label).update(f"Body Height: {sms.get('body_height', '—'):.4f} m")
        pos = sms.get('position', '—')
        if pos != '—':
            pos_str = f"[{pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f}]"
        else:
            pos_str = "—"
        self.query_one("#sport-pos", Label).update(f"Position: {pos_str}")
    
    def update_connection_details_ui(self, details):
        """Update connection details display."""
        try:
            # ICE state with color coding
            ice_state = details.get('ice_connection_state', 'unknown')
            ice_color = 'green' if ice_state == 'connected' else ('yellow' if ice_state == 'checking' else 'red')
            self.query_one("#ice-state", Label).update(f"ICE State: [{ice_color}]{ice_state}[/{ice_color}]")
            
            # Signaling state
            sig_state = details.get('signaling_state', 'unknown')
            self.query_one("#signaling-state", Label).update(f"Signaling: {sig_state}")
            
            # PC connection state with color coding
            pc_state = details.get('connection_state', 'unknown')
            pc_color = 'green' if pc_state == 'connected' else ('yellow' if pc_state == 'connecting' else 'red')
            self.query_one("#pc-state", Label).update(f"PC State: [{pc_color}]{pc_state}[/{pc_color}]")
            
            # DataChannel state with color coding
            dc_state = details.get('datachannel_state', 'unknown')
            dc_color = 'green' if dc_state == 'open' else ('yellow' if dc_state == 'connecting' else 'red')
            self.query_one("#dc-state", Label).update(f"DataChannel: [{dc_color}]{dc_state}[/{dc_color}]")
            
            # Uptime
            uptime = details.get('connection_uptime', 0.0)
            if uptime > 0:
                uptime_str = f"{int(uptime // 60)}m {int(uptime % 60)}s"
            else:
                uptime_str = "—"
            self.query_one("#uptime", Label).update(f"Uptime: {uptime_str}")
            
            # Last keepalive
            last_ka = details.get('last_keepalive')
            if last_ka:
                ka_age = time.time() - last_ka
                if ka_age < 25:  # Recent (within 25 seconds)
                    ka_str = f"{int(ka_age)}s ago"
                    ka_color = 'green'
                elif ka_age < 40:  # Getting old
                    ka_str = f"{int(ka_age)}s ago"
                    ka_color = 'yellow'
                else:  # Very old
                    ka_str = f"{int(ka_age)}s ago"
                    ka_color = 'red'
                self.query_one("#keepalive", Label).update(f"Last Keepalive: [{ka_color}]{ka_str}[/{ka_color}]")
            else:
                self.query_one("#keepalive", Label).update("Last Keepalive: —")
            
            # Message rates
            lowstate_rate = details.get('lowstate_msg_rate', 0.0)
            sportmode_rate = details.get('sportmode_msg_rate', 0.0)
            self.query_one("#lowstate-rate", Label).update(f"LowState Rate: {lowstate_rate:.1f} msg/s")
            self.query_one("#sportmode-rate", Label).update(f"SportMode Rate: {sportmode_rate:.1f} msg/s")
        except Exception:
            pass  # Best-effort, don't crash on UI update
    
    def action_quit(self) -> None:
        """Handle quit action."""
        self.exit()
    
    def poll_database(self) -> None:
        """Poll in-memory store for latest data and update UI (best-effort, non-blocking)."""
        try:
            # Get latest lowstate (best-effort, don't block)
            try:
                lowstate = get_latest_lowstate()
                if lowstate:
                    # Update motors (best-effort)
                    try:
                        motor_state = lowstate.get('motor_state', [])
                        if motor_state:
                            self.update_motors(motor_state)
                    except Exception:
                        pass  # Skip this update if it fails
                    
                    # Update BMS (best-effort)
                    try:
                        bms_state = lowstate.get('bms_state', {})
                        if bms_state:
                            self.update_bms(bms_state)
                    except Exception:
                        pass  # Skip this update if it fails
                    
                    # Update IMU (best-effort)
                    try:
                        imu_state = lowstate.get('imu_state', {})
                        if imu_state:
                            self.update_imu(imu_state)
                    except Exception:
                        pass  # Skip this update if it fails
                    
                    # Update timestamp (best-effort)
                    try:
                        if lowstate.get('timestamp'):
                            self.last_update = time.strftime('%H:%M:%S', time.localtime(lowstate['timestamp']))
                    except Exception:
                        pass
            except Exception:
                pass  # Skip lowstate entirely if it fails
            
            # Get latest sport mode state (best-effort)
            try:
                sportmodestate = get_latest_sportmodestate()
                if sportmodestate:
                    try:
                        self.update_sport_mode(sportmodestate)
                    except Exception:
                        pass  # Skip this update if it fails
                    try:
                        if sportmodestate.get('timestamp') and not lowstate:
                            self.last_update = time.strftime('%H:%M:%S', time.localtime(sportmodestate['timestamp']))
                    except Exception:
                        pass
            except Exception:
                pass  # Skip sportmode entirely if it fails
            
            # Get error count (best-effort)
            try:
                error_count = get_error_count()
                self.error_count = error_count
            except Exception:
                pass  # Skip if it fails
            
            # Get connection state (best-effort)
            try:
                conn_state = get_latest_connection_state()
                if conn_state != self.connection_state:
                    self.connection_state = conn_state
            except Exception:
                pass  # Skip if it fails
            
            # Get bandwidth (best-effort, display accurate value)
            try:
                kbps = get_bandwidth_kbps()
                self.bandwidth_kbps = kbps
            except Exception:
                pass  # Skip if it fails
            
            # Get and display connection details (best-effort)
            try:
                details = get_connection_details()
                self.update_connection_details_ui(details)
            except Exception:
                pass  # Skip if it fails
        except Exception:
            # If anything catastrophic happens, just skip this poll cycle
            pass
    
    def action_refresh(self) -> None:
        """Handle refresh action."""
        self.poll_database()
    
    async def run_webrtc_connection(self):
        """Run the WebRTC connection."""
        global conn
        
        # Suppress console output during connection
        import sys
        from io import StringIO
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = StringIO()
        sys.stderr = StringIO()
        
        try:
            connection_error_detected = {"value": False}
            
            def exception_handler(loop, context):
                exception = context.get('exception')
                if exception and isinstance(exception, AttributeError):
                    msg = str(exception)
                    if "'NoneType' object has no attribute 'media'" in msg:
                        log_to_file(f"WARNING: Intermittent connection error: {msg}")
                        connection_error_detected["value"] = True
                        return
                loop.default_exception_handler(context)
            
            loop = asyncio.get_event_loop()
            loop.set_exception_handler(exception_handler)
            
            # Infinite retry loop with exponential backoff (matching lidar2)
            retry_count = 0
            consecutive_fast_failures = 0
            
            while True:  # Infinite retry loop
                connection_start = time.time()
                conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
                try:
                    log_to_file(f"Connecting (attempt #{retry_count + 1})...")
                    connection_error_detected["value"] = False
                    
                    async def connect_with_monitoring():
                        connect_task = asyncio.create_task(conn.connect())
                        while not connect_task.done():
                            if connection_error_detected["value"]:
                                connect_task.cancel()
                                raise RuntimeError("Background task error detected")
                            await asyncio.sleep(0.05)
                        return await connect_task
                    
                    await asyncio.wait_for(connect_with_monitoring(), timeout=60.0)
                    
                    if connection_error_detected["value"]:
                        raise RuntimeError("Background task error detected")
                    
                    pc = getattr(conn, "pc", None)
                    if not pc:
                        raise RuntimeError("Peer connection not created")
                    
                    start_time = asyncio.get_event_loop().time()
                    while True:
                        if connection_error_detected["value"]:
                            raise RuntimeError("Background task error detected")
                        
                        state = getattr(pc, "connectionState", None)
                        if state == "connected":
                            if pc.remoteDescription:
                                break
                            else:
                                raise RuntimeError("Connected but no remote description")
                        
                        if state in {"failed", "disconnected", "closed"}:
                            raise RuntimeError(f"Connection state is {state}")
                        
                        if asyncio.get_event_loop().time() - start_time > 2.0:
                            raise RuntimeError("Connection state not progressing")
                        
                        await asyncio.sleep(0.05)
                    
                    log_to_file("Connection established successfully")
                    write_connection_state("Connected")
                    self.connection_state = "Connected"
                    
                    # Initialize connection details immediately after connection
                    try:
                        pc = getattr(conn, 'pc', None)
                        if pc:
                            update_connection_details(
                                ice_connection_state=getattr(pc, 'iceConnectionState', 'unknown'),
                                ice_gathering_state=getattr(pc, 'iceGatheringState', 'unknown'),
                                signaling_state=getattr(pc, 'signalingState', 'unknown'),
                                connection_state=getattr(pc, 'connectionState', 'unknown'),
                                datachannel_state=get_datachannel_state(conn),
                                connection_uptime=0.0,
                            )
                            log_to_file(
                                "Initial connection state: "
                                f"ICE={getattr(pc, 'iceConnectionState', 'unknown')}, "
                                f"PC={getattr(pc, 'connectionState', 'unknown')}, "
                                f"DC={get_datachannel_state(conn)}"
                            )
                    except Exception as e:
                        log_to_file(f"WARNING: Could not initialize connection details: {e}")
                        import traceback
                        log_to_file(traceback.format_exc())
                    
                    # Disable traffic saving immediately (keepalive)
                    try:
                        await conn.datachannel.disableTrafficSaving(True)
                        log_to_file("Traffic saving disabled (keepalive enabled)")
                    except Exception as e:
                        log_to_file(f"WARNING: Could not disable traffic saving: {e}")
                    
                    # Initialize last message time tracking and counters
                    self._last_message_time = {'lowstate': None, 'sportmode': None}
                    self._lowstate_msg_count = 0
                    self._sportmode_msg_count = 0
                    
                    # Wrap callbacks in async tasks to prevent blocking the WebRTC event loop
                    # This matches lidar2's approach and prevents connection hangs
                    async def lowstate_callback_task(message):
                        """Async wrapper for lowstate callback."""
                        try:
                            self.lowstate_callback(message)
                        except Exception as e:
                            log_to_file(f"ERROR in lowstate_callback_task: {e}")
                    
                    async def sportmodestate_callback_task(message):
                        """Async wrapper for sportmodestate callback."""
                        try:
                            self.sportmodestate_callback(message)
                        except Exception as e:
                            log_to_file(f"ERROR in sportmodestate_callback_task: {e}")
                    
                    def lowstate_message_handler(message):
                        """Synchronous handler that creates async task."""
                        asyncio.create_task(lowstate_callback_task(message))
                    
                    def sportmodestate_message_handler(message):
                        """Synchronous handler that creates async task."""
                        asyncio.create_task(sportmodestate_callback_task(message))
                    
                    # Subscribe to sensor feeds with async task wrappers
                    conn.datachannel.pub_sub.subscribe(RTC_TOPIC['LOW_STATE'], lowstate_message_handler)
                    conn.datachannel.pub_sub.subscribe(RTC_TOPIC['LF_SPORT_MOD_STATE'], sportmodestate_message_handler)
                    
                    # Patch error handler to log to file and update UI (no console print)
                    from go2_webrtc_driver.msgs import error_handler
                    original_handle_error = error_handler.handle_error
                    
                    def patched_handle_error(message):
                        self.error_callback(message)
                        # Log error to file instead of printing to console
                        data = message.get("data", [])
                        for error in data:
                            timestamp, error_source, error_code_int = error
                            readable_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
                            log_to_file(f"ERROR: Time={readable_time}, Source={error_source}, Code={error_code_int}")
                    
                    error_handler.handle_error = patched_handle_error
                    
                    # Keepalive loop with connection monitoring (matching lidar2 approach)
                    connection_start_time = asyncio.get_event_loop().time()
                    connection_start_wall_time = time.time()
                    last_keepalive = time.time()
                    msg_count_start_time = time.time()
                    
                    try:
                        while True:
                            if connection_error_detected["value"]:
                                break
                            
                            await asyncio.sleep(1)  # Check more frequently for shutdown
                            
                            current_time = time.time()
                            loop_time = asyncio.get_event_loop().time()
                            uptime = loop_time - connection_start_time
                            
                            # Collect detailed connection state (with protection against hanging)
                            ice_conn_state = 'unknown'
                            ice_gathering_state = 'unknown'
                            signaling_state = 'unknown'
                            pc_state = 'unknown'
                            dc_state = 'unknown'
                            is_connected = False
                            
                            try:
                                pc = getattr(conn, 'pc', None)
                                if pc:
                                    try:
                                        ice_conn_state = getattr(pc, 'iceConnectionState', 'unknown')
                                    except Exception:
                                        ice_conn_state = 'error'
                                    
                                    try:
                                        ice_gathering_state = getattr(pc, 'iceGatheringState', 'unknown')
                                    except Exception:
                                        ice_gathering_state = 'error'
                                    
                                    try:
                                        signaling_state = getattr(pc, 'signalingState', 'unknown')
                                    except Exception:
                                        signaling_state = 'error'
                                    
                                    try:
                                        pc_state = getattr(pc, 'connectionState', 'unknown')
                                    except Exception:
                                        pc_state = 'error'
                                else:
                                    # PC doesn't exist yet - connection not initialized
                                    ice_conn_state = 'not_initialized'
                                    pc_state = 'not_initialized'
                                
                                # Get datachannel state
                                dc_state = get_datachannel_state(conn)
                                
                                # Get connection flag
                                try:
                                    is_connected = getattr(conn, 'isConnected', False)
                                except Exception:
                                    is_connected = False
                            except Exception as e:
                                log_to_file(f"WARNING: Error checking connection status: {e}")
                                import traceback
                                log_to_file(traceback.format_exc())
                                is_connected = False
                                pc_state = 'error'
                            
                            # Calculate message rates (over last 5 seconds)
                            msg_window = current_time - msg_count_start_time
                            if msg_window >= 5.0:
                                lowstate_rate = self._lowstate_msg_count / msg_window if msg_window > 0 else 0.0
                                sportmode_rate = self._sportmode_msg_count / msg_window if msg_window > 0 else 0.0
                                self._lowstate_msg_count = 0
                                self._sportmode_msg_count = 0
                                msg_count_start_time = current_time
                            else:
                                lowstate_rate = self._lowstate_msg_count / msg_window if msg_window > 0 else 0.0
                                sportmode_rate = self._sportmode_msg_count / msg_window if msg_window > 0 else 0.0
                            
                            # Get last message times
                            last_lowstate_time = self._last_message_time.get('lowstate')
                            last_sportmode_time = self._last_message_time.get('sportmode')
                            
                            # Update connection details in memory store
                            update_connection_details(
                                ice_connection_state=ice_conn_state,
                                ice_gathering_state=ice_gathering_state,
                                signaling_state=signaling_state,
                                connection_state=pc_state,
                                datachannel_state=dc_state,
                                last_keepalive=last_keepalive,
                                connection_uptime=uptime,
                                lowstate_msg_rate=lowstate_rate,
                                sportmode_msg_rate=sportmode_rate,
                                last_lowstate_time=last_lowstate_time,
                                last_sportmode_time=last_sportmode_time,
                            )
                            
                            # Also check if we've stopped receiving messages (connection might be dead)
                            # Match lidar2 logic: check if we've received any messages, and if so, check timeout
                            # Use the most recent message time from either channel
                            last_message_time = None
                            if self._last_message_time['lowstate']:
                                last_message_time = self._last_message_time['lowstate']
                            if self._last_message_time['sportmode']:
                                if not last_message_time or self._last_message_time['sportmode'] > last_message_time:
                                    last_message_time = self._last_message_time['sportmode']
                            
                            # If we've received messages before, check if they've stopped
                            if last_message_time:
                                time_since_last_msg = current_time - last_message_time
                                if time_since_last_msg > 10:  # No messages for 10 seconds = dead connection
                                    log_to_file(f"WARNING: No sensor messages for {time_since_last_msg:.1f}s")
                                    is_connected = False
                            
                            if (
                                not is_connected
                                or pc_state in ['closed', 'failed', 'disconnected']
                                or ice_conn_state in ['closed', 'failed', 'disconnected']
                            ):
                                uptime = asyncio.get_event_loop().time() - connection_start_time
                                log_to_file(
                                    f"Connection lost after {uptime:.1f}s uptime "
                                    f"(pc_state={pc_state}, ice_state={ice_conn_state})"
                                )
                                raise ConnectionError("WebRTC connection lost")
                            
                            # Send active keepalive every 20 seconds (matching lidar2)
                            if current_time - last_keepalive >= 20.0:
                                try:
                                    dc_state_check = get_datachannel_state(conn)
                                    if dc_state_check == 'open':
                                        await conn.datachannel.disableTrafficSaving(True)
                                        last_keepalive = current_time
                                        update_connection_details(last_keepalive=last_keepalive)
                                        uptime = asyncio.get_event_loop().time() - connection_start_time
                                        log_to_file(f"Keepalive sent at {uptime:.0f}s")
                                    else:
                                        log_to_file(f"WARNING: Data channel not open (state: {dc_state_check}), cannot send keepalive")
                                        # Don't update last_keepalive so we'll try again soon
                                except Exception as e:
                                    log_to_file(f"WARNING: Keepalive failed: {e}")
                                    # Don't break on keepalive failure - connection might still be alive
                                    # Don't update last_keepalive so we'll try again soon
                            
                            # Log connection status every 30 seconds
                            uptime = asyncio.get_event_loop().time() - connection_start_time
                            if int(uptime) % 30 == 0 and uptime > 0:
                                log_to_file(f"Connection stable: {uptime:.0f}s")
                    except ConnectionError as ce:
                        log_to_file("Connection error detected, exiting keepalive loop")
                        # Re-raise to trigger retry in outer loop
                        raise
                    except asyncio.CancelledError:
                        log_to_file("Connection cancelled")
                        raise
                    
                    # If we exit the keepalive loop without exception, connection was lost
                    # This shouldn't normally happen, but if it does, trigger retry
                    log_to_file("Keepalive loop exited unexpectedly, triggering retry")
                    raise ConnectionError("Keepalive loop exited")
                    
                except KeyboardInterrupt:
                    log_to_file("Keyboard interrupt - shutting down gracefully...")
                    break
                except Exception as e:
                    # Calculate connection duration and retry delay (matching lidar2)
                    connection_duration = time.time() - connection_start
                    retry_count += 1
                    
                    if connection_duration < 10:
                        consecutive_fast_failures += 1
                    else:
                        consecutive_fast_failures = 0
                    
                    if consecutive_fast_failures > 3:
                        reconnect_delay = min(30, 10 * consecutive_fast_failures)
                        log_to_file(f"WARNING: {consecutive_fast_failures} fast failures detected")
                    else:
                        reconnect_delay = min(retry_count * 2, 30)
                    
                    log_to_file(f"Connection failed after {connection_duration:.1f}s: {e}")
                    log_to_file(f"Reconnecting in {reconnect_delay}s... (attempt #{retry_count})")
                    
                    # Disconnect and cleanup
                    if conn:
                        try:
                            await asyncio.wait_for(conn.disconnect(), timeout=5.0)
                        except Exception:
                            pass
                    connection_error_detected["value"] = False
                    
                    # Sleep with small increments to allow for shutdown checks
                    for _ in range(int(reconnect_delay)):
                        await asyncio.sleep(1.0)
            
            # Final cleanup
            if conn:
                try:
                    await asyncio.wait_for(conn.disconnect(), timeout=5.0)
                except Exception:
                    pass
        finally:
            # Restore console output
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        
        write_connection_state("Disconnected")
        self.connection_state = "Disconnected"
        log_to_file("WebRTC connection closed")
    
    def lowstate_callback(self, message):
        """Handle LOW_STATE messages - write to in-memory store."""
        try:
            # Update last message time for connection monitoring
            if hasattr(self, '_last_message_time'):
                self._last_message_time['lowstate'] = time.time()
            # Increment message counter for rate calculation
            if hasattr(self, '_lowstate_msg_count'):
                self._lowstate_msg_count += 1
            
            data = message.get('data', {})
            write_lowstate(data)
            
            # Log based on verbose logging flag
            if ENABLE_VERBOSE_LOGGING:
                # Log every message when verbose logging is enabled
                motor_state = data.get('motor_state', [])
                max_temp = max([m.get('temperature', 0) for m in motor_state]) if motor_state else 0
                log_to_file(f"LOW_STATE: Max motor temp: {max_temp}°C, Motors: {len(motor_state)}")
            else:
                # Only log occasionally to reduce I/O overhead (every 100th message or every 10 seconds)
                if not hasattr(self, '_lowstate_log_counter'):
                    self._lowstate_log_counter = 0
                    self._last_lowstate_log_time = time.time()
                
                self._lowstate_log_counter += 1
                current_time = time.time()
                
                if self._lowstate_log_counter % 100 == 0 or (current_time - self._last_lowstate_log_time) >= 10.0:
                    motor_state = data.get('motor_state', [])
                    max_temp = max([m.get('temperature', 0) for m in motor_state]) if motor_state else 0
                    log_to_file(f"LOW_STATE: Max motor temp: {max_temp}°C, Motors: {len(motor_state)}")
                    self._last_lowstate_log_time = current_time
        except Exception as e:
            log_to_file(f"ERROR in lowstate_callback: {e}")
            import traceback
            log_to_file(traceback.format_exc())
    
    def sportmodestate_callback(self, message):
        """Handle LF_SPORT_MOD_STATE messages - write to in-memory store."""
        try:
            # Update last message time for connection monitoring
            if hasattr(self, '_last_message_time'):
                self._last_message_time['sportmode'] = time.time()
            # Increment message counter for rate calculation
            if hasattr(self, '_sportmode_msg_count'):
                self._sportmode_msg_count += 1
            
            data = message.get('data', {})
            write_sportmodestate(data)
            
            # Log based on verbose logging flag
            if ENABLE_VERBOSE_LOGGING:
                # Log every message when verbose logging is enabled
                log_to_file(f"SPORT_MODE_STATE: Mode={data.get('mode', 'N/A')}, BodyHeight={data.get('body_height', 'N/A')}")
            else:
                # Only log occasionally to reduce I/O overhead (every 50th message or every 10 seconds)
                if not hasattr(self, '_sportmode_log_counter'):
                    self._sportmode_log_counter = 0
                    self._last_sportmode_log_time = time.time()
                
                self._sportmode_log_counter += 1
                current_time = time.time()
                
                if self._sportmode_log_counter % 50 == 0 or (current_time - self._last_sportmode_log_time) >= 10.0:
                    log_to_file(f"SPORT_MODE_STATE: Mode={data.get('mode', 'N/A')}, BodyHeight={data.get('body_height', 'N/A')}")
                    self._last_sportmode_log_time = current_time
        except Exception as e:
            log_to_file(f"ERROR in sportmodestate_callback: {e}")
            import traceback
            log_to_file(traceback.format_exc())
    
    def error_callback(self, message):
        """Handle error messages - write to database."""
        try:
            data = message.get("data", [])
            for error in data:
                timestamp, error_source, error_code_int = error
                write_error(error_source, error_code_int, error)
            log_to_file(f"ERROR MESSAGE: {json.dumps(message, indent=2)}")
        except Exception as e:
            log_to_file(f"ERROR in error_callback: {e}")
    
    async def on_unmount(self) -> None:
        """Clean up on exit."""
        global conn
        if conn:
            try:
                await asyncio.wait_for(conn.disconnect(), timeout=5.0)
            except Exception:
                pass
        if LOG_TO_FILE_ENABLED and log_file_handle:
            try:
                log_file_handle.close()
            except Exception:
                pass
        # Delete database on exit (unless in test mode or DB not enabled)
        if ENABLE_DB:
            if not TEST_MODE:
                try:
                    if os.path.exists(db_path):
                        os.remove(db_path)
                        log_to_file("Database cleaned up on exit")
                except Exception as e:
                    log_to_file(f"ERROR cleaning up database: {e}")
            else:
                log_to_file(f"TEST MODE: Database preserved at {db_path}")

def main():
    """Main entry point."""
    global TEST_MODE, ENABLE_DB, ENABLE_VERBOSE_LOGGING, LOG_TO_FILE_ENABLED
    
    parser = argparse.ArgumentParser(description='Go2 Sensor Monitor - Fault Investigation')
    parser.add_argument('--db', action='store_true',
                        help='Enable SQLite database logging (default: in-memory only for performance)')
    parser.add_argument('--test', action='store_true', 
                       help='Test mode: preserve database on exit for auditing (requires --db)')
    parser.add_argument('--log', '--verbose-logging', action='store_true', dest='verbose_logging',
                        help='Enable verbose file logging (logs every sensor message, default: throttled logging)')
    parser.add_argument('--log-file', action='store_true', dest='enable_file_logging',
                        help='Persist full sensor_log.txt to disk (default: only keep last few seconds in memory)')
    args = parser.parse_args()
    TEST_MODE = args.test
    ENABLE_DB = args.db
    ENABLE_VERBOSE_LOGGING = args.verbose_logging
    
    # Test mode requires database to be enabled
    if TEST_MODE and not ENABLE_DB:
        print("WARNING: --test flag requires --db flag. Database logging will be enabled.")
        ENABLE_DB = True
    
    # Configure file logging (opt-in)
    if args.enable_file_logging:
        enable_file_logging()
        log_to_file("File logging enabled - persisting sensor_log.txt")
    else:
        _append_log("File logging disabled - retaining last few seconds in memory (use --log-file to persist)",
                    already_formatted=False)
    
    # Initialize database only if enabled
    if ENABLE_DB:
        init_database()
        log_to_file(f"Database logging enabled: {db_path}")
        if TEST_MODE:
            log_to_file("TEST MODE ENABLED: Database will be preserved on exit")
            print("TEST MODE: Database will be preserved at:", db_path)
    else:
        log_to_file("Database logging disabled - using in-memory store only")
    
    # Log verbose logging status
    if ENABLE_VERBOSE_LOGGING:
        log_to_file("VERBOSE LOGGING ENABLED: All sensor messages will be logged")
    else:
        log_to_file("Verbose logging disabled - using throttled logging (every 100th lowstate, every 50th sportmode)")
    
    app = SensorMonitorApp()
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        if ENABLE_DB and TEST_MODE:
            print(f"\nTEST MODE: Database preserved at: {db_path}")
            print("You can audit it with: sqlite3", db_path)
        if LOG_TO_FILE_ENABLED and log_file_handle:
            try:
                log_file_handle.close()
            except Exception:
                pass

if __name__ == "__main__":
    main()
