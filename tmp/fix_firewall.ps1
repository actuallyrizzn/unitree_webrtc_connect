# Add firewall rules for Python WebRTC
Write-Host "Adding Windows Firewall rules for Python WebRTC..."

netsh advfirewall firewall add rule name="Python WebRTC Inbound" dir=in action=allow program="C:\Program Files\Python313\python.exe" enable=yes
netsh advfirewall firewall add rule name="Python WebRTC Outbound" dir=out action=allow program="C:\Program Files\Python313\python.exe" enable=yes

Write-Host "Firewall rules added successfully!"
Write-Host "Press any key to close..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

