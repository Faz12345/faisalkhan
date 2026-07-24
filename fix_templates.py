import os

d = r'c:\Users\FAISAL-PC\dental\app\backend\templates'
files = ['register.html', 'my_appointments.html', 'doctor_patients.html', 'patient_detail.html']

for f in files:
    path = os.path.join(d, f)
    with open(path, 'r', encoding='utf-8') as fp:
        content = fp.read()
    
    stripped = content.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        inner = stripped[1:-1]
        inner = inner.replace('\\"', '"')
        inner = inner.replace('\\n', '\n')
        with open(path, 'w', encoding='utf-8') as fp:
            fp.write(inner)
        print(f'Fixed: {f}')
    else:
        print(f'Already OK: {f}')
print('Done')
