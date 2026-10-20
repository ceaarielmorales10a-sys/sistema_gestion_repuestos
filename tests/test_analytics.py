from types import SimpleNamespace
from unittest.mock import patch

import pytest

from run import _analytics_dashboard_data, _analytics_days, _get_gemini_api_key, app


class AnalyticsCursor:
    def __init__(self):
        self.queries = []
        self.single_rows = iter([(2, 10, 100), (50, 1)])
        self.multiple_rows = iter([
            [],
            [('Motor', 2, 50, 10)],
            [('Disponible', 2, 10)],
            [(1, 'REP-001', 1, 2, 'Motor', 4)],
        ])

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return next(self.single_rows)

    def fetchall(self):
        return next(self.multiple_rows)


class AnalyticsConnection:
    def cursor(self):
        return AnalyticsPageCursor()

    def close(self):
        pass


class AnalyticsPageCursor:
    def close(self):
        pass


def test_analytics_days_only_accepts_supported_ranges():
    assert [_analytics_days(value) for value in ('7', '30', '90')] == [7, 30, 90]


def test_gemini_api_key_reads_updated_dotenv_when_environment_is_empty():
    with (
        patch.dict('run.os.environ', {'GEMINI_API_KEY': ''}),
        patch('run.dotenv_values', return_value={'GEMINI_API_KEY': 'test-key'}),
    ):
        assert _get_gemini_api_key() == 'test-key'


def test_gemini_api_key_prefers_nonempty_environment_variable():
    with (
        patch.dict('run.os.environ', {'GEMINI_API_KEY': 'environment-key'}),
        patch('run.dotenv_values', return_value={'GEMINI_API_KEY': 'dotenv-key'}),
    ):
        assert _get_gemini_api_key() == 'environment-key'


def test_analytics_predictions_only_sum_sales_within_selected_period():
    columns = {
        'dbo.repuesto': [
            'id_repuesto', 'codigo_repuesto', 'stock', 'stock_minimo',
            'precio_venta', 'precio_compra', 'id_tipo_repuesto',
            'id_estado_repuesto',
        ],
        'dbo.venta': ['id_venta', 'fecha_venta', 'total'],
        'dbo.detalle_venta': [
            'id_venta', 'id_repuesto', 'cantidad', 'precio_unitario',
        ],
        'dbo.tipo_repuesto': ['id_tipo_repuesto', 'nombre'],
        'dbo.estado_repuesto': ['id_estado_repuesto', 'estado'],
    }
    cursor = AnalyticsCursor()

    with patch('run.get_table_columns', side_effect=lambda _cursor, table: columns[table]):
        data = _analytics_dashboard_data(cursor, 30)

    prediction_query, params = cursor.queries[-1]
    assert 'INNER JOIN dbo.venta v ON v.id_venta = dv.id_venta' in prediction_query
    assert 'WHERE v.fecha_venta >= DATEADD(day, -(? - 1)' in prediction_query
    assert 'SUM(dv.cantidad) AS vendidas' in prediction_query
    assert 'LEFT JOIN dbo.detalle_venta dv ON dv.id_repuesto' not in prediction_query
    assert params == (30,)
    assert data['predicciones'][0]['dias_agotamiento'] == 7.5
    assert data['predicciones'][0]['requiere_reabastecimiento'] is True


def test_analytics_page_renders_metrics_and_chart_containers():
    dashboard_data = {
        'periodo_dias': 30,
        'metricas': {
            'repuestos': 2,
            'unidades_inventario': 5,
            'ingresos': 125.5,
            'ventas': 3,
            'margen_porcentaje': 20.0,
        },
        'temporal': [],
        'categorias': [],
        'distribucion': [],
        'predicciones': [{
            'codigo': 'REP-001',
            'nombre': 'Repuesto REP-001',
            'categoria': 'Motor',
            'stock': 1,
            'minimo': 2,
            'dias_agotamiento': 7.5,
            'urgencia': 'Reabastecer urgente',
            'requiere_reabastecimiento': True,
        }],
        'repuestos_urgentes': 1,
    }
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['user'] = 'Prueba'
        session['role'] = 'Administrador del Sistema'

    with (
        patch('run.get_db_connection', return_value=AnalyticsConnection()),
        patch('run._analytics_dashboard_data', return_value=dashboard_data),
        patch('run.get_current_sidebar_user', return_value={
            'name': 'Prueba',
            'role': 'Administrador del Sistema',
        }),
        patch('run.get_overdue_loan_count', return_value=0),
    ):
        response = client.get('/analytics')

    assert response.status_code == 200
    assert b'Analítica IA' in response.data
    assert b'Predicciones Inteligentes de Stock' in response.data
    assert b'id="temporal-chart"' in response.data
    assert b'id="inventory-chart"' in response.data
    assert b'REP-001' in response.data


@pytest.mark.parametrize(
    ('path', 'method'),
    [
        ('/api/analytics/dashboard', 'get'),
        ('/api/analytics/stock', 'post'),
    ],
)
def test_analytics_apis_require_module_permission(path, method):
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['user'] = 'Prueba'
        session['role'] = 'Empleado'

    with patch('run.get_role_permissions_from_db', return_value={}):
        response = getattr(client, method)(path, json={'dias': 30})

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/dashboard')


def test_ai_endpoint_uses_dotenv_key_and_returns_analysis():
    connection = AnalyticsConnection()
    gemini_response = SimpleNamespace(text='Recomendación de prueba')
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['user'] = 'Prueba'
        session['role'] = 'Administrador del Sistema'

    with (
        patch.dict('run.os.environ', {'GEMINI_API_KEY': ''}),
        patch('run.dotenv_values', return_value={'GEMINI_API_KEY': 'test-key'}),
        patch('run.get_db_connection', return_value=connection),
        patch('run._analytics_dashboard_data', return_value={'metricas': {}}),
        patch('run.get_overdue_loan_count', return_value=0),
        patch('google.genai.Client') as gemini_client,
    ):
        gemini_client.return_value.models.generate_content.return_value = gemini_response
        response = client.post('/api/analytics/stock', json={'dias': 30})

    assert response.status_code == 200
    assert response.json == {'success': True, 'analisis': 'Recomendación de prueba'}
    gemini_client.assert_called_once_with(api_key='test-key')
    generate_content = gemini_client.return_value.models.generate_content
    assert generate_content.call_args.kwargs['model'] == 'gemini-3.8-flash'
    assert 'Quetzales (Q)' in generate_content.call_args.kwargs['contents']
    assert 'no en dólares' in generate_content.call_args.kwargs['contents']


@pytest.mark.parametrize(
    'api_error',
    [
        ConnectionError('connection failed'),
        RuntimeError('API overloaded'),
    ],
)
def test_ai_endpoint_returns_clear_json_when_gemini_is_unavailable(api_error):
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['user'] = 'Prueba'
        session['role'] = 'Administrador del Sistema'

    with (
        patch.dict('run.os.environ', {'GEMINI_API_KEY': 'test-key'}),
        patch('run.get_db_connection', return_value=AnalyticsConnection()),
        patch('run._analytics_dashboard_data', return_value={'metricas': {}}),
        patch('run.get_overdue_loan_count', return_value=0),
        patch('google.genai.Client') as gemini_client,
    ):
        gemini_client.return_value.models.generate_content.side_effect = api_error
        response = client.post('/api/analytics/stock', json={'dias': 30})

    assert response.status_code == 503
    assert response.is_json
    assert response.json == {
        'success': False,
        'error': 'El servicio de IA no está disponible temporalmente. Intenta de nuevo más tarde.',
    }
