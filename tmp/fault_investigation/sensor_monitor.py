"""
Go2 Sensor Monitor - Simplified TUI
===================================

Simple, non-blocking sensor monitor that polls latest data.
No DB, no accumulation, optional file logging.

Usage:
    python tmp/fault_investigation/sensor_monitor.py [--log-file sensor_log.txt]
"""

import os
import sys
import asyncio
import time
import threading
import argparse
from typing import Optional, Dict, Any
from collections import deque
from datetime import datetime

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Static, Header, Footer, DataTable, Label
from textual.reactive import reactive

from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC
import go2_webrtc_driver.util as _util
import go2_webrtc_driver.webrtc_datachannel as _webrtc_datachannel
from go2_webrtc_driver.msgs import error_handler

# ---------------------------------------------------------------------------
# Simple file logging (only when flag is set)
# ---------------------------------------------------------------------------

log_file_handle = None
LOG_ENABLED = False

def log_to_file(message: str) -> None:
    """Write to file only if logging is enabled."""
    if LOG_ENABLED and log_file_handle:
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            log_file_handle.write(f"[{timestamp}] {message}\n")
            log_file_handle.flush()
        except Exception:
            pass

def enable_file_logging(log_path: str) -> None:
    """Enable file logging."""
    global log_file_handle, LOG_ENABLED
    try:
        log_file_handle = open(log_path, 'w', encoding='utf-8')
        LOG_ENABLED = True
        log_to_file("File logging enabled")
    except Exception as exc:
        LOG_ENABLED = False
        log_file_handle = None
        print(f"WARNING: Failed to enable file logging: {exc}")

# Patch print_status to use our logger
def _patched_print_status(status_type: str, status_message: str) -> None:
    log_to_file(f"{status_type}: {status_message}")

_util.print_status = _patched_print_status

# ---------------------------------------------------------------------------
# Latest data store (thread-safe, no accumulation)
# ---------------------------------------------------------------------------

data_lock = threading.Lock()
bandwidth_lock = threading.Lock()

# Latest data only - overwrite, don't accumulate
latest_data: Dict[str, Any] = {
    "lowstate": None,
    "sportmode": None,
    "connection_state": "Disconnected",
    "connection_details": {
        "ice_connection_state": "unknown",
        "connection_state": "unknown",
        "datachannel_state": "unknown",
        "connection_uptime": 0.0,
        "lowstate_msg_rate": 0.0,
        "sportmode_msg_rate": 0.0,
        "last_keepalive": None,
    },
}

# Bandwidth tracking (simple, capped)
bandwidth_samples = deque(maxlen=80)  # ~8 seconds at 10 Hz
message_metrics = {
    "lowstate_count": 0,
    "sportmode_count": 0,
    "window_start": time.time(),
}

# ---------------------------------------------------------------------------
# Callbacks - simple, non-blocking
# ---------------------------------------------------------------------------

def _estimate_size(obj: Any) -> int:
    """Quick size estimate for bandwidth tracking."""
    if isinstance(obj, dict):
        return sum(_estimate_size(v) for v in obj.values()) + len(obj) * 10
    if isinstance(obj, list):
        return sum(_estimate_size(item) for item in obj) + len(obj) * 5
    if isinstance(obj, (int, float)):
        return 8
    if isinstance(obj, str):
        return len(obj)
    return 20

def lowstate_callback(message: Dict[str, Any]) -> None:
    """Update latest lowstate data - simple, non-blocking."""
    data = message.get("data", {})
    now = time.time()
    
    # Track bandwidth
    size = _estimate_size(data)
    with bandwidth_lock:
        bandwidth_samples.append((now, size))
    
    # Update latest data (overwrite, no accumulation)
    with data_lock:
        message_metrics["lowstate_count"] += 1
        latest_data["lowstate"] = {
            "motor_state": data.get("motor_state", [])[:20],
            "bms_state": data.get("bms_state", {}),
            "imu_state": data.get("imu_state", {}),
            "foot_force": data.get("foot_force", [])[:4],
            "timestamp": now,
        }
        latest_data["connection_details"]["lowstate_msg_rate"] = message_metrics["lowstate_count"] / max(0.1, now - message_metrics["window_start"])

def sportmode_callback(message: Dict[str, Any]) -> None:
    """Update latest sportmode data - simple, non-blocking."""
    data = message.get("data", {})
    now = time.time()
    
    # Track bandwidth
    size = _estimate_size(data)
    with bandwidth_lock:
        bandwidth_samples.append((now, size))
    
    # Update latest data (overwrite, no accumulation)
    with data_lock:
        message_metrics["sportmode_count"] += 1
        latest_data["sportmode"] = {
            "mode": data.get("mode"),
            "body_height": data.get("body_height"),
            "position": (data.get("position") or [])[:3],
            "velocity": (data.get("velocity") or [])[:3],
            "imu_state": data.get("imu_state", {}),
            "timestamp": now,
        }
        latest_data["connection_details"]["sportmode_msg_rate"] = message_metrics["sportmode_count"] / max(0.1, now - message_metrics["window_start"])

def error_callback(message: Dict[str, Any]) -> None:
    """Handle error messages."""
    log_to_file(f"ERROR: {message}")

# ---------------------------------------------------------------------------
# UI Components
# ---------------------------------------------------------------------------

MOTOR_LABELS = [
    "FL-Hip", "FL-Thigh", "FL-Calf",
    "FR-Hip", "FR-Thigh", "FR-Calf",
    "RL-Hip", "RL-Thigh", "RL-Calf",
    "RR-Hip", "RR-Thigh", "RR-Calf",
]

class MotorTable(DataTable):
    """Motor temperature table."""
    
    def on_mount(self) -> None:
        self.add_columns("Motor", "Temp (°C)", "Position", "Lost")
        for i in range(12):
            label = MOTOR_LABELS[i] if i < len(MOTOR_LABELS) else f"M{i+1}"
            self.add_row(label, "—", "—", "—")

class SensorMonitorApp(App):
    """Simplified sensor monitor TUI."""
    
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
    """
    
    BINDINGS = [("q", "quit", "Quit")]
    
    connection_state = reactive("Disconnected")
    last_update = reactive("—")
    bandwidth_kbps = reactive(0.0)
    
    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header(show_clock=True)
        yield Static("GO2 SENSOR MONITOR", id="header")
        yield Horizontal(
            Vertical(
                Static("MOTOR TEMPERATURES", classes="section-title"),
                MotorTable(id="motors"),
                id="motors-panel",
            ),
            Vertical(
                Static("SYSTEM STATUS", classes="section-title"),
                Label("Connection: [bold]Disconnected[/bold]", id="conn-status"),
                Label("Last Update: —", id="last-update"),
                Label("Bandwidth: —", id="bandwidth"),
                Static("", id="spacer-conn"),
                Static("CONNECTION DETAILS", classes="section-title"),
                Label("ICE State: —", id="ice-state"),
                Label("PC State: —", id="pc-state"),
                Label("DataChannel: —", id="dc-state"),
                Label("Uptime: —", id="uptime"),
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
        )
        yield Footer()
    
    def on_mount(self) -> None:
        """Start connection and polling."""
        self.set_timer(0.1, self.start_connection)
        # Poll at 2 Hz - simple, non-blocking
        self.set_interval(0.5, self.poll_latest_data)
    
    async def start_connection(self) -> None:
        """Start WebRTC connection."""
        asyncio.create_task(self.run_webrtc_connection())
    
    def watch_connection_state(self, state: str) -> None:
        """Update connection state display."""
        widget = self.query_one("#conn-status", Label)
        color = "green" if state == "Connected" else "red"
        widget.update(f"Connection: [{color}]{state}[/{color}]")
    
    def watch_last_update(self, update_time: str) -> None:
        """Update last update time."""
        self.query_one("#last-update", Label).update(f"Last Update: {update_time}")
    
    def watch_bandwidth_kbps(self, kbps: float) -> None:
        """Update bandwidth display."""
        widget = self.query_one("#bandwidth", Label)
        if kbps > 0:
            widget.update(f"Bandwidth: {kbps:.1f} kb/s")
        else:
            widget.update("Bandwidth: —")
    
    def poll_latest_data(self) -> None:
        """Poll latest data and update UI - non-blocking."""
        try:
            with data_lock:
                lowstate = latest_data.get("lowstate")
                sportmode = latest_data.get("sportmode")
                details = latest_data.get("connection_details", {})
            
            # Update motors
            if lowstate:
                try:
                    motor_state = lowstate.get("motor_state", [])
                    if motor_state:
                        self.update_motors(motor_state)
                except Exception:
                    pass
            
            # Update BMS
            if lowstate:
                try:
                    bms_state = lowstate.get("bms_state", {})
                    if bms_state:
                        self.update_bms(bms_state)
                except Exception:
                    pass
            
            # Update IMU
            if lowstate:
                try:
                    imu_state = lowstate.get("imu_state", {})
                    if imu_state:
                        self.update_imu(imu_state)
                except Exception:
                    pass
            
            # Update sport mode
            if sportmode:
                try:
                    self.update_sport_mode(sportmode)
                except Exception:
                    pass
            
            # Update connection details
            try:
                self.update_connection_details(details)
            except Exception:
                pass
            
            # Update timestamp
            if lowstate and lowstate.get("timestamp"):
                try:
                    self.last_update = time.strftime('%H:%M:%S', time.localtime(lowstate['timestamp']))
                except Exception:
                    pass
            
            # Calculate bandwidth
            try:
                with bandwidth_lock:
                    samples = list(bandwidth_samples)
                if len(samples) >= 2:
                    start_time = samples[0][0]
                    end_time = samples[-1][0]
                    if end_time > start_time:
                        total_bytes = sum(b for _, b in samples)
                        bytes_per_sec = total_bytes / (end_time - start_time)
                        self.bandwidth_kbps = (bytes_per_sec * 8) / 1024.0
            except Exception:
                pass
                
        except Exception:
            pass  # Best-effort, don't crash
    
    def update_motors(self, motor_state: list) -> None:
        """Update motor table."""
        try:
            table = self.query_one("#motors", MotorTable)
            table.clear()
            for i, motor in enumerate(motor_state[:12]):
                label = MOTOR_LABELS[i] if i < len(MOTOR_LABELS) else f"M{i+1}"
                temp = motor.get("temperature", "—")
                pos = f"{motor.get('q', 0):.2f}" if motor.get('q') is not None else "—"
                lost = motor.get("lost", "—")
                table.add_row(label, str(temp), pos, str(lost))
        except Exception:
            pass
    
    def update_bms(self, bms_state: dict) -> None:
        """Update BMS display."""
        try:
            self.query_one("#bms-soc", Label).update(f"SOC: {bms_state.get('soc', '—')}%")
            self.query_one("#bms-current", Label).update(f"Current: {bms_state.get('current', '—')}mA")
            bq_ntc = bms_state.get("bq_ntc", ["—", "—"])
            if isinstance(bq_ntc, list):
                self.query_one("#bms-bq", Label).update(f"BQ NTC: {bq_ntc[0]}/{bq_ntc[1]}°C")
            mcu_ntc = bms_state.get("mcu_ntc", ["—", "—"])
            if isinstance(mcu_ntc, list):
                self.query_one("#bms-mcu", Label).update(f"MCU NTC: {mcu_ntc[0]}/{mcu_ntc[1]}°C")
        except Exception:
            pass
    
    def update_imu(self, imu_state: dict) -> None:
        """Update IMU display."""
        try:
            rpy = imu_state.get("rpy", [0, 0, 0])
            if isinstance(rpy, list) and len(rpy) >= 3:
                self.query_one("#imu-roll", Label).update(f"Roll: {rpy[0]:.3f}")
                self.query_one("#imu-pitch", Label).update(f"Pitch: {rpy[1]:.3f}")
                self.query_one("#imu-yaw", Label).update(f"Yaw: {rpy[2]:.3f}")
        except Exception:
            pass
    
    def update_sport_mode(self, sportmode: dict) -> None:
        """Update sport mode display."""
        try:
            self.query_one("#sport-mode", Label).update(f"Mode: {sportmode.get('mode', '—')}")
            height = sportmode.get("body_height")
            if height is not None:
                self.query_one("#sport-height", Label).update(f"Body Height: {height:.3f}")
            pos = sportmode.get("position", [])
            if isinstance(pos, list) and len(pos) >= 3:
                self.query_one("#sport-pos", Label).update(f"Position: [{pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f}]")
        except Exception:
            pass
    
    def update_connection_details(self, details: dict) -> None:
        """Update connection details."""
        try:
            self.query_one("#ice-state", Label).update(f"ICE State: {details.get('ice_connection_state', '—')}")
            self.query_one("#pc-state", Label).update(f"PC State: {details.get('connection_state', '—')}")
            self.query_one("#dc-state", Label).update(f"DataChannel: {details.get('datachannel_state', '—')}")
            uptime = details.get("connection_uptime", 0.0)
            if uptime > 0:
                uptime_str = f"{int(uptime // 60)}m {int(uptime % 60)}s"
            else:
                uptime_str = "—"
            self.query_one("#uptime", Label).update(f"Uptime: {uptime_str}")
            low_rate = details.get("lowstate_msg_rate", 0.0)
            sport_rate = details.get("sportmode_msg_rate", 0.0)
            self.query_one("#lowstate-rate", Label).update(f"LowState Rate: {low_rate:.1f} msg/s")
            self.query_one("#sportmode-rate", Label).update(f"SportMode Rate: {sport_rate:.1f} msg/s")
        except Exception:
            pass
    
    def get_datachannel_state(self, conn: Go2WebRTCConnection) -> str:
        """Get data channel state."""
        try:
            datachannel = getattr(conn, "datachannel", None)
            if not datachannel:
                return "missing"
            channel = getattr(datachannel, "channel", None)
            if channel:
                state = getattr(channel, "readyState", None)
                if state:
                    return str(state)
            if getattr(datachannel, "data_channel_opened", False):
                return "open"
            return "closed"
        except Exception:
            return "error"
    
    async def run_webrtc_connection(self) -> None:
        """Run WebRTC connection loop."""
        retry_count = 0
        
        while True:
            conn: Optional[Go2WebRTCConnection] = None
            try:
                conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
                log_to_file(f"Connecting (attempt #{retry_count + 1})...")
                
                await asyncio.wait_for(conn.connect(), timeout=60.0)
                
                pc = getattr(conn, "pc", None)
                if not pc:
                    raise RuntimeError("Peer connection not created")
                
                # Wait for connection
                start_wait = time.time()
                while True:
                    state = getattr(pc, "connectionState", None)
                    if state == "connected":
                        if pc.remoteDescription:
                            break
                        raise RuntimeError("Connected but no remote description")
                    if state in {"failed", "disconnected", "closed"}:
                        raise RuntimeError(f"Connection state is {state}")
                    if time.time() - start_wait > 3.0:
                        raise RuntimeError("Connection state not progressing")
                    await asyncio.sleep(0.05)
                
                log_to_file("Connection established")
                self.connection_state = "Connected"
                
                with data_lock:
                    latest_data["connection_state"] = "Connected"
                
                # Initial keepalive
                try:
                    await conn.datachannel.disableTrafficSaving(True)
                    log_to_file("Initial keepalive sent")
                except Exception as e:
                    log_to_file(f"WARNING: Initial keepalive failed: {e}")
                
                # Register callbacks
                conn.datachannel.pub_sub.subscribe(RTC_TOPIC['LOW_STATE'], lowstate_callback)
                conn.datachannel.pub_sub.subscribe(RTC_TOPIC['LF_SPORT_MOD_STATE'], sportmode_callback)
                error_handler.handle_error = error_callback
                
                # Reset message metrics
                with data_lock:
                    message_metrics["lowstate_count"] = 0
                    message_metrics["sportmode_count"] = 0
                    message_metrics["window_start"] = time.time()
                
                # Keepalive loop
                connection_start_time = time.time()
                last_keepalive = time.time()
                keepalive_interval = 8.0
                
                while True:
                    await asyncio.sleep(1.0)
                    
                    now = time.time()
                    uptime = now - connection_start_time
                    
                    # Update connection details
                    pc_state = getattr(pc, "connectionState", "unknown")
                    ice_state = getattr(pc, "iceConnectionState", "unknown")
                    dc_state = self.get_datachannel_state(conn)
                    
                    with data_lock:
                        latest_data["connection_details"].update(
                            ice_connection_state=ice_state,
                            connection_state=pc_state,
                            datachannel_state=dc_state,
                            connection_uptime=uptime,
                        )
                    
                    # Check connection health
                    if pc_state in {"closed", "failed", "disconnected"} or ice_state in {"closed", "failed", "disconnected"}:
                        raise ConnectionError(f"Connection lost (pc={pc_state}, ice={ice_state})")
                    
                    # Send keepalive
                    if now - last_keepalive >= keepalive_interval:
                        try:
                            if dc_state == "open":
                                await conn.datachannel.disableTrafficSaving(True)
                                last_keepalive = now
                                with data_lock:
                                    latest_data["connection_details"]["last_keepalive"] = last_keepalive
                                log_to_file(f"Keepalive sent at {uptime:.0f}s")
                        except Exception as e:
                            log_to_file(f"WARNING: Keepalive failed: {e}")
                    
                    # Reset message rate window periodically
                    if now - message_metrics["window_start"] >= 5.0:
                        with data_lock:
                            message_metrics["lowstate_count"] = 0
                            message_metrics["sportmode_count"] = 0
                            message_metrics["window_start"] = now
                
            except KeyboardInterrupt:
                log_to_file("Keyboard interrupt")
                break
            except Exception as exc:
                duration = time.time() - (connection_start_time if 'connection_start_time' in locals() else time.time())
                retry_count += 1
                delay = min(30, max(2, retry_count * 5))
                log_to_file(f"Connection failed after {duration:.1f}s: {exc}")
                log_to_file(f"Reconnecting in {delay}s (attempt #{retry_count + 1})")
                
                self.connection_state = "Disconnected"
                with data_lock:
                    latest_data["connection_state"] = "Disconnected"
                
                if conn:
                    try:
                        await asyncio.wait_for(conn.disconnect(), timeout=5.0)
                    except Exception:
                        pass
                
                await asyncio.sleep(delay)
                continue
    
    async def on_unmount(self) -> None:
        """Clean up on exit."""
        if log_file_handle:
            try:
                log_file_handle.close()
            except Exception:
                pass

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Go2 Sensor Monitor (Simplified TUI)")
    parser.add_argument("--log-file", type=str, default=None, help="Enable file logging to specified path")
    args = parser.parse_args()
    
    if args.log_file:
        enable_file_logging(args.log_file)
    
    app = SensorMonitorApp()
    app.run()

if __name__ == "__main__":
    main()
