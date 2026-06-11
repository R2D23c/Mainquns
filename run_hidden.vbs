' run_hidden.vbs — wrapper для Task Scheduler чтобы скрыть cmd окно.
'
' Task Scheduler с LogonType=Interactive по умолчанию создаёт ВИДИМОЕ окно
' для cmd.exe — это мешает pyautogui clicks во время warmup.py UI install
' (cmd может перехватить focus или просто визуально перекрыть LS).
'
' VBS через WScript.Shell.Run с windowStyle=0 запускает процесс полностью
' невидимо. Используется для LsWatchdog задачи (она тикает каждые 5 мин и
' раньше каждый раз показывала мерцающий cmd).
'
' Usage:
'   wscript.exe "run_hidden.vbs" "C:\warmup\run_watchdog.bat"

If WScript.Arguments.Count > 0 Then
    Set objShell = CreateObject("WScript.Shell")
    ' windowStyle=0 (hidden), waitOnReturn=False (не блокируем wscript)
    objShell.Run """" & WScript.Arguments(0) & """", 0, False
End If
