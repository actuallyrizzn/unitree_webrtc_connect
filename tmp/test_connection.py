import builtins as _builtins
import re as _re
_builtin_print = _builtins.print
emoji_pattern = _re.compile('[\U0001F300-\U0001FAD6\U0001FAE0-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF]+', flags=_re.UNICODE)
def no_emoji_print(*args, **kwargs):
    args = tuple(emoji_pattern.sub('', str(a)) for a in args)
    return _builtin_print(*args, **kwargs)
_builtins.print = no_emoji_print

import asyncio
import logging
import json
import sys
import time
from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC

# Patch print_status
def print_status(status_type, status_message):
    current_time = time.strftime("%H:%M:%S")
    _builtin_print(f"[{current_time}] {status_type}: {status_message}")

import go2_webrtc_driver.util as _util
_util.print_status = print_status

logging.basicConfig(level=logging.WARNING)
    
async def main():
    try:
        print("="*60)
        print("GO2 CONNECTION TEST")
        print("="*60)
        
        # Connect to Go2 in AP mode
        print("\n[1/3] Initializing connection to Go2 (AP Mode - 192.168.12.1)...")
        conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
        
        print("[2/3] Connecting to Go2...")
        await conn.connect()
        
        # Wait a moment for connection to stabilize
        await asyncio.sleep(2)
        
        print("[3/3] Testing data channel - querying motion mode...")
        
        # Simple query to test if data channel works
        response = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["MOTION_SWITCHER"], 
            {"api_id": 1001},
            timeout=5
        )
        
        if response and 'data' in response:
            if response['data']['header']['status']['code'] == 0:
                data = json.loads(response['data']['data'])
                current_mode = data.get('name', 'unknown')
                print(f"\n" + "="*60)
                print(f"SUCCESS! Go2 is connected and responding.")
                print(f"Current motion mode: {current_mode}")
                print("="*60)
                print("\nConnection is working! You can now proceed with your work.")
            else:
                print(f"\nWarning: Got response but status code: {response['data']['header']['status']['code']}")
        else:
            print("\nWarning: Connection established but no data received from query.")
            
        await conn.disconnect()
        
    except Exception as e:
        print(f"\n{'='*60}")
        print(f"ERROR: Connection failed")
        print(f"{'='*60}")
        print(f"Error details: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
        sys.exit(0)

