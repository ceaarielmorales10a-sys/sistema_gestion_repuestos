from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from run import app, get_estado_general_id, split_repuestos_por_estado


ROOT = Path(__file__).resolve().parents[1]


class EstadoCursor:
    description = [('id_estado',), ('estado',)]

    def __init__(self):
        self.result = None
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))
        if query.startswith('SELECT TOP 1 * FROM estado_general'):
            self.description = [('id_estado',), ('estado',)]
            self.result = None
        elif 'FROM estado_general' in query:
            self.result = (9,) if params == ('activo',) else (14,)
        elif 'FROM repuesto WHERE id_repuesto = ?' in query:
            self.result = SimpleNamespace(
                id_repuesto=4,
                precio_venta=100,
                stock=2,
                id_estado_general=14,
            )
        else:
            self.result = None

    def fetchone(self):
        return self.result

    def close(self):
        pass


class EstadoConnection:
    def __init__(self):
        self.db_cursor = EstadoCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.db_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


@pytest.mark.parametrize(('active', 'expected'), [(True, 9), (False, 14)])
def test_general_status_id_is_resolved_from_catalog(active, expected):
    cursor = EstadoCursor()

    assert get_estado_general_id(cursor, active) == expected


def test_add_and_edit_forms_only_offer_status_checkbox_when_editing():
    fields = (ROOT / 'app' / 'templates' / '_repuesto_form_fields.html').read_text(encoding='utf-8')

    assert '{% if is_edit %}' in fields
    assert 'name="activo"' in fields
    assert 'repuesto.id_estado_general == estado_activo_id' in fields


def test_inventory_splits_inactive_parts_from_active_parts():
    active, inactive = split_repuestos_por_estado([
        {'id_repuesto': 1, 'estado': 'Activo'},
        {'id_repuesto': 2, 'estado': 'Inactivo'},
        {'id_repuesto': 3, 'estado': '-'},
    ])

    assert [part['id_repuesto'] for part in active] == [1, 3]
    assert [part['id_repuesto'] for part in inactive] == [2]


def test_inventory_template_shows_active_and_inactive_status_surfaces():
    template = (ROOT / 'app' / 'templates' / 'inventario.html').read_text(encoding='utf-8')
    rows = (ROOT / 'app' / 'templates' / '_inventario_repuesto_rows.html').read_text(encoding='utf-8')

    assert 'inventory-active-body' in template
    assert 'inventory-inactive-body' in template
    assert 'Repuestos inactivos' in template
    assert 'Estado del inventario' in rows
    assert 'inventory-status-badge' in rows


def test_inventory_switch_scopes_filters_to_selected_state():
    template = (ROOT / 'app' / 'templates' / 'inventario.html').read_text(encoding='utf-8')

    assert 'data-inventory-status="activo"' in template
    assert 'data-inventory-status="inactivo"' in template
    assert "params.set('estado', selectedInventoryStatus)" in template
    assert 'fetchInventoryList(inventoryLists[status].page)' in template
    assert 'inventory-active-panel' in template
    assert 'inventory-inactive-panel' in template


def test_new_sale_rejects_inactive_repuesto_on_server():
    connection = EstadoConnection()
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['user'] = 'Prueba'
        session['role'] = 'Administrador del Sistema'

    with patch('run.get_db_connection', return_value=connection):
        response = client.post(
            '/ventas/nueva',
            data={
                'id_metodo_pago': '1',
                'id_repuesto[]': ['4'],
                'cantidad[]': ['1'],
                'descuento[]': ['0'],
            },
        )

    assert response.status_code == 302
    assert connection.rolled_back
    assert not connection.committed
    assert not any('INSERT INTO venta' in query for query, _ in connection.db_cursor.queries)
