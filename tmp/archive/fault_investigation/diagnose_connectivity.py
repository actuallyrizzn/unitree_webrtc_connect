"""
Diagnostic script to check if the Go2 Air robot has any network connectivity.

This checks basic network functions without requiring WebRTC connection.
"""
import subprocess
import sys
import time

def ping_host(host, count=4):
    """Ping a host and return True if successful."""
    try:
        # Windows ping command
        result = subprocess.run(
            ['ping', '-n', str(count), host],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0
    except:
        return False

def check_wifi_networks():
    """Check for Go2 WiFi networks."""
    try:
        # Windows netsh command to list WiFi networks
        result = subprocess.run(
            ['netsh', 'wlan', 'show', 'networks'],
            capture_output=True,
            text=True,
            timeout=10
        )
        output = result.stdout.lower()
        # Look for Unitree or Go2 in network names
        if 'unitree' in output or 'go2' in output:
            return True
        return False
    except:
        return False

def main():
    print("="*60)
    print("GO2 AIR CONNECTIVITY DIAGNOSTIC")
    print("="*60)
    print("\nThis script checks basic network connectivity.")
    print("It does NOT attempt WebRTC connection.\n")
    
    # Check 1: Ping test
    print("1. Testing ping to 192.168.12.1...")
    if ping_host("192.168.12.1", count=2):
        print("   ✓ Robot responds to ping")
        print("   → Robot is on network and partially functional")
        print("   → WebRTC service may be crashed/not starting")
    else:
        print("   ✗ Robot does NOT respond to ping")
        print("   → Robot may be completely off or network is down")
    
    # Check 2: WiFi network
    print("\n2. Checking for Go2 WiFi networks...")
    if check_wifi_networks():
        print("   ✓ Go2 WiFi network detected")
        print("   → Robot is broadcasting WiFi")
        print("   → Try connecting to the WiFi network")
    else:
        print("   ✗ No Go2 WiFi network found")
        print("   → Robot may not be in AP mode or WiFi is disabled")
    
    # Check 3: Try HTTP port (if ping works)
    print("\n3. Checking HTTP ports...")
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        
        # Check port 8081 (old SDP endpoint)
        result_8081 = sock.connect_ex(('192.168.12.1', 8081))
        sock.close()
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        # Check port 9991 (new encrypted endpoint)
        result_9991 = sock.connect_ex(('192.168.12.1', 9991))
        sock.close()
        
        if result_8081 == 0:
            print("   ✓ Port 8081 is open (old SDP endpoint)")
        else:
            print("   ✗ Port 8081 is closed")
            
        if result_9991 == 0:
            print("   ✓ Port 9991 is open (encrypted SDP endpoint)")
        else:
            print("   ✗ Port 9991 is closed")
            
        if result_8081 != 0 and result_9991 != 0:
            print("   → WebRTC service ports are not responding")
            print("   → Service may have crashed or not started")
    except Exception as e:
        print(f"   Error checking ports: {e}")
    
    print("\n" + "="*60)
    print("DIAGNOSIS COMPLETE")
    print("="*60)
    print("\nIf ping works but WebRTC doesn't:")
    print("  - The robot is partially functional")
    print("  - WebRTC service likely crashed or won't start")
    print("  - May need firmware reflash or service restart")
    print("\nIf nothing responds:")
    print("  - Robot may be completely off")
    print("  - Try extended power cycle (remove battery 30-60 min)")
    print("  - May need professional repair")

if __name__ == "__main__":
    main()

