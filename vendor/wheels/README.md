This folder can contain offline Python wheels so `start.bat` can install dependencies without internet.

To populate (on a machine with internet):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\\download_deps.ps1
```

