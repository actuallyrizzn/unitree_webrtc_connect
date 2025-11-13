#!/usr/bin/env python3
"""Network diagnostic script for robot connection"""
import socket
import subprocess
import sys

print("=" * 60)
print("NETWORK DIAGNOSTICS")
print("=" * 60)

# 1. Check local IP addresses
print("\n1. LOCAL IP ADDRESSES:")
try:
    import netifaces
    for interface in netifaces.interfaces():
        addrs = netifaces.ifaddresses(interface)
        if netifaces.AF_INET in addrs:
            for addr in addrs[netifaces.AF_INET]:
                ip = addr.get('addr', '')
                if ip and not ip.startswith('127.'):
                    print(f"  {interface}: {ip}")
except ImportError:
    # Fallback to socket method
    hostname = socket.gethostname()
    print(f"  Hostname: {hostname}")
    try:
        local_ip = socket.gethostbyname(hostname)
        print(f"  Primary IP: {local_ip}")
    except:
        pass

# 2. Check robot reachability
print("\n2. ROBOT REACHABILITY (192.168.12.1):")
robot_ip = "192.168.12.1"

# Ping test
try:
    result = subprocess.run(['ping', '-n', '2', robot_ip], 
                          capture_output=True, text=True, timeout=5)
    if 'TTL' in result.stdout or 'time=' in result.stdout:
        print("  [OK] Ping successful")
        # Extract latency
        for line in result.stdout.split('\n'):
            if 'time=' in line or 'time<' in line:
                print(f"  {line.strip()}")
    else:
        print("  [FAIL] Ping failed - no response")
        print(f"  Output: {result.stdout[:200]}")
except Exception as e:
    print(f"  [ERROR] Ping test failed: {e}")

# 3. Check port 9991
print("\n3. PORT 9991 ACCESS:")
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    result = s.connect_ex((robot_ip, 9991))
    s.close()
    if result == 0:
        print("  [OK] Port 9991 is OPEN")
    else:
        print(f"  [FAIL] Port 9991 is CLOSED (error code: {result})")
except Exception as e:
    print(f"  [ERROR] Port test failed: {e}")

# 4. Test HTTP connection
print("\n4. HTTP CONNECTION TEST (port 9991):")
try:
    import urllib.request
    url = f"http://{robot_ip}:9991/con_notify"
    req = urllib.request.Request(url)
    req.add_header('User-Agent', 'Python-diagnostic')
    with urllib.request.urlopen(req, timeout=3) as response:
        status = response.getcode()
        data = response.read()[:100]
        print(f"  [OK] HTTP {status} - Response received ({len(data)} bytes)")
        print(f"  Response preview: {data[:50]}...")
except urllib.error.URLError as e:
    print(f"  [FAIL] HTTP connection failed: {e}")
except Exception as e:
    print(f"  [ERROR] HTTP test failed: {e}")

# 5. Check for 192.168.12.x network
print("\n5. NETWORK INTERFACE CHECK:")
print("  Looking for 192.168.12.x address...")
try:
    import netifaces
    found = False
    for interface in netifaces.interfaces():
        addrs = netifaces.ifaddresses(interface)
        if netifaces.AF_INET in addrs:
            for addr in addrs[netifaces.AF_INET]:
                ip = addr.get('addr', '')
                if ip and ip.startswith('192.168.12.'):
                    print(f"  [OK] Found 192.168.12.x on {interface}: {ip}")
                    found = True
    if not found:
        print("  [FAIL] No 192.168.12.x address found - NOT connected to robot network")
except ImportError:
    print("  [WARN] netifaces not available, using fallback method")
    # Try socket method
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((robot_ip, 80))
        local_ip = s.getsockname()[0]
        s.close()
        if local_ip.startswith('192.168.12.'):
            print(f"  [OK] Found 192.168.12.x address: {local_ip}")
        else:
            print(f"  [WARN] Local IP is {local_ip} (not on 192.168.12.x network)")
    except Exception as e:
        print(f"  [ERROR] Could not determine local IP: {e}")

print("\n" + "=" * 60)
print("DIAGNOSTICS COMPLETE")
print("=" * 60)





