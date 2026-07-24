import os
import re

d = r'c:\Users\FAISAL-PC\dental\app\backend\templates'
files = ['doctor_dashboard.html', 'doctor_patients.html', 'patient_detail.html']

for fname in files:
    path = os.path.join(d, fname)
    with open(path, 'rb') as f:
        raw = f.read()
    
    text = raw.decode('utf-8')
    stripped = text.strip()
    
    # Check if starts with " and ends with " (literal double quote chars)
    if stripped[0] == '"' and stripped[-1] == '"':
        # Remove outer quotes and unescape
        inner = stripped[1:-1]
        inner = inner.replace('\\"', '"')
        inner = inner.replace('\\n', '\n')
        
        with open(path, 'wb') as f:
            f.write(inner.encode('utf-8'))
        print(f'FIXED: {fname}')
    else:
        print(f'ALREADY OK: {fname}')

print('Done!')
</path>
</create_file>
