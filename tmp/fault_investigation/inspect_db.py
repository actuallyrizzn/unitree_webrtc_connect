"""Quick script to inspect the sensor database."""
import sqlite3
import json
import os
from datetime import datetime

db_path = os.path.join(os.path.dirname(__file__), 'sensor_data.db')

if not os.path.exists(db_path):
    print("Database file does not exist!")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print("Tables:", [t[0] for t in tables])
print()

# Check row counts
cursor.execute("SELECT COUNT(*) FROM lowstate")
lowstate_count = cursor.fetchone()[0]
print(f"Lowstate rows: {lowstate_count}")

cursor.execute("SELECT COUNT(*) FROM sportmodestate")
sportmodestate_count = cursor.fetchone()[0]
print(f"Sportmodestate rows: {sportmodestate_count}")

cursor.execute("SELECT COUNT(*) FROM errors")
error_count = cursor.fetchone()[0]
print(f"Error rows: {error_count}")

cursor.execute("SELECT COUNT(*) FROM connection_state")
conn_state_count = cursor.fetchone()[0]
print(f"Connection state rows: {conn_state_count}")
print()

# Get latest lowstate
if lowstate_count > 0:
    cursor.execute("SELECT timestamp, motor_state, bms_state FROM lowstate ORDER BY timestamp DESC LIMIT 1")
    row = cursor.fetchone()
    if row:
        print("Latest Lowstate:")
        print(f"  Timestamp: {datetime.fromtimestamp(row[0]).strftime('%Y-%m-%d %H:%M:%S')}")
        if row[1]:
            motors = json.loads(row[1])
            print(f"  Motor count: {len(motors)}")
            if motors:
                print(f"  First motor temp: {motors[0].get('temperature', 'N/A')}°C")
        if row[2]:
            bms = json.loads(row[2])
            print(f"  BMS SOC: {bms.get('soc', 'N/A')}%")
        print()

# Get latest sportmodestate
if sportmodestate_count > 0:
    cursor.execute("SELECT timestamp, mode, body_height FROM sportmodestate ORDER BY timestamp DESC LIMIT 1")
    row = cursor.fetchone()
    if row:
        print("Latest Sportmodestate:")
        print(f"  Timestamp: {datetime.fromtimestamp(row[1]).strftime('%Y-%m-%d %H:%M:%S') if row[1] else 'N/A'}")
        print(f"  Mode: {row[1]}")
        print(f"  Body Height: {row[2]}")
        print()

# Get connection states
if conn_state_count > 0:
    cursor.execute("SELECT state, timestamp FROM connection_state ORDER BY timestamp DESC LIMIT 5")
    rows = cursor.fetchall()
    print("Recent Connection States:")
    for row in rows:
        print(f"  {row[0]} at {datetime.fromtimestamp(row[1]).strftime('%Y-%m-%d %H:%M:%S')}")
    print()

# Get errors
if error_count > 0:
    cursor.execute("SELECT timestamp, error_source, error_code FROM errors ORDER BY timestamp DESC LIMIT 5")
    rows = cursor.fetchall()
    print("Recent Errors:")
    for row in rows:
        print(f"  Source: {row[1]}, Code: {row[2]} at {datetime.fromtimestamp(row[0]).strftime('%Y-%m-%d %H:%M:%S')}")
    print()

conn.close()

