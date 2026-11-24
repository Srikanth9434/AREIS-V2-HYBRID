@echo off
setlocal
cd /d "%~dp0"

set "SERVER=%~dp0llama-cuda\llama-server.exe"
if not exist "%SERVER%" set "SERVER=C:\Users\srikanth\Desktop\AREIS_PP\dist\cuda\llama-b10853-bin-win-cuda-12.4-x64\llama-server.exe"

set "MODEL=%~dp0models\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
set "CTX=2048"

if not exist "%SERVER%" (
  echo llama-server.exe not found
  pause
  exit /b 1
)
if not exist "%MODEL%" (
  echo 7B model shard 1 not found in models\
  pause
  exit /b 1
)
if not exist "%~dp0models\qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf" (
  echo Missing shard 2 of the 7B model. Wait for the download to finish.
  pause
  exit /b 1
)

echo Starting Qwen2.5-7B on port 8090 (no browser UI)
"%SERVER%" -m "%MODEL%" -ngl 99 -c %CTX% --port 8090 --host 127.0.0.1 --no-webui
pause
