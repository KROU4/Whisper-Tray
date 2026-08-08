# Нативная упаковка и релиз

WhisperTray использует Briefcase 0.3.25. Установщики собираются на целевой ОС:
нативные рантаймы, форматы установщиков и средства подписи не
кросс-компилируются.

## Подготовка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

После изменения исходного кода выполните `briefcase update <platform>`. После
изменения зависимостей — `briefcase update -r <platform>`.

## Windows: MSI

```powershell
python -m briefcase create windows --no-input
python tools/add_windows_desktop_shortcut.py
python -m briefcase build windows --no-input
python -m briefcase package windows --no-input
```

Проверьте установку и удаление в чистых Windows 10 и Windows 11 VM: запуск из
меню «Пуск», доступ к микрофону, регистрацию сочетания клавиш, вставку текста
как минимум в двух приложениях и удаление файлов установщика.

## macOS: DMG и PKG

```bash
python -m briefcase create macOS --no-input
python -m briefcase build macOS --no-input
python -m briefcase package macOS -p dmg --adhoc-sign --no-input
python -m briefcase package macOS -p pkg --adhoc-sign --no-input
```

Ad-hoc подпись подходит только для проверки в CI. Для публичного релиза нужны
сертификат Apple Developer и нотарификация. Если распространяются обе
архитектуры, проверьте Intel и Apple Silicon.

## Linux

```bash
python -m briefcase create linux --no-input
python -m briefcase build linux --no-input
python -m briefcase package linux --no-input
```

Пакет зависит от дистрибутива. Release workflow собирает его на Ubuntu.
Глобальные сочетания клавиш требуют X11; в Wayland это ограничение отражается
в приложении.

## Автоматический релиз

`.github/workflows/release.yml` запускается при отправке тега `v*`. Workflow:

1. устанавливает зависимости и запускает `python -m pytest -q` на Windows,
   macOS и Ubuntu;
2. собирает пакеты для каждой платформы;
3. загружает содержимое `dist/*` как артефакты workflow;
4. после успеха всех сборок создает один GitHub Release с объединенными
   артефактами.

Создавайте тег только из чистой, проверенной ветки `main`:

```powershell
git tag v1.1.0
git push origin v1.1.0
```

## Приемка релиза

- Полный набор тестов проходит на трех ОС.
- В истории Git нет API-ключей, расшифровок, логов, локального конфига и аудио.
- MSI устанавливается и удаляется на чистой Windows VM.
- Пакеты macOS запускаются после проверки подписи и нотарификации.
- Linux-пакет устанавливается на целевой версии Ubuntu.
- Режим «Приватность» проверен без сети.
- Режим «Скорость» проверен одноразовым пользовательским ключом Groq.
- В заметках к релизу зафиксированы размер пакетов и SHA-256.

Локальная модель Whisper скачивается после установки и не включается в релизный
пакет.
