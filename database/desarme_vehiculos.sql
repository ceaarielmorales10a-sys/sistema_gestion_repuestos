/*
   Vincula el registro de dbo.vehiculo_desarme con su proceso de desarme.
   La tabla dbo.vehiculo_desarme ya contiene los datos generales del vehículo;
   esta migración sólo agrega la relación opcional a dbo.desarme.
*/
USE [Sistema_repuestos];
GO

IF OBJECT_ID(N'dbo.vehiculo_desarme', N'U') IS NULL
    THROW 50001, 'No existe la tabla dbo.vehiculo_desarme.', 1;
IF OBJECT_ID(N'dbo.desarme', N'U') IS NULL
    THROW 50002, 'No existe la tabla dbo.desarme.', 1;
GO

IF COL_LENGTH(N'dbo.desarme', N'id_vehiculo_desarme') IS NULL
    ALTER TABLE dbo.desarme ADD id_vehiculo_desarme INT NULL;
GO

/* El origen se guarda como texto en vehiculo_desarme.origen_adquisicion.
   En el proceso el ID del catálogo es complementario y, si no se especifica,
   debe poder quedar NULL para conservar la opción visual "Sin especificar". */
IF EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = N'dbo' AND TABLE_NAME = N'desarme'
      AND COLUMN_NAME = N'id_vehiculo_origen' AND IS_NULLABLE = N'NO'
)
    ALTER TABLE dbo.desarme ALTER COLUMN id_vehiculo_origen INT NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_desarme_vehiculo_desarme'
)
    ALTER TABLE dbo.desarme WITH CHECK ADD CONSTRAINT FK_desarme_vehiculo_desarme
        FOREIGN KEY (id_vehiculo_desarme)
        REFERENCES dbo.vehiculo_desarme(id_vehiculo_desarme);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'dbo.desarme')
      AND name = N'UX_desarme_vehiculo_desarme'
)
    CREATE UNIQUE INDEX UX_desarme_vehiculo_desarme
        ON dbo.desarme(id_vehiculo_desarme)
        WHERE id_vehiculo_desarme IS NOT NULL;
GO

IF OBJECT_ID(N'dbo.incidencia_desarme', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.incidencia_desarme (
        id_incidencia INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_incidencia_desarme PRIMARY KEY,
        id_desarme INT NOT NULL,
        id_detalle_desarme INT NOT NULL,
        descripcion NVARCHAR(1000) NOT NULL,
        fecha_incidencia DATE NOT NULL
            CONSTRAINT DF_incidencia_desarme_fecha DEFAULT (CONVERT(date, GETDATE())),
        id_usuario INT NULL,
        id_estado INT NOT NULL,
        CONSTRAINT FK_incidencia_desarme_desarme
            FOREIGN KEY (id_desarme) REFERENCES dbo.desarme(id_desarme),
        CONSTRAINT FK_incidencia_desarme_detalle
            FOREIGN KEY (id_detalle_desarme)
            REFERENCES dbo.detalle_desarme(id_detalle_desarme)
    );

    CREATE INDEX IX_incidencia_desarme_proceso
        ON dbo.incidencia_desarme(id_desarme, id_incidencia DESC);
END;
GO
