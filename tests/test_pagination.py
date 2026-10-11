from pathlib import Path
from unittest.mock import patch

from run import app


ROOT = Path(__file__).resolve().parents[1]


class ClientCursor:
    description = [
        ('id_cliente',),
        ('nombre_completo',),
        ('nit',),
        ('telefono',),
        ('correo',),
        ('direccion',),
        ('fecha_registro',),
    ]

    def execute(self, _query, _params=None):
        pass

    def fetchall(self):
        return [
            (index, f'Cliente {index}', f'NIT-{index}', '-', '-', '-', None)
            for index in range(1, 13)
        ]

    def fetchone(self):
        return None

    def close(self):
        pass


class ClientConnection:
    def cursor(self):
        return ClientCursor()

    def close(self):
        pass


def test_client_report_uses_full_dataset_instead_of_current_page():
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['user'] = 'Prueba'
        session['role'] = 'Administrador del Sistema'

    with (
        patch('run.get_db_connection', return_value=ClientConnection()),
        patch(
            'run.get_table_columns',
            return_value=[
                'id_cliente', 'nombre_completo', 'nit', 'telefono',
                'correo', 'direccion', 'fecha_registro',
            ],
        ),
        patch('run.cliente_display_from_row', side_effect=lambda row: row['nombre_completo']),
        patch('run.get_overdue_loan_count', return_value=0),
    ):
        response = client.get('/clientes?page=2')

    assert response.status_code == 200
    assert b'Cliente 1' in response.data
    assert b'Cliente 12' in response.data
    assert b'data-pagination-page-size="10"' in response.data


def test_data_lists_and_reports_use_ten_record_pagination():
    surfaces = {
        'clientes.html': 2,
        'inventario.html': 3,
        'ventas.html': 2,
        'devoluciones.html': 2,
        'prestamos.html': 2,
        'desarme.html': 1,
        'desarme_detalle.html': 2,
        'usuario.html': 2,
        'mantenimiento.html': 1,
    }

    for filename, expected_tables in surfaces.items():
        content = (ROOT / 'app' / 'templates' / filename).read_text(encoding='utf-8')
        assert content.count('data-pagination-page-size="10"') == expected_tables
        assert "filename='pagination.js'" in content or 'filename="pagination.js"' in content

    sales = (ROOT / 'app' / 'templates' / 'ventas.html').read_text(encoding='utf-8')
    inventory = (ROOT / 'app' / 'templates' / 'inventario.html').read_text(encoding='utf-8')
    loans = (ROOT / 'app' / 'templates' / 'prestamos.html').read_text(encoding='utf-8')
    assert 'data-pagination-group-next=".detail"' in sales
    assert 'data-pagination-group-next=".detail-row"' in inventory
    assert 'data-pagination-group-key="loanId"' in loans
