from run import normalize_permission_value, has_module_permission


def test_normalize_permission_value():
    assert normalize_permission_value('solo lectura') == 'read'
    assert normalize_permission_value('lectura y escritura') == 'write'
    assert normalize_permission_value('sin acceso') == 'none'
    assert normalize_permission_value('read') == 'read'


def test_has_module_permission():
    permissions = {
        'inventario': 'read',
        'ventas': 'write',
        'usuarios': 'none',
    }

    assert has_module_permission(permissions, 'inventario', 'read') is True
    assert has_module_permission(permissions, 'inventario', 'write') is False
    assert has_module_permission(permissions, 'ventas', 'write') is True
    assert has_module_permission(permissions, 'usuarios', 'read') is False
