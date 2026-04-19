# WhisperTray

Локальный голосовой ввод для Windows через [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Живёт в системном трее — нажал хоткей, сказал, текст вставился в активное поле.

Никаких облаков, никаких API-ключей.

## Как это работает

1. Нажимаешь `Win+Alt` — начинается запись (HUD показывает **● REC  0:03**, иконка трея краснеет)
2. Нажимаешь снова — Whisper транскрибирует локально
3. Текст вставляется напрямую через Windows SendInput — буфер обмена не затрагивается

## Установка

**Требования:** Python 3.10+, Windows 10/11

```bash
git clone https://github.com/<your-username>/whispetray.git
cd whispetray
pip install -r requirements.txt
python main.py
```

При первом запуске скачается модель Whisper (по умолчанию `small`, ~460 МБ).

### CUDA — ускорение на GPU NVIDIA

Программа автоматически определяет наличие CUDA и использует GPU если он доступен (`float16`). При ошибке — тихий fallback на CPU (`int8`).

Для работы с CUDA нужны библиотеки cuDNN:

```bash
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

На GPU транскрипция `small`-модели занимает ~0.3–0.8 сек вместо 2–4 сек на CPU.

## Настройки

Правой кнопкой по иконке в трее → **Настройки**:

| Параметр | По умолчанию | Описание |
|---|---|---|
| `model` | `small` | Размер модели: `tiny`, `base`, `small`, `medium`, `large` |
| `hotkey` | `win+alt` | Глобальный хоткей для записи |
| `language` | Авто | Язык распознавания (ru / en / uk / de / fr / es / it / pl) |
| `device_index` | системный | Микрофон |

Или редактируй `config.json` вручную — изменения применяются без перезапуска.

## Автозапуск

Правой кнопкой по иконке → **Автозапуск** — приложение добавится в реестр `HKCU\...\Run`.

## Зависимости

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — транскрипция (CPU/CUDA)
- [sounddevice](https://python-sounddevice.readthedocs.io/) — захват звука
- [pystray](https://github.com/moses-palmer/pystray) — системный трей
- [keyboard](https://github.com/boppreh/keyboard) — глобальные хоткеи
- [Pillow](https://python-pillow.org/) — генерация иконки трея
- tkinter — HUD и окно настроек

## Лицензия

MIT
