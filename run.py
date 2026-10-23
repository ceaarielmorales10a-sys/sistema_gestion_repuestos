from flask import Flask, g, render_template, request, redirect, url_for, flash, session, jsonify, send_file
import os
import io
import json
import re
import secrets 
import csv
from uuid import uuid4
from datetime import datetime, timedelta
from decimal import Decimal
from xml.sax.saxutils import escape
import pyodbc
try:
    import boto3
except ImportError:
    boto3 = None
from dotenv import dotenv_values, load_dotenv
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash
from werkzeug.security import generate_password_hash
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# Buscamos la ruta absoluta de run.py
base_dir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(base_dir, '.env'))

# Definimos las rutas exactas apuntando a la subcarpeta 'app'
templates_dir = os.path.join(base_dir, 'app', 'templates')
static_dir = os.path.join(base_dir, 'app', 'static')

# Inicializamos Flask vinculando correctamente la subcarpeta 'app'
app = Flask('app', template_folder=templates_dir, static_folder=static_dir)
# En producción defina FLASK_SECRET_KEY con un valor largo y aleatorio.
# Una clave conocida permitiría fabricar cookies de sesión.
app.secret_key = os.getenv('SECRET_KEY') or os.getenv('FLASK_SECRET_KEY') or secrets.token_urlsafe(48)

# S3 se configura con variables de entorno o el proveedor de credenciales de AWS.
AWS_REGION = os.getenv('AWS_REGION', 'us-east-2')
BUCKET_NAME = os.getenv('S3_BUCKET_NAME', 'sistema-repuestos-imagenes-2026')
s3_client = None
if boto3:
    s3_client = boto3.client(
        's3',
        region_name=AWS_REGION,
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID') or None,
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY') or None,
    )

# Configuración de sesión: 10 minutos de inactividad antes de expirar
app.config['PERMANENT_SESSION_LIFETIME'] = 600  # 10 minutos en segundos


def has_permission_in_session(module_key, required_level='read'):
    role_permissions = session.get('role_permissions') or build_default_permissions_for_role(session.get('role'))
    return has_module_permission(role_permissions, module_key, required_level)


app.jinja_env.globals['has_permission'] = has_permission_in_session


def repuesto_photo_url(photo_path):
    if not photo_path:
        return ''
    if str(photo_path).startswith(('https://', 'http://')):
        return photo_path
    return url_for('static', filename=photo_path)


app.jinja_env.globals['repuesto_photo_url'] = repuesto_photo_url
UPLOAD_REPUESTOS_DIR = os.path.join(static_dir, 'uploads', 'repuestos')
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
MAX_IMAGE_SIZE = 3 * 1024 * 1024


@app.before_request
def restringir_modulos_bodeguero():
    # Solo el inicio de sesión y los archivos estáticos son públicos.
    rutas_publicas = {'login', 'static'}
    if request.endpoint not in rutas_publicas and (
        not session.get('user_id') or not session.get('user')
    ):
        if request.path.rstrip('/') == '/mantenimiento/auditoria':
            return jsonify({'error': 'La sesión expiró. Inicia sesión y vuelve a abrir las bitácoras.'}), 401
        return redirect(url_for('login'))

    ruta = request.path.rstrip('/') or '/'
    role = session.get('role')
    role_id = session.get('role_id')

    if role_id is not None:
        role_permissions = get_role_permissions_for_role_id(role_id, role)
    else:
        role_permissions = get_role_permissions_from_db(role)

    session['role_permissions'] = role_permissions
    session['role'] = role or session.get('role')

    admin_only_prefixes = ('/usuario', '/mantenimiento')
    inventory_admin_prefixes = (
        '/inventario/agregar',
        '/inventario/modificar',
        '/inventario/eliminar',
        '/inventario/limpiar-duplicados',
    )

    if role == 'Administrador del Sistema':
        return None

    module_routes = (
        ('/api/analytics', 'analytics'),
        ('/analytics', 'analytics'),
        ('/clientes', 'clientes'),
        ('/inventario', 'inventario'),
        ('/ventas', 'ventas'),
        ('/prestamos', 'prestamos'),
        ('/devoluciones', 'devoluciones'),
        ('/desarme', 'desarme'),
        ('/usuario', 'usuarios'),
        ('/mantenimiento', 'mantenimiento'),
    )
    for prefix, module_key in module_routes:
        if ruta.startswith(prefix) and not has_module_permission(role_permissions, module_key, 'read'):
            if ruta.startswith('/mantenimiento/auditoria'):
                return jsonify({'error': 'No tienes permisos para consultar estas bitácoras.'}), 403
            flash('No tienes permisos para acceder a este módulo.', 'error')
            return redirect(url_for('dashboard'))

    write_routes = (
        ('/clientes', 'clientes', {'POST'}),
        ('/inventario/agregar', 'inventario', {'GET', 'POST'}),
        ('/inventario/modificar', 'inventario', {'GET', 'POST'}),
        ('/inventario/eliminar', 'inventario', {'GET', 'POST'}),
        ('/inventario/limpiar-duplicados', 'inventario', {'GET', 'POST'}),
        ('/ventas/nueva', 'ventas', {'GET', 'POST'}),
        ('/ventas/modificar', 'ventas', {'GET', 'POST'}),
        ('/ventas/eliminar', 'ventas', {'POST'}),
        ('/prestamos/nuevo', 'prestamos', {'POST'}),
        ('/prestamos/', 'prestamos', {'POST'}),
        ('/devoluciones/nueva', 'devoluciones', {'POST'}),
        ('/devoluciones/eliminar', 'devoluciones', {'POST'}),
        ('/desarme', 'desarme', {'POST'}),
        ('/usuario/nuevo', 'usuarios', {'GET', 'POST'}),
        ('/usuario/modificar', 'usuarios', {'GET', 'POST'}),
        ('/usuario/eliminar', 'usuarios', {'POST'}),
        ('/usuario/roles/save', 'usuarios', {'POST'}),
        ('/mantenimiento/catalogo', 'mantenimiento', {'POST'}),
        ('/mantenimiento/tablas/', 'mantenimiento', {'POST'}),
        ('/mantenimiento/backup/restaurar', 'mantenimiento', {'POST'}),
    )
    write_module = next(
        (module_key for prefix, module_key, methods in write_routes
         if ruta.startswith(prefix) and request.method in methods),
        None,
    )
    if write_module and not has_module_permission(role_permissions, write_module, 'write'):
        if request.is_json:
            return jsonify({'error': 'Tu rol solo tiene permiso de lectura en este modulo.'}), 403
        flash('Tu rol solo tiene permiso de lectura en este modulo.', 'error')
        destinations = {
            'clientes': 'clientes', 'inventario': 'inventario', 'ventas': 'ventas',
            'prestamos': 'prestamos', 'devoluciones': 'devoluciones',
            'desarme': 'desarme', 'usuarios': 'usuarios', 'mantenimiento': 'mantenimiento',
        }
        return redirect(url_for(destinations.get(write_module, 'dashboard')))

    if ruta.startswith(inventory_admin_prefixes):
        if not has_module_permission(role_permissions, 'inventario', 'write'):
            flash('No tienes permisos para modificar el inventario.', 'error')
            return redirect(url_for('inventario'))

    return None

# CADENA DE CONEXIÓN A SQL SERVER
def _odbc_value(value):
    """Quote ODBC values so delimiters in credentials remain valid."""
    return '{' + str(value).replace('}', '}}') + '}'


def build_sql_server_connection_string():
    """Build the SQL Server connection from the host's environment."""
    server = os.getenv('DB_SERVER')
    database = os.getenv('DB_NAME')
    username = os.getenv('DB_USER')
    password = os.getenv('DB_PASSWORD')
    missing = [name for name, value in (
        ('DB_SERVER', server), ('DB_NAME', database),
        ('DB_USER', username), ('DB_PASSWORD', password),
    ) if not value]
    if missing:
        raise RuntimeError(
            'Falta configurar la conexión a SQL Server: ' + ', '.join(missing) +
            '. Configure estas variables en el entorno del servicio AWS.'
        )

    port = os.getenv('DB_PORT')
    if port and ',' not in server:
        server = f'{server},{port}'
    driver = os.getenv('DB_DRIVER', 'ODBC Driver 17 for SQL Server')
    encrypt = os.getenv('DB_ENCRYPT', 'yes')
    trust_certificate = os.getenv('DB_TRUST_SERVER_CERTIFICATE', 'yes')
    return (
        f'Driver={_odbc_value(driver)};'
        f'Server={_odbc_value(server)};'
        f'Database={_odbc_value(database)};'
        f'UID={_odbc_value(username)};'
        f'PWD={_odbc_value(password)};'
        f'Encrypt={encrypt};'
        f'TrustServerCertificate={trust_certificate};'
        'Connection Timeout=15;'
    )


CONN_STR = build_sql_server_connection_string()


def get_db_connection():
    """Devuelve una conexión pyodbc usando la cadena CONN_STR."""
    try:
        return pyodbc.connect(CONN_STR)
    except pyodbc.Error as exc:
        app.logger.exception('No se pudo conectar con SQL Server.')
        raise RuntimeError(
            'No se pudo conectar con la base de datos SQL Server. Verifique el '
            'endpoint, puerto 1433, credenciales y reglas de red del servicio AWS.'
        ) from exc


def get_overdue_loan_count():
    if hasattr(g, 'overdue_loan_count'):
        return g.overdue_loan_count

    g.overdue_loan_count = 0
    if not session.get('user_id') or not has_permission_in_session('prestamos', 'read'):
        return g.overdue_loan_count

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM prestamo p "
            "LEFT JOIN estado_prestamo ep ON ep.id_estado_prestamo = p.id_estado "
            "WHERE LOWER(LTRIM(RTRIM(ep.estado))) = 'vencido' "
            "OR (LOWER(LTRIM(RTRIM(ep.estado))) = 'activo' "
            "AND p.fecha_devolucion_estimada < CAST(GETDATE() AS DATE))"
        )
        g.overdue_loan_count = cursor.fetchone()[0] or 0
    finally:
        cursor.close()
        conn.close()

    return g.overdue_loan_count


@app.context_processor
def inject_overdue_loan_count():
    return {'vencidos': get_overdue_loan_count()}


def get_estado_usuario_id(cursor, activo):
    """Obtiene un estado válido para usuario; 0 no es una clave foránea válida."""
    if activo:
        return 1

    try:
        columns = get_table_columns(cursor, 'estado_general')
        display_column = choose_display_column(columns)
        if display_column:
            cursor.execute(
                f"SELECT TOP 1 id_estado FROM estado_general "
                f"WHERE LOWER(LTRIM(RTRIM({display_column}))) = ?",
                ('inactivo',),
            )
            row = cursor.fetchone()
            if row:
                return row[0]
    except Exception:
        pass

    # En el catálogo estándar: 1 = Activo y 2 = Inactivo.
    return 2


def get_estado_general_id(cursor, activo):
    """Resolve the active/inactive foreign key from estado_general by its label."""
    columns = get_table_columns(cursor, 'estado_general')
    id_column = next(
        (column for column in columns if column.lower() == 'id_estado'),
        next((column for column in columns if column.lower().startswith('id')), None),
    )
    display_column = choose_display_column(columns)
    if not id_column or not display_column:
        raise ValueError('No se encontró el catálogo de estados generales.')

    state_name = 'activo' if activo else 'inactivo'
    cursor.execute(
        f"SELECT TOP 1 [{id_column}] FROM estado_general "
        f"WHERE LOWER(LTRIM(RTRIM([{display_column}]))) = ?",
        (state_name,),
    )
    row = cursor.fetchone()
    if not row:
        raise ValueError(f'No existe el estado general «{state_name}».')
    return row[0]


def split_repuestos_por_estado(repuestos):
    """Keep inactive inventory separate while treating unknown legacy states as active."""
    activos = []
    inactivos = []
    for repuesto in repuestos:
        if str(repuesto.get('estado') or '').strip().casefold() == 'inactivo':
            inactivos.append(repuesto)
        else:
            activos.append(repuesto)
    return activos, inactivos


def get_table_columns(cursor, table_name):
    try:
        cursor.execute(f"SELECT TOP 1 * FROM {table_name}")
        return [col[0] for col in cursor.description]
    except Exception:
        return []


def choose_display_column(columns):
    # Prefer columns that contain these tokens
    # La descripción nunca es el nombre principal de un catálogo.  Esto es
    # importante para tablas como vehiculo_origen, que usan `origen` en vez
    # de `nombre` y ahora también tienen una columna `descripcion`.
    preferred = [
        'nombre', 'motivo', 'origen', 'estado', 'transmision', 'ubicacion',
        'rol', 'tipo', 'marca', 'modelo', 'color', 'lado', 'posicion',
        'name', 'descripcion'
    ]
    for token in preferred:
        for c in columns:
            if token in c.lower() and not c.lower().startswith('id'):
                return c
    # fallback: first non-id column
    for c in columns:
        if not c.lower().startswith('id'):
            return c
    return None


def choose_numeric_column(columns):
    """Choose a column name likely to contain numeric stock/quantity values."""
    preferred = ['stock', 'cantidad', 'existencia', 'qty', 'cantidad_disponible', 'cantidad_total', 'cantidad_stock', 'unidades', 'disponible']
    for token in preferred:
        for c in columns:
            if token in c.lower() and not c.lower().startswith('id'):
                return c
    # fallback: any column that contains 'cant' or 'stock'
    for c in columns:
        if 'cant' in c.lower() or 'stock' in c.lower() or 'disp' in c.lower():
            return c
    return None


def negative_form_number_label(values):
    """Devuelve la etiqueta del primer valor numérico negativo recibido."""
    for label, value in values.items():
        if value in (None, ''):
            continue
        try:
            if float(str(value).replace(',', '.')) < 0:
                return label
        except (TypeError, ValueError):
            continue
    return None


def matches_multiword_search(search_query, record, fields):
    """Check if all words in search_query match any of the fields in record.
    Returns True if every word in the query is found in at least one field.
    """
    if not search_query:
        return True
    
    words = [w.strip().lower() for w in search_query.split() if w.strip()]
    if not words:
        return True
    
    for word in words:
        found = False
        for field in fields:
            value = str(record.get(field, '')).lower()
            if word in value:
                found = True
                break
        if not found:
            return False
    return True


def choose_numeric_column_by_sample(columns, rows):
    """Choose a numeric column from the first row sample if name heuristics fail."""
    if not rows or not columns:
        return None

    import decimal

    deny_tokens = ['precio', 'valor', 'costo', 'importe']
    priority_tokens = ['stock', 'cant', 'exist', 'disp', 'unid']

    # prefer a name-based numeric column in the sample on the first row
    for token in priority_tokens:
        for idx, col in enumerate(columns):
            if token in col.lower() and not col.lower().startswith('id'):
                value = rows[0][idx]
                if value is not None:
                    return col

    for idx, col in enumerate(columns):
        if col.lower().startswith('id'):
            continue
        if any(t in col.lower() for t in deny_tokens):
            continue
        value = rows[0][idx]
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float, decimal.Decimal)):
            return col
        if isinstance(value, str) and value.strip().replace('.', '', 1).replace(',', '', 1).isdigit():
            return col
    return None


def fetch_catalog_for_candidates(cursor, candidates, include_inactive=False):
    """Try candidate table names and return list of {'id','nombre'} or None."""
    for table in candidates:
        cols = get_table_columns(cursor, table)
        if not cols:
            continue
        id_col = None
        for c in cols:
            if c.lower().startswith('id'):
                id_col = c
                break
        display_col = choose_display_column(cols)
        has_estado = any(c.lower() == 'id_estado' for c in cols)
        if id_col and display_col:
            try:
                if has_estado and not include_inactive:
                    cursor.execute(f"SELECT {id_col} AS id, {display_col} AS nombre FROM {table} WHERE id_estado = 1 ORDER BY {display_col}")
                else:
                    cursor.execute(f"SELECT {id_col} AS id, {display_col} AS nombre FROM {table} ORDER BY {display_col}")
                return cursor.fetchall()
            except Exception:
                continue
    return []


def fetch_modelos_by_marca(cursor, id_marca):
    try:
        cols = get_table_columns(cursor, 'modelo_vehiculo')
        has_estado = any(c.lower() == 'id_estado' for c in cols)
        if has_estado:
            cursor.execute(
                "SELECT id_modelo AS id, nombre FROM modelo_vehiculo WHERE id_marca = ? AND id_estado = 1 ORDER BY nombre",
                (id_marca,)
            )
        else:
            cursor.execute(
                "SELECT id_modelo AS id, nombre FROM modelo_vehiculo WHERE id_marca = ? ORDER BY nombre",
                (id_marca,)
            )
        return cursor.fetchall()
    except Exception:
        return []


def get_display_name_by_id(cursor, table_candidates, id_value):
    for table in table_candidates:
        cols = get_table_columns(cursor, table)
        if not cols:
            continue
        id_col = None
        for c in cols:
            if c.lower().startswith('id'):
                id_col = c
                break
        display_col = choose_display_column(cols)
        if id_col and display_col:
            try:
                cursor.execute(f"SELECT {display_col} FROM {table} WHERE {id_col} = ?", (id_value,))
                row = cursor.fetchone()
                if row:
                    return row[0]
            except Exception:
                continue
    return None


def get_user_full_name(cursor, user_id):
    """Return the user's complete name, falling back to their username."""
    if user_id is None:
        return None
    try:
        cursor.execute(
            "SELECT primer_nombre, segundo_nombre, primer_apellido, segundo_apellido, username "
            "FROM usuario WHERE id_usuario = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        full_name = ' '.join(str(part).strip() for part in row[:4] if part and str(part).strip())
        return full_name or (str(row[4]).strip() if row[4] else None)
    except Exception:
        return None


def lookup_foreign_key_display(cursor, fk_column, fk_value, table_candidates=None):
    if fk_value is None:
        return None

    if table_candidates is None:
        table_candidates = []

    table_candidates = list(table_candidates)
    for table in table_candidates:
        name = get_display_name_by_id(cursor, [table], fk_value)
        if name:
            return name

    return None


def get_cliente_full_name(cursor, id_cliente):
    """Obtiene el nombre visible del cliente desde su registro real."""
    if id_cliente is None:
        return None
    try:
        columns = get_table_columns(cursor, 'cliente')
        id_column = cliente_field(columns, 'id_cliente') or next(
            (column for column in columns if str(column).lower().startswith('id')), None
        )
        if not id_column:
            return None

        cursor.execute(f"SELECT * FROM cliente WHERE [{id_column}] = ?", (id_cliente,))
        row = cursor.fetchone()
        if not row:
            return None

        data = dict(zip(columns, row))
        name = cliente_display_from_row(data)
        if not name.startswith('Cliente #'):
            return name

        # El registro puede no tener nombres, pero aún puede identificarse
        # desde un dato que sí existe en la tabla.
        email = data.get(cliente_field(columns, 'correo', 'email'))
        nit = data.get(cliente_field(columns, 'nit'))
        return str(email or nit or name).strip()
    except Exception:
        return None
    return None


def get_repuesto_display_name(cursor, id_repuesto):
    if id_repuesto is None:
        return None
    try:
        cursor.execute(
            "SELECT codigo_repuesto, observaciones, id_tipo_repuesto, id_marca, id_modelo, id_color, id_lado, id_posicion "
            "FROM repuesto WHERE id_repuesto = ?",
            (id_repuesto,)
        )
        row = cursor.fetchone()
        if row:
            codigo, observaciones, id_tipo, id_marca, id_modelo, id_color, id_lado, id_posicion = row
            parts = []
            if codigo:
                parts.append(str(codigo))
            tipo = lookup_foreign_key_display(cursor, 'id_tipo_repuesto', id_tipo, ['tipo_repuesto'])
            if tipo:
                parts.append(tipo)
            marca = lookup_foreign_key_display(cursor, 'id_marca', id_marca, ['marca', 'marca_vehiculo'])
            if marca:
                parts.append(marca)
            modelo = lookup_foreign_key_display(cursor, 'id_modelo', id_modelo, ['modelo', 'modelo_vehiculo'])
            if modelo:
                parts.append(modelo)
            color = lookup_foreign_key_display(cursor, 'id_color', id_color, ['color', 'color_repuesto'])
            if color:
                parts.append(color)
            lado = lookup_foreign_key_display(cursor, 'id_lado', id_lado, ['lado', 'lado_repuesto'])
            if lado:
                parts.append(lado)
            posicion = lookup_foreign_key_display(cursor, 'id_posicion', id_posicion, ['posicion', 'posicion_repuesto'])
            if posicion:
                parts.append(posicion)
            if len(parts) > 1:
                return ' - '.join(parts)
            if observaciones:
                return str(observaciones)
            if codigo:
                return str(codigo)
    except Exception:
        pass
    return None


def get_repuesto_year(cursor, id_repuesto):
    """Devuelve el año asociado al repuesto sin alterar su nombre visible."""
    if id_repuesto is None:
        return None
    try:
        cursor.execute('SELECT id_anio FROM repuesto WHERE id_repuesto = ?', (id_repuesto,))
        row = cursor.fetchone()
        if not row or row[0] is None:
            return None
        return lookup_foreign_key_display(cursor, 'id_anio', row[0], ['anio_vehiculo']) or str(row[0])
    except Exception:
        return None


def get_repuestos_registrados(cursor):
    """Obtiene una vista compacta de los repuestos registrados para mostrar como fondo."""
    try:
        cursor.execute(
            "SELECT TOP 50 id_repuesto, codigo_repuesto, stock, precio_venta, observaciones "
            "FROM repuesto ORDER BY id_repuesto DESC"
        )
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(cols, row)) for row in rows]
    except Exception:
        return []


def insert_row_filtered(cursor, table_name, data_dict):
    """Insert into table_name only the keys from data_dict that exist as columns."""
    cols = get_table_columns(cursor, table_name)
    if not cols:
        raise Exception(f"Tabla '{table_name}' no encontrada o sin columnas accesibles")

    insert_cols = []
    values = []
    for key, val in data_dict.items():
        if key in cols:
            insert_cols.append(key)
            values.append(val)

    if not insert_cols:
        raise Exception("Ninguna columna válida encontrada para insertar")

    placeholders = ', '.join(['?'] * len(values))
    cols_sql = ', '.join(insert_cols)
    sql = f"INSERT INTO {table_name} ({cols_sql}) VALUES ({placeholders})"
    cursor.execute(sql, tuple(values))
    return cursor.rowcount


def ensure_repuesto_photo_column(cursor):
    """Agrega el campo de fotografía una única vez en instalaciones antiguas."""
    columns = get_table_columns(cursor, 'repuesto')
    if not any(column.lower() == 'fotografia' for column in columns):
        cursor.execute("ALTER TABLE repuesto ADD fotografia VARCHAR(500) NULL")


def save_repuesto_photo(file_storage):
    """Valida y guarda una foto en S3 o, si no está disponible, localmente."""
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError('La fotografía debe ser JPG, PNG o WEBP.')

    file_storage.stream.seek(0, os.SEEK_END)
    file_size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if file_size > MAX_IMAGE_SIZE:
        raise ValueError('La fotografía no puede superar 3 MB.')

    photo_name = f"{uuid4().hex}{extension}"
    if s3_client:
        object_key = f'repuestos/{photo_name}'
        try:
            file_storage.stream.seek(0)
            s3_client.upload_fileobj(
                file_storage.stream,
                BUCKET_NAME,
                object_key,
                ExtraArgs={'ContentType': file_storage.mimetype or 'application/octet-stream'},
            )
            return f'https://{BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{object_key}'
        except Exception:
            app.logger.exception('No se pudo subir la fotografía a S3; se guardará localmente.')

    os.makedirs(UPLOAD_REPUESTOS_DIR, exist_ok=True)
    file_storage.stream.seek(0)
    file_storage.save(os.path.join(UPLOAD_REPUESTOS_DIR, photo_name))
    return f"uploads/repuestos/{photo_name}"


def get_uploaded_repuesto_photo():
    """Obtiene el archivo elegido entre cámara y galería del formulario."""
    return next(
        (file_storage for file_storage in request.files.getlist('fotografia') if file_storage and file_storage.filename),
        None,
    )


def get_tipo_movimiento_by_id(cursor, id_tipo_movimiento):
    if id_tipo_movimiento is None:
        return None
    try:
        cursor.execute(
            "SELECT id_tipo_movimiento, nombre, descripcion, id_estado FROM tipo_movimiento WHERE id_tipo_movimiento = ?",
            (id_tipo_movimiento,)
        )
        row = cursor.fetchone()
        if row:
            return {
                'id_tipo_movimiento': row[0],
                'nombre': row[1],
                'descripcion': row[2],
                'id_estado': row[3],
            }
    except Exception:
        pass
    return None


def get_all_tipos_movimiento(cursor):
    """Obtiene todos los tipos de movimiento de la base de datos."""
    try:
        cursor.execute(
            "SELECT id_tipo_movimiento, nombre, descripcion, id_estado FROM tipo_movimiento ORDER BY nombre"
        )
        rows = cursor.fetchall()
        tipos = []
        for row in rows:
            tipos.append({
                'id_tipo_movimiento': row[0],
                'nombre': row[1],
                'descripcion': row[2],
                'id_estado': row[3],
            })
        return tipos
    except Exception:
        return []


def registrar_movimiento_inventario(cursor, id_repuesto, id_tipo_movimiento, cantidad, detalle, id_usuario=None, id_estado=1, fecha_movimiento=None):
    if id_repuesto is None:
        return None
    if fecha_movimiento is None:
        fecha_movimiento = datetime.now()
    if id_usuario is None:
        id_usuario = session.get('user_id') if 'user_id' in session else None

    tipo_mov = get_tipo_movimiento_by_id(cursor, id_tipo_movimiento)
    if tipo_mov is None:
        try:
            cursor.execute(
                "SELECT id_tipo_movimiento FROM tipo_movimiento WHERE LOWER(nombre) = LOWER(?) OR LOWER(descripcion) = LOWER(?)",
                (str(id_tipo_movimiento), str(id_tipo_movimiento))
            )
            row = cursor.fetchone()
            if row:
                id_tipo_movimiento = row[0]
            else:
                return None
        except Exception:
            return None

    # Prevent duplicate movements: check if an identical movement was created in the last minute
    try:
        fecha_str = str(fecha_movimiento).split('.')[0] if fecha_movimiento else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        fecha_date = fecha_str.split(' ')[0]
        cursor.execute(
            "SELECT COUNT(*) FROM movimiento_inventario WHERE id_repuesto = ? AND id_tipo_movimiento = ? AND detalle = ? AND id_usuario = ? AND CAST(fecha_movimiento AS DATE) = ?",
            (id_repuesto, id_tipo_movimiento, detalle or 'Movimiento de inventario', id_usuario, fecha_date)
        )
        duplicate_count = cursor.fetchone()[0]
        if duplicate_count > 0:
            return 0
    except Exception:
        pass

    try:
        cursor.execute(
            "INSERT INTO movimiento_inventario (id_repuesto, id_tipo_movimiento, fecha_movimiento, cantidad, detalle, id_usuario, id_estado) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id_repuesto, id_tipo_movimiento, fecha_movimiento, cantidad or 0, detalle or 'Movimiento de inventario', id_usuario, id_estado)
        )
        return cursor.rowcount
    except Exception:
        return None


def get_movimientos_inventario(cursor):
    try:
        cursor.execute(
            "SELECT m.id_movimiento, m.id_repuesto, m.id_tipo_movimiento, m.fecha_movimiento, m.cantidad, m.detalle, m.id_usuario, m.id_estado, r.codigo_repuesto "
            "FROM movimiento_inventario m LEFT JOIN repuesto r ON r.id_repuesto = m.id_repuesto ORDER BY m.fecha_movimiento DESC"
        )
        rows = cursor.fetchall()
        columns = [c[0] for c in cursor.description]
        movimientos = []
        for row in rows:
            data = dict(zip(columns, row))
            tipo_mov = get_tipo_movimiento_by_id(cursor, data.get('id_tipo_movimiento'))
            usuario_nombre = None
            if data.get('id_usuario'):
                try:
                    cursor.execute("SELECT username, primer_nombre, segundo_nombre, primer_apellido, segundo_apellido FROM usuario WHERE id_usuario = ?", (data.get('id_usuario'),))
                    user_row = cursor.fetchone()
                    if user_row:
                        nombre_parts = [p for p in [user_row[1], user_row[2], user_row[3], user_row[4]] if p]
                        usuario_nombre = ' '.join(nombre_parts) or user_row[0]
                except Exception:
                    usuario_nombre = None
            repuesto_nombre = get_repuesto_display_name(cursor, data.get('id_repuesto')) or (data.get('codigo_repuesto') or f"ID {data.get('id_repuesto') or '-'}")
            movimientos.append({
                'id_movimiento': data.get('id_movimiento'),
                'id_repuesto': data.get('id_repuesto'),
                'repuesto_nombre': repuesto_nombre,
                'codigo_repuesto': data.get('codigo_repuesto') or '-',
                'id_tipo_movimiento': data.get('id_tipo_movimiento'),
                'tipo_movimiento': tipo_mov.get('nombre') if tipo_mov else 'Sin tipo',
                'fecha_movimiento': data.get('fecha_movimiento'),
                'cantidad': data.get('cantidad') or 0,
                'detalle': data.get('detalle') or '-',
                'usuario': usuario_nombre or 'Sistema',
                'estado': 'Activo' if data.get('id_estado') == 1 else ('Inactivo' if data.get('id_estado') else 'Sin Estado'),
                'procedencia': '-',
            })
        return movimientos
    except Exception:
        return []


def parse_int_like(val):
    """Try to parse various numeric formats into an int (robust to commas, spaces)."""
    if val is None:
        return None
    # If already numeric
    try:
        if isinstance(val, int):
            return val
        if isinstance(val, float):
            return int(round(val))
    except Exception:
        pass

    s = str(val).strip()
    # Try simple float parse
    try:
        return int(round(float(s)))
    except Exception:
        pass

    # Try comma as decimal separator
    try:
        s2 = s.replace('.', '').replace(',', '.')
        return int(round(float(s2)))
    except Exception:
        pass

    # Remove non-digit characters except - and .
    import re
    m = re.search(r"-?[0-9]+(?:[.,][0-9]+)?", s)
    if m:
        try:
            candidate = m.group(0).replace(',', '.')
            return int(round(float(candidate)))
        except Exception:
            pass
    return 0

MODULOS_SISTEMA = [
    ('analytics', 'Analítica IA'),
    ('clientes', 'Clientes'),
    ('inventario', 'Inventario'),
    ('ventas', 'Ventas'),
    ('prestamos', 'Préstamos'),
    ('devoluciones', 'Devoluciones'),
    ('desarme', 'Desarme de Vehículos'),
    ('usuarios', 'Usuarios'),
    ('mantenimiento', 'Mantenimiento'),
]

PERMISSION_ORDER = {'none': 0, 'read': 1, 'write': 2}


def normalize_permission_value(value):
    if value is None:
        return 'none'
    normalized = str(value).strip().lower()
    if not normalized:
        return 'none'
    if normalized in {'sin acceso', 'none', '0'}:
        return 'none'
    if normalized in {'solo lectura', 'read', 'lectura', '1'}:
        return 'read'
    if normalized in {'lectura y escritura', 'write', 'escritura', '2'}:
        return 'write'
    return normalized if normalized in {'none', 'read', 'write'} else 'none'


DEFAULT_ROLE_PERMISSIONS = {
    'administrador del sistema': {key: 'write' for key, _ in MODULOS_SISTEMA},
    'vendedor': {
        'analytics': 'read',
        'clientes': 'read',
        'inventario': 'read',
        'ventas': 'write',
        'prestamos': 'read',
        'devoluciones': 'read',
        'desarme': 'none',
        'usuarios': 'none',
        'mantenimiento': 'none',
    },
    'bodeguero': {
        'analytics': 'read',
        'clientes': 'none',
        'inventario': 'write',
        'ventas': 'none',
        'prestamos': 'none',
        'devoluciones': 'none',
        'desarme': 'write',
        'usuarios': 'none',
        'mantenimiento': 'none',
    },
    'almacenista': {
        'analytics': 'read',
        'clientes': 'read',
        'inventario': 'write',
        'ventas': 'none',
        'prestamos': 'read',
        'devoluciones': 'read',
        'desarme': 'read',
        'usuarios': 'none',
        'mantenimiento': 'none',
    },
}


def _normalize_role_name(role_name):
    """Normaliza el nombre del rol a la forma canónica para buscar permisos."""
    if not role_name:
        return ''
    normalized = str(role_name).strip().lower()
    if 'admin' in normalized:
        return 'administrador del sistema'
    if 'vendedor' in normalized:
        return 'vendedor'
    if 'bodeguero' in normalized:
        return 'bodeguero'
    if 'almacen' in normalized:
        return 'almacenista'
    return normalized


def build_default_permissions_for_role(role_name):
    role_key = _normalize_role_name(role_name)
    return DEFAULT_ROLE_PERMISSIONS.get(role_key, {key: 'none' for key, _ in MODULOS_SISTEMA})


def ensure_role_permission_tables(cursor):
    try:
        cursor.execute("SELECT TOP 1 * FROM modulo")
    except Exception:
        try:
            cursor.execute("""
                CREATE TABLE dbo.modulo (
                    id_modulo INT IDENTITY(1,1) PRIMARY KEY,
                    clave NVARCHAR(50) NOT NULL UNIQUE,
                    nombre NVARCHAR(100) NOT NULL,
                    descripcion NVARCHAR(250) NULL
                )
            """)
        except Exception:
            return False

    try:
        cursor.execute("SELECT TOP 1 * FROM rol_modulo")
    except Exception:
        try:
            cursor.execute("""
                CREATE TABLE dbo.rol_modulo (
                    id_rol_modulo INT IDENTITY(1,1) PRIMARY KEY,
                    id_rol INT NOT NULL,
                    id_modulo INT NOT NULL,
                    acceso NVARCHAR(20) NOT NULL DEFAULT 'none',
                    CONSTRAINT FK_rol_modulo_rol FOREIGN KEY (id_rol) REFERENCES dbo.rol(id_rol),
                    CONSTRAINT FK_rol_modulo_modulo FOREIGN KEY (id_modulo) REFERENCES dbo.modulo(id_modulo),
                    CONSTRAINT UQ_rol_modulo UNIQUE (id_rol, id_modulo)
                )
            """)
        except Exception:
            return False

    return True


def seed_default_modules(cursor):
    if not ensure_role_permission_tables(cursor):
        return
    for key, name in MODULOS_SISTEMA:
        try:
            cursor.execute("SELECT id_modulo FROM modulo WHERE clave = ?", (key,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO modulo (clave, nombre, descripcion) VALUES (?, ?, ?)",
                    (key, name, f'Módulo {name}')
                )
        except Exception:
            pass


def get_module_id_by_key(cursor, module_key):
    try:
        cursor.execute("SELECT id_modulo FROM modulo WHERE clave = ?", (module_key,))
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def get_role_permissions_for_role_id(role_id, fallback_name=None):
    role_name = fallback_name or ''
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if role_id is None:
            cursor.close(); conn.close()
            return build_default_permissions_for_role(role_name)

        cursor.execute("SELECT nombre FROM rol WHERE id_rol = ?", (int(role_id),))
        role_row = cursor.fetchone()
        if role_row:
            role_name = role_row[0] or role_name

        role_name = _normalize_role_name(role_name)

        if not ensure_role_permission_tables(cursor):
            cursor.close(); conn.close()
            return build_default_permissions_for_role(role_name)

        seed_default_modules(cursor)
        cursor.execute(
            "SELECT m.clave, rm.acceso FROM rol_modulo rm INNER JOIN modulo m ON m.id_modulo = rm.id_modulo WHERE rm.id_rol = ?",
            (int(role_id),),
        )
        rows = cursor.fetchall()
        permissions = {key: 'none' for key, _ in MODULOS_SISTEMA}
        for module_key, access in [(row[0], row[1]) for row in rows]:
            permissions[module_key] = normalize_permission_value(access)

        cursor.close(); conn.close()
        if rows:
            return permissions
        return build_default_permissions_for_role(role_name)
    except Exception:
        return build_default_permissions_for_role(role_name)


def get_role_permissions_from_db(role_name):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if not ensure_role_permission_tables(cursor):
            cursor.close(); conn.close()
            return build_default_permissions_for_role(role_name)

        seed_default_modules(cursor)
        role_lower = _normalize_role_name(role_name)
        cursor.execute(
            "SELECT r.id_rol, r.nombre FROM rol r WHERE LOWER(r.nombre) = ?",
            (role_lower,),
        )
        role_row = cursor.fetchone()
        if not role_row:
            cursor.close(); conn.close()
            return build_default_permissions_for_role(role_name)

        role_id = role_row[0]
        cursor.execute(
            "SELECT m.clave, rm.acceso FROM rol_modulo rm INNER JOIN modulo m ON m.id_modulo = rm.id_modulo WHERE rm.id_rol = ?",
            (role_id,),
        )
        rows = cursor.fetchall()
        permissions = {key: 'none' for key, _ in MODULOS_SISTEMA}
        for module_key, access in [(row[0], row[1]) for row in rows]:
            permissions[module_key] = normalize_permission_value(access)
        for key, _ in MODULOS_SISTEMA:
            permissions.setdefault(key, 'none')
        cursor.close(); conn.close()
        if rows:
            return permissions
        return build_default_permissions_for_role(role_name)
    except Exception:
        return build_default_permissions_for_role(role_name)


def sync_session_role_permissions(role_name, role_id=None):
    permissions = get_role_permissions_for_role_id(role_id, role_name) if role_id is not None else get_role_permissions_from_db(role_name)
    session['role_permissions'] = permissions
    return permissions


def get_permission_value_for_module(role_permissions, module_key):
    if not isinstance(role_permissions, dict):
        return 'none'
    return normalize_permission_value(role_permissions.get(module_key, 'none'))


def has_module_permission(role_permissions, module_key, required_level='read'):
    permissions = role_permissions or {}
    if not module_key:
        return False
    current = normalize_permission_value(permissions.get(module_key, 'none'))
    if current == 'write':
        return True
    if required_level == 'write':
        return current == 'write'
    return current in {'read', 'write'}


def get_role_permission_rows(cursor, role_id):
    if role_id is None:
        return []
    if not ensure_role_permission_tables(cursor):
        return []
    cursor.execute(
        "SELECT m.clave, m.nombre, rm.acceso FROM rol_modulo rm INNER JOIN modulo m ON m.id_modulo = rm.id_modulo WHERE rm.id_rol = ? ORDER BY m.nombre",
        (role_id,),
    )
    return cursor.fetchall()


def get_role_rows_with_permissions(cursor):
    try:
        seed_default_modules(cursor)
    except Exception:
        pass

    try:
        cursor.execute("SELECT id_rol, nombre, descripcion FROM rol ORDER BY nombre")
        roles = cursor.fetchall()
    except Exception:
        return []

    result = []
    for role_id, role_name, description in roles:
        permissions = build_default_permissions_for_role(role_name)
        stored_rows = get_role_permission_rows(cursor, role_id)
        if stored_rows:
            for module_key, module_name, access in stored_rows:
                permissions[module_key] = normalize_permission_value(access)
        result.append({
            'id': role_id,
            'nombre': role_name,
            'descripcion': description or '',
            'permisos': permissions,
        })
    return result


def get_roles_with_permissions(cursor):
    permission_tables_available = ensure_role_permission_tables(cursor)
    try:
        cursor.execute("SELECT id_rol, nombre, descripcion FROM rol ORDER BY nombre")
        roles_db = cursor.fetchall()
    except Exception:
        return [], permission_tables_available

    roles = []
    for role_id, role_name, description in roles_db:
        permissions = {key: 'none' for key, _ in MODULOS_SISTEMA}
        stored_rows = get_role_permission_rows(cursor, role_id) if permission_tables_available else []
        if stored_rows:
            for module_key, module_name, access in stored_rows:
                permissions[module_key] = normalize_permission_value(access)
        elif permission_tables_available:
            permissions = build_default_permissions_for_role(role_name)
        roles.append({
            'id': role_id,
            'nombre': role_name,
            'descripcion': description or '',
            'permisos': permissions,
        })
    return roles, permission_tables_available


def validar_usuario_db(usuario, password):
    try:
        conn = pyodbc.connect(CONN_STR)
        cursor = conn.cursor()

        # 1. Consultamos trayendo el password_hash y estado del usuario
        query = (
            "SELECT u.username, u.id_rol, u.password_hash, u.id_estado, r.nombre "
            "FROM usuario u LEFT JOIN rol r ON r.id_rol = u.id_rol "
            "WHERE u.username = ?"
        )
        cursor.execute(query, (usuario,))
        row = cursor.fetchone()

        cursor.close()
        conn.close()

        if row:
            username_db, id_rol, hash_guardado, id_estado, rol_nombre = row[0], row[1], row[2], row[3], row[4]

            # 2. Validamos el texto ingresado contra el hash seguro de la base de datos
            if check_password_hash(hash_guardado, password):
                rol_texto = str(rol_nombre or '').strip() or 'Usuario'
                rol_normalizado = rol_texto.casefold()

                # Mantener exactamente el nombre del rol guardado en la BD para roles personalizados.
                # Sólo usamos los nombres clásicos como alias visual si el rol real lo coincide.
                if 'admin' in rol_normalizado or id_rol == 1:
                    rol_texto = 'Administrador del Sistema'
                elif 'vendedor' in rol_normalizado or id_rol == 3:
                    rol_texto = 'Vendedor'
                elif 'bodeguero' in rol_normalizado or id_rol == 4:
                    rol_texto = 'Bodeguero'
                elif 'almacen' in rol_normalizado or id_rol == 2:
                    rol_texto = 'Almacenista'

                # Normalize role name to match DEFAULT_ROLE_PERMISSIONS keys (lowercase)
                role_for_permissions = rol_texto.casefold()

                if id_estado != 1:
                    return {"user": username_db, "role": rol_texto, "role_id": id_rol, "active": False}

                return {"user": username_db, "role": rol_texto, "role_id": id_rol, "active": True}

        return None

    except Exception as e:
        print("\n" + "=" * 50)
        print(f"⚠️ ERROR DE CONEXIÓN CON SQL SERVER: {e}")
        print("=" * 50 + "\n")
        return None


def get_user_id_by_username(username):
    conn = None
    cursor = None
    try:
        conn = pyodbc.connect(CONN_STR)
        cursor = conn.cursor()
        cursor.execute('SELECT id_usuario FROM usuario WHERE username = ?', (username,))
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def get_user_full_name_by_id(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT primer_nombre, segundo_nombre, primer_apellido, segundo_apellido "
            "FROM usuario WHERE id_usuario = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            parts = [p for p in [row[0], row[1], row[2], row[3]] if p]
            full_name = ' '.join(parts)
            return full_name or None
    except Exception as e:
        print(f"⚠️ ERROR al obtener el nombre de usuario: {e}")
    return None


def validar_nombre_persona(valor):
    if valor is None:
        return False
    texto = str(valor).strip()
    if not texto:
        return False
    return bool(re.fullmatch(r"[A-Za-zÁÉÍÓÚáéíóúÑñÜü\s]+", texto))


def get_current_sidebar_user():
    """Obtiene nombre y rol actualizados para el pie del menú lateral."""
    sidebar_user = {
        'name': session.get('full_name') or session.get('user', 'Usuario'),
        'role': session.get('role', 'Usuario')
    }
    user_id = session.get('user_id')
    if not user_id:
        return sidebar_user
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT u.primer_nombre, u.segundo_nombre, u.primer_apellido, u.segundo_apellido, "
            "u.username, r.nombre FROM usuario u LEFT JOIN rol r ON u.id_rol = r.id_rol "
            "WHERE u.id_usuario = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            sidebar_user['name'] = session.get('full_name') or ' '.join(part for part in row[:4] if part) or row[4] or sidebar_user['name']
            sidebar_user['role'] = row[5] or sidebar_user['role']
    except Exception:
        pass
    return sidebar_user


def get_usuarios_reporte(from_date=None, to_date=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    usuarios = []
    try:
        cursor.execute(
            "SELECT u.id_usuario, u.username, u.primer_nombre, u.segundo_nombre, u.primer_apellido, u.segundo_apellido, u.password_hash, u.correo, u.telefono, u.id_rol, u.id_estado, u.fecha_creacion, r.nombre AS rol_nombre "
            "FROM usuario u LEFT JOIN rol r ON u.id_rol = r.id_rol ORDER BY u.id_usuario"
        )
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description]

        for row in rows:
            data = dict(zip(cols, row))
            name_parts = [
                data.get('primer_nombre') or '',
                data.get('segundo_nombre') or '',
                data.get('primer_apellido') or '',
                data.get('segundo_apellido') or ''
            ]
            full_name = ' '.join([p for p in name_parts if p]).strip() or data.get('username', '')
            created_at = data.get('fecha_creacion')
            estado_text = 'Activo' if data.get('id_estado') == 1 else 'Inactivo'
            usuario = {
                'id_usuario': data.get('id_usuario'),
                'username': data.get('username'),
                'full_name': full_name,
                'rol': data.get('rol_nombre') or 'Usuario',
                'email': data.get('correo') or '-',
                'telefono': data.get('telefono') or '-',
                'estado': estado_text,
                'created_at': created_at
            }

            if from_date:
                try:
                    created_str = str(created_at).split(' ')[0] if created_at else ''
                    if created_str and created_str < from_date:
                        continue
                except Exception:
                    pass
            if to_date:
                try:
                    created_str = str(created_at).split(' ')[0] if created_at else ''
                    if created_str and created_str > to_date:
                        continue
                except Exception:
                    pass

            usuarios.append(usuario)
    finally:
        cursor.close()
        conn.close()
    return usuarios


def draw_logo_in_pdf(pdf, width, height):
    logo_path = os.path.join(base_dir, 'app', 'static', 'logo.png')
    if os.path.exists(logo_path):
        try:
            from PIL import Image
            with Image.open(logo_path) as img:
                original_width, original_height = img.size
        except Exception:
            original_width, original_height = 300, 120

        max_width = 115
        max_height = 42
        scale = min(max_width / original_width, max_height / original_height) if original_width and original_height else 1
        logo_width = max(40, original_width * scale)
        logo_height = max(18, original_height * scale)
        x = width - logo_width - 25
        y = height - logo_height - 22
        pdf.drawImage(logo_path, x, y, width=logo_width, height=logo_height, preserveAspectRatio=True, mask='auto')


def draw_table_pdf(pdf, title, headers, rows, y_start=680):
    page_width, page_height = letter
    margin_left = 40
    table_width = page_width - (margin_left * 2)

    pdf.setFont('Helvetica-Bold', 18)
    pdf.drawString(margin_left, page_height - 55, title)
    draw_logo_in_pdf(pdf, page_width, page_height)

    pdf.setFont('Helvetica', 10)
    pdf.drawString(margin_left, page_height - 76, f'Generado: {datetime.now().strftime("%d/%m/%Y, %H:%M:%S")}')

    if not rows:
        pdf.setFont('Helvetica-Oblique', 11)
        pdf.drawString(margin_left, page_height - 120, 'No se encontraron registros para el rango seleccionado.')
        return

    style_sheet = getSampleStyleSheet()
    cell_style = style_sheet['BodyText']
    cell_style.fontName = 'Helvetica'
    cell_style.fontSize = 7
    cell_style.leading = 9
    cell_style.wordWrap = 'CJK'

    table_data = [headers]
    for row in rows:
        table_data.append([Paragraph(str(value), cell_style) for value in row])

    col_count = len(headers)
    col_widths = [table_width / col_count for _ in headers]

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), (0.90, 0.94, 1.00)),
        ('TEXTCOLOR', (0, 0), (-1, 0), (0, 0, 0)),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 7),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.7, (0.45, 0.45, 0.45)),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [(1, 1, 1), (0.97, 0.98, 1.0)]),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))

    table.wrapOn(pdf, table_width, page_height)
    usable_height = page_height - 120
    y_position = min(y_start, usable_height - table._height)
    table.drawOn(pdf, margin_left, y_position)


def get_ventas_reporte():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT id_venta, fecha_venta, id_cliente, id_usuario, total, id_metodo_pago, id_estado_venta '
            'FROM venta ORDER BY fecha_venta DESC'
        )
        sale_rows = cursor.fetchall()
        sale_cols = [col[0] for col in cursor.description]

        cursor.execute(
            'SELECT id_venta, id_repuesto, cantidad FROM detalle_venta'
        )
        detail_rows = cursor.fetchall()
        detail_cols = [col[0] for col in cursor.description]
        details_by_sale = {}
        for detail_row in detail_rows:
            detail = dict(zip(detail_cols, detail_row))
            sale_id = detail.get('id_venta')
            if sale_id is not None:
                detail['repuesto'] = get_repuesto_display_name(
                    cursor, detail.get('id_repuesto')
                ) or str(detail.get('id_repuesto') or '-')
                details_by_sale.setdefault(sale_id, []).append(detail)

        report_rows = []
        for sale_row in sale_rows:
            sale = dict(zip(sale_cols, sale_row))
            sale_id = sale.get('id_venta')
            cliente = get_cliente_full_name(cursor, sale.get('id_cliente')) or 'Sin cliente frecuente'
            vendedor = get_user_full_name(cursor, sale.get('id_usuario')) or get_user_full_name_by_id(sale.get('id_usuario')) or '-'
            metodo_pago = lookup_foreign_key_display(
                cursor, 'id_metodo_pago', sale.get('id_metodo_pago'), ['metodo_pago', 'forma_pago']
            ) or '-'
            details = details_by_sale.get(sale_id, [])
            units = sum(int(detail.get('cantidad') or 0) for detail in details)
            detail_text = ', '.join(
                f"{detail['repuesto']} (x{int(detail.get('cantidad') or 0)})"
                for detail in details
            ) or 'Sin detalle registrado'
            report_rows.append({
                'id_venta': sale_id,
                'numero': f'V-{int(sale_id):06d}' if sale_id is not None else '-',
                'fecha': str(sale.get('fecha_venta') or ''),
                'cliente': cliente,
                'vendedor': vendedor,
                'metodo_pago': metodo_pago,
                'repuestos': len(details),
                'unidades': units,
                'detalle_repuestos': detail_text,
                'total': float(sale.get('total') or 0),
                'id_estado_venta': int(sale.get('id_estado_venta') or 1),
                'estado': 'Devuelta' if int(sale.get('id_estado_venta') or 1) == 2 else 'Completada',
            })
        return report_rows
    finally:
        cursor.close()
        conn.close()


def filter_ventas_reporte(rows, from_date=None, to_date=None, cliente=None, vendedor=None, repuesto=None, metodo_pago=None, estado=None, min_total=None, max_total=None):
    filtered = []
    for row in rows:
        sale_date = str(row.get('fecha') or '').split(' ')[0]
        if from_date and sale_date and sale_date < from_date:
            continue
        if to_date and sale_date and sale_date > to_date:
            continue
        if cliente and cliente.casefold() not in str(row.get('cliente') or '').casefold():
            continue
        if vendedor and vendedor.casefold() not in str(row.get('vendedor') or '').casefold():
            continue
        if repuesto and repuesto.casefold() not in str(row.get('detalle_repuestos') or '').casefold():
            continue
        if metodo_pago and metodo_pago.casefold() != str(row.get('metodo_pago') or '').casefold():
            continue
        if estado and estado != str(row.get('id_estado_venta') or 1):
            continue
        if min_total is not None:
            total = float(row.get('total') or 0)
            if total < min_total:
                continue
        if max_total is not None:
            total = float(row.get('total') or 0)
            if total > max_total:
                continue
        filtered.append(row)
    return filtered


def get_repuestos_reporte(from_date=None, to_date=None, tipo_repuesto=None, marca=None, estado_repuesto=None, ubicacion=None, stock_bajo_only=False, search=None, stock_min=None, stock_max=None, precio_compra_min=None, precio_venta_max=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    repuestos = []
    try:
        cursor.execute(
            "SELECT id_repuesto, codigo_repuesto, id_vehiculo_origen, id_tipo_repuesto, id_marca, id_modelo, id_anio, id_color, id_lado, id_posicion, id_estado_repuesto, id_ubicacion, precio_compra, precio_venta, fecha_ingreso, stock FROM repuesto ORDER BY id_repuesto"
        )
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description]

        for row in rows:
            data = dict(zip(cols, row))
            fecha_ingreso = data.get('fecha_ingreso')
            repuesto = {
                'id_repuesto': data.get('id_repuesto'),
                'codigo_repuesto': data.get('codigo_repuesto') or '-',
                'tipo_repuesto': get_display_name_by_id(cursor, ['tipo_repuesto'], data.get('id_tipo_repuesto')) or '-',
                'marca': get_display_name_by_id(cursor, ['marca_vehiculo', 'marca'], data.get('id_marca')) or '-',
                'modelo': get_display_name_by_id(cursor, ['modelo_vehiculo', 'modelo'], data.get('id_modelo')) or '-',
                'anio': get_display_name_by_id(cursor, ['anio_vehiculo'], data.get('id_anio')) or (data.get('id_anio') or '-'),
                'stock': data.get('stock') if data.get('stock') is not None else 0,
                'precio_compra': data.get('precio_compra') or 0,
                'precio_venta': data.get('precio_venta') or 0,
                'ubicacion_bodega': get_display_name_by_id(cursor, ['bodega', 'ubicacion', 'ubicacion_bodega'], data.get('id_ubicacion')) or '-',
                'estado_repuesto': get_display_name_by_id(cursor, ['estado_repuesto'], data.get('id_estado_repuesto')) or '-',
                'fecha_ingreso': fecha_ingreso,
            }

            if from_date:
                try:
                    fecha = str(fecha_ingreso).split(' ')[0] if fecha_ingreso else ''
                    if fecha and fecha < from_date:
                        continue
                except Exception:
                    pass
            if to_date:
                try:
                    fecha = str(fecha_ingreso).split(' ')[0] if fecha_ingreso else ''
                    if fecha and fecha > to_date:
                        continue
                except Exception:
                    pass
            if tipo_repuesto:
                if str(repuesto.get('tipo_repuesto') or '').lower() != str(tipo_repuesto).strip().lower():
                    continue
            if marca:
                if str(repuesto.get('marca') or '').lower() != str(marca).strip().lower():
                    continue
            if estado_repuesto:
                if str(repuesto.get('estado_repuesto') or '').lower() != str(estado_repuesto).strip().lower():
                    continue
            if ubicacion:
                if str(repuesto.get('ubicacion_bodega') or '').lower() != str(ubicacion).strip().lower():
                    continue
            if search:
                search_lower = search.lower()
                searchable = ' '.join([
                    str(repuesto.get('id_repuesto') or ''),
                    str(repuesto.get('codigo_repuesto') or ''),
                    str(repuesto.get('tipo_repuesto') or ''),
                    str(repuesto.get('marca') or ''),
                    str(repuesto.get('modelo') or ''),
                    str(repuesto.get('anio') or ''),
                    str(repuesto.get('color') or ''),
                    str(repuesto.get('lado') or ''),
                    str(repuesto.get('posicion') or ''),
                    str(repuesto.get('estado_repuesto') or ''),
                    str(repuesto.get('ubicacion_bodega') or ''),
                ]).lower()
                if search_lower not in searchable:
                    continue
            if stock_min:
                try:
                    if (repuesto.get('stock') or 0) < int(stock_min):
                        continue
                except Exception:
                    pass
            if stock_max:
                try:
                    if (repuesto.get('stock') or 0) > int(stock_max):
                        continue
                except Exception:
                    pass
            if precio_compra_min:
                try:
                    if (repuesto.get('precio_compra') or 0) < float(precio_compra_min):
                        continue
                except Exception:
                    pass
            if precio_venta_max:
                try:
                    if (repuesto.get('precio_venta') or 0) > float(precio_venta_max):
                        continue
                except Exception:
                    pass
            if stock_bajo_only:
                if int(repuesto.get('stock') or 0) > 5:
                    continue

            repuestos.append(repuesto)
    finally:
        cursor.close()
        conn.close()

    return repuestos


@app.route('/inventario/exportar-pdf')
def exportar_pdf_inventario():
    if 'user' not in session:
        return redirect(url_for('login'))
    if session.get('role') == 'Vendedor':
        flash('No tienes permisos para exportar reportes de inventario.', 'error')
        return redirect(url_for('inventario'))

    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()
    tipo_repuesto = request.args.get('tipo_repuesto', '').strip()
    marca = request.args.get('marca', '').strip()
    estado_repuesto = request.args.get('estado_repuesto', '').strip()
    ubicacion = request.args.get('ubicacion', '').strip()
    search = request.args.get('search', '').strip()
    stock_min = request.args.get('stock_min', '').strip()
    stock_max = request.args.get('stock_max', '').strip()
    precio_compra_min = request.args.get('precio_compra_min', '').strip()
    precio_venta_max = request.args.get('precio_venta_max', '').strip()
    stock_bajo_only = request.args.get('stock_bajo', '').strip().lower() in ('1', 'true', 'yes', 'on')
    repuestos = get_repuestos_reporte(
        from_date=from_date or None,
        to_date=to_date or None,
        tipo_repuesto=tipo_repuesto or None,
        marca=marca or None,
        estado_repuesto=estado_repuesto or None,
        ubicacion=ubicacion or None,
        search=search or None,
        stock_min=stock_min or None,
        stock_max=stock_max or None,
        precio_compra_min=precio_compra_min or None,
        precio_venta_max=precio_venta_max or None,
        stock_bajo_only=stock_bajo_only,
    )

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    rows = [
        [
            str(item.get('codigo_repuesto') or '-'),
            str(item.get('tipo_repuesto') or '-'),
            str(item.get('marca') or '-'),
            str(item.get('modelo') or '-'),
            str(item.get('anio') or '-'),
            str(item.get('stock') or 0),
            f"Q {float(item.get('precio_compra') or 0):,.2f}",
            f"Q {float(item.get('precio_venta') or 0):,.2f}",
            str(item.get('ubicacion_bodega') or '-'),
        ]
        for item in repuestos
    ]

    pdf.setTitle('Reporte de Inventario - Importadora Morales')
    headers = ['Código', 'Tipo', 'Marca', 'Modelo', 'Año', 'Stock', 'P. Compra', 'P. Venta', 'Ubicación']
    if from_date or to_date:
        pdf.setFont('Helvetica', 10)
        pdf.drawString(60, height - 90, f'Filtro: {from_date or "Inicio"} - {to_date or "Hoy"}')
    draw_table_pdf(pdf, 'Reporte de Inventario — Importadora Morales', headers, rows)

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name='reporte_inventario.pdf', mimetype='application/pdf')


@app.route('/inventario/report-data')
def inventario_report_data():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    if session.get('role') == 'Vendedor':
        return jsonify({'error': 'Forbidden'}), 403

    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()
    tipo_repuesto = request.args.get('tipo_repuesto', '').strip()
    marca = request.args.get('marca', '').strip()
    estado_repuesto = request.args.get('estado_repuesto', '').strip()
    ubicacion = request.args.get('ubicacion', '').strip()
    search = request.args.get('search', '').strip()
    stock_min = request.args.get('stock_min', '').strip()
    stock_max = request.args.get('stock_max', '').strip()
    precio_compra_min = request.args.get('precio_compra_min', '').strip()
    precio_venta_max = request.args.get('precio_venta_max', '').strip()

    repuestos = get_repuestos_reporte(
        from_date=from_date or None,
        to_date=to_date or None,
        tipo_repuesto=tipo_repuesto or None,
        marca=marca or None,
        estado_repuesto=estado_repuesto or None,
        ubicacion=ubicacion or None,
        search=search or None,
        stock_min=stock_min or None,
        stock_max=stock_max or None,
        precio_compra_min=precio_compra_min or None,
        precio_venta_max=precio_venta_max or None,
        stock_bajo_only=False,
    )

    filters = {
        'from_date': from_date,
        'to_date': to_date,
        'tipo_repuesto': tipo_repuesto,
        'marca': marca,
        'estado_repuesto': estado_repuesto,
        'ubicacion': ubicacion,
    }

    return jsonify({
        'repuestos': repuestos,
        'filters': filters
    })


def get_movimientos_reporte(from_date=None, to_date=None, tipo=None, usuario=None, search=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        movimientos = get_movimientos_inventario(cursor)
    finally:
        cursor.close()
        conn.close()

    filtered = []
    for movimiento in movimientos:
        fecha = str(movimiento.get('fecha_movimiento') or '').split(' ')[0]
        tipo_nombre = str(movimiento.get('tipo_movimiento') or '').strip().lower()
        usuario_nombre = str(movimiento.get('usuario') or '').strip().lower()
        search_text = ' '.join([
            str(movimiento.get('id_movimiento') or ''),
            str(movimiento.get('id_repuesto') or ''),
            str(movimiento.get('repuesto_nombre') or ''),
            str(movimiento.get('tipo_movimiento') or ''),
            str(movimiento.get('detalle') or ''),
            str(movimiento.get('usuario') or ''),
            str(movimiento.get('codigo_repuesto') or ''),
        ]).lower()

        if from_date and fecha and fecha < from_date:
            continue
        if to_date and fecha and fecha > to_date:
            continue
        if tipo and tipo_nombre != str(tipo).strip().lower():
            continue
        if usuario and usuario_nombre != str(usuario).strip().lower():
            continue
        if search and search.lower() not in search_text:
            continue

        filtered.append(movimiento)

    return filtered


@app.route('/inventario/movimientos/exportar-pdf')
def exportar_pdf_movimientos():
    if 'user' not in session:
        return redirect(url_for('login'))
    if session.get('role') == 'Vendedor':
        flash('No tienes permisos para exportar reportes de movimientos.', 'error')
        return redirect(url_for('inventario'))

    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()
    tipo = request.args.get('tipo', '').strip()
    usuario = request.args.get('usuario', '').strip()
    search = request.args.get('search', '').strip()
    movimientos = get_movimientos_reporte(
        from_date=from_date or None,
        to_date=to_date or None,
        tipo=tipo or None,
        usuario=usuario or None,
        search=search or None,
    )

    buffer = io.BytesIO()
    page_width, page_height = landscape(letter)
    margin_left = 28
    margin_right = 28
    usable_width = page_width - margin_left - margin_right
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=margin_left,
        rightMargin=margin_right,
        topMargin=78,
        bottomMargin=38,
        title='Reporte de Movimientos - Importadora Morales',
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'MovementReportTitle',
        parent=styles['Title'],
        fontName='Helvetica-Bold',
        fontSize=17,
        leading=21,
        alignment=0,
        textColor='#172554',
        spaceAfter=3,
    )
    subtitle_style = ParagraphStyle(
        'MovementReportSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11,
        textColor='#64748b',
        spaceAfter=4,
    )
    cell_style = ParagraphStyle(
        'MovementReportCell',
        parent=styles['BodyText'],
        fontName='Helvetica',
        fontSize=7.5,
        leading=9,
        textColor='#1e293b',
        wordWrap='CJK',
    )
    header_style = ParagraphStyle(
        'MovementReportHeader',
        parent=cell_style,
        fontName='Helvetica-Bold',
        fontSize=7.5,
        leading=9,
        textColor='#ffffff',
    )
    centered_style = ParagraphStyle(
        'MovementReportCenteredCell',
        parent=cell_style,
        alignment=1,
    )

    filters = []
    if from_date:
        filters.append(f'Desde: {escape(from_date)}')
    if to_date:
        filters.append(f'Hasta: {escape(to_date)}')
    if tipo:
        filters.append(f'Tipo: {escape(tipo)}')
    if usuario:
        filters.append(f'Usuario: {escape(usuario)}')
    if search:
        filters.append(f'Búsqueda: {escape(search)}')
    filter_summary = ' · '.join(filters) if filters else 'Sin filtros aplicados'

    story = [
        Paragraph('Reporte de movimientos de inventario', title_style),
        Paragraph('Importadora Morales', subtitle_style),
        Paragraph(
            f'<b>Generado:</b> {datetime.now().strftime("%d/%m/%Y %H:%M")} '
            f'&nbsp;&nbsp; <b>Filtros:</b> {filter_summary}',
            subtitle_style,
        ),
        Spacer(1, 8),
    ]

    headers = [
        'ID movimiento', 'Repuesto', 'Tipo', 'Fecha y hora',
        'Cantidad', 'Detalle', 'Usuario', 'Estado',
    ]
    table_data = [[Paragraph(escape(header), header_style) for header in headers]]
    for item in movimientos:
        values = [
            item.get('id_movimiento') or '-',
            item.get('repuesto_nombre') or item.get('id_repuesto') or '-',
            item.get('tipo_movimiento') or '-',
            item.get('fecha_movimiento') or '-',
            item.get('cantidad') or 0,
            item.get('detalle') or '-',
            item.get('usuario') or '-',
            item.get('estado') or '-',
        ]
        table_data.append([
            Paragraph(escape(str(value)), centered_style if index in (0, 4) else cell_style)
            for index, value in enumerate(values)
        ])

    column_widths = [
        0.08 * usable_width,
        0.21 * usable_width,
        0.10 * usable_width,
        0.13 * usable_width,
        0.07 * usable_width,
        0.22 * usable_width,
        0.11 * usable_width,
        0.08 * usable_width,
    ]
    table = Table(
        table_data,
        colWidths=column_widths,
        repeatRows=1,
        splitByRow=1,
        hAlign='LEFT',
    )
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), '#1e3a8a'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (4, 0), (4, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.35, '#cbd5e1'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), ['#ffffff', '#f1f5f9']),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    if movimientos:
        story.append(table)
    else:
        story.append(Paragraph('No se encontraron movimientos para los filtros seleccionados.', cell_style))

    def draw_page_chrome(page_canvas, document):
        page_canvas.saveState()
        page_canvas.setStrokeColorRGB(0.82, 0.86, 0.91)
        page_canvas.line(margin_left, page_height - 37, page_width - margin_right, page_height - 37)
        page_canvas.setFont('Helvetica-Bold', 8)
        page_canvas.setFillColorRGB(0.20, 0.29, 0.43)
        page_canvas.drawString(margin_left, page_height - 29, 'IMPORTADORA MORALES  |  INVENTARIO')
        page_canvas.setStrokeColorRGB(0.82, 0.86, 0.91)
        page_canvas.line(margin_left, 27, page_width - margin_right, 27)
        page_canvas.setFont('Helvetica', 7)
        page_canvas.setFillColorRGB(0.39, 0.45, 0.54)
        page_canvas.drawString(margin_left, 16, 'Reporte de movimientos de inventario')
        page_canvas.drawRightString(page_width - margin_right, 16, f'Página {document.page}')
        page_canvas.restoreState()

    doc.build(story, onFirstPage=draw_page_chrome, onLaterPages=draw_page_chrome)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name='reporte_movimientos.pdf', mimetype='application/pdf')


@app.route('/inventario/movimientos/report-data')
def inventario_movimientos_report_data():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    if session.get('role') == 'Vendedor':
        return jsonify({'error': 'Forbidden'}), 403

    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()
    tipo = request.args.get('tipo', '').strip()
    usuario = request.args.get('usuario', '').strip()
    search = request.args.get('search', '').strip()
    page = int(request.args.get('movimientos_page', 1))
    per_page = 17

    movimientos = get_movimientos_reporte(
        from_date=from_date or None,
        to_date=to_date or None,
        tipo=tipo or None,
        usuario=usuario or None,
        search=search or None,
    )

    # Apply pagination
    total = len(movimientos)
    start = (page - 1) * per_page
    end = start + per_page
    movimientos_paginated = movimientos[start:end]

    filters = {
        'from_date': from_date,
        'to_date': to_date,
        'tipo': tipo,
        'usuario': usuario,
        'search': search,
        'movimientos_page': page,
    }

    return jsonify({
        'movimientos': movimientos_paginated,
        'filters': filters,
        'page': page,
        'total': total,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
    })


@app.route('/usuario/exportar-pdf')
def exportar_pdf_usuarios():
    if 'user' not in session:
        return redirect(url_for('login'))

    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()
    usuarios = get_usuarios_reporte(from_date=from_date or None, to_date=to_date or None)

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    rows = [
        [
            usuario.get('username') or '-',
            usuario.get('full_name') or '-',
            usuario.get('rol') or '-',
            usuario.get('email') or '-',
            usuario.get('telefono') or '-',
            usuario.get('estado') or '-',
            str(usuario.get('created_at') or '-').split(' ')[0] if usuario.get('created_at') else '-',
        ]
        for usuario in usuarios
    ]

    pdf.setTitle('Reporte de Usuarios - Importadora Morales')
    headers = ['Usuario', 'Nombre Completo', 'Rol', 'Email', 'Teléfono', 'Estado', 'Creado']
    if from_date or to_date:
        pdf.setFont('Helvetica', 10)
        pdf.drawString(60, height - 90, f'Filtro: {from_date or "Inicio"} - {to_date or "Hoy"}')
    draw_table_pdf(pdf, 'Reporte de Usuarios — Importadora Morales', headers, rows)

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name='reporte_usuarios.pdf', mimetype='application/pdf')


@app.route('/usuario/nuevo', methods=['GET', 'POST'])
def nuevo_usuario():
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id_rol AS id, nombre, descripcion FROM rol ORDER BY nombre")
        roles_db = cursor.fetchall()
        roles = []
        for role_id, role_name, description in roles_db:
            permissions = {key: 'none' for key, _ in MODULOS_SISTEMA}
            stored_rows = get_role_permission_rows(cursor, role_id)
            if stored_rows:
                for module_key, module_name, access in stored_rows:
                    permissions[module_key] = normalize_permission_value(access)
            else:
                permissions = build_default_permissions_for_role(role_name)
            roles.append({
                'id': role_id,
                'nombre': role_name,
                'descripcion': description or '',
                'permisos': permissions,
            })
    except Exception as e:
        roles = []
        flash(f'Error al cargar roles: {str(e)}', 'error')
    finally:
        cursor.close()
        conn.close()

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        primer_nombre = request.form.get('primer_nombre', '').strip()
        segundo_nombre = request.form.get('segundo_nombre', '').strip()
        primer_apellido = request.form.get('primer_apellido', '').strip()
        segundo_apellido = request.form.get('segundo_apellido', '').strip()
        email = request.form.get('email', '').strip()
        telefono = request.form.get('telefono', '').strip()
        rol_id = request.form.get('rol_id', '')
        activo = request.form.get('activo') == 'on'

        if not username or not password or not primer_nombre or not primer_apellido or not email or not rol_id:
            flash('Completa los campos obligatorios.', 'error')
            return render_template('registrar_usuario.html', roles=roles, user_name=session.get('user', 'Usuario'))

        if not validar_nombre_persona(primer_nombre) or not validar_nombre_persona(primer_apellido):
            flash('Los nombres y apellidos solo pueden contener letras y espacios.', 'error')
            return render_template('registrar_usuario.html', roles=roles, user_name=session.get('user', 'Usuario'))

        if segundo_nombre and not validar_nombre_persona(segundo_nombre):
            flash('El segundo nombre solo puede contener letras y espacios.', 'error')
            return render_template('registrar_usuario.html', roles=roles, user_name=session.get('user', 'Usuario'))

        if segundo_apellido and not validar_nombre_persona(segundo_apellido):
            flash('El segundo apellido solo puede contener letras y espacios.', 'error')
            return render_template('registrar_usuario.html', roles=roles, user_name=session.get('user', 'Usuario'))

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO usuario (username, password_hash, primer_nombre, segundo_nombre, primer_apellido, segundo_apellido, correo, telefono, id_rol, id_estado, fecha_creacion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE())",
                (
                    username,
                    generate_password_hash(password),
                    primer_nombre,
                    segundo_nombre or None,
                    primer_apellido,
                    segundo_apellido or None,
                    email,
                    telefono or None,
                    int(rol_id),
                    get_estado_usuario_id(cursor, activo),
                )
            )
            conn.commit()
            cursor.close()
            conn.close()
            flash('Usuario registrado correctamente.', 'success')
            return redirect(url_for('usuarios'))
        except Exception as e:
            flash(f'No se pudo guardar el usuario: {str(e)}', 'error')
            return render_template('registrar_usuario.html', roles=roles, user_name=session.get('user', 'Usuario'))

    user_name = session.get('user', 'Usuario')
    if 'user_id' in session:
        nombre_db = get_user_full_name_by_id(session['user_id'])
        if nombre_db:
            user_name = nombre_db

    return render_template('registrar_usuario.html', roles=roles, user_name=user_name)

@app.route('/usuario/modificar/<int:id_usuario>', methods=['GET', 'POST'])
def modificar_usuario(id_usuario):
    if 'user' not in session:
        return redirect(url_for('login'))

    current_user_id = session.get('user_id')
    current_user_role = (session.get('role') or '').strip()
    is_self = current_user_id and id_usuario == current_user_id
    is_administrator = current_user_role.casefold() in ('administrador', 'administrador del sistema')
    if is_self and not is_administrator:
        flash('No está permitido modificar los permisos o el rol de su propia cuenta de usuario activa.', 'error')
        return redirect(url_for('usuarios'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id_rol AS id, nombre, descripcion FROM rol ORDER BY nombre")
        roles_db = cursor.fetchall()
        roles = []
        for role_id, role_name, description in roles_db:
            permissions = {key: 'none' for key, _ in MODULOS_SISTEMA}
            stored_rows = get_role_permission_rows(cursor, role_id)
            if stored_rows:
                for module_key, module_name, access in stored_rows:
                    permissions[module_key] = normalize_permission_value(access)
            else:
                permissions = build_default_permissions_for_role(role_name)
            roles.append({
                'id': role_id,
                'nombre': role_name,
                'descripcion': description or '',
                'permisos': permissions,
            })

        cursor.execute(
            "SELECT u.id_usuario, u.username, u.password_hash, u.primer_nombre, u.segundo_nombre, u.primer_apellido, u.segundo_apellido, u.correo, u.telefono, u.id_rol, u.id_estado "
            "FROM usuario u WHERE u.id_usuario = ?",
            (id_usuario,)
        )
        row = cursor.fetchone()
        if not row:
            flash('No se encontró el usuario para modificar.', 'error')
            return redirect(url_for('usuarios'))

        usuario = {
            'id_usuario': row[0],
            'username': row[1],
            'password_hash': row[2],
            'primer_nombre': row[3],
            'segundo_nombre': row[4],
            'primer_apellido': row[5],
            'segundo_apellido': row[6],
            'correo': row[7],
            'telefono': row[8],
            'id_rol': row[9],
            'id_estado': row[10],
        }

        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            new_password = request.form.get('password', '').strip()
            password_hash = (
                generate_password_hash(new_password)
                if new_password else usuario.get('password_hash') or ''
            )
            primer_nombre = request.form.get('primer_nombre', '').strip()
            segundo_nombre = request.form.get('segundo_nombre', '').strip()
            primer_apellido = request.form.get('primer_apellido', '').strip()
            segundo_apellido = request.form.get('segundo_apellido', '').strip()
            email = request.form.get('email', '').strip()
            telefono = request.form.get('telefono', '').strip()
            rol_id = request.form.get('rol_id', '')
            activo = request.form.get('activo') == 'on'

            # Los administradores pueden editar sus datos, pero no su rol ni el estado de su cuenta.
            if is_self:
                rol_id = str(usuario['id_rol'])
                activo = usuario['id_estado'] == 1

            if not username or not primer_nombre or not primer_apellido or not email or not telefono or not rol_id:
                flash('Completa los campos obligatorios.', 'error')
                return render_template('modificar_usuario.html', roles=roles, usuario=usuario, user_name=session.get('user', 'Usuario'))

            if not validar_nombre_persona(primer_nombre) or not validar_nombre_persona(primer_apellido):
                flash('Los nombres y apellidos solo pueden contener letras y espacios.', 'error')
                return render_template('modificar_usuario.html', roles=roles, usuario=usuario, user_name=session.get('user', 'Usuario'))

            if segundo_nombre and not validar_nombre_persona(segundo_nombre):
                flash('El segundo nombre solo puede contener letras y espacios.', 'error')
                return render_template('modificar_usuario.html', roles=roles, usuario=usuario, user_name=session.get('user', 'Usuario'))

            if segundo_apellido and not validar_nombre_persona(segundo_apellido):
                flash('El segundo apellido solo puede contener letras y espacios.', 'error')
                return render_template('modificar_usuario.html', roles=roles, usuario=usuario, user_name=session.get('user', 'Usuario'))

            cursor.execute(
                "UPDATE usuario SET username = ?, password_hash = ?, primer_nombre = ?, segundo_nombre = ?, primer_apellido = ?, segundo_apellido = ?, correo = ?, telefono = ?, id_rol = ?, id_estado = ? WHERE id_usuario = ?",
                (
                    username,
                    password_hash,
                    primer_nombre,
                    segundo_nombre or None,
                    primer_apellido,
                    segundo_apellido or None,
                    email,
                    telefono or None,
                    int(rol_id),
                    get_estado_usuario_id(cursor, activo),
                    id_usuario,
                )
            )
            conn.commit()
            flash('Usuario actualizado correctamente.', 'success')
            return redirect(url_for('usuarios'))

        user_name = session.get('user', 'Usuario')
        if 'user_id' in session:
            nombre_db = get_user_full_name_by_id(session['user_id'])
            if nombre_db:
                user_name = nombre_db

        return render_template('modificar_usuario.html', roles=roles, usuario=usuario, user_name=user_name)
    except Exception as e:
        flash(f'Error al cargar el usuario: {str(e)}', 'error')
        return redirect(url_for('usuarios'))
    finally:
        cursor.close()
        conn.close()

@app.route('/usuario/eliminar/<int:id_usuario>', methods=['POST'])
def eliminar_usuario(id_usuario):
    if 'user' not in session:
        return redirect(url_for('login'))

    if not has_permission_in_session('usuarios', 'write'):
        flash('No tienes permisos para eliminar usuarios.', 'error')
        return redirect(url_for('usuarios'))

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM usuario WHERE id_usuario = ?", (id_usuario,))
        deleted_rows = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()

        if deleted_rows > 0:
            flash('Usuario eliminado con éxito.', 'success')
        else:
            flash('No se encontró el usuario para eliminar.', 'error')
    except Exception as e:
        flash(f'No se pudo eliminar el usuario: {str(e)}', 'error')

    return redirect(url_for('usuarios'))

@app.route('/usuario/get/<int:id_usuario>')
def usuario_get(id_usuario):
    if 'user' not in session or not has_permission_in_session('usuarios', 'read'):
        return jsonify({'error': 'No tienes permisos'}), 403

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT u.id_usuario, u.username, u.primer_nombre, u.segundo_nombre, u.primer_apellido, u.segundo_apellido, u.correo, u.telefono, u.id_rol, u.id_estado, r.nombre as rol_nombre "
            "FROM usuario u LEFT JOIN rol r ON r.id_rol = u.id_rol WHERE u.id_usuario = ?",
            (id_usuario,)
        )
        row = cursor.fetchone()
        
        if not row:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Usuario no encontrado'}), 404
        
        columns = [col[0] for col in cursor.description]
        usuario = dict(zip(columns, row))
        
        cursor.close()
        conn.close()
        
        # Format for frontend
        full_name = ' '.join(filter(None, [
            usuario.get('primer_nombre'),
            usuario.get('segundo_nombre'),
            usuario.get('primer_apellido'),
            usuario.get('segundo_apellido')
        ]))
        
        return jsonify({
            'success': True,
            'usuario': {
                'id': usuario['id_usuario'],
                'username': usuario['username'],
                'primer_nombre': usuario['primer_nombre'] or '',
                'segundo_nombre': usuario['segundo_nombre'] or '',
                'primer_apellido': usuario['primer_apellido'] or '',
                'segundo_apellido': usuario['segundo_apellido'] or '',
                'full_name': full_name,
                'email': usuario['correo'] or '',
                'telefono': usuario['telefono'] or '',
                'role_id': usuario['id_rol'],
                'rol': usuario['rol_nombre'] or '',
                'activo': '1' if usuario['id_estado'] == 1 else '2'
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/usuario/roles/save', methods=['POST'])
def guardar_rol_usuario():
    if 'user' not in session:
        return redirect(url_for('login'))
    if not has_permission_in_session('usuarios', 'write'):
        flash('No tienes permisos para gestionar roles.', 'error')
        return redirect(url_for('usuarios'))

    role_id = request.form.get('role_id')
    role_name = request.form.get('role_name', '').strip()
    role_description = request.form.get('role_description', '').strip()
    if not role_name:
        flash('El nombre del rol es obligatorio.', 'error')
        return redirect(url_for('usuarios'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if not ensure_role_permission_tables(cursor):
            raise RuntimeError(
                'Las tablas de permisos no existen y el usuario de base de datos no tiene permiso para crearlas. '
                'Ejecute database/roles_permisos.sql con un usuario administrador de SQL Server.'
            )
        seed_default_modules(cursor)

        if role_id:
            cursor.execute(
                "UPDATE rol SET nombre = ?, descripcion = ? WHERE id_rol = ?",
                (role_name, role_description or None, int(role_id)),
            )
            current_role_id = int(role_id)
            flash('Rol actualizado correctamente.', 'success')
        else:
            cursor.execute(
                "INSERT INTO rol (nombre, descripcion) OUTPUT INSERTED.id_rol VALUES (?, ?)",
                (role_name, role_description or None),
            )
            row = cursor.fetchone()
            current_role_id = row[0] if row else None
            if current_role_id is None:
                raise RuntimeError('No se pudo obtener el ID del rol creado.')
            flash('Rol creado correctamente.', 'success')

        for module_key, _ in MODULOS_SISTEMA:
            access = normalize_permission_value(request.form.get(f'perm_{module_key}', 'none'))
            module_id = get_module_id_by_key(cursor, module_key)
            if module_id is None:
                continue
            cursor.execute(
                "SELECT id_rol_modulo FROM rol_modulo WHERE id_rol = ? AND id_modulo = ?",
                (current_role_id, module_id),
            )
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    "UPDATE rol_modulo SET acceso = ? WHERE id_rol = ? AND id_modulo = ?",
                    (access, current_role_id, module_id),
                )
            else:
                cursor.execute(
                    "INSERT INTO rol_modulo (id_rol, id_modulo, acceso) VALUES (?, ?, ?)",
                    (current_role_id, module_id, access),
                )

        conn.commit()

        if session.get('role_id') == current_role_id:
            session['role_permissions'] = get_role_permissions_for_role_id(current_role_id, session.get('role'))
        else:
            session['role_permissions'] = get_role_permissions_from_db(session.get('role'))
    except Exception as e:
        conn.rollback()
        flash(f'No se pudo guardar el rol: {str(e)}', 'error')
    finally:
        cursor.close(); conn.close()

    return redirect(url_for('usuarios') + '#roles')


@app.route('/usuario')
def usuarios():
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    usuarios = []
    try:
        cursor.execute(
            "SELECT TOP (1000) u.id_usuario, u.username, u.primer_nombre, u.segundo_nombre, u.primer_apellido, u.segundo_apellido, u.password_hash, u.correo, u.telefono, u.id_rol, u.id_estado, u.fecha_creacion, r.nombre AS rol_nombre "
            "FROM usuario u "
            "LEFT JOIN rol r ON u.id_rol = r.id_rol "
            "ORDER BY u.id_usuario DESC"
        )
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description]

        roles, permission_tables_available = get_roles_with_permissions(cursor)

        for row in rows:
            data = dict(zip(cols, row))
            name_parts = [
                data.get('primer_nombre') or '',
                data.get('segundo_nombre') or '',
                data.get('primer_apellido') or '',
                data.get('segundo_apellido') or ''
            ]
            full_name = ' '.join([p for p in name_parts if p]).strip() or data.get('username', '')
            estado_text = 'Activo' if data.get('id_estado') == 1 else 'Inactivo'
            usuarios.append({
                'id_usuario': data.get('id_usuario'),
                'id_rol': data.get('id_rol'),
                'username': data.get('username'),
                'full_name': full_name,
                'rol': data.get('rol_nombre') or 'Usuario',
                'password': data.get('password_hash') or data.get('password'),
                'email': data.get('correo') or '-',
                'telefono': data.get('telefono') or '-',
                'estado': estado_text,
                'created_at': data.get('fecha_creacion'),
                'password_hash': data.get('password_hash')
            })
    except Exception as e:
        flash(f'Error al cargar usuarios: {str(e)}', 'error')
        roles = []
    finally:
        cursor.close()
        conn.close()

    total_usuarios = len(usuarios)
    usuarios_activos = sum(user['estado'].lower() == 'activo' for user in usuarios)
    administradores = sum('admin' in user['rol'].lower() for user in usuarios)
    try:
        current_page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        current_page = 1
    page_size = 10
    total_pages = max(1, (total_usuarios + page_size - 1) // page_size)
    current_page = min(current_page, total_pages)
    start = (current_page - 1) * page_size
    usuarios_paginados = usuarios[start:start + page_size]
    sidebar_user = get_current_sidebar_user()
    return render_template('usuario.html', usuarios=usuarios_paginados, report_usuarios=usuarios,
                           roles=roles, modulos=MODULOS_SISTEMA,
                           permission_tables_available=permission_tables_available,
                           user_name=sidebar_user['name'], user_role=sidebar_user['role'],
                           current_user_id=session.get('user_id'),
                           total_usuarios=total_usuarios,
                           usuarios_activos=usuarios_activos,
                           administradores=administradores,
                           current_page=current_page, total_pages=total_pages,
                           usuarios_paginados=usuarios_paginados)

@app.route('/', methods=['GET', 'POST'])
def login():
    # Si el usuario ya tiene una sesión iniciada, lo mandamos directo al dashboard
    if 'user' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        usuario = request.form.get('username')
        password = request.form.get('password')
        
        cuenta_valida = validar_usuario_db(usuario, password)
        
        if cuenta_valida:
            if not cuenta_valida.get('active', True):
                flash('Su usuario está inactivo. Por favor, consulte con el administrador del sistema.', 'error')
                return redirect(url_for('login'))

            user_id = get_user_id_by_username(cuenta_valida['user'])
            if user_id is None:
                flash('No se pudo validar la cuenta de usuario.', 'error')
                return redirect(url_for('login'))
            audit_conn = None
            audit_cursor = None
            try:
                audit_conn = get_db_connection()
                audit_cursor = audit_conn.cursor()
                audit_cursor.execute(
                    "INSERT INTO [dbo].[bitacora_login] ([id_usuario], [ip_origen], [estado], [detalles]) "
                    "VALUES (?, ?, 'EXITOSO', ?)",
                    (user_id, request.remote_addr, 'Inicio de sesión exitoso'),
                )
                audit_conn.commit()
            except Exception:
                # An audit outage should not prevent a valid user from signing in.
                app.logger.exception('No fue posible registrar el inicio de sesión en bitacora_login.')
            finally:
                if audit_cursor:
                    try:
                        audit_cursor.close()
                    except Exception:
                        pass
                if audit_conn:
                    try:
                        audit_conn.close()
                    except Exception:
                        pass
            session['user_id'] = user_id
            session['user'] = cuenta_valida['user']
            session['full_name'] = get_user_full_name_by_id(user_id) or cuenta_valida['user']
            session['role'] = cuenta_valida['role']
            session['role_id'] = cuenta_valida.get('role_id')
            session['role_permissions'] = sync_session_role_permissions(cuenta_valida['role'], cuenta_valida.get('role_id'))
            session.permanent = True
            return redirect(url_for('dashboard'))
        else:
            flash('Usuario o contraseña incorrectos.', 'error')
            return redirect(url_for('login'))
            
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    # Filtro de seguridad: Si no hay sesión, regresa al login obligatoriamente
    if 'user' not in session:
        return redirect(url_for('login'))
        
    user_name = session.get('full_name') or session.get('user', 'Usuario')
    if 'user_id' in session:
        nombre_db = get_user_full_name_by_id(session['user_id'])
        if nombre_db:
            user_name = nombre_db
            session['full_name'] = nombre_db
    # Renderiza la plantilla única; los bloques {% if %} de Jinja ocultarán/mostrarán los módulos
    return render_template('menu_principal.html', user_name=user_name)


def _analytics_days(raw_value):
    try:
        days = int(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError('Selecciona un período de 7, 30 o 90 días.') from error
    if days not in {7, 30, 90}:
        raise ValueError('Selecciona un período de 7, 30 o 90 días.')
    return days


def _analytics_quote(identifier):
    return f'[{str(identifier).replace("]", "]]")}]'


def _get_gemini_api_key():
    api_key = os.getenv('GEMINI_API_KEY', '').strip()
    if api_key:
        return api_key
    env_values = dotenv_values(os.path.join(base_dir, '.env'))
    return str(env_values.get('GEMINI_API_KEY') or '').strip()


def _analytics_dashboard_data(cursor, days):
    inventory_columns = {
        column.lower(): column for column in get_table_columns(cursor, 'dbo.repuesto')
    }
    sale_columns = {
        column.lower() for column in get_table_columns(cursor, 'dbo.venta')
    }
    sale_detail_columns = {
        column.lower() for column in get_table_columns(cursor, 'dbo.detalle_venta')
    }
    required_inventory = {
        'id_repuesto', 'codigo_repuesto', 'stock', 'stock_minimo',
        'precio_venta', 'precio_compra',
    }
    required_sales = {'id_venta', 'fecha_venta', 'total'}
    required_sale_details = {'id_venta', 'id_repuesto', 'cantidad', 'precio_unitario'}
    if not required_inventory.issubset(inventory_columns):
        raise ValueError('Faltan columnas necesarias en dbo.repuesto para generar Analytics.')
    if not required_sales.issubset(sale_columns) or not required_sale_details.issubset(sale_detail_columns):
        raise ValueError('Faltan columnas necesarias en las tablas de ventas para generar Analytics.')

    type_columns = {
        column.lower(): column for column in get_table_columns(cursor, 'dbo.tipo_repuesto')
    }
    type_id_column = type_columns.get('id_tipo_repuesto') or next(
        (column for key, column in type_columns.items() if key.startswith('id')),
        None,
    )
    type_name_column = choose_display_column(list(type_columns.values()))
    has_type_join = bool(
        type_id_column and type_name_column
        and 'id_tipo_repuesto' in inventory_columns
    )
    type_label = (
        f'COALESCE(CONVERT(NVARCHAR(120), t.{_analytics_quote(type_name_column)}), '
        "N'Sin categoría')"
        if has_type_join else "N'Sin categoría'"
    )
    type_join = (
        f'LEFT JOIN dbo.tipo_repuesto t ON '
        f'r.{_analytics_quote(inventory_columns["id_tipo_repuesto"])} = '
        f't.{_analytics_quote(type_id_column)}'
        if has_type_join else ''
    )
    revenue_expression = 'COALESCE(dv.subtotal, dv.precio_unitario * dv.cantidad)'
    if 'subtotal' not in sale_detail_columns:
        revenue_expression = 'dv.precio_unitario * dv.cantidad'

    cursor.execute(
        'SELECT COUNT_BIG(1), COALESCE(SUM(CONVERT(BIGINT, COALESCE(stock, 0))), 0), '
        'COALESCE(SUM(CONVERT(DECIMAL(19,2), COALESCE(stock, 0)) * '
        'COALESCE(precio_compra, 0)), 0) '
        'FROM dbo.repuesto'
    )
    inventory_row = cursor.fetchone()
    cursor.execute(
        'SELECT COALESCE(SUM(total), 0), COUNT_BIG(1) '
        'FROM dbo.venta WHERE fecha_venta >= DATEADD(day, -(? - 1), CONVERT(date, GETDATE()))',
        (days,),
    )
    sales_row = cursor.fetchone()

    cursor.execute(
        'WITH unidades_por_venta AS ('
        'SELECT id_venta, SUM(cantidad) AS unidades '
        'FROM dbo.detalle_venta GROUP BY id_venta) '
        'SELECT CONVERT(date, v.fecha_venta) AS fecha, '
        'SUM(COALESCE(v.total, 0)) AS ingresos, '
        'SUM(COALESCE(uv.unidades, 0)) AS unidades '
        'FROM dbo.venta v LEFT JOIN unidades_por_venta uv ON uv.id_venta = v.id_venta '
        'WHERE v.fecha_venta >= DATEADD(day, -(? - 1), CONVERT(date, GETDATE())) '
        'GROUP BY CONVERT(date, v.fecha_venta) ORDER BY fecha',
        (days,),
    )
    temporal_rows = cursor.fetchall()
    temporal_by_date = {
        row[0].date() if hasattr(row[0], 'date') else row[0]: {
            'ingresos': float(row[1] or 0),
            'ventas': int(row[2] or 0),
        }
        for row in temporal_rows
    }
    today = datetime.now().date()
    temporal = []
    for offset in range(days - 1, -1, -1):
        date_value = today - timedelta(days=offset)
        values = temporal_by_date.get(date_value, {'ingresos': 0, 'ventas': 0})
        temporal.append({
            'fecha': date_value.isoformat(),
            'ingresos': values['ingresos'],
            'ventas': values['ventas'],
        })

    cursor.execute(
        f'SELECT {type_label} AS categoria, '
        'COALESCE(SUM(dv.cantidad), 0) AS unidades, '
        f'COALESCE(SUM({revenue_expression}), 0) AS ingresos, '
        'COALESCE(SUM((dv.precio_unitario - COALESCE(r.precio_compra, 0)) '
        '* dv.cantidad), 0) AS margen '
        'FROM dbo.detalle_venta dv '
        'INNER JOIN dbo.venta v ON v.id_venta = dv.id_venta '
        'LEFT JOIN dbo.repuesto r ON r.id_repuesto = dv.id_repuesto '
        f'{type_join} '
        'WHERE v.fecha_venta >= DATEADD(day, -(? - 1), CONVERT(date, GETDATE())) '
        f'GROUP BY {type_label} ORDER BY ingresos DESC',
        (days,),
    )
    category_rows = cursor.fetchall()
    categories = []
    for row in category_rows:
        revenue = float(row[2] or 0)
        margin = float(row[3] or 0)
        categories.append({
            'nombre': str(row[0] or 'Sin categoría'),
            'unidades': int(row[1] or 0),
            'ingresos': revenue,
            'margen': margin,
            'margen_porcentaje': (margin / revenue * 100) if revenue > 0 else 0,
        })

    status_columns = {
        column.lower(): column for column in get_table_columns(cursor, 'dbo.estado_repuesto')
    }
    status_id_column = status_columns.get('id_estado_repuesto') or next(
        (column for key, column in status_columns.items() if key.startswith('id')),
        None,
    )
    status_name_column = choose_display_column(list(status_columns.values()))
    if (
        'id_estado_repuesto' in inventory_columns
        and status_id_column
        and status_name_column
    ):
        cursor.execute(
            'SELECT COALESCE(CONVERT(NVARCHAR(100), e.'
            f'{_analytics_quote(status_name_column)}), N\'Sin estado\'), '
            'COUNT_BIG(1), COALESCE(SUM(CONVERT(BIGINT, COALESCE(r.'
            f'{_analytics_quote(inventory_columns["stock"])}, 0))), 0) '
            'FROM dbo.repuesto r LEFT JOIN dbo.estado_repuesto e ON '
            f'r.{_analytics_quote(inventory_columns["id_estado_repuesto"])} = '
            f'e.{_analytics_quote(status_id_column)} '
            f'GROUP BY e.{_analytics_quote(status_name_column)} '
            f'ORDER BY e.{_analytics_quote(status_name_column)}'
        )
        status_rows = cursor.fetchall()
    else:
        cursor.execute(
            'SELECT CASE WHEN COALESCE(stock, 0) <= 0 THEN N\'Agotado\' '
            'WHEN COALESCE(stock, 0) <= COALESCE(stock_minimo, 0) THEN N\'Bajo mínimo\' '
            'ELSE N\'Disponible\' END, COUNT_BIG(1), '
            'COALESCE(SUM(CONVERT(BIGINT, COALESCE(stock, 0))), 0) '
            'FROM dbo.repuesto GROUP BY CASE WHEN COALESCE(stock, 0) <= 0 '
            'THEN N\'Agotado\' WHEN COALESCE(stock, 0) <= COALESCE(stock_minimo, 0) '
            'THEN N\'Bajo mínimo\' ELSE N\'Disponible\' END'
        )
        status_rows = cursor.fetchall()
    inventory_distribution = [
        {'nombre': str(row[0] or 'Sin estado'), 'repuestos': int(row[1] or 0), 'unidades': int(row[2] or 0)}
        for row in status_rows
    ]

    stock_column = _analytics_quote(inventory_columns['stock'])
    stock_threshold_column = _analytics_quote(inventory_columns['stock_minimo'])
    inventory_id_column = _analytics_quote(inventory_columns['id_repuesto'])
    inventory_code_column = _analytics_quote(inventory_columns['codigo_repuesto'])
    cursor.execute(
        f'SELECT r.{inventory_id_column}, r.{inventory_code_column}, '
        f'COALESCE(r.{stock_column}, 0), COALESCE(r.{stock_threshold_column}, 0), '
        f'{type_label}, COALESCE(ventas.vendidas, 0) AS vendidas, '
        'COALESCE(ventas.vendidas_90_dias, 0) AS vendidas_90_dias '
        'FROM dbo.repuesto r '
        f'{type_join} '
        'LEFT JOIN ('
        'SELECT dv.id_repuesto, '
        'SUM(CASE WHEN v.fecha_venta >= DATEADD(day, -(? - 1), CONVERT(date, GETDATE())) '
        'THEN dv.cantidad ELSE 0 END) AS vendidas, '
        'SUM(dv.cantidad) AS vendidas_90_dias '
        'FROM dbo.detalle_venta dv '
        'INNER JOIN dbo.venta v ON v.id_venta = dv.id_venta '
        'WHERE v.fecha_venta >= DATEADD(day, -(? - 1), CONVERT(date, GETDATE())) '
        'OR v.fecha_venta >= DATEADD(day, -89, CONVERT(date, GETDATE())) '
        'GROUP BY dv.id_repuesto'
        ') ventas ON ventas.id_repuesto = r.'
        f'{inventory_id_column} ORDER BY COALESCE(r.{stock_column}, 0) ASC',
        (days, days),
    )
    prediction_rows = cursor.fetchall()
    predictions = []
    low_stock_count = 0
    total_margin = sum(item['margen'] for item in categories)
    total_category_revenue = sum(item['ingresos'] for item in categories)
    for row in prediction_rows:
        stock = float(row[2] or 0)
        minimum = float(row[3] or 0)
        average_daily_sales = float(row[5] or 0) / days
        if average_daily_sales <= 0:
            historical_sales = float(row[6] or 0) if len(row) > 6 else 0
            average_daily_sales = historical_sales / 90
        days_remaining = stock / average_daily_sales if average_daily_sales > 0 else None
        needs_restock = stock <= minimum or (days_remaining is not None and days_remaining <= 14)
        if needs_restock:
            low_stock_count += 1
        if stock <= 0:
            urgency = 'Agotado'
        elif stock <= minimum or (days_remaining is not None and days_remaining <= 7):
            urgency = 'Reabastecer urgente'
        elif days_remaining is not None and days_remaining <= 14:
            urgency = 'Vigilar inventario'
        else:
            urgency = 'Stock en niveles óptimos'
        if needs_restock or len(predictions) < 6:
            predictions.append({
                'codigo': str(row[1] or f'REP-{int(row[0]):06d}'),
                'nombre': f"Repuesto {row[1] or int(row[0])}",
                'categoria': str(row[4] or 'Sin categoría'),
                'stock': stock,
                'minimo': minimum,
                'dias_agotamiento': round(days_remaining, 1) if days_remaining is not None else None,
                'tendencia_diaria': round(average_daily_sales, 2),
                'urgencia': urgency,
                'requiere_reabastecimiento': needs_restock,
            })
            predictions = predictions[:6]

    return {
        'periodo_dias': days,
        'metricas': {
            'repuestos': int(inventory_row[0] or 0),
            'unidades_inventario': int(inventory_row[1] or 0),
            'valor_inventario': float(inventory_row[2] or 0),
            'ingresos': float(sales_row[0] or 0),
            'ventas': int(sales_row[1] or 0),
            'margen_porcentaje': (
                total_margin / total_category_revenue * 100
                if total_category_revenue > 0 else 0
            ),
        },
        'temporal': temporal,
        'categorias': categories,
        'distribucion': inventory_distribution,
        'predicciones': predictions,
        'repuestos_urgentes': low_stock_count,
    }


@app.route('/analytics')
def analytics():
    if not has_permission_in_session('analytics', 'read'):
        flash('No tienes permisos para acceder a Analítica IA.', 'error')
        return redirect(url_for('dashboard'))
    try:
        days = _analytics_days(request.args.get('dias', '30'))
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            analytics_data = _analytics_dashboard_data(cursor, days)
        finally:
            cursor.close()
            conn.close()
        data_error = None
    except ValueError as error:
        days = 30
        analytics_data = None
        data_error = str(error)
    except Exception:
        app.logger.exception('No se pudieron cargar las métricas de Analítica IA.')
        days = 30
        analytics_data = None
        data_error = 'No fue posible consultar los datos de Analytics en SQL Server.'
    sidebar_user = get_current_sidebar_user()
    return render_template(
        'analytics.html',
        user_name=sidebar_user['name'],
        user_role=sidebar_user['role'],
        analytics_data=analytics_data,
        data_error=data_error,
        selected_days=days,
    )


@app.route('/api/analytics/dashboard')
def analytics_dashboard_api():
    try:
        days = _analytics_days(request.args.get('dias', '30'))
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            dashboard_data = _analytics_dashboard_data(cursor, days)
        finally:
            cursor.close()
            conn.close()
        return jsonify({'success': True, 'data': dashboard_data})
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 400
    except Exception:
        app.logger.exception('No se pudieron consultar las métricas de Analítica IA.')
        return jsonify({
            'success': False,
            'error': 'No fue posible consultar los datos de Analytics en SQL Server.',
        }), 503


@app.route('/api/analytics/stock', methods=['POST'])
def analizar_inventario_analytics():
    try:
        request_data = request.get_json(silent=True) or {}
        days = _analytics_days(request_data.get('dias', 30))
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 400

    api_key = _get_gemini_api_key()
    if not api_key:
        return jsonify({
            'success': False,
            'error': 'Configura GEMINI_API_KEY en el archivo .env para habilitar las recomendaciones de IA.',
        }), 503

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            analytics_data = _analytics_dashboard_data(cursor, days)
        finally:
            cursor.close()
            conn.close()
    except Exception:
        app.logger.exception('No se pudieron preparar los datos para las recomendaciones de IA.')
        return jsonify({
            'success': False,
            'error': 'No fue posible consultar el inventario y las ventas en SQL Server.',
        }), 503

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        prompt = (
            "Analiza el siguiente inventario de repuestos de mi taller en Guatemala,"
            " identifica cuáles tienen stock bajo (menor o igual al mínimo) y dame"
            " recomendaciones breves de reabastecimiento. IMPORTANTE: Todos los montos"
            " y valores monetarios deben expresarse estrictamente en **Quetzales (Q)**"
            f" y no en dólares: {json.dumps(analytics_data, ensure_ascii=False)}"
        )

        response = client.models.generate_content(
            model='gemini-3.8-flash',
            contents=prompt,
        )

        return jsonify({'success': True, 'analisis': response.text})
    except Exception:
        app.logger.exception('No se pudo generar el análisis de inventario con Gemini.')
        return jsonify({
            'success': False,
            'error': 'El servicio de IA no está disponible temporalmente. Intenta de nuevo más tarde.',
        }), 503


# Catálogos que se administran desde el módulo de mantenimiento.  Se prueban
# nombres alternos porque hay instalaciones antiguas de la base de datos con
# una nomenclatura ligeramente distinta.
MANTENIMIENTO_CATALOGOS = [
    ('anios', 'Años', 'fa-calendar-days', ['anio_vehiculo', 'anio']),
    ('marcas', 'Marcas', 'fa-car', ['marca_vehiculo', 'marca']),
    ('modelos', 'Modelos', 'fa-chevron-right', ['modelo_vehiculo', 'modelo']),
    ('colores', 'Colores', 'fa-palette', ['color', 'color_repuesto']),
    ('tipos', 'Tipos de Repuesto', 'fa-screwdriver-wrench', ['tipo_repuesto']),
    ('origenes', 'Origen Vehículo', 'fa-location-dot', ['vehiculo_origen']),
    ('transmisiones', 'Transmisiones', 'fa-gears', ['transmision', 'transmision_vehiculo']),
    ('ubicaciones', 'Ubicaciones', 'fa-map-pin', ['ubicacion', 'bodega', 'ubicacion_bodega']),
    ('metodos_pago', 'Métodos de Pago', 'fa-credit-card', ['metodo_pago']),
    ('roles', 'Roles', 'fa-book-open', ['rol']),
    ('tipo_desarme', 'Tipo de Desarme', 'fa-hammer', ['tipo_desarme']),
    ('estado_pieza', 'Estado de Pieza', 'fa-box-open', ['estado_pieza']),
    ('motivo_incidencia', 'Motivo de Incidencia', 'fa-circle-exclamation', ['motivo_incidencia']),
]

# Campos adicionales que se muestran y editan en cada catálogo.  Sólo se
# habilitan si la columna existe, para conservar compatibilidad con bases
# anteriores a la migración incluida en database/mantenimiento_catalogos.sql.
CATALOGO_CAMPOS_ADICIONALES = {
    'marcas': [('descripcion', 'Descripción'), ('id_estado', 'Estado')],
    'colores': [('descripcion', 'Descripción'), ('id_estado', 'Estado')],
    'anios': [('id_estado', 'Estado')],
    'modelos': [('descripcion', 'Descripción'), ('id_estado', 'Estado')],
    # La tabla tipo_repuesto define la ubicación sugerida para cada tipo. Se
    # usan las mismas columnas que el esquema actual de Sistema_repuestos.
    'tipos': [
        ('descripcion', 'Descripción'),
        ('id_estado', 'Estado'),
        ('id_ubicacion', 'Ubicación (ID)'),
    ],
    'estados': [('descripcion', 'Descripción'), ('id_estado', 'Estado')],
    'origenes': [('descripcion', 'Descripción'), ('id_estado', 'Estado')],
    'transmisiones': [('descripcion', 'Descripción'), ('id_estado', 'Estado')],
    'ubicaciones': [('descripcion', 'Descripción'), ('id_estado', 'Estado')],
    'metodos_pago': [('id_estado', 'Estado')],
    'roles': [('descripcion', 'Descripción'), ('id_estado', 'Estado')],
    'estado_desarme': [('descripcion', 'Descripción'), ('id_estado', 'Estado')],
    'estado_pieza_desarme': [('descripcion', 'Descripción'), ('id_estado', 'Estado')],
    'motivo_devolucion': [('id_estado', 'Estado')],
    'estado_prestamo': [('id_estado', 'Estado')],
    'tipo_desarme': [('id_estado', 'Estado')],
    'estado_pieza': [('id_estado', 'Estado')],
    'motivo_incidencia': [('id_estado', 'Estado')],
}

# Campos que son llaves foráneas dentro de los catálogos.  Además de evitar
# que el usuario deba memorizar IDs, esta configuración permite mostrar el
# nombre real del registro relacionado en la tabla de mantenimiento.
CATALOGO_CAMPOS_RELACIONADOS = {
    'tipos': {
        'id_estado': ['estado_general', 'estado_repuesto'],
        'id_ubicacion': ['ubicacion', 'bodega', 'ubicacion_bodega'],
    },
}


def get_mantenimiento_catalogo(key, cursor):
    """Resuelve sólo tablas permitidas para evitar nombres SQL recibidos del cliente."""
    for catalogo_key, label, icon, candidates in MANTENIMIENTO_CATALOGOS:
        if catalogo_key != key:
            continue
        for table in candidates:
            columns = get_table_columns(cursor, table)
            id_column = next((column for column in columns if column.lower().startswith('id')), None)
            display_column = choose_display_column(columns)
            if id_column and display_column:
                return {'key': catalogo_key, 'label': label, 'icon': icon,
                        'table': table, 'id_column': id_column, 'display_column': display_column,
                        'name_label': 'Motivo' if catalogo_key == 'motivo_devolucion' else (
                            'Estado' if catalogo_key == 'estado_prestamo' else 'Nombre'
                        ),
                        'fields': [
                            {
                                'name': field_name,
                                'label': field_label,
                                'options': [
                                    {'id': row[0], 'nombre': row[1]}
                                    for row in fetch_catalog_for_candidates(
                                        cursor,
                                        CATALOGO_CAMPOS_RELACIONADOS.get(catalogo_key, {}).get(field_name, []),
                                    )
                                ],
                            }
                            for field_name, field_label in CATALOGO_CAMPOS_ADICIONALES.get(catalogo_key, [])
                            if field_name.lower() != id_column.lower()
                            and any(column.lower() == field_name.lower() for column in columns)
                        ]}
    return None


def fetch_tipos_repuesto(cursor, only_active=True):
    """Obtiene tipos junto con su ubicación sugerida; puede limitarse a activos."""
    columns = get_table_columns(cursor, 'tipo_repuesto')
    id_column = next((column for column in columns if column.lower().startswith('id')), None)
    display_column = choose_display_column(columns)
    ubicacion_column = next((column for column in columns if column.lower() == 'id_ubicacion'), None)
    estado_column = next((column for column in columns if column.lower() == 'id_estado'), None)
    if not id_column or not display_column:
        return []

    location_select = f', {ubicacion_column} AS id_ubicacion' if ubicacion_column else ''
    status_select = f', {estado_column} AS id_estado' if estado_column else ''
    cursor.execute(
        f"SELECT {id_column} AS id, {display_column} AS nombre{location_select}{status_select} "
        f"FROM tipo_repuesto ORDER BY {display_column}"
    )
    tipos = cursor.fetchall()

    # Los tipos inactivos se conservan en el catálogo para el historial, pero
    # no deben estar disponibles al registrar un nuevo repuesto.
    if not estado_column or not only_active:
        return tipos

    tipos_activos = []
    for tipo in tipos:
        estado_id = tipo.id_estado
        estado_nombre = get_display_name_by_id(
            cursor, ['estado_general', 'estado_repuesto'], estado_id
        )
        if estado_nombre is None or str(estado_nombre).strip().casefold() == 'activo':
            tipos_activos.append(tipo)
    return tipos_activos


def get_tipo_ubicacion(cursor, id_tipo_repuesto):
    """Obtiene la ubicación asignada al tipo de repuesto seleccionado."""
    columns = get_table_columns(cursor, 'tipo_repuesto')
    id_column = next((column for column in columns if column.lower().startswith('id')), None)
    ubicacion_column = next((column for column in columns if column.lower() == 'id_ubicacion'), None)
    if not id_tipo_repuesto or not id_column or not ubicacion_column:
        return None
    cursor.execute(
        f"SELECT {ubicacion_column} FROM tipo_repuesto WHERE {id_column} = ?",
        (id_tipo_repuesto,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


MANTENIMIENTO_BITACORAS = {
    'clientes': {'label': 'Clientes', 'tables': ('bitacora_cliente',)},
    'repuestos': {'label': 'Repuestos', 'tables': ('bitacora_repuesto',)},
    'ventas': {'label': 'Ventas', 'tables': ('bitacora_venta',)},
    'prestamos': {'label': 'Préstamos', 'tables': ('bitacora_prestamo',)},
    'login': {'label': 'Inicios de sesión', 'tables': ('bitacora_login',)},
}


def _audit_column(columns, candidates):
    indexed = {str(column).casefold(): column for column in columns}
    return next((indexed[candidate.casefold()] for candidate in candidates if candidate.casefold() in indexed), None)


def _audit_text(value):
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value).strip()


def _audit_user_display(cursor, user_id):
    """Resolve an audit user ID using the exact usuario.id_usuario key."""
    if user_id is None:
        return None
    try:
        cursor.execute(
            'SELECT primer_nombre, segundo_nombre, primer_apellido, segundo_apellido, username '
            'FROM usuario WHERE id_usuario = ?',
            (user_id,),
        )
        row = cursor.fetchone()
    except Exception:
        return None
    if not row:
        return None
    full_name = ' '.join(str(part).strip() for part in row[:4] if part and str(part).strip())
    return full_name or (str(row[4]).strip() if row[4] else None)


def _audit_state_display(cursor, column, value):
    """Translate state foreign keys found in audit snapshots to catalog labels."""
    if value is None or not str(column).casefold().startswith('id_estado'):
        return None
    column_key = str(column).casefold()
    candidates = {
        'id_estado_venta': ('estado_venta',),
        'id_estado_repuesto': ('estado_repuesto',),
        'id_estado_prestamo': ('estado_prestamo',),
        'id_estado_cliente': ('estado_cliente', 'estado_general'),
        'id_estado': ('estado_general', 'estado_cliente', 'estado_venta', 'estado_repuesto'),
    }.get(column_key, ())
    for table in candidates:
        columns = get_table_columns(cursor, f'dbo.{table}')
        by_lower = {str(item).casefold(): item for item in columns}
        id_candidates = {
            'estado_general': ('id_estado_general', 'id_estado', 'id_estado_cliente'),
            'estado_cliente': ('id_estado_cliente', 'id_estado'),
            'estado_venta': ('id_estado_venta', 'id_estado'),
            'estado_repuesto': ('id_estado_repuesto', 'id_estado'),
            'estado_prestamo': ('id_estado_prestamo', 'id_estado'),
        }.get(table, (column_key,))
        id_column = next((by_lower[name] for name in id_candidates if name in by_lower), None)
        label_column = next((by_lower[name] for name in ('estado', 'nombre', 'descripcion') if name in by_lower), None)
        if not id_column or not label_column:
            continue
        try:
            cursor.execute(
                f'SELECT [{label_column}] FROM [dbo].[{table}] WHERE [{id_column}] = ?',
                (value,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception:
            continue
    return None


REPUESTO_BITACORA_COLUMNS = [
    'id_bitacora', 'id_repuesto', 'codigo_repuesto', 'accion',
    'precio_anterior', 'precio_nuevo', 'stock_anterior', 'stock_nuevo',
    'detalles', 'fecha_accion', 'usuario',
]


def _mantenimiento_auditoria_rows(module_key, filters=None):
    module = MANTENIMIENTO_BITACORAS.get(module_key)
    if module is None:
        raise ValueError('El módulo de bitácora solicitado no existe.')

    filters = filters or {}
    query = str(filters.get('q') or '').strip()
    from_date = str(filters.get('from_date') or '').strip()
    to_date = str(filters.get('to_date') or '').strip()
    selected_user = str(filters.get('usuario') or '').strip().casefold()
    conn = get_db_connection()
    cursor = conn.cursor()
    records = []
    users = set()
    unavailable_tables = []
    table_columns_map = {}
    try:
        loan_state_names = {}
        if module_key == 'prestamos':
            loan_state_names = {'1': 'Pendiente / Activo', '2': 'Devuelto / Pagado', '3': 'Vencido'}
            try:
                cursor.execute('SELECT id_estado_prestamo, estado FROM estado_prestamo')
                loan_state_names.update({str(row[0]): str(row[1]) for row in cursor.fetchall() if row[0] is not None and row[1]})
            except Exception:
                pass
        for table in module['tables']:
            columns = get_table_columns(cursor, f'dbo.{table}')
            if not columns:
                unavailable_tables.append(table)
                continue

            date_column = _audit_column(columns, (
                'fecha_accion', 'fecha_login', 'fecha_eliminacion', 'fecha_borrado',
                'fecha_bitacora', 'fecha_registro', 'fecha', 'created_at',
            ))
            action_column = _audit_column(columns, (
                'accion', 'estado', 'tipo_accion', 'accion_realizada', 'operacion', 'evento',
            ))
            user_column = _audit_column(columns, (
                'id_usuario_cambio', 'id_usuario_accion', 'usuario_eliminacion',
                'eliminado_por', 'id_usuario', 'usuario', 'nombre_usuario', 'username',
            ))
            record_column = _audit_column(columns, (
                'id_detalle_venta', 'id_detalle_prestamo',
                'id_cliente', 'id_repuesto', 'id_venta', 'id_prestamo', 'id_usuario',
                'id_registro', 'id_registro_eliminado',
            ))
            audit_id_column = _audit_column(columns, (
                f'id_{table}', 'id_bitacora', 'id_auditoria',
            ))

            def quote(column):
                return f'[{str(column).replace("]", "]]")}]'

            order_column = date_column or audit_id_column
            sql = f"SELECT * FROM [dbo].[{table}]"
            if order_column:
                sql += f' ORDER BY {quote(order_column)} DESC'
            cursor.execute(sql)
            result_columns = [column[0] for column in cursor.description]
            table_columns_map[table] = result_columns

            for raw_row in cursor.fetchall():
                data = dict(zip(result_columns, raw_row))
                date_value = _audit_text(data.get(date_column)) if date_column else ''
                date_key = date_value[:10]
                if from_date and (not date_key or date_key < from_date):
                    continue
                if to_date and (not date_key or date_key > to_date):
                    continue

                raw_user = data.get(user_column) if user_column else None
                raw_user_text = _audit_text(raw_user)
                if raw_user is not None and re.fullmatch(r'\d+', raw_user_text):
                    user_value = _audit_user_display(cursor, raw_user_text) or raw_user_text
                else:
                    user_value = raw_user_text or 'No identificado'
                action = _audit_text(data.get(action_column)) if action_column else ''
                loan_id_column = _audit_column(result_columns, ('id_prestamo',)) if table == 'bitacora_prestamo' else None
                loan_id = data.get(loan_id_column) if loan_id_column else None
                observation_column = _audit_column(result_columns, ('detalles', 'observaciones', 'detalle', 'descripcion'))
                observation = _audit_text(data.get(observation_column)) if observation_column else ''
                actor_match = re.search(
                    r'id_usuario\s*:\s*(\d+)',
                    ' '.join(_audit_text(value) for value in data.values() if value is not None),
                    re.IGNORECASE,
                )
                if actor_match and user_value == 'No identificado':
                    user_value = _audit_user_display(cursor, actor_match.group(1)) or actor_match.group(1)
                audit_user_note = ''
                if module_key == 'clientes':
                    audit_user_note = ' '.join((action, _audit_text(raw_user), observation)).casefold()
                    # Older client audit rows kept the actor ID inside the
                    # serialized details instead of a dedicated user column.
                    if re.search(r'creaci[o\u00f3]n|cread[oa]|nuevo cliente|registro de cliente', audit_user_note):
                        action = 'Creaci\u00f3n'
                    elif re.search(r'actualizaci[o\u00f3]n|modificaci[o\u00f3]n|actualizad[oa]', audit_user_note):
                        action = 'Modificaci\u00f3n'
                    elif re.search(r'eliminaci[o\u00f3]n|eliminad[oa]', audit_user_note):
                        action = 'Eliminaci\u00f3n'
                    else:
                        action = action or 'Modificación'
                if table == 'bitacora_prestamo':
                    if 'cambio de estado' in observation.casefold():
                        action = 'Cambio de estado'
                    elif not action:
                        action = 'Actualización'
                users.add(user_value)
                if selected_user and user_value.casefold() != selected_user:
                    continue
                if observation:
                    state_column = {
                        'prestamos': 'id_estado_prestamo',
                        'ventas': 'id_estado_venta',
                        'repuestos': 'id_estado_repuesto',
                        'clientes': 'id_estado_cliente',
                    }.get(module_key, 'id_estado')

                    def state_transition_label(match):
                        old_id, new_id = match.group(2), match.group(4)
                        old_label = loan_state_names.get(old_id) if module_key == 'prestamos' else None
                        new_label = loan_state_names.get(new_id) if module_key == 'prestamos' else None
                        old_label = old_label or _audit_state_display(cursor, state_column, old_id) or old_id
                        new_label = new_label or _audit_state_display(cursor, state_column, new_id) or new_id
                        return f'{match.group(1)}{old_label}{match.group(3)}{new_label}'

                    observation = re.sub(
                        r'(Anterior:\s*)(\d+)(\s*->\s*Nuevo:\s*)(\d+)',
                        state_transition_label,
                        observation,
                        flags=re.IGNORECASE,
                    )
                excluded_columns = {
                    column for column in
                    (date_column, action_column, user_column, audit_id_column, record_column)
                    if column is not None
                }
                detail_columns = [
                    column for column in result_columns
                    if column not in excluded_columns
                    and not str(column).casefold().startswith('id_bitacora')
                ]
                detail_parts = []
                for column in detail_columns:
                    value = data.get(column)
                    if value is None or not _audit_text(value):
                        continue
                    column_key = str(column).casefold()
                    state_label = _audit_state_display(cursor, column, value)
                    if column_key.startswith('id_estado'):
                        label = state_label or (loan_state_names.get(str(value)) if table == 'bitacora_prestamo' else None) or _audit_text(value)
                        detail_parts.append(f'Estado: {label}')
                    elif table == 'bitacora_prestamo' and column == observation_column:
                        detail_parts.append(f'{column.replace("_", " ").capitalize()}: {observation}')
                    else:
                        detail_parts.append(f'{column.replace("_", " ").capitalize()}: {_audit_text(value)}')
                detail = ' · '.join(detail_parts)
                searchable_text = ' '.join(
                    _audit_text(value) for value in data.values() if value is not None
                )
                if query and not matches_multiword_search(query, {'text': searchable_text}, ['text']):
                    continue

                record = {
                    'fecha': date_value or '—',
                    'usuario': user_value,
                    'accion': action or ('Actualización' if table == 'bitacora_prestamo' else 'Eliminación'),
                    'registro': _audit_text(loan_id) if loan_id is not None else (_audit_text(data.get(record_column)) if record_column else '—'),
                    'detalle': detail or f'Registro eliminado de {table.replace("_", " ")}',
                    'tabla': 'prestamo' if table == 'bitacora_prestamo' else ('cliente' if module_key == 'clientes' else table),
                }
                if table == 'bitacora_repuesto':
                    record['accion'] = action or 'Modificación'
                    record['detalle'] = _audit_text(data.get(_audit_column(result_columns, ('detalles',)))) or detail
                    audit_user_id = data.get(_audit_column(result_columns, ('id_usuario',)))
                    record['usuario'] = _audit_user_display(cursor, audit_user_id) or user_value
                    record['registro'] = _audit_text(data.get(_audit_column(result_columns, ('id_repuesto',)))) or '—'
                    record['raw_data'] = {
                        col: _audit_text(data.get(col)) for col in REPUESTO_BITACORA_COLUMNS
                        if col in data
                    }
                    record['raw_data']['usuario'] = record['usuario']
                elif table == 'bitacora_login':
                    record['accion'] = action or 'EXITOSO'
                    record['detalle'] = _audit_text(data.get(observation_column)) if observation_column else ''
                    record['usuario'] = _audit_user_display(cursor, data.get(_audit_column(result_columns, ('id_usuario',)))) or user_value
                    record['registro'] = _audit_text(data.get(_audit_column(result_columns, ('id_usuario',)))) or '—'
                records.append(record)
        # Populate the filter from the complete user catalog, including users
        # who have not generated an entry in the selected audit module yet.
        try:
            cursor.execute(
                'SELECT primer_nombre, segundo_nombre, primer_apellido, segundo_apellido, username '
                'FROM [dbo].[usuario]'
            )
            for user_row in cursor.fetchall():
                full_name = ' '.join(
                    str(part).strip() for part in user_row[:4]
                    if part and str(part).strip()
                )
                display = full_name or (str(user_row[4]).strip() if user_row[4] else '')
                if display:
                    users.add(display)
        except Exception:
            # Keep observed audit users available if the user catalog is
            # temporarily inaccessible or uses a legacy schema.
            pass
    finally:
        cursor.close()
        conn.close()

    records.sort(key=lambda row: row['fecha'], reverse=True)
    return records, sorted(users, key=str.casefold), unavailable_tables, table_columns_map


@app.route('/mantenimiento')
def mantenimiento():
    if 'user' not in session:
        return redirect(url_for('login'))
    if not has_permission_in_session('mantenimiento', 'read'):
        flash('No tienes permisos para acceder al módulo de mantenimiento.', 'error')
        return redirect(url_for('dashboard'))

    catalogos, total_registros = [], 0
    active_catalog = request.args.get('catalogo', '').strip()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        for key, label, icon, _ in MANTENIMIENTO_CATALOGOS:
            catalogo = get_mantenimiento_catalogo(key, cursor)
            if not catalogo:
                continue
            if key == 'modelos':
                # Un modelo depende de una marca, por eso requiere una vista
                # propia con marca y descripción, no la tabla genérica.
                columns = get_table_columns(cursor, catalogo['table'])
                marca_column = next((c for c in columns if c.lower() == 'id_marca'), None)
                descripcion_column = next((c for c in columns if 'descripcion' in c.lower() or 'observacion' in c.lower()), None)
                cursor.execute(f"SELECT * FROM {catalogo['table']} ORDER BY {catalogo['display_column']} ASC")
                column_names = [column[0] for column in cursor.description]
                rows = []
                for raw_row in cursor.fetchall():
                    item = dict(zip(column_names, raw_row))
                    marca_id = item.get(marca_column) if marca_column else None
                    estado_id = item.get('id_estado')
                    rows.append({
                        'id': item.get(catalogo['id_column']),
                        'nombre': item.get(catalogo['display_column']),
                        'marca_id': marca_id,
                        'marca': get_display_name_by_id(cursor, ['marca_vehiculo', 'marca'], marca_id) or 'Sin marca',
                        'descripcion': item.get(descripcion_column) if descripcion_column else '',
                        'extra': {'id_estado': estado_id},
                        'extra_display': {'id_estado': 'Activo' if estado_id == 1 else ('Inactivo' if estado_id == 2 else '—')},
                    })
                catalogo['marcas'] = [
                    {'id': row[0], 'nombre': row[1]}
                    for row in fetch_catalog_for_candidates(cursor, ['marca_vehiculo', 'marca'])
                ]
                catalogo['rows'] = rows
            else:
                columns = get_table_columns(cursor, catalogo['table'])
                # Motivo de devolución debe conservar el orden definido en la
                # tabla (id_motivo), no el alfabético de la descripción.
                if key == 'motivo_devolucion':
                    order_column = catalogo['id_column']
                    order_direction = 'ASC'
                else:
                    order_column = catalogo['display_column']
                    order_direction = 'ASC'
                cursor.execute(f"SELECT * FROM {catalogo['table']} ORDER BY {order_column} {order_direction}")
                column_names = [column[0] for column in cursor.description]
                rows = []
                for raw_row in cursor.fetchall():
                    item = dict(zip(column_names, raw_row))
                    extra = {field['name']: item.get(field['name']) for field in catalogo['fields']}
                    extra_display = {}
                    for field in catalogo['fields']:
                        related_tables = CATALOGO_CAMPOS_RELACIONADOS.get(key, {}).get(field['name'])
                        value = extra[field['name']]
                        extra_display[field['name']] = (
                            get_display_name_by_id(cursor, related_tables, value)
                            if related_tables and value is not None else value
                        )
                    # Include id_estado for the edit modal checkbox
                    estado_id = item.get('id_estado')
                    extra['id_estado'] = estado_id
                    extra_display['id_estado'] = 'Activo' if estado_id == 1 else ('Inactivo' if estado_id == 2 else '—')
                    search = ' '.join(str(value or '') for value in [item.get(catalogo['display_column'])] + list(extra.values()))
                    rows.append({'id': item.get(catalogo['id_column']),
                                 'nombre': item.get(catalogo['display_column']),
                                 'extra': extra, 'extra_display': extra_display, 'search': search.lower()})
                catalogo['rows'] = rows
            catalogo['count'] = len(rows)
            total_registros += len(rows)
            try:
                catalog_page = max(1, int(request.args.get(f'page_{key}', 1)))
            except (TypeError, ValueError):
                catalog_page = 1
            catalog_page_size = 10
            catalog_total_pages = max(1, (len(rows) + catalog_page_size - 1) // catalog_page_size)
            catalog_page = min(catalog_page, catalog_total_pages)
            catalogo['page'] = catalog_page
            catalogo['total_pages'] = catalog_total_pages
            start = (catalog_page - 1) * catalog_page_size
            catalogo['rows'] = rows[start:start + catalog_page_size]
            catalogos.append(catalogo)
        cursor.close()
        conn.close()
    except Exception as error:
        flash(f'No fue posible cargar los catálogos: {error}', 'error')

    # Si la URL no indica un catálogo válido, se abre el primero disponible.
    available_catalogs = {catalogo['key'] for catalogo in catalogos}
    if active_catalog not in available_catalogs:
        active_catalog = catalogos[0]['key'] if catalogos else ''

    # Estas tablas contienen transacciones y se presentan por separado para
    # operaciones de limpieza, tal como el diseño de referencia.
    tablas_sistema = []
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        for table, label in [('repuesto', 'Productos'), ('venta', 'Ventas'), ('prestamo', 'Préstamos'),
                             ('usuario', 'Usuarios'), ('devolucion', 'Devoluciones')]:
            if get_table_columns(cursor, table):
                cursor.execute(f'SELECT COUNT(*) FROM {table}')
                tablas_sistema.append({'table': table, 'label': label, 'count': cursor.fetchone()[0]})
        cursor.close()
        conn.close()
    except Exception:
        pass
    sidebar_user = get_current_sidebar_user()
    return render_template(
        'mantenimiento.html', catalogos=catalogos, active_catalog=active_catalog,
        total_registros=total_registros, tablas_sistema=tablas_sistema,
        user_name=sidebar_user['name'], user_role=sidebar_user['role'],
        initial_view='audit' if request.args.get('vista') == 'audit' else 'database',
        maintenance_can_write=has_permission_in_session('mantenimiento', 'write'),
    )


@app.route('/mantenimiento/tablas/<key>/limpiar', methods=['POST'])
def limpiar_tabla_mantenimiento(key):
    if 'user' not in session or not has_permission_in_session('mantenimiento', 'write'):
        return jsonify({'error': 'No tienes permisos para limpiar tablas.'}), 403

    # El cliente sólo puede elegir entre estas tablas; nunca se usa un nombre
    # de tabla arbitrario recibido en el cuerpo de la petición.
    tables = {
        'repuesto': ('repuesto', 'Productos'),
        'venta': ('venta', 'Ventas'),
        'prestamo': ('prestamo', 'Préstamos'),
        'usuario': ('usuario', 'Usuarios'),
        'devolucion': ('devolucion', 'Devoluciones'),
    }
    if key not in tables:
        return jsonify({'error': 'La tabla seleccionada no está disponible para limpieza.'}), 404

    table, label = tables[key]
    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if not get_table_columns(cursor, table):
            return jsonify({'error': f'No se encontró la tabla {label}.'}), 404

        # Los detalles forman parte de estas tablas transaccionales y deben
        # eliminarse primero para respetar sus relaciones.
        cleanup = {
            'venta': [
                ('detalle_devolucion', 'id_devolucion IN (SELECT id_devolucion FROM dbo.devolucion WHERE id_venta IN (SELECT id_venta FROM dbo.venta))'),
                ('devolucion', 'id_venta IN (SELECT id_venta FROM dbo.venta)'),
                ('detalle_venta', 'id_venta IN (SELECT id_venta FROM dbo.venta)'),
            ],
            'prestamo': [
                ('detalle_prestamo', 'id_prestamo IN (SELECT id_prestamo FROM dbo.prestamo)'),
            ],
            'devolucion': [
                ('detalle_devolucion', 'id_devolucion IN (SELECT id_devolucion FROM dbo.devolucion)'),
            ],
        }
        for child, condition in cleanup.get(table, []):
            if get_table_columns(cursor, child):
                cursor.execute(f'DELETE FROM dbo.[{child}] WHERE {condition}')

        # Evita borrar productos o usuarios que aún son usados por registros
        # históricos. El mensaje de FK que retorne SQL Server se presenta al usuario.
        cursor.execute(f'DELETE FROM dbo.[{table}]')
        deleted = max(cursor.rowcount, 0)
        conn.commit()
        return jsonify({'success': True, 'message': f'{label}: se limpiaron {deleted} registros.'})
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception('No fue posible limpiar la tabla de mantenimiento %s.', table)
        return jsonify({'error': 'No se pudo limpiar la tabla. Puede tener registros relacionados; limpia primero sus tablas dependientes.'}), 409
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route('/mantenimiento/auditoria')
def mantenimiento_auditoria():
    if 'user' not in session or not has_permission_in_session('mantenimiento', 'read'):
        return jsonify({'error': 'No tienes permisos para consultar las bitácoras.'}), 403

    module_key = request.args.get('modulo', 'clientes').strip()
    filters = {
        'q': request.args.get('q', ''),
        'from_date': request.args.get('from_date', ''),
        'to_date': request.args.get('to_date', ''),
        'usuario': request.args.get('usuario', ''),
    }
    try:
        rows, users, unavailable_tables, table_columns_map = _mantenimiento_auditoria_rows(module_key, filters)
        try:
            page = max(1, int(request.args.get('page', 1)))
        except (TypeError, ValueError):
            page = 1
        page_size = 10
        total_pages = max(1, (len(rows) + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        response = {
            'success': True,
            'modulo': module_key,
            'label': MANTENIMIENTO_BITACORAS[module_key]['label'],
            'registros': rows[start:start + page_size],
            'total': len(rows),
            'pagina': page,
            'total_paginas': total_pages,
            'usuarios': users,
            'tablas_no_disponibles': unavailable_tables,
        }
        if module_key == 'repuestos':
            response['columnas'] = REPUESTO_BITACORA_COLUMNS
        return jsonify(response)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except Exception:
        app.logger.exception('No fue posible consultar las bitácoras del módulo %s.', module_key)
        return jsonify({'error': 'No fue posible consultar las bitácoras. Verifica la conexión y el esquema de la base de datos.'}), 500


@app.route('/mantenimiento/auditoria/exportar/<file_format>')
def exportar_mantenimiento_auditoria(file_format):
    if 'user' not in session or not has_permission_in_session('mantenimiento', 'read'):
        return jsonify({'error': 'No tienes permisos para exportar las bitácoras.'}), 403
    if file_format not in {'csv', 'pdf'}:
        return jsonify({'error': 'Formato de exportación no válido.'}), 400

    module_key = request.args.get('modulo', 'clientes').strip()
    filters = {
        'q': request.args.get('q', ''),
        'from_date': request.args.get('from_date', ''),
        'to_date': request.args.get('to_date', ''),
        'usuario': request.args.get('usuario', ''),
    }
    try:
        rows, _, _, _ = _mantenimiento_auditoria_rows(module_key, filters)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except Exception:
        app.logger.exception('No fue posible exportar las bitácoras del módulo %s.', module_key)
        return jsonify({'error': 'No fue posible generar la exportación de bitácoras.'}), 500

    module_label = MANTENIMIENTO_BITACORAS[module_key]['label']
    if module_key == 'repuestos':
        headers = [col.replace('_', ' ').capitalize() for col in REPUESTO_BITACORA_COLUMNS]
        export_rows = [
            [row.get('raw_data', {}).get(col, '—') for col in REPUESTO_BITACORA_COLUMNS]
            for row in rows
        ]
    else:
        headers = ['Fecha', 'Acción', 'Registro', 'Detalle', 'Usuario', 'Tabla']
        export_rows = [
            [row['fecha'], row['accion'], row['registro'], row['detalle'], row['usuario'], row['tabla']]
            for row in rows
        ]
    filename_base = f'bitacora_{module_key}'
    if file_format == 'csv':
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(headers)
        for row in export_rows:
            writer.writerow([
                f"'{value}" if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value
                for value in row
            ])
        buffer = io.BytesIO(('\ufeff' + output.getvalue()).encode('utf-8'))
        return send_file(
            buffer, as_attachment=True, download_name=f'{filename_base}.csv',
            mimetype='text/csv; charset=utf-8',
        )

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=letter, leftMargin=36, rightMargin=36,
        topMargin=42, bottomMargin=36,
    )
    styles = getSampleStyleSheet()
    cell_style = styles['BodyText']
    cell_style.fontName = 'Helvetica'
    cell_style.fontSize = 7
    cell_style.leading = 9
    cell_style.wordWrap = 'CJK'
    table_data = [[Paragraph(escape(value), cell_style) for value in headers]]
    table_data.extend([
        [Paragraph(escape(str(value or '—')), cell_style) for value in row]
        for row in export_rows
    ])
    table = Table(
        table_data,
        colWidths=[540 / max(1, len(headers))] * len(headers),
        repeatRows=1,
        splitByRow=1,
    )
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), (0.90, 0.94, 1.00)),
        ('TEXTCOLOR', (0, 0), (-1, 0), (0, 0, 0)),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.4, (0.72, 0.76, 0.82)),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [(1, 1, 1), (0.97, 0.98, 1.0)]),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    document.build([
        Paragraph(f'Auditoría y Bitácoras — {escape(module_label)}', styles['Title']),
        Spacer(1, 12),
        table,
    ])
    buffer.seek(0)
    return send_file(
        buffer, as_attachment=True, download_name=f'{filename_base}.pdf',
        mimetype='application/pdf',
    )


@app.route('/mantenimiento/catalogo/<key>', methods=['POST'])
def guardar_catalogo_mantenimiento(key):
    if 'user' not in session or not has_permission_in_session('mantenimiento', 'write'):
        flash('No tienes permisos para modificar catálogos.', 'error')
        return redirect(url_for('dashboard'))

    action = request.form.get('action', 'add')
    value = request.form.get('nombre', '').strip()
    row_id = request.form.get('id', '').strip()
    if action != 'delete' and not value:
        flash('Ingresa un valor para el catálogo.', 'error')
        return redirect(url_for('mantenimiento', catalogo=key))
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        catalogo = get_mantenimiento_catalogo(key, cursor)
        if not catalogo:
            raise ValueError('El catálogo seleccionado no está disponible.')
        table, id_column, display_column = catalogo['table'], catalogo['id_column'], catalogo['display_column']
        columns = get_table_columns(cursor, table)
        # Some catalogs (notably estado_general) use id_estado as their
        # identity primary key. It must never be treated as the optional
        # active/inactive flag, otherwise edits generate UPDATE id_estado =
        # ... and SQL Server rejects the identity-column update.
        has_estado = any(
            c.lower() == 'id_estado' and c.lower() != id_column.lower()
            for c in columns
        )
        if key == 'modelos' and action != 'delete':
            marca_id = request.form.get('id_marca', '').strip()
            if not marca_id:
                raise ValueError('Selecciona la marca del modelo.')
            descripcion_column = next((c for c in columns if 'descripcion' in c.lower() or 'observacion' in c.lower()), None)
            model_data = {display_column: value, 'id_marca': marca_id}
            if descripcion_column:
                model_data[descripcion_column] = request.form.get('descripcion', '').strip() or None
            if has_estado:
                model_data['id_estado'] = 1 if request.form.get('id_estado') else 2
            if action == 'add':
                insert_columns = [column for column in model_data if column in columns]
                cursor.execute(
                    f"INSERT INTO {table} ({', '.join(insert_columns)}) VALUES ({', '.join('?' for _ in insert_columns)})",
                    tuple(model_data[column] for column in insert_columns),
                )
                message = 'Modelos: registro agregado correctamente.'
            elif action == 'edit' and row_id:
                update_columns = [column for column in model_data if column in columns]
                cursor.execute(
                    f"UPDATE {table} SET {', '.join(f'{column} = ?' for column in update_columns)} WHERE {id_column} = ?",
                    tuple(model_data[column] for column in update_columns) + (row_id,),
                )
                message = 'Modelos: registro actualizado correctamente.'
            else:
                raise ValueError('La operación solicitada no es válida.')
        elif action in ('add', 'edit'):
            extra_data = {
                field['name']: request.form.get(field['name'], '').strip() or None
                for field in catalogo['fields'] if field['name'] in columns
            }
            if has_estado:
                extra_data['id_estado'] = 1 if request.form.get('id_estado') else 2
            data = {display_column: value, **extra_data}
            if action == 'add':
                insert_columns = list(data)
                cursor.execute(
                    f"INSERT INTO {table} ({', '.join(insert_columns)}) VALUES ({', '.join('?' for _ in insert_columns)})",
                    tuple(data[column] for column in insert_columns),
                )
                message = f'{catalogo["label"]}: registro agregado correctamente.'
            elif row_id:
                update_columns = list(data)
                cursor.execute(
                    f"UPDATE {table} SET {', '.join(f'{column} = ?' for column in update_columns)} WHERE {id_column} = ?",
                    tuple(data[column] for column in update_columns) + (row_id,),
                )
                message = f'{catalogo["label"]}: registro actualizado correctamente.'
            else:
                raise ValueError('La operación solicitada no es válida.')
        elif action == 'delete' and row_id:
            cursor.execute(f'DELETE FROM {table} WHERE {id_column} = ?', (row_id,))
            message = f'{catalogo["label"]}: registro eliminado correctamente.'
        else:
            raise ValueError('La operación solicitada no es válida.')
        conn.commit()
        cursor.close()
        conn.close()
        flash(message, 'success')
    except Exception as error:
        try:
            conn.rollback()
            cursor.close()
            conn.close()
        except Exception:
            pass
        flash(f'No se pudo guardar el cambio. Puede estar relacionado con otros registros: {error}', 'error')
    return redirect(url_for('mantenimiento', catalogo=key))


@app.route('/mantenimiento/backup/descargar')
def mantenimiento_backup_descargar():
    if 'user' not in session or not has_permission_in_session('mantenimiento', 'read'):
        return jsonify({'error': 'No tienes permisos para crear backups.'}), 403

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Obtener TODAS las tablas de usuario de la base de datos
        cursor.execute("""
            SELECT TABLE_NAME 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_TYPE = 'BASE TABLE' 
            AND TABLE_SCHEMA = 'dbo'
            ORDER BY TABLE_NAME
        """)
        tablas_backup = [row[0] for row in cursor.fetchall()]

        import zipfile
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for tabla in tablas_backup:
                try:
                    columns = get_table_columns(cursor, tabla)
                    if not columns:
                        continue

                    cursor.execute(f'SELECT * FROM [{tabla}]')
                    rows = cursor.fetchall()

                    if not rows:
                        # Crear CSV solo con headers aunque esté vacío
                        csv_buffer = io.StringIO()
                        writer = csv.writer(csv_buffer)
                        writer.writerow(columns)
                        csv_content = csv_buffer.getvalue()
                        zipf.writestr(f'{tabla}.csv', csv_content.encode('utf-8'))
                        continue

                    # Crear CSV en memoria
                    csv_buffer = io.StringIO()
                    writer = csv.writer(csv_buffer)
                    writer.writerow(columns)  # Header
                    
                    for row in rows:
                        writer.writerow([str(val) if val is not None else '' for val in row])
                    
                    # Agregar al ZIP
                    csv_content = csv_buffer.getvalue()
                    zipf.writestr(f'{tabla}.csv', csv_content.encode('utf-8'))

                except Exception as e:
                    # Continuar con otras tablas si una falla
                    continue

        cursor.close()
        conn.close()

        zip_buffer.seek(0)
        filename = f'backup_importadora_morales_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/zip'
        )

    except Exception as e:
        return jsonify({'error': f'Error al crear backup: {str(e)}'}), 500


@app.route('/mantenimiento/backup/restaurar', methods=['POST'])
def mantenimiento_backup_restaurar():
    if 'user' not in session or not has_permission_in_session('mantenimiento', 'write'):
        return jsonify({'error': 'No tienes permisos para restaurar backups.'}), 403

    if 'archivo' not in request.files:
        return jsonify({'error': 'No se seleccionó ningún archivo.'}), 400

    file = request.files['archivo']
    if file.filename == '':
        return jsonify({'error': 'No se seleccionó ningún archivo.'}), 400

    if not file.filename.endswith('.zip'):
        return jsonify({'error': 'El archivo debe ser formato .zip con archivos CSV'}), 400

    try:
        import zipfile
        zip_content = file.read()
        zip_buffer = io.BytesIO(zip_content)

        conn = get_db_connection()
        cursor = conn.cursor()

        # Obtener todas las tablas de la BD para ordenarlas por dependencias
        cursor.execute("""
            SELECT TABLE_NAME 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_TYPE = 'BASE TABLE' 
            AND TABLE_SCHEMA = 'dbo'
        """)
        all_tables = [row[0] for row in cursor.fetchall()]

        # Ordenar tablas: primero las que no tienen FKs (catálogos), luego las que sí
        # Usamos una heurística simple: tablas con "id_" en muchas columnas van al final
        def table_sort_key(t):
            cols = get_table_columns(cursor, t)
            fk_count = sum(1 for c in cols if c.lower().startswith('id_') and c.lower() != 'id')
            return (fk_count, t)

        tablas_orden = sorted(all_tables, key=table_sort_key)

        restored_tables = 0
        total_rows = 0
        errors = []

        with zipfile.ZipFile(zip_buffer, 'r') as zipf:
            for tabla in tablas_orden:
                csv_filename = f'{tabla}.csv'
                if csv_filename not in zipf.namelist():
                    continue

                try:
                    with zipf.open(csv_filename) as csv_file:
                        # Leer CSV
                        content = csv_file.read().decode('utf-8')
                        reader = csv.reader(io.StringIO(content))
                        rows = list(reader)

                    if not rows:
                        continue

                    columns = rows[0]
                    data_rows = rows[1:]

                    # Verificar que la tabla existe
                    table_columns = get_table_columns(cursor, tabla)
                    if not table_columns:
                        errors.append(f'Tabla {tabla} no existe en BD')
                        continue

                    # Filtrar columnas que existen en la BD
                    valid_columns = [c for c in columns if c in table_columns]
                    if not valid_columns:
                        errors.append(f'Ninguna columna válida en {tabla}')
                        continue

                    # Limpiar tabla
                    cursor.execute(f'DELETE FROM [{tabla}]')

                    # Insertar datos
                    placeholders = ', '.join(['?'] * len(valid_columns))
                    cols_sql = ', '.join(f'[{c}]' for c in valid_columns)
                    sql = f'INSERT INTO [{tabla}] ({cols_sql}) VALUES ({placeholders})'

                    col_indices = [columns.index(c) for c in valid_columns]

                    for row in data_rows:
                        values = [row[i] if row[i] != '' else None for i in col_indices]
                        cursor.execute(sql, values)
                        total_rows += 1

                    restored_tables += 1

                except Exception as e:
                    errors.append(f'Error en {tabla}: {str(e)}')

        if errors:
            conn.rollback()
            cursor.close()
            conn.close()
            return jsonify({'error': f'Errores al restaurar: {len(errors)} tablas fallaron', 'detalles': errors[:5]}), 500

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True, 'message': f'Backup restaurado: {restored_tables} tablas, {total_rows} registros.'})

    except zipfile.BadZipFile:
        return jsonify({'error': 'Archivo ZIP inválido o corrupto'}), 400
    except Exception as e:
        return jsonify({'error': f'Error al restaurar backup: {str(e)}'}), 500


def clean_duplicate_movements():
    """Limpia movimientos duplicados de la BD. Elimina movimientos automáticos si existen movimientos iniciales del mismo repuesto."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Eliminar movimientos con detalle "Ingreso automático de repuesto" si existen movimientos "Ingreso inicial del repuesto" del mismo repuesto
        cursor.execute("""
            DELETE FROM movimiento_inventario
            WHERE detalle = 'Ingreso automático de repuesto'
            AND id_repuesto IN (
                SELECT DISTINCT id_repuesto FROM movimiento_inventario
                WHERE detalle = 'Ingreso inicial del repuesto'
            )
        """)
        conn.commit()
        cursor.close()
        conn.close()
    except Exception:
        pass


@app.route('/inventario/limpiar-duplicados')
def limpiar_duplicados():
    """Ruta para limpiar manualmente movimientos duplicados."""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    role = session.get('role')
    if role == 'Vendedor':
        flash('No tienes permisos para limpiar datos.', 'error')
        return redirect(url_for('inventario'))
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM movimiento_inventario
            WHERE detalle = 'Ingreso automático de repuesto'
            AND id_repuesto IN (
                SELECT DISTINCT id_repuesto FROM movimiento_inventario
                WHERE detalle = 'Ingreso inicial del repuesto'
            )
        """)
        eliminados = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        flash(f'Se eliminaron {eliminados} movimientos duplicados.', 'success')
    except Exception as e:
        flash(f'Error al limpiar duplicados: {str(e)}', 'error')
    
    return redirect(url_for('inventario'))


@app.route('/inventario')
def inventario():
    # Validación de sesión para evitar accesos no autorizados
    if 'user' not in session:
        return redirect(url_for('login'))
    lista_repuestos = []

    # Cargar repuestos directamente y resolver nombres mediante helpers
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Load available tipo_repuesto options from the database for the filter dropdown
        tipo_repuestos = fetch_catalog_for_candidates(cursor, ['tipo_repuesto']) or []
        selected_tipo_id = request.args.get('tipo_repuesto', '').strip()
        search_query = request.args.get('q', '').strip()
        stock_bajo_filter = request.args.get('stock_bajo', '').strip()

        # Report filters
        report_from_date = request.args.get('from_date', '').strip()
        report_to_date = request.args.get('to_date', '').strip()
        report_tipo_repuesto = request.args.get('tipo_repuesto', '').strip()
        report_marca = request.args.get('marca', '').strip()
        report_estado = request.args.get('estado_repuesto', '').strip()
        report_ubicacion = request.args.get('ubicacion', '').strip()
        report_search = request.args.get('search', '').strip()
        report_stock_min = request.args.get('stock_min', '').strip()
        report_stock_max = request.args.get('stock_max', '').strip()
        report_precio_compra_min = request.args.get('precio_compra_min', '').strip()
        report_precio_venta_max = request.args.get('precio_venta_max', '').strip()

        # The general active/inactive status is separate from the physical part status.
        repuesto_columns = get_table_columns(cursor, 'repuesto')
        photo_select = ', fotografia' if any(column.lower() == 'fotografia' for column in repuesto_columns) else ''
        estado_general_select = (
            ', id_estado_general'
            if any(column.lower() == 'id_estado_general' for column in repuesto_columns)
            else ', NULL AS id_estado_general'
        )
        transmision_select = (
            ', id_transmision'
            if any(column.lower() == 'id_transmision' for column in repuesto_columns)
            else ', NULL AS id_transmision'
        )
        base_sql = (
            "SELECT id_repuesto, codigo_repuesto, id_vehiculo_origen, id_tipo_repuesto, "
            "id_marca, id_modelo, id_anio, id_color, id_lado, id_posicion, id_estado_repuesto, "
            "id_ubicacion, precio_compra, precio_venta, observaciones, fecha_ingreso, "
            "id_usuario_registro, stock, stock_minimo, stock_maximo "
            f"{photo_select}{estado_general_select}{transmision_select} FROM repuesto"
        )
        params = []
        if selected_tipo_id:
            try:
                params.append(int(selected_tipo_id))
            except ValueError:
                params.append(selected_tipo_id)
            base_sql += " WHERE id_tipo_repuesto = ?"
        # El ID se genera al registrar cada repuesto; ordenarlo de forma descendente
        # mantiene los registros más recientes al inicio de la paginación.
        base_sql += " ORDER BY id_repuesto DESC"
        cursor.execute(base_sql, tuple(params))
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description]

        # force stock column if exists in repuesto schema
        stock_col = 'stock' if 'stock' in cols else choose_numeric_column(cols)
        if not stock_col:
            stock_col = choose_numeric_column_by_sample(cols, rows) or 'stock'

        # DEBUG: print detected columns and chosen stock column
        try:
            print('\n[DEBUG] repuesto columns:', cols)
            print('[DEBUG] chosen stock_col:', stock_col)
            if rows:
                print('[DEBUG] first row raw values:', rows[0])
        except Exception:
            pass


        # candidate tables for related lookups
        tipo_candidates = ['tipo_repuesto']
        marca_candidates = ['marca_vehiculo', 'marca']
        modelo_candidates = ['modelo_vehiculo', 'modelo']
        color_candidates = ['color']
        lado_candidates = ['lado_repuesto']
        posicion_candidates = ['posicion_repuesto']

        for r in rows:
            data = dict(zip(cols, r))
            codigo = data.get('codigo_repuesto') or data.get('codigo') or data.get('codigo_repuesto')
            stock = data.get(stock_col) if stock_col in data else data.get('stock')
            if stock is None:
                stock = 0
            precio = data.get('precio_venta') or data.get('precio') or data.get('price') or 0.0
            rep_id = data.get('id_repuesto') or data.get('id')

            vehiculo_origen = get_display_name_by_id(cursor, ['vehiculo_origen'], data.get('id_vehiculo_origen')) or '-'
            tipo_repuesto = get_display_name_by_id(cursor, tipo_candidates, data.get('id_tipo_repuesto')) or '-'
            marca = get_display_name_by_id(cursor, marca_candidates, data.get('id_marca')) or '-'
            modelo = get_display_name_by_id(cursor, modelo_candidates, data.get('id_modelo')) or '-'
            anio = get_display_name_by_id(cursor, ['anio_vehiculo'], data.get('id_anio')) or data.get('id_anio') or '-'
            color = get_display_name_by_id(cursor, color_candidates, data.get('id_color')) or '-'
            lado = get_display_name_by_id(cursor, lado_candidates, data.get('id_lado')) or '-'
            posicion = get_display_name_by_id(cursor, posicion_candidates, data.get('id_posicion')) or '-'
            estado_repuesto_text = get_display_name_by_id(cursor, ['estado_repuesto'], data.get('id_estado_repuesto')) or '-'
            # Support schemas that use either id_ubicacion or id_bodega as the FK
            loc_id = data.get('id_ubicacion') or data.get('id_bodega')
            ubicacion_bodega = get_display_name_by_id(cursor, ['bodega', 'ubicacion', 'ubicacion_bodega'], loc_id) or '-'
            usuario_registro = get_user_full_name(cursor, data.get('id_usuario_registro')) or get_display_name_by_id(cursor, ['usuario'], data.get('id_usuario_registro')) or '-'
            # Prefer the explicit repuesto estado FK if present; fall back to any
            # legacy `id_estado` if it exists in the schema (some installs may
            # still have it). This avoids SQL errors when `id_estado` column
            # is not present in the `repuesto` table.
            estado_general = get_display_name_by_id(
                cursor, ['estado_general'], data.get('id_estado_general')
            ) or '-'
            transmision = get_display_name_by_id(cursor, ['transmision', 'transmision_vehiculo', 'tipo_transmision'], data.get('id_transmision')) or data.get('transmision') or data.get('transmision_repuesto') or '-'
            stock_minimo = data.get('stock_minimo') or data.get('min_stock') or data.get('stock_min') or data.get('stock_minimo') or ''
            stock_maximo = data.get('stock_maximo') or data.get('max_stock') or data.get('stock_max') or data.get('stock_maximo') or ''
            
            # ensure numeric stock using robust parser
            try:
                raw_stock = stock
                stock_val = parse_int_like(raw_stock)
            except Exception:
                stock_val = 0

            # DEBUG: show raw stock and parsed
            try:
                print(f"[DEBUG] codigo={codigo} raw_stock={raw_stock!r} parsed_stock={stock_val}")
            except Exception:
                pass

            try:
                precio_val = float(precio) if precio is not None else 0.0
            except Exception:
                precio_val = 0.0

            try:
                precio_compra_val = float(data.get('precio_compra')) if data.get('precio_compra') is not None else 0.0
            except Exception:
                precio_compra_val = 0.0

            lista_repuestos.append({
                'id_repuesto': rep_id,
                'codigo_repuesto': codigo,
                'vehiculo_origen': vehiculo_origen,
                'tipo_repuesto': tipo_repuesto,
                'marca': marca,
                'modelo': modelo,
                'anio': anio,
                'color': color,
                'lado': lado,
                'posicion': posicion,
                'estado_repuesto': estado_repuesto_text,
                'ubicacion_bodega': ubicacion_bodega,
                'precio_compra': precio_compra_val,
                'precio_venta': precio_val,
                'observaciones': data.get('observaciones'),
                'fotografia': data.get('fotografia'),
                'fecha_ingreso': data.get('fecha_ingreso'),
                'usuario_registro': usuario_registro,
                'estado': estado_general,
                'transmision': transmision,
                'stock_minimo': stock_minimo,
                'stock_maximo': stock_maximo,
                'stock': stock_val
            })

        cursor.close()
        conn.close()

        if search_query:
            search_fields = [
                'id_repuesto',
                'codigo_repuesto',
                'vehiculo_origen',
                'marca',
                'modelo',
                'anio',
                'color',
                'lado',
                'posicion',
                'estado_repuesto',
                'ubicacion_bodega',
                'precio_compra',
                'precio_venta',
                'observaciones',
                'fecha_ingreso',
                'usuario_registro',
                'estado',
                'stock',
                'stock_minimo',
                'stock_maximo'
            ]
            lista_repuestos = [r for r in lista_repuestos if matches_multiword_search(search_query, r, search_fields)]

        if stock_bajo_filter:
            lista_repuestos = [r for r in lista_repuestos if r.get('stock', 0) <= (r.get('stock_minimo') or 5)]
    except Exception as e:
        print(f"\n⚠️ ERROR EN CONSULTA DE INVENTARIO: {e}\n")
        flash(f"Error al conectar con la base de datos: {e}", "error")
        tipo_repuestos = []
        selected_tipo_id = ''
        search_query = ''

    # Paginación: 10 repuestos por página, ordenados por ID descendente.
    try:
        current_page = int(request.args.get('page', 1))
    except (TypeError, ValueError):
        current_page = 1

    report_marcas = sorted({r.get('marca') for r in lista_repuestos if r.get('marca') and r.get('marca') != '-'})
    report_estados = sorted({r.get('estado_repuesto') for r in lista_repuestos if r.get('estado_repuesto') and r.get('estado_repuesto') != '-'})
    report_ubicaciones = sorted({r.get('ubicacion_bodega') for r in lista_repuestos if r.get('ubicacion_bodega') and r.get('ubicacion_bodega') != '-'})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        movimientos = get_movimientos_inventario(cursor)
        cursor.close()
        conn.close()
    except Exception:
        movimientos = []

    try:
        movimientos_page = max(1, int(request.args.get('movimientos_page', 1)))
    except (TypeError, ValueError):
        movimientos_page = 1
    movimientos_page_size = 17
    movimientos_total = len(movimientos)
    movimientos_total_pages = max(1, (movimientos_total + movimientos_page_size - 1) // movimientos_page_size)
    movimientos_page = min(movimientos_page, movimientos_total_pages)
    movimientos_start = (movimientos_page - 1) * movimientos_page_size
    movimientos_paginados = movimientos[movimientos_start:movimientos_start + movimientos_page_size]

    ingresos_count = sum(1 for m in movimientos if str(m.get('tipo_movimiento', '')).lower() == 'ingreso')
    ventas_count = sum(1 for m in movimientos if str(m.get('tipo_movimiento', '')).lower() == 'venta')
    prestamos_count = sum(1 for m in movimientos if str(m.get('tipo_movimiento', '')).lower() == 'prestamo')
    devoluciones_count = sum(1 for m in movimientos if str(m.get('tipo_movimiento', '')).lower() == 'devolucion')
    
    try:
        conn2 = get_db_connection()
        cursor2 = conn2.cursor()
        tipos_movimiento_db = get_all_tipos_movimiento(cursor2)
        report_usuarios = []
        cursor2.close()
        conn2.close()
        movimientos_tipos = [t.get('nombre') for t in tipos_movimiento_db]
    except Exception:
        movimientos_tipos = sorted({str(m.get('tipo_movimiento') or '').strip() for m in movimientos if str(m.get('tipo_movimiento') or '').strip()})
    
    movimientos_usuarios = sorted({str(m.get('usuario') or '').strip() for m in movimientos if str(m.get('usuario') or '').strip()})

    page_size = 10
    total_items = len(lista_repuestos)
    repuestos_activos, repuestos_inactivos = split_repuestos_por_estado(lista_repuestos)
    activos_total = len(repuestos_activos)
    inactivos_total = len(repuestos_inactivos)
    activos_total_pages = max(1, (activos_total + page_size - 1) // page_size)
    inactivos_total_pages = max(1, (inactivos_total + page_size - 1) // page_size)
    current_page = max(1, current_page)
    total_pages = max(activos_total_pages, inactivos_total_pages)
    activos_page = min(current_page, activos_total_pages)
    inactivos_page = min(current_page, inactivos_total_pages)

    stock_bajo = sum(1 for repuesto in lista_repuestos if repuesto.get('stock', 0) <= 5)

    # Filtrar repuestos para el reporte usando los filtros del reporte
    report_repuestos = lista_repuestos
    if (report_from_date or report_to_date or report_tipo_repuesto or report_marca
            or report_estado or report_ubicacion or report_search or report_stock_min
            or report_stock_max or report_precio_compra_min or report_precio_venta_max):
        filtered_report = []
        for repuesto in lista_repuestos:
            fecha_ingreso = repuesto.get('fecha_ingreso')
            if report_from_date:
                try:
                    fecha = str(fecha_ingreso).split(' ')[0] if fecha_ingreso else ''
                    if fecha and fecha < report_from_date:
                        continue
                except Exception:
                    pass
            if report_to_date:
                try:
                    fecha = str(fecha_ingreso).split(' ')[0] if fecha_ingreso else ''
                    if fecha and fecha > report_to_date:
                        continue
                except Exception:
                    pass
            if report_tipo_repuesto:
                if str(repuesto.get('tipo_repuesto') or '').lower() != str(report_tipo_repuesto).strip().lower():
                    continue
            if report_marca:
                if str(repuesto.get('marca') or '').lower() != str(report_marca).strip().lower():
                    continue
            if report_estado:
                if str(repuesto.get('estado_repuesto') or '').lower() != str(report_estado).strip().lower():
                    continue
            if report_ubicacion:
                if str(repuesto.get('ubicacion_bodega') or '').lower() != str(report_ubicacion).strip().lower():
                    continue
            if report_search:
                search_lower = report_search.lower()
                searchable = ' '.join([
                    str(repuesto.get('id_repuesto') or ''),
                    str(repuesto.get('codigo_repuesto') or ''),
                    str(repuesto.get('tipo_repuesto') or ''),
                    str(repuesto.get('marca') or ''),
                    str(repuesto.get('modelo') or ''),
                    str(repuesto.get('anio') or ''),
                    str(repuesto.get('color') or ''),
                    str(repuesto.get('lado') or ''),
                    str(repuesto.get('posicion') or ''),
                    str(repuesto.get('estado_repuesto') or ''),
                    str(repuesto.get('ubicacion_bodega') or ''),
                    str(repuesto.get('observaciones') or ''),
                ]).lower()
                if search_lower not in searchable:
                    continue
            if report_stock_min:
                try:
                    if (repuesto.get('stock') or 0) < int(report_stock_min):
                        continue
                except Exception:
                    pass
            if report_stock_max:
                try:
                    if (repuesto.get('stock') or 0) > int(report_stock_max):
                        continue
                except Exception:
                    pass
            if report_precio_compra_min:
                try:
                    if (repuesto.get('precio_compra') or 0) < float(report_precio_compra_min):
                        continue
                except Exception:
                    pass
            if report_precio_venta_max:
                try:
                    if (repuesto.get('precio_venta') or 0) > float(report_precio_venta_max):
                        continue
                except Exception:
                    pass
            filtered_report.append(repuesto)
        report_repuestos = filtered_report
    else:
        report_repuestos = lista_repuestos

    tipos_count = len(tipo_repuestos)
    vehiculos_origen_count = len({
        repuesto['vehiculo_origen']
        for repuesto in lista_repuestos
        if repuesto.get('vehiculo_origen') and repuesto['vehiculo_origen'] != '-'
    })
    total_valor_venta = sum(
        (float(repuesto.get('precio_venta') or 0) * float(repuesto.get('stock') or 0))
        for repuesto in lista_repuestos
    )

    activos_start = (activos_page - 1) * page_size
    inactivos_start = (inactivos_page - 1) * page_size
    repuestos_activos_paginados = repuestos_activos[activos_start:activos_start + page_size]
    repuestos_inactivos_paginados = repuestos_inactivos[inactivos_start:inactivos_start + page_size]

    sidebar_user = get_current_sidebar_user()
    return render_template(
        'inventario.html',
        repuestos=repuestos_activos_paginados,
        repuestos_activos=repuestos_activos_paginados,
        repuestos_inactivos=repuestos_inactivos_paginados,
        activos_total=activos_total,
        inactivos_total=inactivos_total,
        activos_page=activos_page,
        inactivos_page=inactivos_page,
        activos_total_pages=activos_total_pages,
        inactivos_total_pages=inactivos_total_pages,
        report_repuestos=report_repuestos,
        total_items=total_items,
        stock_bajo=stock_bajo,
        tipos_count=tipos_count,
        vehiculos_origen_count=vehiculos_origen_count,
        total_valor_venta=total_valor_venta,
        current_page=current_page,
        total_pages=total_pages,
        tipo_repuestos=tipo_repuestos,
        report_marcas=report_marcas,
        report_estados=report_estados,
        report_ubicaciones=report_ubicaciones,
        report_from_date=report_from_date,
        report_to_date=report_to_date,
        report_tipo_repuesto=report_tipo_repuesto,
        report_marca=report_marca,
        report_estado=report_estado,
        report_ubicacion=report_ubicacion,
        report_search=report_search,
        report_stock_min=report_stock_min,
        report_stock_max=report_stock_max,
        report_precio_compra_min=report_precio_compra_min,
        report_precio_venta_max=report_precio_venta_max,
        movimientos=movimientos_paginados,
        movimientos_total=movimientos_total,
        movimientos_page=movimientos_page,
        movimientos_total_pages=movimientos_total_pages,
        movimientos_tipos=movimientos_tipos,
        movimientos_usuarios=movimientos_usuarios,
        ingresos_count=ingresos_count,
        ventas_count=ventas_count,
        prestamos_count=prestamos_count,
        devoluciones_count=devoluciones_count,
        selected_tipo_id=selected_tipo_id,
        search_query=search_query,
        stock_bajo_filter=stock_bajo_filter,
        user_name=sidebar_user['name'],
        user_role=sidebar_user['role']
    )


@app.route('/inventario/list-data')
def inventario_list_data():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    if session.get('role') == 'Vendedor':
        return jsonify({'error': 'Forbidden'}), 403

    search_query = request.args.get('q', '').strip()
    selected_tipo_id = request.args.get('tipo_repuesto', '').strip()
    estado_filter = request.args.get('estado', 'todos').strip().casefold()
    stock_bajo_filter = request.args.get('stock_bajo', '').strip()
    page = request.args.get('page', 1, type=int)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        tipo_repuestos = fetch_catalog_for_candidates(cursor, ['tipo_repuesto']) or []

        repuesto_columns = get_table_columns(cursor, 'repuesto')
        photo_select = ', fotografia' if any(column.lower() == 'fotografia' for column in repuesto_columns) else ''
        estado_general_select = (
            ', id_estado_general'
            if any(column.lower() == 'id_estado_general' for column in repuesto_columns)
            else ', NULL AS id_estado_general'
        )
        transmision_select = (
            ', id_transmision'
            if any(column.lower() == 'id_transmision' for column in repuesto_columns)
            else ', NULL AS id_transmision'
        )
        base_sql = (
            "SELECT id_repuesto, codigo_repuesto, id_vehiculo_origen, id_tipo_repuesto, "
            "id_marca, id_modelo, id_anio, id_color, id_lado, id_posicion, id_estado_repuesto, "
            "id_ubicacion, precio_compra, precio_venta, observaciones, fecha_ingreso, "
            "id_usuario_registro, stock, stock_minimo, stock_maximo "
            f"{photo_select}{estado_general_select}{transmision_select} FROM repuesto"
        )
        params = []
        if selected_tipo_id:
            try:
                params.append(int(selected_tipo_id))
            except ValueError:
                params.append(selected_tipo_id)
            base_sql += " WHERE id_tipo_repuesto = ?"
        base_sql += " ORDER BY id_repuesto DESC"
        cursor.execute(base_sql, tuple(params))
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description]

        stock_col = 'stock' if 'stock' in cols else choose_numeric_column(cols)
        if not stock_col:
            stock_col = choose_numeric_column_by_sample(cols, rows) or 'stock'

        tipo_candidates = ['tipo_repuesto']
        marca_candidates = ['marca_vehiculo', 'marca']
        modelo_candidates = ['modelo_vehiculo', 'modelo']
        color_candidates = ['color']
        lado_candidates = ['lado_repuesto']
        posicion_candidates = ['posicion_repuesto']

        lista_repuestos = []
        for r in rows:
            data = dict(zip(cols, r))
            codigo = data.get('codigo_repuesto') or data.get('codigo') or data.get('codigo_repuesto')
            stock = data.get(stock_col) if stock_col in data else data.get('stock')
            if stock is None:
                stock = 0
            precio = data.get('precio_venta') or data.get('precio') or data.get('price') or 0.0
            rep_id = data.get('id_repuesto') or data.get('id')

            vehiculo_origen = get_display_name_by_id(cursor, ['vehiculo_origen'], data.get('id_vehiculo_origen')) or '-'
            tipo_repuesto = get_display_name_by_id(cursor, tipo_candidates, data.get('id_tipo_repuesto')) or '-'
            marca = get_display_name_by_id(cursor, marca_candidates, data.get('id_marca')) or '-'
            modelo = get_display_name_by_id(cursor, modelo_candidates, data.get('id_modelo')) or '-'
            anio = get_display_name_by_id(cursor, ['anio_vehiculo'], data.get('id_anio')) or data.get('id_anio') or '-'
            color = get_display_name_by_id(cursor, color_candidates, data.get('id_color')) or '-'
            lado = get_display_name_by_id(cursor, lado_candidates, data.get('id_lado')) or '-'
            posicion = get_display_name_by_id(cursor, posicion_candidates, data.get('id_posicion')) or '-'
            estado_repuesto_text = get_display_name_by_id(cursor, ['estado_repuesto'], data.get('id_estado_repuesto')) or '-'
            loc_id = data.get('id_ubicacion') or data.get('id_bodega')
            ubicacion_bodega = get_display_name_by_id(cursor, ['bodega', 'ubicacion', 'ubicacion_bodega'], loc_id) or '-'
            usuario_registro = get_user_full_name(cursor, data.get('id_usuario_registro')) or get_display_name_by_id(cursor, ['usuario'], data.get('id_usuario_registro')) or '-'
            estado_general = get_display_name_by_id(
                cursor, ['estado_general'], data.get('id_estado_general')
            ) or '-'
            transmision = get_display_name_by_id(cursor, ['transmision', 'transmision_vehiculo', 'tipo_transmision'], data.get('id_transmision')) or data.get('transmision') or data.get('transmision_repuesto') or '-'
            stock_minimo = data.get('stock_minimo') or data.get('min_stock') or data.get('stock_min') or data.get('stock_minimo') or ''
            stock_maximo = data.get('stock_maximo') or data.get('max_stock') or data.get('stock_max') or data.get('stock_maximo') or ''

            try:
                raw_stock = stock
                stock_val = parse_int_like(raw_stock)
            except Exception:
                stock_val = 0

            try:
                precio_val = float(precio) if precio is not None else 0.0
            except Exception:
                precio_val = 0.0

            try:
                precio_compra_val = float(data.get('precio_compra')) if data.get('precio_compra') is not None else 0.0
            except Exception:
                precio_compra_val = 0.0

            lista_repuestos.append({
                'id_repuesto': rep_id,
                'codigo_repuesto': codigo,
                'vehiculo_origen': vehiculo_origen,
                'tipo_repuesto': tipo_repuesto,
                'marca': marca,
                'modelo': modelo,
                'anio': anio,
                'color': color,
                'lado': lado,
                'posicion': posicion,
                'estado_repuesto': estado_repuesto_text,
                'ubicacion_bodega': ubicacion_bodega,
                'precio_compra': precio_compra_val,
                'precio_venta': precio_val,
                'observaciones': data.get('observaciones'),
                'fotografia': data.get('fotografia'),
                'fecha_ingreso': data.get('fecha_ingreso'),
                'usuario_registro': usuario_registro,
                'estado': estado_general,
                'transmision': transmision,
                'stock_minimo': stock_minimo,
                'stock_maximo': stock_maximo,
                'stock': stock_val
            })

        cursor.close()
        conn.close()

        if search_query:
            search_fields = [
                'id_repuesto',
                'codigo_repuesto',
                'vehiculo_origen',
                'marca',
                'modelo',
                'anio',
                'color',
                'lado',
                'posicion',
                'estado_repuesto',
                'ubicacion_bodega',
                'precio_compra',
                'precio_venta',
                'observaciones',
                'fecha_ingreso',
                'usuario_registro',
                'estado',
                'stock',
                'stock_minimo',
                'stock_maximo'
            ]
            lista_repuestos = [r for r in lista_repuestos if matches_multiword_search(search_query, r, search_fields)]

        if stock_bajo_filter:
            lista_repuestos = [r for r in lista_repuestos if r.get('stock', 0) <= (r.get('stock_minimo') or 5)]

        if estado_filter == 'activo':
            lista_repuestos = [
                repuesto for repuesto in lista_repuestos
                if str(repuesto.get('estado') or '').strip().casefold() != 'inactivo'
            ]
        elif estado_filter == 'inactivo':
            lista_repuestos = [
                repuesto for repuesto in lista_repuestos
                if str(repuesto.get('estado') or '').strip().casefold() == 'inactivo'
            ]

    except Exception as e:
        print(f"\n⚠️ ERROR EN CONSULTA DE INVENTARIO LIST DATA: {e}\n")
        lista_repuestos = []
        tipo_repuestos = []

    page_size = 10
    total_items = len(lista_repuestos)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    repuestos_paginados = lista_repuestos[start_index:end_index]

    tipo_repuestos_json = [{'id': t[0], 'nombre': t[1]} for t in tipo_repuestos]

    return jsonify({
        'repuestos': repuestos_paginados,
        'total_items': total_items,
        'total_pages': total_pages,
        'current_page': page,
        'tipo_repuestos': tipo_repuestos_json,
        'selected_tipo_id': selected_tipo_id,
        'search_query': search_query,
        'stock_bajo_filter': stock_bajo_filter,
    })


@app.route('/inventario/agregar', methods=['GET', 'POST'])
def agregar_repuesto():
    if 'user' not in session:
        return redirect(url_for('login'))

    role = session.get('role')
    if role == 'Vendedor':
        flash('No tienes permisos para agregar repuestos.', 'error')
        return redirect(url_for('inventario'))

    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == 'POST':
        try:
            codigo_repuesto = request.form.get('codigo_repuesto')
            id_vehiculo_origen = request.form.get('id_vehiculo_origen')
            id_tipo_repuesto = request.form.get('id_tipo_repuesto')
            id_marca = request.form.get('id_marca')
            id_modelo = request.form.get('id_modelo')
            id_color = request.form.get('id_color') or None
            id_lado = request.form.get('id_lado') or None

            if id_modelo and not id_marca:
                flash('Seleccione primero la marca antes de elegir un modelo.', 'error')
                return redirect(url_for('agregar_repuesto'))

            if id_modelo and id_marca:
                cursor.execute(
                    "SELECT COUNT(1) FROM modelo_vehiculo WHERE id_modelo = ? AND id_marca = ?",
                    (id_modelo, id_marca)
                )
                row = cursor.fetchone()
                if not row or row[0] == 0:
                    flash('El modelo seleccionado no corresponde a la marca elegida.', 'error')
                    return redirect(url_for('agregar_repuesto'))
            id_posicion = request.form.get('id_posicion') or None
            stock_minimo = request.form.get('stock_minimo') or None
            stock_maximo = request.form.get('stock_maximo')
            if stock_maximo == '':
                stock_maximo = None
            if stock_maximo is None:
                flash('El campo Stock máximo es obligatorio.', 'error')
                return redirect(url_for('agregar_repuesto'))
            id_anio = request.form.get('id_anio')
            id_estado_repuesto = request.form.get('id_estado_repuesto')
            id_estado_general = get_estado_general_id(cursor, activo=True)
            # La ubicación se define por el tipo para evitar asignaciones
            # manuales inconsistentes desde el formulario.
            id_ubicacion = get_tipo_ubicacion(cursor, id_tipo_repuesto)
            id_bodega = id_ubicacion
            precio_compra = request.form.get('precio_compra')
            precio_venta = request.form.get('precio_venta')
            stock = request.form.get('stock')
            negative_field = negative_form_number_label({
                'Stock mínimo': stock_minimo,
                'Stock máximo': stock_maximo,
                'Precio de compra': precio_compra,
                'Precio de venta': precio_venta,
                'Stock': stock,
            })
            if negative_field:
                flash(f'{negative_field} no puede ser un número negativo.', 'error')
                return redirect(url_for('agregar_repuesto'))
            observaciones = request.form.get('observaciones') or None
            fotografia = None
            photo_file = get_uploaded_repuesto_photo()
            if photo_file and photo_file.filename:
                ensure_repuesto_photo_column(cursor)
                fotografia = save_repuesto_photo(photo_file)
            id_estado = 1  # Estado inicial por defecto (ej: Disponible)
            id_usuario_registro = session.get('user_id')
            fecha_ingreso = __import__('datetime').datetime.now()
            # Construimos dict con los posibles campos; insert_row_filtered filtrará los que no existan
            insert_data = {
                'codigo_repuesto': codigo_repuesto,
                'id_vehiculo_origen': id_vehiculo_origen,
                'id_tipo_repuesto': id_tipo_repuesto,
                'id_marca': id_marca,
                'id_modelo': id_modelo,
                'id_color': id_color,
                'id_lado': id_lado,
                'id_posicion': id_posicion,
                'id_transmision': request.form.get('id_transmision') or None,
                'stock_minimo': stock_minimo,
                'stock_maximo': stock_maximo,
                'id_anio': id_anio,
                'id_estado_repuesto': id_estado_repuesto,
                # Some schemas use 'id_ubicacion', others 'id_bodega' — include both so insert_row_filtered picks the correct one
                'id_ubicacion': id_ubicacion,
                'id_bodega': id_bodega,
                'precio_compra': precio_compra,
                'precio_venta': precio_venta,
                'stock': stock,
                'observaciones': observaciones,
                'fotografia': fotografia,
                'fecha_ingreso': fecha_ingreso,
                'id_usuario_registro': id_usuario_registro,
                'id_estado': id_estado,
                'id_estado_general': id_estado_general,
            }

            rowcount = insert_row_filtered(cursor, 'repuesto', insert_data)
            if rowcount is None or rowcount <= 0:
                raise RuntimeError(
                    'La base de datos no confirmó la inserción del repuesto.'
                )
            
            repuesto_id = None
            try:
                cursor.execute(
                    "SELECT TOP 1 id_repuesto FROM repuesto WHERE codigo_repuesto = ? ORDER BY id_repuesto DESC",
                    (codigo_repuesto,)
                )
                row = cursor.fetchone()
                if row:
                    repuesto_id = row[0]
            except Exception:
                repuesto_id = None

            if repuesto_id is not None:
                try:
                    cursor.execute(
                        'INSERT INTO [dbo].[bitacora_repuesto] '
                        '([id_repuesto], [codigo_repuesto], [accion], [precio_anterior], [precio_nuevo], '
                        '[stock_anterior], [stock_nuevo], [detalles], [id_usuario]) '
                        "VALUES (?, ?, 'CREACION', NULL, ?, NULL, ?, ?, ?)",
                        (repuesto_id, codigo_repuesto, precio_venta, parse_int_like(stock) or 0,
                         f"Alta de repuesto; precio nuevo: {precio_venta}; stock nuevo: {parse_int_like(stock) or 0}",
                         session.get('user_id')),
                    )
                    registrar_movimiento_inventario(
                        cursor,
                        repuesto_id,
                        1,
                        parse_int_like(stock) or 0,
                        'Ingreso inicial del repuesto',
                        session.get('user_id'),
                        1,
                        fecha_ingreso,
                    )
                    # La BD puede crear un movimiento automático al insertar el
                    # repuesto. El alta ya registra el ingreso inicial con el
                    # stock real, por lo que ese movimiento extra no debe quedar
                    # en el historial.
                    cursor.execute(
                        "DELETE FROM movimiento_inventario "
                        "WHERE id_repuesto = ? AND detalle = ?",
                        (repuesto_id, 'Ingreso automático de repuesto'),
                    )
                except Exception:
                    raise

            conn.commit()

            flash('¡Repuesto guardado exitosamente en el inventario!', 'success')
            return redirect(url_for('inventario'))

        except Exception as e:
            # Diagnóstico temporal: dejar el error de SQL Server en los logs.
            print(f'❌ ERROR REAL DE RDS AL INSERTAR REPUESTO: {e}')
            conn.rollback()
            flash(f'⚠️ ERROR DE BASE DE DATOS EN AWS: {str(e)}', 'danger')
            return redirect(url_for('inventario'))
        
        finally:
            cursor.close()
            conn.close()

    else:
        # MÉTODO GET: Mapeado exactamente con las columnas reales de tus tablas
        try:
                # Use dynamic catalog fetcher with candidate table names to avoid column-name mismatches
                vehiculo_origen_candidates = ['vehiculo_origen']
                vehiculos_origen = fetch_catalog_for_candidates(cursor, vehiculo_origen_candidates) or []

                tipos = fetch_tipos_repuesto(cursor)

                transmision_candidates = ['transmision', 'transmision_vehiculo', 'tipo_transmision']
                transmisiones = fetch_catalog_for_candidates(cursor, transmision_candidates) or []

                marca_candidates = ['marca', 'marca_vehiculo']
                marcas = fetch_catalog_for_candidates(cursor, marca_candidates) or []

                modelos = []

                anio_candidates = ['anio_vehiculo']
                anios = fetch_catalog_for_candidates(cursor, anio_candidates) or []

                color_candidates = ['color', 'color_repuesto']
                colores = fetch_catalog_for_candidates(cursor, color_candidates) or []

                lado_candidates = ['lado', 'lado_repuesto']
                lados = fetch_catalog_for_candidates(cursor, lado_candidates) or []

                posicion_candidates = ['posicion', 'posicion_repuesto']
                posiciones = fetch_catalog_for_candidates(cursor, posicion_candidates) or []

                estado_repuesto_candidates = ['estado_repuesto']
                estados_repuesto = fetch_catalog_for_candidates(cursor, estado_repuesto_candidates) or []

                bodega_candidates = ['ubicacion', 'bodega']
                bodegas = fetch_catalog_for_candidates(cursor, bodega_candidates) or []

                repuestos_registrados = get_repuestos_registrados(cursor)
                return render_template('agregar_repuesto.html', 
                                       vehiculos_origen=vehiculos_origen,
                                       tipos=tipos, 
                                       transmisiones=transmisiones,
                                       marcas=marcas, 
                                       modelos=modelos, 
                                       anios=anios,
                                       colores=colores, 
                                       lados=lados, 
                                       posiciones=posiciones,
                                       estados_repuesto=estados_repuesto,
                                       bodegas=bodegas,
                                       repuestos_registrados=repuestos_registrados)
        
        except Exception as e:
            flash(f'Error al cargar los catálogos: {str(e)}', 'error')
            return render_template('agregar_repuesto.html', tipos=[], transmisiones=[], marcas=[], modelos=[], anios=[], colores=[], lados=[], posiciones=[], estados_repuesto=[], bodegas=[], vehiculos_origen=[], repuestos_registrados=[])
        
        finally:
            cursor.close()
            conn.close()

@app.route('/api/modelos')
def api_modelos():
    id_marca = request.args.get('marca_id')
    if not id_marca:
        return jsonify([])

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        modelos = fetch_modelos_by_marca(cursor, id_marca) or []
        return jsonify([{'id': m.id, 'nombre': m.nombre} for m in modelos])
    except Exception:
        return jsonify([])
    finally:
        cursor.close()
        conn.close()

@app.route('/inventario/modificar/<int:id_repuesto>', methods=['GET', 'POST'])
def modificar_repuesto(id_repuesto):
    if 'user' not in session:
        return redirect(url_for('login'))

    role = session.get('role')
    if role == 'Vendedor':
        flash('No tienes permisos para modificar repuestos.', 'error')
        return redirect(url_for('inventario'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if request.method == 'POST':
            cursor.execute(
                'SELECT codigo_repuesto, precio_venta, stock FROM repuesto WHERE id_repuesto = ?',
                (id_repuesto,),
            )
            old_values = cursor.fetchone()
            if not old_values:
                flash('No se encontró el repuesto para modificar.', 'error')
                return redirect(url_for('inventario'))
            stock_maximo = request.form.get('stock_maximo')
            if stock_maximo == '':
                stock_maximo = None
            if stock_maximo is None:
                flash('El campo Stock máximo es obligatorio.', 'error')
                return redirect(url_for('modificar_repuesto', id_repuesto=id_repuesto))
            negative_field = negative_form_number_label({
                'Stock mínimo': request.form.get('stock_minimo'),
                'Stock máximo': stock_maximo,
                'Precio de compra': request.form.get('precio_compra'),
                'Precio de venta': request.form.get('precio_venta'),
                'Stock': request.form.get('stock'),
            })
            if negative_field:
                flash(f'{negative_field} no puede ser un número negativo.', 'error')
                return redirect(url_for('modificar_repuesto', id_repuesto=id_repuesto))
            update_data = {
                'codigo_repuesto': request.form.get('codigo_repuesto'),
                'id_vehiculo_origen': request.form.get('id_vehiculo_origen'),
                'id_tipo_repuesto': request.form.get('id_tipo_repuesto'),
                'id_marca': request.form.get('id_marca'),
                'id_modelo': request.form.get('id_modelo'),
                'id_color': request.form.get('id_color') or None,
                'id_lado': request.form.get('id_lado') or None,
                'id_posicion': request.form.get('id_posicion') or None,
                'id_transmision': request.form.get('id_transmision') or None,
                'stock_minimo': request.form.get('stock_minimo') or None,
                'stock_maximo': stock_maximo,
                'id_anio': request.form.get('id_anio'),
                'id_estado_repuesto': request.form.get('id_estado_repuesto'),
                # Siempre se conserva la ubicación configurada en el tipo.
                'id_ubicacion': get_tipo_ubicacion(cursor, request.form.get('id_tipo_repuesto')),
                'id_bodega': get_tipo_ubicacion(cursor, request.form.get('id_tipo_repuesto')),
                'precio_compra': request.form.get('precio_compra'),
                'precio_venta': request.form.get('precio_venta'),
                'stock': request.form.get('stock'),
                'observaciones': request.form.get('observaciones') or None,
                'id_estado_general': get_estado_general_id(
                    cursor, activo=request.form.get('activo') == 'on'
                ),
            }
            photo_file = get_uploaded_repuesto_photo()
            if photo_file and photo_file.filename:
                ensure_repuesto_photo_column(cursor)
                update_data['fotografia'] = save_repuesto_photo(photo_file)
            elif request.form.get('eliminar_fotografia') == '1':
                ensure_repuesto_photo_column(cursor)
                update_data['fotografia'] = None
            columns = get_table_columns(cursor, 'repuesto')
            set_cols = [col for col in update_data if col in columns]
            if set_cols:
                set_clause = ', '.join([f"{col} = ?" for col in set_cols])
                params = [update_data[col] for col in set_cols] + [id_repuesto]
                cursor.execute(f"UPDATE repuesto SET {set_clause} WHERE id_repuesto = ?", tuple(params))
                cursor.execute(
                    'INSERT INTO [dbo].[bitacora_repuesto] '
                    '([id_repuesto], [codigo_repuesto], [accion], [precio_anterior], [precio_nuevo], '
                    '[stock_anterior], [stock_nuevo], [detalles], [id_usuario]) '
                    "VALUES (?, ?, 'MODIFICACION', ?, ?, ?, ?, ?, ?)",
                    (
                        id_repuesto, update_data.get('codigo_repuesto') or old_values[0],
                        old_values[1], update_data.get('precio_venta'), old_values[2],
                        update_data.get('stock'),
                        f"Precio: {old_values[1]} -> {update_data.get('precio_venta')}; "
                        f"stock: {old_values[2]} -> {update_data.get('stock')}",
                        session.get('user_id'),
                    ),
                )
                conn.commit()
                flash('Repuesto actualizado correctamente.', 'success')
            else:
                flash('No se pudo actualizar el repuesto: columnas no válidas.', 'error')
            return redirect(url_for('inventario'))

        cursor.execute("SELECT * FROM repuesto WHERE id_repuesto = ?", (id_repuesto,))
        row = cursor.fetchone()
        if not row:
            flash('No se encontró el repuesto para modificar.', 'error')
            return redirect(url_for('inventario'))

        cols = [c[0] for c in cursor.description]
        repuesto = dict(zip(cols, row))

        vehiculo_origen_candidates = ['vehiculo_origen']
        # En edición incluir orígenes inactivos también, para que un valor ya
        # guardado siga disponible y seleccionado en el formulario.
        vehiculos_origen = fetch_catalog_for_candidates(
            cursor, vehiculo_origen_candidates, include_inactive=True
        ) or []

        tipos = fetch_tipos_repuesto(cursor, only_active=False)

        transmision_candidates = ['transmision', 'transmision_vehiculo', 'tipo_transmision']
        transmisiones = fetch_catalog_for_candidates(cursor, transmision_candidates) or []

        marca_candidates = ['marca', 'marca_vehiculo']
        marcas = fetch_catalog_for_candidates(cursor, marca_candidates) or []

        modelo_candidates = ['modelo', 'modelo_vehiculo']
        modelos = fetch_catalog_for_candidates(cursor, modelo_candidates) or []

        anio_candidates = ['anio_vehiculo']
        anios = fetch_catalog_for_candidates(cursor, anio_candidates) or []

        color_candidates = ['color', 'color_repuesto']
        colores = fetch_catalog_for_candidates(cursor, color_candidates) or []

        lado_candidates = ['lado', 'lado_repuesto']
        lados = fetch_catalog_for_candidates(cursor, lado_candidates) or []

        posicion_candidates = ['posicion', 'posicion_repuesto']
        posiciones = fetch_catalog_for_candidates(cursor, posicion_candidates) or []

        estado_activo_id = get_estado_general_id(cursor, activo=True)

        estado_repuesto_candidates = ['estado_repuesto']
        estados_repuesto = fetch_catalog_for_candidates(cursor, estado_repuesto_candidates) or []

        bodega_candidates = ['ubicacion', 'bodega']
        bodegas = fetch_catalog_for_candidates(cursor, bodega_candidates) or []

        repuestos_registrados = get_repuestos_registrados(cursor)
        return render_template('modificar_repuesto.html', 
                               repuesto=repuesto,
                               vehiculos_origen=vehiculos_origen,
                               tipos=tipos, 
                               transmisiones=transmisiones,
                               marcas=marcas, 
                               modelos=modelos, 
                               anios=anios,
                               colores=colores, 
                               lados=lados, 
                               posiciones=posiciones,
                               estado_activo_id=estado_activo_id,
                               estados_repuesto=estados_repuesto,
                               bodegas=bodegas,
                               repuestos_registrados=repuestos_registrados)
    except Exception as e:
        flash(f'Error al cargar el repuesto: {str(e)}', 'error')
        return redirect(url_for('inventario'))
    finally:
        cursor.close()
        conn.close()

@app.route('/inventario/eliminar/<int:id_repuesto>', methods=['GET', 'POST'])
def eliminar_repuesto(id_repuesto):
    if 'user' not in session:
        return redirect(url_for('login'))

    role = session.get('role')
    if role == 'Vendedor':
        flash('No tienes permisos para eliminar repuestos.', 'error')
        return redirect(url_for('inventario'))

    if request.method not in ('POST', 'GET'):
        return redirect(url_for('inventario'))

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Primero eliminar los movimientos de inventario relacionados
        cursor.execute("DELETE FROM movimiento_inventario WHERE id_repuesto = ?", (id_repuesto,))
        
        # Luego eliminar el repuesto
        cursor.execute("DELETE FROM repuesto WHERE id_repuesto = ?", (id_repuesto,))
        deleted_rows = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()

        if deleted_rows > 0:
            flash('Repuesto borrado con éxito', 'success')
        else:
            flash('No se encontró el repuesto para eliminar.', 'error')
    except Exception as e:
        flash(f'No se pudo eliminar el repuesto: {str(e)}', 'error')

    return redirect(url_for('inventario'))

def cliente_field(columns, *names):
    """Encuentra una columna de cliente sin depender de una versión del esquema."""
    indexed = {column.lower(): column for column in columns}
    return next((indexed[name] for name in names if name in indexed), None)


def cliente_display_from_row(data):
    parts = [data.get(key) for key in (
        'primer_nombre', 'segundo_nombre', 'primer_apellido', 'segundo_apellido'
    ) if data.get(key)]
    return ' '.join(str(part).strip() for part in parts) or (
        data.get('nombre_completo') or data.get('nombre_cliente') or data.get('nombre')
        or f"Cliente #{data.get('id_cliente', '')}"
    )


def _clientes_activos(cursor):
    columns = get_table_columns(cursor, 'cliente')
    id_column = cliente_field(columns, 'id_cliente') or next(
        (column for column in columns if column.lower().startswith('id')), None
    )
    if not id_column:
        raise ValueError('La tabla cliente no tiene una columna identificadora.')

    status_column = cliente_field(columns, 'id_estado_general', 'id_estado')
    params = ()
    status_filter = ''
    if status_column:
        status_filter = f' WHERE [{status_column}] = ?'
        params = (get_estado_general_id(cursor, activo=True),)

    cursor.execute(
        f'SELECT * FROM cliente{status_filter} ORDER BY [{id_column}]',
        params,
    )
    column_names = [column[0] for column in cursor.description]
    return [
        dict(zip(column_names, row))
        for row in cursor.fetchall()
    ]


def _cliente_esta_activo(cursor, client_id):
    if client_id is None:
        return True

    columns = get_table_columns(cursor, 'cliente')
    id_column = cliente_field(columns, 'id_cliente') or next(
        (column for column in columns if column.lower().startswith('id')), None
    )
    if not id_column:
        raise ValueError('La tabla cliente no tiene una columna identificadora.')

    status_column = cliente_field(columns, 'id_estado_general', 'id_estado')
    if not status_column:
        return True

    active_status_id = get_estado_general_id(cursor, activo=True)
    cursor.execute(
        f'SELECT 1 FROM cliente WHERE [{id_column}] = ? AND [{status_column}] = ?',
        (client_id, active_status_id),
    )
    return cursor.fetchone() is not None


@app.route('/clientes', methods=['GET', 'POST'])
def clientes():
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        columns = get_table_columns(cursor, 'cliente')
        id_column = cliente_field(columns, 'id_cliente') or next(
            (column for column in columns if column.lower().startswith('id')), None
        )
        if not id_column:
            raise ValueError('La tabla cliente no tiene una columna identificadora.')

        editable = [
            column for column in columns
            if column.lower() in {
                'primer_nombre', 'segundo_nombre', 'primer_apellido', 'segundo_apellido',
                'nombre_cliente', 'nombre_completo', 'nombre', 'nit', 'telefono',
                'correo', 'email', 'direccion'
            }
        ]
        status_column = cliente_field(columns, 'id_estado_general', 'id_estado')

        if request.method == 'POST':
            action = request.form.get('action')
            client_id = request.form.get('id_cliente')
            if action == 'delete' and client_id:
                cursor.execute(f"DELETE FROM cliente WHERE [{id_column}] = ?", (client_id,))
                conn.commit()
                flash('Cliente eliminado correctamente.', 'success')
            else:
                values = {column: request.form.get(column, '').strip() or None for column in editable}
                if status_column:
                    values[status_column] = get_estado_general_id(
                        cursor,
                        activo=action == 'create' or request.form.get('activo') == 'on',
                    )
                usable = {key: value for key, value in values.items() if key in columns}
                if action == 'edit' and client_id:
                    assignments = ', '.join(f'[{key}] = ?' for key in usable)
                    cursor.execute(
                        f"UPDATE cliente SET {assignments} WHERE [{id_column}] = ?",
                        tuple(usable.values()) + (client_id,)
                    )
                    flash('Cliente actualizado correctamente.', 'success')
                elif action == 'create':
                    if not usable:
                        raise ValueError('No se encontraron campos de cliente para registrar.')
                    cursor.execute(
                        f"INSERT INTO cliente ({', '.join(f'[{key}]' for key in usable)}) "
                        f"VALUES ({', '.join('?' for _ in usable)})",
                        tuple(usable.values())
                    )
                    flash('Cliente registrado correctamente.', 'success')
                conn.commit()
            return redirect(url_for('clientes'))

        cursor.execute(f"SELECT * FROM cliente ORDER BY [{id_column}] DESC")
        rows = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
        active_status_id = (
            get_estado_general_id(cursor, activo=True) if status_column else None
        )
        client_data = []
        for data in rows:
            client_data.append({
                'id': data.get(id_column),
                'nombre': cliente_display_from_row(data),
                'nit': data.get(cliente_field(columns, 'nit')) or '-',
                'telefono': data.get(cliente_field(columns, 'telefono', 'celular')) or '-',
                'correo': data.get(cliente_field(columns, 'correo', 'email')) or '-',
                'direccion': data.get(cliente_field(columns, 'direccion')) or '-',
                'fecha': data.get(cliente_field(columns, 'fecha_registro', 'fecha_creacion')),
                'activo': (
                    str(data.get(status_column)) == str(active_status_id)
                    if status_column else True
                ),
                'data': data,
            })
        total_clientes = len(client_data)
        try:
            current_page = max(1, int(request.args.get('page', 1)))
        except (TypeError, ValueError):
            current_page = 1
        page_size = 10
        total_pages = max(1, (total_clientes + page_size - 1) // page_size)
        current_page = min(current_page, total_pages)
        start = (current_page - 1) * page_size
        return render_template(
            'clientes.html', clientes=client_data[start:start + page_size],
            report_clientes=client_data,
            total_clientes=total_clientes, current_page=current_page,
            total_pages=total_pages, editable=editable,
            has_client_status=bool(status_column), active_status_id=active_status_id,
        )
    except Exception as e:
        flash(f'No se pudo cargar clientes: {str(e)}', 'error')
        return render_template('clientes.html', clientes=[], report_clientes=[], total_clientes=0,
                       current_page=1, total_pages=1, editable=[],
                       has_client_status=False, active_status_id=1)
    finally:
        cursor.close()
        conn.close()


@app.route('/clientes/reporte-pdf')
def exportar_pdf_clientes():
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        columns = get_table_columns(cursor, 'cliente')
        id_column = cliente_field(columns, 'id_cliente') or next(
            (column for column in columns if column.lower().startswith('id')), None
        )
        if not id_column:
            raise ValueError('No se encontró el identificador de clientes.')

        query = request.args.get('q', '').strip()
        from_date = request.args.get('from_date', '').strip()
        to_date = request.args.get('to_date', '').strip()
        estado = request.args.get('estado', '').strip().lower()

        cursor.execute(f"SELECT * FROM cliente ORDER BY [{id_column}]")
        records = [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]
        rows = []
        for data in records:
            nombre = cliente_display_from_row(data)
            nit = data.get(cliente_field(columns, 'nit')) or '-'
            telefono = data.get(cliente_field(columns, 'telefono', 'celular')) or '-'
            correo = data.get(cliente_field(columns, 'correo', 'email')) or '-'
            direccion = data.get(cliente_field(columns, 'direccion')) or '-'
            search_fields = ['nombre', 'nit', 'telefono', 'correo']
            searchable_record = {
                'nombre': nombre,
                'nit': nit,
                'telefono': telefono,
                'correo': correo
            }
            if query and not matches_multiword_search(query, searchable_record, search_fields):
                continue
            # Filter by estado
            cliente_activo = data.get(cliente_field(columns, 'activo')) or data.get('id_estado_general') == 1
            estado_str = 'activo' if cliente_activo else 'inactivo'
            if estado and estado != estado_str:
                continue
            # Filter by date
            fecha_registro = data.get('fecha_registro') or data.get('fecha')
            if from_date:
                try:
                    fecha = str(fecha_registro).split(' ')[0] if fecha_registro else ''
                    if fecha and fecha < from_date:
                        continue
                except Exception:
                    pass
            if to_date:
                try:
                    fecha = str(fecha_registro).split(' ')[0] if fecha_registro else ''
                    if fecha and fecha > to_date:
                        continue
                except Exception:
                    pass
            rows.append([nombre, nit, telefono, correo, direccion, estado_str])

        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=letter)
        pdf.setTitle('Reporte de Clientes - Importadora Morales')
        draw_table_pdf(
            pdf, 'Reporte de Clientes — Importadora Morales',
            ['Nombre completo', 'NIT', 'Telefono', 'Correo', 'Direccion', 'Estado'], rows
        )
        pdf.showPage()
        pdf.save()
        buffer.seek(0)
        return send_file(
            buffer, as_attachment=True, download_name='reporte_clientes.pdf',
            mimetype='application/pdf'
        )
    except Exception as e:
        flash(f'No se pudo generar el reporte de clientes: {str(e)}', 'error')
        return redirect(url_for('clientes'))
    finally:
        cursor.close()
        conn.close()


@app.route('/ventas')
def ventas():
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    ventas = []
    total_items = 0

    try:
        repuesto_display_col = 'codigo_repuesto'
        repuesto_cols = get_table_columns(cursor, 'repuesto')
        if repuesto_cols:
            detected = choose_display_column(repuesto_cols)
            if detected:
                repuesto_display_col = detected

        cursor.execute(
            "SELECT id_venta, fecha_venta, id_cliente, id_usuario, total, "
            "observaciones, id_metodo_pago, descuento, id_estado_venta "
            "FROM venta ORDER BY fecha_venta DESC"
        )
        sale_rows = cursor.fetchall()
        sale_cols = [c[0] for c in cursor.description]

        cursor.execute(
            f"SELECT dv.id_venta, dv.id_repuesto, dv.precio_unitario, "
            f"dv.subtotal, dv.cantidad, r.{repuesto_display_col} AS repuesto_nombre, "
            "COALESCE((SELECT SUM(dd.cantidad) FROM detalle_devolucion dd "
            "INNER JOIN devolucion d ON d.id_devolucion = dd.id_devolucion "
            "WHERE d.id_venta = dv.id_venta AND dd.id_repuesto = dv.id_repuesto), 0) AS cantidad_devuelta "
            "FROM detalle_venta dv "
            "LEFT JOIN repuesto r ON r.id_repuesto = dv.id_repuesto"
        )
        detail_rows = cursor.fetchall()
        detail_cols = [c[0] for c in cursor.description]
        detalles_por_venta = {}
        for detail_row in detail_rows:
            detail = dict(zip(detail_cols, detail_row))
            id_venta_detalle = detail.get('id_venta')
            id_repuesto = detail.get('id_repuesto')
            if id_venta_detalle is None or id_repuesto is None:
                continue
            cantidad = int(detail.get('cantidad') or 0)
            cantidad_devuelta = int(detail.get('cantidad_devuelta') or 0)
            if cantidad < 0:
                cantidad += cantidad_devuelta * 2
                detail['cantidad'] = cantidad
                detail['subtotal'] = cantidad * float(detail.get('precio_unitario') or 0)
            detalles_por_venta.setdefault(id_venta_detalle, []).append({
                'codigo': id_repuesto,
                'repuesto': get_repuesto_display_name(cursor, id_repuesto) or detail.get('repuesto_nombre') or str(id_repuesto),
                'anio': get_repuesto_year(cursor, id_repuesto),
                'precio_unitario': detail.get('precio_unitario') or 0.0,
                'subtotal': detail.get('subtotal') or 0.0,
                'cantidad': detail.get('cantidad') or 0,
                'cantidad_devuelta': cantidad_devuelta,
            })

        ventas_por_id = {}
        for row in sale_rows:
            data = dict(zip(sale_cols, row))
            fecha_venta = data.get('fecha_venta') or data.get('fecha') or data.get('fecha_registro') or '-'
            raw_cliente = data.get('id_cliente') or data.get('cliente')
            raw_usuario = data.get('id_usuario') or data.get('usuario')
            raw_metodo_pago = data.get('id_metodo_pago')
            cliente = get_cliente_full_name(cursor, data.get('id_cliente')) or raw_cliente or 'Cliente no frecuente'
            usuario = get_user_full_name(cursor, data.get('id_usuario')) or lookup_foreign_key_display(cursor, 'id_usuario', data.get('id_usuario'), ['usuario']) or raw_usuario or '-'
            metodo_pago = lookup_foreign_key_display(cursor, 'id_metodo_pago', raw_metodo_pago, ['metodo_pago', 'forma_pago']) or raw_metodo_pago or '-'
            id_venta = data.get('id_venta')
            if id_venta not in ventas_por_id:
                ventas_por_id[id_venta] = {
                    'id_venta': id_venta,
                    'fecha_venta': fecha_venta,
                    'cliente': cliente,
                    'usuario': usuario,
                    'total': data.get('total') or 0.0,
                    'descuento': data.get('descuento') or 0.0,
                    'observaciones': data.get('observaciones') or '-',
                    'metodo_pago': metodo_pago,
                    'id_estado_venta': int(data.get('id_estado_venta') or 1),
                    'estado': 'Devuelta' if int(data.get('id_estado_venta') or 1) == 2 else 'Completada',
                    'items': detalles_por_venta.get(id_venta, []),
                }

            # Una venta puede tener varios repuestos. Se agrupan para mostrar una
            # sola fila principal y sus artículos en el detalle desplegable.
            # id_repuesto es la relación que confirma que hay un artículo en
            # detalle_venta; no dependemos de que el ID autoincremental llegue
            # con un nombre específico desde SQL Server.
            if False:  # El detalle ya se cargó directamente en detalles_por_venta.
                ventas_por_id[id_venta]['items'].append({
                    'codigo': data.get('id_repuesto') or '-',
                    'repuesto': repuesto,
                    'precio_unitario': data.get('precio_unitario') or 0.0,
                    'subtotal': data.get('subtotal') or 0.0,
                    'cantidad': data.get('cantidad') or 0,
                })

        ventas = list(ventas_por_id.values())
        ingresos_totales = sum(float(venta['total'] or 0) for venta in ventas if venta['id_estado_venta'] == 1)
        hoy = datetime.now().date()
        ventas_hoy = sum(
            float(venta['total'] or 0) for venta in ventas
            if venta['id_estado_venta'] == 1
            and getattr(venta['fecha_venta'], 'date', lambda: venta['fecha_venta'])() == hoy
        )
        total_transacciones = len(ventas)
        report_ventas = get_ventas_reporte()

        total_items = len(ventas)
        try:
            current_page = int(request.args.get('page', 1))
        except (TypeError, ValueError):
            current_page = 1
        page_size = 10
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        if current_page < 1:
            current_page = 1
        if current_page > total_pages:
            current_page = total_pages

        start_index = (current_page - 1) * page_size
        end_index = start_index + page_size
        ventas = ventas[start_index:end_index]

        clientes = []
        metodos_pago = []
        repuestos = []
        show_edit_sale = request.args.get('modal') == 'editar'
        edit_sale = None
        if request.args.get('modal') in ('nueva', 'editar'):
            for data in _clientes_activos(cursor):
                clientes.append({
                    'id': data['id_cliente'],
                    'nombre': cliente_display_from_row(data),
                    'nit': data.get('nit') or '',
                    'correo': data.get('correo') or '',
                })
            cursor.execute('SELECT id_metodo_pago, metodo FROM metodo_pago ORDER BY id_metodo_pago')
            metodos_pago = [{'id': row[0], 'nombre': row[1]} for row in cursor.fetchall()]
            estado_activo_id = get_estado_general_id(cursor, activo=True)
            cursor.execute(
                'SELECT id_repuesto, codigo_repuesto, precio_venta, stock '
                'FROM repuesto WHERE stock > 0 AND id_estado_general = ? '
                'ORDER BY codigo_repuesto',
                (estado_activo_id,),
            )
            product_rows = cursor.fetchall()
            for row in product_rows:
                repuestos.append({
                    'id': row[0],
                    'codigo': row[1] or str(row[0]),
                    'nombre': get_repuesto_display_name(cursor, row[0]) or row[1] or str(row[0]),
                    'anio': get_repuesto_year(cursor, row[0]) or 'No registrado',
                    'precio': float(row[2] or 0),
                    'stock': row[3] or 0,
                })

        if show_edit_sale:
            id_venta_editar = request.args.get('id_venta')
            if id_venta_editar:
                cursor.execute(
                    'SELECT v.id_venta, v.fecha_venta, v.id_cliente, v.id_usuario, v.total, v.observaciones, v.id_metodo_pago, '
                    'u.username, u.primer_nombre, u.segundo_nombre, u.primer_apellido, u.segundo_apellido '
                    'FROM venta v LEFT JOIN usuario u ON u.id_usuario = v.id_usuario WHERE v.id_venta = ?',
                    (id_venta_editar,)
                )
                sale_row = cursor.fetchone()
                if sale_row:
                    sale_data = dict(zip([col[0] for col in cursor.description], sale_row))
                    cursor.execute(
                        'SELECT dv.id_repuesto, dv.cantidad, dv.precio_unitario, dv.subtotal, r.codigo_repuesto, r.precio_venta, r.stock '
                        'FROM detalle_venta dv LEFT JOIN repuesto r ON r.id_repuesto = dv.id_repuesto WHERE dv.id_venta = ?',
                        (id_venta_editar,)
                    )
                    detail_rows = cursor.fetchall()
                    detail_cols = [col[0] for col in cursor.description]
                    items = []
                    for item_row in detail_rows:
                        item_data = dict(zip(detail_cols, item_row))
                        items.append({
                            'id_repuesto': item_data.get('id_repuesto'),
                            'cantidad': item_data.get('cantidad') or 0,
                            'precio_unitario': float(item_data.get('precio_unitario') or 0),
                            'subtotal': float(item_data.get('subtotal') or 0),
                            'codigo': item_data.get('codigo_repuesto') or str(item_data.get('id_repuesto')),
                            'nombre': get_repuesto_display_name(cursor, item_data.get('id_repuesto')) or (item_data.get('codigo_repuesto') or str(item_data.get('id_repuesto'))),
                            'stock': item_data.get('stock') or 0,
                        })
                    usuario_nombre = (
                        get_user_full_name(cursor, sale_data.get('id_usuario'))
                        or lookup_foreign_key_display(cursor, 'id_usuario', sale_data.get('id_usuario'), ['usuario'])
                        or ' '.join(
                            part for part in [
                                sale_data.get('primer_nombre'),
                                sale_data.get('segundo_nombre'),
                                sale_data.get('primer_apellido'),
                                sale_data.get('segundo_apellido'),
                            ] if part
                        )
                        or sale_data.get('username')
                        or session.get('full_name', session.get('user', 'Usuario'))
                    )
                    edit_sale = {
                        'id_venta': sale_data.get('id_venta'),
                        'cliente_id': sale_data.get('id_cliente'),
                        'cliente_nombre': get_cliente_full_name(cursor, sale_data.get('id_cliente')) or 'Sin cliente frecuente',
                        'usuario': usuario_nombre,
                        'metodo_pago_id': sale_data.get('id_metodo_pago'),
                        'observaciones': sale_data.get('observaciones') or '',
                        'total': float(sale_data.get('total') or 0),
                        'items': items,
                    }

    except Exception as e:
        print(f"\n⚠️ ERROR EN CONSULTA DE VENTAS: {e}\n")
        flash(f'Error al cargar ventas: {str(e)}', 'error')
    finally:
        cursor.close()
        conn.close()

    return render_template(
        'ventas.html',
        ventas=ventas,
        total_items=total_items,
        current_page=current_page,
        total_pages=total_pages,
        ingresos_totales=ingresos_totales if 'ingresos_totales' in locals() else 0,
        ventas_hoy=ventas_hoy if 'ventas_hoy' in locals() else 0,
        total_transacciones=total_transacciones if 'total_transacciones' in locals() else 0,
        show_new_sale=request.args.get('modal') == 'nueva',
        show_edit_sale=show_edit_sale,
        edit_sale=edit_sale if 'edit_sale' in locals() else None,
        clientes=clientes if 'clientes' in locals() else [],
        metodos_pago=metodos_pago if 'metodos_pago' in locals() else [],
        repuestos=repuestos if 'repuestos' in locals() else [],
        report_ventas=report_ventas if 'report_ventas' in locals() else [],
    )


@app.route('/ventas/reporte-pdf')
def exportar_pdf_ventas():
    if 'user' not in session:
        return redirect(url_for('login'))

    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()
    cliente = request.args.get('cliente', '').strip()
    vendedor = request.args.get('vendedor', '').strip()
    repuesto = request.args.get('repuesto', '').strip()
    metodo_pago = request.args.get('metodo_pago', '').strip()
    estado = request.args.get('estado', '').strip()
    min_total = request.args.get('min_total', '').strip()
    max_total = request.args.get('max_total', '').strip()
    rows = filter_ventas_reporte(
        get_ventas_reporte(),
        from_date=from_date or None,
        to_date=to_date or None,
        cliente=cliente or None,
        vendedor=vendedor or None,
        repuesto=repuesto or None,
        metodo_pago=metodo_pago or None,
        estado=estado or None,
        min_total=float(min_total) if min_total else None,
        max_total=float(max_total) if max_total else None,
    )

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle('Reporte de Ventas - Importadora Morales')
    headers = ['N? Venta', 'Fecha', 'Cliente', 'Vendedor', 'Pago', 'Estado', 'Repuestos vendidos', 'Unidades', 'Total']
    pdf_rows = [
        [
            row['numero'],
            row['fecha'].split(' ')[0] if row['fecha'] else '-',
            row['cliente'],
            row['vendedor'],
            row['metodo_pago'],
            row['estado'],
            row['detalle_repuestos'],
            str(row['unidades']),
            f"Q {row['total']:,.2f}",
        ]
        for row in rows
    ]
    draw_table_pdf(pdf, 'Reporte de Ventas — Importadora Morales', headers, pdf_rows)
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name='reporte_ventas.pdf',
        mimetype='application/pdf',
    )


@app.route('/ventas/nueva', methods=['GET', 'POST'])
def nueva_venta():
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if request.method == 'POST':
            id_cliente = request.form.get('id_cliente') or None
            id_metodo_pago = request.form.get('id_metodo_pago') or None
            observaciones = request.form.get('observaciones', '').strip() or None
            repuesto_ids = request.form.getlist('id_repuesto[]')
            quantities = request.form.getlist('cantidad[]')
            discounts = request.form.getlist('descuento[]')

            if not id_metodo_pago:
                raise ValueError('Selecciona un metodo de pago.')
            if id_cliente and not _cliente_esta_activo(cursor, id_cliente):
                raise ValueError('El cliente seleccionado está inactivo y no se puede vender.')

            items = []
            total = 0.0
            for index, repuesto_id in enumerate(repuesto_ids):
                if not repuesto_id:
                    continue
                try:
                    quantity = int(quantities[index])
                    discount = float(discounts[index] or 0)
                except (IndexError, TypeError, ValueError):
                    raise ValueError('Revisa las cantidades y descuentos de los repuestos.')
                if quantity < 1 or discount < 0:
                    raise ValueError('Las cantidades deben ser mayores que cero y el descuento no puede ser negativo.')

                estado_activo_id = get_estado_general_id(cursor, activo=True)
                cursor.execute(
                    'SELECT id_repuesto, precio_venta, stock, id_estado_general '
                    'FROM repuesto WHERE id_repuesto = ?',
                    (repuesto_id,)
                )
                repuesto = cursor.fetchone()
                if not repuesto:
                    raise ValueError('Uno de los repuestos seleccionados ya no existe.')
                if repuesto.id_estado_general != estado_activo_id:
                    raise ValueError(f'El repuesto {repuesto_id} está inactivo y no se puede vender.')
                if repuesto.stock < quantity:
                    raise ValueError(f'El repuesto {repuesto_id} no tiene stock suficiente.')

                price = float(repuesto.precio_venta or 0)
                subtotal = max(0, price * quantity - discount)
                total += subtotal
                items.append((repuesto.id_repuesto, quantity, price, subtotal))

            if not items:
                raise ValueError('Agrega al menos un repuesto a la venta.')

            cursor.execute(
                'SET NOCOUNT ON; '
                'DECLARE @venta_insertada TABLE (id_venta INT); '
                'INSERT INTO venta (fecha_venta, id_cliente, id_usuario, total, observaciones, id_metodo_pago, descuento) '
                'OUTPUT INSERTED.id_venta INTO @venta_insertada (id_venta) '
                'VALUES (GETDATE(), ?, ?, ?, ?, ?, ?); '
                'SELECT id_venta FROM @venta_insertada;',
                (id_cliente, session.get('user_id'), total, observaciones, id_metodo_pago, 0)
            )
            id_venta = cursor.fetchone()[0]
            for id_repuesto, quantity, price, subtotal in items:
                cursor.execute(
                    'INSERT INTO detalle_venta (id_venta, id_repuesto, precio_unitario, subtotal, cantidad) '
                    'VALUES (?, ?, ?, ?, ?)',
                    (id_venta, id_repuesto, price, subtotal, quantity)
                )
            conn.commit()
            flash('Venta registrada correctamente.', 'success')
            return redirect(url_for('ventas'))

        clientes = []
        for data in _clientes_activos(cursor):
            clientes.append({
                'id': data['id_cliente'],
                'nombre': cliente_display_from_row(data),
                'nit': data.get('nit') or '',
                'correo': data.get('correo') or '',
            })

        cursor.execute('SELECT id_metodo_pago, metodo FROM metodo_pago ORDER BY id_metodo_pago')
        metodos_pago = [{'id': row[0], 'nombre': row[1]} for row in cursor.fetchall()]
        estado_activo_id = get_estado_general_id(cursor, activo=True)
        cursor.execute(
            'SELECT id_repuesto, codigo_repuesto, precio_venta, stock '
            'FROM repuesto WHERE stock > 0 AND id_estado_general = ? '
            'ORDER BY codigo_repuesto',
            (estado_activo_id,),
        )
        repuestos = []
        for row in cursor.fetchall():
            repuestos.append({
                'id': row[0],
                'codigo': row[1] or str(row[0]),
                'nombre': get_repuesto_display_name(cursor, row[0]) or row[1] or str(row[0]),
                'precio': float(row[2] or 0),
                'stock': row[3] or 0,
            })
        return redirect(url_for('ventas', modal='nueva'))
    except Exception as error:
        conn.rollback()
        flash(str(error), 'error')
        return redirect(url_for('nueva_venta'))
    finally:
        cursor.close()
        conn.close()

@app.route('/ventas/modificar/<int:id_venta>', methods=['GET', 'POST'])
def modificar_venta(id_venta):
    if 'user' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            id_cliente = request.form.get('id_cliente') or None
            id_metodo_pago = request.form.get('id_metodo_pago') or None
            observaciones = request.form.get('observaciones', '').strip() or None
            repuesto_ids = request.form.getlist('id_repuesto[]')
            quantities = request.form.getlist('cantidad[]')
            discounts = request.form.getlist('descuento[]')

            if not id_metodo_pago:
                raise ValueError('Selecciona un método de pago.')
            if id_cliente and not _cliente_esta_activo(cursor, id_cliente):
                raise ValueError('El cliente seleccionado está inactivo y no se puede vender.')

            items = []
            total = 0.0
            for index, repuesto_id in enumerate(repuesto_ids):
                if not repuesto_id:
                    continue
                try:
                    quantity = int(quantities[index])
                    discount = float(discounts[index] or 0)
                except (IndexError, TypeError, ValueError):
                    raise ValueError('Revisa las cantidades y descuentos de los repuestos.')
                if quantity < 1 or discount < 0:
                    raise ValueError('Las cantidades deben ser mayores que cero y el descuento no puede ser negativo.')

                estado_activo_id = get_estado_general_id(cursor, activo=True)
                cursor.execute(
                    'SELECT id_repuesto, precio_venta, stock, id_estado_general '
                    'FROM repuesto WHERE id_repuesto = ?',
                    (repuesto_id,),
                )
                repuesto = cursor.fetchone()
                if not repuesto:
                    raise ValueError('Uno de los repuestos seleccionados ya no existe.')
                if repuesto.id_estado_general != estado_activo_id:
                    raise ValueError(f'El repuesto {repuesto_id} está inactivo y no se puede vender.')
                if repuesto.stock < quantity:
                    raise ValueError(f'El repuesto {repuesto_id} no tiene stock suficiente.')
                price = float(repuesto.precio_venta or 0)
                subtotal = max(0, price * quantity - discount)
                total += subtotal
                items.append((repuesto.id_repuesto, quantity, price, subtotal))

            if not items:
                raise ValueError('Agrega al menos un repuesto a la venta.')

            cursor.execute('DELETE FROM detalle_venta WHERE id_venta = ?', (id_venta,))
            cursor.execute(
                'UPDATE venta SET id_cliente = ?, id_metodo_pago = ?, observaciones = ?, total = ?, id_usuario = ? WHERE id_venta = ?',
                (id_cliente, id_metodo_pago, observaciones, total, session.get('user_id'), id_venta)
            )
            for repuesto_id, quantity, price, subtotal in items:
                cursor.execute(
                    'INSERT INTO detalle_venta (id_venta, id_repuesto, precio_unitario, subtotal, cantidad) VALUES (?, ?, ?, ?, ?)',
                    (id_venta, repuesto_id, price, subtotal, quantity)
                )
            conn.commit()
            flash('Venta actualizada correctamente.', 'success')
            return redirect(url_for('ventas'))
        except Exception as e:
            conn.rollback()
            flash(str(e), 'error')
            return redirect(url_for('ventas', modal='editar', id_venta=id_venta))
        finally:
            cursor.close(); conn.close()

    return redirect(url_for('ventas', modal='editar', id_venta=id_venta))

@app.route('/ventas/eliminar/<int:id_venta>', methods=['POST'])
def eliminar_venta(id_venta):
    if 'user' not in session:
        return redirect(url_for('login'))

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            'DELETE FROM detalle_devolucion '
            'WHERE id_devolucion IN (SELECT id_devolucion FROM devolucion WHERE id_venta = ?)',
            (id_venta,)
        )
        cursor.execute('DELETE FROM devolucion WHERE id_venta = ?', (id_venta,))
        cursor.execute('DELETE FROM detalle_venta WHERE id_venta = ?', (id_venta,))
        cursor.execute('DELETE FROM venta WHERE id_venta = ?', (id_venta,))
        deleted_rows = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()

        if deleted_rows > 0:
            flash('Venta eliminada correctamente.', 'success')
        else:
            flash('No se encontró la venta para eliminar.', 'error')
    except Exception as e:
        flash(f'No se pudo eliminar la venta: {str(e)}', 'error')

    return redirect(url_for('ventas'))


def _devoluciones_write_allowed():
    return has_permission_in_session('devoluciones', 'write')


@app.route('/devoluciones')
def devoluciones():
    conn = get_db_connection()
    cursor = conn.cursor()
    devoluciones_rows = []
    ventas_disponibles = []
    try:
        cursor.execute(
            'SELECT d.id_devolucion AS id_devolucion, d.id_venta, d.fecha_devolucion, d.id_usuario, d.total_devuelto, '
            'dv.id_repuesto, dv.cantidad, dv.id_motivo, dv.precio_unitario, dv.subtotal, '
            'r.codigo_repuesto '
            'FROM devolucion d '
            'LEFT JOIN detalle_devolucion dv ON dv.id_devolucion = d.id_devolucion '
            'LEFT JOIN repuesto r ON r.id_repuesto = dv.id_repuesto '
            'ORDER BY d.fecha_devolucion DESC, d.id_devolucion DESC'
        )
        grouped = {}
        devolucion_columns = [column[0] for column in cursor.description]
        for row in cursor.fetchall():
            data = dict(zip(devolucion_columns, row))
            id_devolucion = data.get('id_devolucion')
            if id_devolucion is None:
                id_devolucion = data.get('id_devolucion_1')
            if id_devolucion is None and row:
                id_devolucion = row[0]
            if id_devolucion is None:
                continue
            current = grouped.setdefault(id_devolucion, {
                'id_devolucion': id_devolucion,
                'id_venta': data.get('id_venta'),
                'fecha_devolucion': data.get('fecha_devolucion'),
                'usuario': get_user_full_name(cursor, data.get('id_usuario')) or lookup_foreign_key_display(cursor, 'id_usuario', data.get('id_usuario'), ['usuario']) or 'Sistema',
                'cliente': get_cliente_full_name(cursor, None),
                'total_devuelto': Decimal('0.00'),
                'items': [],
            })
            id_repuesto = data.get('id_repuesto')
            if id_repuesto is not None:
                subtotal = Decimal(str(data.get('subtotal') or 0))
                current['total_devuelto'] += subtotal
                current['items'].append({
                    'codigo': data.get('codigo_repuesto') or str(id_repuesto),
                    'nombre': get_repuesto_display_name(cursor, id_repuesto) or data.get('codigo_repuesto') or str(id_repuesto),
                    'anio': get_repuesto_year(cursor, id_repuesto) or 'No registrado',
                    'cantidad': data.get('cantidad') or 0,
                    'precio_unitario': float(data.get('precio_unitario') or 0),
                    'subtotal': float(subtotal),
                    'motivo': lookup_foreign_key_display(cursor, 'id_motivo', data.get('id_motivo'), ['motivo_devolucion']) or 'Otro',
                })
        for item in grouped.values():
            item['items'] = list(item.get('items', []))
            item['total_devuelto'] = float(item['total_devuelto'])
            cursor.execute('SELECT id_cliente FROM venta WHERE id_venta = ?', (item['id_venta'],))
            sale = cursor.fetchone()
            item['cliente'] = get_cliente_full_name(cursor, sale[0]) if sale else 'Sin cliente frecuente'
            devoluciones_rows.append(item)

        clientes_por_id = {}
        client_columns = get_table_columns(cursor, 'cliente')
        client_id_column = cliente_field(client_columns, 'id_cliente') or next(
            (column for column in client_columns if str(column).lower().startswith('id')), None
        )
        if client_id_column:
            cursor.execute('SELECT * FROM cliente')
            for client_row in cursor.fetchall():
                client_data = dict(zip([column[0] for column in cursor.description], client_row))
                client_id = client_data.get(client_id_column)
                if client_id is not None:
                    clientes_por_id[client_id] = cliente_display_from_row(client_data)

        cursor.execute(
            'SELECT v.id_venta, v.fecha_venta, v.id_cliente, v.total, mp.metodo '
            'FROM venta v LEFT JOIN metodo_pago mp ON mp.id_metodo_pago = v.id_metodo_pago '
            'ORDER BY v.fecha_venta DESC, v.id_venta DESC'
        )
        for sale_row in cursor.fetchall():
            sale = {
                'id': sale_row[0], 'fecha': sale_row[1],
                'cliente': clientes_por_id.get(sale_row[2]) or 'Sin cliente frecuente',
                'total': float(sale_row[3] or 0), 'metodo_pago': sale_row[4] or 'Sin especificar',
                'items': [],
            }
            cursor.execute(
                'SELECT dv.id_repuesto, dv.cantidad, dv.precio_unitario, r.codigo_repuesto, '
                'COALESCE((SELECT SUM(dd.cantidad) FROM detalle_devolucion dd '
                'INNER JOIN devolucion d ON d.id_devolucion = dd.id_devolucion '
                'WHERE d.id_venta = dv.id_venta AND dd.id_repuesto = dv.id_repuesto), 0) AS cantidad_devuelta '
                'FROM detalle_venta dv LEFT JOIN repuesto r ON r.id_repuesto = dv.id_repuesto '
                'WHERE dv.id_venta = ? ORDER BY dv.id_detalle_venta',
                (sale['id'],)
            )
            for item_row in cursor.fetchall():
                cantidad_disponible = int(item_row[1] or 0)
                cantidad_devuelta = int(item_row[4] or 0)
                if cantidad_disponible < cantidad_devuelta:
                    cantidad_disponible += cantidad_devuelta * 2
                cantidad_vendida = cantidad_disponible
                cantidad_disponible = max(cantidad_vendida - cantidad_devuelta, 0)
                sale['items'].append({
                    'id': item_row[0], 'cantidad': cantidad_disponible,
                    'cantidad_vendida': cantidad_vendida,
                    'cantidad_devuelta': cantidad_devuelta,
                    'precio': float(item_row[2] or 0),
                    'codigo': item_row[3] or str(item_row[0]),
                    'nombre': get_repuesto_display_name(cursor, item_row[0]) or item_row[3] or str(item_row[0]),
                    'anio': get_repuesto_year(cursor, item_row[0]) or 'No registrado',
                })
            # La venta se mantiene localizable por cliente o número aun cuando
            # ya no tenga unidades disponibles. La interfaz indicará ese caso
            # sin mostrar filas de repuestos con cantidad cero.
            ventas_disponibles.append(sale)

        cursor.execute('SELECT id_motivo, motivo FROM motivo_devolucion ORDER BY motivo')
        motivos = [{'id': row[0], 'nombre': row[1]} for row in cursor.fetchall()]
        cursor.execute(
            'SELECT COUNT(*), COALESCE(SUM(subtotal), 0) FROM detalle_devolucion'
        )
        total_devoluciones, monto_total = cursor.fetchone()
        total_items_devueltos = sum(len(d.get('items', [])) for d in devoluciones_rows)
    finally:
        cursor.close()
        conn.close()

    return render_template(
        'devoluciones.html', devoluciones=devoluciones_rows, ventas_disponibles=ventas_disponibles,
        motivos=motivos, total_devoluciones=total_devoluciones or 0, monto_total=float(monto_total or 0),
        total_items_devueltos=total_items_devueltos,
    )


def _desarme_form_int(form, name, label, required=True):
    value = str(form.get(name) or '').strip()
    if not value and not required:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f'Selecciona un valor válido para {label}.')


def _desarme_save_vehicle(cursor, form, user_id, catalogs, process_link_ready):
    vehicle_columns = {
        column.lower(): column for column in get_table_columns(cursor, 'vehiculo_desarme')
    }
    required_vehicle_columns = {
        'id_vehiculo_desarme', 'id_marca', 'id_modelo', 'id_anio',
    }
    if not required_vehicle_columns.issubset(vehicle_columns):
        raise ValueError('La tabla vehiculo_desarme no tiene las columnas requeridas.')
    if not process_link_ready:
        raise ValueError(
            'Falta la relación entre desarme y vehiculo_desarme. '
            'Ejecuta la migración database/desarme_vehiculos.sql.'
        )

    brand_id = _desarme_form_int(form, 'id_marca', 'la marca')
    model_id = _desarme_form_int(form, 'id_modelo', 'el modelo')
    year_id = _desarme_form_int(form, 'id_anio', 'el año')
    color_id = _desarme_form_int(form, 'id_color', 'el color', required=False)
    transmission_id = _desarme_form_int(
        form, 'id_transmision', 'la transmisión', required=False
    )
    state_id = _desarme_form_int(form, 'id_estado_desarme', 'el estado')

    def catalog_contains(rows, value):
        return any(str(row[0]) == str(value) for row in rows)

    if not catalog_contains(catalogs['marcas'], brand_id):
        raise ValueError('La marca seleccionada no existe.')
    if not catalog_contains(fetch_modelos_by_marca(cursor, brand_id) or [], model_id):
        raise ValueError('El modelo seleccionado no corresponde a la marca.')
    if not catalog_contains(catalogs['anios'], year_id):
        raise ValueError('El año seleccionado no existe.')
    if color_id is not None and not catalog_contains(catalogs['colores'], color_id):
        raise ValueError('El color seleccionado no existe.')
    if transmission_id is not None and not catalog_contains(
        catalogs['transmisiones'], transmission_id
    ):
        raise ValueError('La transmisión seleccionada no existe.')
    if transmission_id is not None and 'id_transmision' not in vehicle_columns:
        raise ValueError('La tabla vehiculo_desarme no permite guardar la transmisión.')
    if not catalog_contains(catalogs['estados'], state_id):
        raise ValueError('El estado seleccionado no existe.')

    acquired_date = str(form.get('fecha_adquisicion') or '').strip() or None
    if acquired_date:
        try:
            datetime.strptime(acquired_date, '%Y-%m-%d')
        except ValueError:
            raise ValueError('La fecha de adquisición no es válida.')

    values = {
        'id_marca': brand_id,
        'id_modelo': model_id,
        'id_anio': year_id,
        'id_color': color_id,
        'id_transmision': transmission_id,
        'vin_chasis': str(form.get('vin_chasis') or '').strip() or None,
        'placa': str(form.get('placa') or '').strip() or None,
        'fecha_adquisicion': acquired_date,
        'origen_adquisicion': str(form.get('origen_adquisicion') or '').strip() or None,
        'id_estado': 1,
    }
    if 'fecha_registro' in vehicle_columns:
        values['fecha_registro'] = datetime.now()

    action = str(form.get('action') or 'create').strip()
    vehicle_id = _desarme_form_int(
        form, 'id_vehiculo_desarme', 'el vehículo', required=action == 'edit'
    )

    if action == 'create':
        insert_values = [
            (vehicle_columns[key], value)
            for key, value in values.items()
            if key in vehicle_columns
        ]
        id_column = vehicle_columns['id_vehiculo_desarme']
        cursor.execute(
            'SET NOCOUNT ON; '
            'DECLARE @inserted_vehicle TABLE (id INT); '
            'INSERT INTO dbo.vehiculo_desarme '
            f'({", ".join(f"[{column}]" for column, _ in insert_values)}) '
            f'OUTPUT INSERTED.[{id_column}] INTO @inserted_vehicle '
            f'VALUES ({", ".join("?" for _ in insert_values)}); '
            'SELECT id FROM @inserted_vehicle;',
            tuple(value for _, value in insert_values),
        )
        inserted = cursor.fetchone()
        if not inserted or inserted[0] is None:
            raise ValueError('No se pudo recuperar el identificador del vehículo registrado.')
        vehicle_id = int(inserted[0])
    elif action == 'edit':
        cursor.execute(
            'SELECT id_vehiculo_desarme FROM dbo.vehiculo_desarme '
            'WHERE id_vehiculo_desarme = ?',
            (vehicle_id,),
        )
        if not cursor.fetchone():
            raise ValueError('No se encontró el vehículo que deseas modificar.')
        update_values = [
            (vehicle_columns[key], value)
            for key, value in values.items()
            if key in vehicle_columns and key != 'fecha_registro'
        ]
        cursor.execute(
            'UPDATE dbo.vehiculo_desarme SET '
            + ', '.join(f'[{column}] = ?' for column, _ in update_values)
            + ' WHERE [id_vehiculo_desarme] = ?',
            tuple(value for _, value in update_values) + (vehicle_id,),
        )
    else:
        raise ValueError('La operación solicitada no es válida.')

    cursor.execute(
        'SELECT TOP 1 id_desarme FROM dbo.desarme '
        'WHERE id_vehiculo_desarme = ? ORDER BY id_desarme DESC',
        (vehicle_id,),
    )
    process = cursor.fetchone()
    process_id = process[0] if process else None
    if process_id is None:
        process_columns = {
            column.lower(): column for column in get_table_columns(cursor, 'desarme')
        }
        user_column = next(
            (
                column
                for column in ('id_usuario_responsable', 'id_usuario')
                if column in process_columns
            ),
            None,
        )
        if user_column and user_id is None:
            raise ValueError('No se pudo identificar al usuario responsable del desarme.')
        process_values = {
            'id_vehiculo_desarme': vehicle_id,
            'id_estado_desarme': state_id,
            'fecha_inicio': datetime.now() if str(
                next((name for value, name in catalogs['estados'] if str(value) == str(state_id)), '')
            ).strip().casefold().startswith('en proceso') else None,
            'id_vehiculo_origen': None,
            'id_estado': 1,
        }
        if user_column:
            process_values[user_column] = user_id
        process_insert_values = [
            (process_columns[key], value)
            for key, value in process_values.items()
            if key in process_columns and value is not None
        ]
        if not {'id_vehiculo_desarme', 'id_estado_desarme'}.issubset(process_columns):
            raise ValueError('La tabla desarme no tiene las columnas necesarias para crear el proceso.')
        cursor.execute(
            'INSERT INTO dbo.desarme '
            f'({", ".join(f"[{column}]" for column, _ in process_insert_values)}) '
            f'VALUES ({", ".join("?" for _ in process_insert_values)})',
            tuple(value for _, value in process_insert_values),
        )
    else:
        cursor.execute(
            'UPDATE dbo.desarme SET id_estado_desarme = ? WHERE id_desarme = ?',
            (state_id, process_id),
        )

    return vehicle_id


@app.route('/desarme', methods=['GET', 'POST'])
def desarme():
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = None
    cursor = None
    page_error = None
    estados = []
    origenes = []
    marcas = []
    anios = []
    colores = []
    transmisiones = []
    pending_state_id = None
    process_link_ready = False
    schema_ready = False
    vehicles = []
    summary = {'pending': 0, 'in_progress': 0, 'completed': 0, 'parts': 0}
    filters = {
        'q': request.args.get('q', '').strip(),
        'estado': request.args.get('estado', '').strip(),
        'origen': request.args.get('origen', '').strip(),
    }

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        estados = [
            {'id': row[0], 'nombre': row[1]}
            for row in (fetch_catalog_for_candidates(cursor, ['estado_desarme']) or [])
        ]
        origenes = [
            {'id': row[0], 'nombre': row[1]}
            for row in (fetch_catalog_for_candidates(cursor, ['vehiculo_origen']) or [])
        ]
        marcas = [
            {'id': row[0], 'nombre': row[1]}
            for row in (fetch_catalog_for_candidates(cursor, ['marca_vehiculo', 'marca']) or [])
        ]
        anios = [
            {'id': row[0], 'nombre': row[1]}
            for row in (fetch_catalog_for_candidates(cursor, ['anio_vehiculo', 'anio']) or [])
        ]
        colores = [
            {'id': row[0], 'nombre': row[1]}
            for row in (fetch_catalog_for_candidates(cursor, ['color', 'color_repuesto']) or [])
        ]
        transmisiones = [
            {'id': row[0], 'nombre': row[1]}
            for row in (fetch_catalog_for_candidates(
                cursor, ['transmision', 'transmision_vehiculo', 'tipo_transmision']
            ) or [])
        ]
        pending_state_id = next(
            (
                state['id'] for state in estados
                if str(state['nombre']).strip().casefold().startswith('pendiente')
            ),
            None,
        )

        desarme_columns = {
            column.lower() for column in get_table_columns(cursor, 'desarme')
        }
        vehicle_columns = {
            column.lower() for column in get_table_columns(cursor, 'vehiculo_desarme')
        }
        process_link_ready = 'id_vehiculo_desarme' in desarme_columns
        schema_ready = (
            {'id_vehiculo_desarme', 'id_marca', 'id_modelo', 'id_anio'}.issubset(vehicle_columns)
            and {'id_vehiculo_desarme', 'id_estado_desarme'}.issubset(desarme_columns)
            and process_link_ready
            and bool(marcas)
            and bool(anios)
            and bool(estados)
            and pending_state_id is not None
        )
        if request.method == 'POST':
            if not has_permission_in_session('desarme', 'write'):
                flash('No tienes permisos para registrar o modificar vehículos de desarme.', 'error')
                return redirect(url_for('desarme'))
            if not schema_ready:
                raise ValueError(
                    'El módulo de desarme no tiene el esquema o los catálogos requeridos.'
                )
            vehicle_id = _desarme_save_vehicle(
                cursor,
                request.form,
                session.get('user_id'),
                {
                    'marcas': [(item['id'], item['nombre']) for item in marcas],
                    'anios': [(item['id'], item['nombre']) for item in anios],
                    'colores': [(item['id'], item['nombre']) for item in colores],
                    'transmisiones': [(item['id'], item['nombre']) for item in transmisiones],
                    'estados': [(item['id'], item['nombre']) for item in estados],
                },
                process_link_ready,
            )
            conn.commit()
            flash(
                'Vehículo registrado correctamente.' if request.form.get('action') != 'edit'
                else 'Vehículo actualizado correctamente.',
                'success',
            )
            return redirect(url_for('desarme_detalle', vehicle_id=vehicle_id))

        process_join = (
            '(d.id_vehiculo_desarme = vd.id_vehiculo_desarme OR '
            '(d.id_vehiculo_desarme IS NULL '
            'AND d.id_desarme = vd.id_vehiculo_desarme '
            'AND NOT EXISTS (SELECT 1 FROM dbo.desarme linked '
            'WHERE linked.id_vehiculo_desarme = vd.id_vehiculo_desarme)))'
            if process_link_ready
            else 'd.id_desarme = vd.id_vehiculo_desarme'
        )
        cursor.execute(
            'SELECT vd.id_vehiculo_desarme, vd.id_marca, vd.id_modelo, vd.id_anio, vd.id_color, '
            'vd.vin_chasis, vd.placa, vd.fecha_adquisicion, vd.origen_adquisicion, '
            'd.id_desarme, d.fecha_inicio, d.fecha_finalizacion, d.observaciones, '
            'd.id_estado_desarme, ed.nombre AS estado, d.id_vehiculo_origen, vo.origen, '
            'COALESCE(SUM(dd.cantidad), 0) AS piezas, vd.id_transmision '
            'FROM dbo.vehiculo_desarme vd '
            'LEFT JOIN dbo.desarme d ON ' + process_join + ' '
            'LEFT JOIN dbo.estado_desarme ed ON ed.id_estado_desarme = d.id_estado_desarme '
            'LEFT JOIN dbo.vehiculo_origen vo ON vo.id_origen = d.id_vehiculo_origen '
            'LEFT JOIN dbo.detalle_desarme dd ON dd.id_desarme = d.id_desarme '
            'GROUP BY vd.id_vehiculo_desarme, vd.id_marca, vd.id_modelo, vd.id_anio, vd.id_color, '
            'vd.vin_chasis, vd.placa, vd.fecha_adquisicion, vd.origen_adquisicion, '
            'd.id_desarme, d.fecha_inicio, d.fecha_finalizacion, d.observaciones, '
            'd.id_estado_desarme, ed.nombre, d.id_vehiculo_origen, vo.origen, vd.id_transmision '
            'ORDER BY vd.id_vehiculo_desarme DESC'
        )
        columns = [column[0] for column in cursor.description]
        for row in cursor.fetchall():
            item = dict(zip(columns, row))
            label = item.get('estado') or 'Pendiente'
            normalized_state = str(label).strip().casefold()
            pieces = int(item.get('piezas') or 0)
            vehicle = {
                'id': item.get('id_vehiculo_desarme'),
                'id_marca': item.get('id_marca'),
                'id_modelo': item.get('id_modelo'),
                'id_anio': item.get('id_anio'),
                'id_color': item.get('id_color'),
                'id_transmision': item.get('id_transmision'),
                'marca': get_display_name_by_id(
                    cursor, ['marca_vehiculo', 'marca'], item.get('id_marca')
                ) or '—',
                'modelo': get_display_name_by_id(
                    cursor, ['modelo_vehiculo', 'modelo'], item.get('id_modelo')
                ) or '—',
                'anio': get_display_name_by_id(
                    cursor, ['anio_vehiculo', 'anio'], item.get('id_anio')
                ) or '—',
                'color': get_display_name_by_id(
                    cursor, ['color', 'color_repuesto'], item.get('id_color')
                ) or '—',
                'transmision': get_display_name_by_id(
                    cursor, ['transmision', 'transmision_vehiculo', 'tipo_transmision'],
                    item.get('id_transmision'),
                ) or '—',
                'vin_chasis': item.get('vin_chasis'),
                'placa': item.get('placa'),
                'fecha_adquisicion': item.get('fecha_adquisicion'),
                'process_id': item.get('id_desarme'),
                'fecha_inicio': item.get('fecha_inicio'),
                'fecha_finalizacion': item.get('fecha_finalizacion'),
                'observaciones': item.get('observaciones'),
                'state_id': item.get('id_estado_desarme') or pending_state_id,
                'estado': label,
                'state_class': _desarme_state_class(label),
                'origen': item.get('origen_adquisicion') or item.get('origen') or 'Sin especificar',
                'id_origen': item.get('origen_adquisicion') or item.get('origen') or '',
                'piezas': pieces,
            }
            if filters['estado'] and str(vehicle['state_id']) != filters['estado']:
                continue
            if filters['origen'] and str(vehicle['id_origen']).strip().casefold() != filters['origen'].strip().casefold():
                continue
            search_fields = [
                'id', 'marca', 'modelo', 'anio',
                'placa', 'vin_chasis', 'origen',
                'estado', 'observaciones',
            ]
            if filters['q'] and not matches_multiword_search(filters['q'], vehicle, search_fields):
                continue

            if normalized_state.startswith('pendiente'):
                summary['pending'] += 1
            elif normalized_state.startswith('en proceso'):
                summary['in_progress'] += 1
            elif _desarme_state_class(label) == 'completed':
                summary['completed'] += 1
            summary['parts'] += pieces
            vehicles.append(vehicle)
    except Exception as error:
        app.logger.exception('No se pudo cargar el módulo de desarme.')
        if request.method == 'POST':
            if conn is not None:
                conn.rollback()
            flash(f'No se pudo guardar el vehículo de desarme: {error}', 'error')
            return redirect(url_for('desarme', open_modal=1))
        page_error = 'No fue posible cargar los datos del desarme. Intenta actualizar la página.'
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

    summary_cards = [
        {'label': 'Pendientes', 'value': summary['pending'], 'color': 'pending'},
        {'label': 'En proceso', 'value': summary['in_progress'], 'color': 'in-progress'},
        {'label': 'Desarmados', 'value': summary['completed'], 'color': 'completed'},
        {'label': 'Total piezas', 'value': summary['parts'], 'color': 'parts'},
    ]
    user = get_current_sidebar_user()
    return render_template(
        'desarme.html',
        user_name=user['name'],
        user_role=user['role'],
        can_create=has_permission_in_session('desarme', 'write'),
        summary_cards=summary_cards,
        estados=estados,
        origenes=origenes,
        vehicles=vehicles,
        filters=filters,
        has_filters=any(filters.values()),
        page_error=page_error,
        marcas=marcas,
        anios=anios,
        colores=colores,
        transmisiones=transmisiones,
        today=datetime.now().strftime('%Y-%m-%d'),
        pending_state_id=pending_state_id,
        process_link_ready=process_link_ready,
        schema_ready=schema_ready,
    )


@app.route('/desarme/list-data')
def desarme_list_data():
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    filters = {
        'q': request.args.get('q', '').strip(),
        'estado': request.args.get('estado', '').strip(),
        'origen': request.args.get('origen', '').strip(),
    }
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        estados = [
            {'id': row[0], 'nombre': row[1]}
            for row in (fetch_catalog_for_candidates(cursor, ['estado_desarme']) or [])
        ]
        origenes = [
            {'id': row[0], 'nombre': row[1]}
            for row in (fetch_catalog_for_candidates(cursor, ['vehiculo_origen']) or [])
        ]
        marcas = [
            {'id': row[0], 'nombre': row[1]}
            for row in (fetch_catalog_for_candidates(cursor, ['marca_vehiculo', 'marca']) or [])
        ]
        anios = [
            {'id': row[0], 'nombre': row[1]}
            for row in (fetch_catalog_for_candidates(cursor, ['anio_vehiculo', 'anio']) or [])
        ]
        colores = [
            {'id': row[0], 'nombre': row[1]}
            for row in (fetch_catalog_for_candidates(cursor, ['color', 'color_repuesto']) or [])
        ]
        transmisiones = [
            {'id': row[0], 'nombre': row[1]}
            for row in (fetch_catalog_for_candidates(
                cursor, ['transmision', 'transmision_vehiculo', 'tipo_transmision']
            ) or [])
        ]
        pending_state_id = next(
            (
                state['id'] for state in estados
                if str(state['nombre']).strip().casefold().startswith('pendiente')
            ),
            None,
        )

        desarme_columns = {
            column.lower() for column in get_table_columns(cursor, 'desarme')
        }
        vehicle_columns = {
            column.lower() for column in get_table_columns(cursor, 'vehiculo_desarme')
        }
        process_link_ready = 'id_vehiculo_desarme' in desarme_columns

        process_join = (
            '(d.id_vehiculo_desarme = vd.id_vehiculo_desarme OR '
            '(d.id_vehiculo_desarme IS NULL '
            'AND d.id_desarme = vd.id_vehiculo_desarme '
            'AND NOT EXISTS (SELECT 1 FROM dbo.desarme linked '
            'WHERE linked.id_vehiculo_desarme = vd.id_vehiculo_desarme)))'
            if process_link_ready
            else 'd.id_desarme = vd.id_vehiculo_desarme'
        )
        cursor.execute(
            'SELECT vd.id_vehiculo_desarme, vd.id_marca, vd.id_modelo, vd.id_anio, vd.id_color, '
            'vd.vin_chasis, vd.placa, vd.fecha_adquisicion, vd.origen_adquisicion, '
            'd.id_desarme, d.fecha_inicio, d.fecha_finalizacion, d.observaciones, '
            'd.id_estado_desarme, ed.nombre AS estado, d.id_vehiculo_origen, vo.origen, '
            'COALESCE(SUM(dd.cantidad), 0) AS piezas, vd.id_transmision '
            'FROM dbo.vehiculo_desarme vd '
            'LEFT JOIN dbo.desarme d ON ' + process_join + ' '
            'LEFT JOIN dbo.estado_desarme ed ON ed.id_estado_desarme = d.id_estado_desarme '
            'LEFT JOIN dbo.vehiculo_origen vo ON vo.id_origen = d.id_vehiculo_origen '
            'LEFT JOIN dbo.detalle_desarme dd ON dd.id_desarme = d.id_desarme '
            'GROUP BY vd.id_vehiculo_desarme, vd.id_marca, vd.id_modelo, vd.id_anio, vd.id_color, '
            'vd.vin_chasis, vd.placa, vd.fecha_adquisicion, vd.origen_adquisicion, '
            'd.id_desarme, d.fecha_inicio, d.fecha_finalizacion, d.observaciones, '
            'd.id_estado_desarme, ed.nombre, d.id_vehiculo_origen, vo.origen, vd.id_transmision '
            'ORDER BY vd.id_vehiculo_desarme DESC'
        )
        columns = [column[0] for column in cursor.description]
        vehicles = []
        summary = {'pending': 0, 'in_progress': 0, 'completed': 0, 'parts': 0}

        for row in cursor.fetchall():
            item = dict(zip(columns, row))
            label = item.get('estado') or 'Pendiente'
            normalized_state = str(label).strip().casefold()
            pieces = int(item.get('piezas') or 0)
            vehicle = {
                'id': item.get('id_vehiculo_desarme'),
                'id_marca': item.get('id_marca'),
                'id_modelo': item.get('id_modelo'),
                'id_anio': item.get('id_anio'),
                'id_color': item.get('id_color'),
                'id_transmision': item.get('id_transmision'),
                'marca': get_display_name_by_id(
                    cursor, ['marca_vehiculo', 'marca'], item.get('id_marca')
                ) or '—',
                'modelo': get_display_name_by_id(
                    cursor, ['modelo_vehiculo', 'modelo'], item.get('id_modelo')
                ) or '—',
                'anio': get_display_name_by_id(
                    cursor, ['anio_vehiculo', 'anio'], item.get('id_anio')
                ) or '—',
                'color': get_display_name_by_id(
                    cursor, ['color', 'color_repuesto'], item.get('id_color')
                ) or '—',
                'transmision': get_display_name_by_id(
                    cursor, ['transmision', 'transmision_vehiculo', 'tipo_transmision'],
                    item.get('id_transmision'),
                ) or '—',
                'vin_chasis': item.get('vin_chasis'),
                'placa': item.get('placa'),
                'fecha_adquisicion': item.get('fecha_adquisicion'),
                'process_id': item.get('id_desarme'),
                'fecha_inicio': item.get('fecha_inicio'),
                'fecha_finalizacion': item.get('fecha_finalizacion'),
                'observaciones': item.get('observaciones'),
                'state_id': item.get('id_estado_desarme') or pending_state_id,
                'estado': label,
                'state_class': _desarme_state_class(label),
                'origen': item.get('origen_adquisicion') or item.get('origen') or 'Sin especificar',
                'id_origen': item.get('origen_adquisicion') or item.get('origen') or '',
                'piezas': pieces,
            }
            if filters['estado'] and str(vehicle['state_id']) != filters['estado']:
                continue
            if filters['origen'] and str(vehicle['id_origen']).strip().casefold() != filters['origen'].strip().casefold():
                continue
            search_fields = [
                'id', 'marca', 'modelo', 'anio',
                'placa', 'vin_chasis', 'origen',
                'estado', 'observaciones',
            ]
            if filters['q'] and not matches_multiword_search(filters['q'], vehicle, search_fields):
                continue

            if normalized_state.startswith('pendiente'):
                summary['pending'] += 1
            elif normalized_state.startswith('en proceso'):
                summary['in_progress'] += 1
            elif _desarme_state_class(label) == 'completed':
                summary['completed'] += 1
            summary['parts'] += pieces
            vehicles.append(vehicle)

        # Pagination
        page_size = 10
        total_items = len(vehicles)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        end = start + page_size
        vehicles_page = vehicles[start:end]

        # Render table rows HTML
        can_create = has_permission_in_session('desarme', 'write')
        rows_html = ''
        if vehicles_page:
            for vehicle in vehicles_page:
                fecha_adq = vehicle['fecha_adquisicion'].strftime('%d/%m/%Y') if vehicle['fecha_adquisicion'] and hasattr(vehicle['fecha_adquisicion'], 'strftime') else vehicle['fecha_adquisicion'] or '—'
                fecha_fin = vehicle['fecha_finalizacion'].strftime('%d/%m/%Y') if vehicle['fecha_finalizacion'] and hasattr(vehicle['fecha_finalizacion'], 'strftime') else vehicle['fecha_finalizacion'] or '—'
                rows_html += f'''
<tr>
    <td class="vehicle-id" data-label="ID vehículo">VEH-{{"%06d"|format({vehicle['id']}|int)}}</td>
    <td class="vehicle-make" data-label="Marca">{vehicle['marca']}</td>
    <td data-label="Modelo">{vehicle['modelo']}</td>
    <td data-label="Año">{vehicle['anio']}</td>
    <td data-label="Color">{vehicle['color']}</td>
    <td class="vehicle-identifier" data-label="Placa / VIN">
        {f'<span>{vehicle["placa"]}</span>' if vehicle['placa'] else ''}
        {f'<small>{vehicle["vin_chasis"]}</small>' if vehicle['vin_chasis'] else ''}
        {'—' if not vehicle['placa'] and not vehicle['vin_chasis'] else ''}
    </td>
    <td data-label="Origen">{vehicle['origen']}</td>
    <td data-label="Fecha adq.">{fecha_adq}</td>
    <td data-label="Estado"><span class="state-badge {vehicle['state_class']}">{vehicle['estado']}</span></td>
    <td data-label="Desarme">{fecha_fin}</td>
    <td data-label="Piezas"><span class="parts-count">{vehicle['piezas']}</span></td>
    <td data-label="Acciones">
        <div class="vehicle-row-actions">
            <a class="vehicle-detail-button" href="{{{{ url_for('desarme_detalle', vehicle_id={vehicle['id']}) }}}}" title="Ver todos los detalles del desarme" aria-label="Ver todos los detalles del desarme VEH-{{"%06d"|format({vehicle['id']}|int)}}"><i class="fa-regular fa-file-lines" aria-hidden="true"></i></a>
'''
                if can_create:
                    rows_html += f'''
            <button class="vehicle-edit-button" type="button" title="Modificar vehículo" aria-label="Modificar vehículo VEH-{{"%06d"|format({vehicle['id']}|int)}}" data-edit-vehicle data-id="{vehicle['id']}" data-brand="{vehicle['id_marca'] or ''}" data-model="{vehicle['id_modelo'] or ''}" data-year="{vehicle['id_anio'] or ''}" data-color="{vehicle['id_color'] or ''}" data-transmission="{vehicle['id_transmision'] or ''}" data-plate="{vehicle['placa'] or ''}" data-vin="{vehicle['vin_chasis'] or ''}" data-origin="{vehicle['origen'] if vehicle['origen'] != 'Sin especificar' else ''}" data-acquired="{vehicle['fecha_adquisicion'].strftime('%Y-%m-%d') if vehicle['fecha_adquisicion'] and hasattr(vehicle['fecha_adquisicion'], 'strftime') else vehicle['fecha_adquisicion'] or ''}" data-state="{vehicle['state_id'] or ''}"><i class="fa-solid fa-pen" aria-hidden="true"></i></button>
'''
                rows_html += '''
        </div>
    </td>
</tr>
'''
        else:
            has_filters = any(filters.values())
            rows_html = f'''
<tr>
    <td colspan="12" class="empty-state">
        <i class="fa-solid fa-car-side" aria-hidden="true"></i>
        <p>{'No hay vehículos registrados para desarme' if not has_filters else 'No se encontraron vehículos con esos filtros'}</p>
    </td>
</tr>
'''

        # Summary cards
        summary_cards = [
            {'label': 'Pendientes', 'value': summary['pending'], 'color': 'pending'},
            {'label': 'En proceso', 'value': summary['in_progress'], 'color': 'in-progress'},
            {'label': 'Desarmados', 'value': summary['completed'], 'color': 'completed'},
            {'label': 'Total piezas', 'value': summary['parts'], 'color': 'parts'},
        ]

        # Pagination HTML
        pagination_html = ''
        if total_pages > 1:
            pagination_html = '<div class="table-pagination">'
            pagination_html += f'<span class="table-pagination-summary">Mostrando {start + 1}–{min(end, total_items)} de {total_items} registros</span>'
            pagination_html += '<div class="table-pagination-controls">'
            if page > 1:
                pagination_html += f'<button type="button" class="table-pagination-previous" data-page="{page - 1}" aria-label="Página anterior">‹</button>'
            else:
                pagination_html += '<button type="button" class="table-pagination-previous" disabled aria-label="Página anterior">‹</button>'

            page_numbers = set([1, total_pages])
            for p in range(max(1, page - 2), min(total_pages, page + 2) + 1):
                page_numbers.add(p)
            prev = 0
            for p in sorted(page_numbers):
                if p - prev > 1:
                    pagination_html += '<span class="table-pagination-gap">…</span>'
                pagination_html += f'<button type="button" class="table-pagination-page {"is-current" if p == page else ""}" data-page="{p}" aria-label="Ir a la página {p}"{" aria-current=\"page\"" if p == page else ""}>{p}</button>'
                prev = p

            if page < total_pages:
                pagination_html += f'<button type="button" class="table-pagination-next" data-page="{page + 1}" aria-label="Página siguiente">›</button>'
            else:
                pagination_html += '<button type="button" class="table-pagination-next" disabled aria-label="Página siguiente">›</button>'
            pagination_html += '</div></div>'

        return jsonify({
            'success': True,
            'vehicles': vehicles_page,
            'rows_html': rows_html,
            'pagination_html': pagination_html,
            'summary': summary,
            'summary_cards': summary_cards,
            'total_items': total_items,
            'total_pages': total_pages,
            'current_page': page,
            'has_filters': any(filters.values()),
            'filters': filters,
        })
    except Exception as error:
        app.logger.exception('No se pudo cargar la lista de desarme.')
        return jsonify({'error': 'No fue posible cargar los datos del desarme.'}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/desarme/<int:vehicle_id>')
def desarme_detalle(vehicle_id):
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        vehicle_columns = get_table_columns(cursor, 'vehiculo_desarme')
        if not vehicle_columns or 'id_vehiculo_desarme' not in {
            column.lower() for column in vehicle_columns
        }:
            flash('No está disponible el detalle del vehículo para desarme.', 'error')
            return redirect(url_for('desarme'))

        desarme_columns = {
            column.lower() for column in get_table_columns(cursor, 'desarme')
        }
        process_link_ready = 'id_vehiculo_desarme' in desarme_columns
        process_join = (
            '(d.id_vehiculo_desarme = vd.id_vehiculo_desarme OR '
            '(d.id_vehiculo_desarme IS NULL '
            'AND d.id_desarme = vd.id_vehiculo_desarme '
            'AND NOT EXISTS (SELECT 1 FROM dbo.desarme linked '
            'WHERE linked.id_vehiculo_desarme = vd.id_vehiculo_desarme)))'
            if process_link_ready
            else 'd.id_desarme = vd.id_vehiculo_desarme'
        )
        cursor.execute(
            'SELECT d.id_desarme, d.fecha_inicio, d.fecha_finalizacion, d.observaciones, '
            'd.id_estado_desarme, ed.nombre AS estado, d.id_vehiculo_origen, vo.origen, '
            'vd.id_vehiculo_desarme, vd.id_marca, vd.id_modelo, vd.id_anio, vd.id_color, '
            'vd.vin_chasis, vd.placa, vd.fecha_adquisicion, vd.origen_adquisicion, '
            'COALESCE((SELECT SUM(dd.cantidad) FROM dbo.detalle_desarme dd '
            'WHERE dd.id_desarme = d.id_desarme), 0) AS piezas, vd.id_transmision '
            'FROM dbo.vehiculo_desarme vd '
            'LEFT JOIN dbo.desarme d ON ' + process_join + ' '
            'LEFT JOIN dbo.estado_desarme ed ON ed.id_estado_desarme = d.id_estado_desarme '
            'LEFT JOIN dbo.vehiculo_origen vo ON vo.id_origen = d.id_vehiculo_origen '
            'WHERE vd.id_vehiculo_desarme = ?',
            (vehicle_id,),
        )
        row = cursor.fetchone()
        if row is None:
            flash('No se encontró el vehículo para desarme solicitado.', 'error')
            return redirect(url_for('desarme'))

        state = row[5] or 'Pendiente'
        normalized_state = str(state).strip().casefold()
        vehicle = {
            'id': row[8] or vehicle_id,
            'marca': get_display_name_by_id(cursor, ['marca_vehiculo', 'marca'], row[9]) or '—',
            'modelo': get_display_name_by_id(cursor, ['modelo_vehiculo', 'modelo'], row[10]) or '—',
            'anio': get_display_name_by_id(cursor, ['anio_vehiculo', 'anio'], row[11]) or '—',
            'color': get_display_name_by_id(cursor, ['color', 'color_repuesto'], row[12]) or '—',
            'id_transmision': row[18],
            'transmision': get_display_name_by_id(
                cursor, ['transmision', 'transmision_vehiculo', 'tipo_transmision'], row[18]
            ) or '—',
            'vin_chasis': row[13],
            'placa': row[14],
            'fecha_adquisicion': row[15],
            'origen': row[16] or row[7] or 'Sin especificar',
            'piezas': int(row[17] or 0),
            'estado': state,
            'state_class': _desarme_state_class(state),
            'process_id': row[0],
            'has_process': row[0] is not None,
            'process_code': f"DSR-{int(row[0]):06d}" if row[0] is not None else '—',
            'fecha_inicio': row[1],
            'fecha_finalizacion': row[2],
            'responsable': 'No asignado',
        }
        pieces = []
        process_id = row[0]
        detail_columns = {
            column.lower(): column for column in get_table_columns(cursor, 'detalle_desarme')
        }
        detail_process_column = next(
            (
                detail_columns[name]
                for name in ('id_desarme', 'id_proceso_desarme')
                if name in detail_columns
            ),
            None,
        )
        if process_id is not None and detail_process_column:
            detail_id_column = next(
                (
                    detail_columns[name]
                    for name in ('id_detalle_desarme', 'id_detalle')
                    if name in detail_columns
                ),
                None,
            )
            detail_order = f' ORDER BY [{detail_id_column}]' if detail_id_column else ''
            cursor.execute(
                f'SELECT * FROM dbo.detalle_desarme '
                f'WHERE [{detail_process_column}] = ?{detail_order}',
                (process_id,),
            )
            detail_names = [column[0].lower() for column in cursor.description]
            for detail_row in cursor.fetchall():
                data = dict(zip(detail_names, detail_row))
                id_tipo = data.get('id_tipo_repuesto')
                id_estado_pieza = data.get('id_estado_pieza')
                id_repuesto = data.get('id_repuesto')
                estado_pieza = (
                    data.get('estado_pieza')
                    or get_display_name_by_id(
                        cursor, ['estado_pieza_desarme', 'estado_pieza'], id_estado_pieza
                    )
                    or 'Sin estado'
                )
                tipo_repuesto = (
                    data.get('tipo_repuesto')
                    or get_display_name_by_id(cursor, ['tipo_repuesto'], id_tipo)
                    or '—'
                )
                codigo_repuesto = data.get('codigo_repuesto')
                if id_repuesto is not None and codigo_repuesto is None:
                    cursor.execute(
                        'SELECT codigo_repuesto FROM dbo.repuesto WHERE id_repuesto = ?',
                        (id_repuesto,),
                    )
                    repuesto_row = cursor.fetchone()
                    codigo_repuesto = repuesto_row[0] if repuesto_row else None
                lado = (
                    data.get('lado')
                    or get_display_name_by_id(
                        cursor, ['lado_repuesto', 'lado'], data.get('id_lado')
                    )
                    or 'No aplica'
                )
                posicion = (
                    data.get('posicion')
                    or get_display_name_by_id(
                        cursor, ['posicion_repuesto', 'posicion'], data.get('id_posicion')
                    )
                    or 'No aplica'
                )
                pieces.append({
                    'id_detalle_desarme': (
                        data.get('id_detalle_desarme')
                        or data.get('id_detalle')
                        or len(pieces) + 1
                    ),
                    'id_tipo_repuesto': id_tipo,
                    'id_lado': data.get('id_lado'),
                    'id_posicion': data.get('id_posicion'),
                    'precio_venta': data.get('precio_venta') or data.get('precio') or 0,
                    'observaciones': data.get('observaciones'),
                    'tipo_repuesto': tipo_repuesto,
                    'lado': lado,
                    'posicion': posicion,
                    'cantidad': data.get('cantidad') or 0,
                    'estado_pieza': estado_pieza,
                    'state_class': (
                        'is-damaged'
                        if any(token in str(estado_pieza).strip().casefold() for token in ('dañad', 'damag'))
                        else ''
                    ),
                    'ruta_fotografia': (
                        data.get('ruta_fotografia')
                        or data.get('fotografia')
                        or data.get('foto')
                    ),
                    'id_repuesto': id_repuesto,
                    'codigo_repuesto': codigo_repuesto,
                })
        vehicle['piezas'] = sum(int(piece['cantidad'] or 0) for piece in pieces)
        result_counts, finalization_pieces = _desarme_result_summary(pieces)
        result = None
        result_error = None
        result_columns = {
            column.lower() for column in get_table_columns(cursor, 'resultado_desarme')
        }
        required_result_columns = {
            'id_resultado_desarme', 'id_desarme', 'piezas_obtenidas',
            'piezas_aptas', 'piezas_danadas', 'piezas_no_aptas',
            'observaciones', 'fecha_registro', 'id_usuario', 'id_estado',
        }
        if required_result_columns.issubset(result_columns) and process_id is not None:
            cursor.execute(
                'SELECT TOP 1 id_resultado_desarme, piezas_obtenidas, piezas_aptas, '
                'piezas_danadas, piezas_no_aptas, observaciones, fecha_registro, '
                'id_usuario, id_estado FROM dbo.resultado_desarme '
                'WHERE id_desarme = ? ORDER BY id_resultado_desarme DESC',
                (process_id,),
            )
            result_row = cursor.fetchone()
            if result_row:
                result = {
                    'id': result_row[0],
                    'piezas_obtenidas': result_row[1],
                    'piezas_aptas': result_row[2],
                    'piezas_danadas': result_row[3],
                    'piezas_no_aptas': result_row[4],
                    'observaciones': result_row[5],
                    'fecha_registro': result_row[6],
                    'usuario': get_user_full_name(cursor, result_row[7]) or lookup_foreign_key_display(
                        cursor, 'id_usuario', result_row[7], ['usuario']
                    ) or '—',
                    'estado': _desarme_result_state_name(cursor, result_row[8]) or '—',
                }
                vehicle['responsable'] = result['usuario']
        elif process_id is not None:
            result_error = (
                'No está configurada la tabla resultado_desarme con las columnas requeridas.'
            )
        incidents = []
        incidents_error = None
        incident_statuses = []
        incident_columns = {
            column.lower() for column in get_table_columns(cursor, 'incidencia_desarme')
        }
        required_incident_columns = {
            'id_incidencia', 'id_desarme', 'id_detalle_desarme',
            'descripcion', 'fecha_incidencia', 'id_usuario', 'id_estado',
        }
        if required_incident_columns.issubset(incident_columns) and process_id is not None:
            incident_statuses = _desarme_incident_statuses(cursor)
            incident_status_names = {
                int(status['id']): status['nombre']
                for status in incident_statuses
            }
            cursor.execute(
                'SELECT id_incidencia, id_detalle_desarme, descripcion, '
                'fecha_incidencia, id_usuario, id_estado '
                'FROM dbo.incidencia_desarme WHERE id_desarme = ? '
                'ORDER BY id_incidencia DESC',
                (process_id,),
            )
            pieces_by_id = {
                int(piece['id_detalle_desarme']): piece
                for piece in pieces
                if piece.get('id_detalle_desarme') is not None
            }
            for incident_row in cursor.fetchall():
                associated_piece = pieces_by_id.get(int(incident_row[1] or 0))
                incidents.append({
                    'id': incident_row[0],
                    'pieza': (
                        f"DTD-{int(incident_row[1]):06d} — {associated_piece['tipo_repuesto']}"
                        if associated_piece else '—'
                    ),
                    'descripcion': incident_row[2],
                    'fecha_incidencia': incident_row[3],
                    'usuario': get_user_full_name(cursor, incident_row[4]) or lookup_foreign_key_display(
                        cursor, 'id_usuario', incident_row[4], ['usuario']
                    ) or '—',
                    'estado': incident_status_names.get(
                        int(incident_row[5]) if incident_row[5] is not None else -1,
                        str(incident_row[5]) if incident_row[5] is not None else '—',
                    ),
                })
            if not incident_statuses:
                incidents_error = (
                    'No se encontró el catálogo asociado a id_estado de '
                    'incidencia_desarme.'
                )
        else:
            incidents_error = (
                'No está configurada la tabla de incidencias de Desarme. '
                'Ejecuta la migración database/desarme_vehiculos.sql y vuelve a cargar.'
            )
        return render_template(
            'desarme_detalle.html',
            vehicle=vehicle,
            pieces=pieces,
            incidents=incidents,
            incidents_error=incidents_error,
            incident_statuses=incident_statuses,
            result=result,
            result_error=result_error,
            result_counts=result_counts,
            finalization_pieces=finalization_pieces,
            can_create=has_permission_in_session('desarme', 'write'),
            registered_by=session.get('full_name') or session.get('user', 'Usuario'),
            registered_at=datetime.now().strftime('%d/%m/%Y %H:%M'),
            today_iso=datetime.now().strftime('%Y-%m-%d'),
            piece_types=fetch_catalog_for_candidates(cursor, ['tipo_repuesto']) or [],
            piece_states=fetch_catalog_for_candidates(cursor, ['estado_pieza_desarme', 'estado_pieza']) or [],
            piece_sides=fetch_catalog_for_candidates(cursor, ['lado_repuesto', 'lado']) or [],
            piece_positions=fetch_catalog_for_candidates(cursor, ['posicion_repuesto', 'posicion']) or [],
            piece_form_values={},
        )
    except Exception:
        app.logger.exception('No se pudo cargar el detalle del vehículo para desarme.')
        flash('No fue posible cargar el detalle del vehículo. Intenta actualizar la página.', 'error')
        return redirect(url_for('desarme'))
    finally:
        cursor.close()
        conn.close()


@app.route('/desarme/<int:vehicle_id>/incidencias', methods=['POST'])
def registrar_incidencia_desarme(vehicle_id):
    if not has_permission_in_session('desarme', 'write'):
        flash('No tienes permisos para registrar incidencias.', 'error')
        return redirect(url_for('desarme_detalle', vehicle_id=vehicle_id))

    description = request.form.get('descripcion', '').strip()
    detail_id_raw = request.form.get('id_detalle_desarme', '').strip()
    status_id_raw = request.form.get('id_estado', '').strip()
    date_raw = request.form.get('fecha_incidencia', '').strip()
    if not description or len(description) > 1000:
        flash('La descripción es obligatoria y no puede superar 1000 caracteres.', 'error')
        return redirect(url_for('desarme_detalle', vehicle_id=vehicle_id) + '?open_incident=1')
    try:
        detail_id = int(detail_id_raw)
        status_id = int(status_id_raw)
        incident_date = datetime.strptime(date_raw, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        flash('Selecciona una pieza, un estado y una fecha válidos para la incidencia.', 'error')
        return redirect(url_for('desarme_detalle', vehicle_id=vehicle_id) + '?open_incident=1')

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        process_id = _desarme_process_id(cursor, vehicle_id)
        if process_id is None:
            raise ValueError('No se encontró el proceso de desarme de este vehículo.')
        incident_columns = {
            column.lower() for column in get_table_columns(cursor, 'incidencia_desarme')
        }
        required_incident_columns = {
            'id_incidencia', 'id_desarme', 'id_detalle_desarme',
            'descripcion', 'fecha_incidencia', 'id_usuario', 'id_estado',
        }
        if not required_incident_columns.issubset(incident_columns):
            raise ValueError(
                'No está configurada la tabla de incidencias. '
                'Ejecuta la migración database/desarme_vehiculos.sql.'
            )
        valid_status_ids = {
            int(status['id'])
            for status in _desarme_incident_statuses(cursor)
        }
        if status_id not in valid_status_ids:
            raise ValueError('El estado seleccionado no existe en el catálogo de incidencias.')
        cursor.execute(
            'SELECT COUNT(*) FROM dbo.detalle_desarme '
            'WHERE id_detalle_desarme = ? AND id_desarme = ?',
            (detail_id, process_id),
        )
        if not cursor.fetchone()[0]:
            raise ValueError('La pieza seleccionada no pertenece a este proceso de desarme.')
        cursor.execute(
            'INSERT INTO dbo.incidencia_desarme '
            '(id_desarme, id_detalle_desarme, descripcion, fecha_incidencia, id_usuario, id_estado) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (process_id, detail_id, description, incident_date, session.get('user_id'), status_id),
        )
        conn.commit()
        flash('La incidencia se registró correctamente.', 'success')
    except Exception as error:
        conn.rollback()
        app.logger.exception(
            'No se pudo registrar la incidencia del vehículo %s.', vehicle_id
        )
        flash(f'No se pudo registrar la incidencia: {error}', 'error')
        return redirect(url_for('desarme_detalle', vehicle_id=vehicle_id) + '?open_incident=1')
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('desarme_detalle', vehicle_id=vehicle_id) + '#tab-incidents')


def _desarme_incident_statuses(cursor):
    """Load valid incidence states, preferring the table referenced by id_estado."""
    cursor.execute(
        'SELECT TOP 1 rs.name, rt.name, rc.name '
        'FROM sys.foreign_key_columns fkc '
        'JOIN sys.tables st ON st.object_id = fkc.parent_object_id '
        'JOIN sys.schemas ss ON ss.schema_id = st.schema_id '
        'JOIN sys.columns sc ON sc.object_id = st.object_id '
        ' AND sc.column_id = fkc.parent_column_id '
        'JOIN sys.tables rt ON rt.object_id = fkc.referenced_object_id '
        'JOIN sys.schemas rs ON rs.schema_id = rt.schema_id '
        'JOIN sys.columns rc ON rc.object_id = rt.object_id '
        ' AND rc.column_id = fkc.referenced_column_id '
        'WHERE ss.name = ? AND st.name = ? AND sc.name = ?',
        ('dbo', 'incidencia_desarme', 'id_estado'),
    )
    foreign_key_target = cursor.fetchone()
    if foreign_key_target:
        status_tables = [(foreign_key_target[0], foreign_key_target[1])]
        referenced_id = foreign_key_target[2]
    else:
        status_tables = [
            ('dbo', 'estado_incidencia_desarme'),
            ('dbo', 'estado_incidencia'),
            ('dbo', 'estado_general'),
            ('dbo', 'estado_repuesto'),
        ]
        referenced_id = None

    for schema_name, table_name in status_tables:
        table_ref = f'[{schema_name.replace("]", "]]")}].[{table_name.replace("]", "]]")}]'
        columns = get_table_columns(cursor, table_ref)
        id_column = next(
            (column for column in columns if column.lower() == referenced_id.lower()),
            None,
        ) if referenced_id else next(
            (column for column in columns if column.lower().startswith('id')),
            None,
        )
        display_column = choose_display_column(columns)
        if not id_column or not display_column:
            continue
        cursor.execute(
            f'SELECT [{id_column.replace("]", "]]")}] AS id, '
            f'[{display_column.replace("]", "]]")}] AS nombre '
            f'FROM {table_ref} ORDER BY [{display_column.replace("]", "]]")}]'
        )
        return [
            {'id': row[0], 'nombre': row[1]}
            for row in cursor.fetchall()
        ]
    return []


def _desarme_process_id(cursor, vehicle_id):
    columns = {column.lower() for column in get_table_columns(cursor, 'desarme')}
    if 'id_vehiculo_desarme' in columns:
        cursor.execute(
            'SELECT TOP 1 id_desarme FROM dbo.desarme '
            'WHERE id_vehiculo_desarme = ? ORDER BY id_desarme DESC',
            (vehicle_id,),
        )
        row = cursor.fetchone()
        if row:
            return row[0]
    cursor.execute(
        'SELECT TOP 1 id_desarme FROM dbo.desarme WHERE id_desarme = ?',
        (vehicle_id,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _desarme_state_id(cursor, label_prefix):
    rows = fetch_catalog_for_candidates(cursor, ['estado_desarme']) or []
    target = label_prefix.strip().casefold()
    for state_id, name in rows:
        if str(name or '').strip().casefold().startswith(target):
            return state_id
    return None


def _desarme_state_class(state_name):
    normalized = str(state_name or '').strip().casefold()
    if normalized.startswith('pendiente'):
        return 'pending'
    if normalized.startswith('en proceso'):
        return 'in-progress'
    if normalized.startswith(('finaliz', 'desarmad')):
        return 'completed'
    return ''


def _desarme_final_state_id(cursor):
    return (
        _desarme_state_id(cursor, 'desarmado')
        or _desarme_state_id(cursor, 'finalizado')
    )


def _desarme_piece_category(state_name):
    normalized = str(state_name or '').strip().casefold()
    if any(token in normalized for token in ('incompleta', 'no apt', 'inutil', 'rechaz')):
        return 'no-aptas'
    if any(token in normalized for token in ('dañad', 'danad', 'damage')):
        return 'danadas'
    if any(token in normalized for token in ('apta', 'apto', 'aprovech', 'buen')):
        return 'aptas'
    return 'no-aptas'


def _desarme_result_summary(pieces):
    counts = {
        'piezas_obtenidas': 0,
        'piezas_aptas': 0,
        'piezas_danadas': 0,
        'piezas_no_aptas': 0,
    }
    summary_pieces = []
    for piece in pieces:
        category = _desarme_piece_category(piece.get('estado_pieza'))
        quantity = int(piece.get('cantidad') or 0)
        counts['piezas_obtenidas'] += quantity
        counts[
            'piezas_aptas' if category == 'aptas'
            else 'piezas_danadas' if category == 'danadas'
            else 'piezas_no_aptas'
        ] += quantity
        summary_pieces.append({
            'nombre': f"{piece.get('tipo_repuesto') or 'Pieza'} — {piece.get('lado') or 'No aplica'}",
            'estado': piece.get('estado_pieza') or 'Sin estado',
            'categoria': category,
            'cantidad': quantity,
        })
    return counts, summary_pieces


def _desarme_fk_target(cursor, table_name, column_name):
    cursor.execute(
        'SELECT rs.name, rt.name, rc.name '
        'FROM sys.foreign_key_columns fkc '
        'JOIN sys.tables st ON st.object_id = fkc.parent_object_id '
        'JOIN sys.schemas ss ON ss.schema_id = st.schema_id '
        'JOIN sys.columns sc ON sc.object_id = st.object_id '
        ' AND sc.column_id = fkc.parent_column_id '
        'JOIN sys.tables rt ON rt.object_id = fkc.referenced_object_id '
        'JOIN sys.schemas rs ON rs.schema_id = rt.schema_id '
        'JOIN sys.columns rc ON rc.object_id = rt.object_id '
        ' AND rc.column_id = fkc.referenced_column_id '
        'WHERE ss.name = ? AND st.name = ? AND sc.name = ?',
        ('dbo', table_name, column_name),
    )
    return cursor.fetchone()


def _desarme_result_state_id(cursor):
    target = _desarme_fk_target(cursor, 'resultado_desarme', 'id_estado')
    candidates = (
        [(target[0], target[1], target[2])]
        if target else [
            ('dbo', 'estado_resultado_desarme', None),
            ('dbo', 'estado_resultado', None),
            ('dbo', 'estado_general', None),
            ('dbo', 'estado_repuesto', None),
        ]
    )
    for schema_name, table_name, referenced_id in candidates:
        table_ref = f'[{schema_name.replace("]", "]]")}].[{table_name.replace("]", "]]")}]'
        columns = get_table_columns(cursor, table_ref)
        id_column = next(
            (
                column for column in columns
                if column.lower() == referenced_id.lower()
            ),
            None,
        ) if referenced_id else next(
            (column for column in columns if column.lower().startswith('id')),
            None,
        )
        display_column = choose_display_column(columns)
        if not id_column or not display_column:
            continue
        cursor.execute(
            f'SELECT [{id_column.replace("]", "]]")}], '
            f'[{display_column.replace("]", "]]")}] FROM {table_ref}'
        )
        statuses = cursor.fetchall()
        for state_id, label in statuses:
            normalized = str(label or '').strip().casefold()
            if normalized in {'activo', 'activa', 'registrado', 'registrada', 'finalizado', 'finalizada'}:
                return state_id
    return None


def _desarme_result_state_name(cursor, state_id):
    if state_id is None:
        return None
    target = _desarme_fk_target(cursor, 'resultado_desarme', 'id_estado')
    candidates = (
        [(target[0], target[1], target[2])]
        if target else [
            ('dbo', 'estado_resultado_desarme', None),
            ('dbo', 'estado_resultado', None),
            ('dbo', 'estado_general', None),
            ('dbo', 'estado_repuesto', None),
        ]
    )
    for schema_name, table_name, referenced_id in candidates:
        table_ref = f'[{schema_name.replace("]", "]]")}].[{table_name.replace("]", "]]")}]'
        columns = get_table_columns(cursor, table_ref)
        id_column = next(
            (column for column in columns if column.lower() == referenced_id.lower()),
            None,
        ) if referenced_id else next(
            (column for column in columns if column.lower().startswith('id')),
            None,
        )
        display_column = choose_display_column(columns)
        if not id_column or not display_column:
            continue
        cursor.execute(
            f'SELECT [{display_column.replace("]", "]]")}] FROM {table_ref} '
            f'WHERE [{id_column.replace("]", "]]")}] = ?',
            (state_id,),
        )
        row = cursor.fetchone()
        if row:
            return row[0]
    return None


def _desarme_available_inventory_state_id(cursor):
    target = _desarme_fk_target(cursor, 'repuesto', 'id_estado_repuesto')
    candidates = (
        [(target[0], target[1], target[2])]
        if target else [
            ('dbo', 'estado_repuesto', None),
            ('dbo', 'estado_general', None),
        ]
    )
    for schema_name, table_name, referenced_id in candidates:
        table_ref = f'[{schema_name.replace("]", "]]")}].[{table_name.replace("]", "]]")}]'
        columns = get_table_columns(cursor, table_ref)
        id_column = next(
            (
                column for column in columns
                if column.lower() == referenced_id.lower()
            ),
            None,
        ) if referenced_id else next(
            (column for column in columns if column.lower().startswith('id')),
            None,
        )
        display_column = choose_display_column(columns)
        if not id_column or not display_column:
            continue
        cursor.execute(
            f'SELECT [{id_column.replace("]", "]]")}], '
            f'[{display_column.replace("]", "]]")}] FROM {table_ref}'
        )
        statuses = cursor.fetchall()
        for state_id, label in statuses:
            normalized = str(label or '').strip().casefold()
            if any(token in normalized for token in (
                'dispon', 'activo', 'activa', 'apto', 'apta', 'usable',
                'usado', 'reutil', 'aprovech', 'bueno',
            )):
                return state_id
        # Legacy catalogs sometimes use numeric states without a descriptive
        # availability label. Keep the conventional first state as fallback.
        if statuses and table_name.lower() in {'estado_repuesto', 'estado_general'}:
            for state_id, _ in statuses:
                if str(state_id) == '1':
                    return state_id
        if statuses and target and table_name.lower() == str(target[1]).lower():
            return statuses[0][0]
    return None


def _register_desarme_piece_in_inventory(cursor, process_id, vehicle_data, piece, user_id):
    piece_columns = {
        column.lower(): column for column in get_table_columns(cursor, 'detalle_desarme')
    }
    repuesto_columns = {
        column.lower(): column for column in get_table_columns(cursor, 'repuesto')
    }
    detail_id = piece.get('id_detalle_desarme')
    quantity = int(piece.get('cantidad') or 0)
    if quantity <= 0 or not piece.get('id_tipo_repuesto') or not detail_id:
        raise ValueError('Una pieza apta no tiene tipo, identificador o cantidad válida.')

    # Calcular stock_minimo y stock_maximo automáticamente (10% / 3x)
    stock_minimo = max(1, int(quantity * 0.10))
    stock_maximo = quantity * 3

    id_tipo_repuesto = piece.get('id_tipo_repuesto')
    id_lado = piece.get('id_lado')
    id_posicion = piece.get('id_posicion')
    id_vehiculo_origen = vehicle_data.get('id_vehiculo_origen')
    id_marca = vehicle_data.get('id_marca')
    id_modelo = vehicle_data.get('id_modelo')
    id_anio = vehicle_data.get('id_anio')
    id_color = vehicle_data.get('id_color')
    id_transmision = vehicle_data.get('id_transmision')

    # Get photo from piece
    photo_column = piece_columns.get('ruta_fotografia') or piece_columns.get('fotografia') or piece_columns.get('foto')
    piece_photo = piece.get(photo_column) if photo_column else None

    repuesto_id = None

    if 'id_repuesto' in piece_columns and piece.get('id_repuesto'):
        repuesto_id = int(piece['id_repuesto'])

    if repuesto_id is None:
        match_columns = []
        match_params = []
        if 'id_tipo_repuesto' in repuesto_columns:
            match_columns.append(f'[{repuesto_columns["id_tipo_repuesto"]}] = ?')
            match_params.append(id_tipo_repuesto)
        if 'id_marca' in repuesto_columns and id_marca is not None:
            match_columns.append(f'[{repuesto_columns["id_marca"]}] = ?')
            match_params.append(id_marca)
        if 'id_modelo' in repuesto_columns and id_modelo is not None:
            match_columns.append(f'[{repuesto_columns["id_modelo"]}] = ?')
            match_params.append(id_modelo)
        if 'id_anio' in repuesto_columns and id_anio is not None:
            match_columns.append(f'[{repuesto_columns["id_anio"]}] = ?')
            match_params.append(id_anio)
        if 'id_color' in repuesto_columns and id_color is not None:
            match_columns.append(f'[{repuesto_columns["id_color"]}] = ?')
            match_params.append(id_color)
        if 'id_transmision' in repuesto_columns and id_transmision is not None:
            match_columns.append(f'[{repuesto_columns["id_transmision"]}] = ?')
            match_params.append(id_transmision)
        if 'id_lado' in repuesto_columns and id_lado is not None:
            match_columns.append(f'[{repuesto_columns["id_lado"]}] = ?')
            match_params.append(id_lado)
        if 'id_posicion' in repuesto_columns and id_posicion is not None:
            match_columns.append(f'[{repuesto_columns["id_posicion"]}] = ?')
            match_params.append(id_posicion)

        if match_columns:
            where_clause = ' AND '.join(match_columns)
            cursor.execute(
                f'SELECT TOP 1 [{repuesto_columns["id_repuesto"]}], [{repuesto_columns["stock"]}] '
                f'FROM dbo.repuesto WHERE {where_clause}',
                tuple(match_params),
            )
            existing = cursor.fetchone()
            if existing:
                repuesto_id = int(existing[0])

    if repuesto_id is not None:
        if 'stock' not in repuesto_columns:
            raise ValueError('La tabla repuesto no tiene la columna de stock requerida.')
        update_columns = [repuesto_columns['stock']]
        set_clause = f'[{update_columns[0]}] = COALESCE([{update_columns[0]}], 0) + ?'
        params = [quantity]
        # Actualizar también stock_minimo y stock_maximo
        if 'stock_minimo' in repuesto_columns:
            set_clause += f', [{repuesto_columns["stock_minimo"]}] = ?'
            params.append(stock_minimo)
        if 'stock_maximo' in repuesto_columns:
            set_clause += f', [{repuesto_columns["stock_maximo"]}] = ?'
            params.append(stock_maximo)
        if 'id_transmision' in repuesto_columns and id_transmision is not None:
            set_clause += f', [{repuesto_columns["id_transmision"]}] = ?'
            params.append(id_transmision)
        if 'id_estado_repuesto' in repuesto_columns:
            available_state = _desarme_available_inventory_state_id(cursor)
            if available_state is None:
                raise ValueError('No se encontró el estado Disponible para registrar inventario.')
            set_clause += f', [{repuesto_columns["id_estado_repuesto"]}] = ?'
            params.append(available_state)
        if 'id_estado_general' in repuesto_columns:
            set_clause += f', [{repuesto_columns["id_estado_general"]}] = ?'
            params.append(get_estado_general_id(cursor, activo=True))
        # Update photo if piece has one
        if piece_photo and 'fotografia' in repuesto_columns:
            set_clause += f', [{repuesto_columns["fotografia"]}] = ?'
            params.append(piece_photo)
        params.append(repuesto_id)
        cursor.execute(
            f'UPDATE dbo.repuesto SET {set_clause} '
            f'WHERE [{repuesto_columns["id_repuesto"]}] = ?',
            tuple(params),
        )
        if cursor.rowcount != 1:
            raise ValueError('No se encontró el repuesto para actualizar stock.')
    else:
        if 'id_repuesto' not in repuesto_columns:
            raise ValueError('La tabla repuesto no tiene identificador para crear inventario.')
        detail_id_column = piece_columns.get('id_detalle_desarme') or piece_columns.get('id_detalle')
        code = f'REP-{int(detail_id):06d}'
        data = {
            'codigo_repuesto': code,
            'id_vehiculo_origen': id_vehiculo_origen,
            'id_tipo_repuesto': id_tipo_repuesto,
            'id_marca': id_marca,
            'id_modelo': id_modelo,
            'id_anio': id_anio,
            'id_color': id_color,
            'id_transmision': id_transmision,
            'id_lado': id_lado,
            'id_posicion': id_posicion,
            'id_ubicacion': get_tipo_ubicacion(cursor, id_tipo_repuesto),
            'id_bodega': get_tipo_ubicacion(cursor, id_tipo_repuesto),
            'stock_minimo': stock_minimo,
            'stock_maximo': stock_maximo,
            'stock': quantity,
            'precio_compra': 0,
            'precio_venta': piece.get('precio_venta') or 0,
            'observaciones': piece.get('observaciones') or f'Recuperado del desarme DSR-{int(process_id):06d}.',
            'fecha_ingreso': datetime.now(),
            'id_usuario_registro': user_id,
            'id_estado': 1,
        }
        if piece_photo and 'fotografia' in repuesto_columns:
            data['fotografia'] = piece_photo
        if 'id_estado_general' in repuesto_columns:
            data['id_estado_general'] = get_estado_general_id(cursor, activo=True)
        if 'id_estado_repuesto' in repuesto_columns:
            available_state = _desarme_available_inventory_state_id(cursor)
            if available_state is None:
                raise ValueError('No se encontró el estado Disponible para registrar inventario.')
            data['id_estado_repuesto'] = available_state
        insert_values = [
            (repuesto_columns[key], value)
            for key, value in data.items()
            if key in repuesto_columns and value is not None
        ]
        if not any(column.lower() == 'codigo_repuesto' for column, _ in insert_values):
            raise ValueError('La tabla repuesto requiere una columna codigo_repuesto.')
        column_sql = ', '.join(f'[{column}]' for column, _ in insert_values)
        value_sql = ', '.join('?' for _ in insert_values)
        identity_column = repuesto_columns['id_repuesto']
        cursor.execute(
            'SET NOCOUNT ON; '
            'DECLARE @inserted_repuesto TABLE (id_repuesto INT); '
            f'INSERT INTO dbo.repuesto ({column_sql}) '
            f'OUTPUT INSERTED.[{identity_column}] INTO @inserted_repuesto (id_repuesto) '
            f'VALUES ({value_sql}); '
            'SELECT id_repuesto FROM @inserted_repuesto',
            tuple(value for _, value in insert_values),
        )
        while cursor.description is None:
            if not cursor.nextset():
                break
        row = cursor.fetchone()
        if not row or row[0] is None:
            raise ValueError('No se pudo recuperar el identificador del repuesto registrado.')
        repuesto_id = int(row[0])
        if detail_id_column and 'id_repuesto' in piece_columns:
            cursor.execute(
                f'UPDATE dbo.detalle_desarme SET [{piece_columns["id_repuesto"]}] = ? '
                f'WHERE [{detail_id_column}] = ?',
                (repuesto_id, detail_id),
            )

    movement_result = registrar_movimiento_inventario(
        cursor,
        repuesto_id,
        1,
        quantity,
        f'Ingreso por desarme DSR-{int(process_id):06d}, DTD-{int(detail_id):06d}',
        user_id,
        1,
    )
    if movement_result is None:
        raise ValueError('No se pudo registrar el movimiento de ingreso al inventario.')
    cursor.execute(
        "DELETE FROM dbo.movimiento_inventario "
        "WHERE id_repuesto = ? AND detalle = 'Ingreso automático de repuesto'",
        (repuesto_id,),
    )


@app.route('/desarme/<int:vehicle_id>/iniciar', methods=['POST'])
def iniciar_desarme(vehicle_id):
    if not has_permission_in_session('desarme', 'write'):
        flash('No tienes permisos para iniciar un desarme.', 'error')
        return redirect(url_for('desarme_detalle', vehicle_id=vehicle_id))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        process_id = _desarme_process_id(cursor, vehicle_id)
        state_id = _desarme_state_id(cursor, 'en proceso')
        if process_id is None or state_id is None:
            raise ValueError('No se encontró el proceso o el estado «En proceso» en los catálogos.')
        cursor.execute(
            'UPDATE dbo.desarme SET id_estado_desarme = ?, fecha_inicio = COALESCE(fecha_inicio, GETDATE()) '
            'WHERE id_desarme = ?',
            (state_id, process_id),
        )
        conn.commit()
        flash('El desarme se inició correctamente.', 'success')
    except Exception as error:
        conn.rollback()
        app.logger.exception('No se pudo iniciar el desarme del vehículo %s.', vehicle_id)
        flash(f'No se pudo iniciar el desarme: {error}', 'error')
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('desarme_detalle', vehicle_id=vehicle_id))


@app.route('/desarme/<int:vehicle_id>/finalizar', methods=['POST'])
def finalizar_desarme(vehicle_id):
    if not has_permission_in_session('desarme', 'write'):
        flash('No tienes permisos para finalizar un desarme.', 'error')
        return redirect(url_for('desarme_detalle', vehicle_id=vehicle_id))

    conn = get_db_connection()
    cursor = conn.cursor()
    finalization_failed = False
    try:
        process_id = _desarme_process_id(cursor, vehicle_id)
        if process_id is None:
            raise ValueError('No se encontró el proceso de desarme de este vehículo.')
        cursor.execute(
            'SELECT ed.nombre FROM dbo.desarme d WITH (UPDLOCK, HOLDLOCK) '
            'LEFT JOIN dbo.estado_desarme ed ON ed.id_estado_desarme = d.id_estado_desarme '
            'WHERE d.id_desarme = ?',
            (process_id,),
        )
        process_state = cursor.fetchone()
        if not process_state or not str(process_state[0] or '').strip().casefold().startswith('en proceso'):
            raise ValueError('Solo se puede finalizar un proceso que esté En proceso.')
        cursor.execute(
            'SELECT TOP 1 r.id_resultado_desarme FROM dbo.resultado_desarme r '
            'WHERE r.id_desarme = ? ORDER BY r.id_resultado_desarme DESC',
            (process_id,),
        )
        if cursor.fetchone():
            raise ValueError('Este proceso ya tiene un resultado registrado.')
        result_columns = {
            column.lower() for column in get_table_columns(cursor, 'resultado_desarme')
        }
        required_result_columns = {
            'id_resultado_desarme', 'id_desarme', 'piezas_obtenidas',
            'piezas_aptas', 'piezas_danadas', 'piezas_no_aptas',
            'observaciones', 'fecha_registro', 'id_usuario', 'id_estado',
        }
        if not required_result_columns.issubset(result_columns):
            raise ValueError('La tabla dbo.resultado_desarme no tiene las columnas requeridas.')
        result_state_id = _desarme_result_state_id(cursor)
        if result_state_id is None:
            raise ValueError('No se encontró un estado válido en el catálogo de resultado_desarme.')

        cursor.execute(
            'SELECT vd.id_marca, vd.id_modelo, vd.id_anio, vd.id_color, vd.id_transmision '
            'FROM dbo.desarme d '
            'LEFT JOIN dbo.vehiculo_desarme vd ON vd.id_vehiculo_desarme = ? '
            'WHERE d.id_desarme = ?',
            (vehicle_id, process_id),
        )
        vehicle_data_row = cursor.fetchone()
        if not vehicle_data_row:
            raise ValueError('No se encontraron los datos del vehículo para registrar inventario.')
        desarme_columns = {
            column.lower() for column in get_table_columns(cursor, 'desarme')
        }
        origin_id = None
        if 'id_vehiculo_origen' in desarme_columns:
            cursor.execute(
                'SELECT id_vehiculo_origen FROM dbo.desarme WHERE id_desarme = ?',
                (process_id,),
            )
            origin_row = cursor.fetchone()
            origin_id = origin_row[0] if origin_row else None
        vehicle_data = {
            'id_marca': vehicle_data_row[0],
            'id_modelo': vehicle_data_row[1],
            'id_anio': vehicle_data_row[2],
            'id_color': vehicle_data_row[3],
            'id_transmision': vehicle_data_row[4],
            'id_vehiculo_origen': origin_id,
        }
        detail_columns = {
            column.lower(): column for column in get_table_columns(cursor, 'detalle_desarme')
        }
        process_column = detail_columns.get('id_desarme') or detail_columns.get('id_proceso_desarme')
        detail_id_column = detail_columns.get('id_detalle_desarme') or detail_columns.get('id_detalle')
        if not process_column or not detail_id_column:
            raise ValueError('La tabla dbo.detalle_desarme no tiene las columnas requeridas.')
        cursor.execute(
            f'SELECT * FROM dbo.detalle_desarme WHERE [{process_column}] = ? '
            f'ORDER BY [{detail_id_column}]',
            (process_id,),
        )
        detail_names = [column[0].lower() for column in cursor.description]
        pieces = [dict(zip(detail_names, row)) for row in cursor.fetchall()]
        for piece in pieces:
            quantity = int(piece.get('cantidad') or 0)
            if quantity < 0:
                raise ValueError('Una pieza tiene una cantidad negativa y no se puede finalizar.')
            state_name = piece.get('estado_pieza')
            if state_name is None:
                state_name = get_display_name_by_id(
                    cursor,
                    ['estado_pieza_desarme', 'estado_pieza'],
                    piece.get('id_estado_pieza') or piece.get('id_estado_pieza_desarme'),
                )
            piece['estado_pieza'] = state_name
            piece['id_detalle_desarme'] = piece.get('id_detalle_desarme') or piece.get('id_detalle')
            piece['precio_venta'] = piece.get('precio_venta') or piece.get('precio') or 0

        counts, summary_pieces = _desarme_result_summary(pieces)
        for piece, summary_piece in zip(pieces, summary_pieces):
            if summary_piece['categoria'] == 'aptas':
                _register_desarme_piece_in_inventory(
                    cursor, process_id, vehicle_data, piece, session.get('user_id')
                )

        observations = request.form.get('observaciones', '').strip()
        if len(observations) > 1000:
            raise ValueError('Las observaciones no pueden superar 1000 caracteres.')
        cursor.execute(
            'INSERT INTO dbo.resultado_desarme '
            '(id_desarme, piezas_obtenidas, piezas_aptas, piezas_danadas, '
            'piezas_no_aptas, observaciones, fecha_registro, id_usuario, id_estado) '
            'VALUES (?, ?, ?, ?, ?, ?, GETDATE(), ?, ?)',
            (
                process_id,
                counts['piezas_obtenidas'],
                counts['piezas_aptas'],
                counts['piezas_danadas'],
                counts['piezas_no_aptas'],
                observations or None,
                session.get('user_id'),
                result_state_id,
            ),
        )
        state_id = _desarme_final_state_id(cursor)
        if state_id is None:
            raise ValueError(
                'No se encontró un estado finalizado (Desarmado o Finalizado) en el catálogo.'
            )
        cursor.execute(
            'UPDATE dbo.desarme SET id_estado_desarme = ?, fecha_finalizacion = GETDATE() '
            'WHERE id_desarme = ?',
            (state_id, process_id),
        )
        conn.commit()
        flash('El desarme finalizó correctamente; el resultado quedó guardado y las piezas aptas ingresaron al inventario.', 'success')
    except Exception as error:
        conn.rollback()
        app.logger.exception('No se pudo finalizar el desarme del vehículo %s.', vehicle_id)
        flash(f'No se pudo finalizar el desarme: {error}', 'error')
        finalization_failed = True
    finally:
        cursor.close()
        conn.close()
    suffix = '?open_finalize=1' if finalization_failed else ''
    return redirect(url_for('desarme_detalle', vehicle_id=vehicle_id) + suffix + '#tab-result')


@app.route('/desarme/<int:vehicle_id>/piezas', methods=['POST'])
def agregar_pieza_desarme(vehicle_id):
    if not has_permission_in_session('desarme', 'write'):
        flash('No tienes permisos para agregar piezas al desarme.', 'error')
        return redirect(url_for('desarme_detalle', vehicle_id=vehicle_id))

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        process_id = _desarme_process_id(cursor, vehicle_id)
        if process_id is None:
            raise ValueError('No se encontró el proceso de desarme de este vehículo.')

        columns = {column.lower() for column in get_table_columns(cursor, 'detalle_desarme')}
        process_column = next(
            (name for name in ('id_desarme', 'id_proceso_desarme') if name in columns),
            None,
        )
        detail_id_column = next(
            (name for name in ('id_detalle_desarme', 'id_detalle') if name in columns),
            None,
        )
        type_id = request.form.get('id_tipo_repuesto', '').strip()
        detail_id = request.form.get('id_detalle_desarme', '').strip()
        quantity_text = request.form.get('cantidad', '').strip()
        price_text = request.form.get('precio_venta', '').strip()
        if not process_column or not type_id:
            raise ValueError('El formulario o el esquema de detalle de desarme está incompleto.')
        quantity = int(quantity_text)
        price = Decimal(price_text or '0')
        if quantity < 1 or price < 0:
            raise ValueError('La cantidad debe ser mayor a cero y el precio no puede ser negativo.')

        values = {
            'id_tipo_repuesto': int(type_id),
            'cantidad': quantity,
            'id_lado': int(request.form['id_lado']) if request.form.get('id_lado') else None,
            'id_posicion': int(request.form['id_posicion']) if request.form.get('id_posicion') else None,
            'observaciones': request.form.get('observaciones', '').strip() or None,
            'estado_pieza': request.form.get('estado_pieza', '').strip() or None,
        }
        if 'id_usuario_registro' in columns:
            registered_by = session.get('user_id')
            if registered_by is None:
                raise ValueError('No se pudo identificar al usuario que registra la pieza.')
            values['id_usuario_registro'] = int(registered_by)
        price_column = next(
            (name for name in ('precio_venta', 'precio') if name in columns),
            None,
        )
        if price_column:
            values[price_column] = price
        state_column = next(
            (name for name in ('id_estado_pieza', 'id_estado_pieza_desarme', 'id_estado') if name in columns),
            None,
        )
        if state_column:
            state_catalog = fetch_catalog_for_candidates(
                cursor, ['estado_pieza_desarme', 'estado_pieza']
            ) or []
            wanted_state = request.form.get('estado_pieza', '').strip().casefold()
            state_id = next(
                (catalog_id for catalog_id, name in state_catalog
                 if str(name or '').strip().casefold() == wanted_state),
                None,
            )
            if state_id is not None:
                values[state_column] = state_id
            else:
                raise ValueError(
                    'No se pudo identificar el estado seleccionado en el catálogo de estados de pieza.'
                )

        photo_column = next(
            (name for name in ('ruta_fotografia', 'fotografia', 'foto') if name in columns),
            None,
        )
        photo_file = get_uploaded_repuesto_photo()
        if photo_file and photo_column:
            values[photo_column] = save_repuesto_photo(photo_file)

        update_values = [
            (name, value) for name, value in values.items()
            if name in columns and name != 'id_usuario_registro'
        ]
        if detail_id:
            if not detail_id_column:
                raise ValueError('La tabla de detalle no tiene un identificador de pieza editable.')
            try:
                detail_id_value = int(detail_id)
            except ValueError as error:
                raise ValueError('El identificador de la pieza no es válido.') from error
            cursor.execute(
                f'SELECT COUNT(*) FROM dbo.detalle_desarme '
                f'WHERE [{detail_id_column}] = ? AND [{process_column}] = ?',
                (detail_id_value, process_id),
            )
            if not cursor.fetchone()[0]:
                raise ValueError('La pieza no pertenece al proceso de este vehículo.')
            if not update_values:
                raise ValueError('No se encontraron campos compatibles para actualizar la pieza.')
            set_clause = ', '.join(f'[{name}] = ?' for name, _ in update_values)
            cursor.execute(
                f'UPDATE dbo.detalle_desarme SET {set_clause} '
                f'WHERE [{detail_id_column}] = ? AND [{process_column}] = ?',
                tuple(value for _, value in update_values) + (detail_id_value, process_id),
            )
            flash('La pieza se actualizó correctamente.', 'success')
        else:
            insert_values = [(process_column, process_id)] + update_values
            if 'id_usuario_registro' in columns:
                insert_values.append(('id_usuario_registro', int(session['user_id'])))
            if not insert_values:
                raise ValueError('No se encontraron campos compatibles para guardar la pieza.')
            column_list = ', '.join(f'[{name}]' for name, _ in insert_values)
            placeholders = ', '.join('?' for _ in insert_values)
            cursor.execute(
                f'INSERT INTO dbo.detalle_desarme ({column_list}) VALUES ({placeholders})',
                tuple(value for _, value in insert_values),
            )
            flash('La pieza se agregó correctamente.', 'success')
        conn.commit()
    except Exception as error:
        conn.rollback()
        app.logger.exception('No se pudo agregar una pieza al vehículo %s.', vehicle_id)
        flash(f'No se pudo agregar la pieza: {error}', 'error')
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('desarme_detalle', vehicle_id=vehicle_id, open_piece=1))


def _prestamos_write_allowed():
    return has_permission_in_session('prestamos', 'write')


def _prestamo_estado_id(cursor, objetivo):
    cursor.execute('SELECT id_estado_prestamo, estado FROM estado_prestamo ORDER BY id_estado_prestamo')
    rows = cursor.fetchall()
    labels = [(row[0], str(row[1] or '').casefold()) for row in rows]
    tokens = {
        'activo': ('activo', 'pendiente'),
        'vencido': ('vencido',),
        'devuelto': ('devuelto',),
        'pagado': ('pagado', 'transfe'),
    }.get(objetivo, ())
    for state_id, label in labels:
        if any(token in label for token in tokens):
            return state_id
    fallback = {'activo': 1, 'vencido': 3, 'devuelto': 2, 'pagado': 2}.get(objetivo)
    return fallback


@app.route('/prestamos')
def prestamos():
    conn = get_db_connection()
    cursor = conn.cursor()
    prestamos_rows = []
    clientes = []
    repuestos = []
    estados = []
    metodos_pago = []
    try:
        estado_vencido_id = _prestamo_estado_id(cursor, 'vencido')
        estado_activo_id = _prestamo_estado_id(cursor, 'activo')
        cursor.execute(
            'UPDATE prestamo SET id_estado = ? '
            'WHERE id_estado = ? AND fecha_devolucion_estimada < CAST(GETDATE() AS DATE)',
            (estado_vencido_id, estado_activo_id),
        )
        conn.commit()
        cursor.execute(
            'SELECT p.id_prestamo, p.fecha_prestamo, p.fecha_devolucion_estimada, p.id_cliente, p.id_usuario, '
            'p.observaciones, p.id_estado, ep.estado '
            'FROM prestamo p LEFT JOIN estado_prestamo ep ON ep.id_estado_prestamo = p.id_estado '
            'ORDER BY p.fecha_prestamo DESC, p.id_prestamo DESC'
        )
        loan_columns = [column[0] for column in cursor.description]
        for row in cursor.fetchall():
            loan = dict(zip(loan_columns, row))
            id_cliente = loan.get('id_cliente') or (row[3] if len(row) > 3 else None)
            id_usuario = loan.get('id_usuario') or (row[4] if len(row) > 4 else None)
            id_estado = loan.get('id_estado') or (row[6] if len(row) > 6 else None)
            estado = loan.get('estado') or (row[7] if len(row) > 7 else None)
            id_prestamo = loan.get('id_prestamo') or (row[0] if len(row) > 0 else None)
            
            if id_prestamo is None:
                continue
                
            loan['id_prestamo'] = id_prestamo
            loan['id_estado'] = id_estado
            loan['cliente'] = get_cliente_full_name(cursor, id_cliente) or 'Sin cliente frecuente'
            loan['usuario'] = get_user_full_name(cursor, id_usuario) or lookup_foreign_key_display(cursor, 'id_usuario', id_usuario, ['usuario']) or 'Sistema'
            loan['estado'] = estado or 'Activo'
            cursor.execute(
                'SELECT dp.id_repuesto, dp.cantidad, dp.precio, dp.observaciones, r.codigo_repuesto '
                'FROM detalle_prestamo dp LEFT JOIN repuesto r ON r.id_repuesto = dp.id_repuesto '
                'WHERE dp.id_prestamo = ? ORDER BY dp.id_detalle_prestamo', (loan['id_prestamo'],)
            )
            loan['items'] = []
            loan['total'] = 0.0
            loan['unidades'] = 0
            for detail in cursor.fetchall():
                quantity = detail[1] or 0
                price = float(detail[2] or 0)
                loan['items'].append({
                    'id': detail[0], 'codigo': detail[4] or str(detail[0]),
                    'nombre': get_repuesto_display_name(cursor, detail[0]) or detail[4] or str(detail[0]),
                    'anio': get_repuesto_year(cursor, detail[0]),
                    'cantidad': quantity, 'precio': price, 'observaciones': detail[3] or '',
                })
                loan['total'] += price * quantity
                loan['unidades'] += quantity
            prestamos_rows.append(loan)

        for data in _clientes_activos(cursor):
            clientes.append({'id': data['id_cliente'], 'nombre': cliente_display_from_row(data)})
        cursor.execute('SELECT id_estado_prestamo, estado FROM estado_prestamo ORDER BY id_estado_prestamo')
        estados = [{'id': row[0], 'nombre': row[1]} for row in cursor.fetchall()]
        cursor.execute('SELECT id_metodo_pago, metodo FROM metodo_pago ORDER BY id_metodo_pago')
        metodos_pago = [{'id': row[0], 'nombre': row[1]} for row in cursor.fetchall()]
        cursor.execute(
            "SELECT id_repuesto, codigo_repuesto, precio_venta, stock FROM repuesto "
            "WHERE COALESCE(stock, 0) > 0 "
            "AND (id_estado_general <> 2 OR id_estado_general IS NULL) ORDER BY codigo_repuesto"
        )
        for row in cursor.fetchall():
            repuestos.append({
                'id': row[0], 'codigo': row[1] or str(row[0]),
                'nombre': get_repuesto_display_name(cursor, row[0]) or row[1] or str(row[0]),
                'precio': float(row[2] or 0), 'stock': row[3] or 0,
            })
    finally:
        cursor.close()
        conn.close()

    activos = sum(1 for loan in prestamos_rows if loan['estado'].lower() == 'activo')
    vencidos = sum(1 for loan in prestamos_rows if loan['estado'].lower() == 'vencido')
    valor_prestamo = sum(loan['total'] for loan in prestamos_rows if loan['estado'].lower() in {'activo', 'vencido'})
    g.overdue_loan_count = vencidos
    return render_template(
        'prestamos.html', prestamos=prestamos_rows, clientes=clientes, repuestos=repuestos,
        estados=estados, metodos_pago=metodos_pago,
        activos=activos, vencidos=vencidos, valor_prestamo=valor_prestamo,
    )


@app.route('/prestamos/nuevo', methods=['POST'])
def nuevo_prestamo():
    if not _prestamos_write_allowed():
        flash('No tienes permisos para registrar préstamos.', 'error')
        return redirect(url_for('prestamos'))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        id_cliente = request.form.get('id_cliente') or None
        fecha_devolucion = request.form.get('fecha_devolucion') or None
        observaciones = request.form.get('observaciones', '').strip() or None
        repuesto_ids = request.form.getlist('id_repuesto[]')
        quantities = request.form.getlist('cantidad[]')
        prices = request.form.getlist('precio[]')
        if not id_cliente or not fecha_devolucion:
            raise ValueError('Selecciona un cliente y una fecha estimada de devolución.')
        if not _cliente_esta_activo(cursor, id_cliente):
            raise ValueError('El cliente seleccionado está inactivo y no se puede prestar.')
        items = []
        requested_quantities = {}
        for index, repuesto_id in enumerate(repuesto_ids):
            if not repuesto_id:
                continue
            quantity = int(quantities[index])
            price = float(prices[index] or 0)
            if quantity < 1 or price < 0:
                raise ValueError('Revisa cantidades y precios del préstamo.')
            cursor.execute(
                'SELECT id_repuesto, stock, id_estado_general FROM repuesto WHERE id_repuesto = ?', (repuesto_id,)
            )
            repuesto = cursor.fetchone()
            if not repuesto:
                raise ValueError('Uno de los repuestos seleccionados no existe.')
            if repuesto[2] == 2:
                raise ValueError('Uno de los repuestos seleccionados está inactivo y no puede ser prestado.')
            if repuesto[1] is not None and quantity > repuesto[1]:
                raise ValueError('La cantidad solicitada supera el stock disponible.')
            items.append((repuesto[0], quantity, price))
            requested_quantities[repuesto[0]] = requested_quantities.get(repuesto[0], 0) + quantity
        if not items:
            raise ValueError('Agrega al menos un repuesto al préstamo.')

        # Una unidad prestada deja de estar disponible desde este momento. La
        # condición evita sobrepasar el stock ante solicitudes concurrentes.
        for id_repuesto, quantity in requested_quantities.items():
            cursor.execute(
                'UPDATE repuesto SET stock = stock - ? '
                'WHERE id_repuesto = ? AND stock >= ?',
                (quantity, id_repuesto, quantity),
            )
            if cursor.rowcount != 1:
                raise ValueError('La cantidad solicitada supera el stock disponible.')

        cursor.execute(
            'SET NOCOUNT ON; '
            'DECLARE @prestamo_insertado TABLE (id_prestamo INT); '
            'INSERT INTO prestamo (fecha_prestamo, fecha_devolucion_estimada, id_cliente, id_usuario, observaciones, id_estado) '
            'OUTPUT INSERTED.id_prestamo INTO @prestamo_insertado (id_prestamo) '
            'VALUES (CAST(GETDATE() AS DATE), ?, ?, ?, ?, 1); '
            'SELECT id_prestamo FROM @prestamo_insertado;',
            (fecha_devolucion, id_cliente, session.get('user_id'), observaciones)
        )
        id_prestamo = cursor.fetchone()[0]
        for id_repuesto, quantity, price in items:
            cursor.execute(
                'INSERT INTO detalle_prestamo (id_prestamo, id_repuesto, observaciones, cantidad, precio) VALUES (?, ?, ?, ?, ?)',
                (id_prestamo, id_repuesto, observaciones, quantity, price)
            )
        conn.commit()
        flash('Préstamo registrado correctamente.', 'success')
    except (TypeError, ValueError) as error:
        conn.rollback()
        flash(str(error), 'error')
    except Exception as error:
        conn.rollback()
        flash(f'No fue posible registrar el préstamo: {error}', 'error')
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('prestamos'))


@app.route('/prestamos/<int:id_prestamo>/estado', methods=['POST'])
def actualizar_estado_prestamo(id_prestamo):
    if not _prestamos_write_allowed():
        flash('No tienes permisos para actualizar préstamos.', 'error')
        return redirect(url_for('prestamos'))
    estado = request.form.get('estado', '').strip().lower()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT p.id_estado, p.id_cliente, ep.estado '
            'FROM prestamo p LEFT JOIN estado_prestamo ep ON ep.id_estado_prestamo = p.id_estado '
            'WHERE p.id_prestamo = ?',
            (id_prestamo,),
        )
        current = cursor.fetchone()
        if not current:
            raise ValueError('No se encontró el préstamo.')
        
        current_estado = current[0]
        id_cliente = current[1]
        current_estado_label = str(current[2] or '').casefold()
        finalized_label = any(
            token in current_estado_label
            for token in ('devuelto', 'pagado', 'transfe')
        )
        can_finalize = not finalized_label and (
            current_estado in (1, 3)
            or any(token in current_estado_label for token in ('activo', 'pendiente', 'vencido'))
        )
        
        if estado == 'devuelto':
            if can_finalize:
                cursor.execute(
                    'SELECT id_repuesto, cantidad FROM detalle_prestamo WHERE id_prestamo = ?', (id_prestamo,)
                )
                for id_repuesto, quantity in cursor.fetchall():
                    cursor.execute(
                        'UPDATE repuesto SET stock = COALESCE(stock, 0) + ? '
                        'WHERE id_repuesto = ?',
                        (quantity, id_repuesto),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError('No se encontró el repuesto original para reintegrarlo al inventario.')
                    registrar_movimiento_inventario(cursor, id_repuesto, 4, quantity, f'Retorno de préstamo P-{id_prestamo:06d}', session.get('user_id'))
                estado_final_id = _prestamo_estado_id(cursor, 'devuelto')
                cursor.execute(
                    'UPDATE prestamo SET id_estado = ? WHERE id_prestamo = ?',
                    (estado_final_id, id_prestamo),
                )
                if cursor.rowcount != 1:
                    raise ValueError('No se pudo actualizar el estado del préstamo.')
                flash('Préstamo marcado como devuelto. Repuestos reintegrados al inventario.', 'success')
            elif 'devuelto' in current_estado_label:
                flash('El préstamo ya está devuelto.', 'info')
            else:
                flash('Un préstamo pagado no puede marcarse como devuelto.', 'error')
                
        elif estado == 'pagado':
            if can_finalize:
                cursor.execute(
                    'SELECT id_repuesto, cantidad, precio FROM detalle_prestamo WHERE id_prestamo = ?', (id_prestamo,)
                )
                items = cursor.fetchall()
                if not items:
                    raise ValueError('El préstamo no tiene repuestos para convertir en venta.')
                
                id_metodo_pago = request.form.get('id_metodo_pago', type=int)
                cursor.execute(
                    'SELECT id_metodo_pago FROM metodo_pago WHERE id_metodo_pago = ?',
                    (id_metodo_pago,),
                )
                metodo_pago = cursor.fetchone()
                if not metodo_pago:
                    raise ValueError('Selecciona un metodo de pago valido.')

                total = sum(float(detail[2] or 0) * (detail[1] or 0) for detail in items)
                observaciones = f'Convertido de préstamo P-{id_prestamo:06d}'
                
                cursor.execute(
                    'SET NOCOUNT ON; '
                    'DECLARE @venta_insertada TABLE (id_venta INT); '
                    'INSERT INTO venta (fecha_venta, id_cliente, id_usuario, total, observaciones, id_metodo_pago, descuento) '
                    'OUTPUT INSERTED.id_venta INTO @venta_insertada (id_venta) '
                    'VALUES (GETDATE(), ?, ?, ?, ?, ?, 0); '
                    'SELECT id_venta FROM @venta_insertada;',
                    (id_cliente, session.get('user_id'), total, observaciones, id_metodo_pago)
                )
                id_venta = cursor.fetchone()[0]
                
                for id_repuesto, quantity, price in items:
                    # El prestamo ya retiro estas unidades del inventario. El
                    # trigger de detalle_venta vuelve a validar y descontar stock,
                    # por eso se acredita la cantidad temporalmente en esta
                    # transaccion; el trigger la descuenta al insertar el detalle.
                    cursor.execute(
                        'UPDATE repuesto SET stock = COALESCE(stock, 0) + ? '
                        'WHERE id_repuesto = ?',
                        (quantity, id_repuesto),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError('No se encontro el repuesto del prestamo para completar la venta.')
                    cursor.execute(
                        'INSERT INTO detalle_venta (id_venta, id_repuesto, precio_unitario, subtotal, cantidad) '
                        'VALUES (?, ?, ?, ?, ?)',
                        (id_venta, id_repuesto, price, float(price or 0) * (quantity or 0), quantity)
                    )
                    registrar_movimiento_inventario(cursor, id_repuesto, 1, quantity, f'Venta por préstamo pagado P-{id_prestamo:06d}', session.get('user_id'))
                
                estado_final_id = _prestamo_estado_id(cursor, 'pagado')
                cursor.execute(
                    'UPDATE prestamo SET id_estado = ? WHERE id_prestamo = ?',
                    (estado_final_id, id_prestamo),
                )
                if cursor.rowcount != 1:
                    raise ValueError('No se pudo actualizar el estado del préstamo.')
                flash(f'Préstamo convertido en venta V-{id_venta:06d}.', 'success')
            elif any(token in current_estado_label for token in ('pagado', 'transfe')):
                flash('El préstamo ya está pagado.', 'info')
            else:
                flash('Un préstamo devuelto no puede convertirse en venta.', 'error')
                
        elif estado in ('activo', 'vencido'):
            target = _prestamo_estado_id(cursor, estado)
            cursor.execute('UPDATE prestamo SET id_estado = ? WHERE id_prestamo = ?', (target, id_prestamo))
            flash('Estado del préstamo actualizado correctamente.', 'success')
        else:
            flash('El estado solicitado no es válido.', 'error')
            return redirect(url_for('prestamos'))
        
        conn.commit()
    except Exception as error:
        conn.rollback()
        flash(f'No fue posible actualizar el préstamo: {error}', 'error')
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('prestamos'))


@app.route('/prestamos/<int:id_prestamo>/eliminar', methods=['POST'])
def eliminar_prestamo(id_prestamo):
    if not _prestamos_write_allowed():
        flash('No tienes permisos para eliminar préstamos.', 'error')
        return redirect(url_for('prestamos'))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT id_repuesto FROM detalle_prestamo WHERE id_prestamo = ?', (id_prestamo,))
        repuesto_ids = [row[0] for row in cursor.fetchall()]
        for id_repuesto in repuesto_ids:
            cursor.execute('UPDATE repuesto SET id_estado_repuesto = 1 WHERE id_repuesto = ?', (id_repuesto,))
        cursor.execute('DELETE FROM prestamo WHERE id_prestamo = ?', (id_prestamo,))
        conn.commit()
        flash('Préstamo eliminado correctamente.', 'success')
    except Exception as error:
        conn.rollback()
        flash(f'No fue posible eliminar el préstamo: {error}', 'error')
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('prestamos'))


@app.route('/prestamos/reporte-pdf')
def exportar_pdf_prestamos():
    if not has_permission_in_session('prestamos', 'read'):
        return redirect(url_for('dashboard'))
    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()
    return_from = request.args.get('return_from', '').strip()
    return_to = request.args.get('return_to', '').strip()
    estado_filter = request.args.get('estado', '').strip().lower()
    cliente_filter = request.args.get('cliente', '').strip().lower()
    vendedor_filter = request.args.get('vendedor', '').strip().lower()
    repuesto_filter = request.args.get('repuesto', '').strip().lower()
    metodo_pago_filter = request.args.get('metodo_pago', '').strip()
    min_total = request.args.get('min_total', '').strip()
    max_total = request.args.get('max_total', '').strip()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT p.id_prestamo, p.fecha_prestamo, p.fecha_devolucion_estimada, p.id_cliente, ep.estado, '
            'COALESCE(SUM(dp.cantidad * dp.precio), 0), p.id_usuario '
            'FROM prestamo p LEFT JOIN estado_prestamo ep ON ep.id_estado_prestamo = p.id_estado '
            'LEFT JOIN detalle_prestamo dp ON dp.id_prestamo = p.id_prestamo '
            'GROUP BY p.id_prestamo, p.fecha_prestamo, p.fecha_devolucion_estimada, p.id_cliente, ep.estado, p.id_usuario '
            'ORDER BY p.fecha_prestamo DESC'
        )
        rows = []
        for row in cursor.fetchall():
            loan_date = str(row[1] or '')[:10]
            return_date = str(row[2] or '')[:10]
            loan_state = str(row[4] or 'Activo').strip().lower()
            client_name = get_cliente_full_name(cursor, row[3]) or 'Sin cliente frecuente'
            vendedor_name = get_user_full_name(cursor, row[6]) or get_display_name_by_id(cursor, ['usuario'], row[6]) or 'Sistema'
            cursor.execute(
                'SELECT dp.id_repuesto, r.codigo_repuesto '
                'FROM detalle_prestamo dp LEFT JOIN repuesto r ON r.id_repuesto = dp.id_repuesto '
                'WHERE dp.id_prestamo = ?', (row[0],)
            )
            repuesto_text = ' '.join(
                ' '.join(str(value or '') for value in detail)
                + ' ' + str(get_repuesto_display_name(cursor, detail[0]) or '')
                for detail in cursor.fetchall()
            ).lower()
            total_amount = float(row[5] or 0)
            if from_date and loan_date < from_date:
                continue
            if to_date and loan_date > to_date:
                continue
            if return_from and return_date < return_from:
                continue
            if return_to and return_date > return_to:
                continue
            if estado_filter and loan_state != estado_filter:
                continue
            if cliente_filter and cliente_filter not in client_name.lower():
                continue
            if vendedor_filter and vendedor_filter not in vendedor_name.lower():
                continue
            if repuesto_filter and repuesto_filter not in repuesto_text:
                continue
            if metodo_pago_filter and metodo_pago_filter != str(metodo_pago_filter):
                continue
            if min_total and total_amount < float(min_total):
                continue
            if max_total and total_amount > float(max_total):
                continue
            rows.append((row[0], row[1], row[2], row[4], row[5], vendedor_name))
    finally:
        cursor.close()
        conn.close()
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle('Reporte de Préstamos - Importadora Morales')
    draw_table_pdf(pdf, 'Reporte de Préstamos — Importadora Morales', ['N° Préstamo', 'Fecha', 'Devolución estimada', 'Estado', 'Vendedor', 'Valor'], [[f'P-{r[0]:06d}', str(r[1])[:10], str(r[2])[:10], r[3] or '-', r[5] or '-', f'Q {float(r[4] or 0):,.2f}'] for r in rows])
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name='reporte_prestamos.pdf', mimetype='application/pdf')


@app.route('/devoluciones/nueva', methods=['POST'])
def nueva_devolucion():
    if not _devoluciones_write_allowed():
        flash('No tienes permisos para registrar devoluciones.', 'error')
        return redirect(url_for('devoluciones'))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        id_venta = request.form.get('id_venta', type=int)
        repuesto_ids = request.form.getlist('id_repuesto[]')
        quantities = request.form.getlist('cantidad[]')
        motives = request.form.getlist('id_motivo[]')
        if not id_venta:
            raise ValueError('Selecciona la venta que deseas devolver.')

        items = []
        total = Decimal('0.00')
        for index, repuesto_id in enumerate(repuesto_ids):
            if not repuesto_id:
                continue
            if index >= len(quantities):
                continue
            quantity = int(quantities[index])
            if quantity < 1:
                continue
            if index >= len(motives):
                raise ValueError('Falta el motivo de devolución de al menos un repuesto.')
            motivo_id = int(motives[index])
            cursor.execute(
                'SELECT dv.id_repuesto, dv.cantidad, dv.precio_unitario, '
                'COALESCE((SELECT SUM(dd.cantidad) FROM detalle_devolucion dd '
                'INNER JOIN devolucion d ON d.id_devolucion = dd.id_devolucion '
                'WHERE d.id_venta = dv.id_venta AND dd.id_repuesto = dv.id_repuesto), 0) '
                'FROM detalle_venta dv WHERE dv.id_venta = ? AND dv.id_repuesto = ?',
                (id_venta, repuesto_id)
            )
            detail = cursor.fetchone()
            if not detail:
                raise ValueError('La cantidad devuelta supera la cantidad disponible de la venta.')
            cantidad_devuelta = int(detail[3] or 0)
            cantidad_vendida = int(detail.cantidad or 0)
            if cantidad_vendida < cantidad_devuelta:
                cantidad_vendida += cantidad_devuelta * 2
            cantidad_disponible = cantidad_vendida - cantidad_devuelta
            if quantity > cantidad_disponible:
                raise ValueError('La cantidad devuelta supera la cantidad disponible de la venta.')
            price = Decimal(str(detail.precio_unitario or 0)).quantize(Decimal('0.01'))
            subtotal = (price * quantity).quantize(Decimal('0.01'))
            items.append((detail.id_repuesto, quantity, motivo_id, price, subtotal, cantidad_vendida))
            total += subtotal
        if not items:
            raise ValueError('Selecciona al menos un repuesto para devolver.')

        cursor.execute(
            'INSERT INTO devolucion (id_venta, fecha_devolucion, id_usuario, total_devuelto) '
            'OUTPUT INSERTED.id_devolucion VALUES (?, GETDATE(), ?, ?)',
            (id_venta, session.get('user_id'), total)
        )
        id_devolucion = cursor.fetchone()[0]
        for id_repuesto, quantity, motivo_id, price, subtotal, cantidad_vendida in items:
            cursor.execute(
                'INSERT INTO detalle_devolucion (id_devolucion, id_repuesto, cantidad, id_motivo, precio_unitario, subtotal) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (id_devolucion, id_repuesto, quantity, motivo_id, price, subtotal)
            )
            cursor.execute(
                'UPDATE detalle_venta SET cantidad = ?, subtotal = ? WHERE id_venta = ? AND id_repuesto = ?',
                (cantidad_vendida, cantidad_vendida * float(price), id_venta, id_repuesto)
            )
            registrar_movimiento_inventario(cursor, id_repuesto, 4, quantity, f'Devolucion de venta V-{id_venta:06d}', session.get('user_id'))
        cursor.execute(
            'SELECT COUNT(*) FROM detalle_venta dv '
            'WHERE dv.id_venta = ? AND dv.cantidad > '
            '(SELECT COALESCE(SUM(dd.cantidad), 0) FROM detalle_devolucion dd '
            'INNER JOIN devolucion d ON d.id_devolucion = dd.id_devolucion '
            'WHERE d.id_venta = dv.id_venta AND dd.id_repuesto = dv.id_repuesto)',
            (id_venta,),
        )
        has_items_not_fully_returned = cursor.fetchone()[0] > 0
        cursor.execute(
            'UPDATE venta SET id_estado_venta = ? WHERE id_venta = ?',
            (1 if has_items_not_fully_returned else 2, id_venta),
        )
        conn.commit()
        flash('Devolución registrada correctamente.', 'success')
    except (TypeError, ValueError) as error:
        conn.rollback()
        flash(str(error), 'error')
    except Exception as error:
        conn.rollback()
        flash(f'No fue posible registrar la devolución: {error}', 'error')
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('devoluciones'))


@app.route('/devoluciones/eliminar/<int:id_devolucion>', methods=['POST'])
def eliminar_devolucion(id_devolucion):
    if not _devoluciones_write_allowed():
        flash('No tienes permisos para eliminar devoluciones.', 'error')
        return redirect(url_for('devoluciones'))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT id_repuesto, cantidad, id_venta FROM detalle_devolucion d JOIN devolucion h ON h.id_devolucion = d.id_devolucion WHERE d.id_devolucion = ?', (id_devolucion,))
        items = cursor.fetchall()
        if not items:
            raise ValueError('No se encontró la devolución.')
        for id_repuesto, quantity, id_venta in items:
            cursor.execute('UPDATE repuesto SET stock = stock - ? WHERE id_repuesto = ?', (quantity, id_repuesto))
        cursor.execute('DELETE FROM detalle_devolucion WHERE id_devolucion = ?', (id_devolucion,))
        cursor.execute('DELETE FROM devolucion WHERE id_devolucion = ?', (id_devolucion,))
        conn.commit()
        flash('Devolución eliminada y stock restaurado.', 'success')
    except Exception as error:
        conn.rollback()
        flash(f'No fue posible eliminar la devolución: {error}', 'error')
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for('devoluciones'))


@app.route('/devoluciones/reporte-pdf')
def exportar_pdf_devoluciones():
    if not has_permission_in_session('devoluciones', 'read'):
        return redirect(url_for('dashboard'))
    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()
    cliente = request.args.get('cliente', '').strip().lower()
    vendedor = request.args.get('vendedor', '').strip().lower()
    repuesto = request.args.get('repuesto', '').strip().lower()
    motivo = request.args.get('motivo', '').strip()
    estado_venta = request.args.get('estado_venta', '').strip()
    min_total = request.args.get('min_total', '').strip()
    max_total = request.args.get('max_total', '').strip()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT d.id_devolucion, d.id_venta, d.fecha_devolucion, d.total_devuelto, '
            'dv.id_repuesto, dv.cantidad, dv.subtotal, r.codigo_repuesto, '
            'dv.id_motivo, m.motivo AS motivo_nombre, '
            'v.id_cliente, v.id_usuario, v.id_estado_venta, '
            'c.primer_nombre, c.segundo_nombre, c.primer_apellido, c.segundo_apellido, c.nit, c.correo '
            'FROM devolucion d '
            'LEFT JOIN detalle_devolucion dv ON dv.id_devolucion = d.id_devolucion '
            'LEFT JOIN repuesto r ON r.id_repuesto = dv.id_repuesto '
            'LEFT JOIN motivo_devolucion m ON m.id_motivo = dv.id_motivo '
            'LEFT JOIN venta v ON v.id_venta = d.id_venta '
            'LEFT JOIN cliente c ON c.id_cliente = v.id_cliente '
            'ORDER BY d.fecha_devolucion DESC'
        )
        raw_rows = cursor.fetchall()
        rows = []
        for row in raw_rows:
            dev_date = str(row[2] or '')[:10]
            if from_date and dev_date < from_date:
                continue
            if to_date and dev_date > to_date:
                continue
            client_name = ' '.join(
                str(part).strip() for part in (row[13], row[14], row[15], row[16])
                if part is not None and str(part).strip()
            )
            if not client_name:
                client_name = str(row[17] or row[18] or f'Cliente #{row[10] or "-"}')
            if cliente and cliente not in client_name.lower():
                continue
            vendedor_name = get_user_full_name(cursor, row[11]) or get_display_name_by_id(cursor, ['usuario'], row[11]) or 'Sistema'
            if vendedor and vendedor not in vendedor_name.lower():
                continue
            repuesto_text = f'{row[7] or ""} {get_repuesto_display_name(cursor, row[4]) or ""}'.lower()
            if repuesto and repuesto not in repuesto_text:
                continue
            if motivo and str(row[8] or '') != str(motivo):
                continue
            if estado_venta and str(row[12] or '1') != estado_venta:
                continue
            subtotal = float(row[6] or 0)
            if min_total and subtotal < float(min_total):
                continue
            if max_total and subtotal > float(max_total):
                continue
            rows.append((
                row[0], row[1], row[2], row[4], row[7],
                get_repuesto_display_name(cursor, row[4]) or '-',
                client_name, vendedor_name, row[5], row[6], row[9],
            ))
    finally:
        cursor.close()
        conn.close()
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setTitle('Reporte de Devoluciones - Importadora Morales')
    draw_table_pdf(
        pdf, 'Reporte de Devoluciones - Importadora Morales',
        ['No. Devolucion', 'No. Venta', 'Fecha', 'Repuesto', 'Codigo', 'Cliente', 'Vendedor', 'Cantidad', 'Total (Q)', 'Motivo'],
        [[
            f'D-{row[0]:06d}', f'V-{row[1]:06d}', str(row[2])[:10],
            row[5], row[4] or '-', row[6] or '-', row[7] or '-',
            str(row[8]), f'Q {float(row[9] or 0):,.2f}', row[10] or '-',
        ] for row in rows],
    )
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name='reporte_devoluciones.pdf', mimetype='application/pdf')

@app.route('/keep-alive', methods=['POST'])
def keep_alive():
    """Endpoint para mantener la sesión activa mientras la pestaña está abierta."""
    if 'user' in session:
        session.modified = True  # Actualiza el timestamp de la sesión
        return jsonify({'status': 'ok'})
    return jsonify({'status': 'expired'}), 401


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)

