# Start CSE Advisor automatically when you log in to Windows.
# Run once from this folder:   powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1
# Remove it later with:        powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1 -Remove
param([switch]$Remove)

$name = "CSE Advisor"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed the '$name' startup task."
    return
}
$bat = Join-Path $PSScriptRoot "start.bat"
if (-not (Test-Path $bat)) { throw "start.bat not found next to this script." }
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Description "Starts the CSE Advisor backend and opens http://localhost:8000" -Force | Out-Null
Write-Host "Done. '$name' will start when you log in. (The app listens on this PC only.)"
