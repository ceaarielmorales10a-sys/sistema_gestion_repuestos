/* Ejecutar una sola vez en la base Sistema_repuestos con un usuario que pueda crear tablas. */
USE [Sistema_repuestos];
GO

IF OBJECT_ID(N'dbo.modulo', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.modulo (
        id_modulo INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        clave NVARCHAR(50) NOT NULL UNIQUE,
        nombre NVARCHAR(100) NOT NULL,
        descripcion NVARCHAR(250) NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.rol_modulo', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.rol_modulo (
        id_rol_modulo INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        id_rol INT NOT NULL,
        id_modulo INT NOT NULL,
        acceso NVARCHAR(20) NOT NULL CONSTRAINT DF_rol_modulo_acceso DEFAULT 'none',
        CONSTRAINT FK_rol_modulo_rol FOREIGN KEY (id_rol) REFERENCES dbo.rol(id_rol),
        CONSTRAINT FK_rol_modulo_modulo FOREIGN KEY (id_modulo) REFERENCES dbo.modulo(id_modulo),
        CONSTRAINT UQ_rol_modulo UNIQUE (id_rol, id_modulo)
    );
END;
GO

MERGE dbo.modulo AS destino
USING (VALUES
    ('analytics', N'Analítica IA'),
    ('clientes', N'Clientes'),
    ('inventario', N'Inventario'),
    ('ventas', N'Ventas'),
    ('prestamos', N'Préstamos'),
    ('devoluciones', N'Devoluciones'),
    ('desarme', N'Desarme de Vehículos'),
    ('usuarios', N'Usuarios'),
    ('mantenimiento', N'Mantenimiento')
) AS origen(clave, nombre)
ON destino.clave = origen.clave
WHEN NOT MATCHED THEN
    INSERT (clave, nombre, descripcion)
    VALUES (origen.clave, origen.nombre, N'Módulo ' + origen.nombre);
GO

/* Crear una fila explícita para cada módulo de cada rol existente.
   Los roles clásicos conservan sus permisos iniciales; los roles personalizados
   comienzan sin acceso hasta que el administrador los configure. */
INSERT INTO dbo.rol_modulo (id_rol, id_modulo, acceso)
SELECT r.id_rol, m.id_modulo,
    CASE
        WHEN LOWER(r.nombre) LIKE '%admin%' THEN 'write'
        WHEN LOWER(r.nombre) LIKE '%vendedor%' AND m.clave IN ('analytics', 'clientes', 'inventario', 'prestamos', 'devoluciones') THEN 'read'
        WHEN LOWER(r.nombre) LIKE '%vendedor%' AND m.clave = 'ventas' THEN 'write'
        WHEN LOWER(r.nombre) LIKE '%bodeguero%' AND m.clave = 'inventario' THEN 'write'
        WHEN LOWER(r.nombre) LIKE '%bodeguero%' AND m.clave = 'desarme' THEN 'write'
        WHEN LOWER(r.nombre) LIKE '%bodeguero%' AND m.clave = 'analytics' THEN 'read'
        WHEN LOWER(r.nombre) LIKE '%almacen%' AND m.clave IN ('inventario', 'desarme') THEN 'write'
        WHEN LOWER(r.nombre) LIKE '%almacen%' AND m.clave IN ('analytics', 'clientes', 'prestamos', 'devoluciones') THEN 'read'
        ELSE 'none'
    END
FROM dbo.rol r
CROSS JOIN dbo.modulo m
WHERE NOT EXISTS (
    SELECT 1
    FROM dbo.rol_modulo rm
    WHERE rm.id_rol = r.id_rol AND rm.id_modulo = m.id_modulo
);
GO
