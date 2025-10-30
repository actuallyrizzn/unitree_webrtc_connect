# Script to install Visual C++ Build Tools
# This script needs to be run as Administrator

Write-Host "Installing Visual C++ Build Tools via Chocolatey..."
Write-Host "Note: This requires Administrator privileges"

# Refresh PATH to ensure Chocolatey is available
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

# Install Visual C++ Build Tools
choco install visualcppbuildtools -y

Write-Host ""
Write-Host "After installation, you may need to restart your terminal/shell or refresh the environment."
Write-Host "Then you can建筑 run: pip install -r requirements.txt"

