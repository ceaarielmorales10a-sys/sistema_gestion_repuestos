from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file
import os
import io
from uuid import uuid4
from datetime import datetime
import pyodbc
from werkzeug.utils import secure_filename
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle

# Buscamos la ruta absoluta de run.py
base_dir = os.path.abspath(os.path.dirname(__file__))

# Definimos las rutas exactas apuntando a la subcarpeta 'app'
templates_dir = os.path.join(base_dir, 'app', 'templates')
static_dir = os.path.join(base_dir, 'app', 'static')

# Inicializamos Flask vinculando correctamente la subcarpeta 'app'
app = Flask('app', template_folder=templates_dir, static_folder=static_dir)
app.secret_key = 'clave_secreta_umg_2026'
UPLOAD_REPUESTOS_DIR = os.path.join(static_dir, 'uploads', 'repuestos')
ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
MAX_IMAGE_SIZE = 3 * 1024 * 1024


@app.before_request
def restringir_modulos_bodeguero():
    """Limita al bodeguero a los módulos asignados, aun si escribe una URL."""
    ruta = request.path.rstrip('/') or '/'
    if session.get('role') == 'Vendedor' and ruta == '/desarme':
        flash('No tienes permisos para acceder al módulo de desarme.', 'error')
        return redirect(url_for('dashboard'))

    if session.get('role') != 'Bodeguero':
        return None

    rutas_permitidas = ('/', '/dashboard', '/analytics', '/desarme', '/logout')
    if ruta in rutas_permitidas or ruta.startswith('/inventario') or ruta.startswith('/static/'):
        return None

    flash('No tienes permisos para acceder a este módulo.', 'error')
    return redirect(url_for('inventario'))

# CADENA DE CONEXIÓN A SQL SERVER
CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=ARIEL2021\\SQLEXPRESS01;"
    "Database=Sistema_repuestos;"
    "UID=sa;"
    "PWD=Arielmorales;"
)


def get_db_connection():
    """Devuelve una conexión pyodbc usando la cadena CONN_STR."""
    return pyodbc.connect(CONN_STR)


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


def get_table_columns(cursor, table_name):
    try:
        cursor.execute(f"SELECT TOP 1 * FROM {table_name}")
        return [col[0] for col in cursor.description]
    except Exception:
        return []


def choose_display_column(columns):
    # Prefer columns that contain these tokens
    preferred = ['nombre', 'tipo', 'marca', 'modelo', 'color', 'lado', 'posicion', 'descripcion', 'name']
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


def fetch_catalog_for_candidates(cursor, candidates):
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
        if id_col and display_col:
            try:
                cursor.execute(f"SELECT {id_col} AS id, {display_col} AS nombre FROM {table} ORDER BY {display_col}")
                return cursor.fetchall()
            except Exception:
                continue
    return []


def fetch_modelos_by_marca(cursor, id_marca):
    try:
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
    if id_cliente is None:
        return None
    try:
        cursor.execute(
            "SELECT primer_nombre, segundo_nombre, primer_apellido, segundo_apellido "
            "FROM cliente WHERE id_cliente = ?",
            (id_cliente,)
        )
        row = cursor.fetchone()
        if row:
            parts = [p for p in [row[0], row[1], row[2], row[3]] if p]
            if parts:
                return ' '.join(parts)
    except Exception:
        pass
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
    """Valida y guarda una imagen de repuesto, devolviendo su ruta relativa."""
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

    os.makedirs(UPLOAD_REPUESTOS_DIR, exist_ok=True)
    photo_name = f"{uuid4().hex}{extension}"
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
                        usuario_nombre = (user_row[0] if user_row[0] else ' '.join(nombre_parts))
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

def validar_usuario_db(usuario, password):
    try:
        conn = pyodbc.connect(CONN_STR)
        cursor = conn.cursor()
        
        query = (
            "SELECT u.id_usuario, u.username, u.primer_nombre, u.segundo_nombre, "
            "u.primer_apellido, u.segundo_apellido, u.id_rol, r.nombre "
            "FROM usuario u "
            "LEFT JOIN rol r ON u.id_rol = r.id_rol "
            "WHERE u.username = ? AND u.password_hash = ? AND u.id_estado = 1"
        )
        cursor.execute(query, (usuario, password))
        row = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if row:
            rol_nombre = row[7] or ''
            rol_upper = rol_nombre.strip().upper()
            if rol_upper.startswith('ADMIN'):
                rol_texto = 'Administrador del Sistema'
            elif rol_upper.startswith('VENDEDOR'):
                rol_texto = 'Vendedor'
            elif rol_upper.startswith('BODEGUERO'):
                rol_texto = 'Bodeguero'
            elif rol_upper.startswith('ALMACEN'):
                rol_texto = 'Almacenista'
            else:
                rol_texto = rol_nombre.title() if rol_nombre else 'Usuario'

            # Construir nombre completo con los cuatro campos
            partes = [row[2], row[3], row[4], row[5]]
            full_name = " ".join([p for p in partes if p])
            
            return {
                'id': row[0],
                'user': row[1],
                'full_name': full_name or row[1],
                'nombres': f"{row[2]} {row[3]}".strip(),
                'apellidos': f"{row[4]} {row[5]}".strip(),
                'role': rol_texto
            }
        return None
        
    except Exception as e:
        print("\n" + "="*50)
        print(f"⚠️ ERROR DE CONEXIÓN CON SQL SERVER: {e}")
        print("="*50 + "\n")
        return None

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


def get_current_sidebar_user():
    """Obtiene nombre y rol actualizados para el pie del menú lateral."""
    sidebar_user = {'name': session.get('user', 'Usuario'), 'role': session.get('role', 'Usuario')}
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
            sidebar_user['name'] = ' '.join(part for part in row[:4] if part) or row[4] or sidebar_user['name']
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


def get_repuestos_reporte(from_date=None, to_date=None, tipo_repuesto=None, marca=None, estado_repuesto=None, ubicacion=None, stock_bajo_only=False):
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
    stock_bajo_only = request.args.get('stock_bajo', '').strip().lower() in ('1', 'true', 'yes', 'on')
    repuestos = get_repuestos_reporte(
        from_date=from_date or None,
        to_date=to_date or None,
        tipo_repuesto=tipo_repuesto or None,
        marca=marca or None,
        estado_repuesto=estado_repuesto or None,
        ubicacion=ubicacion or None,
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
    pdf = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    rows = [
        [
            str(item.get('id_movimiento') or '-'),
            str(item.get('repuesto_nombre') or item.get('id_repuesto') or '-'),
            str(item.get('tipo_movimiento') or '-'),
            str(item.get('fecha_movimiento') or '-'),
            str(item.get('cantidad') or 0),
            str(item.get('detalle') or '-'),
            str(item.get('usuario') or '-'),
            str(item.get('estado') or '-'),
        ]
        for item in movimientos
    ]

    pdf.setTitle('Reporte de Movimientos - Importadora Morales')
    headers = ['ID Movimiento', 'Repuesto', 'Tipo', 'Fecha', 'Cantidad', 'Detalle', 'Usuario', 'Estado']
    if from_date or to_date:
        pdf.setFont('Helvetica', 10)
        pdf.drawString(60, height - 90, f'Filtro: {from_date or "Inicio"} - {to_date or "Hoy"}')
    draw_table_pdf(pdf, 'Reporte de Movimientos — Importadora Morales', headers, rows)

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name='reporte_movimientos.pdf', mimetype='application/pdf')


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
        cursor.execute("SELECT id_rol AS id, nombre FROM rol ORDER BY nombre")
        roles = cursor.fetchall()
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

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO usuario (username, password_hash, primer_nombre, segundo_nombre, primer_apellido, segundo_apellido, correo, telefono, id_rol, id_estado, fecha_creacion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE())",
                (
                    username,
                    password,
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

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id_rol AS id, nombre FROM rol ORDER BY nombre")
        roles = cursor.fetchall()

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
            password = request.form.get('password', '').strip() or usuario.get('password_hash') or ''
            primer_nombre = request.form.get('primer_nombre', '').strip()
            segundo_nombre = request.form.get('segundo_nombre', '').strip()
            primer_apellido = request.form.get('primer_apellido', '').strip()
            segundo_apellido = request.form.get('segundo_apellido', '').strip()
            email = request.form.get('email', '').strip()
            telefono = request.form.get('telefono', '').strip()
            rol_id = request.form.get('rol_id', '')
            activo = request.form.get('activo') == 'on'

            if not username or not primer_nombre or not primer_apellido or not email or not rol_id:
                flash('Completa los campos obligatorios.', 'error')
                return render_template('modificar_usuario.html', roles=roles, usuario=usuario, user_name=session.get('user', 'Usuario'))

            cursor.execute(
                "UPDATE usuario SET username = ?, password_hash = ?, primer_nombre = ?, segundo_nombre = ?, primer_apellido = ?, segundo_apellido = ?, correo = ?, telefono = ?, id_rol = ?, id_estado = ? WHERE id_usuario = ?",
                (
                    username,
                    password,
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

    if session.get('role') != 'Administrador del Sistema':
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
            "LEFT JOIN rol r ON u.id_rol = r.id_rol"
        )
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description]

        cursor.execute("SELECT id_rol AS id, nombre FROM rol ORDER BY nombre")
        roles = cursor.fetchall()
        for row in rows:
            data = dict(zip(cols, row))
            # Build full name from primer/segundo nombre y apellidos
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

    sidebar_user = get_current_sidebar_user()
    return render_template('usuario.html', usuarios=usuarios, roles=roles,
                           user_name=sidebar_user['name'], user_role=sidebar_user['role'])

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
            session['user_id'] = cuenta_valida['id']
            session['user'] = cuenta_valida['user']
            session['full_name'] = cuenta_valida['full_name']
            session['nombres'] = cuenta_valida['nombres']
            session['apellidos'] = cuenta_valida['apellidos']
            session['role'] = cuenta_valida['role']
            return redirect(url_for('dashboard'))
        else:
            flash('Usuario o contraseña incorrectos o error de conexión.', 'error')
            return redirect(url_for('login'))
            
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    # Filtro de seguridad: Si no hay sesión, regresa al login obligatoriamente
    if 'user' not in session:
        return redirect(url_for('login'))
        
    user_name = session.get('user', 'Usuario')
    if 'user_id' in session:
        nombre_db = get_user_full_name_by_id(session['user_id'])
        if nombre_db:
            user_name = nombre_db
    # Renderiza la plantilla única; los bloques {% if %} de Jinja ocultarán/mostrarán los módulos
    return render_template('menu_principal.html', user_name=user_name)


# Catálogos que se administran desde el módulo de mantenimiento.  Se prueban
# nombres alternos porque hay instalaciones antiguas de la base de datos con
# una nomenclatura ligeramente distinta.
MANTENIMIENTO_CATALOGOS = [
    ('anios', 'Años', 'fa-calendar-days', ['anio_vehiculo', 'anio']),
    ('marcas', 'Marcas', 'fa-car', ['marca_vehiculo', 'marca']),
    ('modelos', 'Modelos', 'fa-chevron-right', ['modelo_vehiculo', 'modelo']),
    ('colores', 'Colores', 'fa-palette', ['color', 'color_repuesto']),
    ('tipos', 'Tipos de Repuesto', 'fa-screwdriver-wrench', ['tipo_repuesto']),
    ('estados', 'Estados', 'fa-tag', ['estado_general', 'estado_repuesto']),
    ('origenes', 'Origen Vehículo', 'fa-location-dot', ['vehiculo_origen']),
    ('transmisiones', 'Transmisiones', 'fa-gears', ['transmision', 'transmision_vehiculo']),
    ('ubicaciones', 'Ubicaciones', 'fa-map-pin', ['ubicacion', 'bodega', 'ubicacion_bodega']),
    ('roles', 'Roles', 'fa-book-open', ['rol']),
    ('estado_desarme', 'Estado de Desarme', 'fa-truck-ramp-box', ['estado_desarme']),
    ('tipo_desarme', 'Tipo de Desarme', 'fa-hammer', ['tipo_desarme']),
    ('estado_pieza', 'Estado de Pieza', 'fa-box-open', ['estado_pieza']),
    ('motivo_incidencia', 'Motivo de Incidencia', 'fa-circle-exclamation', ['motivo_incidencia']),
]


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
                        'table': table, 'id_column': id_column, 'display_column': display_column}
    return None


@app.route('/mantenimiento')
def mantenimiento():
    if 'user' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'Administrador del Sistema':
        flash('No tienes permisos para acceder al módulo de mantenimiento.', 'error')
        return redirect(url_for('dashboard'))

    catalogos, total_registros = [], 0
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
                cursor.execute(f"SELECT * FROM {catalogo['table']} ORDER BY {catalogo['display_column']}")
                column_names = [column[0] for column in cursor.description]
                rows = []
                for raw_row in cursor.fetchall():
                    item = dict(zip(column_names, raw_row))
                    marca_id = item.get(marca_column) if marca_column else None
                    rows.append({
                        'id': item.get(catalogo['id_column']),
                        'nombre': item.get(catalogo['display_column']),
                        'marca_id': marca_id,
                        'marca': get_display_name_by_id(cursor, ['marca_vehiculo', 'marca'], marca_id) or 'Sin marca',
                        'descripcion': item.get(descripcion_column) if descripcion_column else '',
                    })
                catalogo['marcas'] = [
                    {'id': row[0], 'nombre': row[1]}
                    for row in fetch_catalog_for_candidates(cursor, ['marca_vehiculo', 'marca'])
                ]
                catalogo['rows'] = rows
            else:
                cursor.execute(
                    f"SELECT {catalogo['id_column']} AS id, {catalogo['display_column']} AS nombre "
                    f"FROM {catalogo['table']} ORDER BY {catalogo['display_column']}"
                )
                rows = cursor.fetchall()
                catalogo['rows'] = [{'id': row[0], 'nombre': row[1]} for row in rows]
            catalogo['count'] = len(rows)
            total_registros += len(rows)
            catalogos.append(catalogo)
        cursor.close()
        conn.close()
    except Exception as error:
        flash(f'No fue posible cargar los catálogos: {error}', 'error')

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
    return render_template('mantenimiento.html', catalogos=catalogos, total_registros=total_registros,
                           tablas_sistema=tablas_sistema, user_name=sidebar_user['name'], user_role=sidebar_user['role'])


@app.route('/mantenimiento/catalogo/<key>', methods=['POST'])
def guardar_catalogo_mantenimiento(key):
    if 'user' not in session or session.get('role') != 'Administrador del Sistema':
        flash('No tienes permisos para modificar catálogos.', 'error')
        return redirect(url_for('dashboard'))

    action = request.form.get('action', 'add')
    value = request.form.get('nombre', '').strip()
    row_id = request.form.get('id', '').strip()
    if action != 'delete' and not value:
        flash('Ingresa un valor para el catálogo.', 'error')
        return redirect(url_for('mantenimiento'))
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        catalogo = get_mantenimiento_catalogo(key, cursor)
        if not catalogo:
            raise ValueError('El catálogo seleccionado no está disponible.')
        table, id_column, display_column = catalogo['table'], catalogo['id_column'], catalogo['display_column']
        if key == 'modelos' and action != 'delete':
            marca_id = request.form.get('id_marca', '').strip()
            if not marca_id:
                raise ValueError('Selecciona la marca del modelo.')
            columns = get_table_columns(cursor, table)
            descripcion_column = next((c for c in columns if 'descripcion' in c.lower() or 'observacion' in c.lower()), None)
            model_data = {display_column: value, 'id_marca': marca_id}
            if descripcion_column:
                model_data[descripcion_column] = request.form.get('descripcion', '').strip() or None
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
        elif action == 'add':
            cursor.execute(f'INSERT INTO {table} ({display_column}) VALUES (?)', (value,))
            message = f'{catalogo["label"]}: registro agregado correctamente.'
        elif action == 'edit' and row_id:
            cursor.execute(f'UPDATE {table} SET {display_column} = ? WHERE {id_column} = ?', (value, row_id))
            message = f'{catalogo["label"]}: registro actualizado correctamente.'
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
    return redirect(url_for('mantenimiento'))

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

        # Note: some schemas used to have an `id_estado` column which was removed in
        # newer schemas. We avoid referencing that non-existent column here and
        # prefer `id_estado_repuesto` which exists on current installations.
        repuesto_columns = get_table_columns(cursor, 'repuesto')
        photo_select = ', fotografia' if any(column.lower() == 'fotografia' for column in repuesto_columns) else ''
        base_sql = (
            "SELECT id_repuesto, codigo_repuesto, id_vehiculo_origen, id_tipo_repuesto, "
            "id_marca, id_modelo, id_anio, id_color, id_lado, id_posicion, id_estado_repuesto, "
            "id_ubicacion, precio_compra, precio_venta, observaciones, fecha_ingreso, "
            "id_usuario_registro, stock, stock_minimo, stock_maximo "
            f"{photo_select} FROM repuesto"
        )
        params = []
        if selected_tipo_id:
            try:
                params.append(int(selected_tipo_id))
            except ValueError:
                params.append(selected_tipo_id)
            base_sql += " WHERE id_tipo_repuesto = ?"
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
            usuario_registro = get_display_name_by_id(cursor, ['usuario'], data.get('id_usuario_registro')) or '-'
            # Prefer the explicit repuesto estado FK if present; fall back to any
            # legacy `id_estado` if it exists in the schema (some installs may
            # still have it). This avoids SQL errors when `id_estado` column
            # is not present in the `repuesto` table.
            estado_fk = data.get('id_estado_repuesto') or data.get('id_estado')
            estado_general = get_display_name_by_id(cursor, ['estado_general', 'estado_repuesto'], estado_fk) or '-'
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
            search_lower = search_query.lower()
            def matches_search(repuesto):
                return any(
                    search_lower in str(repuesto.get(field, '')).lower()
                    for field in [
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
                )
            lista_repuestos = [r for r in lista_repuestos if matches_search(r)]
    except Exception as e:
        print(f"\n⚠️ ERROR EN CONSULTA DE INVENTARIO: {e}\n")
        flash(f"Error al conectar con la base de datos: {e}", "error")
        tipo_repuestos = []
        selected_tipo_id = ''
        search_query = ''

    # Paginación simple: 7 repuestos por página
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

    ingresos_count = sum(1 for m in movimientos if str(m.get('tipo_movimiento', '')).lower() == 'ingreso')
    ventas_count = sum(1 for m in movimientos if str(m.get('tipo_movimiento', '')).lower() == 'venta')
    prestamos_count = sum(1 for m in movimientos if str(m.get('tipo_movimiento', '')).lower() == 'prestamo')
    devoluciones_count = sum(1 for m in movimientos if str(m.get('tipo_movimiento', '')).lower() == 'devolucion')
    
    try:
        conn2 = get_db_connection()
        cursor2 = conn2.cursor()
        tipos_movimiento_db = get_all_tipos_movimiento(cursor2)
        cursor2.close()
        conn2.close()
        movimientos_tipos = [t.get('nombre') for t in tipos_movimiento_db]
    except Exception:
        movimientos_tipos = sorted({str(m.get('tipo_movimiento') or '').strip() for m in movimientos if str(m.get('tipo_movimiento') or '').strip()})
    
    movimientos_usuarios = sorted({str(m.get('usuario') or '').strip() for m in movimientos if str(m.get('usuario') or '').strip()})

    page_size = 7
    total_items = len(lista_repuestos)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    if current_page < 1:
        current_page = 1
    if current_page > total_pages:
        current_page = total_pages

    stock_bajo = sum(1 for repuesto in lista_repuestos if repuesto.get('stock', 0) <= 5)
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

    start_index = (current_page - 1) * page_size
    end_index = start_index + page_size
    repuestos_paginados = lista_repuestos[start_index:end_index]

    sidebar_user = get_current_sidebar_user()
    return render_template(
        'inventario.html',
        repuestos=repuestos_paginados,
        report_repuestos=lista_repuestos,
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
        movimientos=movimientos,
        movimientos_tipos=movimientos_tipos,
        movimientos_usuarios=movimientos_usuarios,
        ingresos_count=ingresos_count,
        ventas_count=ventas_count,
        prestamos_count=prestamos_count,
        devoluciones_count=devoluciones_count,
        selected_tipo_id=selected_tipo_id,
        search_query=search_query,
        user_name=sidebar_user['name'],
        user_role=sidebar_user['role']
    )


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
            stock_maximo = request.form.get('stock_maximo') or None
            id_anio = request.form.get('id_anio')
            id_estado_repuesto = request.form.get('id_estado_repuesto')
            id_ubicacion = request.form.get('id_ubicacion') or request.form.get('id_bodega') or None
            id_bodega = id_ubicacion
            precio_compra = request.form.get('precio_compra')
            precio_venta = request.form.get('precio_venta')
            stock = request.form.get('stock')
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
                'id_estado': id_estado
            }

            insert_row_filtered(cursor, 'repuesto', insert_data)
            
            conn.commit()

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
                    conn.commit()
                except Exception:
                    pass

            flash('¡Repuesto guardado exitosamente en el inventario!', 'success')
            return redirect(url_for('inventario'))

        except Exception as e:
            conn.rollback()
            flash(f'Error al guardar el repuesto: {str(e)}', 'error')
            return redirect(url_for('agregar_repuesto'))
        
        finally:
            cursor.close()
            conn.close()

    else:
        # MÉTODO GET: Mapeado exactamente con las columnas reales de tus tablas
        try:
                # Use dynamic catalog fetcher with candidate table names to avoid column-name mismatches
                vehiculo_origen_candidates = ['vehiculo_origen']
                vehiculos_origen = fetch_catalog_for_candidates(cursor, vehiculo_origen_candidates) or []

                tipo_candidates = ['tipo_repuesto', 'tipo', 'tipo_repuesto_vehiculo']
                tipos = fetch_catalog_for_candidates(cursor, tipo_candidates) or []

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
                'stock_maximo': request.form.get('stock_maximo') or None,
                'id_anio': request.form.get('id_anio'),
                'id_estado_repuesto': request.form.get('id_estado_repuesto'),
                'id_ubicacion': request.form.get('id_ubicacion') or request.form.get('id_bodega') or None,
                'id_bodega': request.form.get('id_ubicacion') or request.form.get('id_bodega') or None,
                'precio_compra': request.form.get('precio_compra'),
                'precio_venta': request.form.get('precio_venta'),
                'stock': request.form.get('stock'),
                'observaciones': request.form.get('observaciones') or None,
            }
            photo_file = get_uploaded_repuesto_photo()
            if photo_file and photo_file.filename:
                ensure_repuesto_photo_column(cursor)
                update_data['fotografia'] = save_repuesto_photo(photo_file)
            columns = get_table_columns(cursor, 'repuesto')
            set_cols = [col for col in update_data if col in columns]
            if set_cols:
                set_clause = ', '.join([f"{col} = ?" for col in set_cols])
                params = [update_data[col] for col in set_cols] + [id_repuesto]
                cursor.execute(f"UPDATE repuesto SET {set_clause} WHERE id_repuesto = ?", tuple(params))
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
        vehiculos_origen = fetch_catalog_for_candidates(cursor, vehiculo_origen_candidates) or []

        tipo_candidates = ['tipo_repuesto', 'tipo', 'tipo_repuesto_vehiculo']
        tipos = fetch_catalog_for_candidates(cursor, tipo_candidates) or []

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

@app.route('/ventas')
def ventas():
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    ventas = []
    total_items = 0
    current_page = 1
    total_pages = 1

    try:
        repuesto_display_col = 'codigo_repuesto'
        repuesto_cols = get_table_columns(cursor, 'repuesto')
        if repuesto_cols:
            detected = choose_display_column(repuesto_cols)
            if detected:
                repuesto_display_col = detected

        cursor.execute(
            f"SELECT v.id_venta, v.fecha_venta, v.id_cliente, v.id_usuario, v.total, v.observaciones, v.id_estado, v.id_metodo_pago, v.descuento, "
            f"dv.id_detalle_venta, dv.id_repuesto, dv.precio_unitario, dv.subtotal, dv.cantidad, "
            f"r.{repuesto_display_col} AS repuesto_nombre "
            "FROM venta v "
            "LEFT JOIN detalle_venta dv ON dv.id_venta = v.id_venta "
            "LEFT JOIN repuesto r ON r.id_repuesto = dv.id_repuesto "
            "ORDER BY v.fecha_venta DESC"
        )
        rows = cursor.fetchall()
        cols = [c[0] for c in cursor.description]

        for row in rows:
            data = dict(zip(cols, row))
            fecha_venta = data.get('fecha_venta') or data.get('fecha') or data.get('fecha_registro') or '-'
            raw_cliente = data.get('id_cliente') or data.get('cliente')
            raw_usuario = data.get('id_usuario') or data.get('usuario')
            raw_estado = data.get('id_estado')
            raw_metodo_pago = data.get('id_metodo_pago')
            cliente = get_cliente_full_name(cursor, data.get('id_cliente')) or raw_cliente or '-'
            usuario = lookup_foreign_key_display(cursor, 'id_usuario', data.get('id_usuario'), ['usuario']) or raw_usuario or '-'
            estado = lookup_foreign_key_display(cursor, 'id_estado', raw_estado, ['estado']) or raw_estado or '-'
            metodo_pago = lookup_foreign_key_display(cursor, 'id_metodo_pago', raw_metodo_pago, ['metodo_pago', 'forma_pago']) or raw_metodo_pago or '-'
            repuesto = get_repuesto_display_name(cursor, data.get('id_repuesto')) or data.get('repuesto_nombre') or data.get('id_repuesto') or '-'

            ventas.append({
                'id_venta': data.get('id_venta'),
                'fecha_venta': fecha_venta,
                'cliente': cliente,
                'usuario': usuario,
                'repuesto': repuesto,
                'precio_unitario': data.get('precio_unitario') or 0.0,
                'subtotal': data.get('subtotal') or 0.0,
                'cantidad': data.get('cantidad') or 0,
                'descuento': data.get('descuento') or 0.0,
                'total': data.get('total') or 0.0,
                'observaciones': data.get('observaciones') or '-',
                'estado': estado,
                'metodo_pago': metodo_pago,
            })

        total_items = len(ventas)
        try:
            current_page = int(request.args.get('page', 1))
        except (TypeError, ValueError):
            current_page = 1
        page_size = 7
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        if current_page < 1:
            current_page = 1
        if current_page > total_pages:
            current_page = total_pages

        start_index = (current_page - 1) * page_size
        end_index = start_index + page_size
        ventas = ventas[start_index:end_index]

    except Exception as e:
        print(f"\n⚠️ ERROR EN CONSULTA DE VENTAS: {e}\n")
        flash(f'Error al cargar ventas: {str(e)}', 'error')
    finally:
        cursor.close()
        conn.close()

    return render_template('ventas.html', ventas=ventas, total_items=total_items, current_page=current_page, total_pages=total_pages)

@app.route('/ventas/modificar/<int:id_venta>')
def modificar_venta(id_venta):
    if 'user' not in session:
        return redirect(url_for('login'))

    flash('Funcionalidad de modificación de venta aún no implementada.', 'info')
    return redirect(url_for('ventas'))

@app.route('/ventas/eliminar/<int:id_venta>', methods=['POST'])
def eliminar_venta(id_venta):
    if 'user' not in session:
        return redirect(url_for('login'))

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
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

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
