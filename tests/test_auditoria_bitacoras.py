from datetime import datetime
from unittest.mock import patch

from run import app


class AuditCursor:
    columns = [
        'id_bitacora_cliente',
        'id_cliente',
        'fecha_eliminacion',
        'accion',
        'usuario',
        'datos_eliminados',
    ]

    def __init__(self):
        self.description = []

    def execute(self, query, _params=None):
        assert 'FROM [dbo].[bitacora_cliente]' in query
        self.description = [(column,) for column in self.columns]

    def fetchall(self):
        return [
            (
                index, index + 100, datetime(2026, 10, 1, 12, index % 60),
                'Eliminación', f'Usuario {index % 2 + 1}', f'Cliente eliminado {index}',
            )
            for index in range(1, 13)
        ]

    def close(self):
        pass


class AuditConnection:
    def cursor(self):
        return AuditCursor()

    def close(self):
        pass


def audit_client():
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 1
        session['user'] = 'Administrador'
        session['role'] = 'Administrador del Sistema'
    return client


def patched_audit_db():
    return (
        patch('run.get_db_connection', return_value=AuditConnection()),
        patch(
            'run.get_table_columns',
            return_value=AuditCursor.columns,
        ),
        patch('run.get_role_permissions_from_db', return_value={}),
    )


def test_audit_api_reads_client_log_and_paginates_ten_records():
    client = audit_client()
    patches = patched_audit_db()
    with patches[0], patches[1], patches[2]:
        response = client.get('/mantenimiento/auditoria?modulo=clientes&page=2')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['total'] == 12
    assert payload['pagina'] == 2
    assert len(payload['registros']) == 2
    assert payload['registros'][0]['tabla'] == 'bitacora_cliente'
    assert payload['usuarios'] == ['Usuario 1', 'Usuario 2']


def test_audit_exports_filtered_records_as_csv_and_pdf():
    client = audit_client()
    patches = patched_audit_db()
    with patches[0], patches[1], patches[2]:
        csv_response = client.get('/mantenimiento/auditoria/exportar/csv?modulo=clientes')
    assert csv_response.status_code == 200
    assert b'Fecha,Acci' in csv_response.data
    assert b'bitacora_cliente' in csv_response.data

    patches = patched_audit_db()
    with patches[0], patches[1], patches[2]:
        pdf_response = client.get('/mantenimiento/auditoria/exportar/pdf?modulo=clientes')
    assert pdf_response.status_code == 200
    assert pdf_response.data.startswith(b'%PDF')


def test_audit_endpoints_require_administrator():
    client = app.test_client()
    with client.session_transaction() as session:
        session['user_id'] = 2
        session['user'] = 'Empleado'
        session['role'] = 'Empleado'

    with patch('run.get_role_permissions_from_db', return_value={}):
        response = client.get('/mantenimiento/auditoria')

    assert response.status_code == 302


def test_maintenance_page_can_open_audit_section_directly():
    client = audit_client()
    with (
        patch('run.get_role_permissions_from_db', return_value={}),
        patch('run.get_db_connection', return_value=AuditConnection()),
        patch('run.get_mantenimiento_catalogo', return_value=None),
        patch('run.get_table_columns', return_value=[]),
        patch('run.get_current_sidebar_user', return_value={
            'name': 'Administrador',
            'role': 'Administrador del Sistema',
        }),
        patch('run.get_overdue_loan_count', return_value=0),
    ):
        response = client.get('/mantenimiento?vista=audit')

    assert response.status_code == 200
    assert b'Auditor\xc3\xada y Bit\xc3\xa1coras' in response.data
    assert b'data-maintenance-view="database"' in response.data
    assert b'id="audit-filters"' in response.data
