from pathlib import Path
path = Path('app/templates/usuario.html')
print('path', path.resolve())
text = path.read_text(encoding='utf-8', errors='replace')
print('len', len(text))
for token in ['Contraseña', 'password_hash', '{{ user.password', '{{ user.password_hash']:
    idx = text.find(token)
    print(token, idx)
start = text.find('<table')
print('table idx', start)
if start != -1:
    print('table snippet:', repr(text[start:start+500]))
# show specific lines around table
lines = text.splitlines()
for i in range(max(0, start and text[:start].count('\n')-5), min(len(lines), (text[:start].count('\n')+20))):
    print(i+1, lines[i])
