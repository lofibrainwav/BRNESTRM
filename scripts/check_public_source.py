from pathlib import Path
import re
patterns=[
 r'-----BEGIN (?:RSA|OPENSSH|EC|PRIVATE) PRIVATE KEY-----',
 r'gh[pousr]_[A-Za-z0-9_]{30,}',
 r'sk-(?:proj-)?[A-Za-z0-9_-]{24,}',
 r'https://chatgpt\\.com/g/g-p-',
]
for p in Path('.').rglob('*'):
 if not p.is_file() or '.git' in p.parts or '__pycache__' in p.parts or p.name=='check_public_source.py':continue
 if p.suffix not in {'.py','.html','.cjs','.md','.yml','.yaml',''}:raise SystemExit('Unexpected public file:'+str(p))
 value=p.read_text()
 if any(re.search(pattern,value) for pattern in patterns):
  raise SystemExit('BLOCKED_PUBLIC_CONTENT:'+str(p))
print('PUBLIC_SOURCE_SCAN_PASS')
