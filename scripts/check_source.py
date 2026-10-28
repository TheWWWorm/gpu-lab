#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check source syntax and obvious release hygiene without touching services."""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
IGNORED={'.git','.venv','venv','__pycache__','.artifacts','dist','node_modules','.godot','local'}


def files():
    if (ROOT/'.git').is_dir():
        tracked=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
        if any(tracked):
            return [ROOT/name for name in tracked if name]
    return [p for p in ROOT.rglob('*') if p.is_file() and not IGNORED.intersection(p.relative_to(ROOT).parts)]


def main():
    errors=[]
    for path in files():
        name=str(path.relative_to(ROOT))
        if IGNORED.intersection(path.relative_to(ROOT).parts) or path.name in {'token','Xauthority','.Xauthority','.env','cookies.txt'}:
            errors.append(name+': runtime/private file is tracked')
        if path.is_symlink():
            errors.append(name+': symlinks are not included in source releases')
            continue
        if path.stat().st_size>5*1024*1024:
            errors.append(name+': unexpected large source file')
        try:
            content=path.read_text()
        except UnicodeDecodeError:
            if path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}:
                errors.append(name+': unexpected binary file')
            continue
        if path.suffix=='.py':
            try: ast.parse(content,filename=name)
            except SyntaxError as exc: errors.append(str(exc))
        if path.suffix=='.json':
            try: json.loads(content)
            except ValueError as exc: errors.append(name+': '+str(exc))
        for uuid in re.findall(r'GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',content):
            if not uuid.startswith('GPU-00000000-0000-0000-0000-'):
                errors.append(name+': real-looking GPU UUID; use synthetic example IDs')
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',content):
            errors.append(name+': private key material')
        for username in re.findall(r'/home/([A-Za-z0-9_.-]+)/',content):
            if username not in {'alice','test-user','USER'}:
                errors.append(name+': workstation-specific home path')
    for name in ['LICENSE','NOTICE','VERSION','README.md','CONTRIBUTING.md','SECURITY.md']:
        if not (ROOT/name).is_file(): errors.append('Missing '+name)
    version=(ROOT/'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+',version): errors.append('VERSION must be a semantic version')
    if f'"version": "{version}"' not in (ROOT/'gpu_lab.py').read_text():
        errors.append('MCP server version does not match VERSION')
    if errors:
        print('\n'.join(errors),file=sys.stderr)
        return 1
    print('Source syntax, version and release hygiene checks passed (not a full secret scanner).')
    return 0


if __name__=='__main__':
    sys.exit(main())
