#!/usr/bin/env python3
"""Check interface metrics"""
import subprocess

r = subprocess.run(['powershell', '-NoProfile', '-Command', 
    'Get-NetIPInterface | Where-Object {$_.ConnectionState -eq "Connected"} | Sort-Object InterfaceMetric | Select-Object InterfaceAlias, InterfaceMetric | Format-Table -AutoSize'],
    capture_output=True, text=True)
print(r.stdout)





