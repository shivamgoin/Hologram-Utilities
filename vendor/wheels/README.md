This folder can contain offline Python wheels so the launcher scripts can install dependencies without internet.

To populate (on a machine with internet):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\\win\\download_deps.ps1
```
