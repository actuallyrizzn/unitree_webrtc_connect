"""
Go2 Sensor Monitor (CLI Edition)
================================

Lightweight, non-Textual variant of the sensor monitor that prints status
updates to stdout so we can troubleshoot WebRTC stability without the TUI.

Usage:
    python tmp/fault_investigation/sensor_monitor_cli.py

Optional flags:
    --keepalive-interval SECONDS   (default: 8)
    --status-interval SECONDS      (default: 1)
    --disable-bandwidth            Skip bandwidth estimation
    --log-file PATH                Mirror status lines to the given file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Set UTF-8 encoding for stdout/stderr on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC
import go2_webrtc_driver.util as _util
import go2_webrtc_driver.webrtc_datachannel as _webrtc_datachannel
from go2_webrtc_driver.msgs import error_handler

# Patch print_status immediately after import to strip emojis
emoji_pattern = re.compile('[\U0001F300-\U0001FAD6\U0001FAE0-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF]+', flags=re.UNICODE)

def _patched_print_status_early(status_type: str, status_message: str) -> None:
    # Strip emojis and print without the clock emoji prefix
    clean_type = emoji_pattern.sub('', str(status_type))
    clean_msg = emoji_pattern.sub('', str(status_message))
    current_time = time.strftime("%H:%M:%S")
    print(f"{clean_type:<25}: {clean_msg:<15} ({current_time})", flush=True)

_util.print_status = _patched_print_status_early

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_path: Optional[str]) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_path:
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )
    logging.getLogger("aiortc").setLevel(logging.WARNING)
    logging.info("CLI monitor logging initialised")


# ---------------------------------------------------------------------------
# Globals guarded by threading locks (callbacks run in aiortc event loop)
# ---------------------------------------------------------------------------

memory_lock = threading.Lock()
bandwidth_lock = threading.Lock()

memory_store: Dict[str, Any] = {
    "lowstate": None,
    "sportmode": None,
    "errors": deque(maxlen=100),
    "connection_state": "Disconnected",
    "connection_details": {
        "ice_connection_state": "unknown",
        "ice_gathering_state": "unknown",
        "signaling_state": "unknown",
        "connection_state": "unknown",
        "datachannel_state": "unknown",
        "last_keepalive": None,
        "connection_uptime": 0.0,
        "lowstate_msg_rate": 0.0,
        "sportmode_msg_rate": 0.0,
        "last_lowstate_time": None,
        "last_sportmode_time": None,
    },
}

bandwidth_data: Dict[str, Any] = {"samples": deque()}
MAX_BANDWIDTH_SAMPLES = 80  # ~8 seconds if sampling every 0.1s

# Counters for message-rate estimation
message_metrics = {
    "lowstate_count": 0,
    "sportmode_count": 0,
    "window_start": time.time(),
    "lowstate_rate": 0.0,
    "sportmode_rate": 0.0,
}

# Track raw connection state for printing
status_snapshot: Dict[str, Any] = {
    "last_status_print": 0.0,
    "last_keepalive_print": 0.0,
}


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _estimate_size(obj: Any) -> int:
    if isinstance(obj, dict):
        return sum(_estimate_size(v) for v in obj.values()) + len(obj) * 10
    if isinstance(obj, list):
        return sum(_estimate_size(item) for item in obj) + len(obj) * 5
    if isinstance(obj, (int, float)):
        return 8
    if isinstance(obj, str):
        return len(obj)
    return 20


def get_datachannel_state(conn: Go2WebRTCConnection) -> str:
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


def track_bandwidth(byte_count: int, disable: bool) -> None:
    if disable:
        return

    current_time = time.time()
    with bandwidth_lock:
        bandwidth_data["samples"].append((current_time, byte_count))
        cutoff = current_time - 3.0
        while bandwidth_data["samples"] and bandwidth_data["samples"][0][0] < cutoff:
            bandwidth_data["samples"].popleft()
        while len(bandwidth_data["samples"]) > MAX_BANDWIDTH_SAMPLES:
            bandwidth_data["samples"].popleft()


def get_bandwidth_kbps(disable: bool) -> float:
    if disable:
        return 0.0
    with bandwidth_lock:
        samples = list(bandwidth_data["samples"])
    if len(samples) < 2:
        return 0.0
    start_time = samples[0][0]
    end_time = samples[-1][0]
    if end_time <= start_time:
        return 0.0
    total_bytes = sum(b for _, b in samples)
    bytes_per_sec = total_bytes / (end_time - start_time)
    return (bytes_per_sec * 8) / 1024.0


def get_latest_states() -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    with memory_lock:
        lowstate = memory_store.get("lowstate")
        sportmode = memory_store.get("sportmode")
    return lowstate, sportmode


def update_connection_details(**kwargs: Any) -> None:
    with memory_lock:
        memory_store["connection_details"].update(kwargs)


def record_error(error_msg: str) -> None:
    with memory_lock:
        memory_store["errors"].append((time.time(), error_msg))


def format_bms(lowstate: Optional[Dict[str, Any]]) -> str:
    if not lowstate:
        return "SOC=--% I=----mA V=--.-V T=--/--°C"
    bms = lowstate.get("bms_state") or {}
    soc = bms.get("soc", "--")
    current = bms.get("current", "--")
    voltage = bms.get("power_v", bms.get("voltage", "--"))
    temps = bms.get("bq_ntc", ["--", "--"])
    temp_mcu = bms.get("mcu_ntc", ["--", "--"])
    temp_str = f"{temps[0]}/{temps[1]}°C" if isinstance(temps, list) else temps
    if isinstance(temp_mcu, list):
        temp_str += f" MCU {temp_mcu[0]}/{temp_mcu[1]}°C"
    return f"SOC={soc}% I={current}mA V={voltage} T={temp_str}"


# ---------------------------------------------------------------------------
# Callbacks invoked from aiortc thread
# ---------------------------------------------------------------------------

async def _patched_wait_datachannel_open(self, timeout: float = 5):
    deadline = time.time() + max(timeout, 5)
    last_log = 0.0
    while time.time() < deadline:
        if getattr(self, "data_channel_opened", False):
            return
        channel = getattr(self, "channel", None)
        state = getattr(channel, "readyState", None)
        now = time.time()
        if now - last_log > 0.5:
            logging.info("Waiting for datachannel readyState=%s", state or "unknown")
            last_log = now
        await asyncio.sleep(0.1)
    raise asyncio.TimeoutError("Data channel did not open in time")


_webrtc_datachannel.WebRTCDataChannel.wait_datachannel_open = _patched_wait_datachannel_open


def lowstate_callback(message: Dict[str, Any], disable_bandwidth: bool) -> None:
    data = message.get("data", {})
    track_bandwidth(_estimate_size(data), disable_bandwidth)
    now = time.time()

    with memory_lock:
        message_metrics["lowstate_count"] += 1
        memory_store["connection_details"]["last_lowstate_time"] = now
        memory_store["lowstate"] = {
            "motor_state": data.get("motor_state", [])[:20],
            "bms_state": data.get("bms_state", {}),
            "imu_state": data.get("imu_state", {}),
            "foot_force": data.get("foot_force", [])[:4],
            "timestamp": now,
        }


def sportmode_callback(message: Dict[str, Any], disable_bandwidth: bool) -> None:
    data = message.get("data", {})
    track_bandwidth(_estimate_size(data), disable_bandwidth)
    now = time.time()

    with memory_lock:
        message_metrics["sportmode_count"] += 1
        memory_store["connection_details"]["last_sportmode_time"] = now
        memory_store["sportmode"] = {
            "mode": data.get("mode"),
            "body_height": data.get("body_height"),
            "position": (data.get("position") or [])[:3],
            "velocity": (data.get("velocity") or [])[:3],
            "imu_state": data.get("imu_state", {}),
            "timestamp": now,
        }


def error_callback(message: Dict[str, Any]) -> None:
    payload = json.dumps(message, indent=2)
    logging.warning("Robot error message: %s", payload)
    record_error(payload)


# ---------------------------------------------------------------------------
# CLI monitor
# ---------------------------------------------------------------------------

async def run_cli_monitor(args: argparse.Namespace) -> None:
    connection_error_detected = {"value": False}

    def exception_handler(loop: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
        msg = context.get("message")
        exc = context.get("exception")
        logging.warning("Asyncio exception handler fired: %s %s", msg, exc)
        if exc and isinstance(exc, AttributeError) and "media" in str(exc):
            logging.warning("Captured aiortc media AttributeError; flagging for reconnect")
            connection_error_detected["value"] = True

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(exception_handler)

    error_handler.handle_error = error_callback

    retry_count = 0
    consecutive_fast_failures = 0
    test_timeout = args.timeout
    test_start_time = time.time()

    keepalive_interval = max(args.keepalive_interval, 3.0)
    status_interval = max(args.status_interval, 0.5)

    while True:
        # Check overall test timeout before attempting connection
        elapsed_total = time.time() - test_start_time
        if elapsed_total >= test_timeout:
            logging.info("Test timeout reached (%.1fs total), exiting", test_timeout)
            return
        connection_start_wall = time.time()
        conn: Optional[Go2WebRTCConnection] = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
        try:
            logging.info("Connecting (attempt #%d)...", retry_count + 1)
            connection_error_detected["value"] = False

            async def connect_with_monitoring():
                connect_task = asyncio.create_task(conn.connect())
                while not connect_task.done():
                    if connection_error_detected["value"]:
                        connect_task.cancel()
                        raise RuntimeError("Background task error detected during connect()")
                    await asyncio.sleep(0.05)
                return await connect_task

            await asyncio.wait_for(connect_with_monitoring(), timeout=60.0)

            pc = getattr(conn, "pc", None)
            if not pc:
                raise RuntimeError("Peer connection not created")

            start_wait = loop.time()
            while True:
                if connection_error_detected["value"]:
                    raise RuntimeError("Background task error detected post-connect")
                state = getattr(pc, "connectionState", None)
                if state == "connected":
                    if pc.remoteDescription:
                        break
                    raise RuntimeError("Connected but no remote description")
                if state in {"failed", "disconnected", "closed"}:
                    raise RuntimeError(f"Connection state is {state}")
                if loop.time() - start_wait > 3.0:
                    raise RuntimeError("Connection state not progressing")
                await asyncio.sleep(0.05)

            logging.info("Connection established; disabling traffic saving...")
            try:
                await conn.datachannel.disableTrafficSaving(True)
                logging.info("Initial keepalive sent (traffic saving disabled)")
            except Exception as exc:
                logging.warning("Initial keepalive failed: %s", exc)

            # Register callbacks
            conn.datachannel.pub_sub.subscribe(
                RTC_TOPIC["LOW_STATE"],
                lambda message: lowstate_callback(message, args.disable_bandwidth),
            )
            conn.datachannel.pub_sub.subscribe(
                RTC_TOPIC["LF_SPORT_MOD_STATE"],
                lambda message: sportmode_callback(message, args.disable_bandwidth),
            )

            connection_start_time = loop.time()
            connection_start_wall = time.time()
            last_keepalive = time.time()

            while True:
                # Check overall test timeout
                elapsed_total = time.time() - test_start_time
                if elapsed_total >= test_timeout:
                    logging.info("Test timeout reached (%.1fs total), shutting down gracefully", test_timeout)
                    if conn:
                        try:
                            await asyncio.wait_for(conn.disconnect(), timeout=5.0)
                        except Exception:
                            pass
                    return  # Exit the entire function
                if connection_error_detected["value"]:
                    raise RuntimeError("Background task error detected during monitoring")

                await asyncio.sleep(1.0)

                now = time.time()
                uptime = loop.time() - connection_start_time

                pc_state = getattr(pc, "connectionState", "unknown")
                ice_state = getattr(pc, "iceConnectionState", "unknown")
                sig_state = getattr(pc, "signalingState", "unknown")
                dc_state = get_datachannel_state(conn)
                is_connected = getattr(conn, "isConnected", False)

                # Message rates
                with memory_lock:
                    window_duration = now - message_metrics["window_start"]
                    if window_duration >= 1.0:
                        message_metrics["lowstate_rate"] = (
                            message_metrics["lowstate_count"] / window_duration
                        )
                        message_metrics["sportmode_rate"] = (
                            message_metrics["sportmode_count"] / window_duration
                        )
                        message_metrics["lowstate_count"] = 0
                        message_metrics["sportmode_count"] = 0
                        message_metrics["window_start"] = now
                    low_rate = message_metrics["lowstate_rate"]
                    sport_rate = message_metrics["sportmode_rate"]

                    last_low_msg = memory_store["connection_details"]["last_lowstate_time"]
                    last_sport_msg = memory_store["connection_details"]["last_sportmode_time"]

                    memory_store["connection_details"].update(
                        ice_connection_state=ice_state,
                        signaling_state=sig_state,
                        connection_state=pc_state,
                        datachannel_state=dc_state,
                        connection_uptime=uptime,
                        lowstate_msg_rate=low_rate,
                        sportmode_msg_rate=sport_rate,
                    )

                if (
                    (last_low_msg and now - last_low_msg > 10)
                    or (last_sport_msg and now - last_sport_msg > 10)
                ):
                    logging.warning("No sensor messages in >10s; marking connection as dead")
                    is_connected = False

                if (
                    not is_connected
                    or pc_state in {"closed", "failed", "disconnected"}
                    or ice_state in {"closed", "failed", "disconnected"}
                ):
                    raise RuntimeError(
                        f"Connection lost (pc={pc_state}, ice={ice_state}, dc={dc_state})"
                    )

                if now - last_keepalive >= keepalive_interval:
                    try:
                        await conn.datachannel.disableTrafficSaving(True)
                        last_keepalive = now
                        update_connection_details(last_keepalive=last_keepalive)
                        logging.debug("Keepalive sent at %.1fs uptime", uptime)
                    except Exception as exc:
                        logging.warning("Keepalive failed: %s", exc)

                # Status print
                if now - status_snapshot["last_status_print"] >= status_interval:
                    status_snapshot["last_status_print"] = now
                    lowstate, sportmode = get_latest_states()
                    kbps = get_bandwidth_kbps(args.disable_bandwidth)
                    bms_line = format_bms(lowstate)
                    print(
                        f"[{datetime.now():%H:%M:%S}] uptime={uptime:5.1f}s "
                        f"pc={pc_state:<11} ice={ice_state:<11} dc={dc_state:<9} "
                        f"low={low_rate:4.1f}/s sport={sport_rate:4.1f}/s "
                        f"bw={kbps:7.1f} kb/s | {bms_line}",
                        flush=True,
                    )

        except KeyboardInterrupt:
            logging.info("Keyboard interrupt received, shutting down.")
            if conn:
                try:
                    await asyncio.wait_for(conn.disconnect(), timeout=5.0)
                except Exception:
                    pass
            break

        except Exception as exc:
            duration = time.time() - connection_start_wall
            retry_count += 1
            if duration < 10:
                consecutive_fast_failures += 1
            else:
                consecutive_fast_failures = 0
            delay = min(30, max(2, consecutive_fast_failures * 5))
            logging.warning("Connection failed after %.1fs: %s", duration, exc)
            logging.warning("Reconnecting in %ds (attempt #%d)", delay, retry_count + 1)
            if conn:
                try:
                    await asyncio.wait_for(conn.disconnect(), timeout=5.0)
                except Exception:
                    pass
            await asyncio.sleep(delay)
            continue

        finally:
            connection_error_detected["value"] = False


def main() -> None:
    parser = argparse.ArgumentParser(description="Go2 Sensor Monitor (CLI)")
    parser.add_argument("--keepalive-interval", type=float, default=8.0)
    parser.add_argument("--status-interval", type=float, default=1.0)
    parser.add_argument("--disable-bandwidth", action="store_true")
    parser.add_argument("--log-file", type=str, default=None)
    parser.add_argument("--timeout", type=float, default=60.0, help="Test timeout in seconds (default: 60)")
    args = parser.parse_args()

    setup_logging(args.log_file)

    # Replace early patch with logging version
    def patched_print_status(status_type: str, status_message: str) -> None:
        # Emojis already stripped by early patch, but strip again to be safe
        clean_type = emoji_pattern.sub('', str(status_type))
        clean_msg = emoji_pattern.sub('', str(status_message))
        logging.info("%s: %s", clean_type, clean_msg)

    _util.print_status = patched_print_status

    try:
        asyncio.run(run_cli_monitor(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

