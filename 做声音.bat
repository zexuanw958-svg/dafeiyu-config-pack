@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "tools\breeze\make_voice.py" goto :not_extracted

rem 找 AstrBot 自带的 Python：先找启动器的数据目录（默认在用户目录下，「高级」里改过的记在 APPDATA 的设置文件里）
set "PY="
rem 用 Skill 装的：AstrBot 在工作目录（配置包的上一层）的 uv\tools\astrbot 里
for %%w in ("%~dp0..") do if exist "%%~fw\uv\tools\astrbot\Scripts\python.exe" set "PY=%%~fw\uv\tools\astrbot\Scripts\python.exe"
if defined PY goto :run
set "DATA=%USERPROFILE%\.astrbot_launcher"
set "OVERRIDE=%APPDATA%\com.github.raven95676.astrbot-launcher\.data-dir.json"
if exist "%OVERRIDE%" for /f usebackq^ tokens^=4^ delims^=^" %%d in ("%OVERRIDE%") do set "DATA=%%d"
set "DATA=%DATA:\\=\%"
call :find_in "%DATA%"
if not defined PY call :find_in "%USERPROFILE%\.astrbot_launcher"
rem 再找电脑上自己装的 Python；WindowsApps 里那个是应用商店的空壳，跳过
if not defined PY for /f "delims=" %%p in ('where python 2^>nul') do call :try_python "%%p"
if not defined PY goto :no_python

:run
echo 用的 Python：%PY%
"%PY%" -X utf8 tools\breeze\make_voice.py %*
pause
exit /b

:find_in
for /d %%i in ("%~1\instances\*") do if exist "%%i\venv\Scripts\python.exe" set "PY=%%i\venv\Scripts\python.exe"
exit /b

:try_python
if defined PY exit /b
set "CAND=%~1"
if /i not "%CAND:\WindowsApps\=%"=="%CAND%" exit /b
set "PY=%CAND%"
exit /b

:not_extracted
echo 没找到 tools\breeze\make_voice.py。
echo 先把配置包压缩包整个解压出来，再双击解压出来的文件夹里的「做声音.bat」。
pause
exit /b 1

:no_python
echo 没找到 AstrBot 自带的 Python，做声音的工具没法运行。
echo 先确认 AstrBot 是用「AstrBot 启动器」装的，而且实例已经部署、启动过一次。
echo AstrBot 不是用启动器装的：先到 Python 官网装一个 Python，安装第一页勾上「Add python.exe to PATH」，再双击本文件。
pause
exit /b 1
