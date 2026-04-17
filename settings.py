"""
Окно настроек приложения.
Открывается из контекстного меню трея через tk_queue.
"""
import json
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

logger = logging.getLogger(__name__)

# Отображаемые метки и соответствующие имена моделей
MODEL_LABELS = [
    'tiny   (~75 MB)',
    'base   (~142 MB)',
    'small  (~466 MB)',
    'medium (~1.5 GB)',
    'large  (~2.9 GB)',
]
MODEL_NAMES = ['tiny', 'base', 'small', 'medium', 'large']

def _label_for(model_name: str) -> str:
    idx = MODEL_NAMES.index(model_name) if model_name in MODEL_NAMES else 2
    return MODEL_LABELS[idx]

def _name_for(label: str) -> str:
    idx = MODEL_LABELS.index(label) if label in MODEL_LABELS else 2
    return MODEL_NAMES[idx]
CONFIG_FILE = Path(__file__).parent / 'config.json'


def _get_input_devices() -> list[dict]:
    """Возвращает список доступных входных аудиоустройств"""
    result = []
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                result.append({'index': i, 'name': d['name']})
    except Exception as e:
        logger.error(f"Ошибка получения списка устройств: {e}")
    return result


def open_settings(root: tk.Tk, state):
    """
    Открывает модальное окно настроек.
    Должно вызываться из tkinter-потока.
    """
    win = tk.Toplevel(root)
    win.title('Настройки WhisperTray')
    win.geometry('460x260')
    win.resizable(False, False)
    win.grab_set()   # Блокирует взаимодействие с другими окнами

    cfg = state.config
    pad = {'padx': 12, 'pady': 7}

    # ---- Строка 0: модель Whisper ----
    tk.Label(win, text='Модель Whisper:', anchor='w').grid(
        row=0, column=0, sticky='w', **pad
    )
    model_var = tk.StringVar(value=_label_for(cfg.get('model', 'small')))
    ttk.Combobox(
        win, textvariable=model_var,
        values=MODEL_LABELS, state='readonly', width=20
    ).grid(row=0, column=1, sticky='w', **pad)

    # ---- Строка 1: микрофон ----
    devices = _get_input_devices()
    device_names = [d['name'] for d in devices]
    current_idx = cfg.get('device_index')
    current_name = next(
        (d['name'] for d in devices if d['index'] == current_idx),
        device_names[0] if device_names else '',
    )

    tk.Label(win, text='Микрофон:', anchor='w').grid(
        row=1, column=0, sticky='w', **pad
    )
    mic_var = tk.StringVar(value=current_name)
    ttk.Combobox(
        win, textvariable=mic_var,
        values=device_names, state='readonly', width=32
    ).grid(row=1, column=1, sticky='w', **pad)

    # ---- Строка 2: хоткей (рекордер) ----
    tk.Label(win, text='Хоткей:', anchor='w').grid(
        row=2, column=0, sticky='w', **pad
    )

    hotkey_var = tk.StringVar(value=cfg.get('hotkey', 'win+alt'))

    hotkey_frame = tk.Frame(win)
    hotkey_frame.grid(row=2, column=1, sticky='w', **pad)

    # Метка с текущим хоткеем (не редактируемая)
    hotkey_display = tk.Label(
        hotkey_frame, textvariable=hotkey_var,
        width=18, relief='sunken', anchor='w',
        bg='white', padx=6, font=('Consolas', 10),
    )
    hotkey_display.pack(side='left', padx=(0, 8))

    record_btn = tk.Button(hotkey_frame, text='⌨ Записать', width=12)
    record_btn.pack(side='left')

    def _start_capture():
        """Запускает прослушивание клавиатуры в фоновом потоке"""
        record_btn.config(state='disabled', text='Слушаю...')
        hotkey_display.config(bg='#fff3cd')  # Жёлтый фон — активное слушание
        hotkey_var.set('...')

        def _capture():
            import keyboard as kb
            try:
                combo = kb.read_hotkey(suppress=False)
            except Exception:
                combo = None
            # Возвращаемся в tkinter-поток
            win.after(0, lambda: _on_captured(combo))

        def _on_captured(combo: str | None):
            if combo:
                hotkey_var.set(combo)
            else:
                hotkey_var.set(cfg.get('hotkey', 'win+alt'))
            hotkey_display.config(bg='white')
            record_btn.config(state='normal', text='⌨ Записать')

        threading.Thread(target=_capture, daemon=True).start()

    record_btn.config(command=_start_capture)

    # Подсказка
    tk.Label(
        win,
        text='Нажмите «Записать», затем введите нужное сочетание клавиш',
        fg='gray', font=('Segoe UI', 8)
    ).grid(row=3, column=1, sticky='w', padx=12)

    # ---- Кнопки ----
    btn_frame = tk.Frame(win)
    btn_frame.grid(row=4, column=0, columnspan=2, pady=14)

    def save():
        new_model   = _name_for(model_var.get())
        new_hotkey  = hotkey_var.get().strip()
        new_mic_name = mic_var.get()
        new_device_idx = next(
            (d['index'] for d in devices if d['name'] == new_mic_name),
            None,
        )

        if not new_hotkey:
            messagebox.showwarning('Предупреждение', 'Хоткей не может быть пустым.')
            return

        # Применяем изменения в runtime
        old_hotkey = state.config.get('hotkey')
        state.config['model']        = new_model
        state.config['hotkey']       = new_hotkey
        state.config['device_index'] = new_device_idx

        # Перезагружаем хоткей если изменился
        hotkey_listener = getattr(state, 'hotkey_listener', None)
        if hotkey_listener and new_hotkey != old_hotkey:
            hotkey_listener.reload_hotkey(new_hotkey)

        # Сбрасываем кешированный recorder если изменился микрофон
        if hotkey_listener:
            if hotkey_listener._recorder is not None:
                if new_device_idx != getattr(
                    hotkey_listener._recorder, 'device_index', None
                ):
                    hotkey_listener._recorder = None

        # Сбрасываем модель если изменился её размер
        if hotkey_listener and hotkey_listener._transcriber:
            if new_model != hotkey_listener._transcriber.model_size:
                hotkey_listener._transcriber.reload(new_model)

        # Сохраняем в файл
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(state.config, f, indent=2, ensure_ascii=False)
            logger.info("Настройки сохранены")
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
            messagebox.showerror('Ошибка', f'Не удалось сохранить:\n{e}')
            return

        win.destroy()
        messagebox.showinfo('Сохранено', 'Настройки применены.')

    tk.Button(btn_frame, text='Сохранить', command=save, width=13).pack(
        side='left', padx=8
    )
    tk.Button(btn_frame, text='Отмена', command=win.destroy, width=13).pack(
        side='left', padx=8
    )
