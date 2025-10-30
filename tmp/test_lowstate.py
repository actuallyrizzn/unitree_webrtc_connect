import builtins as _builtins
import re as _re
_builtin_print = _builtins.print
emoji_pattern = _re.compile('[\U0001F300-\U0001FAD6\U0001FAE0-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF]+', flags=_re.UNICODE)
def no_emoji_print(*args, **kwargs):
    args = tuple(_emoji_patch(a) for a in args)
    return _builtin_print(*args, **kwargs)
def _emoji_patch(x):
    try: return emoji_pattern.sub('', str(x))
    except Exception: return str(x)
_builtins.print = no_emoji_print

import logging as _logging
class NoEmojiLog(_logging.StreamHandler):
    def emit(self, record):
        record.msg = _emoji_patch(record.msg)
        super().emit(record)
for h in list(_logging.root.handlers):
    _logging.root.removeHandler(h)
_logging.basicConfig(level=_logging.INFO, handlers=[NoEmojiLog()])

import asyncio
import logging
import sys
import time
from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC

# Enable logging for debugging
# logging.basicConfig(level=logging.INFO) # This line is now redundant due to the new_code, but keeping it as per instructions.

def print_status(status_type, status_message):
    current_time = time.strftime("%H:%M:%S")
    _builtin_print(f"[STATUS] {status_type:<25}: {status_message:<15} ({current_time})")

import sys
sys.path.insert(0, '.')
import go2_webrtc_driver.util as _util
_util.print_status = print_status


def display_data(message):

    # Extracting data from the message
    imu_state = message['imu_state']['rpy']
    motor_state = message['motor_state']
    bms_state = message['bms_state']
    foot_force = message['foot_force']
    temperature_ntc1 = message['temperature_ntc1']
    power_v = message['power_v']

    # Clear the entire screen and reset cursor position to top
    sys.stdout.write("\033[H\033[J")

    # Print the Go2 Robot Status
    _builtin_print("Go2 Robot Status (LowState)")
    _builtin_print("===========================")

    # IMU State (RPY)
    _builtin_print(f"IMU - RPY: Roll: {imu_state[0]}, Pitch: {imu_state[1]}, Yaw: {imu_state[2]}")

  # Compact Motor States Display (Each motor on one line)
    _builtin_print("\nMotor States (q, Temperature, Lost):")
    _builtin_print("------------------------------------------------------------")
    for i, motor in enumerate(motor_state):
        # Display motor info in a single line
        _builtin_print(f"Motor {i + 1:2}: q={motor['q']:.4f}, Temp={motor['temperature']}°C, Lost={motor['lost']}")

    # BMS (Battery Management System) State
    _builtin_print("\nBattery Management System (BMS) State:")
    _builtin_print(f"  Version: {bms_state['version_high']}.{bms_state['version_low']}")
    _builtin_print(f"  SOC (State of Charge): {bms_state['soc']}%")
    _builtin_print(f"  Current: {bms_state['current']} mA")
    _builtin_print(f"  Cycle Count: {bms_state['cycle']}")
    _builtin_print(f"  BQ NTC: {bms_state['bq_ntc']}°C")
    _builtin_print(f"  MCU NTC: {bms_state['mcu_ntc']}°C")

    # Foot Force
    _builtin_print(f"\nFoot Force: {foot_force}")

    # Additional Sensors
    _builtin_print(f"Temperature NTC1: {temperature_ntc1}°C")
    _builtin_print(f"Power Voltage: {power_v}V")

    # Optionally, flush to ensure immediate output
    sys.stdout.flush()



async def main():
    try:
        # Choose a connection method (uncomment the correct one)
        # conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip="192.168.8.181")
        # conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalSTA, serialNumber="B42D2000XXXXXXXX")
        # conn = Go2WebRTCConnection(WebRTCConnectionMethod.Remote, serialNumber="B42D2000XXXXXXXX", username="email@gmail.com", password="pass")
        conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)

        # Connect to the WebRTC service.
        await conn.connect()


        # Define a callback function to handle lowstate status when received.
        def lowstate_callback(message):
            current_message = message['data']
            
            display_data(current_message)


        # Subscribe to the sportmode status data and use the callback function to process incoming messages.
        conn.datachannel.pub_sub.subscribe(RTC_TOPIC['LOW_STATE'], lowstate_callback)


        # Keep the program running to allow event handling for 1 hour.
        await asyncio.sleep(3600)

    except ValueError as e:
        # Log any value errors that occur during the process.
        _logging.error(f"An error occurred: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Handle Ctrl+C to exit gracefully.
        _builtin_print("\nProgram interrupted by user")
        sys.exit(0)
