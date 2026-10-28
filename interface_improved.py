# -*- coding: utf-8 -*-
"""
Local Music — локальный аудиоплеер (Python + PyQt6 + python-vlc + SQLite + Mutagen).

Ключевые особенности этой версии:
    • все чтения обложек и длительностей вынесены в QThreadPool (GUI не блокируется);
    • списки треков — кастомные строки в стиле Яндекс Музыки (обложка 40x40, hover-play);
    • запуск трека одиночным кликом;
    • ленивая подгрузка: метаданные читаются только для видимых элементов;
    • тёмная премиальная тема с акцентом #fff000.
"""

import os
import sys
import sqlite3
import random
from pathlib import Path

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

try:
    import vlc
except Exception as exc:
    vlc = None
    VLC_IMPORT_ERROR = exc
else:
    VLC_IMPORT_ERROR = None

from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

from PyQt6.QtCore import (
    Qt,
    QObject,
    QRunnable,
    QThreadPool,
    QTimer,
    QMutex,
    QSize,
    QRectF,
    QPoint,
    QPropertyAnimation,
    QEasingCurve,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QFont,
    QFontMetrics,
    QCursor,
    QPixmap,
    QImage,
    QPainter,
    QPainterPath,
    QColor,
    QLinearGradient,
    QBrush,
)
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QScrollArea,
    QListWidget,
    QListWidgetItem,
    QFrame,
    QSizePolicy,
    QMessageBox,
    QLineEdit,
    QComboBox,
    QStackedWidget,
    QAbstractItemView,
    QGraphicsOpacityEffect,
)

# ======================================================================
# КОНФИГУРАЦИЯ
# ======================================================================

DB_PATH = r"D:\YandexMusicLocal\music_collection.db"
MUSIC_BASE_DIR = Path(r"D:\Music")
COVER_FILENAMES = (
    "cover.jpg", "cover.jpeg", "cover.png",
    "folder.jpg", "folder.jpeg", "folder.png",
    "front.jpg", "front.jpeg", "front.png",
    "album.jpg", "album.jpeg", "album.png",
)

ACCENT = "#fff000"
ACCENT_HOVER = "#fff64d"
ACCENT_PRESSED = "#e0d400"
BACKGROUND = "#0f0f12"
SURFACE = "#18181c"
SURFACE_2 = "#1e1e24"
SURFACE_3 = "#26262e"
LIST_BG = "#141418"
TEXT = "#ffffff"
MUTED = "#a0a0a8"
DIM = "#6c6c78"

PLACEHOLDER_CHAR = "♪"
ROW_HEIGHT = 58
CARD_WIDTH = 200
CARD_COVER = 176
ROW_BATCH = 60
CARD_BATCH = 24


# ======================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ======================================================================

def db_connect():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"База данных не найдена:\n{DB_PATH}")
    return sqlite3.connect(DB_PATH)


def format_ms(milliseconds):
    if milliseconds is None or milliseconds < 0:
        milliseconds = 0

    total_seconds = int(milliseconds // 1000)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def safe_text(value, fallback="—"):
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def file_sort_key(path):
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0


# ======================================================================
# РАБОТА С БАЗОЙ ДАННЫХ
# ======================================================================

def get_all_albums(sort_mode="Исполнитель"):
    conn = db_connect()
    cur = conn.cursor()

    order_map = {
        "Исполнитель": "artist COLLATE NOCASE, album_name COLLATE NOCASE",
        "Название": "album_name COLLATE NOCASE, artist COLLATE NOCASE",
        "Год": "year IS NULL, year, album_name COLLATE NOCASE",
    }
    order_by = order_map.get(sort_mode, order_map["Исполнитель"])

    cur.execute(
        f"""
        SELECT id, artist, album_name, year
        FROM albums
        ORDER BY {order_by}
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_album_cover_paths():
    """Один запрос вместо N+1: путь первого трека для каждого альбома."""
    try:
        conn = db_connect()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT album_id, MIN(id), file_path
            FROM tracks
            GROUP BY album_id
            """
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return {}
    return {album_id: path for album_id, _min_id, path in rows}


def get_album_info(album_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT artist, album_name, year
        FROM albums
        WHERE id = ?
        """,
        (album_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_tracks_for_album(album_id, sort_mode="Номер"):
    conn = db_connect()
    cur = conn.cursor()

    order_map = {
        "Номер": "tracks.id",
        "Название": "tracks.track_name COLLATE NOCASE",
        "Жанр": "tracks.genre COLLATE NOCASE, tracks.track_name COLLATE NOCASE",
    }
    order_by = order_map.get(sort_mode, "tracks.id")

    cur.execute(
        f"""
        SELECT
            tracks.id,
            tracks.track_name,
            tracks.genre,
            tracks.file_path,
            albums.artist,
            albums.album_name,
            albums.year
        FROM tracks
        JOIN albums ON tracks.album_id = albums.id
        WHERE tracks.album_id = ?
        ORDER BY {order_by}
        """,
        (album_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def _rows_to_tracks(rows):
    result = []
    for track_id, title, genre, path, artist, album, year in rows:
        result.append(
            {
                "id": track_id,
                "track_name": safe_text(
                    title, Path(path).stem if path else "Неизвестный трек"
                ),
                "genre": safe_text(genre, "Разное"),
                "file_path": path,
                "artist": safe_text(artist, "Неизвестный исполнитель"),
                "album": safe_text(album, "Без названия"),
                "year": year,
            }
        )
    return result


def get_all_tracks():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            tracks.id,
            tracks.track_name,
            tracks.genre,
            tracks.file_path,
            albums.artist,
            albums.album_name,
            albums.year
        FROM tracks
        JOIN albums ON tracks.album_id = albums.id
        """
    )
    rows = cur.fetchall()
    conn.close()
    return _rows_to_tracks(rows)


def search_tracks(query_text):
    query_text = (query_text or "").strip()
    if not query_text:
        return get_all_tracks()

    conn = db_connect()
    cur = conn.cursor()
    pattern = f"%{query_text}%"

    cur.execute(
        """
        SELECT
            tracks.id,
            tracks.track_name,
            tracks.genre,
            tracks.file_path,
            albums.artist,
            albums.album_name,
            albums.year
        FROM tracks
        JOIN albums ON tracks.album_id = albums.id
        WHERE
            tracks.track_name LIKE ?
            OR tracks.genre LIKE ?
            OR tracks.file_path LIKE ?
            OR albums.artist LIKE ?
            OR albums.album_name LIKE ?
        """,
        (pattern, pattern, pattern, pattern, pattern),
    )
    rows = cur.fetchall()
    conn.close()
    return _rows_to_tracks(rows)


# ======================================================================
# ГРАФИЧЕСКИЕ ХЕЛПЕРЫ
# ======================================================================

_PLACEHOLDER_CACHE = {}


def make_placeholder_pixmap(size=300, radius=None):
    if radius is None:
        radius = max(6, int(size * 0.07))

    key = (int(size), int(radius))
    cached = _PLACEHOLDER_CACHE.get(key)
    if cached is not None:
        return cached

    pixmap = QPixmap(int(size), int(size))
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)

    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#26262e"))
    gradient.setColorAt(1.0, QColor("#15151a"))
    painter.fillPath(path, QBrush(gradient))

    note_color = QColor(ACCENT)
    note_color.setAlpha(70)
    painter.setPen(note_color)
    painter.setFont(QFont("Segoe UI Symbol", max(10, int(size * 0.34))))
    painter.drawText(
        QRectF(0, 0, size, size),
        int(Qt.AlignmentFlag.AlignCenter),
        PLACEHOLDER_CHAR,
    )
    painter.end()

    _PLACEHOLDER_CACHE[key] = pixmap
    return pixmap


def fit_image(image, size):
    """Масштаб «по большей стороне» + центральная обрезка. Потокобезопасно."""
    if image is None or image.isNull():
        return None

    size = int(size)
    scaled = image.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - size) // 2)
    y = max(0, (scaled.height() - size) // 2)
    return scaled.copy(x, y, size, size)


def rounded_pixmap_from_image(image, size, radius):
    if image is None or image.isNull():
        return make_placeholder_pixmap(size, radius)

    if image.width() != size or image.height() != size:
        image = fit_image(image, size)
        if image is None:
            return make_placeholder_pixmap(size, radius)

    source = QPixmap.fromImage(image)
    result = QPixmap(int(size), int(size))
    result.fill(Qt.GlobalColor.transparent)

    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, source)
    painter.end()

    return result


def fade_in(widget, duration=240):
    """Плавное «проявление» виджета после подгрузки обложки."""
    try:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _cleanup():
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                pass

        animation.finished.connect(_cleanup)
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    except RuntimeError:
        pass


# ======================================================================
# ПОТОКОБЕЗОПАСНЫЙ КЭШ МЕТАДАННЫХ
# ======================================================================

class MediaMetadataCache:
    """Кэш длительностей и обложек. Чтение выполняется в рабочих потоках."""

    MAX_IMAGES = 1200

    def __init__(self):
        self._mutex = QMutex()
        self._durations = {}
        self._images = {}
        self._image_order = []
        self._folder_covers = {}

    # ---------------- длительность ----------------

    def cached_duration(self, file_path):
        if not file_path:
            return 0
        self._mutex.lock()
        try:
            return self._durations.get(file_path)
        finally:
            self._mutex.unlock()

    def duration_ms(self, file_path):
        if not file_path:
            return 0

        cached = self.cached_duration(file_path)
        if cached is not None:
            return cached

        duration_ms = 0
        try:
            ext = Path(file_path).suffix.lower()
            if ext == ".mp3":
                audio = MP3(file_path)
            elif ext == ".m4a":
                audio = MP4(file_path)
            else:
                audio = None

            if audio is not None and getattr(audio, "info", None):
                length = float(getattr(audio.info, "length", 0) or 0)
                duration_ms = max(0, int(length * 1000))
        except Exception:
            duration_ms = 0

        self._mutex.lock()
        try:
            self._durations[file_path] = duration_ms
        finally:
            self._mutex.unlock()

        return duration_ms

    # ---------------- обложки ----------------

    def cached_image(self, file_path, size):
        key = (file_path, int(size))
        self._mutex.lock()
        try:
            if key in self._images:
                return True, self._images[key]
            return False, None
        finally:
            self._mutex.unlock()

    def _store_image(self, key, image):
        self._mutex.lock()
        try:
            if key not in self._images:
                self._image_order.append(key)
            self._images[key] = image
            while len(self._image_order) > self.MAX_IMAGES:
                old = self._image_order.pop(0)
                self._images.pop(old, None)
        finally:
            self._mutex.unlock()

    def _folder_cover_bytes(self, folder):
        folder_key = str(folder)

        self._mutex.lock()
        try:
            if folder_key in self._folder_covers:
                return self._folder_covers[folder_key]
        finally:
            self._mutex.unlock()

        data = None
        try:
            for filename in COVER_FILENAMES:
                candidate = folder / filename
                if candidate.is_file():
                    data = candidate.read_bytes()
                    break
        except (OSError, ValueError):
            data = None

        self._mutex.lock()
        try:
            self._folder_covers[folder_key] = data
        finally:
            self._mutex.unlock()

        return data

    def read_cover_bytes(self, file_path):
        if not file_path:
            return None

        cover = None
        try:
            ext = Path(file_path).suffix.lower()

            if ext == ".mp3":
                audio = MP3(file_path)
                if audio.tags:
                    apic_frames = audio.tags.getall("APIC")
                    if apic_frames:
                        cover = apic_frames[0].data

            elif ext == ".m4a":
                audio = MP4(file_path)
                if audio.tags:
                    covers = audio.tags.get("covr")
                    if covers:
                        cover = bytes(covers[0])
        except Exception:
            cover = None

        if cover is None:
            try:
                cover = self._folder_cover_bytes(Path(file_path).parent)
            except (OSError, ValueError):
                cover = None

        return cover

    def load_image(self, file_path, size):
        """Блокирующее чтение обложки. Вызывается только из рабочего потока."""
        key = (file_path, int(size))
        found, image = self.cached_image(file_path, size)
        if found:
            return image

        data = self.read_cover_bytes(file_path)
        image = None

        if data:
            raw = QImage.fromData(data)
            if not raw.isNull():
                image = fit_image(raw, size)

        self._store_image(key, image)
        return image


MEDIA_CACHE = MediaMetadataCache()


# ======================================================================
# АСИНХРОННЫЙ ЗАГРУЗЧИК (QThreadPool + QRunnable)
# ======================================================================

class _WorkerSignals(QObject):
    finished = pyqtSignal(object)


class _CoverTask(QRunnable):
    def __init__(self, file_path, size, generation, token):
        super().__init__()
        self.setAutoDelete(True)
        self.file_path = file_path
        self.size = size
        self.generation = generation
        self.token = token
        self.signals = _WorkerSignals()

    def run(self):
        if self.generation != LOADER.generation:
            return
        try:
            image = MEDIA_CACHE.load_image(self.file_path, self.size)
        except Exception:
            image = None
        try:
            self.signals.finished.emit((self.token, image))
        except RuntimeError:
            pass


class _DurationTask(QRunnable):
    def __init__(self, file_path, generation, token):
        super().__init__()
        self.setAutoDelete(True)
        self.file_path = file_path
        self.generation = generation
        self.token = token
        self.signals = _WorkerSignals()

    def run(self):
        if self.generation != LOADER.generation:
            return
        try:
            duration = MEDIA_CACHE.duration_ms(self.file_path)
        except Exception:
            duration = 0
        try:
            self.signals.finished.emit((self.token, duration))
        except RuntimeError:
            pass


class MediaLoader(QObject):
    """Единая точка входа для фоновых чтений с диска."""

    def __init__(self):
        super().__init__()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, min(8, os.cpu_count() or 4)))
        self.generation = 0

    def cancel_pending(self):
        """Помечает все ранее поставленные задачи как неактуальные."""
        self.generation += 1

    def request_cover(self, file_path, size, token, callback):
        if not file_path:
            callback((token, None))
            return

        found, image = MEDIA_CACHE.cached_image(file_path, size)
        if found:
            callback((token, image))
            return

        task = _CoverTask(file_path, size, self.generation, token)
        task.signals.finished.connect(callback)
        self.pool.start(task)

    def request_duration(self, file_path, token, callback):
        if not file_path:
            callback((token, 0))
            return

        cached = MEDIA_CACHE.cached_duration(file_path)
        if cached is not None:
            callback((token, cached))
            return

        task = _DurationTask(file_path, self.generation, token)
        task.signals.finished.connect(callback)
        self.pool.start(task)

    def shutdown(self):
        self.cancel_pending()
        self.pool.clear()
        self.pool.waitForDone(1500)


LOADER = MediaLoader()


# ======================================================================
# БАЗОВЫЕ ВИДЖЕТЫ
# ======================================================================

class ElidedLabel(QLabel):
    """QLabel с аккуратным многоточием, не растягивающий раскладку."""

    def __init__(self, text="", color=TEXT, parent=None):
        super().__init__(text, parent)
        self._full_text = text or ""
        self._color = QColor(color)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text):
        self._full_text = text or ""
        super().setText(self._full_text)
        self.update()

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(self.font())
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            max(0, self.width()),
        )
        painter.setPen(self._color)
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            elided,
        )
        painter.end()


class AsyncCoverLabel(QLabel):
    """Обложка, которая подгружается в фоне и плавно проявляется."""

    def __init__(self, size, radius, parent=None):
        super().__init__(parent)
        self._size = int(size)
        self._radius = int(radius)
        self._token = 0
        self.setFixedSize(self._size, self._size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setPixmap(make_placeholder_pixmap(self._size, self._radius))

    def reset(self):
        self._token += 1
        self.setPixmap(make_placeholder_pixmap(self._size, self._radius))

    def load(self, file_path):
        self._token += 1
        self.setPixmap(make_placeholder_pixmap(self._size, self._radius))
        LOADER.request_cover(file_path, self._size, self._token, self._on_ready)

    def _on_ready(self, payload):
        try:
            token, image = payload
            if token != self._token:
                return
            if image is None or image.isNull():
                return
            self.setPixmap(
                rounded_pixmap_from_image(image, self._size, self._radius)
            )
            fade_in(self)
        except RuntimeError:
            pass


# ======================================================================
# СТРОКА ТРЕКА В СТИЛЕ ЯНДЕКС МУЗЫКИ
# ======================================================================

class TrackRowWidget(QWidget):
    clicked = pyqtSignal(int)

    ART_SIZE = 40

    def __init__(self, index, track, show_number=True, parent=None):
        super().__init__(parent)
        self.setObjectName("TrackRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(ROW_HEIGHT - 4)

        self.index = index
        self.track = track
        self._loaded = False
        self._cover_token = 0
        self._duration_token = 0
        self._playing = False

        self.setProperty("hovered", False)
        self.setProperty("playing", False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 18, 5)
        layout.setSpacing(13)

        if show_number:
            self.number_label = QLabel(str(index + 1))
            self.number_label.setObjectName("RowNumber")
            self.number_label.setFixedWidth(24)
            self.number_label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.number_label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
            layout.addWidget(self.number_label)
        else:
            self.number_label = None

        self.art = QLabel()
        self.art.setObjectName("RowArt")
        self.art.setFixedSize(self.ART_SIZE, self.ART_SIZE)
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art.setPixmap(make_placeholder_pixmap(self.ART_SIZE, 6))
        self.art.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        layout.addWidget(self.art)

        self.play_overlay = QLabel("▶", self.art)
        self.play_overlay.setObjectName("RowPlayOverlay")
        self.play_overlay.setGeometry(0, 0, self.ART_SIZE, self.ART_SIZE)
        self.play_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.play_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._overlay_effect = QGraphicsOpacityEffect(self.play_overlay)
        self._overlay_effect.setOpacity(0.0)
        self.play_overlay.setGraphicsEffect(self._overlay_effect)
        self._overlay_anim = QPropertyAnimation(
            self._overlay_effect, b"opacity", self
        )
        self._overlay_anim.setDuration(170)
        self._overlay_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)

        title_font = QFont("Segoe UI", 11)
        title_font.setWeight(QFont.Weight.DemiBold)

        subtitle_font = QFont("Segoe UI", 9)

        self.title_label = ElidedLabel(
            safe_text(track.get("track_name"), "Неизвестный трек"), TEXT
        )
        self.title_label.setFont(title_font)
        text_box.addWidget(self.title_label)

        self.subtitle_label = ElidedLabel(
            safe_text(track.get("artist"), "Неизвестный исполнитель"), MUTED
        )
        self.subtitle_label.setFont(subtitle_font)
        text_box.addWidget(self.subtitle_label)

        layout.addLayout(text_box, stretch=1)

        self.album_label = ElidedLabel(safe_text(track.get("album"), ""), DIM)
        self.album_label.setFont(QFont("Segoe UI", 9))
        self.album_label.setFixedWidth(190)
        layout.addWidget(self.album_label)

        self.duration_label = QLabel("--:--")
        self.duration_label.setObjectName("RowDuration")
        self.duration_label.setFixedWidth(52)
        self.duration_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.duration_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        layout.addWidget(self.duration_label)

    # ---------------- ленивые данные ----------------

    def ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True

        file_path = self.track.get("file_path")

        self._cover_token += 1
        LOADER.request_cover(file_path, self.ART_SIZE, self._cover_token,
                             self._on_cover_ready)

        self._duration_token += 1
        LOADER.request_duration(file_path, self._duration_token,
                                self._on_duration_ready)

    def _on_cover_ready(self, payload):
        try:
            token, image = payload
            if token != self._cover_token:
                return
            if image is None or image.isNull():
                return
            self.art.setPixmap(
                rounded_pixmap_from_image(image, self.ART_SIZE, 6)
            )
            fade_in(self.art, 220)
        except RuntimeError:
            pass

    def _on_duration_ready(self, payload):
        try:
            token, duration = payload
            if token != self._duration_token:
                return
            self.duration_label.setText(format_ms(duration))
        except RuntimeError:
            pass

    # ---------------- состояние ----------------

    def set_playing(self, playing):
        if self._playing == playing:
            return
        self._playing = playing
        self.setProperty("playing", playing)
        self.title_label.set_color(ACCENT if playing else TEXT)
        if self.number_label is not None:
            self.number_label.setText(
                "▶" if playing else str(self.index + 1)
            )
        self._repolish()

    def _repolish(self):
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def _animate_overlay(self, target):
        self._overlay_anim.stop()
        self._overlay_anim.setStartValue(self._overlay_effect.opacity())
        self._overlay_anim.setEndValue(target)
        self._overlay_anim.start()

    def enterEvent(self, event):
        self.setProperty("hovered", True)
        self._repolish()
        self._animate_overlay(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setProperty("hovered", False)
        self._repolish()
        self._animate_overlay(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)


# ======================================================================
# СПИСОК ТРЕКОВ (лениво наполняемый)
# ======================================================================

class TrackListWidget(QListWidget):
    track_clicked = pyqtSignal(int)

    def __init__(self, show_numbers=True, parent=None):
        super().__init__(parent)
        self.setObjectName("TrackList")
        self.show_numbers = show_numbers

        self._tracks = []
        self._created = 0
        self._playing_path = None

        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setUniformItemSizes(True)
        self.setSpacing(0)

        self._visible_timer = QTimer(self)
        self._visible_timer.setSingleShot(True)
        self._visible_timer.setInterval(40)
        self._visible_timer.timeout.connect(self._update_visible)

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    # ---------------- наполнение ----------------

    def set_tracks(self, tracks):
        LOADER.cancel_pending()
        self.clear()
        self._tracks = list(tracks or [])
        self._created = 0
        self.verticalScrollBar().setValue(0)
        self._append_batch()
        QTimer.singleShot(0, self._fill_viewport)

    def _append_batch(self):
        if self._created >= len(self._tracks):
            return

        end = min(len(self._tracks), self._created + ROW_BATCH)
        for index in range(self._created, end):
            track = self._tracks[index]
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            self.addItem(item)

            row = TrackRowWidget(index, track, self.show_numbers, self)
            if self._playing_path and track.get("file_path") == self._playing_path:
                row.set_playing(True)
            row.clicked.connect(self.track_clicked.emit)
            self.setItemWidget(item, row)

        self._created = end
        self._visible_timer.start()

    def _fill_viewport(self):
        if self._created >= len(self._tracks):
            return
        if self.verticalScrollBar().maximum() <= 0:
            self._append_batch()
            QTimer.singleShot(0, self._fill_viewport)

    def _on_scroll(self, value):
        bar = self.verticalScrollBar()
        if bar.maximum() - value < 700:
            self._append_batch()
        self._visible_timer.start()

    def _update_visible(self):
        count = self.count()
        if count == 0:
            return

        viewport_height = max(10, self.viewport().height())
        first_index = self.indexAt(QPoint(6, 6))
        last_index = self.indexAt(QPoint(6, viewport_height - 6))

        first = first_index.row() if first_index.isValid() else 0
        last = last_index.row() if last_index.isValid() else count - 1

        start = max(0, first - 4)
        end = min(count - 1, last + 8)

        for row_index in range(start, end + 1):
            item = self.item(row_index)
            if item is None:
                continue
            widget = self.itemWidget(item)
            if widget is not None:
                widget.ensure_loaded()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._visible_timer.start()
        QTimer.singleShot(0, self._fill_viewport)

    def showEvent(self, event):
        super().showEvent(event)
        self._visible_timer.start()
        QTimer.singleShot(0, self._fill_viewport)

    # ---------------- подсветка играющего ----------------

    def set_playing_path(self, file_path):
        self._playing_path = file_path
        for row_index in range(self.count()):
            item = self.item(row_index)
            widget = self.itemWidget(item) if item else None
            if widget is None:
                continue
            widget.set_playing(
                bool(file_path) and widget.track.get("file_path") == file_path
            )


# ======================================================================
# КАРТОЧКА АЛЬБОМА
# ======================================================================

class AlbumCard(QFrame):
    clicked = pyqtSignal(int)

    def __init__(self, album_id, artist, album_name, year,
                 cover_path=None, parent=None):
        super().__init__(parent)
        self.album_id = album_id
        self.cover_path = cover_path
        self._cover_requested = False

        self.setObjectName("AlbumCard")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedSize(CARD_WIDTH, 268)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 14)
        layout.setSpacing(12)

        self.cover = AsyncCoverLabel(CARD_COVER, 12, self)
        layout.addWidget(self.cover, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.play_badge = QLabel("▶", self.cover)
        self.play_badge.setObjectName("CardPlayBadge")
        self.play_badge.setFixedSize(44, 44)
        self.play_badge.move(CARD_COVER - 56, CARD_COVER - 56)
        self.play_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.play_badge.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._badge_effect = QGraphicsOpacityEffect(self.play_badge)
        self._badge_effect.setOpacity(0.0)
        self.play_badge.setGraphicsEffect(self._badge_effect)
        self._badge_anim = QPropertyAnimation(self._badge_effect, b"opacity", self)
        self._badge_anim.setDuration(180)
        self._badge_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        title_font = QFont("Segoe UI", 10)
        title_font.setWeight(QFont.Weight.DemiBold)

        self.title = ElidedLabel(safe_text(album_name, "Без названия"), TEXT)
        self.title.setFont(title_font)
        self.title.setFixedHeight(20)
        layout.addWidget(self.title)

        subtitle_text = safe_text(artist, "Неизвестный исполнитель")
        if year:
            subtitle_text += f"  •  {year}"

        self.subtitle = ElidedLabel(subtitle_text, MUTED)
        self.subtitle.setFont(QFont("Segoe UI", 9))
        self.subtitle.setFixedHeight(18)
        layout.addWidget(self.subtitle)

    def ensure_cover(self):
        if self._cover_requested:
            return
        self._cover_requested = True
        if self.cover_path:
            self.cover.load(self.cover_path)

    def _animate_badge(self, target):
        self._badge_anim.stop()
        self._badge_anim.setStartValue(self._badge_effect.opacity())
        self._badge_anim.setEndValue(target)
        self._badge_anim.start()

    def enterEvent(self, event):
        self._animate_badge(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_badge(0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.album_id)
        super().mousePressEvent(event)


# ======================================================================
# БЛОК «СЕЙЧАС ИГРАЕТ»
# ======================================================================

class NowPlayingHero(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NowPlayingHero")
        self.setFixedHeight(232)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(26, 24, 30, 24)
        layout.setSpacing(28)

        self.cover = AsyncCoverLabel(184, 16, self)
        layout.addWidget(self.cover, alignment=Qt.AlignmentFlag.AlignVCenter)

        info = QVBoxLayout()
        info.setContentsMargins(0, 6, 0, 6)
        info.setSpacing(6)

        eyebrow = QLabel("СЕЙЧАС ИГРАЕТ")
        eyebrow.setObjectName("HeroEyebrow")
        eyebrow_font = QFont("Segoe UI", 8)
        eyebrow_font.setWeight(QFont.Weight.Bold)
        eyebrow.setFont(eyebrow_font)
        info.addWidget(eyebrow)

        track_font = QFont("Segoe UI", 24)
        track_font.setWeight(QFont.Weight.Bold)

        self.track_label = ElidedLabel("Ничего не играет", TEXT)
        self.track_label.setFont(track_font)
        self.track_label.setFixedHeight(42)
        info.addWidget(self.track_label)

        self.artist_label = ElidedLabel("—", "#d7d7dd")
        self.artist_label.setFont(QFont("Segoe UI", 13))
        self.artist_label.setFixedHeight(24)
        info.addWidget(self.artist_label)

        self.meta_label = ElidedLabel("Альбом: —", MUTED)
        self.meta_label.setFont(QFont("Segoe UI", 10))
        self.meta_label.setFixedHeight(20)
        info.addWidget(self.meta_label)

        self.extra_label = ElidedLabel("Жанр: —   •   Год: —", DIM)
        self.extra_label.setFont(QFont("Segoe UI", 10))
        self.extra_label.setFixedHeight(20)
        info.addWidget(self.extra_label)

        info.addStretch()

        layout.addLayout(info, stretch=1)

    def set_track(self, track, cover_path):
        self.track_label.setText(
            safe_text(track.get("track_name"), "Неизвестный трек")
        )
        self.artist_label.setText(
            safe_text(track.get("artist"), "Неизвестный исполнитель")
        )
        self.meta_label.setText(
            f"Альбом: {safe_text(track.get('album'), '—')}"
        )
        year = track.get("year")
        self.extra_label.setText(
            f"Жанр: {safe_text(track.get('genre'), 'Разное')}"
            f"   •   Год: {year if year else '—'}"
        )
        self.cover.load(cover_path)


# ======================================================================
# СТРАНИЦА: ГЛАВНАЯ
# ======================================================================

class HomePage(QWidget):
    album_selected = pyqtSignal(int)
    play_all_requested = pyqtSignal()
    shuffle_all_requested = pyqtSignal()
    sort_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._cards = []
        self._pending_albums = []
        self._cover_map = {}
        self._created = 0
        self._columns = 0
        self._loaded_once = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 22, 26, 14)
        outer.setSpacing(18)

        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        heading = QVBoxLayout()
        heading.setSpacing(2)

        title_font = QFont("Segoe UI", 23)
        title_font.setWeight(QFont.Weight.Bold)

        title = QLabel("Моя коллекция")
        title.setObjectName("PageTitle")
        title.setFont(title_font)
        heading.addWidget(title)

        self.count_label = QLabel("Загрузка библиотеки…")
        self.count_label.setObjectName("SecondaryText")
        heading.addWidget(self.count_label)

        header_row.addLayout(heading)
        header_row.addStretch()

        self.sort_combo = QComboBox()
        self.sort_combo.setObjectName("SortCombo")
        self.sort_combo.addItems(["Исполнитель", "Название", "Год"])
        self.sort_combo.currentTextChanged.connect(self.sort_changed.emit)
        header_row.addWidget(self.sort_combo)

        self.shuffle_button = QPushButton("Перемешать")
        self.shuffle_button.setObjectName("GhostButton")
        self.shuffle_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.shuffle_button.clicked.connect(self.shuffle_all_requested.emit)
        header_row.addWidget(self.shuffle_button)

        self.play_all_button = QPushButton("▶  Слушать всё")
        self.play_all_button.setObjectName("AccentButton")
        self.play_all_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.play_all_button.clicked.connect(self.play_all_requested.emit)
        header_row.addWidget(self.play_all_button)

        outer.addLayout(header_row)

        self.hero = NowPlayingHero()
        outer.addWidget(self.hero)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("HomeScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.grid_container = QWidget()
        self.grid_container.setObjectName("GridContainer")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(0, 4, 10, 24)
        self.grid_layout.setHorizontalSpacing(18)
        self.grid_layout.setVerticalSpacing(20)
        self.grid_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )

        self.scroll_area.setWidget(self.grid_container)
        outer.addWidget(self.scroll_area, stretch=1)

        self._visible_timer = QTimer(self)
        self._visible_timer.setSingleShot(True)
        self._visible_timer.setInterval(45)
        self._visible_timer.timeout.connect(self._update_visible_cards)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(180)
        self._resize_timer.timeout.connect(self._maybe_relayout)

        self.scroll_area.verticalScrollBar().valueChanged.connect(
            lambda _v: self._visible_timer.start()
        )

    # ---------------- загрузка ----------------

    def ensure_loaded(self):
        if not self._loaded_once:
            self.load_albums()

    def load_albums(self, sort_mode=None):
        if sort_mode:
            self.sort_combo.blockSignals(True)
            if self.sort_combo.findText(sort_mode) >= 0:
                self.sort_combo.setCurrentText(sort_mode)
            self.sort_combo.blockSignals(False)

        LOADER.cancel_pending()
        self._loaded_once = True
        self._clear_grid()

        try:
            albums = get_all_albums(self.sort_combo.currentText())
            self._cover_map = get_album_cover_paths()
        except FileNotFoundError as exc:
            self.count_label.setText("База не найдена")
            error = QLabel(str(exc))
            error.setObjectName("ErrorLabel")
            error.setWordWrap(True)
            self.grid_layout.addWidget(error, 0, 0)
            return

        self.count_label.setText(f"{len(albums)} альбомов в библиотеке")

        self._pending_albums = albums
        self._created = 0
        self._columns = self._calculate_columns()
        self._add_card_batch()

    def _clear_grid(self):
        self._cards = []
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def _add_card_batch(self):
        if self._created >= len(self._pending_albums):
            self._visible_timer.start()
            return

        end = min(len(self._pending_albums), self._created + CARD_BATCH)
        columns = max(1, self._columns)

        for position in range(self._created, end):
            album_id, artist, album_name, year = self._pending_albums[position]
            card = AlbumCard(
                album_id,
                artist,
                album_name,
                year,
                self._cover_map.get(album_id),
            )
            card.clicked.connect(self.album_selected.emit)
            self.grid_layout.addWidget(
                card, position // columns, position % columns
            )
            self._cards.append(card)

        self._created = end
        self._visible_timer.start()
        QTimer.singleShot(0, self._add_card_batch)

    def _calculate_columns(self):
        width = max(600, self.scroll_area.viewport().width() or self.width())
        return max(2, min(8, (width - 10) // (CARD_WIDTH + 18)))

    def _update_visible_cards(self):
        if not self._cards:
            return

        bar = self.scroll_area.verticalScrollBar()
        top = bar.value() - 320
        bottom = bar.value() + self.scroll_area.viewport().height() + 700

        for card in self._cards:
            y = card.y()
            if y + card.height() >= top and y <= bottom:
                card.ensure_cover()

    def _maybe_relayout(self):
        if not self._loaded_once:
            return
        new_columns = self._calculate_columns()
        if new_columns != self._columns:
            self.load_albums(self.sort_combo.currentText())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start()
        self._visible_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._visible_timer.start()


# ======================================================================
# СТРАНИЦА: АЛЬБОМ
# ======================================================================

class AlbumDetailPage(QWidget):
    back_clicked = pyqtSignal()
    track_activated = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_album_id = None
        self.current_queue = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 22, 26, 14)
        outer.setSpacing(18)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        self.back_button = QPushButton("←  Назад")
        self.back_button.setObjectName("GhostButton")
        self.back_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.back_button.clicked.connect(self.back_clicked.emit)
        top_row.addWidget(self.back_button)
        top_row.addStretch()

        sort_caption = QLabel("Сортировка")
        sort_caption.setObjectName("SecondaryText")
        top_row.addWidget(sort_caption)

        self.sort_combo = QComboBox()
        self.sort_combo.setObjectName("SortCombo")
        self.sort_combo.addItems(["Номер", "Название", "Жанр"])
        self.sort_combo.currentTextChanged.connect(
            lambda _t: self._reload_current_album()
        )
        top_row.addWidget(self.sort_combo)

        outer.addLayout(top_row)

        header = QFrame()
        header.setObjectName("AlbumHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(22, 20, 24, 20)
        header_layout.setSpacing(24)

        self.album_cover = AsyncCoverLabel(150, 16, self)
        header_layout.addWidget(
            self.album_cover, alignment=Qt.AlignmentFlag.AlignVCenter
        )

        header_text = QVBoxLayout()
        header_text.setSpacing(6)

        kicker = QLabel("АЛЬБОМ")
        kicker.setObjectName("HeroEyebrow")
        kicker_font = QFont("Segoe UI", 8)
        kicker_font.setWeight(QFont.Weight.Bold)
        kicker.setFont(kicker_font)
        header_text.addWidget(kicker)

        title_font = QFont("Segoe UI", 23)
        title_font.setWeight(QFont.Weight.Bold)

        self.album_title = ElidedLabel("Альбом", TEXT)
        self.album_title.setFont(title_font)
        self.album_title.setFixedHeight(40)
        header_text.addWidget(self.album_title)

        self.album_subtitle = ElidedLabel("—", MUTED)
        self.album_subtitle.setFont(QFont("Segoe UI", 11))
        self.album_subtitle.setFixedHeight(22)
        header_text.addWidget(self.album_subtitle)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(10)

        self.play_album_button = QPushButton("▶  Слушать")
        self.play_album_button.setObjectName("AccentButton")
        self.play_album_button.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor)
        )
        self.play_album_button.clicked.connect(lambda: self.track_activated.emit(0))
        buttons_row.addWidget(self.play_album_button)
        buttons_row.addStretch()

        header_text.addLayout(buttons_row)
        header_layout.addLayout(header_text, stretch=1)

        outer.addWidget(header)

        self.track_list = TrackListWidget(show_numbers=True)
        self.track_list.track_clicked.connect(self.track_activated.emit)
        outer.addWidget(self.track_list, stretch=1)

    def load_album(self, album_id):
        self.current_album_id = album_id
        self._reload_current_album()

    def _reload_current_album(self):
        if self.current_album_id is None:
            return

        info = get_album_info(self.current_album_id)
        if not info:
            self.album_title.setText("Альбом не найден")
            self.album_subtitle.setText("")
            self.track_list.set_tracks([])
            return

        artist, album_name, year = info
        artist = safe_text(artist, "Неизвестный исполнитель")
        album_name = safe_text(album_name, "Без названия")

        rows = get_tracks_for_album(
            self.current_album_id, self.sort_combo.currentText()
        )

        self.current_queue = []
        for (track_id, title, genre, file_path,
             track_artist, track_album, track_year) in rows:
            self.current_queue.append(
                {
                    "id": track_id,
                    "track_name": safe_text(
                        title,
                        Path(file_path).stem if file_path else "Неизвестный трек",
                    ),
                    "genre": safe_text(genre, "Разное"),
                    "file_path": file_path,
                    "artist": safe_text(track_artist, artist),
                    "album": safe_text(track_album, album_name),
                    "year": track_year or year,
                }
            )

        self.album_title.setText(album_name)
        subtitle = artist
        if year:
            subtitle += f"  •  {year}"
        subtitle += f"  •  {len(self.current_queue)} треков"
        self.album_subtitle.setText(subtitle)

        if self.current_queue:
            self.album_cover.load(self.current_queue[0].get("file_path"))
        else:
            self.album_cover.reset()

        self.track_list.set_tracks(self.current_queue)

    def get_queue_data(self):
        return list(self.current_queue)


# ======================================================================
# СТРАНИЦА: ПОИСК / ВСЯ БИБЛИОТЕКА
# ======================================================================

class SearchPage(QWidget):
    track_activated = pyqtSignal(int)
    play_results_requested = pyqtSignal()
    shuffle_results_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.results = []
        self.current_query = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 22, 26, 14)
        outer.setSpacing(18)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        title_font = QFont("Segoe UI", 23)
        title_font.setWeight(QFont.Weight.Bold)

        title = QLabel("Треки")
        title.setObjectName("PageTitle")
        title.setFont(title_font)
        title_box.addWidget(title)

        self.result_count = QLabel("Введите запрос или просмотрите всю библиотеку.")
        self.result_count.setObjectName("SecondaryText")
        title_box.addWidget(self.result_count)

        title_row.addLayout(title_box)
        title_row.addStretch()

        sort_caption = QLabel("Сортировка")
        sort_caption.setObjectName("SecondaryText")
        title_row.addWidget(sort_caption)

        self.sort_combo = QComboBox()
        self.sort_combo.setObjectName("SortCombo")
        self.sort_combo.addItems(
            [
                "Название",
                "Исполнитель",
                "Альбом",
                "Жанр",
                "Год",
                "Длительность",
                "Размер файла",
                "Изменение файла",
            ]
        )
        self.sort_combo.currentTextChanged.connect(lambda _t: self.resort())
        title_row.addWidget(self.sort_combo)

        self.shuffle_button = QPushButton("Перемешать")
        self.shuffle_button.setObjectName("GhostButton")
        self.shuffle_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.shuffle_button.clicked.connect(self.shuffle_results_requested.emit)
        title_row.addWidget(self.shuffle_button)

        self.play_button = QPushButton("▶  Слушать")
        self.play_button.setObjectName("AccentButton")
        self.play_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.play_button.clicked.connect(self.play_results_requested.emit)
        title_row.addWidget(self.play_button)

        outer.addLayout(title_row)

        self.track_list = TrackListWidget(show_numbers=False)
        self.track_list.track_clicked.connect(self._on_track_clicked)
        outer.addWidget(self.track_list, stretch=1)

    def set_query(self, text):
        self.current_query = text
        self.refresh()

    def refresh(self):
        try:
            self.results = search_tracks(self.current_query)
        except FileNotFoundError:
            self.results = []

        self._sort_results(self.sort_combo.currentText())
        self._populate()

    def resort(self):
        if not self.results:
            return
        self._sort_results(self.sort_combo.currentText())
        self._populate()

    def _sort_results(self, sort_mode):
        if sort_mode == "Название":
            self.results.sort(key=lambda x: x["track_name"].lower())
        elif sort_mode == "Исполнитель":
            self.results.sort(
                key=lambda x: (x["artist"].lower(), x["track_name"].lower())
            )
        elif sort_mode == "Альбом":
            self.results.sort(
                key=lambda x: (x["album"].lower(), x["track_name"].lower())
            )
        elif sort_mode == "Жанр":
            self.results.sort(
                key=lambda x: (x["genre"].lower(), x["track_name"].lower())
            )
        elif sort_mode == "Год":
            self.results.sort(key=lambda x: (x["year"] is None, x["year"] or 0))
        elif sort_mode == "Длительность":
            # Только уже вычисленные значения — диск в GUI-потоке не трогаем.
            self.results.sort(
                key=lambda x: MEDIA_CACHE.cached_duration(x["file_path"]) or 0
            )
        elif sort_mode == "Размер файла":
            def size_key(track):
                try:
                    return Path(track["file_path"]).stat().st_size
                except (OSError, TypeError, ValueError):
                    return 0
            self.results.sort(key=size_key)
        elif sort_mode == "Изменение файла":
            self.results.sort(key=lambda x: file_sort_key(x["file_path"]))

    def _populate(self):
        self.result_count.setText(
            f"Найдено треков: {len(self.results)}"
            if self.current_query
            else f"Треков в библиотеке: {len(self.results)}"
        )
        self.track_list.set_tracks(self.results)

    def _on_track_clicked(self, index):
        if 0 <= index < len(self.results):
            self.track_activated.emit(index)

    def selected_track(self):
        return None


# ======================================================================
# НИЖНИЙ ПЛЕЕР
# ======================================================================

class PlayerBar(QWidget):
    play_pause_clicked = pyqtSignal()
    volume_changed = pyqtSignal(int)
    next_clicked = pyqtSignal()
    previous_clicked = pyqtSignal()
    queue_item_clicked = pyqtSignal(int)
    shuffle_toggled = pyqtSignal(bool)
    seek_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.setFixedHeight(104)

        self._user_seeking = False

        root = QHBoxLayout(self)
        root.setContentsMargins(22, 12, 22, 12)
        root.setSpacing(24)

        # ---------- слева: обложка + название ----------
        left = QHBoxLayout()
        left.setSpacing(14)

        self.small_cover = AsyncCoverLabel(56, 10, self)
        left.addWidget(self.small_cover)

        now = QVBoxLayout()
        now.setSpacing(3)
        now.setContentsMargins(0, 4, 0, 4)

        track_font = QFont("Segoe UI", 10)
        track_font.setWeight(QFont.Weight.DemiBold)

        self.track_name_label = ElidedLabel("Ничего не играет", TEXT)
        self.track_name_label.setFont(track_font)
        self.track_name_label.setFixedHeight(20)
        now.addWidget(self.track_name_label)

        self.artist_label = ElidedLabel("—", MUTED)
        self.artist_label.setFont(QFont("Segoe UI", 9))
        self.artist_label.setFixedHeight(18)
        now.addWidget(self.artist_label)

        self.album_label = ElidedLabel("—", DIM)
        self.album_label.setFont(QFont("Segoe UI", 8))
        self.album_label.setFixedHeight(16)
        now.addWidget(self.album_label)

        left_container = QWidget()
        left_container.setFixedWidth(300)
        left_inner = QHBoxLayout(left_container)
        left_inner.setContentsMargins(0, 0, 0, 0)
        left_inner.setSpacing(14)
        left_inner.addLayout(left)
        left_inner.addLayout(now, stretch=1)

        root.addWidget(left_container)

        # ---------- центр: управление + прогресс ----------
        center = QVBoxLayout()
        center.setSpacing(6)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        controls.addStretch()

        self.shuffle_button = QPushButton("⇄")
        self.shuffle_button.setObjectName("NavButton")
        self.shuffle_button.setCheckable(True)
        self.shuffle_button.setFixedSize(38, 38)
        self.shuffle_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.shuffle_button.toggled.connect(self.shuffle_toggled.emit)
        controls.addWidget(self.shuffle_button)

        self.previous_button = QPushButton("⏮")
        self.previous_button.setObjectName("NavButton")
        self.previous_button.setFixedSize(38, 38)
        self.previous_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.previous_button.clicked.connect(self.previous_clicked.emit)
        controls.addWidget(self.previous_button)

        self.play_pause_button = QPushButton("▶")
        self.play_pause_button.setObjectName("PlayPauseButton")
        self.play_pause_button.setFixedSize(52, 52)
        self.play_pause_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.play_pause_button.clicked.connect(self.play_pause_clicked.emit)
        controls.addWidget(self.play_pause_button)

        self.next_button = QPushButton("⏭")
        self.next_button.setObjectName("NavButton")
        self.next_button.setFixedSize(38, 38)
        self.next_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.next_button.clicked.connect(self.next_clicked.emit)
        controls.addWidget(self.next_button)

        controls.addStretch()
        center.addLayout(controls)

        progress = QHBoxLayout()
        progress.setSpacing(10)

        self.time_label = QLabel("00:00")
        self.time_label.setObjectName("TimeLabel")
        self.time_label.setFixedWidth(44)
        self.time_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        progress.addWidget(self.time_label)

        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setObjectName("ProgressSlider")
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.setValue(0)
        self.progress_slider.setFixedHeight(18)
        self.progress_slider.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.progress_slider.sliderPressed.connect(self._on_seek_start)
        self.progress_slider.sliderReleased.connect(self._on_seek_end)
        progress.addWidget(self.progress_slider, stretch=1)

        self.duration_label = QLabel("00:00")
        self.duration_label.setObjectName("TimeLabel")
        self.duration_label.setFixedWidth(44)
        progress.addWidget(self.duration_label)

        center.addLayout(progress)
        root.addLayout(center, stretch=1)

        # ---------- справа: громкость и очередь ----------
        right = QHBoxLayout()
        right.setSpacing(12)

        volume_caption = QLabel("VOL")
        volume_caption.setObjectName("VolumeLabel")
        right.addWidget(volume_caption)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("VolumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.setFixedHeight(18)
        self.volume_slider.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.volume_slider.valueChanged.connect(self._emit_volume)
        right.addWidget(self.volume_slider)

        self.volume_value = QLabel("70")
        self.volume_value.setObjectName("VolumeLabel")
        self.volume_value.setFixedWidth(26)
        right.addWidget(self.volume_value)

        self.queue_combo = QComboBox()
        self.queue_combo.setObjectName("QueueCombo")
        self.queue_combo.setFixedWidth(230)
        self.queue_combo.currentIndexChanged.connect(self._on_queue_changed)
        right.addWidget(self.queue_combo)

        root.addLayout(right)

    def _emit_volume(self, value):
        self.volume_value.setText(str(value))
        self.volume_changed.emit(value)

    def set_now_playing(self, track):
        self.track_name_label.setText(
            safe_text(track.get("track_name"), "Неизвестный трек")
        )
        self.artist_label.setText(
            safe_text(track.get("artist"), "Неизвестный исполнитель")
        )
        self.album_label.setText(safe_text(track.get("album"), "—"))

    def set_cover(self, file_path):
        self.small_cover.load(file_path)

    def set_play_icon(self):
        if self.play_pause_button.text() != "▶":
            self.play_pause_button.setText("▶")

    def set_pause_icon(self):
        if self.play_pause_button.text() != "❚❚":
            self.play_pause_button.setText("❚❚")

    def set_shuffle_state(self, enabled):
        self.shuffle_button.blockSignals(True)
        self.shuffle_button.setChecked(enabled)
        self.shuffle_button.blockSignals(False)

    def _on_seek_start(self):
        self._user_seeking = True

    def _on_seek_end(self):
        self._user_seeking = False
        self.seek_requested.emit(self.progress_slider.value())

    def update_progress(self, promille, current_ms, total_ms):
        if not self._user_seeking:
            self.progress_slider.blockSignals(True)
            self.progress_slider.setValue(max(0, min(1000, int(promille))))
            self.progress_slider.blockSignals(False)

        self.time_label.setText(format_ms(current_ms))
        self.duration_label.setText(format_ms(total_ms))

    def update_queue(self, queue_items, current_index):
        self.queue_combo.blockSignals(True)
        self.queue_combo.clear()

        for index, track in enumerate(queue_items[:400]):
            title = safe_text(track.get("track_name"), "Неизвестный трек")
            artist = safe_text(track.get("artist"), "Неизвестный исполнитель")
            prefix = "▶  " if index == current_index else ""
            self.queue_combo.addItem(f"{prefix}{title} — {artist}")

        if 0 <= current_index < self.queue_combo.count():
            self.queue_combo.setCurrentIndex(current_index)

        self.queue_combo.blockSignals(False)

    def _on_queue_changed(self, index):
        if index >= 0:
            self.queue_item_clicked.emit(index)


# ======================================================================
# ГЛАВНОЕ ОКНО
# ======================================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Local Music")
        self.setMinimumSize(1180, 720)

        if vlc is None:
            QMessageBox.critical(
                self,
                "VLC не найден",
                "Не удалось импортировать python-vlc.\n\n"
                f"Ошибка: {VLC_IMPORT_ERROR}\n\n"
                "Установи VLC и python-vlc, затем запусти приложение снова.",
            )
            raise RuntimeError("python-vlc is unavailable")

        self.vlc_instance = vlc.Instance()
        self.player = self.vlc_instance.media_player_new()

        self.queue = []
        self.queue_index = -1
        self.current_file_path = None

        self.shuffle_enabled = False
        self.play_history = []
        self.history_pos = -1
        self.track_end_handled = False

        central = QWidget()
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = self._build_sidebar()
        root.addWidget(self.sidebar)

        right = QWidget()
        right.setObjectName("ContentArea")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(32, 18, 26, 10)
        search_row.setSpacing(12)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("SearchInput")
        self.search_input.setPlaceholderText(
            "Поиск по трекам, исполнителям, альбомам, жанрам и файлам"
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setFixedHeight(44)
        self.search_input.returnPressed.connect(self._perform_search)
        search_row.addWidget(self.search_input, stretch=1)

        search_button = QPushButton("Найти")
        search_button.setObjectName("AccentButton")
        search_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        search_button.clicked.connect(self._perform_search)
        search_row.addWidget(search_button)

        right_layout.addLayout(search_row)

        self.stack = QStackedWidget()
        self.stack.setObjectName("MainStack")

        self.home_page = HomePage()
        self.home_page.album_selected.connect(self.show_album)
        self.home_page.play_all_requested.connect(self.play_all)
        self.home_page.shuffle_all_requested.connect(self.shuffle_all)
        self.home_page.sort_changed.connect(self._home_sort_changed)

        self.album_page = AlbumDetailPage()
        self.album_page.back_clicked.connect(self.show_home)
        self.album_page.track_activated.connect(self.play_album_track)

        self.search_page = SearchPage()
        self.search_page.track_activated.connect(self.play_search_result)
        self.search_page.play_results_requested.connect(self.play_search_results)
        self.search_page.shuffle_results_requested.connect(
            self.shuffle_search_results
        )

        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.album_page)
        self.stack.addWidget(self.search_page)

        right_layout.addWidget(self.stack, stretch=1)

        self.player_bar = PlayerBar()
        self.player_bar.play_pause_clicked.connect(self.toggle_play_pause)
        self.player_bar.volume_changed.connect(self.on_volume_changed)
        self.player_bar.next_clicked.connect(self.play_next_track)
        self.player_bar.previous_clicked.connect(self.play_previous_track)
        self.player_bar.queue_item_clicked.connect(self.on_queue_item_clicked)
        self.player_bar.shuffle_toggled.connect(self.on_shuffle_toggled)
        self.player_bar.seek_requested.connect(self.on_seek_requested)

        right_layout.addWidget(self.player_bar)

        root.addWidget(right, stretch=1)

        self.player.audio_set_volume(self.player_bar.volume_slider.value())

        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(250)
        self.playback_timer.timeout.connect(self.check_playback_state)
        self.playback_timer.start()

        self.apply_stylesheet()
        self._set_active_nav(self.home_button)

        QTimer.singleShot(0, self.showMaximized)
        QTimer.singleShot(60, self.home_page.ensure_loaded)

    # ------------------------------------------------------------------
    # САЙДБАР
    # ------------------------------------------------------------------

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(238)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 26, 16, 20)
        layout.setSpacing(6)

        logo_font = QFont("Segoe UI", 14)
        logo_font.setWeight(QFont.Weight.Bold)

        logo = QLabel("LOCAL MUSIC")
        logo.setObjectName("Logo")
        logo.setFont(logo_font)
        logo.setContentsMargins(10, 0, 0, 0)
        layout.addWidget(logo)

        subtitle = QLabel("Локальная библиотека")
        subtitle.setObjectName("SidebarSub")
        subtitle.setContentsMargins(10, 0, 0, 0)
        layout.addWidget(subtitle)

        layout.addSpacing(26)

        self.home_button = self._make_nav_button("⌂     Главная")
        self.home_button.clicked.connect(self.show_home)

        self.library_button = self._make_nav_button("♫     Все треки")
        self.library_button.clicked.connect(self.show_library)

        self.search_button = self._make_nav_button("⌕     Поиск")
        self.search_button.clicked.connect(self.show_search)

        self.nav_buttons = [
            self.home_button,
            self.library_button,
            self.search_button,
        ]

        for button in self.nav_buttons:
            layout.addWidget(button)

        layout.addSpacing(22)

        hint = QLabel(
            "MP3 и M4A\n"
            "Обложки читаются из файлов в фоне\n"
            "Звук воспроизводится через VLC"
        )
        hint.setObjectName("SidebarHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addStretch()

        self.version_label = QLabel("Local Music • offline")
        self.version_label.setObjectName("SidebarFooter")
        self.version_label.setContentsMargins(10, 0, 0, 0)
        layout.addWidget(self.version_label)

        return sidebar

    @staticmethod
    def _make_nav_button(text):
        button = QPushButton(text)
        button.setObjectName("NavMenuButton")
        button.setCheckable(True)
        button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        button.setMinimumHeight(46)
        return button

    def _set_active_nav(self, active_button):
        for button in self.nav_buttons:
            button.blockSignals(True)
            button.setChecked(button is active_button)
            button.blockSignals(False)

    # ------------------------------------------------------------------
    # НАВИГАЦИЯ
    # ------------------------------------------------------------------

    def show_home(self):
        self.stack.setCurrentWidget(self.home_page)
        self.home_page.ensure_loaded()
        self._set_active_nav(self.home_button)

    def show_library(self):
        self.search_input.clear()
        self.search_page.set_query("")
        self.stack.setCurrentWidget(self.search_page)
        self._set_active_nav(self.library_button)

    def show_search(self):
        self.stack.setCurrentWidget(self.search_page)
        self.search_input.setFocus()
        self._set_active_nav(self.search_button)

    def _perform_search(self):
        query = self.search_input.text().strip()
        self.search_page.set_query(query)
        self.stack.setCurrentWidget(self.search_page)
        self._set_active_nav(self.search_button)

    def show_album(self, album_id):
        self.album_page.load_album(album_id)
        self.stack.setCurrentWidget(self.album_page)
        self._set_active_nav(None)
        self.album_page.track_list.set_playing_path(self.current_file_path)

    def _home_sort_changed(self, sort_mode):
        self.home_page.load_albums(sort_mode)

    # ------------------------------------------------------------------
    # ОЧЕРЕДЬ И ВОСПРОИЗВЕДЕНИЕ
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_track(track):
        result = dict(track)
        result.setdefault("track_name", "Неизвестный трек")
        result.setdefault("artist", "Неизвестный исполнитель")
        result.setdefault("album", "—")
        result.setdefault("genre", "Разное")
        result.setdefault("year", None)
        result.setdefault("file_path", "")
        return result

    def _set_queue(self, tracks, shuffle_state=False):
        self.queue = [self._normalise_track(track) for track in tracks]
        self.queue_index = 0
        self.play_history = [0]
        self.history_pos = 0
        self.shuffle_enabled = shuffle_state
        self.player_bar.set_shuffle_state(shuffle_state)
        self._start_current_track()

    def play_album_track(self, index):
        queue = self.album_page.get_queue_data()
        if not queue:
            return
        index = max(0, min(index, len(queue) - 1))
        self._set_queue(queue[index:], False)

    def play_search_result(self, index):
        results = self.search_page.results
        if not (0 <= index < len(results)):
            return
        self._set_queue(results[index:], False)

    def play_all(self):
        try:
            tracks = get_all_tracks()
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "База не найдена", str(exc))
            return
        if tracks:
            self._set_queue(tracks, False)

    def shuffle_all(self):
        try:
            tracks = get_all_tracks()
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "База не найдена", str(exc))
            return
        if tracks:
            random.shuffle(tracks)
            self._set_queue(tracks, True)

    def play_search_results(self):
        results = list(self.search_page.results)
        if results:
            self._set_queue(results, False)

    def shuffle_search_results(self):
        results = list(self.search_page.results)
        if results:
            random.shuffle(results)
            self._set_queue(results, True)

    def _start_current_track(self):
        if not (0 <= self.queue_index < len(self.queue)):
            return

        track = self.queue[self.queue_index]
        file_path = track.get("file_path")

        if not file_path or not os.path.exists(file_path):
            QMessageBox.warning(
                self,
                "Файл не найден",
                f"Не удалось найти аудиофайл:\n{file_path}",
            )
            self.play_next_track()
            return

        try:
            self.player.stop()
            media = self.vlc_instance.media_new(file_path)
            self.player.set_media(media)
            self.player.audio_set_volume(self.player_bar.volume_slider.value())

            if self.player.play() == -1:
                raise RuntimeError("VLC не смог начать воспроизведение файла.")
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Ошибка воспроизведения",
                f"Не удалось запустить:\n{file_path}\n\n{exc}",
            )
            return

        self.current_file_path = file_path
        self.track_end_handled = False

        self.player_bar.set_now_playing(track)
        self.player_bar.set_cover(file_path)
        self.player_bar.set_pause_icon()
        self.player_bar.update_queue(self.queue, self.queue_index)
        self.player_bar.update_progress(0, 0, 0)

        self.home_page.hero.set_track(track, file_path)
        self.album_page.track_list.set_playing_path(file_path)
        self.search_page.track_list.set_playing_path(file_path)

    def play_next_track(self):
        if not self.queue:
            return

        if self.shuffle_enabled and len(self.queue) > 1:
            available = [
                i for i in range(len(self.queue)) if i != self.queue_index
            ]
            next_index = random.choice(available)
        else:
            next_index = self.queue_index + 1
            if next_index >= len(self.queue):
                self.player_bar.set_play_icon()
                self.player.stop()
                return

        self.queue_index = next_index
        self.play_history = self.play_history[: self.history_pos + 1]
        self.play_history.append(next_index)
        self.history_pos = len(self.play_history) - 1

        self._start_current_track()

    def play_previous_track(self):
        if self.play_history and self.history_pos > 0:
            self.history_pos -= 1
            self.queue_index = self.play_history[self.history_pos]
            self._start_current_track()
            return

        if not self.shuffle_enabled and self.queue_index > 0:
            self.queue_index -= 1
            self.play_history = [self.queue_index]
            self.history_pos = 0
            self._start_current_track()

    def on_queue_item_clicked(self, index):
        if not (0 <= index < len(self.queue)):
            return
        self.queue_index = index
        self.play_history = [index]
        self.history_pos = 0
        self._start_current_track()

    # ------------------------------------------------------------------
    # УПРАВЛЕНИЕ
    # ------------------------------------------------------------------

    def toggle_play_pause(self):
        if self.queue_index < 0:
            return

        try:
            state = self.player.get_state()

            if state == vlc.State.Playing:
                self.player.pause()
                self.player_bar.set_play_icon()
            else:
                self.player.play()
                self.player_bar.set_pause_icon()
        except Exception:
            self.player.play()
            self.player_bar.set_pause_icon()

    def on_volume_changed(self, value):
        self.player.audio_set_volume(value)

    def on_shuffle_toggled(self, enabled):
        self.shuffle_enabled = enabled

    def on_seek_requested(self, promille):
        if not self.current_file_path:
            return

        length_ms = self.player.get_length()
        if length_ms and length_ms > 0:
            target_ms = int(length_ms * max(0, min(1000, promille)) / 1000)
            self.player.set_time(target_ms)

    def check_playback_state(self):
        if not self.current_file_path:
            return

        try:
            state = self.player.get_state()

            if state == vlc.State.Ended and not self.track_end_handled:
                self.track_end_handled = True
                self.play_next_track()
                return

            if state in (vlc.State.Playing, vlc.State.Paused):
                length_ms = self.player.get_length()
                time_ms = self.player.get_time()

                promille = (
                    int(1000 * max(0, time_ms) / length_ms)
                    if length_ms and length_ms > 0
                    else 0
                )

                self.player_bar.update_progress(
                    promille, max(0, time_ms), max(0, length_ms)
                )

                if state == vlc.State.Playing:
                    self.player_bar.set_pause_icon()
                else:
                    self.player_bar.set_play_icon()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # ЗАВЕРШЕНИЕ
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        for action in (
            self.playback_timer.stop,
            self.player.stop,
            LOADER.shutdown,
            self.vlc_instance.release,
        ):
            try:
                action()
            except Exception:
                pass
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # QSS
    # ------------------------------------------------------------------

    def apply_stylesheet(self):
        self.setStyleSheet(
            f"""
            QMainWindow,
            QWidget {{
                background-color: {BACKGROUND};
                color: {TEXT};
                font-family: "Segoe UI";
                font-size: 13px;
            }}
            QToolTip {{
                background: {SURFACE_3};
                color: {TEXT};
                border: none;
                border-radius: 8px;
                padding: 6px 10px;
            }}
            
            /* ---------------- САЙДБАР ---------------- */
            #Sidebar {{
                background-color: {SURFACE};
                border: none;
            }}
            #Logo {{
                color: {TEXT};
                letter-spacing: 3px;
            }}
            #SidebarSub {{
                color: {DIM};
                font-size: 11px;
            }}
            #SidebarHint {{
                color: {DIM};
                font-size: 11px;
                padding: 14px;
                background: {SURFACE_2};
                border: none;
                border-radius: 16px;
                line-height: 150%;
            }}
            #SidebarFooter {{
                color: #55555f;
                font-size: 11px;
            }}
            #NavMenuButton {{
                background: transparent;
                border: none;
                border-radius: 12px;
                text-align: left;
                padding: 12px 18px;
                color: #b6b6c0;
                font-size: 14px;
            }}
            #NavMenuButton:hover {{
                background: {SURFACE_2};
                color: {TEXT};
            }}
            #NavMenuButton:checked {{
                background: {SURFACE_3};
                color: {ACCENT};
                font-weight: bold;
            }}
            
            /* ---------------- СТРАНИЦЫ И ПОИСК ---------------- */
            #PageTitle {{
                color: {TEXT};
            }}
            #SecondaryText {{
                color: {MUTED};
            }}
            #SearchInput {{
                background: {SURFACE};
                color: {TEXT};
                border: 1px solid {SURFACE_3};
                border-radius: 14px;
                padding: 0px 18px;
                font-size: 14px;
            }}
            #SearchInput:focus {{
                border: 1px solid {ACCENT};
                background: {SURFACE_2};
            }}
            #AlbumHeader {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {SURFACE}, stop:1 {BACKGROUND});
                border-radius: 20px;
            }}
            
            /* Кнопки в стиле Яндекс Музыки */
            #AccentButton {{
                background: {ACCENT};
                color: #0f0f12;
                border: none;
                border-radius: 12px;
                padding: 10px 22px;
                font-weight: bold;
                font-size: 14px;
            }}
            #AccentButton:hover {{
                background: {ACCENT_HOVER};
            }}
            #AccentButton:pressed {{
                background: {ACCENT_PRESSED};
            }}
            #GhostButton {{
                background: {SURFACE_2};
                color: {TEXT};
                border: 1px solid {SURFACE_3};
                border-radius: 12px;
                padding: 10px 20px;
                font-size: 13px;
            }}
            #GhostButton:hover {{
                background: {SURFACE_3};
                border-color: {DIM};
            }}
            
            /* Выпадающие списки */
            #SortCombo, #QueueCombo {{
                background: {SURFACE};
                color: {TEXT};
                border: 1px solid {SURFACE_3};
                border-radius: 10px;
                padding: 6px 12px;
            }}
            #SortCombo::drop-down, #QueueCombo::drop-down {{
                border: none;
                width: 20px;
            }}
            #SortCombo QAbstractItemView, #QueueCombo QAbstractItemView {{
                background: {SURFACE_2};
                color: {TEXT};
                border: 1px solid {SURFACE_3};
                border-radius: 8px;
                selection-background-color: {SURFACE_3};
                selection-color: {ACCENT};
            }}
            
            /* ---------------- СПИСКИ И РОУ ТРЕКОВ ---------------- */
            #TrackList {{
                background: transparent;
            }}
            #TrackRow {{
                background: transparent;
                border-radius: 10px;
            }}
            #TrackRow[hovered="true"] {{
                background: {SURFACE};
            }}
            #TrackRow[playing="true"] {{
                background: #1d1d14;
            }}
            #RowNumber {{
                color: {DIM};
                font-size: 12px;
            }}
            #RowPlayOverlay {{
                background: rgba(0, 0, 0, 0.5);
                color: {ACCENT};
                font-size: 16px;
                border-radius: 6px;
            }}
            #RowDuration {{
                color: {MUTED};
                font-size: 13px;
            }}
            
            /* ---------------- КАРТОЧКА АЛЬБОМА ---------------- */
            #AlbumCard {{
                background: {SURFACE};
                border-radius: 16px;
            }}
            #AlbumCard:hover {{
                background: {SURFACE_2};
            }}
            #CardPlayBadge {{
                background: {ACCENT};
                color: #0f0f12;
                font-size: 18px;
                font-weight: bold;
                border-radius: 22px;
            }}
            
            /* ---------------- БЛОК СЕЙЧАС ИГРАЕТ ---------------- */
            #NowPlayingHero {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #1a1a22, stop:1 #111116);
                border: 1px solid {SURFACE_2};
                border-radius: 24px;
            }}
            #HeroEyebrow {{
                color: {ACCENT};
                letter-spacing: 1px;
            }}
            
            /* ---------------- НИЖНИЙ ПЛЕЕРБАР ---------------- */
            #PlayerBar {{
                background-color: {SURFACE};
                border-top: 1px solid {SURFACE_2};
            }}
            #PlayPauseButton {{
                background: #ffffff;
                color: #0f0f12;
                border: none;
                border-radius: 26px;
                font-size: 18px;
                font-weight: bold;
                padding-left: 2px;
            }}
            #PlayPauseButton:hover {{
                transform: scale(1.05);
                background: #f0f0f5;
            }}
            #NavButton {{
                background: transparent;
                border: none;
                color: {MUTED};
                font-size: 18px;
            }}
            #NavButton:hover {{
                color: {TEXT};
            }}
            #NavButton:checked {{
                color: {ACCENT};
            }}
            
            /* Ползунки времени и громкости в стиле Яндекс Музыки */
            QSlider::groove:horizontal {{
                background: #2b2b33;
                height: 4px;
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {ACCENT};
                height: 4px;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {ACCENT};
                width: 0px; /* Скрываем ручку по умолчанию */
                height: 0px;
                margin-top: -4px;
                border-radius: 4px;
            }}
            QSlider:hover::handle:horizontal {{
                width: 10px; /* Показываем при наведении */
                height: 10px;
                margin-top: -3px;
            }}
            #TimeLabel, #VolumeLabel {{
                color: {MUTED};
                font-size: 12px;
            }}
            
            /* Скроллбары */
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
            }}
            QScrollBar::handle:vertical {{
                background: {SURFACE_3};
                border-radius: 3px;
                min-height: 40px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {DIM};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            """
        )

# ======================================================================
# ТОЧКА ВХОДА
# ======================================================================
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Local Music")
    try:
        window = MainWindow()
    except Exception as exc:
        QMessageBox.critical(
            None,
            "Ошибка запуска Local Music",
            str(exc),
        )
        return 1
    window.showMaximized()
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
