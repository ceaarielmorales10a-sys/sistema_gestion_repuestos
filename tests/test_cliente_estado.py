from pathlib import Path
from unittest.mock import patch

from run import _cliente_esta_activo, _clientes_activos


class CustomerCursor:
    def __init__(self, rows=(), one=None):
        self.rows = rows
        self.one = one
        self.description = []
        self.executed = []

    def execute(self, query, params=()):
        self.executed.append((query, params))
        if query.startswith('SELECT * FROM cliente'):
            self.description = [
                ('id_cliente',),
                ('primer_nombre',),
                ('id_estado_general',),
            ]

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one


def test_active_client_list_filters_by_general_active_state():
    cursor = CustomerCursor(rows=[(4, 'Cliente Activo', 1)])

    with (
        patch('run.get_table_columns', return_value=[
            'id_cliente', 'primer_nombre', 'id_estado_general',
        ]),
        patch('run.get_estado_general_id', return_value=1),
    ):
        clients = _clientes_activos(cursor)

    assert clients == [{
        'id_cliente': 4,
        'primer_nombre': 'Cliente Activo',
        'id_estado_general': 1,
    }]
    assert cursor.executed == [
        (
            'SELECT * FROM cliente WHERE [id_estado_general] = ? ORDER BY [id_cliente]',
            (1,),
        )
    ]


def test_inactive_client_is_rejected_for_sales_and_loans():
    cursor = CustomerCursor(one=None)

    with (
        patch('run.get_table_columns', return_value=[
            'id_cliente', 'id_estado_general',
        ]),
        patch('run.get_estado_general_id', return_value=1),
    ):
        assert not _cliente_esta_activo(cursor, 9)

    assert cursor.executed == [
        (
            'SELECT 1 FROM cliente WHERE [id_cliente] = ? AND [id_estado_general] = ?',
            (9, 1),
        )
    ]


def test_client_edit_form_exposes_status_checkbox_only_for_existing_clients():
    template = (
        Path(__file__).resolve().parents[1]
        / 'app'
        / 'templates'
        / 'clientes.html'
    ).read_text(encoding='utf-8')

    assert 'name="activo" id="client-active"' in template
    assert 'clientStatusField.hidden = true' in template
    assert 'clientStatusField.hidden = false' in template


def test_client_table_shows_status_and_no_longer_offers_delete_action():
    template = (
        Path(__file__).resolve().parents[1]
        / 'app'
        / 'templates'
        / 'clientes.html'
    ).read_text(encoding='utf-8')

    assert '<th>Estado</th>' in template
    assert "cliente.activo else 'Inactivo'" in template
    assert 'name="action" value="delete"' not in template
    assert 'title="Eliminar este cliente"' not in template
