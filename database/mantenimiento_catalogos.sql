/*
   Migración de catálogos del módulo Mantenimiento.
   Es segura de ejecutar más de una vez en Sistema_repuestos.
*/
USE [Sistema_repuestos];
GO

/* Descripción para los catálogos existentes. */
IF OBJECT_ID(N'dbo.marca_vehiculo', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.marca_vehiculo', N'descripcion') IS NULL ALTER TABLE dbo.marca_vehiculo ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.marca', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.marca', N'descripcion') IS NULL ALTER TABLE dbo.marca ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.color', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.color', N'descripcion') IS NULL ALTER TABLE dbo.color ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.color_repuesto', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.color_repuesto', N'descripcion') IS NULL ALTER TABLE dbo.color_repuesto ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.estado_general', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.estado_general', N'descripcion') IS NULL ALTER TABLE dbo.estado_general ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.estado_repuesto', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.estado_repuesto', N'descripcion') IS NULL ALTER TABLE dbo.estado_repuesto ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.vehiculo_origen', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.vehiculo_origen', N'descripcion') IS NULL ALTER TABLE dbo.vehiculo_origen ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.transmision', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.transmision', N'descripcion') IS NULL ALTER TABLE dbo.transmision ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.transmision_vehiculo', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.transmision_vehiculo', N'descripcion') IS NULL ALTER TABLE dbo.transmision_vehiculo ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.ubicacion', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.ubicacion', N'descripcion') IS NULL ALTER TABLE dbo.ubicacion ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.bodega', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.bodega', N'descripcion') IS NULL ALTER TABLE dbo.bodega ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.ubicacion_bodega', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.ubicacion_bodega', N'descripcion') IS NULL ALTER TABLE dbo.ubicacion_bodega ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.rol', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.rol', N'descripcion') IS NULL ALTER TABLE dbo.rol ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.estado_desarme', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.estado_desarme', N'descripcion') IS NULL ALTER TABLE dbo.estado_desarme ADD descripcion NVARCHAR(250) NULL;

/* Campos del catálogo tipo de repuesto. La ubicación es una clave al catálogo
   de ubicaciones, para que el formulario de alta pueda seleccionarla de forma
   automática. */
IF OBJECT_ID(N'dbo.tipo_repuesto', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.tipo_repuesto', N'descripcion') IS NULL ALTER TABLE dbo.tipo_repuesto ADD descripcion NVARCHAR(250) NULL;
IF OBJECT_ID(N'dbo.tipo_repuesto', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.tipo_repuesto', N'id_estado') IS NULL ALTER TABLE dbo.tipo_repuesto ADD id_estado INT NULL;
IF OBJECT_ID(N'dbo.tipo_repuesto', N'U') IS NOT NULL AND COL_LENGTH(N'dbo.tipo_repuesto', N'id_ubicacion') IS NULL ALTER TABLE dbo.tipo_repuesto ADD id_ubicacion INT NULL;
GO

IF OBJECT_ID(N'dbo.estado_pieza_desarme', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.estado_pieza_desarme (
        id_estado_pieza INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        nombre NVARCHAR(100) NOT NULL,
        descripcion NVARCHAR(250) NULL,
        id_estado INT NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.motivo_devolucion', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.motivo_devolucion (
        id_motivo INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        motivo NVARCHAR(150) NOT NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.estado_prestamo', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.estado_prestamo (
        id_estado_prestamo INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        estado NVARCHAR(100) NOT NULL
    );
END;
GO
