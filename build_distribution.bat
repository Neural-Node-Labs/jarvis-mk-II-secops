@echo off
:: ==============================================================================
:: Windows Production Deployment Packaging Script
:: This script automates the compiling, obfuscating, and bundling of a
:: full-stack Python API (via Cython) and React UI (via Vite) into a secure,
:: production-ready Windows .exe distribution.
:: ==============================================================================

set REPO_ROOT=%CD%
set BACKEND_DIR=%REPO_ROOT%\backend
set FRONTEND_DIR=%REPO_ROOT%\frontend
set BUILD_DIR=%REPO_ROOT%\build_output

echo [1/6] Initializing and clearing previous builds...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%BACKEND_DIR%\frontend_dist" rmdir /s /q "%BACKEND_DIR%\frontend_dist"
if exist "%BACKEND_DIR%\build" rmdir /s /q "%BACKEND_DIR%\build"
if exist "%BACKEND_DIR%\dist" rmdir /s /q "%BACKEND_DIR%\dist"
if exist "%BACKEND_DIR%\api_logic.c" del /f /q "%BACKEND_DIR%\api_logic.c"
if exist "%BACKEND_DIR%\api_logic.cp*.pyd" del /f /q "%BACKEND_DIR%\api_logic.cp*.pyd"
mkdir "%BUILD_DIR%"

echo.
echo [2/6] Building and Minifying React Production Assets...
cd "%FRONTEND_DIR%"
call npm install
if %ERRORLEVEL% NEQ 0 (
    echo Error: npm install failed. Exiting...
    exit /b %ERRORLEVEL%
)
call npm run build
if %ERRORLEVEL% NEQ 0 (
    echo Error: React production build failed. Exiting...
    exit /b %ERRORLEVEL%
)

echo.
echo [3/6] Moving React Assets to Backend for Staging...
xcopy "%FRONTEND_DIR%\dist" "%BACKEND_DIR%\frontend_dist" /E /I /H /Y

echo.
echo [4/6] Compiling Python Code to C-Extension Binary (Cython)...
cd "%BACKEND_DIR%"
python setup.py build_ext --inplace
if %ERRORLEVEL% NEQ 0 (
    echo Error: Cython compilation failed. Ensure C++ Build Tools are installed.
    exit /b %ERRORLEVEL%
)

echo.
echo [5/6] Securing Package Environment (Removing source code)...
:: Verification step to ensure the compiled .pyd file exists before deleting original logic
if exist api_logic*.pyd (
    echo Compiled binary verified. Securely deleting plain-text source files...
    del /f /q api_logic.py
    del /f /q api_logic.c
) else (
    echo Error: Compiled .pyd file was not found! Aborting deployment to protect IP.
    exit /b 1
)

echo.
echo [6/6] Freezing Runtime into Single Executable Package (PyInstaller)...
call pyinstaller --noconfirm --clean --onedir --windowed ^
    --add-data "frontend_dist;frontend_dist" ^
    --name "SecureDesktopApp" ^
    app.py

if %ERRORLEVEL% NEQ 0 (
    echo Error: PyInstaller bundling failed. Exiting...
    exit /b %ERRORLEVEL%
)

echo.
echo Moving complete binary distribution package to build output...
xcopy "%BACKEND_DIR%\dist\SecureDesktopApp" "%BUILD_DIR%\SecureDesktopApp" /E /I /H /Y

echo ==============================================================================
echo SUCCESS: Production deployment package built successfully!
echo Target Location: %BUILD_DIR%\SecureDesktopApp\SecureDesktopApp.exe
echo Original code removed, React minified, and Python API fully compiled to binary.
echo ==============================================================================
pause