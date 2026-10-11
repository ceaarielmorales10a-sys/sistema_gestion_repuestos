# Backup SQL Server Real (.bak) - Importadora Morales

## ⚠️ Requisitos Previos

1. **Acceso al servidor SQL** (RDP, SSH, o consola física)
2. **Permisos de Administrador** en Windows
3. **Rol `sysadmin` o `db_backupoperator`** en SQL Server
4. **PowerShell 5.1+** (incluido en Windows Server 2016+ / Windows 10+)

---

## 📁 Archivos Incluidos

| Archivo | Descripción |
|---------|-------------|
| `backup_sqlserver.ps1` | Script principal PowerShell |
| `backup_sqlserver.bat` | Wrapper batch para ejecución fácil |
| `BACKUP_SQLSERVER_README.md` | Este archivo |

---

## 🚀 Instalación y Configuración

### 1. Copiar al Servidor SQL
```powershell
# Desde tu PC, copia la carpeta al servidor
# Ejemplo vía RDP: arrastra la carpeta a C:\Scripts\BackupSQL\
```

### 2. Editar Configuración (OBLIGATORIO)
Edita `backup_sqlserver.bat` y modifica:
```bat
set SQL_INSTANCE=localhost          \← Nombre de instancia (ej: SERVER\SQLEXPRESS)
set DATABASE_NAME=ImportadoraMorales \← Nombre exacto de tu BD
set BACKUP_PATH=D:\Backups\SQL      \← Carpeta DESTINO (en disco LOCAL del servidor)
set RETENTION_DAYS=7                \← Días a conservar backups antiguos
```

> **Importante**: `BACKUP_PATH` debe ser una ruta **local del servidor SQL** (no red/UNC), ej: `D:\Backups\SQL`, `C:\SQLBackups`

### 3. Probar Manualmente
1. Clic derecho en `backup_sqlserver.bat` → **Ejecutar como administrador**
2. Verificar que crea `ImportadoraMorales_FULL_YYYYMMDD_HHMMSS.bak` en la carpeta

---

## ⏰ Automatización (Programador de Tareas)

### Opción A: Diario a las 02:00 AM (Recomendado)
```powershell
# Ejecutar EN EL SERVIDOR como Admin (PowerShell)
$action = New-ScheduledTaskAction -Execute "C:\Scripts\BackupSQL\backup_sqlserver.bat"
$trigger = New-ScheduledTaskTrigger -Daily -At 2am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd
Register-ScheduledTask -TaskName "BackupSQL_ImportadoraMorales" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
```

### Opción B: Cada 12 horas
```powershell
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(2) -RepetitionInterval (New-TimeSpan -Hours 12) -RepetitionDuration ([TimeSpan]::MaxValue)
```

### Opción C: Via GUI (Task Scheduler)
1. Abrir **Task Scheduler** (`taskschd.msc`)
2. **Create Basic Task** → Nombre: `Backup SQL Importadora Morales`
3. Trigger: **Daily** → 02:00 AM
4. Action: **Start a Program**
   - Program: `C:\Scripts\BackupSQL\backup_sqlserver.bat`
   - Start in: `C:\Scripts\BackupSQL\`
5. **Run with highest privileges** ✓
6. Finish

---

## 🔍 Verificación y Monitoreo

### Ver último backup
```sql
-- En SSMS
SELECT 
    database_name,
    backup_finish_date,
    backup_size / 1024 / 1024 AS Size_MB,
    compressed_backup_size / 1024 / 1024 AS Compressed_MB,
    physical_device_name
FROM msdb.dbo.backupset bs
JOIN msdb.dbo.backupmediafamily bmf ON bs.media_set_id = bmf.media_set_id
WHERE database_name = 'ImportadoraMorales'
  AND type = 'D'  -- Full
ORDER BY backup_finish_date DESC
```

### Ver logs del script
```
C:\Backups\SQL\Logs\backup_YYYYMMDD_HHMMSS.log
```

### Restaurar manualmente (SSMS)
1. Click derecho en Databases → **Restore Database...**
2. **Device** → Seleccionar archivo `.bak`
3. OK → Options → **Overwrite** → Restore

---

## 📋 Parámetros del Script PowerShell

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| `-SqlInstance` | `localhost` | Instancia SQL (ej: `SERVER\SQLEXPRESS`) |
| `-DatabaseName` | `ImportadoraMorales` | Nombre BD exacto |
| `-BackupPath` | `C:\Backups\SQL` | Carpeta destino (local al servidor) |
| `-RetentionDays` | `7` | Días a mantener backups |
| `-CompressBackup` | `$true` | Compresión nativa SQL (ahorra ~70%) |
| `-VerifyBackup` | `$true` | `RESTORE VERIFYONLY` tras backup |
| `-LogPath` | `C:\Backups\Logs` | Carpeta de logs |

---

## ⚡ Ejecución Manual Directa (PowerShell)
```powershell
# Desde PowerShell como Admin en el servidor
.\backup_sqlserver.ps1 -SqlInstance "localhost" -DatabaseName "ImportadoraMorales" -BackupPath "D:\Backups\SQL" -RetentionDays 14 -CompressBackup $true -VerifyBackup $true
```

---

## 🛡️ Seguridad y Buenas Prácticas

1. **Carpeta de backup en disco separado** (D: o E:) del data/log de SQL
2. **Copiar backups a almacenamiento externo/NAS/Cloud** (script adicional con `robocopy` o Azure Blob)
3. **Probar restore mensualmente** en ambiente de test
4. **Monitorear espacio en disco** (alertas si < 20% libre)
5. **Backup de logs de transacciones** cada 15-60 min si recovery model = FULL

---

## 🆘 Troubleshooting

| Error | Causa | Solución |
|-------|-------|----------|
| "Access denied" | Sin permisos Admin / carpeta | Ejecutar como Admin, dar permisos NTFS a cuenta SQL Service |
| "Database does not exist" | Nombre incorrecto | Verificar `SELECT name FROM sys.databases` |
| "Backup failed" | Disco lleno / ruta inválida | Verificar espacio, ruta local (no red) |
| "sqlcmd/Invoke-Sqlcmd not found" | Sin SSMS/SQL tools | Instalar `sqlserver` module: `Install-Module -Name SqlServer` |

---

## 📞 Soporte

Para integrar con la app web (descargar .bak desde UI):
- Requiere **API/endpoint en el servidor** que sirva archivos de `BACKUP_PATH`
- O carpeta compartida (SMB) accesible desde el servidor web
- **No recomendado** exponer backups .bak vía web por seguridad

**Recomendación**: Mantén los `.bak` en el servidor/NAS seguro. La app web usa el `.sql` (export lógico) para portabilidad.