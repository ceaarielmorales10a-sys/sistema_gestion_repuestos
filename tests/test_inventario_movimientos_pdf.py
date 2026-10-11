import re
from unittest.mock import patch

from run import app


def test_inventory_movements_pdf_is_landscape_and_spans_pages():
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['user'] = 'Prueba'
        session['role'] = 'Administrador del Sistema'

    movements = [
        {
            'id_movimiento': index,
            'repuesto_nombre': f'Repuesto de prueba {index}',
            'tipo_movimiento': 'Devolución',
            'fecha_movimiento': '2026-10-07 12:30:00',
            'cantidad': 1,
            'detalle': 'Devolución de venta V-000001',
            'usuario': 'admin',
            'estado': 'Activo',
        }
        for index in range(1, 51)
    ]

    with patch('run.get_movimientos_reporte', return_value=movements):
        response = client.get('/inventario/movimientos/exportar-pdf')

    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'
    assert re.search(
        rb'/MediaBox\s*\[\s*0\s+0\s+792\s+612\s*\]',
        response.data,
    )
    page_count = re.search(rb'/Count\s+(\d+)\s*/Kids\s*\[.*?\]\s*/Type\s*/Pages', response.data)
    assert page_count
    assert int(page_count.group(1)) > 1
