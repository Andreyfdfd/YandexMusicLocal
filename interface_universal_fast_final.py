# -*- coding: utf-8 -*-
"""
Local Music — локальный аудиоплеер для Windows.

Цель этой версии:
    • визуальная стилистика, близкая к современному десктопному Яндекс Музыке:
      тёмный фон, узкий сайдбар, крупные карточки, мягкие скругления,
      жёлтый акцент и нижний плеер;
    • первый запуск — выбор папки с музыкой;
    • выбранная папка и база запоминаются в %LOCALAPPDATA%\\LocalMusic;
    • больше нет привязки к D:\\Music или D:\\YandexMusicLocal;
    • сканирование MP3/M4A выполняется в фоне;
    • длительность, размер, время изменения и базовые теги сохраняются в SQLite;
    • поиск и сортировка по основным полям;
    • обложки из тегов и локальных cover.jpg/folder.jpg/front.jpg/album.jpg;
    • воспроизведение через python-vlc;
    • автоматически создаётся ярлык на рабочем столе с локальной иконкой.

Зависимости:
    pip install PyQt6 python-vlc mutagen pillow

Для звука на Windows нужен установленный VLC Media Player.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# VLC: ищем стандартные папки установки до import vlc.
# ---------------------------------------------------------------------------
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
except Exception as exc:  # pragma: no cover - зависит от окружения Windows
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
    QIcon,
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
    QFileDialog,
    QProgressBar,
    QToolButton,
)

# ===========================================================================
# ПУТИ И НАСТРОЙКИ
# ===========================================================================

APP_NAME = "Local Music"
APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LocalMusic"
APP_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = APP_DIR / "settings.json"
DB_PATH = APP_DIR / "music_collection.db"
ICON_PATH = APP_DIR / "local_music.ico"

# Запасные названия обложек рядом с аудиофайлом.
COVER_FILENAMES = (
    "cover.jpg", "cover.jpeg", "cover.png",
    "folder.jpg", "folder.jpeg", "folder.png",
    "front.jpg", "front.jpeg", "front.png",
    "album.jpg", "album.jpeg", "album.png",
)
AUDIO_EXTENSIONS = {".mp3", ".m4a"}

ACCENT = "#ffd21c"
ACCENT_HOVER = "#ffe064"
ACCENT_PRESSED = "#e6b800"
BG = "#0c0c0e"
BG_2 = "#111114"
SURFACE = "#17171a"
SURFACE_2 = "#1e1e22"
SURFACE_3 = "#28282d"
SURFACE_4 = "#303035"
TEXT = "#ffffff"
MUTED = "#a2a2a8"
DIM = "#707079"
GREEN = "#b9f34a"
ROW_HEIGHT = 64
CARD_WIDTH = 205
CARD_HEIGHT = 288
CARD_COVER = 186


# ===========================================================================
# КОНФИГ / ЯРЛЫК
# ===========================================================================

DEFAULT_SETTINGS = {
    "music_folder": "",
    "volume": 70,
    "last_track": None,
}


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        result = dict(DEFAULT_SETTINGS)
        result.update(data if isinstance(data, dict) else {})
        return result
    except (OSError, ValueError, TypeError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_PATH)


def _is_windows() -> bool:
    return os.name == "nt"


def _desktop_path() -> Path:
    try:
        desktop = Path.home() / "Desktop"
        if desktop.exists():
            return desktop
        shell = os.environ.get("USERPROFILE")
        if shell:
            return Path(shell) / "Desktop"
    except Exception:
        pass
    return Path.home() / "Desktop"


def create_icon_file() -> Path:
    """Создаёт простую локальную иконку без внешних файлов и брендов."""
    if ICON_PATH.exists() and ICON_PATH.stat().st_size > 100:
        return ICON_PATH

    # Для .ico используем Pillow, если установлен. Если нет — PowerShell
    # всё равно сможет создать ярлык, просто возьмёт иконку python.exe.
    try:
        from PIL import Image, ImageDraw

        sizes = [16, 24, 32, 48, 64, 128, 256]
        images = []
        for size in sizes:
            img = Image.new("RGBA", (size, size), (10, 10, 12, 255))
            draw = ImageDraw.Draw(img)
            pad = max(1, size // 10)
            draw.rounded_rectangle(
                [pad, pad, size - pad - 1, size - pad - 1],
                radius=max(2, size // 5),
                fill=(255, 210, 28, 255),
            )
            # Простая нота.
            x = size * 0.48
            y = size * 0.30
            stem_w = max(2, size // 9)
            draw.rectangle([x, y, x + stem_w, size * 0.70], fill=(20, 20, 22, 255))
            draw.ellipse(
                [x - size * 0.12, size * 0.60, x + size * 0.20, size * 0.79],
                fill=(20, 20, 22, 255),
            )
            draw.ellipse(
                [x + size * 0.30, size * 0.48, x + size * 0.62, size * 0.67],
                fill=(20, 20, 22, 255),
            )
            draw.line(
                [x + stem_w, y, x + size * 0.58, size * 0.48],
                fill=(20, 20, 22, 255),
                width=max(2, size // 11),
            )
            images.append(img)
        images[0].save(ICON_PATH, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[1:])
        return ICON_PATH
    except Exception:
        return Path("")


def create_desktop_shortcut() -> bool:
    """Создаёт .lnk на рабочем столе. Работает без pywin32 через PowerShell COM."""
    if not _is_windows():
        return False
    try:
        desktop = _desktop_path()
        desktop.mkdir(parents=True, exist_ok=True)
        shortcut = desktop / "Local Music.lnk"
        icon = create_icon_file()

        # При запуске как .py указываем python.exe/pythonw.exe и скрипт.
        # При запуске собранного EXE указываем сам EXE.
        executable = Path(sys.executable).resolve()
        script_path = Path(__file__).resolve()
        if getattr(sys, "frozen", False):
            target = str(executable)
            arguments = ""
            working_dir = str(executable.parent)
            icon_location = str(icon) if icon else target
        else:
            python_exe = executable
            if python_exe.name.lower() == "python.exe":
                pythonw = python_exe.with_name("pythonw.exe")
                if pythonw.exists():
                    python_exe = pythonw
            target = str(python_exe)
            arguments = f'"{script_path}"'
            working_dir = str(script_path.parent)
            icon_location = str(icon) if icon else str(python_exe)

        ps = f'''
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut({json.dumps(str(shortcut))})
$sc.TargetPath = {json.dumps(target)}
$sc.Arguments = {json.dumps(arguments)}
$sc.WorkingDirectory = {json.dumps(working_dir)}
$sc.IconLocation = {json.dumps(icon_location)}
$sc.Description = {json.dumps("Локальный аудиоплеер Local Music")}
$sc.Save()
'''
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return shortcut.exists()
    except Exception:
        return False


# ===========================================================================
# DATABASE
# ===========================================================================

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist TEXT NOT NULL,
    album_name TEXT NOT NULL,
    year INTEGER DEFAULT NULL,
    cover_path TEXT DEFAULT NULL,
    UNIQUE(artist, album_name)
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id INTEGER NOT NULL,
    track_name TEXT NOT NULL,
    genre TEXT DEFAULT 'Разное',
    file_path TEXT NOT NULL UNIQUE,
    duration_ms INTEGER DEFAULT 0,
    file_size INTEGER DEFAULT 0,
    mtime REAL DEFAULT 0,
    FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tracks_name ON tracks(track_name);
CREATE INDEX IF NOT EXISTS idx_tracks_genre ON tracks(genre);
CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_id);
CREATE INDEX IF NOT EXISTS idx_tracks_duration ON tracks(duration_ms);
CREATE INDEX IF NOT EXISTS idx_albums_artist ON albums(artist);
"""


def db_connect() -> sqlite3.Connection:
    """Быстрое подключение: никаких CREATE/ALTER/PRAGMA WAL на каждом запросе."""
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function(
        "CASEFOLD", 1, lambda value: value.casefold() if isinstance(value, str) else value
    )
    return conn


def ensure_database_schema() -> None:
    """Создаёт/мигрирует схему один раз при запуске приложения."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_SQL)
        required_tracks = {
            "duration_ms": "INTEGER DEFAULT 0",
            "file_size": "INTEGER DEFAULT 0",
            "mtime": "REAL DEFAULT 0",
        }
        required_albums = {"cover_path": "TEXT DEFAULT NULL"}
        track_cols = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
        album_cols = {row[1] for row in conn.execute("PRAGMA table_info(albums)")}
        for name, definition in required_tracks.items():
            if name not in track_cols:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {name} {definition}")
        for name, definition in required_albums.items():
            if name not in album_cols:
                conn.execute(f"ALTER TABLE albums ADD COLUMN {name} {definition}")
        conn.commit()
    finally:
        conn.close()


def clear_database(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM tracks")
    conn.execute("DELETE FROM albums")
    conn.commit()


def safe_text(value, fallback="—") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def format_ms(ms: int | None) -> str:
    ms = max(0, int(ms or 0))
    total = ms // 1000
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def find_folder_cover(folder: Path) -> Optional[str]:
    try:
        for filename in COVER_FILENAMES:
            candidate = folder / filename
            if candidate.is_file():
                return str(candidate)
    except OSError:
        return None
    return None


def parse_media(path: str) -> dict:
    """Читает теги + технические параметры конкретного MP3/M4A."""
    p = Path(path)
    result = {
        "artist": "Разные исполнители",
        "album": p.parent.name if p.parent.name else "Синглы",
        "title": p.stem,
        "genre": "Разное",
        "year": None,
        "duration_ms": 0,
        "cover_bytes": None,
        "cover_path": find_folder_cover(p.parent),
        "file_size": 0,
        "mtime": 0.0,
    }
    try:
        st = p.stat()
        result["file_size"] = int(st.st_size)
        result["mtime"] = float(st.st_mtime)
    except OSError:
        pass

    try:
        if p.suffix.lower() == ".mp3":
            audio = MP3(path)
            result["duration_ms"] = max(0, int(float(getattr(audio.info, "length", 0) or 0) * 1000))
            tags = audio.tags
            if tags:
                if tags.get("TPE1"):
                    result["artist"] = str(tags["TPE1"].text[0]).strip()
                if tags.get("TALB"):
                    result["album"] = str(tags["TALB"].text[0]).strip()
                if tags.get("TIT2"):
                    result["title"] = str(tags["TIT2"].text[0]).strip()
                if tags.get("TCON"):
                    result["genre"] = str(tags["TCON"].text[0]).strip()
                if tags.get("TDRC"):
                    raw_year = str(tags["TDRC"].text[0]).strip()
                    if raw_year[:4].isdigit():
                        result["year"] = int(raw_year[:4])
                apic = tags.getall("APIC")
                if apic:
                    result["cover_bytes"] = apic[0].data

        elif p.suffix.lower() == ".m4a":
            audio = MP4(path)
            result["duration_ms"] = max(0, int(float(getattr(audio.info, "length", 0) or 0) * 1000))
            tags = audio.tags
            if tags:
                if tags.get("\xa9ART"):
                    result["artist"] = str(tags["\xa9ART"][0]).strip()
                if tags.get("\xa9alb"):
                    result["album"] = str(tags["\xa9alb"][0]).strip()
                if tags.get("\xa9nam"):
                    result["title"] = str(tags["\xa9nam"][0]).strip()
                if tags.get("\xa9gen"):
                    result["genre"] = str(tags["\xa9gen"][0]).strip()
                if tags.get("\xa9day"):
                    raw_year = str(tags["\xa9day"][0]).strip()
                    if raw_year[:4].isdigit():
                        result["year"] = int(raw_year[:4])
                covr = tags.get("covr")
                if covr:
                    result["cover_bytes"] = bytes(covr[0])
    except Exception:
        pass

    result["artist"] = safe_text(result["artist"], "Разные исполнители")
    result["album"] = safe_text(result["album"], "Синглы")
    result["title"] = safe_text(result["title"], p.stem)
    result["genre"] = safe_text(result["genre"], "Разное")
    return result


# ===========================================================================
# ФОНОВЫЙ СКАНЕР
# ===========================================================================

class ScanSignals(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int, int)
    error = pyqtSignal(str)


class ScanTask(QRunnable):
    def __init__(self, folder: Path, replace=True):
        super().__init__()
        self.folder = folder
        self.replace = replace
        self.signals = ScanSignals()
        self.setAutoDelete(True)

    def run(self):
        conn = None
        try:
            ensure_database_schema()
            files = [
                p for p in self.folder.rglob("*")
                if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
            ]
            files.sort(key=lambda x: str(x).casefold())

            conn = db_connect()
            if self.replace:
                clear_database(conn)

            album_ids: dict[tuple[str, str], tuple[int, Optional[str], Optional[int]]] = {}
            success = 0

            conn.execute("BEGIN")
            for index, p in enumerate(files, start=1):
                meta = parse_media(str(p))
                key = (meta["artist"], meta["album"])
                if key not in album_ids:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO albums(artist, album_name, year, cover_path) VALUES (?, ?, ?, ?)",
                        (meta["artist"], meta["album"], meta["year"], meta["cover_path"]),
                    )
                    _ = cur
                    row = conn.execute(
                        "SELECT id, cover_path, year FROM albums WHERE artist=? AND album_name=?",
                        key,
                    ).fetchone()
                    album_id = int(row[0])
                    cover_path = row[1] or meta["cover_path"]
                    year = row[2] or meta["year"]
                    if (row[1] is None and meta["cover_path"]) or (row[2] is None and meta["year"]):
                        conn.execute(
                            "UPDATE albums SET cover_path=?, year=? WHERE id=?",
                            (cover_path, year, album_id),
                        )
                    album_ids[key] = (album_id, cover_path, year)
                else:
                    album_id = album_ids[key][0]

                conn.execute(
                    """
                    INSERT OR REPLACE INTO tracks
                    (album_id, track_name, genre, file_path, duration_ms, file_size, mtime)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        album_id,
                        meta["title"],
                        meta["genre"],
                        str(p),
                        meta["duration_ms"],
                        meta["file_size"],
                        meta["mtime"],
                    ),
                )
                success += 1
                if index == 1 or index % 25 == 0 or index == len(files):
                    self.signals.progress.emit(index, len(files), meta["title"])

            conn.commit()
            self.signals.finished.emit(success, len(files) - success)
        except Exception as exc:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            self.signals.error.emit(str(exc))
        finally:
            if conn:
                conn.close()


# ===========================================================================
# МЕТАДАННЫЕ И ОБЛОЖКИ
# ===========================================================================

class MediaCache:
    def __init__(self):
        self.images: dict[tuple[str, int], QImage] = {}
        self.folder_cover_bytes: dict[str, Optional[bytes]] = {}
        self.max_images = 1600

    def load_cover(self, file_path: str, size: int) -> Optional[QImage]:
        key = (file_path, int(size))
        if key in self.images:
            return self.images[key]
        data = None
        try:
            ext = Path(file_path).suffix.lower()
            if ext == ".mp3":
                audio = MP3(file_path)
                if audio.tags and audio.tags.getall("APIC"):
                    data = audio.tags.getall("APIC")[0].data
            elif ext == ".m4a":
                audio = MP4(file_path)
                if audio.tags and audio.tags.get("covr"):
                    data = bytes(audio.tags["covr"][0])
        except Exception:
            data = None

        if data is None:
            try:
                folder = Path(file_path).parent
                folder_key = str(folder)
                if folder_key in self.folder_cover_bytes:
                    data = self.folder_cover_bytes[folder_key]
                else:
                    data = None
                    for filename in COVER_FILENAMES:
                        c = folder / filename
                        if c.is_file():
                            data = c.read_bytes()
                            break
                    self.folder_cover_bytes[folder_key] = data
            except Exception:
                data = None

        image = None
        if data:
            raw = QImage.fromData(data)
            if not raw.isNull():
                scaled = raw.scaled(
                    size,
                    size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = max(0, (scaled.width() - size) // 2)
                y = max(0, (scaled.height() - size) // 2)
                image = scaled.copy(x, y, size, size)
        self.images[key] = image
        while len(self.images) > self.max_images:
            self.images.pop(next(iter(self.images)))
        return image


MEDIA_CACHE = MediaCache()


def make_placeholder(size: int, radius: int = 12) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor("#2b2b31"))
    grad.setColorAt(1, QColor("#16161a"))
    painter.fillPath(path, QBrush(grad))
    painter.setPen(QColor(255, 210, 28, 120))
    painter.setFont(QFont("Segoe UI Symbol", max(18, int(size * 0.28))))
    painter.drawText(QRectF(0, 0, size, size), int(Qt.AlignmentFlag.AlignCenter), "♪")
    painter.end()
    return pm


def rounded_pixmap(image: QImage | None, size: int, radius: int = 12) -> QPixmap:
    if image is None or image.isNull():
        return make_placeholder(size, radius)
    pm = QPixmap.fromImage(image)
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pm)
    painter.end()
    return out


class ImageSignals(QObject):
    ready = pyqtSignal(object)


class ImageTask(QRunnable):
    def __init__(self, path: str, size: int, token: int):
        super().__init__()
        self.path = path
        self.size = size
        self.token = token
        self.signals = ImageSignals()
        self.setAutoDelete(True)

    def run(self):
        image = MEDIA_CACHE.load_cover(self.path, self.size)
        try:
            self.signals.ready.emit((self.token, image))
        except RuntimeError:
            pass


class AsyncCover(QLabel):
    def __init__(self, size: int, radius: int = 12, parent=None):
        super().__init__(parent)
        self.size_px = size
        self.radius = radius
        self.token = 0
        self.pool = QThreadPool.globalInstance()
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPixmap(make_placeholder(size, radius))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def load(self, path: Optional[str]):
        self.token += 1
        self.setPixmap(make_placeholder(self.size_px, self.radius))
        if not path:
            return
        task = ImageTask(path, self.size_px, self.token)
        task.signals.ready.connect(self._on_ready)
        self.pool.start(task)

    def _on_ready(self, payload):
        try:
            token, image = payload
            if token != self.token or image is None or image.isNull():
                return
            self.setPixmap(rounded_pixmap(image, self.size_px, self.radius))
        except RuntimeError:
            pass


# ===========================================================================
# UI: ПОКАЗ ТЕКСТА
# ===========================================================================

class ElidedLabel(QLabel):
    def __init__(self, text="", color=TEXT, parent=None):
        super().__init__(parent)
        self._full = text or ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(f"color:{color};")
        self._refresh()

    def setText(self, text):
        self._full = text or ""
        self._refresh()

    def set_color(self, color):
        self.setStyleSheet(f"color:{color};")

    def setFont(self, font):
        super().setFont(font)
        self._refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self):
        fm = QFontMetrics(self.font())
        visible = fm.elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, self.width()))
        super().setText(visible)
        self.setToolTip(self._full if visible != self._full else "")


# ===========================================================================
# UI: СТРОКА ТРЕКА
# ===========================================================================

class TrackRow(QWidget):
    clicked = pyqtSignal(int)

    def __init__(self, index: int, track: dict, parent=None, compact=False):
        super().__init__(parent)
        self.index = index
        self.track = track
        self.compact = compact
        self.playing = False
        self.setObjectName("TrackRow")
        self.setProperty("playing", False)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(ROW_HEIGHT if not compact else 56)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 4, 16, 4)
        root.setSpacing(12)

        num = QLabel(str(index + 1))
        num.setObjectName("RowNum")
        num.setFixedWidth(30)
        num.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(num)
        self.num = num

        self.cover = AsyncCover(44 if not compact else 40, 8)
        self._cover_requested = False
        self._cover_path = track.get("cover_path") or track.get("file_path")
        root.addWidget(self.cover)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        title = ElidedLabel(safe_text(track.get("track_name"), "Неизвестный трек"))
        f = QFont("Segoe UI", 10)
        f.setWeight(QFont.Weight.DemiBold)
        title.setFont(f)
        self.title = title
        text.addWidget(title)

        artist = ElidedLabel(safe_text(track.get("artist"), "Неизвестный исполнитель"), MUTED)
        artist.setFont(QFont("Segoe UI", 9))
        self.artist = artist
        text.addWidget(artist)
        root.addLayout(text, 1)

        album = ElidedLabel(safe_text(track.get("album"), "—"), DIM)
        album.setFont(QFont("Segoe UI", 9))
        album.setFixedWidth(220 if not compact else 180)
        self.album = album
        root.addWidget(album)

        genre = ElidedLabel(safe_text(track.get("genre"), "Разное"), DIM)
        genre.setFont(QFont("Segoe UI", 9))
        genre.setFixedWidth(130)
        self.genre = genre
        root.addWidget(genre)

        dur = QLabel(format_ms(track.get("duration_ms", 0)))
        dur.setObjectName("RowDuration")
        dur.setFixedWidth(58)
        dur.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(dur)
        self.duration = dur

    def ensure_cover(self):
        if self._cover_requested:
            return
        self._cover_requested = True
        if self._cover_path:
            self.cover.load(self._cover_path)

    def set_playing(self, state: bool):
        self.playing = state
        self.setProperty("playing", state)
        self.title.set_color(ACCENT if state else TEXT)
        self.num.setText("▶" if state else str(self.index + 1))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)


class TrackList(QWidget):
    track_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[TrackRow] = []
        self.tracks: list[dict] = []
        self.playing_path = None
        self.loaded_count = 0
        self.batch_size = 120
        self.container = QVBoxLayout(self)
        self.container.setContentsMargins(0, 0, 0, 0)
        self.container.setSpacing(2)
        self.container.addStretch()

    def set_tracks(self, tracks: list[dict]):
        self.tracks = list(tracks or [])
        self._clear()
        self.loaded_count = 0
        self._append_batch()

    def _append_batch(self):
        if self.loaded_count >= len(self.tracks):
            return
        end = min(len(self.tracks), self.loaded_count + self.batch_size)
        for i in range(self.loaded_count, end):
            track = self.tracks[i]
            row = TrackRow(i, track, compact=False)
            row.clicked.connect(self.track_clicked.emit)
            row.set_playing(bool(self.playing_path and track.get("file_path") == self.playing_path))
            self.container.insertWidget(self.container.count() - 1, row)
            self.rows.append(row)
        self.loaded_count = end

    def maybe_load_more(self, value: int, maximum: int):
        if self.loaded_count >= len(self.tracks):
            return
        if maximum <= 0 or maximum - value < 900:
            self._append_batch()

    def ensure_covers(self, scroll_value: int, viewport_height: int):
        if not self.rows:
            return
        first = max(0, int(scroll_value // max(1, ROW_HEIGHT)) - 6)
        visible = int(viewport_height // max(1, ROW_HEIGHT)) + 12
        last = min(len(self.rows), first + visible)
        for index in range(first, last):
            self.rows[index].ensure_cover()

    def _clear(self):
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows.clear()

    def set_playing_path(self, path: Optional[str]):
        self.playing_path = path
        for row in self.rows:
            row.set_playing(bool(path and row.track.get("file_path") == path))


# ===========================================================================
# UI: АЛЬБОМЫ
# ===========================================================================

class AlbumCard(QFrame):
    clicked = pyqtSignal(int)

    def __init__(self, album_id, artist, album, year, cover_path=None, parent=None):
        super().__init__(parent)
        self.album_id = album_id
        self.setObjectName("AlbumCard")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 12)
        root.setSpacing(8)

        self.cover = AsyncCover(CARD_COVER, 14)
        self.cover_path = cover_path
        self.cover_requested = False
        root.addWidget(self.cover, alignment=Qt.AlignmentFlag.AlignHCenter)

        title = ElidedLabel(safe_text(album, "Без названия"))
        f = QFont("Segoe UI", 10)
        f.setWeight(QFont.Weight.DemiBold)
        title.setFont(f)
        title.setFixedHeight(21)
        root.addWidget(title)

        sub = safe_text(artist, "Неизвестный исполнитель")
        if year:
            sub += f"  •  {year}"
        label = ElidedLabel(sub, MUTED)
        label.setFont(QFont("Segoe UI", 9))
        label.setFixedHeight(18)
        root.addWidget(label)

    def ensure_cover(self):
        if self.cover_requested:
            return
        self.cover_requested = True
        if self.cover_path:
            self.cover.load(self.cover_path)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.album_id)
        super().mousePressEvent(event)


# ===========================================================================
# DB-ЗАПРОСЫ
# ===========================================================================


def _track_rows_to_dict(rows):
    result = []
    for r in rows:
        tid, title, genre, path, duration, size, mtime, artist, album, year, cover_path = r
        result.append({
            "id": tid,
            "track_name": safe_text(title, Path(path).stem if path else "Неизвестный трек"),
            "genre": safe_text(genre, "Разное"),
            "file_path": path,
            "duration_ms": int(duration or 0),
            "file_size": int(size or 0),
            "mtime": float(mtime or 0),
            "artist": safe_text(artist, "Неизвестный исполнитель"),
            "album": safe_text(album, "Без названия"),
            "year": year,
            "cover_path": cover_path,
        })
    return result


def fetch_all_tracks() -> list[dict]:
    conn = db_connect()
    rows = conn.execute(
        """
        SELECT t.id,t.track_name,t.genre,t.file_path,t.duration_ms,t.file_size,t.mtime,
               a.artist,a.album_name,a.year,a.cover_path
        FROM tracks t JOIN albums a ON a.id=t.album_id
        """
    ).fetchall()
    conn.close()
    return _track_rows_to_dict(rows)


def search_library(query: str, genre: str = "Все жанры") -> list[dict]:
    conn = db_connect()
    pattern = f"%{query.strip()}%"
    where = []
    args = []
    if query.strip():
        where.append(
            "(CASEFOLD(t.track_name) LIKE CASEFOLD(?) OR CASEFOLD(a.artist) LIKE CASEFOLD(?) "
            "OR CASEFOLD(a.album_name) LIKE CASEFOLD(?) OR CASEFOLD(t.genre) LIKE CASEFOLD(?) "
            "OR CASEFOLD(t.file_path) LIKE CASEFOLD(?))"
        )
        args.extend([pattern] * 5)
    if genre and genre != "Все жанры":
        where.append("t.genre = ?")
        args.append(genre)
    sql = (
        "SELECT t.id,t.track_name,t.genre,t.file_path,t.duration_ms,t.file_size,t.mtime,"
        "a.artist,a.album_name,a.year,a.cover_path FROM tracks t JOIN albums a ON a.id=t.album_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return _track_rows_to_dict(rows)


def fetch_genres() -> list[str]:
    conn = db_connect()
    rows = conn.execute(
        "SELECT DISTINCT genre FROM tracks WHERE genre IS NOT NULL AND TRIM(genre) <> '' ORDER BY genre COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def fetch_albums(sort_mode: str = "Исполнитель"):
    order = {
        "Исполнитель": "artist COLLATE NOCASE, album_name COLLATE NOCASE",
        "Название": "album_name COLLATE NOCASE, artist COLLATE NOCASE",
        "Год": "year IS NULL, year, album_name COLLATE NOCASE",
    }.get(sort_mode, "artist COLLATE NOCASE, album_name COLLATE NOCASE")
    conn = db_connect()
    rows = conn.execute(
        f"SELECT id,artist,album_name,year,cover_path FROM albums ORDER BY {order}"
    ).fetchall()
    conn.close()
    return rows


def fetch_album_tracks(album_id: int) -> list[dict]:
    conn = db_connect()
    rows = conn.execute(
        """
        SELECT t.id,t.track_name,t.genre,t.file_path,t.duration_ms,t.file_size,t.mtime,
               a.artist,a.album_name,a.year,a.cover_path
        FROM tracks t JOIN albums a ON a.id=t.album_id
        WHERE a.id=? ORDER BY t.id
        """,
        (album_id,),
    ).fetchall()
    conn.close()
    return _track_rows_to_dict(rows)


def fetch_album_info(album_id: int):
    conn = db_connect()
    row = conn.execute(
        "SELECT id,artist,album_name,year,cover_path FROM albums WHERE id=?",
        (album_id,),
    ).fetchone()
    conn.close()
    return row


# ===========================================================================
# ОБЩИЕ ВИДЖЕТЫ
# ===========================================================================

class PillButton(QPushButton):
    pass


class Sidebar(QFrame):
    home_clicked = pyqtSignal()
    library_clicked = pyqtSignal()
    search_clicked = pyqtSignal()
    folder_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(86)
        root = QVBoxLayout(self)
        root.setContentsMargins(11, 18, 11, 18)
        root.setSpacing(7)

        logo = QLabel("✦")
        logo.setObjectName("SideLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setToolTip("Local Music")
        root.addWidget(logo)
        root.addSpacing(18)

        self.btn_home = self._button("⌂", "Главная")
        self.btn_library = self._button("♫", "Коллекция")
        self.btn_search = self._button("⌕", "Поиск")
        root.addWidget(self.btn_home)
        root.addWidget(self.btn_library)
        root.addWidget(self.btn_search)

        root.addSpacing(10)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("SidebarSep")
        root.addWidget(sep)

        self.btn_folder = self._button("▣", "Папка музыки")
        root.addWidget(self.btn_folder)
        root.addStretch()

        status = QLabel("●")
        status.setObjectName("SidebarStatus")
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status.setToolTip("Локальный режим")
        root.addWidget(status)

        self.btn_home.clicked.connect(self.home_clicked.emit)
        self.btn_library.clicked.connect(self.library_clicked.emit)
        self.btn_search.clicked.connect(self.search_clicked.emit)
        self.btn_folder.clicked.connect(self.folder_clicked.emit)

    def _button(self, text, tooltip):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tooltip)
        b.setObjectName("SideButton")
        b.setCheckable(True)
        b.setAutoExclusive(True)
        b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        b.setFixedSize(58, 52)
        b.setFont(QFont("Segoe UI Symbol", 21))
        return b

    def set_active(self, button):
        for b in (self.btn_home, self.btn_library, self.btn_search):
            b.setChecked(b is button)


class TopSearch(QWidget):
    submitted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(28, 18, 28, 8)
        root.setSpacing(10)
        self.edit = QLineEdit()
        self.edit.setObjectName("TopSearch")
        self.edit.setPlaceholderText("Поиск по трекам, исполнителям, альбомам и жанрам")
        self.edit.setClearButtonEnabled(True)
        self.edit.setFixedHeight(46)
        self.edit.returnPressed.connect(lambda: self.submitted.emit(self.edit.text().strip()))
        root.addWidget(self.edit, 1)
        btn = QPushButton("Найти")
        btn.setObjectName("SearchButton")
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn.clicked.connect(lambda: self.submitted.emit(self.edit.text().strip()))
        root.addWidget(btn)


class SortBar(QWidget):
    changed = pyqtSignal(str, bool)

    def __init__(self, modes: list[str], parent=None):
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        label = QLabel("Сортировка")
        label.setObjectName("MutedText")
        root.addWidget(label)
        self.combo = QComboBox()
        self.combo.setObjectName("SortCombo")
        self.combo.addItems(modes)
        root.addWidget(self.combo)
        self.direction = QPushButton("↑")
        self.direction.setObjectName("RoundButton")
        self.direction.setCheckable(True)
        self.direction.setToolTip("Переключить направление")
        self.direction.setFixedSize(38, 38)
        root.addWidget(self.direction)
        self.combo.currentTextChanged.connect(lambda text: self.changed.emit(text, self.direction.isChecked()))
        self.direction.toggled.connect(lambda checked: self.changed.emit(self.combo.currentText(), checked))


# ===========================================================================
# СТРАНИЦА ГЛАВНАЯ
# ===========================================================================

class HomePage(QWidget):
    album_selected = pyqtSignal(int)
    sort_changed = pyqtSignal(str, bool)
    play_all = pyqtSignal()
    shuffle_all = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 10, 28, 22)
        root.setSpacing(16)

        head = QHBoxLayout()
        titlebox = QVBoxLayout()
        titlebox.setSpacing(1)
        title = QLabel("Моя коллекция")
        title.setObjectName("BigTitle")
        titlebox.addWidget(title)
        self.count = QLabel("Загрузка…")
        self.count.setObjectName("MutedText")
        titlebox.addWidget(self.count)
        head.addLayout(titlebox)
        head.addStretch()
        self.sort = SortBar(["Исполнитель", "Название", "Год"])
        self.sort.changed.connect(self.sort_changed.emit)
        head.addWidget(self.sort)
        shuffle = QPushButton("🔀")
        shuffle.setObjectName("RoundButton")
        shuffle.setToolTip("Перемешать всё")
        shuffle.clicked.connect(self.shuffle_all.emit)
        head.addWidget(shuffle)
        listen = QPushButton("▶  Слушать всё")
        listen.setObjectName("AccentButton")
        listen.clicked.connect(self.play_all.emit)
        head.addWidget(listen)
        root.addLayout(head)

        self.now_card = NowPlayingCard()
        self.now_card.setVisible(False)
        root.addWidget(self.now_card)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setContentsMargins(4, 4, 4, 10)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(20)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.scroll.setWidget(self.grid_widget)
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _v: self._update_visible_covers())
        root.addWidget(self.scroll, 1)
        self.cards = []
        self._pending_albums = []
        self._created_cards = 0
        self._current_columns = 0
        self._loaded_once = False
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(180)
        self._relayout_timer.timeout.connect(self._relayout_if_needed)

    def ensure_loaded(self):
        if not self._loaded_once:
            self.load_albums("Исполнитель", False)

    def load_albums(self, mode="Исполнитель", reverse=False):
        self._loaded_once = True
        rows = fetch_albums(mode)
        if reverse:
            rows.reverse()
        self.count.setText(f"{len(rows)} альбомов")

        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards.clear()
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

        viewport_width = max(700, self.scroll.viewport().width())
        self._current_columns = max(2, min(8, (viewport_width - 30) // (CARD_WIDTH + 18)))
        self._pending_albums = rows
        self._created_cards = 0
        self._append_card_batch()

    def _append_card_batch(self):
        if self._created_cards >= len(self._pending_albums):
            QTimer.singleShot(0, self._update_visible_covers)
            return
        end = min(len(self._pending_albums), self._created_cards + 32)
        columns = max(1, self._current_columns)
        for pos in range(self._created_cards, end):
            album_id, artist, album, year, cover_path = self._pending_albums[pos]
            card = AlbumCard(album_id, artist, album, year, cover_path)
            card.clicked.connect(self.album_selected.emit)
            self.grid.addWidget(card, pos // columns, pos % columns)
            self.cards.append(card)
        self._created_cards = end
        self._update_visible_covers()
        if self._created_cards < len(self._pending_albums):
            QTimer.singleShot(0, self._append_card_batch)

    def _update_visible_covers(self):
        if not self.cards:
            return
        value = self.scroll.verticalScrollBar().value()
        top = value - 500
        bottom = value + self.scroll.viewport().height() + 900
        for card in self.cards:
            if card.y() + card.height() >= top and card.y() <= bottom:
                card.ensure_cover()

    def set_now_playing(self, track):
        self.now_card.set_track(track)
        self.now_card.setVisible(track is not None)

    def _relayout_if_needed(self):
        if not self._loaded_once:
            return
        viewport_width = max(700, self.scroll.viewport().width())
        columns = max(2, min(8, (viewport_width - 30) // (CARD_WIDTH + 18)))
        current_columns = getattr(self, "_current_columns", None)
        if current_columns != columns:
            self.load_albums(self.sort.combo.currentText(), self.sort.direction.isChecked())

    def _update_visible_covers(self):
        if not self.cards:
            return
        bar = self.scroll.verticalScrollBar()
        top = bar.value() - 500
        bottom = bar.value() + self.scroll.viewport().height() + 900
        for card in self.cards:
            if card.y() + card.height() >= top and card.y() <= bottom:
                card.ensure_cover()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_timer.start()
        QTimer.singleShot(0, self._update_visible_covers)

    def showEvent(self, event):
        super().showEvent(event)
        self._relayout_timer.stop()
        self._update_visible_covers()


class NowPlayingCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NowPlayingCard")
        self.setMinimumHeight(244)
        root = QHBoxLayout(self)
        root.setContentsMargins(26, 22, 30, 22)
        root.setSpacing(26)
        self.cover = AsyncCover(196, 16)
        root.addWidget(self.cover, alignment=Qt.AlignmentFlag.AlignVCenter)
        info = QVBoxLayout()
        info.setSpacing(5)
        cap = QLabel("СЕЙЧАС ИГРАЕТ")
        cap.setObjectName("YellowCaption")
        info.addWidget(cap)
        self.title = QLabel("Ничего не играет")
        self.title.setObjectName("NowTitle")
        self.title.setWordWrap(True)
        info.addWidget(self.title)
        self.artist = QLabel("—")
        self.artist.setObjectName("NowArtist")
        info.addWidget(self.artist)
        self.album = QLabel("Альбом: —")
        self.album.setObjectName("MutedText")
        info.addWidget(self.album)
        self.meta = QLabel("Жанр: —   •   Год: —")
        self.meta.setObjectName("DimText")
        info.addWidget(self.meta)
        info.addStretch()
        root.addLayout(info, 1)

    def set_track(self, track):
        self.title.setText(safe_text(track.get("track_name"), "Неизвестный трек"))
        self.artist.setText(safe_text(track.get("artist"), "Неизвестный исполнитель"))
        self.album.setText(f"Альбом: {safe_text(track.get('album'), '—')}")
        year = track.get("year") or "—"
        self.meta.setText(f"Жанр: {safe_text(track.get('genre'), 'Разное')}   •   Год: {year}")
        self.cover.load(track.get("file_path"))


# ===========================================================================
# АЛЬБОМ
# ===========================================================================

class AlbumPage(QWidget):
    back = pyqtSignal()
    play_from = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.album_id = None
        self.tracks = []
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 10, 28, 22)
        root.setSpacing(16)
        top = QHBoxLayout()
        btn = QPushButton("←")
        btn.setObjectName("RoundButton")
        btn.setFixedSize(42, 42)
        btn.clicked.connect(self.back.emit)
        top.addWidget(btn)
        hint = QLabel("Коллекция  /  Альбом")
        hint.setObjectName("MutedText")
        top.addWidget(hint)
        top.addStretch()
        root.addLayout(top)

        self.hero = QFrame()
        self.hero.setObjectName("AlbumHero")
        hero_layout = QHBoxLayout(self.hero)
        hero_layout.setContentsMargins(24, 20, 26, 20)
        hero_layout.setSpacing(22)
        self.cover = AsyncCover(168, 16)
        hero_layout.addWidget(self.cover)
        info = QVBoxLayout()
        info.setSpacing(5)
        cap = QLabel("АЛЬБОМ")
        cap.setObjectName("YellowCaption")
        info.addWidget(cap)
        self.title = QLabel("Альбом")
        self.title.setObjectName("BigTitle")
        self.title.setWordWrap(True)
        info.addWidget(self.title)
        self.artist = QLabel("—")
        self.artist.setObjectName("NowArtist")
        info.addWidget(self.artist)
        self.details = QLabel("—")
        self.details.setObjectName("MutedText")
        info.addWidget(self.details)
        info.addStretch()
        play = QPushButton("▶  Слушать")
        play.setObjectName("AccentButton")
        play.clicked.connect(lambda: self.play_from.emit(0))
        info.addWidget(play, alignment=Qt.AlignmentFlag.AlignLeft)
        hero_layout.addLayout(info, 1)
        root.addWidget(self.hero)

        header = QHBoxLayout()
        label = QLabel("Треки")
        label.setObjectName("SectionTitle")
        header.addWidget(label)
        header.addStretch()
        root.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.track_list = TrackList()
        self.track_list.track_clicked.connect(self.play_from.emit)
        scroll.setWidget(self.track_list)
        scroll.verticalScrollBar().valueChanged.connect(
            lambda value, bar=scroll.verticalScrollBar(): (
                self.track_list.maybe_load_more(value, bar.maximum()),
                self.track_list.ensure_covers(value, scroll.viewport().height())
            )
        )
        root.addWidget(scroll, 1)

    def load(self, album_id):
        self.album_id = album_id
        info = fetch_album_info(album_id)
        self.tracks = fetch_album_tracks(album_id)
        if not info:
            return
        _, artist, album, year, cover = info
        self.title.setText(safe_text(album, "Без названия"))
        self.artist.setText(safe_text(artist, "Неизвестный исполнитель"))
        self.details.setText(f"{year or 'Год не указан'}  •  {len(self.tracks)} треков")
        self.cover.load(cover or (self.tracks[0]["file_path"] if self.tracks else None))
        self.track_list.set_tracks(self.tracks)
        # На старте загружаем только видимые обложки, а не весь альбом.
        QTimer.singleShot(0, lambda: self.track_list.ensure_covers(0, 620))


# ===========================================================================
# ПОИСК / БИБЛИОТЕКА
# ===========================================================================

class LibraryPage(QWidget):
    track_clicked = pyqtSignal(int)
    play_results = pyqtSignal()
    shuffle_results = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.results = []
        self.sort_mode = "Название"
        self.reverse = False
        self.genre = "Все жанры"
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 10, 28, 22)
        root.setSpacing(14)

        head = QHBoxLayout()
        titlebox = QVBoxLayout()
        title = QLabel("Все треки")
        title.setObjectName("BigTitle")
        titlebox.addWidget(title)
        self.count = QLabel("Загрузка…")
        self.count.setObjectName("MutedText")
        titlebox.addWidget(self.count)
        head.addLayout(titlebox)
        head.addStretch()

        self.genre_combo = QComboBox()
        self.genre_combo.setObjectName("SortCombo")
        self.genre_combo.addItem("Все жанры")
        try:
            self.genre_combo.addItems(fetch_genres())
        except Exception:
            pass
        self.genre_combo.currentTextChanged.connect(lambda _t: self.refresh())
        head.addWidget(self.genre_combo)
        self.sort = SortBar(["Название", "Исполнитель", "Альбом", "Жанр", "Год", "Длительность", "Размер файла", "Изменение файла"])
        self.sort.changed.connect(self._sort_changed)
        head.addWidget(self.sort)
        shuffle = QPushButton("🔀")
        shuffle.setObjectName("RoundButton")
        shuffle.clicked.connect(self.shuffle_results.emit)
        head.addWidget(shuffle)
        play = QPushButton("▶  Слушать")
        play.setObjectName("AccentButton")
        play.clicked.connect(self.play_results.emit)
        head.addWidget(play)
        root.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.track_list = TrackList()
        self.track_list.track_clicked.connect(self._clicked)
        scroll.setWidget(self.track_list)
        scroll.verticalScrollBar().valueChanged.connect(
            lambda value, bar=scroll.verticalScrollBar(): (
                self.track_list.maybe_load_more(value, bar.maximum()),
                self.track_list.ensure_covers(value, scroll.viewport().height())
            )
        )
        root.addWidget(scroll, 1)

    def _clicked(self, index):
        self.track_clicked.emit(index)

    def set_results(self, results):
        self.results = list(results or [])
        self._sort_results()
        self.count.setText(f"{len(self.results)} треков")
        self.track_list.set_tracks(self.results)
        QTimer.singleShot(0, lambda: self.track_list.ensure_covers(0, 600))

    def refresh(self, query=""):
        self.genre = self.genre_combo.currentText()
        self.set_results(search_library(query, self.genre))

    def _sort_changed(self, mode, reverse):
        self.sort_mode = mode
        self.reverse = reverse
        self._sort_results()
        self.track_list.set_tracks(self.results)
        QTimer.singleShot(0, lambda: self.track_list.ensure_covers(0, 620))

    def _sort_results(self):
        mode = self.sort_mode
        key_funcs = {
            "Название": lambda x: x["track_name"].casefold(),
            "Исполнитель": lambda x: (x["artist"].casefold(), x["track_name"].casefold()),
            "Альбом": lambda x: (x["album"].casefold(), x["track_name"].casefold()),
            "Жанр": lambda x: (x["genre"].casefold(), x["track_name"].casefold()),
            "Год": lambda x: (x["year"] is None, x["year"] or 0, x["track_name"].casefold()),
            "Длительность": lambda x: x.get("duration_ms", 0),
            "Размер файла": lambda x: x.get("file_size", 0),
            "Изменение файла": lambda x: x.get("mtime", 0),
        }
        self.results.sort(key=key_funcs.get(mode, key_funcs["Название"]), reverse=self.reverse)

    def set_playing(self, path):
        self.track_list.set_playing_path(path)


# ===========================================================================
# НИЖНИЙ ПЛЕЕР
# ===========================================================================

class PlayerBar(QFrame):
    play_pause = pyqtSignal()
    previous = pyqtSignal()
    next = pyqtSignal()
    shuffle = pyqtSignal(bool)
    volume = pyqtSignal(int)
    seek = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.setFixedHeight(110)
        self.user_seeking = False
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 10, 18, 10)
        root.setSpacing(22)

        left = QHBoxLayout()
        left.setSpacing(12)
        self.cover = AsyncCover(64, 10)
        left.addWidget(self.cover)
        info = QVBoxLayout()
        info.setSpacing(2)
        self.title = ElidedLabel("Ничего не играет")
        f = QFont("Segoe UI", 10)
        f.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(f)
        info.addWidget(self.title)
        self.artist = ElidedLabel("—", MUTED)
        self.artist.setFont(QFont("Segoe UI", 9))
        info.addWidget(self.artist)
        self.album = ElidedLabel("—", DIM)
        self.album.setFont(QFont("Segoe UI", 8))
        info.addWidget(self.album)
        left.addLayout(info)
        root.addLayout(left, 0)

        center = QVBoxLayout()
        center.setSpacing(5)
        controls = QHBoxLayout()
        controls.addStretch()
        self.shuffle_btn = QPushButton("🔀")
        self.shuffle_btn.setObjectName("MiniButton")
        self.shuffle_btn.setCheckable(True)
        self.shuffle_btn.clicked.connect(self.shuffle.emit)
        controls.addWidget(self.shuffle_btn)
        self.prev_btn = QPushButton("⏮")
        self.prev_btn.setObjectName("MiniButton")
        self.prev_btn.clicked.connect(self.previous.emit)
        controls.addWidget(self.prev_btn)
        self.play_btn = QPushButton("▶")
        self.play_btn.setObjectName("PlayButton")
        self.play_btn.setFixedSize(52, 52)
        self.play_btn.clicked.connect(self.play_pause.emit)
        controls.addWidget(self.play_btn)
        self.next_btn = QPushButton("⏭")
        self.next_btn.setObjectName("MiniButton")
        self.next_btn.clicked.connect(self.next.emit)
        controls.addWidget(self.next_btn)
        controls.addStretch()
        center.addLayout(controls)

        progress = QHBoxLayout()
        self.time = QLabel("00:00")
        self.time.setObjectName("TinyText")
        progress.addWidget(self.time)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName("Progress")
        self.slider.setRange(0, 1000)
        self.slider.sliderPressed.connect(lambda: setattr(self, "user_seeking", True))
        self.slider.sliderReleased.connect(self._seek)
        progress.addWidget(self.slider, 1)
        self.duration = QLabel("00:00")
        self.duration.setObjectName("TinyText")
        progress.addWidget(self.duration)
        center.addLayout(progress)
        root.addLayout(center, 1)

        right = QHBoxLayout()
        right.setSpacing(7)
        vol = QLabel("VOL")
        vol.setObjectName("TinyText")
        right.addWidget(vol)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(int(load_settings().get("volume", 70)))
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.valueChanged.connect(self._volume)
        right.addWidget(self.volume_slider)
        root.addLayout(right, 0)

    def _seek(self):
        self.user_seeking = False
        self.seek.emit(self.slider.value())

    def _volume(self, value):
        self.volume.emit(value)

    def set_track(self, track):
        self.title.setText(safe_text(track.get("track_name"), "Неизвестный трек"))
        self.artist.setText(safe_text(track.get("artist"), "Неизвестный исполнитель"))
        self.album.setText(safe_text(track.get("album"), "—"))
        self.cover.load(track.get("file_path"))

    def set_playing(self, playing):
        self.play_btn.setText("⏸" if playing else "▶")

    def set_progress(self, pos, total):
        if not self.user_seeking:
            value = int(pos * 1000 / total) if total > 0 else 0
            self.slider.blockSignals(True)
            self.slider.setValue(max(0, min(1000, value)))
            self.slider.blockSignals(False)
        self.time.setText(format_ms(pos))
        self.duration.setText(format_ms(total))


# ===========================================================================
# ГЛАВНОЕ ОКНО
# ===========================================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1180, 740)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        try:
            icon = create_icon_file()
            if icon and icon.exists():
                self.setWindowIcon(QIcon(str(icon)))
        except Exception:
            pass

        self.settings = load_settings()
        ensure_database_schema()
        self.music_folder = Path(self.settings.get("music_folder", "")) if self.settings.get("music_folder") else None
        self.queue: list[dict] = []
        self.queue_index = -1
        self.history = []
        self.history_pos = -1
        self.shuffle_enabled = False
        self.current_path = None
        self.track_duration = 0
        self.scan_in_progress = False
        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.setInterval(350)
        self._settings_save_timer.timeout.connect(lambda: save_settings(self.settings))

        if vlc is None:
            raise RuntimeError(
                "Не удалось импортировать python-vlc.\n\n"
                f"Ошибка: {VLC_IMPORT_ERROR}\n\n"
                "Установите VLC Media Player и пакет python-vlc."
            )
        self.vlc_instance = vlc.Instance()
        self.player = self.vlc_instance.media_player_new()

        self._build_ui()
        self._apply_styles()
        self._setup_shortcuts()
        self.player.audio_set_volume(int(self.settings.get("volume", 70)))

        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        QTimer.singleShot(0, self.showMaximized)
        QTimer.singleShot(120, self.home.ensure_loaded)
        QTimer.singleShot(2500, self._first_run_check)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.home_clicked.connect(self.show_home)
        self.sidebar.library_clicked.connect(self.show_library)
        self.sidebar.search_clicked.connect(self.show_search)
        self.sidebar.folder_clicked.connect(self.choose_music_folder)
        root.addWidget(self.sidebar)

        content = QWidget()
        content.setObjectName("Content")
        content_root = QVBoxLayout(content)
        content_root.setContentsMargins(0, 0, 0, 0)
        content_root.setSpacing(0)
        root.addWidget(content, 1)

        self.top_search = TopSearch()
        self.top_search.submitted.connect(self.search_now)
        content_root.addWidget(self.top_search)

        self.stack = QStackedWidget()
        self.home = HomePage()
        self.home.album_selected.connect(self.show_album)
        self.home.sort_changed.connect(self.home_sorted)
        self.home.play_all.connect(self.play_all)
        self.home.shuffle_all.connect(self.shuffle_all)

        self.album = AlbumPage()
        self.album.back.connect(self.show_home)
        self.album.play_from.connect(self.play_album_index)

        self.library = LibraryPage()
        self.library.track_clicked.connect(self.play_library_index)
        self.library.play_results.connect(self.play_library_all)
        self.library.shuffle_results.connect(self.shuffle_library)

        self.stack.addWidget(self.home)
        self.stack.addWidget(self.album)
        self.stack.addWidget(self.library)
        content_root.addWidget(self.stack, 1)

        self.player_bar = PlayerBar()
        self.player_bar.play_pause.connect(self.toggle_play_pause)
        self.player_bar.previous.connect(self.play_previous)
        self.player_bar.next.connect(self.play_next)
        self.player_bar.shuffle.connect(self.toggle_shuffle)
        self.player_bar.volume.connect(self.change_volume)
        self.player_bar.seek.connect(self.seek_to)
        content_root.addWidget(self.player_bar)

        self.sidebar.set_active(self.sidebar.btn_home)

    def _setup_shortcuts(self):
        # QShortcut импортировать не нужно: обработаем через native keyPressEvent.
        pass

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_K:
            self.top_search.edit.setFocus()
            self.top_search.edit.selectAll()
            return
        if event.key() == Qt.Key.Key_Escape and self.top_search.edit.hasFocus():
            self.top_search.edit.clear()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------ navigation
    def show_home(self):
        self.stack.setCurrentWidget(self.home)
        self.sidebar.set_active(self.sidebar.btn_home)
        self.home.ensure_loaded()
        self.home.set_now_playing(self.queue[self.queue_index] if 0 <= self.queue_index < len(self.queue) else None)

    def show_library(self):
        self.stack.setCurrentWidget(self.library)
        self.sidebar.set_active(self.sidebar.btn_library)
        self.library.refresh(self.top_search.edit.text().strip())
        self.library.set_playing(self.current_path)

    def show_search(self):
        self.stack.setCurrentWidget(self.library)
        self.sidebar.set_active(self.sidebar.btn_search)
        self.top_search.edit.setFocus()
        self.library.refresh(self.top_search.edit.text().strip())

    def search_now(self, query):
        self.stack.setCurrentWidget(self.library)
        self.sidebar.set_active(self.sidebar.btn_search)
        self.library.refresh(query)
        self.library.set_playing(self.current_path)

    def home_sorted(self, mode, reverse):
        self.home.load_albums(mode, reverse)

    def show_album(self, album_id):
        self.album.load(album_id)
        self.album.track_list.set_playing_path(self.current_path)
        self.stack.setCurrentWidget(self.album)
        self.sidebar.set_active(None)

    # ------------------------------------------------------------ first run
    def _first_run_check(self):
        create_desktop_shortcut()
        if self.music_folder and self.music_folder.exists():
            self._load_or_scan_existing()
        else:
            self.choose_music_folder(initial=True)

    def choose_music_folder(self, initial=False):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку с музыкой",
            str(self.music_folder or Path.home()),
        )
        if not folder:
            if initial:
                QMessageBox.information(
                    self,
                    "Папка не выбрана",
                    "Без музыкальной папки библиотеку загрузить нельзя.\n\n"
                    "Вы сможете выбрать её позже кнопкой ▣ слева.",
                )
            return
        self.music_folder = Path(folder)
        self.settings["music_folder"] = str(self.music_folder)
        save_settings(self.settings)
        self.start_scan()

    def _load_or_scan_existing(self):
        try:
            conn = db_connect()
            count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            conn.close()
            if count:
                self.home.ensure_loaded()
                return
        except Exception:
            pass
        self.start_scan()

    def start_scan(self):
        if self.scan_in_progress or not self.music_folder:
            return
        self.scan_in_progress = True
        dlg = QFrame(self)
        dlg.setObjectName("ScanPanel")
        dlg.setGeometry(0, 0, self.width(), self.height())
        lay = QVBoxLayout(dlg)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QFrame()
        card.setObjectName("ScanCard")
        card.setFixedWidth(560)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(34, 30, 34, 30)
        card_lay.setSpacing(12)
        title = QLabel("Сканируем музыкальную библиотеку")
        title.setObjectName("ScanTitle")
        card_lay.addWidget(title)
        self.scan_label = QLabel(f"Папка: {self.music_folder}")
        self.scan_label.setObjectName("MutedText")
        self.scan_label.setWordWrap(True)
        card_lay.addWidget(self.scan_label)
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 100)
        self.scan_progress.setValue(0)
        card_lay.addWidget(self.scan_progress)
        self.scan_detail = QLabel("Подготовка…")
        self.scan_detail.setObjectName("DimText")
        card_lay.addWidget(self.scan_detail)
        lay.addWidget(card)
        dlg.show()
        self.scan_panel = dlg

        task = ScanTask(self.music_folder, replace=True)
        task.signals.progress.connect(self._scan_progress)
        task.signals.finished.connect(self._scan_finished)
        task.signals.error.connect(self._scan_error)
        QThreadPool.globalInstance().start(task)

    def _scan_progress(self, current, total, title):
        if hasattr(self, "scan_progress"):
            pct = int(current * 100 / total) if total else 0
            self.scan_progress.setValue(pct)
            self.scan_detail.setText(f"{current:,} / {total:,}  •  {title}".replace(",", " "))

    def _scan_finished(self, success, failed):
        self.scan_in_progress = False
        if hasattr(self, "scan_panel"):
            self.scan_panel.hide()
            self.scan_panel.deleteLater()
        self.home.load_albums("Исполнитель", False)
        QMessageBox.information(self, "Библиотека готова", f"Добавлено треков: {success:,}".replace(",", " "))

    def _scan_error(self, message):
        self.scan_in_progress = False
        if hasattr(self, "scan_panel"):
            self.scan_panel.hide()
            self.scan_panel.deleteLater()
        QMessageBox.critical(self, "Ошибка сканирования", message)

    # ----------------------------------------------------------- playback
    def _set_queue(self, tracks: list[dict], index=0, shuffle=False):
        if not tracks:
            return
        self.queue = [dict(t) for t in tracks]
        self.queue_index = max(0, min(index, len(self.queue) - 1))
        self.history = [self.queue_index]
        self.history_pos = 0
        self.shuffle_enabled = shuffle
        self.player_bar.shuffle_btn.setChecked(shuffle)
        self._start_current()

    def play_all(self):
        tracks = fetch_all_tracks()
        self._set_queue(tracks, 0, False)

    def shuffle_all(self):
        tracks = fetch_all_tracks()
        random.shuffle(tracks)
        self._set_queue(tracks, 0, True)

    def play_album_index(self, index):
        tracks = self.album.tracks
        if tracks:
            self._set_queue(tracks, index, False)

    def play_library_index(self, index):
        results = self.library.results
        if results:
            self._set_queue(results, index, False)

    def play_library_all(self):
        if self.library.results:
            self._set_queue(list(self.library.results), 0, False)

    def shuffle_library(self):
        tracks = list(self.library.results)
        random.shuffle(tracks)
        self._set_queue(tracks, 0, True)

    def _start_current(self):
        if not (0 <= self.queue_index < len(self.queue)):
            return
        track = self.queue[self.queue_index]
        path = track.get("file_path")
        if not path or not Path(path).exists():
            QMessageBox.warning(self, "Файл не найден", f"{path}")
            self.play_next()
            return
        try:
            self.player.stop()
            media = self.vlc_instance.media_new(path)
            self.player.set_media(media)
            self.player.audio_set_volume(self.player_bar.volume_slider.value())
            if self.player.play() == -1:
                raise RuntimeError("VLC не смог начать воспроизведение.")
        except Exception as exc:
            QMessageBox.warning(self, "Ошибка воспроизведения", str(exc))
            return

        self.current_path = path
        self.track_duration = int(track.get("duration_ms") or 0)
        self.player_bar.set_track(track)
        self.player_bar.set_playing(True)
        self.home.set_now_playing(track)
        self.album.track_list.set_playing_path(path)
        self.library.set_playing(path)
        self.settings["last_track"] = path
        save_settings(self.settings)

    def toggle_play_pause(self):
        if self.queue_index < 0:
            return
        try:
            state = self.player.get_state()
            if state == vlc.State.Playing:
                self.player.pause()
                self.player_bar.set_playing(False)
            elif state in (vlc.State.Paused, vlc.State.Stopped, vlc.State.Ended):
                self.player.play()
                self.player_bar.set_playing(True)
            else:
                self.player.play()
                self.player_bar.set_playing(True)
        except Exception:
            self.player.play()
            self.player_bar.set_playing(True)

    def play_next(self):
        if not self.queue:
            return
        if self.shuffle_enabled and len(self.queue) > 1:
            choices = [i for i in range(len(self.queue)) if i != self.queue_index]
            next_index = random.choice(choices)
        else:
            next_index = self.queue_index + 1
        if next_index >= len(self.queue):
            self.player.stop()
            self.player_bar.set_playing(False)
            return
        self.queue_index = next_index
        self.history = self.history[: self.history_pos + 1]
        self.history.append(next_index)
        self.history_pos = len(self.history) - 1
        self._start_current()

    def play_previous(self):
        if self.history_pos > 0:
            self.history_pos -= 1
            self.queue_index = self.history[self.history_pos]
            self._start_current()
        elif not self.shuffle_enabled and self.queue_index > 0:
            self.queue_index -= 1
            self._start_current()

    def toggle_shuffle(self, enabled):
        self.shuffle_enabled = enabled

    def change_volume(self, value):
        value = int(value)
        self.player.audio_set_volume(value)
        self.settings["volume"] = value
        self._settings_save_timer.start()

    def seek_to(self, promille):
        length = self.player.get_length()
        if length and length > 0:
            self.player.set_time(int(length * max(0, min(1000, promille)) / 1000))

    def _tick(self):
        if not self.current_path:
            return
        try:
            state = self.player.get_state()
            if state == vlc.State.Ended:
                self.play_next()
                return
            pos = max(0, self.player.get_time())
            total = self.player.get_length()
            if total <= 0:
                total = self.track_duration
            self.player_bar.set_progress(pos, total)
            self.player_bar.set_playing(state == vlc.State.Playing)
        except Exception:
            pass

    # ---------------------------------------------------------------- close
    def closeEvent(self, event):
        try:
            self.timer.stop()
            self._settings_save_timer.stop()
            save_settings(self.settings)
            self.player.stop()
            self.vlc_instance.release()
        except Exception:
            pass
        super().closeEvent(event)

    # ---------------------------------------------------------------- style
    def _apply_styles(self):
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{
                background: {BG};
                color: {TEXT};
                font-family: "Segoe UI";
                font-size: 13px;
            }}
            QToolTip {{
                background: {SURFACE_3}; color: {TEXT}; border: 0; padding: 7px 10px;
                border-radius: 8px;
            }}
            #Sidebar {{ background: #09090b; border-right: 1px solid #1e1e21; }}
            #SideLogo {{ color: {ACCENT}; font-size: 31px; font-weight: 700; }}
            #SidebarSep {{ color: #222226; background: #222226; border: 0; max-height: 1px; }}
            #SidebarStatus {{ color: {GREEN}; font-size: 10px; padding-bottom: 2px; }}
            #SideButton {{
                background: transparent; border: 0; border-radius: 16px;
                color: #87878f; padding: 0;
            }}
            #SideButton:hover {{ background: #17171a; color: white; }}
            #SideButton:checked {{ background: #232327; color: {ACCENT}; }}
            #Content {{ background: {BG}; }}
            #TopSearch {{
                background: {SURFACE}; border: 1px solid #242428; border-radius: 16px;
                color: white; padding: 0 16px; font-size: 14px;
            }}
            #TopSearch:focus {{ border-color: #3b3b42; background: {SURFACE_2}; }}
            #SearchButton {{
                background: {SURFACE_2}; color: white; border: 1px solid #2b2b30;
                border-radius: 14px; padding: 0 18px; min-height: 44px;
            }}
            #SearchButton:hover {{ background: {SURFACE_3}; }}
            #BigTitle {{ font-size: 28px; font-weight: 750; color: white; }}
            #SectionTitle {{ font-size: 20px; font-weight: 700; }}
            #MutedText {{ color: {MUTED}; font-size: 12px; }}
            #DimText {{ color: {DIM}; font-size: 12px; }}
            #YellowCaption {{ color: {ACCENT}; font-size: 10px; font-weight: 700; letter-spacing: 1.2px; }}
            #NowPlayingCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1b1b20, stop:0.55 #151519, stop:1 #101013);
                border: 1px solid #25252a; border-radius: 24px;
            }}
            #NowTitle {{ font-size: 28px; font-weight: 800; color: white; }}
            #NowArtist {{ font-size: 15px; font-weight: 650; color: #ececf0; }}
            #AlbumCard {{
                background: {SURFACE}; border: 1px solid transparent; border-radius: 20px;
            }}
            #AlbumCard:hover {{ background: {SURFACE_2}; border-color: #2b2b31; }}
            #AlbumHero {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1a1a20, stop:1 #111114);
                border: 1px solid #26262c; border-radius: 24px;
            }}
            #AccentButton {{
                background: {ACCENT}; color: #161616; border: none; border-radius: 15px;
                padding: 11px 18px; font-weight: 800; font-size: 13px;
            }}
            #AccentButton:hover {{ background: {ACCENT_HOVER}; }}
            #AccentButton:pressed {{ background: {ACCENT_PRESSED}; }}
            #RoundButton {{
                background: {SURFACE_2}; color: #eaeaf0; border: 1px solid #2b2b30;
                border-radius: 14px; padding: 0 11px;
            }}
            #RoundButton:hover {{ background: {SURFACE_3}; color: {ACCENT}; }}
            #SortCombo {{
                background: {SURFACE}; color: white; border: 1px solid #2a2a30;
                border-radius: 12px; padding: 7px 10px; min-width: 116px;
            }}
            #SortCombo QAbstractItemView {{
                background: {SURFACE_2}; color: white; border: 1px solid #303037;
                selection-background-color: {SURFACE_3}; selection-color: {ACCENT};
            }}
            #TrackRow {{ background: transparent; border-radius: 13px; }}
            #TrackRow:hover {{ background: {SURFACE}; }}
            #TrackRow[playing="true"] {{ background: #211f10; }}
            #RowNum {{ color: {DIM}; font-size: 12px; }}
            #RowDuration {{ color: {MUTED}; font-size: 12px; }}
            #PlayerBar {{ background: #101013; border-top: 1px solid #232328; }}
            #MiniButton {{ background: transparent; color: {MUTED}; border: 0; border-radius: 18px; font-size: 18px; min-width: 34px; min-height: 34px; }}
            #MiniButton:hover {{ background: {SURFACE}; color: white; }}
            #MiniButton:checked {{ background: {ACCENT}; color: #111114; }}
            #PlayButton {{ background: white; color: #121214; border: 0; border-radius: 26px; font-size: 18px; font-weight: 800; }}
            #PlayButton:hover {{ background: #f2f2f4; }}
            #TinyText {{ color: {DIM}; font-size: 10px; }}
            QSlider::groove:horizontal {{ background: #303035; height: 4px; border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {ACCENT}; height: 4px; border-radius: 2px; }}
            QSlider::handle:horizontal {{ background: transparent; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
            QSlider:hover::handle:horizontal {{ background: {ACCENT}; }}
            #Progress {{ min-height: 15px; }}
            QScrollBar:vertical {{ background: transparent; width: 7px; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: #2b2b31; border-radius: 4px; min-height: 40px; }}
            QScrollBar::handle:vertical:hover {{ background: #46464d; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            #ScanPanel {{ background: rgba(8, 8, 10, 215); }}
            #ScanCard {{ background: {SURFACE}; border: 1px solid #323239; border-radius: 24px; }}
            #ScanTitle {{ font-size: 21px; font-weight: 800; }}
            QProgressBar {{ background: #29292f; border: 0; border-radius: 6px; text-align: center; height: 10px; color: #111; }}
            QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}
            """
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    try:
        window = MainWindow()
        window.show()
        return app.exec()
    except Exception as exc:
        QMessageBox.critical(None, "Ошибка запуска Local Music", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
