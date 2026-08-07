# Contributing

## Before changing code

Open an issue for behavior or architecture changes. Security reports follow
`SECURITY.md` and must not contain sensitive data.

## Local checks

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
ruff check .
python -m compileall -q .
```

Keep Privacy mode offline, keep credentials out of serializable configuration,
and use typed terminal UI events for every success and failure path. Add a
regression test for each fixed bug.

Dependency inputs belong in `requirements.in` or `requirements-dev.in`. Update
the resolved files with:

```powershell
python -m uv pip compile requirements.in -o requirements.txt
python -m uv pip compile requirements-dev.in -o requirements-dev.txt
```

Native package changes must be validated on the target operating system. See
`docs/PACKAGING.md`.
