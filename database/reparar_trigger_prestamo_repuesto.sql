USE [Sistema_repuestos];
GO

/*
    dbo.TR_prestamo_repuesto previously assigned id_estado_repuesto = 3.
    That value is not present in every estado_repuesto catalog and can violate
    the FK when a loan detail is inserted. Stock is already decremented by the
    application; this trigger should only record the inventory movement.
*/
IF OBJECT_ID(N'dbo.TR_prestamo_repuesto', N'TR') IS NOT NULL
    DROP TRIGGER dbo.TR_prestamo_repuesto;
GO

CREATE TRIGGER dbo.TR_prestamo_repuesto
ON dbo.detalle_prestamo
AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;

    INSERT INTO dbo.movimiento_inventario
        (id_repuesto, id_tipo_movimiento, fecha_movimiento, cantidad, detalle, id_usuario, id_estado)
    SELECT
        i.id_repuesto,
        3,
        GETDATE(),
        i.cantidad,
        'Salida por préstamo',
        p.id_usuario,
        1
    FROM inserted AS i
    INNER JOIN dbo.prestamo AS p ON p.id_prestamo = i.id_prestamo;
END;
GO
