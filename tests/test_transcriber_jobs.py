from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core import BackendError
from transcriber import CLOUD_MAX_BYTES, Transcriber, validate_cloud_file


@pytest.mark.parametrize('model_size,beam', [('small', 1), ('tiny', 2)])
def test_dictation_path_uses_fast_decoder_without_reading_cloud_bytes(tmp_path, monkeypatch, model_size, beam):
    path = tmp_path / 'recording.wav'
    path.write_bytes(b'local fixture')
    model = Mock()
    model.transcribe.return_value = (iter([SimpleNamespace(text='test', end=1)]),
                                    SimpleNamespace(duration=1, language='en', language_probability=1))
    t = Transcriber(model_size, config={'profile': 'privacy'})
    t._model = model
    monkeypatch.setattr(type(path), 'read_bytes', lambda _self: pytest.fail('unnecessary cloud read'))
    assert t.transcribe(path) == 'Test.'
    assert model.transcribe.call_args.kwargs['beam_size'] == beam
    assert model.transcribe.call_args.kwargs['condition_on_previous_text'] is False


def test_sdk_retries_disabled(monkeypatch):
    factory = Mock()
    monkeypatch.setattr('groq.Groq', factory)
    t = Transcriber(config={'profile': 'speed'}, credentials=SimpleNamespace(get_groq_key=lambda: 'synthetic'))
    t._get_groq_client()
    assert factory.call_args.kwargs['max_retries'] == 0
    assert factory.call_args.kwargs['timeout'] == 60.0


def test_cloud_format_rejected_before_payload_read(tmp_path, monkeypatch):
    path = tmp_path / 'large.mkv'
    path.write_bytes(b'x')
    monkeypatch.setattr(type(path), 'read_bytes', lambda _self: pytest.fail('payload read before validation'))
    with pytest.raises(BackendError) as error:
        Transcriber(config={'profile': 'speed'}).transcribe_file(path)
    assert error.value.code == 'file_format'


@pytest.mark.parametrize('status,expected_calls,code', [(401, 1, 'cloud_auth'), (429, 3, 'cloud_rate_limit'),
                                                      (503, 3, 'cloud_failed')])
def test_cloud_retries_are_bounded(status, expected_calls, code, monkeypatch):
    class HttpFailure(Exception):
        status_code = status

    create = Mock(side_effect=HttpFailure())
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    t = Transcriber(config={'profile': 'speed', 'groq_max_retries': 99})
    monkeypatch.setattr(t, '_get_groq_client', lambda: client)
    monkeypatch.setattr(t, '_retry_delay', lambda *_: 0)
    with pytest.raises(BackendError) as error:
        t._transcribe_groq_bytes(b'test', 'test.wav')
    assert error.value.code == code
    assert create.call_count == expected_calls


def test_cancellation_does_not_upload():
    t = Transcriber(config={'profile': 'speed'}, cancelled=lambda: True)
    with pytest.raises(BackendError) as error:
        t.transcribe('missing.wav')
    assert error.value.code == 'cancelled'


def test_cloud_size_limit_preflight(tmp_path):
    path = tmp_path / 'large.wav'
    with path.open('wb') as stream:
        stream.seek(CLOUD_MAX_BYTES)
        stream.write(b'x')
    with pytest.raises(BackendError) as error:
        validate_cloud_file(path)
    assert error.value.code == 'file_too_large'


def test_local_segments_stop_on_cancellation():
    cancelled = False
    def segments():
        nonlocal cancelled
        yield SimpleNamespace(text='first', end=1)
        cancelled = True
        yield SimpleNamespace(text='second', end=2)
    t = Transcriber(cancelled=lambda: cancelled)
    with pytest.raises(BackendError) as error:
        t._collect_segments(segments(), SimpleNamespace(duration=2))
    assert error.value.code == 'cancelled'
