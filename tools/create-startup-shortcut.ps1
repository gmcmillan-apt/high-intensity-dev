$ws = New-Object -ComObject WScript.Shell
$shortcut = $ws.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Workstate Dashboard.lnk")
$shortcut.TargetPath = "C:\Users\gmcmillan\Desktop\AI Projects\ACV AI Agent\high-intensity-dev\tools\start-dashboard.vbs"
$shortcut.WorkingDirectory = "C:\Users\gmcmillan\Desktop\AI Projects\ACV AI Agent\high-intensity-dev\tools"
$shortcut.Description = "Workstate Dashboard auto-start"
$shortcut.Save()
Write-Host "Startup shortcut created successfully."
