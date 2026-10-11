@echo off
REM ============================================
REM Backup SQL Server - Importadora Morales
REM Ejecutar como ADMINISTRADOR en el servidor SQL
REM ============================================

title Backup SQL Server - Importadora Morales

echo.
echo ╔═══════════════════════════════════════════════╗
echo ║   BACKUP FULL SQL SERVER - .bak REAL         ║
echo ║   Importadora Morales                        ║
echo ╚═══════════════════════════════════════════════╝
echo.

REM Verificar PowerShell
where powershell >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: PowerShell no encontrado
    pause
    exit /b 1
)

REM Verificar si es Admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Debe ejecutarse COMO ADMINISTRADOR
    echo.
    echo Clic derecho en este archivo > "Ejecutar como administrador"
    pause
    exit /b 1
)

REM Configuración - MODIFICAR SEGÚN TU ENTORNO
set SQL_INSTANCE=localhost
set DATABASE_NAME=ImportadoraMorales
set BACKUP_PATH=D:\Backups\SQL
set RETENTION_DAYS=7

echo Configuración:
echo   Instancia:     %SQL_INSTANCE%
echo   Base de datos: %DATABASE_NAME%
echo   Carpeta backup: %BACKUP_PATH%
echo   Retención:     %RETENTION_DAYS% días
echo.

echo Iniciando backup...
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0backup_sqlserver.ps1" `
    -SqlInstance "%SQL_INSTANCE%" `
    -DatabaseName "%DATABASE_NAME%" `
    -BackupPath "%BACKUP_PATH%" `
    -RetentionDays %RETENTION_DAYS% `
    -CompressBackup $true `
    -VerifyBackup $true

set EXIT_CODE=%ERRORLEVEL%

echo.
if %EXIT_CODE% equ 0 (
    echo ✓ BACKUP COMPLETADO CORRECTAMENTE
) else (
    echo ✗ ERROR EN BACKUP - Revisar logs en %BACKUP_PATH%\Logs
)

echo.
pause
exit /b %EXIT_CODE%