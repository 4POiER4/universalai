"""Build the UniversalAI deployment packages from a committed Git revision."""

import argparse
from copy import copy
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
GUIDE = 'deploy/portal-integration/DEPLOY.md'
SETUP = 'deploy/portal-configure.py'
PORT_UPDATE = (
    'app.py',
    SETUP,
    'deploy/nginx.production.conf.example',
    'deploy/nginx.port-site.conf.example',
)


def write_package(source, target, files):
    with tarfile.open(target, 'w:gz', compresslevel=1) as package:
        for original, destination in files:
            member = copy(source.getmember(original))
            member.name = destination
            with source.extractfile(original) as payload:
                package.addfile(member, payload)
        guide = copy(source.getmember(GUIDE))
        guide.name = 'DEPLOY.md'
        with source.extractfile(GUIDE) as payload:
            package.addfile(guide, payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--ref', default='HEAD')
    args = parser.parse_args()

    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True)
    revision = subprocess.check_output(
        ['git', 'rev-parse', '--verify', '--end-of-options', f'{args.ref}^{{commit}}'], cwd=ROOT, text=True,
    ).strip()
    with tempfile.TemporaryDirectory() as directory:
        snapshot = Path(directory) / 'source.tar'
        subprocess.run(['git', 'archive', '--format=tar', '-o', snapshot, revision], cwd=ROOT, check=True)
        with tarfile.open(snapshot) as source:
            full = ((entry.name, f'UniverslaAI/{entry.name}') for entry in source.getmembers()
                    if entry.isfile() and not entry.name.startswith('deploy/portal-integration/'))
            write_package(source, args.output / 'universalai-portal-release.tar.gz', full)
            write_package(source, args.output / 'universalai-port-update.tar.gz', ((name, name) for name in PORT_UPDATE))
            write_package(source, args.output / 'universalai-portal-update.tar.gz', (
                (SETUP, 'portal-configure.py'),
                ('deploy/portal-integration/portal-sso.patch', 'portal-sso.patch'),
            ))
    print(f'Архивы собраны в {args.output}')


if __name__ == '__main__':
    main()
