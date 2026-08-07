import logging

from logging_setup import SensitiveDataFilter, configure_logging


def test_sensitive_data_filter_redacts_api_keys():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "groq_api_key=super-secret", (), None)

    assert SensitiveDataFilter().filter(record) is True
    assert "super-secret" not in record.msg
    assert "[REDACTED]" in record.msg


def test_sensitive_values_in_format_arguments_are_redacted():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "authorization=%s", ("Bearer-secret",), None)
    assert SensitiveDataFilter().filter(record) is True
    assert "Bearer-secret" not in record.getMessage()


def test_configure_logging_creates_rotating_local_log(tmp_path):
    log_file = configure_logging(tmp_path)
    logging.getLogger("test").info("startup complete")

    assert log_file == tmp_path / "whisper_tray.log"
    assert "startup complete" in log_file.read_text(encoding="utf-8")
