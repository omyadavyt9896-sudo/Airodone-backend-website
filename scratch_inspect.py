import os

for root, dirs, files in os.walk('templates'):
    for f in files:
        if 'admin' in f.lower() or 'admin' in root.lower():
            print(os.path.join(root, f))
