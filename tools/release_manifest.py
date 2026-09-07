"""Write hashes and byte sizes for the actual native release artifacts."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    result = []
    for path in sorted(args.directory.iterdir()):
        if path.suffix.lower() not in {'.msi', '.dmg', '.pkg', '.deb', '.rpm', '.appimage'}:
            continue
        # GitHub changes '~' in uploaded asset names to '.'. Normalize before
        # hashing so the published manifest names the file users download.
        public_name = path.name.replace('~', '.')
        if public_name != path.name:
            target = path.with_name(public_name)
            if target.exists():
                raise FileExistsError(target)
            path.rename(target)
            path = target
        sha = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                sha.update(chunk)
        result.append({'name': path.name, 'bytes': path.stat().st_size, 'sha256': sha.hexdigest()})
    if not result:
        raise SystemExit('No native packages found')
    manifest = args.directory / f'{args.name}-manifest.json'
    manifest.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(manifest.read_text(encoding='utf-8'))


if __name__ == '__main__':
    main()
