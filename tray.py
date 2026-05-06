"""
Системный трей: иконка, контекстное меню, уведомления.
Работает в main thread (требование pystray).
"""
import logging
from io import BytesIO
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item, Menu

logger = logging.getLogger(__name__)


def _create_icon_image(recording: bool = False) -> Image.Image:
    """
    Генерирует иконку трея 64×64.
    recording=True: ярко-красный фон — визуальный сигнал что идёт запись.
    """
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if recording:
        draw.ellipse([2, 2, 62, 62], fill=(160, 20, 20, 255))   # Ярко-красный фон
        draw.ellipse([20, 20, 44, 44], fill=(255, 255, 255, 255))  # Белая точка
        draw.ellipse([27, 27, 37, 37], fill=(220, 40, 40, 255))    # Красный центр
    else:
        draw.ellipse([2, 2, 62, 62], fill=(35, 35, 40, 240))    # Тёмный фон
        draw.ellipse([20, 20, 44, 44], fill=(220, 50, 50, 255))  # Красная точка
        draw.ellipse([26, 26, 38, 38], fill=(255, 255, 255, 180))  # Белый центр

    return img


class TrayApp:
    """Приложение системного трея на базе pystray"""

    def __init__(self, state):
        self.state = state
        self._icon: pystray.Icon | None = None

    # ------------------------------------------------------------------
    # Обработчики меню
    # ------------------------------------------------------------------

    def _on_settings(self, icon, menu_item):
        """Открывает окно настроек через tkinter-поток"""
        self.state.tk_queue.put(('show_settings',))

    def _on_autostart(self, icon, menu_item):
        """Переключает автозапуск"""
        import autostart
        if autostart.is_enabled():
            autostart.disable()
        else:
            autostart.enable()
        icon.update_menu()

    def _autostart_checked(self, menu_item) -> bool:
        """Возвращает текущее состояние автозапуска для отображения галочки"""
        import autostart
        return autostart.is_enabled()

    def _on_transcribe_file(self, icon, menu_item):
        """Открывает диалог выбора аудиофайла через tkinter-поток"""
        self.state.tk_queue.put(('open_file_dialog',))

    def _on_quit(self, icon, menu_item):
        logger.info("Завершение по команде меню")
        icon.stop()

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def set_recording(self, recording: bool):
        """Обновляет иконку трея: красный фон во время записи"""
        if self._icon is None:
            return
        try:
            self._icon.icon = _create_icon_image(recording)
        except Exception as e:
            logger.warning(f"Смена иконки не удалась: {e}")

    def notify(self, title: str, message: str):
        """Показывает всплывающее уведомление трея"""
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception as e:
            logger.warning(f"Уведомление не отображено: {e}")

    def run(self):
        """
        Запускает трей — блокирующий вызов.
        Должен выполняться в main thread.
        """
        menu = Menu(
            item('Настройки',                    self._on_settings),
            item('Транскрибировать аудиофайл',   self._on_transcribe_file),
            item(
                'Автозапуск',
                self._on_autostart,
                checked=self._autostart_checked,
            ),
            Menu.SEPARATOR,
            item('Выход', self._on_quit),
        )

        self._icon = pystray.Icon(
            name='WhisperTray',
            icon=_create_icon_image(),
            title='WhisperTray — голосовой ввод\nwin+alt для записи',
            menu=menu,
        )
        logger.info("Трей запущен. Используйте win+alt для записи.")
        self._icon.run()
