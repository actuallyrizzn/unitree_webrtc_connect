import requests

print("Checking Go2 status...")
print("Testing port 9991 (new method)...")

try:
    response = requests.post("http://192.168.12.1:9991/con_notify", timeout=5)
    print(f"Port 9991 response: {response.status_code}")
    print(f"Content: {response.text[:200]}")
except Exception as e:
    print(f"Port 9991 error: {e}")

print("\nTesting port 8081 (old method)...")
try:
    response = requests.post("http://192.168.12.1:8081/offer", json={"test": "test"}, timeout=5)
    print(f"Port 8081 response: {response.status_code}")
except Exception as e:
    print(f"Port 8081 error: {e}")

