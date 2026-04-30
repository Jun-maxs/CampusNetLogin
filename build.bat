@echo off
chcp 65001 >nul
echo ========================================
echo   校园网登录工具 - 打包脚本
echo ========================================
echo.

REM 检查 PyInstaller
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [1/3] 安装 PyInstaller...
    pip install pyinstaller
) else (
    echo [1/3] PyInstaller 已安装
)

echo.
echo [2/3] 打包客户端...
pyinstaller --onefile --windowed --name CampusNetLogin --icon=NONE app.py

echo.
echo [3/3] 打包服务器...
pyinstaller --onefile --console --name ConfirmServer confirm_server.py

echo.
echo ========================================
echo   打包完成！
echo ========================================
echo.
echo 客户端: dist\CampusNetLogin.exe
echo 服务器: dist\ConfirmServer.exe
echo.
pause
