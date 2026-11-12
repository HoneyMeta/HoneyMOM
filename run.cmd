@echo off
rem HoneyMOM server (http://localhost:8900). Data source = VirtualFactory MQTT on :1883. Close this window to stop.
cd /d "%~dp0"
set "PY=python"
if defined MOM_PYTHON set "PY=%MOM_PYTHON%"
if not exist "web\dist\index.html" (
  echo Building web UI for the first time...
  pushd web && call npm ci && call npm run build && popd
)
echo HoneyMOM starting... open http://localhost:8900  (first start: create the admin account in the browser)
"%PY%" -m honeymom serve
pause
