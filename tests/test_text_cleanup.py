from types import SimpleNamespace

import pytest

import text_cleanup
from config_store import ConfigStore
from core import BackendError
from text_cleanup import MAX_CLEANUP_CHARS, TextCleaner, should_polish
from transcriber import GROQ_BACKEND, LOCAL_BACKEND, Transcriber


class StatusError(Exception):
    def __init__(self, status_code):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


RAW = "ну короче э мне нужно чтобы ты сделал отчёт по продажам за сентябрь и отправил его до пятницы"
CLEAN = "Мне нужно, чтобы ты сделал отчёт по продажам за сентябрь и отправил его до пятницы."


def test_polish_returns_model_output_and_sends_only_wrapped_text():
    client = FakeClient([CLEAN])
    assert TextCleaner(lambda: client).polish(RAW, language="ru") == CLEAN
    call = client.calls[0]
    assert call["model"] == text_cleanup.CLEANUP_MODELS[0]
    assert call["messages"][1]["content"] == f"<transcript>\n{RAW}\n</transcript>"
    assert "ru" in call["messages"][0]["content"]
    assert call["temperature"] == 0.0


def test_polish_falls_through_models_on_rate_limit():
    client = FakeClient([StatusError(429), CLEAN])
    assert TextCleaner(lambda: client).polish(RAW) == CLEAN
    assert [c["model"] for c in client.calls] == list(text_cleanup.CLEANUP_MODELS[:2])


def test_polish_stops_on_rejected_key_and_keeps_transcript():
    client = FakeClient([StatusError(401), CLEAN])
    assert TextCleaner(lambda: client).polish(RAW) == RAW
    assert len(client.calls) == 1


def test_polish_keeps_transcript_when_every_model_fails():
    client = FakeClient([RuntimeError("down")] * len(text_cleanup.CLEANUP_MODELS))
    assert TextCleaner(lambda: client).polish(RAW) == RAW


def test_polish_rejects_answers_instead_of_cleanup():
    answer = "Конечно! Вот подробный план отчёта по продажам. " * 10
    client = FakeClient([answer])
    assert TextCleaner(lambda: client).polish(RAW) == RAW


def test_polish_strips_echoed_tags():
    client = FakeClient([f"<transcript>{CLEAN}</transcript>"])
    assert TextCleaner(lambda: client).polish(RAW) == CLEAN


def test_polish_skips_empty_long_and_unavailable_client():
    def broken():
        raise BackendError("cloud_key_missing", "no key")

    assert TextCleaner(broken).polish(RAW) == RAW
    client = FakeClient([])
    long_text = "слово " * (MAX_CLEANUP_CHARS // 5)
    assert TextCleaner(lambda: client).polish(long_text) == long_text
    assert TextCleaner(lambda: client).polish("  ") == "  "
    assert client.calls == []


def test_polish_respects_cancellation():
    client = FakeClient([CLEAN])
    assert TextCleaner(lambda: client, cancelled=lambda: True).polish(RAW) == RAW
    assert client.calls == []


def test_should_polish_only_in_speed_profile():
    assert should_polish({"profile": "speed"})
    assert should_polish({"profile": "speed", "ai_cleanup": True})
    assert not should_polish({"profile": "speed", "ai_cleanup": False})
    assert not should_polish({"profile": "privacy", "ai_cleanup": True})


def _transcriber(config, client):
    transcriber = Transcriber(config=config)
    transcriber._get_groq_client = lambda: client
    return transcriber


def test_transcriber_polish_runs_only_after_groq_transcription():
    stages = []
    client = FakeClient([CLEAN])
    transcriber = _transcriber({"profile": "speed", "ai_cleanup": True}, client)
    transcriber.on_progress = lambda stage, progress=None: stages.append(stage)
    transcriber.last_backend = GROQ_BACKEND
    assert transcriber.polish(RAW) == CLEAN
    assert stages == ["polishing"]

    transcriber.last_backend = LOCAL_BACKEND
    assert transcriber.polish(RAW) == RAW


def test_transcriber_polish_never_runs_in_privacy_profile():
    client = FakeClient([CLEAN])
    transcriber = _transcriber({"profile": "privacy", "ai_cleanup": True}, client)
    transcriber.last_backend = GROQ_BACKEND
    assert transcriber.polish(RAW) == RAW
    assert client.calls == []


def test_transcriber_polish_raises_cancelled_after_cleanup():
    client = FakeClient([CLEAN])
    flags = iter([False, False, True, True, True])
    transcriber = _transcriber({"profile": "speed"}, client)
    transcriber.cancelled = lambda: next(flags)
    transcriber.last_backend = GROQ_BACKEND
    with pytest.raises(BackendError) as error:
        transcriber.polish(RAW)
    assert error.value.code == "cancelled"


def test_config_defaults_and_sanitizes_ai_cleanup(tmp_path):
    class NoSecrets:
        def set_groq_key(self, value):
            pass

    store = ConfigStore(tmp_path / "config.json", tmp_path / "legacy.json", credentials=NoSecrets())
    assert store.load()["ai_cleanup"] is True
    (tmp_path / "config.json").write_text('{"ai_cleanup": "yes"}', encoding="utf-8")
    assert store.load()["ai_cleanup"] is True
    (tmp_path / "config.json").write_text('{"ai_cleanup": false}', encoding="utf-8")
    assert store.load()["ai_cleanup"] is False


def test_polish_skips_short_phrases_without_a_request():
    client = FakeClient([])
    assert TextCleaner(lambda: client).polish("какая погода завтра") == "какая погода завтра"
    assert client.calls == []


def test_polish_gives_first_model_a_short_timeout_and_returns_one_line():
    client = FakeClient([StatusError(503), "Первое предложение.\n\nВторое предложение."])
    assert TextCleaner(lambda: client).polish(RAW) == "Первое предложение. Второе предложение."
    assert client.calls[0]["timeout"] <= text_cleanup.FIRST_MODEL_TIMEOUT_SECONDS
    assert client.calls[1]["timeout"] > text_cleanup.FIRST_MODEL_TIMEOUT_SECONDS
