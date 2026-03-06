Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\gmcmillan\Desktop\AI Projects\ACV AI Agent\high-intensity-dev\tools"
WshShell.Run "pythonw workstate-dashboard.py --logo images\logo.png", 0, False
