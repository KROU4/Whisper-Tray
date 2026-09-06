"""Verify release versions, native-worker sources and immutable runtime pins."""

import os
import sys
from pathlib import Path

import tomllib


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from version import APP_VERSION

    config = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    assert config['project']['version'] == config['tool']['briefcase']['version'] == APP_VERSION
    tag = os.environ.get('GITHUB_REF', '')
    if tag.startswith('refs/tags/'):
        assert tag.removeprefix('refs/tags/') == f'v{APP_VERSION}', 'Tag/version mismatch'
    app = config['tool']['briefcase']['app']['whispertray']
    for source in app['sources']:
        assert (root / source).exists(), f'Missing packaged source: {source}'
    for source in ('jobs.py', 'inference_worker.py', 'version.py'):
        assert source in app['sources'], f'Missing worker/runtime module: {source}'
    assert app['requirement_installer_args'] == ['--constraint', './requirements.txt']
    for line in (root / 'requirements.txt').read_text().splitlines():
        if line and not line.lstrip().startswith('#'):
            assert '==' in line, f'Unpinned dependency: {line}'
    print(f'Release {APP_VERSION}: versions, sources and dependency pins verified')


if __name__ == '__main__':
    main()
