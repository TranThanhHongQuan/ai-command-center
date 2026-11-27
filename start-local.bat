@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Tao moi truong ao Python...
  py -3 -m venv .venv || python -m venv .venv || (echo Can Python 3.11+ & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || exit /b 1
if not defined AICC_DATA_DIR set "AICC_DATA_DIR=%~dp0data"
if not defined AICC_SCHEDULER set "AICC_SCHEDULER=0"

".venv\Scripts\python.exe" -c "import os,sqlite3,sys;p=os.path.join(os.environ['AICC_DATA_DIR'],'newsroom.sqlite3');sys.exit(0 if os.path.exists(p) and sqlite3.connect(p).execute(\"select count(*) from sqlite_master where name='admin_users'\").fetchone()[0] and sqlite3.connect(p).execute('select count(*) from admin_users').fetchone()[0] else 1)"
if errorlevel 1 (
  echo Chua co tai khoan quan tri. Dat mat khau moi ^(toi thieu 12 ky tu, khong luu vao file^):
  for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "$s=Read-Host -AsSecureString; [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))"`) do set "AICC_ADMIN_PASSWORD=%%p"
)
echo Dang nhap tai http://127.0.0.1:8787/login  ^(tai khoan: %AICC_ADMIN_USER%, mac dinh admin^)
echo Scheduler: AICC_SCHEDULER=%AICC_SCHEDULER% ^(dat 1 de bat quet tu dong moi 60 phut^)
".venv\Scripts\python.exe" -m uvicorn server:app --host 127.0.0.1 --port 8787
