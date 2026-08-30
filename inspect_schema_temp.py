import pyodbc
CONN_STR = (
    'Driver={ODBC Driver 17 for SQL Server};'
    'Server=ARIEL2021\\SQLEXPRESS01;'
    'Database=Sistema_repuestos;'
    'UID=sa;'
    'PWD=Arielmorales;'
)
conn = pyodbc.connect(CONN_STR)
cur = conn.cursor()
print('tables:')
for row in cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME"):
    print(row[0])
print('\nmarca/modelo tables:')
for row in cur.execute("SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME LIKE '%marca%' OR TABLE_NAME LIKE '%modelo%' ORDER BY TABLE_NAME, ORDINAL_POSITION"):
    print(row)
print('\nmarca/modelo columns:')
for row in cur.execute("SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE COLUMN_NAME LIKE '%marca%' OR COLUMN_NAME LIKE '%modelo%' ORDER BY TABLE_NAME, ORDINAL_POSITION"):
    print(row)
cur.close()
conn.close()
