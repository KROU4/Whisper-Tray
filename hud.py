"""
Floating HUD-окно поверх всех окон.
Показывает статус записи (● REC  0:07) и обработки (⏳ Обработка...).
Управляется из tkinter-потока через команды в tk_queue.
"""
import queue
import time
import logging
import tkinter as tk

logger = logging.getLogger(__name__)

# Размеры HUD-окна
HUD_W = 210
HUD_H = 52
MARGIN = 20  # Отступ от краёв экрана (правый нижний угол)


class HudManager:
    """
    Создаёт и управляет HUD-окном внутри tkinter event loop.
    Все методы должны вызываться только из tkinter-потока.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._dot_id = None
        self._text_id = None
        self._pulse_job = None      # ID задачи after() для анимации
        self._pulse_bright = True   # Текущее состояние пульсации
        self._timer_job = None      # ID задачи after() для таймера
        self._rec_start: float | None = None  # Время начала записи

    # ------------------------------------------------------------------
    # Публичные методы
    # ------------------------------------------------------------------

    def show_rec(self):
        """Создаёт HUD, запускает анимацию записи и таймер"""
        self._destroy_window()
        self._create_window()
        self._draw_rec()
        self._rec_start = time.monotonic()
        self._schedule_pulse()
        self._start_timer()

    def show_processing(self):
        """Останавливает пульсацию и таймер, меняет текст на 'Обработка...'"""
        self._cancel_pulse()
        self._cancel_timer()
        if self._canvas is None:
            return
        try:
            self._canvas.itemconfig(self._dot_id, fill='#777777')
            self._canvas.itemconfig(self._text_id, text='⏳ Обработка...')
        except Exception as e:
            logger.warning(f"HUD show_processing ошибка: {e}")

    def hide(self):
        """Скрывает HUD"""
        self._destroy_window()

    # ------------------------------------------------------------------
    # Внутренние методы
    # ------------------------------------------------------------------

    def _create_window(self):
        """Создаёт полупрозрачное окно без рамки поверх всех окон"""
        self._win = tk.Toplevel(self.root)
        self._win.overrideredirect(True)          # Без рамки ОС
        self._win.attributes('-topmost', True)    # Поверх всех
        self._win.attributes('-alpha', 0.88)      # Полупрозрачность

        # Убирает кнопку из панели задач на Windows
        try:
            self._win.wm_attributes('-toolwindow', True)
        except Exception:
            pass

        # Позиционируем в правый нижний угол
        sw = self._win.winfo_screenwidth()
        sh = self._win.winfo_screenheight()
        x = sw - HUD_W - MARGIN
        y = sh - HUD_H - MARGIN
        self._win.geometry(f"{HUD_W}x{HUD_H}+{x}+{y}")

        self._canvas = tk.Canvas(
            self._win,
            width=HUD_W,
            height=HUD_H,
            bg='#1a1a1a',
            highlightthickness=0,
        )
        self._canvas.pack()

    def _draw_rec(self):
        """Рисует красную точку и текст REC на холсте"""
        # Красная пульсирующая точка
        self._dot_id = self._canvas.create_oval(
            12, 15, 30, 33,
            fill='#ff3333',
            outline='',
        )
        # Текст справа от точки
        self._text_id = self._canvas.create_text(
            HUD_W // 2 + 14, HUD_H // 2,
            text='● REC',
            fill='white',
            font=('Segoe UI', 12, 'bold'),
            anchor='center',
        )

    def _schedule_pulse(self):
        """Запускает рекурсивную анимацию пульсации через after()"""
        self._cancel_pulse()
        self._do_pulse()

    def _do_pulse(self):
        """Один шаг анимации — переключает яркость точки"""
        if self._canvas is None or self._dot_id is None:
            return
        try:
            color = '#ff3333' if self._pulse_bright else '#7a0000'
            self._canvas.itemconfig(self._dot_id, fill=color)
            self._pulse_bright = not self._pulse_bright
            self._pulse_job = self._canvas.after(500, self._do_pulse)
        except Exception:
            pass  # Окно уже уничтожено

    def _cancel_pulse(self):
        """Отменяет запланированную анимацию"""
        if self._pulse_job is not None:
            try:
                self._canvas.after_cancel(self._pulse_job)
            except Exception:
                pass
            self._pulse_job = None

    def _start_timer(self):
        """Запускает ежесекундное обновление счётчика времени записи"""
        self._cancel_timer()
        self._tick_timer()

    def _tick_timer(self):
        """Обновляет текст HUD: '● REC  0:07'"""
        if self._canvas is None or self._text_id is None or self._rec_start is None:
            return
        try:
            elapsed = int(time.monotonic() - self._rec_start)
            label = f"● REC  {elapsed // 60}:{elapsed % 60:02d}"
            self._canvas.itemconfig(self._text_id, text=label)
            self._timer_job = self._canvas.after(1000, self._tick_timer)
        except Exception:
            pass

    def _cancel_timer(self):
        """Отменяет таймер"""
        if self._timer_job is not None:
            try:
                self._canvas.after_cancel(self._timer_job)
            except Exception:
                pass
            self._timer_job = None

    def _destroy_window(self):
        """Полностью уничтожает окно и сбрасывает состояние"""
        self._cancel_pulse()
        self._cancel_timer()
        self._rec_start = None
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
        self._win = None
        self._canvas = None
        self._dot_id = None
        self._text_id = None
        self._pulse_bright = True
        self._timer_job = None


# ------------------------------------------------------------------
# Точка входа tkinter-потока
# ------------------------------------------------------------------

def TkLoop(state):
    """
    Основной цикл tkinter. Запускается в daemon-потоке.
    Все операции с окнами происходят здесь.
    Команды принимаются через state.tk_queue.
    """
    root = tk.Tk()
    root.withdraw()  # Корневое окно скрыто — только служебное

    hud = HudManager(root)

    def process_queue():
        """Опрашивает очередь команд каждые 50 мс"""
        try:
            while True:
                msg = state.tk_queue.get_nowait()
                cmd = msg[0] if msg else None
                logger.debug(f"TkLoop команда: {cmd}")

                if cmd == 'show_hud':
                    hud.show_rec()
                elif cmd == 'processing':
                    hud.show_processing()
                elif cmd == 'hide_hud':
                    hud.hide()
                elif cmd == 'show_settings':
                    from settings import open_settings
                    open_settings(root, state)
                else:
                    logger.warning(f"Неизвестная команда: {cmd}")

        except queue.Empty:
            pass  # Очередь пуста — нормально
        except Exception as e:
            logger.error(f"TkLoop ошибка: {e}")

        root.after(50, process_queue)  # Следующая проверка через 50 мс

    root.after(50, process_queue)
    logger.info("Tkinter event loop запущен")
    root.mainloop()
