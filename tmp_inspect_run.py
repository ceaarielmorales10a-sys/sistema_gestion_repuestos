import run
from flask import session
app = run.app
print('run file:', run.__file__)
print('template_folder:', app.template_folder)
print('template searchpath:', app.jinja_loader.searchpath)
try:
    src, filename, _ = app.jinja_loader.get_source(app.jinja_env, 'usuario.html')
    print('usuario template path:', filename)
    print('usuario template starts with:', repr(src[:200]))
except Exception as e:
    print('unable to load usuario.html:', e)

with app.test_request_context('/usuario'):
    session['user'] = 'admin'
    session['user_id'] = 1
    session['role'] = 'Administrador del Sistema'
    rv = run.usuarios()
    html = rv.get_data(as_text=True) if hasattr(rv, 'get_data') else str(rv)
    print('len', len(html))
    print('contains Contraseña', 'Contraseña' in html)
    print('contains password_hash', 'password_hash' in html)
    print('contains user.password', 'user.password' in html)
    print('find <thead', html.find('<thead'))
    print('find <tbody', html.find('<tbody'))
    print('find Contraseña after <thead', html.find('Contraseña', html.find('<thead')))
    print('---TABLE CHUNK---')
    start = html.find('<table')
    if start != -1:
        end = html.find('</table>', start)
        if end != -1:
            print(html[start:end+8])
        else:
            print(html[start:start+2000])
    else:
        print('no table found')
