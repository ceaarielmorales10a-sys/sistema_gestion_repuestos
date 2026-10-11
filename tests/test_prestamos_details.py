from datetime import date
from unittest.mock import patch

from run import app


class LoanCursor:
    def __init__(self):
        self.description = []
        self.rows = []

    def execute(self, query, _params=None):
        normalized = ' '.join(query.lower().split())
        if normalized.startswith('select p.id_prestamo'):
            self.description = [(name,) for name in (
                'id_prestamo', 'fecha_prestamo', 'fecha_devolucion_estimada',
                'id_cliente', 'id_usuario', 'observaciones', 'id_estado', 'estado',
            )]
            self.rows = [(
                101, date(2026, 10, 1), date(2026, 10, 8),
                4, 2, 'Préstamo para reparación', 1, 'Activo',
            )]
        elif normalized.startswith('select dp.id_repuesto'):
            self.rows = [
                (10, 2, 15.0, '', 'REP-010'),
                (11, 1, 20.0, '', 'REP-011'),
            ]
        else:
            self.rows = []

    def fetchall(self):
        return self.rows

    def close(self):
        pass

    def commit(self):
        pass


class LoanConnection:
    def cursor(self):
        return LoanCursor()

    def commit(self):
        pass

    def close(self):
        pass


def test_loan_list_shows_aggregated_quantity_total_and_detail_modal():
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['user'] = 'Prueba'
        session['role'] = 'Administrador del Sistema'

    with (
        patch('run.get_db_connection', return_value=LoanConnection()),
        patch('run.get_cliente_full_name', return_value='Cliente de prueba'),
        patch('run.lookup_foreign_key_display', return_value='Usuario de prueba'),
        patch('run.get_repuesto_display_name', side_effect=lambda _cursor, id_repuesto: f'Repuesto {id_repuesto}'),
        patch('run.get_overdue_loan_count', return_value=0),
    ):
        response = client.get('/prestamos')

    assert response.status_code == 200
    assert b'PRECIO TOTAL' in response.data
    assert b'data-label="Cant.">3<' in response.data
    assert b'Q 50.00' in response.data
    assert b'data-open-loan-detail' in response.data
    assert b'id="loan-detail-modal"' in response.data
    assert b'Precio total del pr\xc3\xa9stamo' in response.data
    assert b'id="mobile-menu-button"' in response.data
    assert b'id="mobile-sidebar-overlay"' in response.data
    assert b'id="sidebar"' in response.data
    assert b'loan-create-form' in response.data
    assert b'class="sale-fields loan-create-fields"' in response.data
    assert b'class="items-section loan-create-items"' in response.data
    assert b'id="add-loan-item"' in response.data


def test_loan_create_form_reuses_sales_layout_with_mobile_rules():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    template = (root / 'app' / 'templates' / 'prestamos.html').read_text(encoding='utf-8')
    stylesheet = (root / 'app' / 'static' / 'prestamos.css').read_text(encoding='utf-8')

    assert "filename='nueva_venta.css'" in template
    assert '.prestamos-page .loan-create-form' in stylesheet
    assert '.prestamos-page .loan-create-items .loan-item' in stylesheet
    assert '@media (max-width: 767px)' in stylesheet
