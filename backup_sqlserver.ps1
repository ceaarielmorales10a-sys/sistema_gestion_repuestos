<# 
.SYNOPSIS
    Script de backup completo para SQL Server - Genera archivos .bak reales
.DESCRIPTION
    Realiza backup FULL de la base de datos ImportadoraMorales (o la configurada)
    Incluye compresión, verificación y limpieza de backups antiguos
.NOTES
    Requiere: Ejecutar como Administrador en el servidor SQL
    Permisos: rol sysadmin o db_backupoperator en SQL Server
#>

param(
    [string]$SqlInstance = "localhost",
    [string]$DatabaseName = "ImportadoraMorales",
    [string]$BackupPath = "C:\Backups\SQL",
    [int]$RetentionDays = 7,
    [bool]$CompressBackup = $true,
    [bool]$VerifyBackup = $true,
    [string]$LogPath = "C:\Backups\Logs"
)

# Configuración
$ErrorActionPreference = "Stop"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupFile = Join-Path $BackupPath "$DatabaseName\_FULL\_$timestamp.bak"
$logFile = Join-Path $LogPath "backup_$timestamp.log"

# Crear directorios si no existen
$null = New-Item -ItemType Directory -Force -Path $BackupPath
$null = New-Item -ItemType Directory -Force -Path (Split-Path $backupFile)
$null = New-Item -ItemType Directory -Force -Path $LogPath

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $entry = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [$Level] $Message"
    Add-Content -Path $logFile -Value $entry
    Write-Host $entry
}

try {
    Write-Log "=== INICIO BACKUP FULL: $DatabaseName ==="
    Write-Log "Instancia: $SqlInstance"
    Write-Log "Archivo destino: $backupFile"

    # Verificar conexión
    $connTest = Invoke-Sqlcmd -ServerInstance $SqlInstance -Query "SELECT 1" -ErrorAction Stop
    Write-Log "Conexión a SQL Server OK"

    # Verificar que la BD existe
    $dbExists = Invoke-Sqlcmd -ServerInstance $SqlInstance -Query "
        SELECT 1 FROM sys.databases WHERE name = '$DatabaseName'
    " -ErrorAction SilentlyContinue
    
    if (-not $dbExists) {
        throw "Base de datos '$DatabaseName' no existe en $SqlInstance"
    }
    Write-Log "Base de datos '$DatabaseName' encontrada"

    # Obtener modo de recuperación
    $recoveryModel = Invoke-Sqlcmd -ServerInstance $SqlInstance -Query "
        SELECT recovery_model_desc FROM sys.databases WHERE name = '$DatabaseName'
    "
    Write-Log "Modelo de recuperación: $($recoveryModel.recovery_model_desc)"

    # Construir comando BACKUP
    $backupCmd = @"
BACKUP DATABASE [$DatabaseName]
TO DISK = N'$backupFile'
WITH 
    NOFORMAT, 
    NOINIT, 
    NAME = N'$DatabaseName-Full Backup $timestamp',
    SKIP, 
    NOREWIND, 
    NOUNLOAD, 
    STATS = 10
"@

    if ($CompressBackup) {
        $backupCmd += ", COMPRESSION"
    }
    else {
        $backupCmd += ", NO_COMPRESSION"
    }

    if ($VerifyBackup) {
        $backupCmd += ", CHECKSUM"
    }

    $backupCmd += ";"

    Write-Log "Ejecutando BACKUP DATABASE..."
    
    # Ejecutar backup
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Invoke-Sqlcmd -ServerInstance $SqlInstance -Query $backupCmd -QueryTimeout 0 -ErrorAction Stop
    $sw.Stop()
    
    Write-Log "Backup completado en $($sw.Elapsed.TotalSeconds) segundos"

    # Verificar backup
    if ($VerifyBackup) {
        Write-Log "Verificando backup con RESTORE VERIFYONLY..."
        $verifyCmd = "RESTORE VERIFYONLY FROM DISK = N'$backupFile';"
        Invoke-Sqlcmd -ServerInstance $SqlInstance -Query $verifyCmd -QueryTimeout 0 -ErrorAction Stop
        Write-Log "Verificación OK - Backup válido"
    }

    # Obtener tamaño
    $fileInfo = Get-Item $backupFile
    $sizeMB = [math]::Round($fileInfo.Length / 1MB, 2)
    Write-Log "Tamaño archivo: $sizeMB MB"

    # Limpiar backups antiguos
    Write-Log "Limpiando backups mayores a $RetentionDays días..."
    $oldBackups = Get-ChildItem -Path (Split-Path $backupFile) -Filter "*.bak" | 
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$RetentionDays) }
    
    foreach ($old in $oldBackups) {
        try {
            Remove-Item $old.FullName -Force
            Write-Log "Eliminado backup antiguo: $($old.Name)"
        }
        catch {
            Write-Log "No se pudo eliminar $($old.Name): $($_.Exception.Message)" "WARN"
        }
    }

    # Limpiar logs antiguos
    $oldLogs = Get-ChildItem -Path $LogPath -Filter "backup_*.log" | 
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$RetentionDays) }
    foreach ($old in $oldLogs) {
        try { Remove-Item $old.FullName -Force } catch { }
    }

    Write-Log "=== BACKUP COMPLETADO EXITOSAMENTE ==="
    exit 0
}
catch {
    Write-Log "ERROR: $($_.Exception.Message)" "ERROR"
    Write-Log $_.ScriptStackTrace "ERROR"
    
    # Intentar limpiar archivo parcial si falló
    if (Test-Path $backupFile) {
        try { Remove-Item $backupFile -Force } catch { }
    }
    
    exit 1
}