#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Create local source archives from a clean Git commit; never uploads files."""
import hashlib
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    version=(ROOT/'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+',version):
        raise SystemExit('Invalid VERSION')
    status=subprocess.check_output(['git','status','--porcelain','--untracked-files=normal'],cwd=ROOT,text=True)
    if status.strip():
        raise SystemExit('Commit reviewed changes before building source archives')
    subprocess.run(['git','rev-parse','--verify','HEAD'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['python3',str(ROOT/'scripts/check_source.py')],cwd=ROOT,check=True)
    dest=ROOT/'dist';dest.mkdir(exist_ok=True)
    prefix=f'gpu-lab-{version}'
    checks=[]
    for fmt,suffix in [('tar.gz','tar.gz'),('zip','zip')]:
        output=dest/f'{prefix}.{suffix}'
        subprocess.run(['git','archive',f'--format={fmt}',f'--prefix={prefix}/',f'--output={output}','HEAD'],cwd=ROOT,check=True)
        checks.append(hashlib.sha256(output.read_bytes()).hexdigest()+'  '+output.name)
        print(output)
    (dest/'SHA256SUMS').write_text('\n'.join(checks)+'\n')


if __name__=='__main__':
    main()
