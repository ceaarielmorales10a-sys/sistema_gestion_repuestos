USE [Sistema_repuestos];
GO

/*
    The application now writes CREACION and MODIFICACION entries using the
    current dbo.bitacora_repuesto schema. This legacy trigger still targets
    the previous snapshot schema and fails on repuesto INSERT/UPDATE.
    Disabling it is reversible with:
    ENABLE TRIGGER dbo.trg_bitacora_repuesto ON dbo.repuesto;
*/
IF EXISTS (
    SELECT 1
    FROM sys.triggers
    WHERE name = N'trg_bitacora_repuesto'
      AND parent_id = OBJECT_ID(N'dbo.repuesto')
      AND is_disabled = 0
)
BEGIN
    DISABLE TRIGGER dbo.trg_bitacora_repuesto ON dbo.repuesto;
END;
GO
