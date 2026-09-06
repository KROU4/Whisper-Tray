import io
import json
import logging
import sys

import pytest

from config_store import ConfigStore
from diagnostics import safe_config_summary
from logging_setup import SensitiveDataFilter


def test_explicit_privacy_wins_over_legacy_backend():
    config, _ = ConfigStore()._migrate({'schema_version': 1, 'profile': 'privacy', 'transcription_backend': 'groq'})
    assert config['profile'] == 'privacy'
    assert config['transcription_backend'] == 'local'


@pytest.mark.parametrize('raw', [
    {'groq_max_retries': 'invalid'}, {'history': {'retention_days': None}},
    {'profile': []}, {'hud': {'position': []}}, {'language': {}},
])
def test_malformed_fields_recover_safely(raw):
    config, _ = ConfigStore()._migrate(raw)
    assert config['profile'] == 'privacy'
    assert 0 <= config['groq_max_retries'] <= 2


def test_corrupt_config_is_preserved_before_recovery(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text('{ broken', encoding='utf-8')
    config = ConfigStore(path, tmp_path / 'absent').load()
    assert config['profile'] == 'privacy'
    backups = list(tmp_path.glob('config.corrupt-*.json'))
    assert len(backups) == 1
    assert backups[0].read_text(encoding='utf-8') == '{ broken'
    assert json.loads(path.read_text(encoding='utf-8'))['profile'] == 'privacy'


def test_exception_and_chained_exception_are_redacted():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SensitiveDataFilter())
    try:
        try:
            raise ValueError('api_key=SYNTHETIC_SECRET_ONE')
        except ValueError as exc:
            raise RuntimeError('Authorization: Bearer SYNTHETIC_SECRET_TWO') from exc
    except RuntimeError:
        record = logging.LogRecord('audit', logging.ERROR, __file__, 1, 'failure', (), sys.exc_info())
    handler.handle(record)
    assert 'SYNTHETIC_SECRET' not in stream.getvalue()


def test_diagnostics_exclude_prompt_and_nested_unknown_fields():
    result = safe_config_summary({'profile': 'privacy', 'groq_prompt': 'private vocabulary',
                                  'history': {'enabled': True, 'text': 'private text'},
                                  'arbitrary': {'password': 'secret'}})
    assert result == {'profile': 'privacy', 'history': {'enabled': True}}


def test_unreadable_config_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    path.write_text('{"profile":"speed"}', encoding='utf-8')
    store = ConfigStore(path, tmp_path / 'missing')
    original_read = type(path).read_text
    monkeypatch.setattr(type(path), 'read_text', lambda *_a, **_k: (_ for _ in ()).throw(PermissionError()))
    assert store.load()['profile'] == 'privacy'
    assert store.recovery_notice == 'config_unavailable'
    assert original_read(path, encoding='utf-8') == '{"profile":"speed"}'


def test_failed_backup_preserves_corrupt_original(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    path.write_text('{ broken', encoding='utf-8')
    store = ConfigStore(path, tmp_path / 'missing')
    monkeypatch.setattr('config_store.shutil.copy2', lambda *_: (_ for _ in ()).throw(PermissionError()))
    assert store.load()['profile'] == 'privacy'
    assert store.recovery_notice == 'config_backup_failed'
    assert path.read_text(encoding='utf-8') == '{ broken'
