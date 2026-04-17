# WhisperTray

Локальный голосовой ввод для Windows через [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Живёт в системном трее — зажал хоткей, сказал, текст вставился в активное поле.

## Как это работает

1. Зажимаешь `Win+Alt` — начинается запись (HUD показывает **● REC**)
2. Отпускаешь — Whisper транскрибирует локально
3. Текст автоматически вставляется в активное окно через буфер обмена

Никаких облаков, никаких API-ключей.

## Установка

**Требования:** Python 3.10+, Windows 10/11

```bash
git clone https://github.com/<your-username>/whispetray.git
cd whispetray
pip install -r requirements.txt
python main.py
```

При первом запуске скачается модель Whisper (по умолчанию `small`, ~460 МБ).

### CUDA (опционально)

Если есть видеокарта NVIDIA — установи `faster-whisper` с поддержкой CUDA для ускорения:

```bash
pip install faster-whisper[cuda]
```

## Настройки

Правой кнопкой по иконке в трее → **Настройки**, или редактируй `config.json` вручную:

| Параметр | По умолчанию | Описание |
|---|---|---|
| `model` | `small` | Размер модели: `tiny`, `base`, `small`, `medium`, `large` |
| `hotkey` | `win+alt` | Глобальный хоткей для записи |
| `language` | `null` | Язык (`ru`, `en`, …) или `null` для автоопределения |
| `device_index` | `null` | Индекс микрофона (`null` = системный по умолчанию) |

## Автозапуск

Правой кнопкой по иконке → **Автозапуск** — приложение добавится в реестр `HKCU\...\Run`.

## Зависимости

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — транскрипция
- [sounddevice](https://python-sounddevice.readthedocs.io/) — захват звука
- [pystray](https://github.com/moses-palmer/pystray) — системный трей
- [keyboard](https://github.com/boppreh/keyboard) — глобальные хоткеи
- [pyautogui](https://pyautogui.readthedocs.io/) + [pyperclip](https://github.com/asweigart/pyperclip) — вставка текста
- tkinter — HUD и окно настроек

## Лицензия

MIT
