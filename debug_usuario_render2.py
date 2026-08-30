import run
from flask import session
app = run.app
with app.test_request_context('/usuario'):
    session['user'] = 'admin'
    session['user_id'] = 1
    session['role'] = 'Administrador del Sistema'
    rv = run.usuarios()
    if hasattr(rv, 'get_data'):
        html = rv.get_data(as_text=True)
    else:
        html = str(rv)
    print('contains table', '<table' in html)
    print('contains Contraseña', 'Contraseña' in html)
    print('contains password_hash', 'password_hash' in html)
    print('contains user.password', 'user.password' in html)
    idx = html.find('<table')
    print('table idx', idx)
    if idx != -1:
        print(html[idx:idx+2000])
