@echo off
REM ============================================================================
REM                            用户配置区域
REM ============================================================================

REM Anaconda 安装路径
set CONDA_BASE_PATH=D:\anaconda

REM 您的工作目录
set WORK_DIR=E:\Desktop2\LMArena-main

REM 您要激活的 Conda 环境名称
set CONDA_ENV_NAME=python3_11_5

REM ！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！
REM  重要：已根据您的日志，将 API 服务器端口更新为 5102。
REM ！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！！
set API_SERVER_PORT=5102


REM ============================================================================
REM                            脚本执行区域
REM          (此版本已修正端口并简化了临时脚本以确保稳定)
REM ============================================================================

REM 检查 Conda 激活脚本是否存在
if not exist "%CONDA_BASE_PATH%\Scripts\activate.bat" (
    echo.
    echo 错误：无法在以下路径找到 Conda 的激活脚本:
    echo "%CONDA_BASE_PATH%\Scripts\activate.bat"
    echo.
    pause
    exit /b
)

REM --- 创建用于 API Server 的、最简化的临时脚本 ---
(
    echo @echo off
    echo call %CONDA_BASE_PATH%\Scripts\activate.bat %CONDA_ENV_NAME%
    echo cd /d %WORK_DIR%
    echo python api_server.py
) > _run_api_server.bat

REM --- 启动 API Server 窗口 ---
echo Starting API Server window...
start "LMArena - API Server" cmd /k _run_api_server.bat


REM --- 智能等待循环：检查 API 服务器端口是否已在监听 ---
echo.
echo ============================================================================
echo Waiting for API Server to start on port %API_SERVER_PORT%...
echo This might take a moment.
echo ============================================================================

:check_port_loop
echo Checking network status for port %API_SERVER_PORT%...
REM 使用 netstat 命令检查指定端口是否处于 LISTENING 状态
netstat -an | findstr "LISTENING" | findstr ":%API_SERVER_PORT%" > nul

REM 如果 findstr 找不到 (errorlevel不为0)，则等待2秒后重试
if %errorlevel% neq 0 (
    timeout /t 2 /nobreak > nul
    goto check_port_loop
)

echo.
echo ============================================================================
echo Server is running! Proceeding to start the ID Updater.
echo ============================================================================
echo.


REM --- 服务器已就绪，现在创建并启动 ID Updater 的临时脚本 ---
(
    echo @echo off
    echo call %CONDA_BASE_PATH%\Scripts\activate.bat %CONDA_ENV_NAME%
    echo cd /d %WORK_DIR%
    echo python id_updater.py
) > _run_id_updater.bat

echo Starting ID Updater window...
start "LMArena - ID Updater" cmd /k _run_id_updater.bat


echo.
echo Main batch script has finished its tasks.
echo Note: Temporary files "_run_api_server.bat" and "_run_id_updater.bat" have been created.