import re
with open('tests/unit/services/temporal/runtime/test_launcher.py', 'r') as f:
    c = f.read()
c = c.replace('    record, process, _ = await launcher.launch(\n    record, process, _ = await launcher.launch(', '    record, process, _ = await launcher.launch(')
with open('tests/unit/services/temporal/runtime/test_launcher.py', 'w') as f:
    f.write(c)
