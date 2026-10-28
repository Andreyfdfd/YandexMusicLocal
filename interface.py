# -*- coding: utf-8 -*-
"""
interface.py
Локальный аудиоплеер в стиле Яндекс Музыки (тёмная тема).
Стек: PyQt6 + python-vlc + SQLite (music_collection.db)

Шаг №3 — «Оживление плеера» + Шаг №4 — «Очередь / плейлист» + Шаг №5:
 - Главная страница: сетка альбомов, кнопки "Играть всё" / "Перемешать всё"
 - Клик по альбому -> список треков этого альбома (кнопка "Назад")
 - Одиночный клик по треку -> сразу воспроизведение через VLC (mp3 / m4a),
   очередь = весь альбом начиная с выбранного трека
 - Play/Pause кнопка меняет символ и состояние
 - Кнопки ⏮ / ⏭ переключают предыдущий/следующий трек
 - Кнопка 🔀 включает случайный порядок следующих треков
 - Прогресс-бар трека с перемоткой (тянешь ползунок -> меняется позиция)
 - По окончании трека автоматически включается следующий из очереди
 - Список "Очередь" в левой панели показывает плейлист,
   клик по любому треку в нём сразу его запускает
 - Слайдер громкости управляет звуком VLC
"""

import sys
import os
import sqlite3
import random

# ----------------------------------------------------------------------
# ВАЖНО ДЛЯ WINDOWS: python-vlc не находит libvlc.dll сам по себе,
# если папка VLC не прописана в системном PATH. Явно подсказываем,
# где искать DLL, ДО того как импортируется модуль vlc.
# Если у тебя VLC установлен в другую папку — поменяй путь ниже.
# ----------------------------------------------------------------------
VLC_INSTALL_DIRS = [
    r"C:\Program Files\VideoLAN\VLC",
    r"C:\Program Files (x86)\VideoLAN\VLC",
]

for _vlc_dir in VLC_INSTALL_DIRS:
    if os.path.isdir(_vlc_dir):
        try:
            os.add_dll_directory(_vlc_dir)
        except (AttributeError, OSError):
            pass
        os.environ["PATH"] = _vlc_dir + os.pathsep + os.environ.get("PATH", "")
        break
else:
    print(
        "ПРЕДУПРЕЖДЕНИЕ: не найдена папка установки VLC ни по одному "
        "из путей в VLC_INSTALL_DIRS. Если VLC установлен в нестандартную "
        "папку, добавь её путь в список VLC_INSTALL_DIRS в начале файла."
    )

import vlc

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QSlider,
    QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea, QStackedWidget,
    QListWidget, QListWidgetItem, QFrame, QSizePolicy, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QFont, QCursor


# ======================================================================
#  КОНФИГУРАЦИЯ
# ======================================================================

DB_PATH = r"D:\YandexMusicLocal\music_collection.db"
COVER_PLACEHOLDER = "♪"
COLUMNS_IN_GRID = 4


# ======================================================================
#  ФУНКЦИИ РАБОТЫ С БАЗОЙ ДАННЫХ
# ======================================================================

def db_connect():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"База данных не найдена: {DB_PATH}")
    return sqlite3.connect(DB_PATH)


def get_all_albums():
    """Возвращает список кортежей (id, artist, album_name, year)."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, artist, album_name, year FROM albums ORDER BY artist, year"
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_album_info(album_id):
    """Возвращает (artist, album_name, year) для одного альбома."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT artist, album_name, year FROM albums WHERE id = ?",
        (album_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_tracks_for_album(album_id):
    """Возвращает список кортежей (id, track_name, genre, file_path)."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, track_name, genre, file_path FROM tracks "
        "WHERE album_id = ? ORDER BY id",
        (album_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_all_tracks():
    """
    Возвращает список словарей (file_path, track_name, artist) для ВСЕХ
    треков библиотеки сразу — используется кнопками "Играть всё" и
    "Перемешать всё" на главной странице.
    """
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT tracks.track_name, tracks.file_path, albums.artist "
        "FROM tracks JOIN albums ON tracks.album_id = albums.id "
        "ORDER BY albums.artist, albums.album_name, tracks.id"
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for track_name, file_path, artist in rows:
        display_name = track_name if track_name else os.path.basename(file_path)
        result.append({
            "file_path": file_path,
            "track_name": display_name,
            "artist": artist if artist else "Неизвестный исполнитель",
        })
    return result


def format_ms(milliseconds):
    """Форматирует миллисекунды в строку вида MM:SS для отображения в плеере."""
    if milliseconds is None or milliseconds < 0:
        milliseconds = 0
    total_seconds = milliseconds // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


# ======================================================================
#  ВИДЖЕТ: КАРТОЧКА АЛЬБОМА
# ======================================================================

class AlbumCard(QFrame):
    """Кликабельная карточка альбома для сетки на главной странице."""

    clicked = pyqtSignal(int)

    def __init__(self, album_id, artist, album_name, year, parent=None):
        super().__init__(parent)
        self.album_id = album_id
        self.setObjectName("AlbumCard")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedSize(190, 240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        cover = QLabel(COVER_PLACEHOLDER)
        cover.setObjectName("CoverPlaceholder")
        cover.setFixedSize(166, 166)
        cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover.setFont(QFont("Segoe UI", 42))

        title_text = album_name if album_name else "Без названия"
        title = QLabel(title_text)
        title.setObjectName("CardTitle")
        title.setWordWrap(True)
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))

        subtitle_text = artist if artist else "Неизвестный исполнитель"
        if year:
            subtitle_text += f" • {year}"
        subtitle = QLabel(subtitle_text)
        subtitle.setObjectName("CardSubtitle")
        subtitle.setWordWrap(True)
        subtitle.setFont(QFont("Segoe UI", 9))

        layout.addWidget(cover, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.album_id)
        super().mousePressEvent(event)


# ======================================================================
#  СТРАНИЦА: ГЛАВНАЯ (СЕТКА АЛЬБОМОВ)
# ======================================================================

class HomePage(QWidget):
    """Главная страница со скроллируемой сеткой альбомов."""

    album_selected = pyqtSignal(int)
    play_all_requested = pyqtSignal()
    shuffle_all_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(24, 24, 24, 24)
        outer_layout.setSpacing(16)

        header_row = QHBoxLayout()

        header = QLabel("Моя коллекция")
        header.setObjectName("PageHeader")
        header.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        header_row.addWidget(header)

        header_row.addStretch()

        self.play_all_button = QPushButton("▶  Играть всё")
        self.play_all_button.setObjectName("HeaderActionButton")
        self.play_all_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.play_all_button.clicked.connect(self.play_all_requested.emit)
        header_row.addWidget(self.play_all_button)

        self.shuffle_all_button = QPushButton("🔀  Перемешать всё")
        self.shuffle_all_button.setObjectName("HeaderActionButton")
        self.shuffle_all_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.shuffle_all_button.clicked.connect(self.shuffle_all_requested.emit)
        header_row.addWidget(self.shuffle_all_button)

        outer_layout.addLayout(header_row)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setObjectName("HomeScrollArea")
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(18)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.scroll_area.setWidget(self.grid_container)
        outer_layout.addWidget(self.scroll_area)

        self.load_albums()

    def load_albums(self):
        # очищаем текущую сетку (на случай перезагрузки)
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        try:
            albums = get_all_albums()
        except FileNotFoundError as exc:
            error_label = QLabel(str(exc))
            error_label.setObjectName("ErrorLabel")
            self.grid_layout.addWidget(error_label, 0, 0)
            return

        row = 0
        col = 0
        for album_id, artist, album_name, year in albums:
            card = AlbumCard(album_id, artist, album_name, year)
            card.clicked.connect(self.album_selected.emit)
            self.grid_layout.addWidget(card, row, col)

            col += 1
            if col >= COLUMNS_IN_GRID:
                col = 0
                row += 1


# ======================================================================
#  СТРАНИЦА: ТРЕКИ АЛЬБОМА
# ======================================================================

class AlbumDetailPage(QWidget):
    """Страница со списком треков одного альбома."""

    back_clicked = pyqtSignal()
    track_activated = pyqtSignal(int)  # индекс трека в self.current_queue (клик = сразу играть)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_artist = ""
        self.current_album_name = ""
        self.current_queue = []  # список dict: file_path, track_name, artist

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        top_row = QHBoxLayout()
        self.back_button = QPushButton("←  Назад")
        self.back_button.setObjectName("BackButton")
        self.back_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.back_button.clicked.connect(self.back_clicked.emit)
        top_row.addWidget(self.back_button)
        top_row.addStretch()
        layout.addLayout(top_row)

        self.album_title_label = QLabel("")
        self.album_title_label.setObjectName("PageHeader")
        self.album_title_label.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        layout.addWidget(self.album_title_label)

        self.album_subtitle_label = QLabel("")
        self.album_subtitle_label.setObjectName("CardSubtitle")
        self.album_subtitle_label.setFont(QFont("Segoe UI", 11))
        layout.addWidget(self.album_subtitle_label)

        self.track_list = QListWidget()
        self.track_list.setObjectName("TrackList")
        self.track_list.setSpacing(3)
        self.track_list.itemClicked.connect(self.on_item_clicked)
        layout.addWidget(self.track_list)

    def load_album(self, album_id):
        info = get_album_info(album_id)
        if info is None:
            self.album_title_label.setText("Альбом не найден")
            self.album_subtitle_label.setText("")
            self.track_list.clear()
            return

        artist, album_name, year = info
        self.current_artist = artist if artist else "Неизвестный исполнитель"
        self.current_album_name = album_name if album_name else "Без названия"

        self.album_title_label.setText(self.current_album_name)
        subtitle = self.current_artist
        if year:
            subtitle += f" • {year}"
        self.album_subtitle_label.setText(subtitle)

        self.track_list.clear()
        self.current_queue = []
        tracks = get_tracks_for_album(album_id)
        for index, (track_id, track_name, genre, file_path) in enumerate(tracks, start=1):
            display_name = track_name if track_name else os.path.basename(file_path)
            item_text = f"{index:02d}.   {display_name}"
            item = QListWidgetItem(item_text)
            self.track_list.addItem(item)

            self.current_queue.append({
                "file_path": file_path,
                "track_name": display_name,
                "artist": self.current_artist,
            })

    def get_queue_data(self):
        """Возвращает список треков текущего альбома для плейлиста/очереди."""
        return self.current_queue

    def on_item_clicked(self, item: QListWidgetItem):
        index = self.track_list.row(item)
        self.track_activated.emit(index)


# ======================================================================
#  ЛЕВАЯ ПАНЕЛЬ ПЛЕЕРА
# ======================================================================

class PlayerBar(QWidget):
    """Панель плеера слева: обложка, название трека, кнопка Play/Pause, громкость."""

    play_pause_clicked = pyqtSignal()
    volume_changed = pyqtSignal(int)
    next_clicked = pyqtSignal()
    previous_clicked = pyqtSignal()
    queue_item_clicked = pyqtSignal(int)
    shuffle_toggled = pyqtSignal(bool)
    seek_requested = pyqtSignal(int)  # значение 0-1000 (промилле от длины трека)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.setFixedWidth(260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 30, 20, 30)
        layout.setSpacing(18)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        logo_label = QLabel("Local Music")
        logo_label.setObjectName("Logo")
        logo_label.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        layout.addWidget(logo_label)

        layout.addSpacing(20)

        self.cover_label = QLabel(COVER_PLACEHOLDER)
        self.cover_label.setObjectName("PlayerCover")
        self.cover_label.setFixedSize(200, 200)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setFont(QFont("Segoe UI", 50))
        layout.addWidget(self.cover_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.track_name_label = QLabel("Ничего не играет")
        self.track_name_label.setObjectName("TrackNameLabel")
        self.track_name_label.setWordWrap(True)
        self.track_name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.track_name_label.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        layout.addWidget(self.track_name_label)

        self.artist_label = QLabel("—")
        self.artist_label.setObjectName("ArtistLabel")
        self.artist_label.setWordWrap(True)
        self.artist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artist_label.setFont(QFont("Segoe UI", 10))
        layout.addWidget(self.artist_label)

        layout.addSpacing(14)

        # --- прогресс трека / перемотка ---
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setObjectName("ProgressSlider")
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.setValue(0)
        self.progress_slider.sliderPressed.connect(self._on_seek_start)
        self.progress_slider.sliderReleased.connect(self._on_seek_end)
        self._user_seeking = False
        layout.addWidget(self.progress_slider)

        time_row = QHBoxLayout()
        self.time_label = QLabel("00:00")
        self.time_label.setObjectName("VolumeLabel")
        self.time_label.setFont(QFont("Segoe UI", 8))
        self.duration_label = QLabel("00:00")
        self.duration_label.setObjectName("VolumeLabel")
        self.duration_label.setFont(QFont("Segoe UI", 8))
        time_row.addWidget(self.time_label)
        time_row.addStretch()
        time_row.addWidget(self.duration_label)
        layout.addLayout(time_row)

        layout.addSpacing(10)

        # --- кнопки Shuffle / Prev / Play-Pause / Next ---
        button_row = QHBoxLayout()
        button_row.setSpacing(14)
        button_row.addStretch()

        self.shuffle_button = QPushButton("🔀")
        self.shuffle_button.setObjectName("NavButton")
        self.shuffle_button.setCheckable(True)
        self.shuffle_button.setFixedSize(40, 40)
        self.shuffle_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.shuffle_button.setFont(QFont("Segoe UI", 13))
        self.shuffle_button.toggled.connect(self.shuffle_toggled.emit)
        button_row.addWidget(self.shuffle_button)

        self.previous_button = QPushButton("⏮")
        self.previous_button.setObjectName("NavButton")
        self.previous_button.setFixedSize(40, 40)
        self.previous_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.previous_button.setFont(QFont("Segoe UI", 14))
        self.previous_button.clicked.connect(self.previous_clicked.emit)
        button_row.addWidget(self.previous_button)

        self.play_pause_button = QPushButton("▶")
        self.play_pause_button.setObjectName("PlayPauseButton")
        self.play_pause_button.setFixedSize(64, 64)
        self.play_pause_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.play_pause_button.setFont(QFont("Segoe UI", 20))
        self.play_pause_button.clicked.connect(self.play_pause_clicked.emit)
        button_row.addWidget(self.play_pause_button)

        self.next_button = QPushButton("⏭")
        self.next_button.setObjectName("NavButton")
        self.next_button.setFixedSize(40, 40)
        self.next_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.next_button.setFont(QFont("Segoe UI", 14))
        self.next_button.clicked.connect(self.next_clicked.emit)
        button_row.addWidget(self.next_button)

        button_row.addStretch()
        layout.addLayout(button_row)

        layout.addSpacing(20)

        # --- громкость ---
        volume_label = QLabel("Громкость")
        volume_label.setObjectName("VolumeLabel")
        volume_label.setFont(QFont("Segoe UI", 9))
        layout.addWidget(volume_label)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("VolumeSlider")
        self.volume_slider.setMinimum(0)
        self.volume_slider.setMaximum(100)
        self.volume_slider.setValue(70)
        self.volume_slider.valueChanged.connect(self.volume_changed.emit)
        layout.addWidget(self.volume_slider)

        layout.addSpacing(20)

        # --- очередь воспроизведения (плейлист) ---
        queue_label = QLabel("Очередь")
        queue_label.setObjectName("VolumeLabel")
        queue_label.setFont(QFont("Segoe UI", 9))
        layout.addWidget(queue_label)

        self.queue_list = QListWidget()
        self.queue_list.setObjectName("QueueList")
        self.queue_list.setMaximumHeight(220)
        self.queue_list.setSpacing(3)
        self.queue_list.itemClicked.connect(self._on_queue_item_clicked)
        layout.addWidget(self.queue_list)

        layout.addStretch()

    def set_now_playing(self, track_name, artist):
        self.track_name_label.setText(track_name if track_name else "Неизвестный трек")
        self.artist_label.setText(artist if artist else "Неизвестный исполнитель")

    def set_play_icon(self):
        self.play_pause_button.setText("▶")

    def set_pause_icon(self):
        self.play_pause_button.setText("⏸")

    def set_shuffle_state(self, enabled):
        """Синхронизирует визуальное состояние кнопки 🔀, не вызывая лишний сигнал."""
        self.shuffle_button.blockSignals(True)
        self.shuffle_button.setChecked(enabled)
        self.shuffle_button.blockSignals(False)

    def _on_seek_start(self):
        self._user_seeking = True

    def _on_seek_end(self):
        self._user_seeking = False
        self.seek_requested.emit(self.progress_slider.value())

    def update_progress(self, promille, current_ms, total_ms):
        """Обновляет ползунок прогресса и метки времени (кроме случая, когда юзер сам тянет ползунок)."""
        if not self._user_seeking:
            self.progress_slider.blockSignals(True)
            self.progress_slider.setValue(max(0, min(1000, promille)))
            self.progress_slider.blockSignals(False)
        self.time_label.setText(format_ms(current_ms))
        self.duration_label.setText(format_ms(total_ms))

    def update_queue(self, queue_items, current_index):
        """Перерисовывает список очереди, выделяя текущий трек."""
        self.queue_list.clear()
        for i, track in enumerate(queue_items):
            prefix = "▶  " if i == current_index else "     "
            text = f"{prefix}{track['track_name']}"
            list_item = QListWidgetItem(text)

            if i == current_index:
                font = list_item.font()
                font.setBold(True)
                list_item.setFont(font)
                list_item.setForeground(Qt.GlobalColor.yellow)

            self.queue_list.addItem(list_item)

        # прокручиваем так, чтобы текущий трек был виден
        if 0 <= current_index < self.queue_list.count():
            self.queue_list.setCurrentRow(current_index)

    def _on_queue_item_clicked(self, item: QListWidgetItem):
        index = self.queue_list.row(item)
        self.queue_item_clicked.emit(index)


# ======================================================================
#  ГЛАВНОЕ ОКНО
# ======================================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Local Music — локальный плеер")
        self.resize(1280, 800)

        # --- VLC-движок ---
        self.vlc_instance = vlc.Instance()
        self.player = self.vlc_instance.media_player_new()
        self.current_file_path = None

        # --- очередь воспроизведения (плейлист) ---
        self.queue = []          # список dict: file_path, track_name, artist
        self.queue_index = -1    # индекс текущего трека в self.queue
        self._track_end_handled = False

        # история реально сыгранных треков (для корректной кнопки "Назад" при shuffle)
        self.play_history = []
        self.history_pos = -1
        self.shuffle_enabled = False

        # таймер, который следит за прогрессом/окончанием трека
        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(300)
        self.playback_timer.timeout.connect(self.check_playback_state)
        self.playback_timer.start()

        # --- центральный виджет ---
        central_widget = QWidget()
        central_layout = QHBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self.setCentralWidget(central_widget)

        # --- левая панель плеера ---
        self.player_bar = PlayerBar()
        self.player_bar.play_pause_clicked.connect(self.toggle_play_pause)
        self.player_bar.volume_changed.connect(self.on_volume_changed)
        self.player_bar.next_clicked.connect(self.play_next_track)
        self.player_bar.previous_clicked.connect(self.play_previous_track)
        self.player_bar.queue_item_clicked.connect(self.on_queue_item_clicked)
        self.player_bar.shuffle_toggled.connect(self.on_shuffle_toggled)
        self.player_bar.seek_requested.connect(self.on_seek_requested)
        central_layout.addWidget(self.player_bar)

        # применяем стартовую громкость к движку
        self.player.audio_set_volume(self.player_bar.volume_slider.value())

        # --- разделительная линия ---
        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedWidth(1)
        central_layout.addWidget(divider)

        # --- правая часть: стек страниц ---
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setObjectName("MainStack")

        self.home_page = HomePage()
        self.home_page.album_selected.connect(self.show_album_detail)
        self.home_page.play_all_requested.connect(self.on_play_all)
        self.home_page.shuffle_all_requested.connect(self.on_shuffle_all)

        self.album_detail_page = AlbumDetailPage()
        self.album_detail_page.back_clicked.connect(self.show_home_page)
        self.album_detail_page.track_activated.connect(self.on_track_activated)

        self.stacked_widget.addWidget(self.home_page)          # index 0
        self.stacked_widget.addWidget(self.album_detail_page)  # index 1

        central_layout.addWidget(self.stacked_widget, stretch=1)

        self.apply_stylesheet()

    # ------------------------------------------------------------------
    #  НАВИГАЦИЯ МЕЖДУ СТРАНИЦАМИ
    # ------------------------------------------------------------------

    def show_album_detail(self, album_id):
        self.album_detail_page.load_album(album_id)
        self.stacked_widget.setCurrentIndex(1)

    def show_home_page(self):
        self.stacked_widget.setCurrentIndex(0)

    # ------------------------------------------------------------------
    #  ВОСПРОИЗВЕДЕНИЕ
    # ------------------------------------------------------------------

    def on_track_activated(self, index):
        """Клик по треку в альбоме: очередь = весь альбом, старт с этого трека."""
        self.queue = self.album_detail_page.get_queue_data()
        self.play_history = []
        self.history_pos = -1
        self.jump_to(index)

    def on_play_all(self):
        """Кнопка 'Играть всё': очередь = вся библиотека по порядку."""
        try:
            tracks = get_all_tracks()
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "База данных не найдена", str(exc))
            return
        if not tracks:
            return

        self.shuffle_enabled = False
        self.player_bar.set_shuffle_state(False)
        self.queue = tracks
        self.play_history = []
        self.history_pos = -1
        self.jump_to(0)

    def on_shuffle_all(self):
        """Кнопка 'Перемешать всё': очередь = вся библиотека в случайном порядке."""
        try:
            tracks = get_all_tracks()
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "База данных не найдена", str(exc))
            return
        if not tracks:
            return

        random.shuffle(tracks)
        self.shuffle_enabled = True
        self.player_bar.set_shuffle_state(True)
        self.queue = tracks
        self.play_history = []
        self.history_pos = -1
        self.jump_to(0)

    def jump_to(self, index, record_history=True):
        """Переключается на трек с данным индексом в self.queue и запоминает его в истории."""
        if not (0 <= index < len(self.queue)):
            return

        self.queue_index = index
        self.play_current_queue_item()

        if record_history:
            # обрезаем "будущую" историю, если до этого жали "Назад"
            self.play_history = self.play_history[:self.history_pos + 1]
            self.play_history.append(index)
            self.history_pos = len(self.play_history) - 1

    def play_current_queue_item(self):
        """Запускает трек, на который сейчас указывает self.queue_index."""
        if not (0 <= self.queue_index < len(self.queue)):
            return

        track = self.queue[self.queue_index]
        file_path = track["file_path"]
        track_name = track["track_name"]
        artist = track["artist"]

        if not file_path or not os.path.exists(file_path):
            QMessageBox.warning(
                self, "Файл не найден",
                f"Не удалось найти аудиофайл:\n{file_path}"
            )
            return

        # останавливаем текущее воспроизведение, если оно было
        self.player.stop()

        media = self.vlc_instance.media_new(file_path)
        self.player.set_media(media)
        self.player.play()

        self.current_file_path = file_path
        self._track_end_handled = False

        self.player_bar.set_now_playing(track_name, artist)
        self.player_bar.set_pause_icon()
        self.player_bar.update_queue(self.queue, self.queue_index)
        self.player_bar.update_progress(0, 0, 0)

    def play_next_track(self):
        """Переходит к следующему треку: случайному (если включён shuffle) или по порядку."""
        if not self.queue:
            return

        if self.shuffle_enabled and len(self.queue) > 1:
            choices = [i for i in range(len(self.queue)) if i != self.queue_index]
            next_index = random.choice(choices)
        else:
            next_index = self.queue_index + 1
            if next_index >= len(self.queue):
                return

        self.jump_to(next_index)

    def play_previous_track(self):
        """
        Возвращается к реально предыдущему сыгранному треку (из истории) —
        это важно при включённом shuffle, где "предыдущий по списку" не подходит.
        """
        if self.history_pos > 0:
            self.history_pos -= 1
            prev_index = self.play_history[self.history_pos]
            self.jump_to(prev_index, record_history=False)
        elif not self.shuffle_enabled and self.queue_index - 1 >= 0:
            self.jump_to(self.queue_index - 1)

    def on_queue_item_clicked(self, index):
        """Клик по треку прямо в списке очереди — сразу переключаемся на него."""
        self.jump_to(index)

    def on_shuffle_toggled(self, enabled):
        self.shuffle_enabled = enabled

    def on_seek_requested(self, promille):
        """Перемотка: пользователь отпустил ползунок прогресса на определённой позиции."""
        if self.current_file_path is None:
            return
        length_ms = self.player.get_length()
        if length_ms and length_ms > 0:
            target_ms = int(length_ms * promille / 1000)
            self.player.set_time(target_ms)

    def check_playback_state(self):
        """Вызывается таймером: обновляет прогресс-бар и включает следующий трек по окончании."""
        if self.current_file_path is None:
            return

        state = self.player.get_state()

        if state == vlc.State.Ended and not self._track_end_handled:
            self._track_end_handled = True
            self.play_next_track()
            return

        if state in (vlc.State.Playing, vlc.State.Paused):
            length_ms = self.player.get_length()
            time_ms = self.player.get_time()
            promille = int(1000 * time_ms / length_ms) if length_ms and length_ms > 0 else 0
            self.player_bar.update_progress(promille, time_ms, length_ms if length_ms and length_ms > 0 else 0)

    def toggle_play_pause(self):
        if self.queue_index == -1:
            # ничего ещё не выбрано для воспроизведения
            return

        state = self.player.get_state()

        if state == vlc.State.Playing:
            self.player.pause()
            self.player_bar.set_play_icon()
        elif state in (vlc.State.Paused, vlc.State.Stopped, vlc.State.Ended):
            self.player.play()
            self.player_bar.set_pause_icon()
        else:
            # состояние NothingSpecial / Opening / Buffering и т.п.
            self.player.play()
            self.player_bar.set_pause_icon()

    def on_volume_changed(self, value):
        self.player.audio_set_volume(value)

    # ------------------------------------------------------------------
    #  ЗАКРЫТИЕ ПРИЛОЖЕНИЯ
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        try:
            self.player.stop()
        except Exception:
            pass
        super().closeEvent(event)

    # ------------------------------------------------------------------
    #  ТЁМНАЯ ТЕМА (QSS)
    # ------------------------------------------------------------------

    def apply_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f0f0f;
            }

            QWidget {
                background-color: #0f0f0f;
                color: #ffffff;
                font-family: "Segoe UI";
            }

            #PlayerBar {
                background-color: #181818;
                border-right: 1px solid #262626;
            }

            #Divider {
                background-color: #262626;
            }

            #MainStack {
                background-color: #0f0f0f;
            }

            #PageHeader {
                color: #ffffff;
            }

            #Logo {
                color: #ffcc00;
            }

            #PlayerCover, #CoverPlaceholder {
                background-color: #262626;
                border-radius: 10px;
                color: #555555;
            }

            #TrackNameLabel {
                color: #ffffff;
            }

            #ArtistLabel, #VolumeLabel, #CardSubtitle {
                color: #a0a0a0;
            }

            #AlbumCard {
                background-color: #181818;
                border-radius: 12px;
            }

            #AlbumCard:hover {
                background-color: #232323;
            }

            #CardTitle {
                color: #ffffff;
            }

            #ErrorLabel {
                color: #ff5555;
                font-size: 14px;
            }

            #PlayPauseButton {
                background-color: #ffcc00;
                color: #0f0f0f;
                border: none;
                border-radius: 32px;
                font-weight: bold;
            }

            #PlayPauseButton:hover {
                background-color: #ffd633;
            }

            #PlayPauseButton:pressed {
                background-color: #e6b800;
            }

            #NavButton {
                background-color: #232323;
                color: #ffffff;
                border: none;
                border-radius: 20px;
            }

            #NavButton:hover {
                background-color: #2f2f2f;
                color: #ffcc00;
            }

            #NavButton:pressed {
                background-color: #1a1a1a;
            }

            #NavButton:checked {
                background-color: #ffcc00;
                color: #0f0f0f;
            }

            #HeaderActionButton {
                background-color: #232323;
                color: #ffffff;
                border: none;
                border-radius: 18px;
                padding: 8px 18px;
                font-size: 13px;
                margin-left: 8px;
            }

            #HeaderActionButton:hover {
                background-color: #2f2f2f;
                color: #ffcc00;
            }

            #ProgressSlider::groove:horizontal {
                background: #333333;
                height: 5px;
                border-radius: 2px;
            }

            #ProgressSlider::handle:horizontal {
                background: #ffcc00;
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }

            #ProgressSlider::sub-page:horizontal {
                background: #ffcc00;
                border-radius: 2px;
            }

            #QueueList {
                background-color: #101010;
                border: none;
                border-radius: 8px;
                padding: 4px;
                font-size: 11px;
            }

            #QueueList::item {
                padding: 10px 8px;
                border-radius: 5px;
                color: #b0b0b0;
            }

            #QueueList::item:hover {
                background-color: #1e1e1e;
            }

            #QueueList::item:selected {
                background-color: #232323;
            }

            #BackButton {
                background-color: #232323;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
            }

            #BackButton:hover {
                background-color: #2f2f2f;
            }

            #TrackList {
                background-color: #161616;
                border: none;
                border-radius: 10px;
                padding: 6px;
                font-size: 13px;
            }

            #TrackList::item {
                padding: 13px 12px;
                border-radius: 6px;
            }

            #TrackList::item:hover {
                background-color: #222222;
            }

            #TrackList::item:selected {
                background-color: #2b2b2b;
                color: #ffcc00;
            }

            QScrollArea, #HomeScrollArea {
                border: none;
                background-color: transparent;
            }

            QScrollBar:vertical {
                background: #0f0f0f;
                width: 10px;
                margin: 0px;
            }

            QScrollBar::handle:vertical {
                background: #333333;
                border-radius: 5px;
                min-height: 30px;
            }

            QScrollBar::handle:vertical:hover {
                background: #444444;
            }

            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }

            QSlider::groove:horizontal {
                background: #333333;
                height: 4px;
                border-radius: 2px;
            }

            QSlider::handle:horizontal {
                background: #ffcc00;
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }

            QSlider::sub-page:horizontal {
                background: #ffcc00;
                border-radius: 2px;
            }
        """)


# ======================================================================
#  ТОЧКА ВХОДА
# ======================================================================

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
