import pyodbc

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=ARIEL2021\\SQLEXPRESS01;"
    "Database=Sistema_repuestos;"
    "UID=sa;"
    "PWD=Arielmorales;"
)

conn = pyodbc.connect(CONN_STR)
cursor = conn.cursor()

# Ver todas las columnas de la tabla usuario
cursor.execute("SELECT TOP 1 * FROM usuario")
cols = [c[0] for c in cursor.description]
print('Columnas de la tabla usuario:')
for col in cols:
    print(f'  - {col}')

# Ver los datos del primer usuario
row = cursor.fetchone()
if row:
    print('\nDatos del primer usuario:')
    for col, val in zip(cols, row):
        print(f'  {col}: {val}')

cursor.close()
conn.close()
