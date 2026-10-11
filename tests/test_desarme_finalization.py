from pathlib import Path
from unittest.mock import patch

import pytest

from run import (
    _desarme_available_inventory_state_id,
    _desarme_final_state_id,
    _desarme_piece_category,
    _desarme_result_summary,
    _desarme_save_vehicle,
    _desarme_state_class,
    _register_desarme_piece_in_inventory,
)


class InventoryStateCursor:
    def __init__(self, statuses):
        self.statuses = statuses

    def execute(self, _query, _params=None):
        pass

    def fetchall(self):
        return self.statuses


class InventoryInsertCursor:
    rowcount = 1

    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return (91,)


class VehicleSaveCursor:
    def __init__(self):
        self.executed = []
        self.last_query = ''

    def execute(self, query, params=None):
        self.executed.append((query, params))
        self.last_query = query

    def fetchone(self):
        if 'SELECT id FROM @inserted_vehicle' in self.last_query:
            return (44,)
        return None


def test_desarme_vehicle_creation_saves_transmission_and_creates_process():
    cursor = VehicleSaveCursor()
    columns = {
        'vehiculo_desarme': [
            'id_vehiculo_desarme', 'id_marca', 'id_modelo', 'id_anio', 'id_color',
            'id_transmision', 'vin_chasis', 'placa', 'fecha_adquisicion',
            'origen_adquisicion', 'id_estado', 'fecha_registro',
        ],
        'desarme': [
            'id_desarme', 'id_vehiculo_desarme', 'id_estado_desarme',
            'id_usuario_responsable',
        ],
    }

    with (
        patch('run.get_table_columns', side_effect=lambda _cursor, table: columns[table.split('.')[-1].strip('[]')]),
        patch('run.fetch_modelos_by_marca', return_value=[(2, 'Modelo')]),
    ):
        vehicle_id = _desarme_save_vehicle(
            cursor,
            {
                'action': 'create',
                'id_marca': '1',
                'id_modelo': '2',
                'id_anio': '3',
                'id_color': '',
                'id_transmision': '4',
                'id_estado_desarme': '5',
                'fecha_adquisicion': '2026-10-07',
            },
            8,
            {
                'marcas': [(1, 'Marca')],
                'anios': [(3, '2010')],
                'colores': [],
                'transmisiones': [(4, 'Automática')],
                'estados': [(5, 'Pendiente')],
            },
            True,
        )

    assert vehicle_id == 44
    vehicle_insert, vehicle_params = next(
        (query, params) for query, params in cursor.executed
        if 'INSERT INTO dbo.vehiculo_desarme' in query
    )
    assert 'OUTPUT INSERTED.[id_vehiculo_desarme] INTO @inserted_vehicle' in vehicle_insert
    assert 'SCOPE_IDENTITY' not in vehicle_insert
    assert '[id_transmision]' in vehicle_insert
    assert 4 in vehicle_params
    process_insert, process_params = next(
        (query, params) for query, params in cursor.executed
        if query.startswith('INSERT INTO dbo.desarme')
    )
    assert '[id_vehiculo_desarme]' in process_insert
    assert '[id_usuario_responsable]' in process_insert
    assert 44 in process_params
    assert 5 in process_params
    assert 8 in process_params


def test_desarme_vehicle_form_includes_transmission_field():
    root = Path(__file__).resolve().parents[1]
    template = (root / 'app' / 'templates' / 'desarme.html').read_text(encoding='utf-8')
    script = (root / 'app' / 'static' / 'desarme.js').read_text(encoding='utf-8')

    assert 'name="id_transmision"' in template
    assert 'data-transmission="{{ vehicle.id_transmision or \'\' }}"' in template
    assert "button.dataset.transmission" in script


def test_finalized_dismantled_piece_is_created_active_with_vehicle_transmission():
    cursor = InventoryInsertCursor()
    columns = {
        'detalle_desarme': ['id_detalle_desarme', 'id_repuesto'],
        'repuesto': [
            'id_repuesto', 'codigo_repuesto', 'id_vehiculo_origen', 'id_tipo_repuesto',
            'id_marca', 'id_modelo', 'id_anio', 'id_color', 'id_transmision',
            'id_lado', 'id_posicion', 'id_ubicacion', 'stock_minimo', 'stock_maximo',
            'stock', 'precio_compra', 'precio_venta', 'observaciones', 'fecha_ingreso',
            'id_usuario_registro', 'id_estado', 'id_estado_repuesto', 'id_estado_general',
        ],
    }

    with (
        patch('run.get_table_columns', side_effect=lambda _cursor, table: columns[table.split('.')[-1].strip('[]')]),
        patch('run._desarme_available_inventory_state_id', return_value=7),
        patch('run.get_estado_general_id', return_value=9),
        patch('run.get_tipo_ubicacion', return_value=6),
        patch('run.registrar_movimiento_inventario', return_value=1),
    ):
        _register_desarme_piece_in_inventory(
            cursor,
            12,
            {
                'id_vehiculo_origen': 3,
                'id_marca': 1,
                'id_modelo': 2,
                'id_anio': 4,
                'id_color': 5,
                'id_transmision': 8,
            },
            {
                'id_detalle_desarme': 21,
                'id_tipo_repuesto': 11,
                'cantidad': 2,
                'precio_venta': 125,
            },
            6,
        )

    insert_query, insert_params = next(
        (query, params) for query, params in cursor.executed
        if query.startswith('INSERT INTO dbo.repuesto')
    )
    assert '[id_transmision]' in insert_query
    assert '[id_estado_general]' in insert_query
    assert 8 in insert_params
    assert 9 in insert_params
    assert 7 in insert_params
    assert 'DSR-000012-000021' in insert_params


def test_desarme_inventory_state_uses_named_available_status():
    cursor = InventoryStateCursor([(5, 'Disponible'), (3, 'Sin stock')])

    with (
        patch('run._desarme_fk_target', return_value=('dbo', 'estado_repuesto', 'id_estado_repuesto')),
        patch('run.get_table_columns', return_value=['id_estado_repuesto', 'estado']),
    ):
        assert _desarme_available_inventory_state_id(cursor) == 5


def test_desarme_inventory_state_falls_back_to_standard_in_stock_id():
    cursor = InventoryStateCursor([(1, 'En bodega'), (3, 'Agotado')])

    with (
        patch('run._desarme_fk_target', return_value=('dbo', 'estado_repuesto', 'id_estado_repuesto')),
        patch('run.get_table_columns', return_value=['id_estado_repuesto', 'estado']),
    ):
        assert _desarme_available_inventory_state_id(cursor) == 1


def test_desarme_inventory_state_does_not_guess_when_standard_id_is_missing():
    cursor = InventoryStateCursor([(5, 'En bodega'), (6, 'Agotado')])

    with (
        patch('run._desarme_fk_target', return_value=('dbo', 'estado_repuesto', 'id_estado_repuesto')),
        patch('run.get_table_columns', return_value=['id_estado_repuesto', 'estado']),
    ):
        assert _desarme_available_inventory_state_id(cursor) is None


@pytest.mark.parametrize(
    ('state_name', 'category'),
    [
        ('Apta', 'aptas'),
        ('Bueno', 'aptas'),
        ('En buen estado', 'aptas'),
        ('Dañada', 'danadas'),
        ('Incompleta', 'no-aptas'),
        ('No apta', 'no-aptas'),
        ('Sin clasificar', 'no-aptas'),
    ],
)
def test_desarme_piece_category(state_name, category):
    assert _desarme_piece_category(state_name) == category


def test_desarme_result_summary_counts_piece_quantities():
    counts, pieces = _desarme_result_summary([
        {'tipo_repuesto': 'Frenos', 'lado': 'Izquierdo', 'estado_pieza': 'Incompleta', 'cantidad': 1},
        {'tipo_repuesto': 'Frenos', 'lado': 'Ambos', 'estado_pieza': 'Dañada', 'cantidad': 1},
        {'tipo_repuesto': 'Puerta', 'lado': 'Derecho', 'estado_pieza': 'Apta', 'cantidad': 2},
    ])

    assert counts == {
        'piezas_obtenidas': 4,
        'piezas_aptas': 2,
        'piezas_danadas': 1,
        'piezas_no_aptas': 1,
    }
    assert [piece['categoria'] for piece in pieces] == ['no-aptas', 'danadas', 'aptas']


@pytest.mark.parametrize(
    ('states', 'expected_id'),
    [
        ([(1, 'Pendiente'), (2, 'En proceso'), (3, 'Desarmado')], 3),
        ([(1, 'Pendiente'), (2, 'En proceso'), (4, 'Finalizado')], 4),
    ],
)
def test_desarme_final_state_accepts_catalog_labels(states, expected_id):
    with patch('run.fetch_catalog_for_candidates', return_value=states):
        assert _desarme_final_state_id(object()) == expected_id


@pytest.mark.parametrize('state_name', ['Desarmado', 'Desarmados', 'Finalizado', 'Finalizada'])
def test_desarme_completed_states_open_full_detail(state_name):
    assert _desarme_state_class(state_name) == 'completed'
