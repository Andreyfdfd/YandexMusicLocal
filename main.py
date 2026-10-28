# -*- coding: utf-8 -*-
"""
Local Music — локальный музыкальный клиент для Windows.

Цель этой версии:
    - аккуратная десктопная компоновка в духе Яндекс Музыки;
    - универсальный первый запуск: выбор папки с музыкой;
    - переносимая библиотека: в БД хранится относительный путь к файлу;
    - SQLite индексирует метаданные и технические параметры;
    - обложки извлекаются во время сканирования в отдельный локальный кэш;
    - GUI никогда не читает MP3/M4A напрямую для показа обложек;
    - изображения декодируются Pillow, а не Qt image plugins;
    - фоновые задачи через QThreadPool;
    - поиск, сортировка, фильтр жанра, направление сортировки;
    - альбомы, библиотека, страница альбома, текущий трек;
    - очередь, shuffle, repeat, перемотка, громкость;
    - горячие клавиши и контекстное меню треков;
    - изменение папки и фоновая пересборка библиотеки;
    - автоматический ярлык на рабочем столе;
    - воспроизведение локальных аудиоформатов через VLC; метаданные через Mutagen.

Зависимости:
    pip install -r requirements.txt

Для разработки нужен установленный VLC Media Player.
Для EXE BAT-скрипт пытается скопировать libVLC и plugins из установленного VLC
рядом с программой, поэтому конечному пользователю VLC ставить не обязательно,
если сборка выполнена на машине, где VLC уже установлен.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import queue
import random
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from mutagen import File as MutagenFile

# ============================================================================
# ПУТИ И НАСТРОЙКИ ПРИЛОЖЕНИЯ
# ============================================================================

APP_NAME = "Local Music"
APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LocalMusic"
APP_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = APP_DIR / "settings.json"
DB_PATH = APP_DIR / "music_collection.db"
COVER_CACHE_DIR = APP_DIR / "covers"
COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
ICON_PATH = APP_DIR / "local_music.ico"

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".flac", ".wav", ".ogg", ".opus",
    ".wma", ".aac", ".ape", ".aif", ".aiff", ".alac",
}
COVER_FILENAMES = (
    "cover.jpg", "cover.jpeg", "cover.png", "cover.webp",
    "folder.jpg", "folder.jpeg", "folder.png", "folder.webp",
    "front.jpg", "front.jpeg", "front.png", "front.webp",
    "album.jpg", "album.jpeg", "album.png", "album.webp",
)

# Визуальные размеры намеренно умеренные: никаких гигантских плиток на 27-дюймовом
# мониторе, но при этом элементы управления остаются удобными на ноутбуке.
SIDEBAR_WIDTH = 240
BOTTOM_PLAYER_HEIGHT = 92
ALBUM_CARD_WIDTH = 230
ALBUM_CARD_HEIGHT = 314
ALBUM_COVER_SIZE = 210
TRACK_ROW_HEIGHT = 72
TRACK_BATCH = 90
ALBUM_BATCH = 30

# Палитра — близкая к современной тёмной схеме музыкальных клиентов.
BG = "#111114"
BG_DEEP = "#0c0c0f"
SURFACE = "#19191d"
SURFACE_HOVER = "#25252a"
SURFACE_ACTIVE = "#303035"
SURFACE_SOFT = "#151518"
BORDER = "#303036"
TEXT = "#ffffff"
MUTED = "#b8b8c0"
DIM = "#8c8c96"
ACCENT = "#ffdf00"
ACCENT_HOVER = "#ffe84a"
ACCENT_PRESSED = "#e6c900"
SUCCESS = "#a6df4f"
DANGER = "#ff6666"

DEFAULT_SETTINGS = {
    "music_folder": "",
    "volume": 70,
    "repeat_mode": 0,
    "shuffle": False,
    "last_track_rel": "",
    "geometry": None,
    "maximized": True,
}


def configured_music_folder(settings: dict) -> Optional[Path]:
    """Return None for an unset folder; Path('') would incorrectly mean the current directory."""
    raw_path = str(settings.get("music_folder") or "").strip()
    if not raw_path:
        return None
    try:
        return Path(raw_path).expanduser()
    except (OSError, ValueError):
        return None


def load_settings() -> dict:
    result = dict(DEFAULT_SETTINGS)
    if not SETTINGS_PATH.exists():
        return result
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            result.update(data)
    except (OSError, ValueError, TypeError):
        pass
    return result


def save_settings(data: dict) -> None:
    temp = SETTINGS_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(SETTINGS_PATH)


def _desktop_path() -> Path:
    # Ask Windows for the real desktop known-folder path first. The Desktop
    # directory may be redirected to OneDrive or another location, so building
    # a shortcut under USERPROFILE\\Desktop can create a duplicate elsewhere.
    if os.name == "nt":
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(32768)
            result = ctypes.windll.shell32.SHGetFolderPathW(
                None, 0x0010, None, 0, buffer
            )
            if result == 0 and buffer.value:
                return Path(buffer.value)
        except (AttributeError, OSError, TypeError, ValueError):
            pass

    candidates = [
        Path.home() / "Desktop",
        Path(os.environ.get("USERPROFILE", "")) / "Desktop",
    ]
    for path in candidates:
        try:
            if path.exists():
                return path
        except OSError:
            continue
    return candidates[0]


def _create_runtime_icon() -> Path:
    """Создаёт локальную .ico, если её нет рядом с приложением."""
    if ICON_PATH.exists() and ICON_PATH.stat().st_size > 100:
        return ICON_PATH
    try:
        from PIL import Image, ImageDraw

        sizes = [16, 24, 32, 48, 64, 128, 256]
        images = []
        for size in sizes:
            image = Image.new("RGBA", (size, size), (10, 10, 12, 255))
            draw = ImageDraw.Draw(image)
            pad = max(1, size // 10)
            draw.rounded_rectangle(
                (pad, pad, size - pad - 1, size - pad - 1),
                radius=max(2, size // 5),
                fill=(255, 221, 0, 255),
            )
            stem_w = max(2, size // 9)
            x = int(size * 0.49)
            y = int(size * 0.28)
            draw.rectangle((x, y, x + stem_w, int(size * 0.69)), fill=(18, 18, 20, 255))
            draw.ellipse(
                (int(size * 0.28), int(size * 0.59), int(size * 0.49), int(size * 0.78)),
                fill=(18, 18, 20, 255),
            )
            draw.line(
                (x + stem_w, y, int(size * 0.70), int(size * 0.46)),
                fill=(18, 18, 20, 255),
                width=max(2, size // 12),
            )
            draw.ellipse(
                (int(size * 0.57), int(size * 0.40), int(size * 0.78), int(size * 0.59)),
                fill=(18, 18, 20, 255),
            )
            images.append(image)
        images[0].save(
            ICON_PATH,
            format="ICO",
            sizes=[(s, s) for s in sizes],
            append_images=images[1:],
        )
    except Exception:
        return Path("")
    return ICON_PATH


def create_desktop_shortcut_async() -> None:
    """Refresh the matching shortcut for a packaged build, never for source runs.

    Development builds use a separate preview shortcut so running main.py or
    testing a preview EXE cannot replace the user's stable desktop launcher.
    The build script is the only component that creates a shortcut for a source
    checkout.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return

    def worker() -> None:
        try:
            executable = Path(sys.executable).resolve()
            is_preview = (
                "preview" in executable.stem.casefold()
                or "preview" in executable.parent.name.casefold()
            )
            shortcut_name = "Local Music Preview.lnk" if is_preview else "Local Music.lnk"
            desktop = _desktop_path()
            desktop.mkdir(parents=True, exist_ok=True)
            shortcut = desktop / shortcut_name
            icon = _create_runtime_icon()

            target = str(executable)
            working_dir = str(executable.parent)
            icon_location = str(icon) if icon else target
            description = (
                "Локальный музыкальный плеер Local Music — тестовая сборка"
                if is_preview
                else "Локальный музыкальный плеер Local Music"
            )

            ps = f"""
$ErrorActionPreference = 'Stop'
try {{
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut({json.dumps(str(shortcut))})
    $sc.TargetPath = {json.dumps(target)}
    $sc.Arguments = ''
    $sc.WorkingDirectory = {json.dumps(working_dir)}
    $sc.IconLocation = {json.dumps(icon_location)}
    $sc.Description = {json.dumps(description)}
    $sc.Save()
}} catch {{
    exit 1
}}
"""
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    ps,
                ],
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


# ============================================================================
# VLC: ищем локальную копию libVLC и обычную установку
# ============================================================================


def _candidate_vlc_dirs() -> list[Path]:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend([exe_dir / "vlc", exe_dir])
    else:
        here = Path(__file__).resolve().parent
        candidates.append(here / "vlc")

    candidates.extend(
        [
            Path(r"C:\Program Files\VideoLAN\VLC"),
            Path(r"C:\Program Files (x86)\VideoLAN\VLC"),
        ]
    )
    return candidates


VLC_DIR = None
for _dir in _candidate_vlc_dirs():
    if (_dir / "libvlc.dll").exists():
        VLC_DIR = _dir
        try:
            os.add_dll_directory(str(_dir))
        except (AttributeError, OSError):
            pass
        os.environ["PATH"] = str(_dir) + os.pathsep + os.environ.get("PATH", "")
        plugins = _dir / "plugins"
        if plugins.exists():
            os.environ["VLC_PLUGIN_PATH"] = str(plugins)
        break

try:
    import vlc
except Exception as exc:  # pragma: no cover - зависит от машины
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
    QRectF,
    QSize,
    QPoint,
    QPropertyAnimation,
    QEasingCurve,
    pyqtSignal,
    QUrl,
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
    QToolButton,
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
    QMenu,
    QSpacerItem,
    QStyle,
    QStyleOptionSlider,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

from PIL import Image, ImageOps


# ============================================================================
# БАЗА ДАННЫХ
# ============================================================================

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
    album_id INTEGER,
    track_name TEXT NOT NULL,
    genre TEXT DEFAULT 'Разное',
    file_path TEXT NOT NULL UNIQUE,
    relative_path TEXT DEFAULT NULL,
    duration_ms INTEGER DEFAULT 0,
    file_size INTEGER DEFAULT 0,
    mtime REAL DEFAULT 0,
    favorite INTEGER DEFAULT 0,
    FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS listening_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_key TEXT NOT NULL,
    track_name TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    file_path TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL,
    ended_at REAL DEFAULT NULL,
    listened_ms INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_listening_started ON listening_sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_listening_track ON listening_sessions(track_key);
CREATE INDEX IF NOT EXISTS idx_listening_artist ON listening_sessions(artist);
CREATE INDEX IF NOT EXISTS idx_listening_album ON listening_sessions(artist, album);

"""


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute("PRAGMA foreign_keys = ON")
    # Journal mode is persistent for the database and is set during schema
    # initialization. Reissuing PRAGMA journal_mode=WAL on every read connection
    # can contend with a concurrent scanner transaction.
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.create_function(
        "CASEFOLD",
        1,
        lambda value: value.casefold() if isinstance(value, str) else value,
    )
    return conn


def ensure_database_schema() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        current_journal = conn.execute("PRAGMA journal_mode").fetchone()
        if not current_journal or str(current_journal[0]).lower() != "wal":
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(SCHEMA_SQL)
        track_cols = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
        album_cols = {row[1] for row in conn.execute("PRAGMA table_info(albums)")}

        additions_tracks = {
            "relative_path": "TEXT DEFAULT NULL",
            "duration_ms": "INTEGER DEFAULT 0",
            "file_size": "INTEGER DEFAULT 0",
            "mtime": "REAL DEFAULT 0",
            "favorite": "INTEGER DEFAULT 0",
        }
        additions_albums = {"cover_path": "TEXT DEFAULT NULL"}

        for column, definition in additions_tracks.items():
            if column not in track_cols:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {column} {definition}")
        for column, definition in additions_albums.items():
            if column not in album_cols:
                conn.execute(f"ALTER TABLE albums ADD COLUMN {column} {definition}")

        # Indexes must be created only after ALTER TABLE has added newer columns.
        # The previous ordering failed on databases created by older player builds.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_name ON tracks(track_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_genre ON tracks(genre)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_duration ON tracks(duration_ms)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_relative_path ON tracks(relative_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_artist ON albums(artist)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_albums_name ON albums(album_name)")
        conn.commit()
    finally:
        conn.close()


def migrate_relative_paths(music_folder: Path) -> None:
    """Однократно переводит старые абсолютные пути в относительные."""
    if not music_folder or not music_folder.exists():
        return
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT id, file_path FROM tracks WHERE relative_path IS NULL OR relative_path = ''"
        ).fetchall()
        changed = 0
        root = music_folder.resolve()
        for track_id, file_path in rows:
            if not file_path:
                continue
            try:
                rel = Path(str(file_path)).resolve().relative_to(root).as_posix()
            except (OSError, ValueError):
                continue
            conn.execute("UPDATE tracks SET relative_path=? WHERE id=?", (rel, track_id))
            changed += 1
            if changed % 500 == 0:
                conn.commit()
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


def format_ms(milliseconds: int | None) -> str:
    total = max(0, int(milliseconds or 0)) // 1000
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def format_bytes(size: int | None) -> str:
    value = max(0, int(size or 0))
    units = ["Б", "КБ", "МБ", "ГБ"]
    unit = units[0]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            break
        value /= 1024
    if unit == "Б":
        return f"{int(value)} {unit}"
    return f"{value:.1f} {unit}"


def format_listening_ms(milliseconds: int | None) -> str:
    """Compact human-readable lifetime listening time."""
    total_minutes = max(0, int(milliseconds or 0)) // 60_000
    if total_minutes == 0:
        return "—" if not milliseconds else "< 1 мин"
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин" if minutes else f"{hours} ч"
    return f"{minutes} мин"


def start_listening_session(track: dict) -> Optional[int]:
    """Persist a playback session immediately, so history survives a crash."""
    file_path = str(track.get("file_path") or "")
    if not file_path:
        return None
    started_at = time.time()
    conn = db_connect()
    try:
        cursor = conn.execute(
            """INSERT INTO listening_sessions
               (track_key, track_name, artist, album, file_path, started_at, listened_ms, completed)
               VALUES (?, ?, ?, ?, ?, ?, 0, 0)""",
            (
                file_path,
                safe_text(track.get("track_name"), "Неизвестный трек"),
                safe_text(track.get("artist"), "Неизвестный исполнитель"),
                safe_text(track.get("album"), "Без названия"),
                file_path,
                started_at,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def update_listening_session(
    session_id: Optional[int],
    listened_ms: int,
    *,
    finished: bool = False,
    completed: bool = False,
) -> None:
    if not session_id:
        return
    conn = db_connect()
    try:
        if finished:
            conn.execute(
                """UPDATE listening_sessions
                   SET listened_ms=?, ended_at=?, completed=?
                   WHERE id=?""",
                (max(0, int(listened_ms)), time.time(), int(bool(completed)), session_id),
            )
        else:
            conn.execute(
                "UPDATE listening_sessions SET listened_ms=? WHERE id=?",
                (max(0, int(listened_ms)), session_id),
            )
        conn.commit()
    finally:
        conn.close()


class ListeningSessionWriterSignals(QObject):
    started = pyqtSignal(int, object, str)
    error = pyqtSignal(int, str)


class ListeningSessionWriter:
    """Serialize listening-history writes outside the GUI thread.

    The background scanner may hold SQLite's single writer lock while indexing
    changed media. Playback must not wait for that lock on the Qt event thread.
    Commands are ordered so start/update/finish operations for a session cannot
    race each other.
    """

    def __init__(self):
        self.signals = ListeningSessionWriterSignals()
        self._commands = queue.Queue()
        self._pending = 0
        self._pending_condition = threading.Condition()
        self._thread = threading.Thread(
            target=self._run,
            name="ListeningSessionWriter",
            daemon=True,
        )
        self._thread.start()

    def _enqueue(self, command) -> None:
        with self._pending_condition:
            self._pending += 1
        self._commands.put(command)

    def start_session(self, generation: int, track: dict) -> None:
        self._enqueue(("start", int(generation), dict(track)))

    def update_session(self, generation: int, listened_ms: int) -> None:
        self._enqueue(("update", int(generation), max(0, int(listened_ms))))

    def finish_session(
        self, generation: int, listened_ms: int, *, completed: bool = False
    ) -> None:
        self._enqueue(
            ("finish", int(generation), max(0, int(listened_ms)), bool(completed))
        )

    def wait_for_idle(self, timeout: float = 0.5) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._pending_condition:
            while self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._pending_condition.wait(remaining)
            return True

    def shutdown(self) -> None:
        self._commands.put(None)

    @staticmethod
    def _with_locked_retry(operation):
        for attempt in range(3):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() or attempt == 2:
                    raise
                time.sleep(0.25 * (attempt + 1))

    def _run(self) -> None:
        sessions: dict[int, Optional[int]] = {}
        while True:
            command = self._commands.get()
            if command is None:
                self._commands.task_done()
                return

            kind, generation, *args = command
            try:
                if kind == "start":
                    session_id = self._with_locked_retry(
                        lambda: start_listening_session(args[0])
                    )
                    sessions[generation] = session_id
                    self.signals.started.emit(generation, session_id, "")
                elif kind == "update":
                    session_id = sessions.get(generation)
                    if session_id:
                        self._with_locked_retry(
                            lambda: update_listening_session(session_id, args[0])
                        )
                elif kind == "finish":
                    session_id = sessions.get(generation)
                    if session_id:
                        self._with_locked_retry(
                            lambda: update_listening_session(
                                session_id,
                                args[0],
                                finished=True,
                                completed=args[1],
                            )
                        )
                    sessions.pop(generation, None)
            except Exception as exc:
                if kind == "start":
                    sessions[generation] = None
                    self.signals.started.emit(generation, None, str(exc))
                else:
                    self.signals.error.emit(generation, f"{kind}: {exc}")
            finally:
                with self._pending_condition:
                    self._pending -= 1
                    self._pending_condition.notify_all()
                self._commands.task_done()


def fetch_artist_listening_totals() -> dict[str, int]:
    conn = db_connect()
    try:
        rows = conn.execute(
            """SELECT artist, COALESCE(SUM(listened_ms), 0)
               FROM listening_sessions
               GROUP BY artist COLLATE NOCASE"""
        ).fetchall()
    finally:
        conn.close()
    return {
        safe_text(artist, "Неизвестный исполнитель").casefold(): int(listened_ms or 0)
        for artist, listened_ms in rows
    }


def build_local_wave(
    tracks: list[dict],
    artist_listening_ms: Optional[dict[str, int]] = None,
) -> list[dict]:
    """Create a weighted local-library mix while avoiding artist dead ends."""
    artist_listening_ms = artist_listening_ms or {}
    remaining: list[tuple[dict, float]] = []
    for original in tracks:
        track = dict(original)
        artist_key = safe_text(track.get("artist"), "Неизвестный исполнитель").casefold()
        track_minutes = max(0, int(track.get("listened_ms") or 0)) / 60_000
        artist_minutes = max(0, int(artist_listening_ms.get(artist_key, 0))) / 60_000
        favorite_boost = 1.7 if track.get("favorite") else 0.0
        discovery_boost = 1.0 if track_minutes < 2 else 0.0
        weight = (
            1.0
            + math.log1p(track_minutes) * 0.45
            + math.log1p(artist_minutes) * 0.65
            + favorite_boost
            + discovery_boost
        )
        remaining.append((track, max(0.1, weight)))

    mix: list[dict] = []
    previous_artist = ""
    while remaining:
        artist_keys = [
            safe_text(track.get("artist"), "Неизвестный исполнитель").casefold()
            for track, _weight in remaining
        ]
        artist_counts: dict[str, int] = {}
        for artist_key in artist_keys:
            artist_counts[artist_key] = artist_counts.get(artist_key, 0) + 1

        eligible = [
            index for index, artist_key in enumerate(artist_keys)
            if artist_key != previous_artist
        ]
        if not eligible:
            # Repetition is unavoidable only when all remaining tracks belong
            # to the same artist as the previously selected track.
            eligible = list(range(len(remaining)))
        else:
            # A locally valid choice can still leave two tracks by one artist
            # stranded at the end. Keep candidates whose removal leaves a
            # feasible no-adjacent-artist ordering, whenever such a choice exists.
            remaining_after = len(remaining) - 1
            viable: list[int] = []
            for index in eligible:
                candidate_artist = artist_keys[index]
                counts_after = dict(artist_counts)
                counts_after[candidate_artist] -= 1
                if counts_after[candidate_artist] > remaining_after // 2:
                    continue
                other_limit = (remaining_after + 1) // 2
                if any(
                    count > other_limit
                    for artist, count in counts_after.items()
                    if artist != candidate_artist
                ):
                    continue
                viable.append(index)
            if viable:
                eligible = viable

        chosen_index = random.choices(
            eligible,
            weights=[remaining[index][1] for index in eligible],
            k=1,
        )[0]
        track, _weight = remaining.pop(chosen_index)
        mix.append(track)
        previous_artist = safe_text(
            track.get("artist"), "Неизвестный исполнитель"
        ).casefold()
    return mix


def fetch_listening_summary() -> dict:
    conn = db_connect()
    try:
        row = conn.execute(
            """SELECT COALESCE(SUM(listened_ms), 0),
                      COUNT(*),
                      COUNT(DISTINCT track_key),
                      MAX(started_at)
               FROM listening_sessions"""
        ).fetchone()
    finally:
        conn.close()
    row = row or (0, 0, 0, None)
    return {
        "listened_ms": int(row[0] or 0),
        "sessions": int(row[1] or 0),
        "tracks": int(row[2] or 0),
        "last_played": row[3],
    }


def fetch_listening_view(mode: str, limit: int = 500) -> list[dict]:
    conn = db_connect()
    try:
        if mode == "История":
            # One row per track, not per listening session.
            rows = conn.execute(
                """SELECT MAX(track_name), MAX(artist), MAX(album), track_key,
                          COUNT(*) AS sessions, SUM(listened_ms) AS total_ms,
                          MAX(started_at), MAX(completed)
                   FROM listening_sessions
                   GROUP BY track_key
                   ORDER BY MAX(started_at) DESC, SUM(listened_ms) DESC LIMIT ?""",
                (int(limit),),
            ).fetchall()
            return [
                {"title": safe_text(r[0], "Неизвестный трек"),
                 "artist": safe_text(r[1], "Неизвестный исполнитель"),
                 "album": safe_text(r[2], "Без названия"),
                 "file_path": str(r[3] or ""),
                 "sessions": int(r[4] or 0), "listened_ms": int(r[5] or 0),
                 "last_played": r[6], "completed": bool(r[7])}
                for r in rows
            ]

        if mode == "Исполнители":
            # Recency determines artist order; lifetime totals remain visible.
            rows = conn.execute(
                """SELECT MAX(artist), COUNT(DISTINCT track_key),
                          SUM(listened_ms), MAX(started_at), COUNT(*)
                   FROM listening_sessions
                   GROUP BY artist COLLATE NOCASE
                   ORDER BY MAX(started_at) DESC, SUM(listened_ms) DESC,
                            MAX(artist) COLLATE NOCASE LIMIT ?""",
                (int(limit),),
            ).fetchall()
            return [
                {"title": safe_text(r[0], "Неизвестный исполнитель"),
                 "artist": "",
                 "album": f"{int(r[1] or 0)} треков · {int(r[4] or 0)} сессий",
                 "track_count": int(r[1] or 0), "sessions": int(r[4] or 0),
                 "listened_ms": int(r[2] or 0), "last_played": r[3],
                 "file_path": ""}
                for r in rows
            ]

        if mode == "Альбомы":
            rows = conn.execute(
                """SELECT album, artist, COUNT(DISTINCT track_key),
                          SUM(listened_ms), MAX(started_at)
                   FROM listening_sessions
                   GROUP BY artist COLLATE NOCASE, album COLLATE NOCASE
                   ORDER BY SUM(listened_ms) DESC, album COLLATE NOCASE LIMIT ?""",
                (int(limit),),
            ).fetchall()
            return [
                {"title": safe_text(r[0], "Без названия"),
                 "artist": safe_text(r[1], "Неизвестный исполнитель"),
                 "album": f"{int(r[2] or 0)} треков",
                 "track_count": int(r[2] or 0),
                 "listened_ms": int(r[3] or 0), "last_played": r[4]}
                for r in rows
            ]

        rows = conn.execute(
            """SELECT MAX(track_name), MAX(artist), MAX(album), track_key,
                      COUNT(*) AS sessions, SUM(listened_ms) AS total_ms,
                      MAX(started_at)
               FROM listening_sessions
               GROUP BY track_key
               ORDER BY SUM(listened_ms) DESC, MAX(started_at) DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
        return [
            {"title": safe_text(r[0], "Неизвестный трек"),
             "artist": safe_text(r[1], "Неизвестный исполнитель"),
             "album": safe_text(r[2], "Без названия"),
             "file_path": str(r[3] or ""),
             "sessions": int(r[4] or 0), "listened_ms": int(r[5] or 0),
             "last_played": r[6]}
            for r in rows
        ]
    finally:
        conn.close()


def fetch_artist_track_history(artist: str, limit: int = 500) -> list[dict]:
    """Return unique tracks for an artist, ranked by cumulative listening time."""
    conn = db_connect()
    try:
        rows = conn.execute(
            """SELECT MAX(track_name), MAX(artist), MAX(album), track_key,
                      COUNT(*) AS sessions, SUM(listened_ms) AS total_ms,
                      MAX(started_at)
               FROM listening_sessions
               WHERE artist = ? COLLATE NOCASE
               GROUP BY track_key
               ORDER BY SUM(listened_ms) DESC, MAX(started_at) DESC,
                        MAX(track_name) COLLATE NOCASE LIMIT ?""",
            (str(artist), int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"title": safe_text(r[0], "Неизвестный трек"),
         "artist": safe_text(r[1], "Неизвестный исполнитель"),
         "album": safe_text(r[2], "Без названия"),
         "file_path": str(r[3] or ""),
         "sessions": int(r[4] or 0), "listened_ms": int(r[5] or 0),
         "last_played": r[6]}
        for r in rows
    ]


def resolve_track_path(track: dict, music_folder: Path) -> str:
    relative = track.get("relative_path")
    if relative:
        try:
            return str((music_folder / Path(relative)).resolve())
        except (OSError, ValueError):
            pass
    path = str(track.get("file_path") or "")
    return path


def relative_path_for(path: Path, music_folder: Path) -> str:
    try:
        return path.resolve().relative_to(music_folder.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


# ============================================================================
# ОБЛОЖКИ
# ============================================================================


def _cover_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()  # noqa: S324 - локальный кэш, не безопасность


def save_cover_bytes(data: Optional[bytes]) -> Optional[str]:
    if not data:
        return None
    digest = _cover_hash(data)
    output = COVER_CACHE_DIR / f"{digest}.jpg"
    if output.exists() and output.stat().st_size > 100:
        return str(output)
    temp = output.with_suffix(".tmp")
    try:
        from io import BytesIO

        with Image.open(BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            else:
                image = image.convert("RGB")
            image.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
            image.save(temp, format="JPEG", quality=92, optimize=True)
        temp.replace(output)
        return str(output)
    except Exception:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
        return None


def find_folder_cover_bytes(folder: Path) -> Optional[bytes]:
    for filename in COVER_FILENAMES:
        candidate = folder / filename
        try:
            if candidate.is_file():
                return candidate.read_bytes()
        except OSError:
            continue
    return None


def extract_embedded_cover(path: str, audio_obj=None) -> Optional[bytes]:
    """Извлекает обложку по запросу, а не при чтении метаданных каждого трека."""
    try:
        extension = Path(path).suffix.lower()
        audio = audio_obj
        if audio is None:
            if extension == ".mp3":
                audio = MP3(path)
            elif extension == ".m4a":
                audio = MP4(path)
            else:
                audio = MutagenFile(path, easy=False)

        if audio is None:
            return None

        if extension == ".mp3":
            tags = getattr(audio, "tags", None)
            if tags:
                frames = tags.getall("APIC")
                if frames:
                    return bytes(frames[0].data)

        elif extension == ".m4a":
            tags = getattr(audio, "tags", None)
            if tags:
                covers = tags.get("covr")
                if covers:
                    return bytes(covers[0])

        pictures = getattr(audio, "pictures", None)
        if pictures:
            return bytes(pictures[0].data)

        tags = getattr(audio, "tags", None)
        if tags:
            # ID3-like frames (used by several formats).
            if hasattr(tags, "getall"):
                frames = tags.getall("APIC")
                if frames:
                    return bytes(frames[0].data)

            # FLAC/Ogg Vorbis commonly store a base64-encoded FLAC Picture block.
            picture_values = tags.get("metadata_block_picture") if hasattr(tags, "get") else None
            if picture_values:
                values = picture_values if isinstance(picture_values, (list, tuple)) else [picture_values]
                from mutagen.flac import Picture

                for value in values:
                    try:
                        encoded = value.encode("ascii") if isinstance(value, str) else bytes(value)
                        picture = Picture(base64.b64decode(encoded))
                        if picture.data:
                            return bytes(picture.data)
                    except Exception:
                        continue

            # Some Vorbis files use the simpler COVERART field.
            cover_values = tags.get("coverart") if hasattr(tags, "get") else None
            if cover_values:
                value = cover_values[0] if isinstance(cover_values, (list, tuple)) else cover_values
                try:
                    encoded = value.encode("ascii") if isinstance(value, str) else bytes(value)
                    return base64.b64decode(encoded)
                except Exception:
                    pass
    except Exception:
        return None
    return None


class CoverMemoryCache:
    """Thread-safe LRU for decoded images with a strict memory budget."""

    def __init__(self, max_bytes: int = 64 * 1024 * 1024):
        self.max_bytes = max(1024, int(max_bytes))
        self._lock = threading.RLock()
        self._images: OrderedDict[tuple[str, int], QImage] = OrderedDict()
        self._image_bytes: dict[tuple[str, int], int] = {}
        self._total_bytes = 0

    @staticmethod
    def _byte_size(image: QImage) -> int:
        try:
            return max(0, int(image.sizeInBytes()))
        except (AttributeError, RuntimeError):
            return max(0, int(image.bytesPerLine() * image.height()))

    def get(self, path: str, size: int) -> Optional[QImage]:
        key = (path, int(size))
        with self._lock:
            image = self._images.get(key)
            if image is not None:
                self._images.move_to_end(key)
            return image

    def put(self, path: str, size: int, image: Optional[QImage]) -> None:
        if image is None or image.isNull():
            return
        key = (path, int(size))
        image_bytes = self._byte_size(image)
        # Do not let one unusually large image evict the entire useful cache.
        if image_bytes > self.max_bytes:
            return

        with self._lock:
            previous_size = self._image_bytes.pop(key, 0)
            if key in self._images:
                self._images.pop(key, None)
                self._total_bytes -= previous_size

            self._images[key] = image
            self._image_bytes[key] = image_bytes
            self._total_bytes += image_bytes

            while self._total_bytes > self.max_bytes and self._images:
                old_key, _old_image = self._images.popitem(last=False)
                old_size = self._image_bytes.pop(old_key, 0)
                self._total_bytes -= old_size

    def clear(self) -> None:
        with self._lock:
            self._images.clear()
            self._image_bytes.clear()
            self._total_bytes = 0


COVER_CACHE = CoverMemoryCache()


def make_placeholder(size: int, radius: int = 14) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0, QColor("#303033"))
    gradient.setColorAt(1, QColor("#151516"))
    painter.fillPath(path, QBrush(gradient))
    painter.setPen(QColor(255, 221, 0, 90))
    painter.setFont(QFont("Segoe UI Symbol", max(18, int(size * 0.26))))
    painter.drawText(QRectF(0, 0, size, size), int(Qt.AlignmentFlag.AlignCenter), "♪")
    painter.end()
    return pm


def load_qimage_from_cover(path: str, size: int) -> Optional[QImage]:
    cached = COVER_CACHE.get(path, size)
    if cached is not None:
        return cached
    if not path:
        return None
    try:
        with Image.open(path) as source:
            source = ImageOps.exif_transpose(source).convert("RGBA")
            fitted = ImageOps.fit(
                source,
                (int(size), int(size)),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            raw = fitted.tobytes("raw", "RGBA")
            image = QImage(
                raw,
                fitted.width(),
                fitted.height(),
                fitted.width() * 4,
                QImage.Format.Format_RGBA8888,
            ).copy()
        COVER_CACHE.put(path, size, image)
        return image
    except Exception:
        return None


def rounded_pixmap(image: Optional[QImage], size: int, radius: int = 14) -> QPixmap:
    if image is None or image.isNull():
        return make_placeholder(size, radius)
    pm = QPixmap.fromImage(image)
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pm)
    painter.end()
    return out


# ============================================================================
# ФОНОВЫЕ ЗАДАЧИ
# ============================================================================


class WorkerSignals(QObject):
    ready = pyqtSignal(object)


class CoverTask(QRunnable):
    def __init__(self, path: str, size: int, token: int):
        super().__init__()
        self.path = path
        self.size = size
        self.token = token
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        image = load_qimage_from_cover(self.path, self.size)
        try:
            self.signals.ready.emit((self.token, image))
        except RuntimeError:
            pass


class AsyncCover(QLabel):
    def __init__(self, size: int, radius: int = 14, parent=None):
        super().__init__(parent)
        self.size_px = int(size)
        self.radius = int(radius)
        self.token = 0
        self.pool = QThreadPool.globalInstance()
        self.setFixedSize(self.size_px, self.size_px)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setPixmap(make_placeholder(self.size_px, self.radius))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def load(self, path: Optional[str]) -> None:
        self.token += 1
        self.setPixmap(make_placeholder(self.size_px, self.radius))
        if not path:
            return
        task = CoverTask(path, self.size_px, self.token)
        task.signals.ready.connect(self._on_ready)
        self.pool.start(task)

    def reset(self) -> None:
        self.token += 1
        self.setPixmap(make_placeholder(self.size_px, self.radius))

    def _on_ready(self, payload) -> None:
        try:
            token, image = payload
            if token != self.token or image is None or image.isNull():
                return
            self.setPixmap(rounded_pixmap(image, self.size_px, self.radius))
        except RuntimeError:
            pass


# ============================================================================
# СКАНЕР
# ============================================================================


class ScanSignals(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(int, int)
    error = pyqtSignal(str)


class ScanTask(QRunnable):
    """Фоновая инкрементальная индексация: неизменившиеся треки не читаются повторно."""

    def __init__(self, folder: Path):
        super().__init__()
        self.folder = folder.resolve()
        self.signals = ScanSignals()
        self.setAutoDelete(True)

    @staticmethod
    def _path_key(path) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    @staticmethod
    def _is_inside_root(file_path: str, root: Path) -> bool:
        try:
            Path(file_path).resolve().relative_to(root.resolve())
            return True
        except (OSError, ValueError, RuntimeError):
            return False

    @staticmethod
    def parse_media(path: Path) -> dict:
        result = {
            "artist": "Разные исполнители",
            "album": path.parent.name or "Синглы",
            "title": path.stem,
            "genre": "Разное",
            "year": None,
            "duration_ms": 0,
            "file_size": 0,
            "mtime": 0.0,
            "_audio": None,
        }

        try:
            stat = path.stat()
            result["file_size"] = int(stat.st_size)
            result["mtime"] = float(stat.st_mtime)
        except OSError:
            pass

        try:
            ext = path.suffix.lower()
            audio = None

            if ext == ".mp3":
                audio = MP3(str(path))
                result["duration_ms"] = max(
                    0,
                    int(float(getattr(audio.info, "length", 0) or 0) * 1000),
                )
                tags = audio.tags
                if tags:
                    if tags.get("TPE1") and tags["TPE1"].text:
                        result["artist"] = str(tags["TPE1"].text[0]).strip()
                    if tags.get("TALB") and tags["TALB"].text:
                        result["album"] = str(tags["TALB"].text[0]).strip()
                    if tags.get("TIT2") and tags["TIT2"].text:
                        result["title"] = str(tags["TIT2"].text[0]).strip()
                    if tags.get("TCON") and tags["TCON"].text:
                        result["genre"] = str(tags["TCON"].text[0]).strip()
                    if tags.get("TDRC") and tags["TDRC"].text:
                        raw = str(tags["TDRC"].text[0]).strip()
                        if raw[:4].isdigit():
                            result["year"] = int(raw[:4])

            elif ext == ".m4a":
                audio = MP4(str(path))
                result["duration_ms"] = max(
                    0,
                    int(float(getattr(audio.info, "length", 0) or 0) * 1000),
                )
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
                        raw = str(tags["\xa9day"][0]).strip()
                        if raw[:4].isdigit():
                            result["year"] = int(raw[:4])

            else:
                # Mutagen's easy tags cover FLAC, WAV, Ogg/Vorbis, Opus, WMA,
                # APE and other supported formats without format-specific code.
                audio = MutagenFile(str(path), easy=True)
                if audio is not None:
                    result["duration_ms"] = max(
                        0,
                        int(float(getattr(audio.info, "length", 0) or 0) * 1000),
                    )
                    tags = getattr(audio, "tags", None) or {}
                    for tag_name, result_name in (
                        ("artist", "artist"),
                        ("album", "album"),
                        ("title", "title"),
                        ("genre", "genre"),
                    ):
                        values = tags.get(tag_name) if hasattr(tags, "get") else None
                        if values:
                            value = values[0] if isinstance(values, (list, tuple)) else values
                            result[result_name] = str(value).strip()
                    dates = tags.get("date") if hasattr(tags, "get") else None
                    if dates:
                        raw = dates[0] if isinstance(dates, (list, tuple)) else dates
                        raw = str(raw).strip()
                        if raw[:4].isdigit():
                            result["year"] = int(raw[:4])

            result["_audio"] = audio
        except Exception:
            # Битые/неподдерживаемые теги не должны ронять всё сканирование.
            result["_audio"] = None

        result["artist"] = safe_text(result["artist"], "Разные исполнители")
        result["album"] = safe_text(result["album"], "Синглы")
        result["title"] = safe_text(result["title"], path.stem)
        result["genre"] = safe_text(result["genre"], "Разное")
        return result

    def run(self) -> None:
        conn = None
        try:
            if not self.folder.is_dir():
                raise FileNotFoundError(f"Музыкальная папка недоступна: {self.folder}")

            ensure_database_schema()
            # Legacy relative-path migration is done on this worker, never in
            # the UI thread where a large SQLite index could delay first paint.
            migrate_relative_paths(self.folder)

            # Сначала перечисляем лёгкие пути файлов. Ошибка обхода отменяет
            # сканирование до любых изменений БД, чтобы не потерять индекс.
            walk_errors = []
            files = []
            for root, _dirs, names in os.walk(self.folder, onerror=walk_errors.append):
                for name in names:
                    path = Path(root) / name
                    if path.suffix.lower() in AUDIO_EXTENSIONS:
                        files.append(path)

            if walk_errors:
                first_error = walk_errors[0]
                raise OSError(
                    f"Не удалось полностью прочитать музыкальную папку: {first_error}"
                )

            files.sort(key=lambda item: str(item).casefold())
            total = len(files)

            conn = db_connect()
            existing_rows = conn.execute(
                "SELECT file_path, file_size, mtime FROM tracks"
            ).fetchall()

            # Если в БД уже есть треки из другой папки, это смена библиотеки:
            # перестраиваем её в транзакции, сохраняя старый индекс до commit.
            same_library = (
                not existing_rows
                or all(
                    self._is_inside_root(str(row[0] or ""), self.folder)
                    for row in existing_rows
                )
            )

            existing_by_path = {}
            if same_library:
                for file_path, file_size, mtime in existing_rows:
                    if not file_path:
                        continue
                    existing_by_path[self._path_key(file_path)] = {
                        "file_path": str(file_path),
                        "file_size": int(file_size or 0),
                        "mtime": float(mtime or 0),
                    }

            conn.execute("BEGIN")
            if not same_library:
                conn.execute("DELETE FROM tracks")
                conn.execute("DELETE FROM albums")
                existing_by_path = {}

            current_path_keys = set()
            album_cache = {}
            album_cover_cache = {}
            folder_cover_cache = {}
            successes = 0
            failed = 0

            for index, path in enumerate(files, start=1):
                try:
                    resolved_path = path.resolve()
                except (OSError, RuntimeError):
                    failed += 1
                    continue

                path_key = self._path_key(resolved_path)
                current_path_keys.add(path_key)

                try:
                    stat = path.stat()
                except OSError:
                    # Оставляем старую запись нетронутой, если файл временно
                    # недоступен: её нельзя считать удалённой.
                    failed += 1
                    continue

                previous = existing_by_path.get(path_key)
                unchanged = (
                    previous is not None
                    and previous["file_size"] == int(stat.st_size)
                    and abs(previous["mtime"] - float(stat.st_mtime)) < 0.001
                )

                if unchanged:
                    successes += 1
                    if index == 1 or index % 40 == 0 or index == total:
                        self.signals.progress.emit(index, total, path.stem)
                    continue

                meta = self.parse_media(path)
                album_key = (meta["artist"], meta["album"])

                if album_key not in album_cache:
                    row = conn.execute(
                        "SELECT id, year, cover_path FROM albums "
                        "WHERE artist=? AND album_name=?",
                        album_key,
                    ).fetchone()

                    if row:
                        album_id = int(row[0])
                        old_year = row[1]
                        old_cover_path = str(row[2]) if row[2] else None
                        if old_cover_path and not Path(old_cover_path).is_file():
                            conn.execute(
                                "UPDATE albums SET cover_path=NULL WHERE id=?",
                                (album_id,),
                            )
                            old_cover_path = None
                        if old_year is None and meta["year"]:
                            conn.execute(
                                "UPDATE albums SET year=? WHERE id=?",
                                (meta["year"], album_id),
                            )
                    else:
                        conn.execute(
                            "INSERT OR IGNORE INTO albums "
                            "(artist, album_name, year, cover_path) VALUES (?, ?, ?, NULL)",
                            (meta["artist"], meta["album"], meta["year"]),
                        )
                        row = conn.execute(
                            "SELECT id, year, cover_path FROM albums "
                            "WHERE artist=? AND album_name=?",
                            album_key,
                        ).fetchone()
                        if not row:
                            failed += 1
                            continue
                        album_id = int(row[0])
                        old_cover_path = str(row[2]) if row[2] else None

                    album_cache[album_key] = album_id
                    album_cover_cache[album_key] = old_cover_path

                album_id = album_cache[album_key]

                # Читаем обложку только если у альбома ещё нет действующего файла.
                # При наличии готовой обложки не копируем APIC/COVR с каждого трека.
                if not album_cover_cache.get(album_key):
                    folder_key = self._path_key(path.parent)
                    if folder_key not in folder_cover_cache:
                        folder_bytes = find_folder_cover_bytes(path.parent)
                        folder_cover_cache[folder_key] = (
                            save_cover_bytes(folder_bytes) if folder_bytes else None
                        )

                    cover_path = folder_cover_cache.get(folder_key)
                    if not cover_path:
                        # The generic easy-tag view may hide format-specific picture
                        # structures; reopen only when a cover is actually needed.
                        audio_for_cover = (
                            meta.get("_audio")
                            if path.suffix.lower() in {".mp3", ".m4a"}
                            else None
                        )
                        cover_bytes = extract_embedded_cover(
                            str(path), audio_for_cover
                        )
                        if cover_bytes:
                            cover_path = save_cover_bytes(cover_bytes)

                    if cover_path:
                        album_cover_cache[album_key] = cover_path
                        conn.execute(
                            "UPDATE albums SET cover_path=? WHERE id=?",
                            (cover_path, album_id),
                        )

                conn.execute(
                    """
                    INSERT INTO tracks
                    (album_id, track_name, genre, file_path, relative_path,
                     duration_ms, file_size, mtime, favorite)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                    ON CONFLICT(file_path) DO UPDATE SET
                        album_id=excluded.album_id,
                        track_name=excluded.track_name,
                        genre=excluded.genre,
                        relative_path=excluded.relative_path,
                        duration_ms=excluded.duration_ms,
                        file_size=excluded.file_size,
                        mtime=excluded.mtime
                    """,
                    (
                        album_id,
                        meta["title"],
                        meta["genre"],
                        str(resolved_path),
                        relative_path_for(path, self.folder),
                        meta["duration_ms"],
                        int(stat.st_size),
                        float(stat.st_mtime),
                    ),
                )

                successes += 1
                if index == 1 or index % 40 == 0 or index == total:
                    self.signals.progress.emit(index, total, meta["title"])

            # Удаляем только действительно исчезнувшие записи текущей папки.
            # При другой папке это уже была транзакционная полная перестройка.
            if same_library:
                for key, old_row in existing_by_path.items():
                    if key not in current_path_keys:
                        conn.execute(
                            "DELETE FROM tracks WHERE file_path=?",
                            (old_row["file_path"],),
                        )

            conn.execute(
                "DELETE FROM albums WHERE NOT EXISTS "
                "(SELECT 1 FROM tracks WHERE tracks.album_id=albums.id)"
            )
            conn.commit()
            self.signals.finished.emit(successes, failed)

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


class CoverRepairSignals(QObject):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)


class CoverRepairTask(QRunnable):
    """Восстанавливает отсутствующие обложки старых БД без полного пересканирования."""

    def __init__(self, folder: Path):
        super().__init__()
        self.folder = folder.resolve()
        self.signals = CoverRepairSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        conn = None
        try:
            conn = db_connect()
            albums = conn.execute("SELECT id, cover_path FROM albums ORDER BY id").fetchall()
            repaired = 0
            total = len(albums)

            for index, (album_id, cover_path) in enumerate(albums, start=1):
                if cover_path and Path(str(cover_path)).is_file():
                    if index == 1 or index % 30 == 0 or index == total:
                        self.signals.progress.emit(index, total)
                    continue

                track_rows = conn.execute(
                    "SELECT file_path, relative_path FROM tracks WHERE album_id=? ORDER BY id LIMIT 12",
                    (album_id,),
                ).fetchall()

                found = None
                for old_path, relative in track_rows:
                    if relative:
                        candidate = self.folder / Path(relative)
                    else:
                        candidate = Path(old_path)
                    if not candidate.is_file():
                        continue

                    # Обложка рядом с папкой альбома имеет приоритет.
                    found = find_folder_cover_bytes(candidate.parent)
                    if found is not None:
                        break

                    # Иначе смотрим в несколько первых файлов альбома.
                    found = extract_embedded_cover(str(candidate))
                    if found is not None:
                        break

                if found is not None:
                    saved = save_cover_bytes(found)
                    if saved:
                        conn.execute("UPDATE albums SET cover_path=? WHERE id=?", (saved, album_id))
                        repaired += 1

                if index == 1 or index % 30 == 0 or index == total:
                    self.signals.progress.emit(index, total)

            conn.commit()
            self.signals.finished.emit(repaired)
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            if conn:
                conn.close()


# ============================================================================
# DB-ВЫБОРКИ
# ============================================================================


def _track_from_row(row) -> dict:
    (
        tid,
        title,
        genre,
        file_path,
        relative_path,
        duration,
        file_size,
        mtime,
        favorite,
        artist,
        album,
        year,
        cover_path,
        listened_ms,
    ) = row
    return {
        "id": int(tid),
        "track_name": safe_text(title, Path(file_path).stem if file_path else "Неизвестный трек"),
        "genre": safe_text(genre, "Разное"),
        "file_path": file_path or "",
        "relative_path": relative_path or "",
        "duration_ms": int(duration or 0),
        "file_size": int(file_size or 0),
        "mtime": float(mtime or 0),
        "favorite": bool(favorite),
        "artist": safe_text(artist, "Неизвестный исполнитель"),
        "album": safe_text(album, "Без названия"),
        "year": year,
        "cover_path": cover_path or "",
        "listened_ms": int(listened_ms or 0),
    }


def _base_track_sql() -> str:
    return """
        SELECT
            t.id,
            t.track_name,
            t.genre,
            t.file_path,
            t.relative_path,
            t.duration_ms,
            t.file_size,
            t.mtime,
            t.favorite,
            a.artist,
            a.album_name,
            a.year,
            a.cover_path,
            COALESCE((
                SELECT SUM(ls.listened_ms)
                FROM listening_sessions ls
                WHERE ls.track_key=t.file_path
            ), 0) AS listened_ms
        FROM tracks t
        JOIN albums a ON a.id=t.album_id
    """


def fetch_all_tracks(
    music_folder: Path,
    sort_mode: str = "Название",
    reverse: bool = False,
    genre: str = "Все жанры",
) -> list[dict]:
    order_map = {
        "Название": "t.track_name COLLATE NOCASE, a.artist COLLATE NOCASE",
        "Исполнитель": "a.artist COLLATE NOCASE, t.track_name COLLATE NOCASE",
        "Альбом": "a.album_name COLLATE NOCASE, t.track_name COLLATE NOCASE",
        "Жанр": "t.genre COLLATE NOCASE, t.track_name COLLATE NOCASE",
        "Год": "a.year IS NULL, a.year, t.track_name COLLATE NOCASE",
        "Длительность": "t.duration_ms, t.track_name COLLATE NOCASE",
        "Размер файла": "t.file_size, t.track_name COLLATE NOCASE",
        "Изменение файла": "t.mtime, t.track_name COLLATE NOCASE",
        "Время прослушивания": "COALESCE((SELECT SUM(ls.listened_ms) FROM listening_sessions ls WHERE ls.track_key=t.file_path), 0), t.track_name COLLATE NOCASE",
    }
    direction = "DESC" if reverse else "ASC"
    order = order_map.get(sort_mode, order_map["Название"])
    conn = db_connect()
    try:
        sql = _base_track_sql()
        args: list[object] = []
        if genre != "Все жанры":
            sql += " WHERE t.genre = ?"
            args.append(genre)
        if sort_mode == "Время прослушивания":
            primary_order = order.split(", t.track_name COLLATE NOCASE", 1)[0]
            sql += f" ORDER BY {primary_order} {direction}, t.track_name COLLATE NOCASE ASC"
        else:
            sql += f" ORDER BY {order} {direction}"
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    return [_track_from_row(row) for row in rows]


def search_tracks(
    query: str,
    genre: str,
    sort_mode: str,
    reverse: bool,
    music_folder: Path,
) -> list[dict]:
    order_map = {
        "Название": "t.track_name COLLATE NOCASE",
        "Исполнитель": "a.artist COLLATE NOCASE, t.track_name COLLATE NOCASE",
        "Альбом": "a.album_name COLLATE NOCASE, t.track_name COLLATE NOCASE",
        "Жанр": "t.genre COLLATE NOCASE, t.track_name COLLATE NOCASE",
        "Год": "a.year IS NULL, a.year, t.track_name COLLATE NOCASE",
        "Длительность": "t.duration_ms, t.track_name COLLATE NOCASE",
        "Размер файла": "t.file_size, t.track_name COLLATE NOCASE",
        "Изменение файла": "t.mtime, t.track_name COLLATE NOCASE",
        "Время прослушивания": "COALESCE((SELECT SUM(ls.listened_ms) FROM listening_sessions ls WHERE ls.track_key=t.file_path), 0), t.track_name COLLATE NOCASE",
    }
    direction = "DESC" if reverse else "ASC"
    order = order_map.get(sort_mode, order_map["Название"])
    pattern = f"%{query.strip()}%"
    sql = _base_track_sql()
    conditions: list[str] = []
    args: list[object] = []

    if query.strip():
        conditions.append(
            "(CASEFOLD(t.track_name) LIKE CASEFOLD(?) "
            "OR CASEFOLD(a.artist) LIKE CASEFOLD(?) "
            "OR CASEFOLD(a.album_name) LIKE CASEFOLD(?) "
            "OR CASEFOLD(t.genre) LIKE CASEFOLD(?) "
            "OR CASEFOLD(t.file_path) LIKE CASEFOLD(?))"
        )
        args.extend([pattern] * 5)
    if genre != "Все жанры":
        conditions.append("t.genre=?")
        args.append(genre)

    conn = db_connect()
    try:
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        if sort_mode == "Время прослушивания":
            primary_order = order.split(", t.track_name COLLATE NOCASE", 1)[0]
            sql += f" ORDER BY {primary_order} {direction}, t.track_name COLLATE NOCASE ASC"
        else:
            sql += f" ORDER BY {order} {direction}"
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    return [_track_from_row(row) for row in rows]


def fetch_genres() -> list[str]:
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT genre FROM tracks "
            "WHERE genre IS NOT NULL AND TRIM(genre)<>'' "
            "ORDER BY genre COLLATE NOCASE"
        ).fetchall()
    finally:
        conn.close()
    return [safe_text(row[0], "Разное") for row in rows]


def fetch_albums(sort_mode: str = "Количество треков", reverse: bool = True) -> list[dict]:
    order_map = {
        "Исполнитель": "a.artist COLLATE NOCASE, a.album_name COLLATE NOCASE",
        "Название": "a.album_name COLLATE NOCASE, a.artist COLLATE NOCASE",
        "Год": "a.year IS NULL, a.year, a.album_name COLLATE NOCASE",
        "Количество треков": "COUNT(t.id), a.album_name COLLATE NOCASE",
    }
    direction = "DESC" if reverse else "ASC"
    order = order_map.get(sort_mode, order_map["Исполнитель"])
    conn = db_connect()
    try:
        rows = conn.execute(
            f"""
            SELECT
                a.id,
                a.artist,
                a.album_name,
                a.year,
                a.cover_path,
                COUNT(t.id) AS track_count,
                (
                    SELECT COALESCE(SUM(ls.listened_ms), 0)
                    FROM listening_sessions ls
                    WHERE ls.artist=a.artist AND ls.album=a.album_name
                ) AS listened_ms
            FROM albums a
            LEFT JOIN tracks t ON t.album_id=a.id
            GROUP BY a.id
            ORDER BY {order} {direction}
            """
        ).fetchall()
    finally:
        conn.close()
    if sort_mode == "Количество треков":
        # Python-side tie-break makes the desired default deterministic across
        # SQLite versions and avoids alias-resolution differences in ORDER BY.
        rows.sort(
            key=lambda row: (
                -int(row[5] or 0) if reverse else int(row[5] or 0),
                safe_text(row[2], "Без названия").casefold(),
            )
        )
    return [
        {
            "id": int(row[0]),
            "artist": safe_text(row[1], "Неизвестный исполнитель"),
            "album": safe_text(row[2], "Без названия"),
            "year": row[3],
            "cover_path": row[4] or "",
            "track_count": int(row[5] or 0),
            "listened_ms": int(row[6] or 0),
        }
        for row in rows
    ]


def fetch_album_info(album_id: int) -> Optional[dict]:
    conn = db_connect()
    try:
        row = conn.execute(
            """SELECT id, artist, album_name, year, cover_path,
                      (SELECT COALESCE(SUM(ls.listened_ms), 0)
                       FROM listening_sessions ls
                       WHERE ls.artist=albums.artist AND ls.album=albums.album_name)
               FROM albums WHERE id=?""",
            (album_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "id": int(row[0]),
        "artist": safe_text(row[1], "Неизвестный исполнитель"),
        "album": safe_text(row[2], "Без названия"),
        "year": row[3],
        "cover_path": row[4] or "",
        "listened_ms": int(row[5] or 0),
    }


def fetch_album_tracks(album_id: int) -> list[dict]:
    conn = db_connect()
    try:
        rows = conn.execute(
            _base_track_sql() + " WHERE a.id=? ORDER BY t.id ASC",
            (album_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_track_from_row(row) for row in rows]


def set_favorite(track_id: int, value: bool) -> None:
    conn = db_connect()
    try:
        conn.execute("UPDATE tracks SET favorite=? WHERE id=?", (1 if value else 0, track_id))
        conn.commit()
    finally:
        conn.close()


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ВИДЖЕТЫ
# ============================================================================


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", color: str = TEXT, parent=None):
        super().__init__(parent)
        self._full_text = text or ""
        self._color = color
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._apply_color()
        self._refresh()

    def setText(self, text: str) -> None:
        text = text or ""
        if text == self._full_text:
            return
        self._full_text = text
        self._refresh()

    def full_text(self) -> str:
        return self._full_text

    def set_color(self, color: str) -> None:
        if color != self._color:
            self._color = color
            self._apply_color()
        self._refresh()

    def _apply_color(self) -> None:
        # Repolishing a stylesheet on every resize can trigger costly style
        # invalidations during hover/layout changes. Update it only on color changes.
        self.setStyleSheet(f"color: {self._color};")

    def setFont(self, font: QFont) -> None:
        super().setFont(font)
        self._refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        metrics = QFontMetrics(self.font())
        text = metrics.elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            max(0, self.width()),
        )
        if self.text() != text:
            super().setText(text)
        tooltip = self._full_text if text != self._full_text else ""
        if self.toolTip() != tooltip:
            self.setToolTip(tooltip)


class IconButton(QToolButton):
    def __init__(
        self,
        text: str,
        size: int = 40,
        tooltip: str = "",
        checkable: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setText(text)
        self.setFixedSize(size, size)
        self.setObjectName("IconButton")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip(tooltip)
        self.setCheckable(checkable)
        self.setFont(QFont("Segoe UI Symbol", 16))


# ============================================================================
# САЙДБАР
# ============================================================================


class Sidebar(QFrame):
    home_clicked = pyqtSignal()
    collection_clicked = pyqtSignal()
    history_clicked = pyqtSignal()
    search_clicked = pyqtSignal()
    folder_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 20, 18, 18)
        root.setSpacing(5)

        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(5, 0, 0, 0)
        logo = QLabel("✦")
        logo.setObjectName("LogoMark")
        logo.setFixedWidth(34)
        logo_row.addWidget(logo)
        logo_text = QLabel("LOCAL MUSIC")
        logo_text.setObjectName("LogoText")
        logo_row.addWidget(logo_text)
        logo_row.addStretch()
        root.addLayout(logo_row)

        sub = QLabel("Локальная библиотека")
        sub.setObjectName("SidebarSub")
        sub.setContentsMargins(40, 0, 0, 0)
        root.addWidget(sub)
        root.addSpacing(24)

        home = self._make_button("⌂", "Главная")
        collection = self._make_button("▦", "Альбомы")
        history = self._make_button("◷", "История")
        search = self._make_button("⌕", "Поиск")
        self.btn_home = home
        self.btn_collection = collection
        self.btn_history = history
        self.btn_search = search
        root.addWidget(home)
        root.addWidget(collection)
        root.addWidget(history)
        root.addWidget(search)

        root.addSpacing(18)
        sep_label = QLabel("БИБЛИОТЕКА")
        sep_label.setObjectName("SidebarCaption")
        root.addWidget(sep_label)

        folder = self._make_button("▣", "Папка музыки")
        folder.setCheckable(False)
        folder.setAutoExclusive(False)
        self.btn_folder = folder
        root.addWidget(folder)

        self.path_label = QLabel("Папка не выбрана")
        self.path_label.setObjectName("SidebarPath")
        self.path_label.setWordWrap(True)
        self.path_label.setToolTip("")
        root.addWidget(self.path_label)

        root.addStretch(1)

        status = QLabel("●  offline · локальный режим")
        status.setObjectName("SidebarStatus")
        root.addWidget(status)

        self.btn_home.clicked.connect(self.home_clicked.emit)
        self.btn_collection.clicked.connect(self.collection_clicked.emit)
        self.btn_history.clicked.connect(self.history_clicked.emit)
        self.btn_search.clicked.connect(self.search_clicked.emit)
        self.btn_folder.clicked.connect(self.folder_clicked.emit)

    def _make_button(self, icon: str, title: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("SideButton")
        button.setText(f"{icon}   {title}")
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setMinimumHeight(46)
        button.setFont(QFont("Segoe UI", 12))
        return button

    def set_active(self, button: Optional[QToolButton]) -> None:
        for candidate in (self.btn_home, self.btn_collection, self.btn_history, self.btn_search):
            candidate.blockSignals(True)
            candidate.setChecked(candidate is button)
            candidate.blockSignals(False)

    def set_music_path(self, path: Optional[Path]) -> None:
        text = str(path) if path else "Папка не выбрана"
        self.path_label.setText(text)
        self.path_label.setToolTip(text)


# ============================================================================
# ПОИСК В ВЕРХНЕЙ ПАНЕЛИ
# ============================================================================


class SearchHeader(QWidget):
    submitted = pyqtSignal(str)
    text_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 8)
        root.setSpacing(12)

        self.icon = QLabel("⌕")
        self.icon.setObjectName("SearchIcon")
        self.icon.setFixedWidth(24)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.icon)

        self.edit = QLineEdit()
        self.edit.setObjectName("SearchField")
        self.edit.setPlaceholderText("Поиск по трекам, исполнителям, альбомам и жанрам")
        self.edit.setClearButtonEnabled(True)
        self.edit.setFixedHeight(46)
        root.addWidget(self.edit, 1)

        self.status = QLabel("")
        self.status.setObjectName("HeaderStatus")
        root.addWidget(self.status)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(220)
        self._timer.timeout.connect(lambda: self.submitted.emit(self.edit.text().strip()))
        self.edit.textChanged.connect(self._on_changed)
        self.edit.returnPressed.connect(lambda: self.submitted.emit(self.edit.text().strip()))

    def _on_changed(self, text: str) -> None:
        self.text_changed.emit(text)
        self._timer.start()

    def clear(self) -> None:
        self.edit.clear()


# ============================================================================
# СОРТИРОВКА
# ============================================================================


class SortBar(QWidget):
    changed = pyqtSignal(str, bool)

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        self.combo = QComboBox()
        self.combo.setObjectName("Combo")
        self.combo.addItems(items)
        self.combo.setMinimumWidth(138)
        root.addWidget(self.combo)

        self.direction = IconButton("↑", 38, "По возрастанию / по убыванию", True)
        root.addWidget(self.direction)

        self.combo.currentTextChanged.connect(self._emit)
        self.direction.toggled.connect(self._emit)

    def _emit(self) -> None:
        self.direction.setText("↓" if self.direction.isChecked() else "↑")
        self.changed.emit(self.combo.currentText(), self.direction.isChecked())

    def current(self) -> tuple[str, bool]:
        return self.combo.currentText(), self.direction.isChecked()


# ============================================================================
# СТРОКА ТРЕКА
# ============================================================================


class TrackRow(QWidget):
    clicked = pyqtSignal(int)

    def __init__(self, index: int, track: dict, music_folder: Path, parent=None):
        super().__init__(parent)
        self.index = index
        self.track = track
        self.music_folder = music_folder
        self.playing = False
        self.cover_requested = False
        self.setObjectName("TrackRow")
        self.setProperty("playing", False)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(TRACK_ROW_HEIGHT)

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 6, 16, 6)
        root.setSpacing(12)

        self.num = QLabel(str(index + 1))
        self.num.setObjectName("RowNum")
        self.num.setFixedWidth(28)
        self.num.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(self.num)

        self.cover = AsyncCover(46, 8)
        root.addWidget(self.cover)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 2, 0, 2)
        text_col.setSpacing(2)
        title = ElidedLabel(safe_text(track.get("track_name"), "Неизвестный трек"))
        title_font = QFont("Segoe UI", 10)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)
        self.title = title
        text_col.addWidget(title)

        artist = ElidedLabel(safe_text(track.get("artist"), "Неизвестный исполнитель"), MUTED)
        artist.setFont(QFont("Segoe UI", 9))
        self.artist = artist
        text_col.addWidget(artist)
        root.addLayout(text_col, 1)

        self.album = ElidedLabel(safe_text(track.get("album"), "—"), DIM)
        self.album.setFont(QFont("Segoe UI", 9))
        self.album.setFixedWidth(250)
        root.addWidget(self.album)

        self.duration = QLabel(format_ms(track.get("duration_ms")))
        self.duration.setObjectName("RowDuration")
        self.duration.setFixedWidth(70)
        self.duration.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(self.duration)

        # Compact metadata: title, artist, album and duration only.
        for child in (
            self.num,
            self.cover,
            self.title,
            self.artist,
            self.album,
            self.duration,
        ):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def _resolved_cover(self) -> str:
        cover = self.track.get("cover_path") or ""
        return cover if Path(cover).is_file() else ""

    def ensure_cover(self) -> None:
        if self.cover_requested:
            return
        self.cover_requested = True
        self.cover.load(self._resolved_cover())

    def set_playing(self, state: bool) -> None:
        state = bool(state)
        if self.playing == state:
            return
        self.playing = state
        self.setProperty("playing", state)
        self.num.setText("▶" if state else str(self.index + 1))
        self.title.set_color(ACCENT if state else TEXT)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def contextMenuEvent(self, event) -> None:
        path = resolve_track_path(self.track, self.music_folder)
        menu = QMenu(self)
        play = menu.addAction("▶  Играть")
        menu.addSeparator()
        copy_path = menu.addAction("Копировать путь")
        open_folder = menu.addAction("Открыть папку")
        menu.addSeparator()
        chosen = menu.exec(event.globalPos())
        if chosen == play:
            self.clicked.emit(self.index)
        elif chosen == copy_path:
            QApplication.clipboard().setText(path)
        elif chosen == open_folder:
            from PyQt6.QtGui import QDesktopServices
            folder = str(Path(path).parent) if path else ""
            if folder:
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        super().contextMenuEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mouseDoubleClickEvent(event)


class TrackList(QWidget):
    track_clicked = pyqtSignal(int)

    def __init__(self, music_folder: Path, parent=None):
        super().__init__(parent)
        self.music_folder = music_folder
        self.tracks: list[dict] = []
        self.rows: list[TrackRow] = []
        self.loaded = 0
        self.playing_path = ""
        self.batch = TRACK_BATCH

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)
        self.container = root
        self.container.addStretch(1)

    def set_tracks(self, tracks: list[dict]) -> None:
        self.tracks = list(tracks or [])
        self._clear_rows()
        self.loaded = 0
        self._append_batch()

    def _clear_rows(self) -> None:
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows.clear()

    def _append_batch(self) -> None:
        if self.loaded >= len(self.tracks):
            return
        end = min(len(self.tracks), self.loaded + self.batch)
        for index in range(self.loaded, end):
            row = TrackRow(index, self.tracks[index], self.music_folder, self)
            row.clicked.connect(self.track_clicked.emit)
            if self.playing_path:
                row.set_playing(self.tracks[index].get("file_path") == self.playing_path)
            self.container.insertWidget(self.container.count() - 1, row)
            self.rows.append(row)
        self.loaded = end

    def maybe_load_more(self, scroll_value: int, maximum: int) -> None:
        if self.loaded >= len(self.tracks):
            return
        if maximum <= 0 or maximum - scroll_value < 900:
            self._append_batch()

    def load_visible_covers(self, scroll_value: int, viewport_height: int) -> None:
        if not self.rows:
            return
        first = max(0, scroll_value // max(1, TRACK_ROW_HEIGHT) - 4)
        count = viewport_height // TRACK_ROW_HEIGHT + 10
        last = min(len(self.rows), first + count)
        for row in self.rows[first:last]:
            row.ensure_cover()

    def set_playing_path(self, path: Optional[str]) -> None:
        self.playing_path = path or ""
        for row in self.rows:
            row.set_playing(bool(self.playing_path and row.track.get("file_path") == self.playing_path))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, lambda: self.load_visible_covers(0, self.height()))


# ============================================================================
# КАРТОЧКА АЛЬБОМА
# ============================================================================


class AlbumCard(QFrame):
    clicked = pyqtSignal(int)
    play_requested = pyqtSignal(int)

    def __init__(self, album: dict, parent=None):
        super().__init__(parent)
        self.album_id = int(album["id"])
        self.setObjectName("AlbumCard")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(ALBUM_CARD_WIDTH, ALBUM_CARD_HEIGHT)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 12)
        root.setSpacing(8)

        cover_wrap = QWidget()
        cover_wrap.setFixedSize(ALBUM_COVER_SIZE, ALBUM_COVER_SIZE)
        cover_wrap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cover_layout = QVBoxLayout(cover_wrap)
        cover_layout.setContentsMargins(0, 0, 0, 0)
        self.cover = AsyncCover(ALBUM_COVER_SIZE, 14, cover_wrap)
        cover_layout.addWidget(self.cover)

        self.play_button = IconButton("▶", 48, "Играть альбом", parent=cover_wrap)
        self.play_button.setObjectName("AlbumPlayButton")
        self.play_button.setVisible(False)
        self.play_button.move(ALBUM_COVER_SIZE - 58, ALBUM_COVER_SIZE - 58)
        self.play_button.clicked.connect(lambda: self.play_requested.emit(self.album_id))
        root.addWidget(cover_wrap, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.cover_wrap = cover_wrap

        self.title = ElidedLabel(safe_text(album.get("album"), "Без названия"))
        title_font = QFont("Segoe UI", 10)
        title_font.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(title_font)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(self.title)

        artist = safe_text(album.get("artist"), "Неизвестный исполнитель")
        year = album.get("year")
        count = int(album.get("track_count") or 0)
        suffix = []
        if year:
            suffix.append(str(year))
        suffix.append(f"{count} тр." if count else "нет треков")
        listened_ms = int(album.get("listened_ms") or 0)
        if listened_ms > 0:
            suffix.append(f"слушали {format_listening_ms(listened_ms)}")
        self.subtitle = ElidedLabel(f"{artist}  •  " + "  •  ".join(suffix), MUTED)
        self.subtitle.setFont(QFont("Segoe UI", 9))
        self.subtitle.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(self.subtitle)

        self.cover_path = album.get("cover_path") or ""
        self.cover_requested = False

    def ensure_cover(self) -> None:
        if self.cover_requested:
            return
        self.cover_requested = True
        path = self.cover_path if Path(self.cover_path).is_file() else ""
        self.cover.load(path)

    def set_play_visible(self, visible: bool) -> None:
        self.play_button.setVisible(bool(visible))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.album_id)
        super().mousePressEvent(event)

    def enterEvent(self, event) -> None:
        self.play_button.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        # Child widgets can briefly receive the pointer while the user moves
        # over the artwork. Defer hiding until the pointer is confirmed outside
        # the whole card; this prevents enter/leave churn and visible flicker.
        QTimer.singleShot(45, self._sync_hover_controls)
        super().leaveEvent(event)

    def _sync_hover_controls(self) -> None:
        pointer = self.mapFromGlobal(QCursor.pos())
        self.set_play_visible(self.isVisible() and self.rect().contains(pointer))


# ============================================================================
# БЛОК «СЕЙЧАС ИГРАЕТ»
# ============================================================================


class NowPlayingHero(QFrame):
    open_album_requested = pyqtSignal()
    open_folder_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NowPlayingHero")
        self.setMinimumHeight(222)
        self.setMaximumHeight(222)

        root = QHBoxLayout(self)
        root.setContentsMargins(24, 18, 26, 18)
        root.setSpacing(24)

        self.cover = AsyncCover(186, 16)
        root.addWidget(self.cover, alignment=Qt.AlignmentFlag.AlignVCenter)

        info = QVBoxLayout()
        info.setContentsMargins(0, 6, 0, 6)
        info.setSpacing(5)

        self.caption = QLabel("ВЫБРАННЫЙ ТРЕК")
        self.caption.setObjectName("AccentCaption")
        self._is_playing = False
        info.addWidget(self.caption)

        self.title = QLabel("Ничего не играет")
        self.title.setObjectName("HeroTitle")
        self.title.setWordWrap(True)
        info.addWidget(self.title)

        self.artist = QLabel("—")
        self.artist.setObjectName("HeroArtist")
        info.addWidget(self.artist)

        self.album = QLabel("Альбом: —")
        self.album.setObjectName("HeroMeta")
        info.addWidget(self.album)

        self.meta = QLabel("Жанр: —  •  Год: —  •  Длительность: —")
        self.meta.setObjectName("HeroMetaSecondary")
        info.addWidget(self.meta)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.open_album = QPushButton("Открыть альбом")
        self.open_album.setObjectName("GhostButton")
        self.open_album.clicked.connect(self.open_album_requested.emit)
        path_row.addWidget(self.open_album)
        self.open_folder = QPushButton("Открыть папку")
        self.open_folder.setObjectName("GhostButton")
        self.open_folder.clicked.connect(self.open_folder_requested.emit)
        path_row.addWidget(self.open_folder)
        path_row.addStretch()
        info.addLayout(path_row)
        info.addStretch()

        root.addLayout(info, 1)

    def set_playing(self, playing: bool) -> None:
        playing = bool(playing)
        if self._is_playing == playing:
            return
        self._is_playing = playing
        self.caption.setText("СЕЙЧАС ИГРАЕТ" if playing else "ВЫБРАННЫЙ ТРЕК")

    def set_track(self, track: Optional[dict]) -> None:
        if not track:
            self.set_playing(False)
            self.title.setText("Ничего не играет")
            self.artist.setText("—")
            self.album.setText("Альбом: —")
            self.meta.setText("Жанр: —  •  Год: —  •  Длительность: —")
            self.cover.reset()
            return
        self.title.setText(safe_text(track.get("track_name"), "Неизвестный трек"))
        self.artist.setText(safe_text(track.get("artist"), "Неизвестный исполнитель"))
        self.album.setText(f"Альбом: {safe_text(track.get('album'), '—')}")
        self.meta.setText(
            f"Жанр: {safe_text(track.get('genre'), 'Разное')}  •  "
            f"Год: {track.get('year') or '—'}  •  "
            f"Длительность: {format_ms(track.get('duration_ms'))}"
        )
        cover = track.get("cover_path") or ""
        self.cover.load(cover if Path(cover).is_file() else "")


# ============================================================================
# ДОМАШНЯЯ СТРАНИЦА
# ============================================================================


class AlbumQuerySignals(QObject):
    finished = pyqtSignal(int, object, str)


class AlbumQueryTask(QRunnable):
    """Loads album summaries from SQLite without blocking the Qt UI thread."""

    def __init__(self, generation: int, sort_mode: str, reverse: bool):
        super().__init__()
        self.generation = generation
        self.sort_mode = sort_mode
        self.reverse = reverse
        self.signals = AlbumQuerySignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            albums = fetch_albums(self.sort_mode, self.reverse)
            self.signals.finished.emit(self.generation, albums, "")
        except Exception as exc:
            self.signals.finished.emit(self.generation, [], str(exc))


class HomePage(QWidget):
    album_selected = pyqtSignal(int)
    album_play_requested = pyqtSignal(int)
    play_all = pyqtSignal()
    shuffle_all = pyqtSignal()
    sort_changed = pyqtSignal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._albums: list[dict] = []
        self.cards: list[AlbumCard] = []
        self._loaded_once = False
        self._created = 0
        self._columns = 1
        self._query_generation = 0
        self._query_in_progress = False
        self._query_task = None
        self._sort_mode = "Количество треков"
        self._reverse = True
        self._cover_update_timer = QTimer(self)
        self._cover_update_timer.setSingleShot(True)
        self._cover_update_timer.setInterval(24)
        self._cover_update_timer.timeout.connect(self._update_visible_covers)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 8, 28, 22)
        root.setSpacing(15)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Альбомы")
        title.setObjectName("PageTitle")
        title_box.addWidget(title)
        self.count = QLabel("Загрузка альбомов…")
        self.count.setObjectName("MutedText")
        title_box.addWidget(self.count)
        heading.addLayout(title_box)
        heading.addStretch()

        self.sort = SortBar(["Исполнитель", "Название", "Год", "Количество треков"])
        self.sort.combo.blockSignals(True)
        self.sort.direction.blockSignals(True)
        self.sort.combo.setCurrentText("Количество треков")
        self.sort.direction.setChecked(True)
        self.sort.direction.setText("↓")
        self.sort.direction.blockSignals(False)
        self.sort.combo.blockSignals(False)
        self.sort.changed.connect(self.sort_changed.emit)
        heading.addWidget(self.sort)

        shuffle = IconButton("🔀", 40, "Перемешать всю библиотеку")
        shuffle.clicked.connect(self.shuffle_all.emit)
        heading.addWidget(shuffle)

        listen = QPushButton("▶  Слушать всё")
        listen.setObjectName("AccentButton")
        listen.clicked.connect(self.play_all.emit)
        heading.addWidget(listen)
        root.addLayout(heading)

        self.now = NowPlayingHero()
        self.now.setVisible(False)
        root.addWidget(self.now)

        section = QLabel("Альбомы")
        section.setObjectName("SectionTitle")
        root.addWidget(section)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("ScrollArea")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setContentsMargins(4, 4, 4, 20)
        self.grid.setHorizontalSpacing(20)
        self.grid.setVerticalSpacing(20)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.scroll.setWidget(self.grid_widget)
        root.addWidget(self.scroll, 1)

        self.scroll.verticalScrollBar().valueChanged.connect(
            lambda _v: self._cover_update_timer.start()
        )
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.setInterval(160)
        self._relayout_timer.timeout.connect(self._relayout)

    def load_albums(self, mode="Количество треков", reverse=True) -> None:
        self._loaded_once = True
        self._sort_mode = mode
        self._reverse = bool(reverse)
        self._query_generation += 1
        generation = self._query_generation
        self._query_in_progress = True
        self.count.setText("Загружаем альбомы…")

        # SQLite aggregation is now off the UI thread. Stale query results are
        # discarded when the user changes sorting or a newer refresh is requested.
        task = AlbumQueryTask(generation, mode, self._reverse)
        self._query_task = task
        task.signals.finished.connect(self._albums_loaded)
        QThreadPool.globalInstance().start(task)

    def _albums_loaded(self, generation: int, albums: list, error: str) -> None:
        if generation != self._query_generation:
            return

        self._query_in_progress = False
        self._query_task = None
        if error:
            self.count.setText("Не удалось загрузить библиотеку")
            self.count.setToolTip(error)
            return

        self.count.setToolTip("")
        self._render_albums(albums)

    def _render_albums(self, albums: list[dict]) -> None:
        self._albums = list(albums)
        self.count.setText(f"{len(self._albums)} альбомов")

        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards.clear()
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        width = max(750, self.scroll.viewport().width())
        self._columns = max(2, min(8, (width - 24) // (ALBUM_CARD_WIDTH + 20)))
        self._created = 0

        # Reserve the full scroll height without creating a QWidget for every
        # album. Cards are materialized only near the viewport as the user scrolls.
        row_step = ALBUM_CARD_HEIGHT + self.grid.verticalSpacing()
        row_count = (len(self._albums) + self._columns - 1) // self._columns
        margins = self.grid.contentsMargins()
        self.grid_widget.setMinimumHeight(
            max(1, row_count * row_step + margins.top() + margins.bottom())
        )
        self._append_album_batch()
        QTimer.singleShot(0, self._update_visible_covers)

    def _append_album_batch(self) -> None:
        if self._created >= len(self._albums):
            return

        end = min(len(self._albums), self._created + ALBUM_BATCH)
        for pos in range(self._created, end):
            card = AlbumCard(self._albums[pos])
            card.clicked.connect(self.album_selected.emit)
            card.play_requested.connect(self.album_play_requested.emit)
            self.grid.addWidget(card, pos // self._columns, pos % self._columns)
            self.cards.append(card)
        self._created = end

    def _update_visible_covers(self) -> None:
        if not self._albums:
            return

        bar = self.scroll.verticalScrollBar()
        value = max(0, bar.value())
        viewport_height = max(1, self.scroll.viewport().height())
        row_step = max(1, ALBUM_CARD_HEIGHT + self.grid.verticalSpacing())

        first_row = max(0, value // row_step - 2)
        last_row = (value + viewport_height) // row_step + 3
        # Include a few rows ahead so scrolling does not reveal blank cards.
        needed_end = min(len(self._albums), (last_row + 1) * self._columns)

        if self._created < needed_end:
            # Add one batch per event-loop turn. Dragging the scrollbar far down
            # should not synchronously instantiate thousands of widgets at once.
            before = self._created
            self._append_album_batch()
            if self._created < needed_end and self._created > before:
                self._cover_update_timer.start(0)
                return
            if self._created == before:
                return

        first_index = first_row * self._columns
        last_index = min(self._created, needed_end)
        for card in self.cards[first_index:last_index]:
            card.ensure_cover()

    def set_now_playing(
        self,
        track: Optional[dict],
        playing: Optional[bool] = None,
    ) -> None:
        self.now.set_track(track)
        if playing is not None:
            self.now.set_playing(playing)
        self.now.setVisible(track is not None)

    def _relayout(self) -> None:
        if not self._loaded_once:
            return
        width = max(750, self.scroll.viewport().width())
        columns = max(2, min(8, (width - 24) // (ALBUM_CARD_WIDTH + 20)))
        if columns != self._columns:
            if self._query_in_progress:
                # The upcoming query result will be rendered using the new width.
                return
            # Resizing changes layout, not the album data; avoid another SQLite query.
            self._render_albums(self._albums)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout_timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded_once:
            mode, reverse = self.sort.current()
            QTimer.singleShot(0, lambda: self.load_albums(mode, reverse))
        else:
            QTimer.singleShot(0, self._update_visible_covers)


# ============================================================================
# СТРАНИЦА АЛЬБОМА
# ============================================================================


class AlbumPage(QWidget):
    back = pyqtSignal()
    play_from = pyqtSignal(int)

    def __init__(self, music_folder: Path, parent=None):
        super().__init__(parent)
        self.music_folder = music_folder
        self.album_id: Optional[int] = None
        self.tracks: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 8, 28, 22)
        root.setSpacing(15)

        top = QHBoxLayout()
        back = IconButton("←", 40, "Назад")
        back.clicked.connect(self.back.emit)
        top.addWidget(back)
        crumb = QLabel("Коллекция  /  Альбом")
        crumb.setObjectName("MutedText")
        top.addWidget(crumb)
        top.addStretch()
        root.addLayout(top)

        self.hero = QFrame()
        self.hero.setObjectName("AlbumHero")
        hero_root = QHBoxLayout(self.hero)
        hero_root.setContentsMargins(24, 20, 26, 20)
        hero_root.setSpacing(22)

        self.cover = AsyncCover(184, 16)
        hero_root.addWidget(self.cover)

        info = QVBoxLayout()
        info.setContentsMargins(0, 4, 0, 4)
        info.setSpacing(5)
        cap = QLabel("АЛЬБОМ")
        cap.setObjectName("AccentCaption")
        info.addWidget(cap)
        self.title = QLabel("Альбом")
        self.title.setObjectName("PageTitleLarge")
        self.title.setWordWrap(True)
        info.addWidget(self.title)
        self.artist = QLabel("—")
        self.artist.setObjectName("HeroArtist")
        info.addWidget(self.artist)
        self.details = QLabel("—")
        self.details.setObjectName("MutedText")
        info.addWidget(self.details)
        info.addStretch()

        actions = QHBoxLayout()
        play = QPushButton("▶  Слушать")
        play.setObjectName("AccentButton")
        play.clicked.connect(lambda: self.play_from.emit(0))
        actions.addWidget(play)
        self.open_folder = QPushButton("Открыть папку")
        self.open_folder.setObjectName("GhostButton")
        actions.addWidget(self.open_folder)
        actions.addStretch()
        info.addLayout(actions)
        hero_root.addLayout(info, 1)
        root.addWidget(self.hero)

        header = QHBoxLayout()
        title_tracks = QLabel("Треки")
        title_tracks.setObjectName("SectionTitle")
        header.addWidget(title_tracks)
        header.addStretch()
        root.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("ScrollArea")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.track_list = TrackList(self.music_folder)
        self.track_list.track_clicked.connect(self.play_from.emit)
        self.scroll.setWidget(self.track_list)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        root.addWidget(self.scroll, 1)

    def load(self, album_id: int) -> None:
        self.album_id = album_id
        info = fetch_album_info(album_id)
        self.tracks = fetch_album_tracks(album_id)
        if not info:
            self.title.setText("Альбом не найден")
            self.artist.setText("—")
            self.details.setText("")
            self.cover.reset()
            self.track_list.set_tracks([])
            return

        self.title.setText(info["album"])
        self.artist.setText(info["artist"])
        listened_ms = int(info.get("listened_ms") or 0)
        played_hint = f"  •  прослушано {format_listening_ms(listened_ms)}" if listened_ms else ""
        self.details.setText(
            f"{info.get('year') or 'Год не указан'}  •  {len(self.tracks)} треков{played_hint}"
        )
        cover = info.get("cover_path") or (self.tracks[0].get("cover_path") if self.tracks else "")
        self.cover.load(cover if Path(cover).is_file() else "")
        self.track_list.set_tracks(self.tracks)
        QTimer.singleShot(0, lambda: self.track_list.load_visible_covers(0, self.scroll.viewport().height()))

        def open_folder() -> None:
            if not self.tracks:
                return
            path = resolve_track_path(self.tracks[0], self.music_folder)
            from PyQt6.QtGui import QDesktopServices
            if path:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

        try:
            self.open_folder.clicked.disconnect()
        except TypeError:
            pass
        self.open_folder.clicked.connect(open_folder)

    def _on_scroll(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        self.track_list.maybe_load_more(value, bar.maximum())
        self.track_list.load_visible_covers(value, self.scroll.viewport().height())


# ============================================================================
# БИБЛИОТЕКА / ПОИСК
# ============================================================================


class LibraryPage(QWidget):
    track_clicked = pyqtSignal(int)
    play_all = pyqtSignal()
    shuffle_all = pyqtSignal()
    wave_requested = pyqtSignal()

    def __init__(self, music_folder: Path, parent=None):
        super().__init__(parent)
        self.setObjectName("LibraryPage")
        self.music_folder = music_folder
        self.results: list[dict] = []
        self.sort_mode = "Название"
        self.reverse = False
        self.genre = "Все жанры"

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 8, 28, 22)
        root.setSpacing(14)

        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Треки")
        title.setObjectName("PageTitle")
        self.title_label = title
        title_box.addWidget(title)
        self.count = QLabel("Загрузка…")
        self.count.setObjectName("MutedText")
        title_box.addWidget(self.count)
        head.addLayout(title_box)
        head.addStretch()

        self.genre_combo = QComboBox()
        self.genre_combo.setObjectName("Combo")
        self.genre_combo.setMinimumWidth(148)
        self.genre_combo.addItem("Все жанры")
        try:
            self.genre_combo.addItems(fetch_genres())
        except Exception:
            pass
        self.genre_combo.currentTextChanged.connect(self._genre_changed)
        head.addWidget(self.genre_combo)

        self.sort = SortBar(
            [
                "Время прослушивания",
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
        self.sort.combo.blockSignals(True)
        self.sort.direction.blockSignals(True)
        self.sort.combo.setCurrentText("Название")
        self.sort.direction.setChecked(False)
        self.sort.direction.setText("↑")
        self.sort.direction.blockSignals(False)
        self.sort.combo.blockSignals(False)
        self.sort.changed.connect(self._sort_changed)
        head.addWidget(self.sort)

        wave = QPushButton("✦  Моя волна")
        wave.setObjectName("WaveButton")
        wave.setToolTip(
            "Локальная персональная подборка по истории, избранному и разнообразию исполнителей"
        )
        wave.clicked.connect(self.wave_requested.emit)
        head.addWidget(wave)

        shuffle = IconButton("🔀", 40, "Перемешать результат")
        shuffle.clicked.connect(self.shuffle_all.emit)
        head.addWidget(shuffle)

        play = QPushButton("▶  Слушать")
        play.setObjectName("AccentButton")
        play.clicked.connect(self.play_all.emit)
        head.addWidget(play)
        root.addLayout(head)

        columns = QHBoxLayout()
        columns.setContentsMargins(10, 0, 16, 0)
        columns.setSpacing(12)
        for text, width in (
            ("", 28),
            ("", 46),
            ("Название / исполнитель", 0),
            ("Альбом", 250),
            ("Длительность", 70),
        ):
            label = QLabel(text)
            label.setObjectName("TableHeader")
            if width:
                label.setFixedWidth(width)
            columns.addWidget(label, 1 if width == 0 else 0)
        root.addLayout(columns)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("ScrollArea")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.track_list = TrackList(self.music_folder)
        self.track_list.track_clicked.connect(self._clicked)
        self.scroll.setWidget(self.track_list)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        root.addWidget(self.scroll, 1)

    def _clicked(self, index: int) -> None:
        self.track_clicked.emit(index)

    def refresh(self, query: str = "") -> None:
        self.genre = self.genre_combo.currentText()
        try:
            self.results = search_tracks(
                query,
                self.genre,
                self.sort_mode,
                self.reverse,
                self.music_folder,
            )
        except Exception:
            self.results = []
        self.count.setText(f"{len(self.results)} треков")
        self.track_list.set_tracks(self.results)
        QTimer.singleShot(0, lambda: self.track_list.load_visible_covers(0, self.scroll.viewport().height()))

    def _genre_changed(self, _text: str) -> None:
        self.refresh()

    def _sort_changed(self, mode: str, reverse: bool) -> None:
        self.sort_mode = mode
        self.reverse = reverse
        self.refresh()

    def set_playing(self, path: Optional[str]) -> None:
        self.track_list.set_playing_path(path)

    def _on_scroll(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        self.track_list.maybe_load_more(value, bar.maximum())
        self.track_list.load_visible_covers(value, self.scroll.viewport().height())


# ============================================================================
# ИСТОРИЯ И СТАТИСТИКА ПРОСЛУШИВАНИЯ
# ============================================================================


class ListeningHistoryPage(QWidget):
    """Aggregated listening history: artists first, then their unique tracks."""

    track_activated = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_artist: Optional[str] = None
        self._row_artists: list[str] = []
        self._row_track_paths: list[str] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 10, 28, 24)
        root.setSpacing(18)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        self.page_title = QLabel("История прослушивания")
        self.page_title.setObjectName("PageTitle")
        title_box.addWidget(self.page_title)
        self.subtitle = QLabel("Исполнители отсортированы по последнему прослушиванию")
        self.subtitle.setObjectName("MutedText")
        title_box.addWidget(self.subtitle)
        heading.addLayout(title_box)
        heading.addStretch()

        self.back_button = QPushButton("← Все исполнители")
        self.back_button.setObjectName("GhostButton")
        self.back_button.setVisible(False)
        self.back_button.clicked.connect(self._back_to_artists)
        heading.addWidget(self.back_button)

        refresh_button = QPushButton("↻  Обновить")
        refresh_button.setObjectName("GhostButton")
        refresh_button.clicked.connect(self.refresh)
        heading.addWidget(refresh_button)
        root.addLayout(heading)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.total_time = self._metric_card("ВСЕГО ПРОСЛУШАНО", "—")
        self.total_sessions = self._metric_card("СЕССИЙ", "0")
        self.unique_tracks = self._metric_card("РАЗНЫХ ТРЕКОВ", "0")
        metrics.addWidget(self.total_time[0], 1)
        metrics.addWidget(self.total_sessions[0], 1)
        metrics.addWidget(self.unique_tracks[0], 1)
        root.addLayout(metrics)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("HistoryTable")
        self.table.setHorizontalHeaderLabels(
            ["Исполнитель", "Композиции / сессии", "Всего прослушано", "Последнее прослушивание"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(48)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setAlternatingRowColors(False)
        self.table.cellDoubleClicked.connect(self._activate_row)
        root.addWidget(self.table, 1)

        self.empty = QLabel("История появится после начала прослушивания")
        self.empty.setObjectName("EmptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setVisible(False)
        root.addWidget(self.empty)

    @staticmethod
    def _metric_card(caption: str, value: str):
        card = QFrame()
        card.setObjectName("HistoryMetricCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(7)
        label = QLabel(caption)
        label.setObjectName("HistoryMetricCaption")
        metric = QLabel(value)
        metric.setObjectName("HistoryMetricValue")
        layout.addWidget(label)
        layout.addWidget(metric)
        return card, metric

    def _activate_row(self, row: int, _column: int) -> None:
        if self.current_artist is None:
            if 0 <= row < len(self._row_artists) and self._row_artists[row]:
                self.current_artist = self._row_artists[row]
                self.refresh()
            return

        if 0 <= row < len(self._row_track_paths):
            path = self._row_track_paths[row]
            if path:
                self.track_activated.emit(path)

    def _back_to_artists(self) -> None:
        self.current_artist = None
        self.refresh()

    @staticmethod
    def _time_label(value) -> str:
        if value is None:
            return "—"
        try:
            return datetime.fromtimestamp(float(value)).strftime("%d.%m.%Y  %H:%M")
        except (ValueError, TypeError, OSError, OverflowError):
            return "—"

    def refresh(self, *_args) -> None:
        try:
            summary = fetch_listening_summary()
            rows = (
                fetch_artist_track_history(self.current_artist)
                if self.current_artist is not None
                else fetch_listening_view("Исполнители")
            )
        except Exception as exc:
            self.subtitle.setText(f"Не удалось прочитать статистику: {exc}")
            return

        self.total_time[1].setText(format_listening_ms(summary["listened_ms"]))
        self.total_sessions[1].setText(f'{summary["sessions"]:,}'.replace(",", " "))
        self.unique_tracks[1].setText(f'{summary["tracks"]:,}'.replace(",", " "))

        if self.current_artist is None:
            self.page_title.setText("История прослушивания")
            self.subtitle.setText(
                "Исполнители по дате последнего прослушивания · двойной щелчок открывает композиции"
            )
            self.back_button.setVisible(False)
            self.table.setHorizontalHeaderLabels(
                ["Исполнитель", "Композиции / сессии", "Всего прослушано", "Последнее прослушивание"]
            )
        else:
            self.page_title.setText(self.current_artist)
            self.subtitle.setText(
                "Композиции исполнителя, отсортированные по суммарному времени прослушивания"
            )
            self.back_button.setVisible(True)
            self.table.setHorizontalHeaderLabels(
                ["Композиция", "Альбом / сессии", "Всего прослушано", "Последнее прослушивание"]
            )

        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(0)
        self._row_artists = []
        self._row_track_paths = []
        for row in rows:
            index = self.table.rowCount()
            title = str(row.get("title") or "—")
            details = str(row.get("album") or "—")
            listened = format_listening_ms(row.get("listened_ms"))
            last_played = self._time_label(row.get("last_played"))

            if self.current_artist is None:
                self._row_artists.append(title)
                self._row_track_paths.append("")
            else:
                sessions = int(row.get("sessions") or 0)
                if sessions > 1:
                    details += f" · {sessions} сессий"
                self._row_artists.append("")
                self._row_track_paths.append(str(row.get("file_path") or ""))

            self.table.insertRow(index)
            values = (title, details, listened, last_played)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 2:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                    item.setToolTip(f"Всего прослушано: {listened}")
                self.table.setItem(index, column, item)

        self.table.setUpdatesEnabled(True)
        self.empty.setVisible(not rows)
        self.table.setVisible(bool(rows))




class SeekSlider(QSlider):
    """Seek slider with reliable click-to-position and drag seeking."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _set_from_mouse_x(self, x: float) -> None:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        groove = style.subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderGroove,
            self,
        )
        handle = style.subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            self,
        )
        left = groove.left() + handle.width() / 2.0
        right = groove.right() - handle.width() / 2.0
        span = max(1, int(round(right - left)))
        position = int(round(max(0.0, min(float(span), x - left))))
        value = QStyle.sliderValueFromPosition(
            self.minimum(),
            self.maximum(),
            position,
            span,
            option.upsideDown,
        )
        self.setSliderPosition(value)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self._set_from_mouse_x(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.isSliderDown() and event.buttons() & Qt.MouseButton.LeftButton:
            self._set_from_mouse_x(event.position().x())
            self.sliderMoved.emit(self.sliderPosition())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self._set_from_mouse_x(event.position().x())
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class PlayerBar(QFrame):
    play_pause = pyqtSignal()
    previous = pyqtSignal()
    next = pyqtSignal()
    shuffle = pyqtSignal(bool)
    repeat = pyqtSignal()
    volume = pyqtSignal(int)
    seek = pyqtSignal(int)
    queue_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.setFixedHeight(BOTTOM_PLAYER_HEIGHT)
        self._seeking = False
        self._queue_block = False

        root = QHBoxLayout(self)
        root.setContentsMargins(18, 9, 18, 9)
        root.setSpacing(18)

        left = QHBoxLayout()
        left.setSpacing(12)
        self.cover = AsyncCover(58, 9)
        left.addWidget(self.cover)

        info = QVBoxLayout()
        info.setContentsMargins(0, 3, 0, 3)
        info.setSpacing(2)
        self.title = ElidedLabel("Ничего не играет", TEXT)
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
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setMinimumWidth(290)
        left_widget.setMaximumWidth(370)
        root.addWidget(left_widget)

        center = QVBoxLayout()
        center.setSpacing(4)

        controls = QHBoxLayout()
        controls.setSpacing(7)
        controls.addStretch()

        self.shuffle_btn = IconButton("⤨", 36, "Случайный порядок", True)
        self.shuffle_btn.setObjectName("ShuffleButton")
        self.shuffle_btn.toggled.connect(self.shuffle.emit)
        controls.addWidget(self.shuffle_btn)

        self.previous_btn = IconButton("⏮", 38, "Предыдущий трек")
        self.previous_btn.clicked.connect(self.previous.emit)
        controls.addWidget(self.previous_btn)

        self.play_btn = IconButton("▶", 50, "Воспроизведение / пауза")
        self.play_btn.setObjectName("PlayButton")
        self.play_btn.clicked.connect(self.play_pause.emit)
        controls.addWidget(self.play_btn)

        self.next_btn = IconButton("⏭", 38, "Следующий трек")
        self.next_btn.clicked.connect(self.next.emit)
        controls.addWidget(self.next_btn)

        self.repeat_btn = IconButton("↻", 36, "Повтор: выключен")
        self.repeat_btn.clicked.connect(self.repeat.emit)
        controls.addWidget(self.repeat_btn)
        controls.addStretch()
        center.addLayout(controls)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)
        self.time = QLabel("00:00")
        self.time.setObjectName("TinyText")
        self.time.setFixedWidth(45)
        self.time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        progress_row.addWidget(self.time)

        self.progress = SeekSlider()
        self.progress.setObjectName("ProgressSlider")
        self.progress.setRange(0, 1000)
        self.progress.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.progress.sliderReleased.connect(self._seek_released)
        progress_row.addWidget(self.progress, 1)

        self.duration = QLabel("00:00")
        self.duration.setObjectName("TinyText")
        self.duration.setFixedWidth(45)
        progress_row.addWidget(self.duration)
        center.addLayout(progress_row)
        root.addLayout(center, 1)

        right = QHBoxLayout()
        right.setSpacing(10)
        self.volume_label = QLabel("🔊")
        self.volume_label.setObjectName("VolumeIcon")
        right.addWidget(self.volume_label)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("VolumeSlider")
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.valueChanged.connect(self._volume_changed)
        right.addWidget(self.volume_slider)

        self.queue = QComboBox()
        self.queue.setObjectName("QueueCombo")
        self.queue.setMinimumWidth(230)
        self.queue.currentIndexChanged.connect(self._queue_changed)
        right.addWidget(self.queue)
        root.addLayout(right)

    def _volume_changed(self, value: int) -> None:
        self.volume.emit(value)

    def _seek_released(self) -> None:
        self._seeking = False
        self.seek.emit(self.progress.value())

    def _queue_changed(self, index: int) -> None:
        if not self._queue_block and index >= 0:
            self.queue_clicked.emit(index)

    def set_track(self, track: Optional[dict]) -> None:
        if not track:
            self.title.setText("Ничего не играет")
            self.artist.setText("—")
            self.album.setText("—")
            self.cover.reset()
            return
        self.title.setText(safe_text(track.get("track_name"), "Неизвестный трек"))
        self.artist.setText(safe_text(track.get("artist"), "Неизвестный исполнитель"))
        self.album.setText(safe_text(track.get("album"), "—"))
        cover = track.get("cover_path") or ""
        self.cover.load(cover if Path(cover).is_file() else "")

    def set_playing(self, playing: bool) -> None:
        self.play_btn.setText("⏸" if playing else "▶")

    def set_shuffle(self, enabled: bool) -> None:
        self.shuffle_btn.blockSignals(True)
        self.shuffle_btn.setChecked(enabled)
        self.shuffle_btn.blockSignals(False)
        self.shuffle_btn.setText("⤨")

    def set_repeat(self, mode: int) -> None:
        texts = {0: "↻", 1: "↻", 2: "↻¹"}
        tips = {0: "Повтор: выключен", 1: "Повтор: очередь", 2: "Повтор: один трек"}
        self.repeat_btn.setText(texts.get(mode, "↻"))
        self.repeat_btn.setToolTip(tips.get(mode, "Повтор"))
        self.repeat_btn.setProperty("repeatActive", mode != 0)
        self.repeat_btn.setProperty("repeatOne", mode == 2)
        self.repeat_btn.style().unpolish(self.repeat_btn)
        self.repeat_btn.style().polish(self.repeat_btn)

    def set_progress(self, current_ms: int, total_ms: int) -> None:
        total_ms = max(0, int(total_ms or 0))
        current_ms = max(0, int(current_ms or 0))
        if not self._seeking:
            value = int(current_ms * 1000 / total_ms) if total_ms > 0 else 0
            self.progress.blockSignals(True)
            self.progress.setValue(max(0, min(1000, value)))
            self.progress.blockSignals(False)
        self.time.setText(format_ms(current_ms))
        self.duration.setText(format_ms(total_ms))

    def set_queue(self, queue: list[dict], current_index: int) -> None:
        self._queue_block = True
        self.queue.clear()
        for index, track in enumerate(queue):
            prefix = "▶  " if index == current_index else ""
            text = f"{prefix}{safe_text(track.get('track_name'), 'Трек')}  —  {safe_text(track.get('artist'), 'Исполнитель')}"
            self.queue.addItem(text)
        if 0 <= current_index < self.queue.count():
            self.queue.setCurrentIndex(current_index)
        self._queue_block = False


# ============================================================================
# ГЛАВНОЕ ОКНО
# ============================================================================


class WaveBackground(QWidget):
    """Paint a restrained golden wave behind the transparent page surfaces."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = max(1, self.width())
        height = max(1, self.height())

        base = QLinearGradient(0, 0, width, height)
        base.setColorAt(0.0, QColor("#1b1b20"))
        base.setColorAt(0.48, QColor("#17171c"))
        base.setColorAt(1.0, QColor("#19191d"))
        painter.fillRect(self.rect(), base)

        wave = QPainterPath()
        wave.moveTo(0, height * 0.72)
        wave.cubicTo(
            width * 0.18, height * 0.55,
            width * 0.36, height * 0.76,
            width * 0.54, height * 0.59,
        )
        wave.cubicTo(
            width * 0.73, height * 0.39,
            width * 0.83, height * 0.54,
            width, height * 0.43,
        )
        wave.lineTo(width, height)
        wave.lineTo(0, height)
        wave.closeSubpath()

        painter.save()
        painter.setClipPath(wave)
        gold = QLinearGradient(0, height * 0.40, 0, height)
        gold.setColorAt(0.0, QColor(255, 220, 0, 0))
        gold.setColorAt(0.30, QColor(214, 181, 0, 24))
        gold.setColorAt(0.68, QColor(181, 151, 0, 42))
        gold.setColorAt(1.0, QColor(154, 127, 0, 48))
        painter.fillRect(self.rect(), gold)
        painter.restore()
        painter.end()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1180, 720)

        self.settings = load_settings()
        configured_folder = configured_music_folder(self.settings)
        self._music_folder_configured = configured_folder is not None
        # Keep a harmless Path placeholder for older UI components; never treat
        # it as a selected library unless the user actually configured a folder.
        self.music_folder = configured_folder or Path()

        # libVLC is initialized lazily at the first playback request so that
        # opening the library does not pay the native engine startup cost.
        ensure_database_schema()
        self.vlc_instance = None
        self.player = None
        self.current_path = ""
        self._player_media_path = ""
        self.current_track: Optional[dict] = None
        self.queue: list[dict] = []
        self.queue_index = -1
        self.history: list[int] = []
        self.history_pos = -1
        self.shuffle_enabled = bool(self.settings.get("shuffle", False))
        self.repeat_mode = int(self.settings.get("repeat_mode", 0) or 0)
        self.track_duration = 0
        self._listening_session_id: Optional[int] = None
        self._listening_generation = 0
        self._listening_active = False
        self._session_error_generation: Optional[int] = None
        self._session_writer = ListeningSessionWriter()
        self._session_writer.signals.started.connect(self._on_listening_session_started)
        self._session_writer.signals.error.connect(self._on_listening_session_error)
        self._listening_session_ms = 0
        self._listening_base_ms = 0
        self._listening_last_sample: Optional[float] = None
        self._listening_last_flush_ms = 0
        self.scan_running = False
        self.scan_silent = False
        self.scan_overlay = None
        self.scan_progress = None
        self.scan_detail = None
        self._scan_task = None
        self._cover_repair_task = None

        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(450)
        self._settings_timer.timeout.connect(lambda: save_settings(self.settings))

        self._build_ui()
        self._apply_stylesheet()
        self._load_saved_view_state()
        self.player_bar.volume_slider.setValue(int(self.settings.get("volume", 70)))
        self.player_bar.set_shuffle(self.shuffle_enabled)
        self.player_bar.set_repeat(self.repeat_mode)

        self.playback_timer = QTimer(self)
        self.playback_timer.setInterval(220)
        self.playback_timer.timeout.connect(self._tick)
        self.playback_timer.start()

        QTimer.singleShot(0, self._post_start)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.home_clicked.connect(self.show_home)
        self.sidebar.collection_clicked.connect(self.show_albums)
        self.sidebar.history_clicked.connect(self.show_history)
        self.sidebar.search_clicked.connect(self.show_search)
        self.sidebar.folder_clicked.connect(self.choose_music_folder)
        root.addWidget(self.sidebar)

        content = WaveBackground()
        content.setObjectName("Content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        root.addWidget(content, 1)

        self.search_header = SearchHeader()
        self.search_header.submitted.connect(self.search_now)
        content_layout.addWidget(self.search_header)

        self.stack = QStackedWidget()
        self.home = HomePage()
        self.home.album_selected.connect(self.show_album)
        self.home.album_play_requested.connect(self.play_album_from_zero)
        self.home.now.open_album_requested.connect(self._open_current_album)
        self.home.now.open_folder_requested.connect(self._open_current_folder)
        self.home.play_all.connect(self.play_all)
        self.home.shuffle_all.connect(self.shuffle_all)
        self.home.sort_changed.connect(self.home_sort_changed)

        self.album_page = AlbumPage(self.music_folder)
        self.album_page.back.connect(self.show_albums)
        self.album_page.play_from.connect(self.play_album_index)

        self.library = LibraryPage(self.music_folder)
        self.library.track_clicked.connect(self.play_library_index)
        self.library.play_all.connect(self.play_library_all)
        self.library.shuffle_all.connect(self.shuffle_library)
        self.library.wave_requested.connect(self.play_my_wave)

        self.history_page = ListeningHistoryPage()
        self.history_page.track_activated.connect(self.play_history_path)
        self.stack.addWidget(self.library)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.album_page)
        self.stack.addWidget(self.history_page)
        content_layout.addWidget(self.stack, 1)

        self.player_bar = PlayerBar()
        self.player_bar.play_pause.connect(self.toggle_play_pause)
        self.player_bar.previous.connect(self.play_previous)
        self.player_bar.next.connect(self.play_next)
        self.player_bar.shuffle.connect(self.toggle_shuffle)
        self.player_bar.repeat.connect(self.cycle_repeat)
        self.player_bar.volume.connect(self.change_volume)
        self.player_bar.seek.connect(self.seek_to)
        self.player_bar.queue_clicked.connect(self.on_queue_clicked)
        player_bar_container = QWidget()
        player_bar_container.setObjectName("PlayerBarContainer")
        player_bar_layout = QHBoxLayout(player_bar_container)
        player_bar_layout.setContentsMargins(12, 6, 12, 8)
        player_bar_layout.setSpacing(0)
        player_bar_layout.addWidget(self.player_bar)
        content_layout.addWidget(player_bar_container)

        self.statusBar().setObjectName("StatusBar")
        self.statusBar().showMessage("Готово")

        self.sidebar.set_music_path(
            self.music_folder
            if self._music_folder_configured and self.music_folder.exists() and self.music_folder.is_dir()
            else None
        )
        self.sidebar.set_active(self.sidebar.btn_home)

    def _load_saved_view_state(self) -> None:
        geometry = self.settings.get("geometry")
        try:
            if isinstance(geometry, dict):
                self.setGeometry(
                    int(geometry.get("x", 100)),
                    int(geometry.get("y", 100)),
                    int(geometry.get("w", 1280)),
                    int(geometry.get("h", 800)),
                )
        except Exception:
            pass

    def _post_start(self) -> None:
        create_desktop_shortcut_async()
        self._validate_music_folder()

    # ------------------------------------------------------------------ folder
    def _validate_music_folder(self) -> None:
        if (
            self._music_folder_configured
            and self.music_folder.exists()
            and self.music_folder.is_dir()
        ):
            self._load_or_scan_existing()
            return
        self.choose_music_folder(initial=True)

    def choose_music_folder(self, initial: bool = False) -> None:
        start = str(
            self.music_folder
            if self._music_folder_configured
            and self.music_folder.exists()
            and self.music_folder.is_dir()
            else Path.home()
        )
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку с музыкой", start)
        if not folder:
            if initial:
                self.statusBar().showMessage("Папка с музыкой не выбрана")
            return
        self._finish_listening_session(completed=False)
        if self.player is not None:
            try:
                self.player.stop()
            except Exception:
                pass
        self.current_track = None
        self.current_path = ""
        self.track_duration = 0
        self.queue = []
        self.queue_index = -1
        self.history = []
        self.history_pos = -1
        self.player_bar.set_track(None)
        self.player_bar.set_playing(False)
        self.player_bar.set_queue([], -1)
        self._player_media_path = ""
        self.home.set_now_playing(None)
        self.album_page.track_list.set_playing_path(None)
        self.library.set_playing(None)

        self.music_folder = Path(folder).resolve()
        self._music_folder_configured = True
        self.settings["music_folder"] = str(self.music_folder)
        save_settings(self.settings)
        self.sidebar.set_music_path(self.music_folder)
        self.album_page.music_folder = self.music_folder
        self.library.music_folder = self.music_folder
        self._reset_library_views()
        self.start_scan()

    def _reset_library_views(self) -> None:
        self.home._loaded_once = False
        self.library.results = []
        self.library.track_list.set_tracks([])
        self.history_page.refresh()

    def _load_or_scan_existing(self) -> None:
        count = 0
        first_track_path = ""

        try:
            conn = db_connect()
            try:
                # The presence check must be O(1); COUNT(*) would scan the whole
                # tracks table on the UI thread and delay startup on large libraries.
                row = conn.execute("SELECT file_path FROM tracks ORDER BY id LIMIT 1").fetchone()
                if row:
                    count = 1
                    first_track_path = str(row[0] or "")
            finally:
                conn.close()
        except Exception:
            count = 0

        # If the saved database appears to belong to this music folder, show
        # the cached library immediately and refresh it in the background.
        # A database from another root must not be shown as if it were current.
        same_root_hint = bool(
            first_track_path
            and ScanTask._is_inside_root(first_track_path, self.music_folder)
        )

        if count > 0 and same_root_hint:
            self.home.load_albums("Количество треков", True)
            self.library.refresh("")
            self._select_first_track()
            self.statusBar().showMessage("Библиотека загружена; проверяем изменения…")
            self.start_scan(silent=True)
        else:
            # First run, empty database, or a changed music folder: use the
            # visible scan panel so the user understands why the library is empty.
            self.start_scan()

    def _make_cover_repair_task(self) -> CoverRepairTask:
        task = CoverRepairTask(self.music_folder)
        task.signals.finished.connect(self._cover_repair_finished)
        task.signals.error.connect(
            lambda message: self.statusBar().showMessage(f"Обложки: {message}", 5000)
        )
        return task

    def start_scan(self, silent: bool = False) -> None:
        if self.scan_running or not self.music_folder:
            return

        self.scan_running = True
        self.scan_silent = bool(silent)
        self.scan_overlay = None
        self.scan_progress = None
        self.scan_detail = None

        if not self.scan_silent:
            overlay = QFrame(self)
            overlay.setObjectName("ScanOverlay")
            overlay.setGeometry(self.rect())
            outer = QVBoxLayout(overlay)
            outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

            card = QFrame()
            card.setObjectName("ScanCard")
            card.setFixedWidth(590)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(34, 30, 34, 30)
            layout.setSpacing(13)

            title = QLabel("Индексируем музыкальную библиотеку")
            title.setObjectName("ScanTitle")
            layout.addWidget(title)
            folder_label = QLabel(str(self.music_folder))
            folder_label.setObjectName("MutedText")
            folder_label.setWordWrap(True)
            layout.addWidget(folder_label)

            self.scan_progress = QProgressBar()
            self.scan_progress.setRange(0, 100)
            self.scan_progress.setValue(0)
            layout.addWidget(self.scan_progress)
            self.scan_detail = QLabel("Подготовка…")
            self.scan_detail.setObjectName("DimText")
            layout.addWidget(self.scan_detail)

            outer.addWidget(card)
            overlay.show()
            self.scan_overlay = overlay
            self.statusBar().showMessage("Сканирование библиотеки…")
        else:
            self.statusBar().showMessage("Проверяем изменения в медиатеке в фоне…")

        task = ScanTask(self.music_folder)
        self._scan_task = task
        task.signals.progress.connect(self._scan_progress)
        task.signals.finished.connect(self._scan_finished)
        task.signals.error.connect(self._scan_error)
        QThreadPool.globalInstance().start(task)

    def _scan_progress(self, current: int, total: int, title: str) -> None:
        if self.scan_silent:
            self.statusBar().showMessage(
                f"Проверяем медиатеку: {current:,} / {total:,} • {title}".replace(",", " ")
            )
        elif self.scan_progress is not None and self.scan_detail is not None:
            self.scan_progress.setValue(int(current * 100 / total) if total else 0)
            self.scan_detail.setText(f"{current:,} / {total:,}   •   {title}".replace(",", " "))

    def _dispose_scan_overlay(self) -> None:
        overlay = self.scan_overlay
        self.scan_overlay = None
        self.scan_progress = None
        self.scan_detail = None
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()

    def _scan_finished(self, success: int, failed: int) -> None:
        was_silent = self.scan_silent
        self.scan_running = False
        self.scan_silent = False
        self._dispose_scan_overlay()
        self._scan_task = None

        current_page = self.stack.currentWidget()
        if current_page is self.home:
            mode, reverse = self.home.sort.current()
            self.home.load_albums(mode, reverse)
        else:
            # Avoid constructing a potentially huge album grid while it is hidden.
            self.home._loaded_once = False

        query = self.search_header.edit.text().strip() if self.sidebar.btn_search.isChecked() else ""
        self.library.refresh(query)
        self.library.set_playing(self.current_path)
        if current_page is self.album_page and self.album_page.album_id is not None:
            self.album_page.load(self.album_page.album_id)
            self.album_page.track_list.set_playing_path(self.current_path)
        if current_page is self.history_page:
            self.history_page.refresh()
        if not self.current_track or not self.current_path or not Path(self.current_path).is_file():
            self._select_first_track()

        self._cover_repair_task = self._make_cover_repair_task()
        QThreadPool.globalInstance().start(self._cover_repair_task)

        summary = f"Библиотека готова: {success:,} треков".replace(",", " ")
        if failed:
            summary += f" • пропущено: {failed:,}".replace(",", " ")
        if was_silent:
            summary = "Медиатека проверена · " + summary
        self.statusBar().showMessage(summary, 7000)

    def _scan_error(self, message: str) -> None:
        was_silent = self.scan_silent
        self.scan_running = False
        self.scan_silent = False
        self._dispose_scan_overlay()
        self._scan_task = None

        if was_silent:
            # Cached tracks remain visible; a later manual scan can retry.
            self.statusBar().showMessage(
                f"Не удалось проверить медиатеку: {message}",
                12000,
            )
        else:
            QMessageBox.critical(self, "Ошибка сканирования", message)

    def _cover_repair_finished(self, repaired: int) -> None:
        self._cover_repair_task = None
        if repaired:
            COVER_CACHE.clear()
            if self.stack.currentWidget() is self.home:
                mode, reverse = self.home.sort.current()
                self.home.load_albums(mode, reverse)
            self.statusBar().showMessage(f"Обложки восстановлены: {repaired}", 4000)

    # ---------------------------------------------------------------- navigation
    def show_home(self) -> None:
        self.stack.setCurrentWidget(self.library)
        self.sidebar.set_active(self.sidebar.btn_home)
        self.search_header._timer.stop()
        self.search_header.edit.blockSignals(True)
        self.search_header.edit.clear()
        self.search_header.edit.blockSignals(False)
        self.library.refresh("")
        self.library.set_playing(self.current_path)
        self.home.set_now_playing(self.current_track)

    def show_library(self) -> None:
        # Backwards-compatible alias for the primary tracks page.
        self.show_home()

    def show_albums(self) -> None:
        self.stack.setCurrentWidget(self.home)
        self.sidebar.set_active(self.sidebar.btn_collection)
        self.home.set_now_playing(self.current_track)
        if not self.home._loaded_once:
            mode, reverse = self.home.sort.current()
            self.home.load_albums(mode, reverse)

    def show_history(self) -> None:
        self.stack.setCurrentWidget(self.history_page)
        self.sidebar.set_active(self.sidebar.btn_history)
        self.history_page.refresh()

    def play_history_path(self, path: str) -> None:
        if not path or not Path(path).is_file():
            self.statusBar().showMessage("Файл из истории больше не найден на диске", 5000)
            return
        tracks = fetch_all_tracks(self.music_folder, "Название", False)
        key = os.path.normcase(os.path.abspath(path))
        for index, track in enumerate(tracks):
            if os.path.normcase(os.path.abspath(track.get("file_path", ""))) == key:
                self._set_queue(tracks, index, False)
                return
        self.statusBar().showMessage(
            "Этот трек не входит в текущую библиотеку. Обнови папку музыки или выполни сканирование.",
            7000,
        )

    def show_search(self) -> None:
        self.stack.setCurrentWidget(self.library)
        self.sidebar.set_active(self.sidebar.btn_search)
        self.search_header.edit.setFocus()
        self.library.refresh(self.search_header.edit.text().strip())
        self.library.set_playing(self.current_path)

    def search_now(self, query: str) -> None:
        self.stack.setCurrentWidget(self.library)
        self.sidebar.set_active(self.sidebar.btn_search)
        self.library.refresh(query)
        self.library.set_playing(self.current_path)
        self.statusBar().showMessage(f"Поиск: {query}" if query else "Вся библиотека", 2500)

    def home_sort_changed(self, mode: str, reverse: bool) -> None:
        self.home.load_albums(mode, reverse)

    def show_album(self, album_id: int) -> None:
        self.album_page.load(album_id)
        self.album_page.track_list.set_playing_path(self.current_path)
        self.stack.setCurrentWidget(self.album_page)
        self.sidebar.set_active(None)

    # ---------------------------------------------------------------- playback
    @staticmethod
    def _normalise_track(track: dict) -> dict:
        result = dict(track)
        defaults = {
            "track_name": "Неизвестный трек",
            "artist": "Неизвестный исполнитель",
            "album": "Без названия",
            "genre": "Разное",
            "year": None,
            "duration_ms": 0,
            "file_size": 0,
            "mtime": 0,
            "cover_path": "",
            "file_path": "",
            "relative_path": "",
            "favorite": False,
            "listened_ms": 0,
        }
        for key, value in defaults.items():
            result.setdefault(key, value)
        return result

    def _set_queue(self, tracks: list[dict], index: int = 0, shuffle: bool = False) -> None:
        tracks = [self._normalise_track(track) for track in tracks]
        if not tracks:
            return
        self.queue = tracks
        self.queue_index = max(0, min(index, len(tracks) - 1))
        self.history = [self.queue_index]
        self.history_pos = 0
        self.shuffle_enabled = shuffle
        self.player_bar.set_shuffle(shuffle)
        self._start_current()

    def play_all(self) -> None:
        tracks = fetch_all_tracks(self.music_folder, "Название", False)
        self._set_queue(tracks, 0, False)

    def shuffle_all(self) -> None:
        tracks = fetch_all_tracks(self.music_folder, "Название", False)
        random.shuffle(tracks)
        self._set_queue(tracks, 0, True)

    def play_album_from_zero(self, album_id: int) -> None:
        tracks = fetch_album_tracks(album_id)
        self._set_queue(tracks, 0, False)

    def play_album_index(self, index: int) -> None:
        tracks = list(self.album_page.tracks)
        if tracks:
            self._set_queue(tracks, index, False)

    def play_library_index(self, index: int) -> None:
        results = self.library.results
        if results:
            self._set_queue(results, index, False)

    def play_library_all(self) -> None:
        if self.library.results:
            self._set_queue(list(self.library.results), 0, False)

    def shuffle_library(self) -> None:
        tracks = list(self.library.results)
        random.shuffle(tracks)
        self._set_queue(tracks, 0, True)

    def play_my_wave(self) -> None:
        try:
            tracks = fetch_all_tracks(self.music_folder, "Время прослушивания", True)
            if not tracks:
                self.statusBar().showMessage("В библиотеке пока нет треков для «Моей волны».", 5000)
                return
            artist_history = fetch_artist_listening_totals()
            mix = build_local_wave(tracks, artist_history)
            self._set_queue(mix, 0, False)
            self.statusBar().showMessage(
                f"Моя волна: персональная подборка из {len(mix)} локальных треков",
                5000,
            )
        except Exception as exc:
            self.statusBar().showMessage(f"Не удалось собрать «Мою волну»: {exc}", 7000)

    def _ensure_player(self) -> bool:
        if self.player is not None and self.vlc_instance is not None:
            return True

        if vlc is None:
            details = str(VLC_IMPORT_ERROR or "модуль python-vlc не найден")
            QMessageBox.warning(
                self,
                "Воспроизведение недоступно",
                "Не удалось загрузить python-vlc.\n\n"
                f"{details}\n\n"
                "Установите зависимости проекта и VLC Media Player, "
                "либо используйте EXE-сборку с libVLC."
            )
            return False

        instance = None
        player = None
        try:
            instance = vlc.Instance("--no-video")
            if instance is None:
                raise RuntimeError("libVLC не создал экземпляр проигрывателя")
            player = instance.media_player_new()
            if player is None:
                raise RuntimeError("libVLC не создал медиаплеер")

            player.audio_set_volume(int(self.player_bar.volume_slider.value()))
            self.vlc_instance = instance
            self.player = player
            return True
        except Exception as exc:
            # Release partially created native resources before allowing a retry.
            if player is not None:
                try:
                    player.release()
                except Exception:
                    pass
            if instance is not None:
                try:
                    instance.release()
                except Exception:
                    pass
            self.vlc_instance = None
            self.player = None
            QMessageBox.warning(
                self,
                "Не удалось запустить аудиодвижок",
                f"{exc}\n\n"
                "Проверь, что VLC Media Player установлен или рядом с приложением "
                "есть папка vlc с libVLC и plugins."
            )
            return False

    def _start_current(self) -> None:
        if not (0 <= self.queue_index < len(self.queue)):
            return
        track = self.queue[self.queue_index]
        path = resolve_track_path(track, self.music_folder)
        if not path or not Path(path).is_file():
            self.statusBar().showMessage(f"Файл не найден: {path}", 5000)
            self.play_next()
            return

        self._finish_listening_session(completed=False)
        if not self._ensure_player():
            return

        try:
            self.player.stop()
            media = self.vlc_instance.media_new(path)
            self.player.set_media(media)
            self.player.audio_set_volume(self.player_bar.volume_slider.value())
            result = self.player.play()
            if result == -1:
                raise RuntimeError("VLC не смог начать воспроизведение")
            self._player_media_path = path
        except Exception as exc:
            QMessageBox.warning(self, "Ошибка воспроизведения", str(exc))
            return

        track["file_path"] = path
        self.current_path = path
        self.current_track = track
        self.track_duration = int(track.get("duration_ms") or 0)

        self.player_bar.set_track(track)
        self.player_bar.set_playing(True)
        self.player_bar.set_queue(self.queue, self.queue_index)
        self.player_bar.set_progress(0, self.track_duration)
        self.home.set_now_playing(track, playing=True)
        self.album_page.track_list.set_playing_path(path)
        self.library.set_playing(path)
        self._begin_listening_session(track)

        self.settings["last_track_rel"] = track.get("relative_path", "")
        self.settings["shuffle"] = self.shuffle_enabled
        self.settings["repeat_mode"] = self.repeat_mode
        self._settings_timer.start()

    def toggle_play_pause(self) -> None:
        if self.queue_index < 0:
            return
        if (
            self.player is None
            or not self.current_path
            or self._player_media_path != self.current_path
        ):
            self._start_current()
            return
        try:
            state = self.player.get_state()
            if state == vlc.State.Playing:
                self.player.pause()
                self.player_bar.set_playing(False)
                self.home.now.set_playing(False)
            else:
                self.player.play()
                self.player_bar.set_playing(True)
                self.home.now.set_playing(True)
        except Exception:
            try:
                self.player.play()
                self.player_bar.set_playing(True)
                self.home.now.set_playing(True)
            except Exception:
                pass

    def play_next(self) -> None:
        if not self.queue:
            return
        if self.repeat_mode == 2:
            # Repeat-one is meaningful only after the selected track is loaded.
            if self.player is None:
                return
            if self._player_media_path != self.current_path:
                self._start_current()
                return
            self.player.set_time(0)
            self.player.play()
            self.player_bar.set_playing(True)
            self.home.now.set_playing(True)
            if not self._listening_active and self.current_track:
                self._begin_listening_session(self.current_track)
            return

        if self.shuffle_enabled and len(self.queue) > 1:
            choices = [index for index in range(len(self.queue)) if index != self.queue_index]
            next_index = random.choice(choices)
        else:
            next_index = self.queue_index + 1

        if next_index >= len(self.queue):
            if self.repeat_mode == 1:
                next_index = 0
            else:
                if self.player is not None:
                    self.player.stop()
                self.player_bar.set_playing(False)
                self.home.now.set_playing(False)
                return

        self.queue_index = next_index
        self.history = self.history[: self.history_pos + 1]
        self.history.append(next_index)
        self.history_pos = len(self.history) - 1
        self._start_current()

    def play_previous(self) -> None:
        if self.history_pos > 0:
            self.history_pos -= 1
            self.queue_index = self.history[self.history_pos]
            self._start_current()
            return
        if not self.shuffle_enabled and self.queue_index > 0:
            self.queue_index -= 1
            self._start_current()

    def toggle_shuffle(self, enabled: bool) -> None:
        self.shuffle_enabled = bool(enabled)
        self.settings["shuffle"] = self.shuffle_enabled
        self._settings_timer.start()
        self.statusBar().showMessage(
            "Случайный порядок: включён" if enabled else "Случайный порядок: выключен",
            2000,
        )

    def cycle_repeat(self) -> None:
        self.repeat_mode = (self.repeat_mode + 1) % 3
        self.player_bar.set_repeat(self.repeat_mode)
        self.settings["repeat_mode"] = self.repeat_mode
        self._settings_timer.start()
        names = {0: "Повтор выключен", 1: "Повтор очереди", 2: "Повтор одного трека"}
        self.statusBar().showMessage(names[self.repeat_mode], 1800)

    def change_volume(self, value: int) -> None:
        if self.player is not None:
            self.player.audio_set_volume(int(value))
        self.settings["volume"] = int(value)
        self._settings_timer.start()

    def seek_to(self, promille: int) -> None:
        try:
            length = self.player.get_length()
            if length > 0:
                self.player.set_time(int(length * max(0, min(1000, promille)) / 1000))
        except Exception:
            pass

    def on_queue_clicked(self, index: int) -> None:
        if 0 <= index < len(self.queue):
            self.queue_index = index
            self.history = [index]
            self.history_pos = 0
            self._start_current()

    def _begin_listening_session(self, track: dict) -> None:
        self._listening_generation += 1
        self._listening_session_id = None
        self._listening_active = True
        self._session_error_generation = None
        self._listening_session_ms = 0
        self._listening_base_ms = int(track.get("listened_ms") or 0)
        self._listening_last_sample = None
        self._listening_last_flush_ms = 0
        self._session_writer.start_session(self._listening_generation, track)

    def _on_listening_session_started(
        self, generation: int, session_id, error: str
    ) -> None:
        if generation != self._listening_generation or not self._listening_active:
            return
        self._listening_session_id = int(session_id) if session_id else None
        if error and self._session_error_generation != generation:
            self._session_error_generation = generation
            self.statusBar().showMessage(
                f"Историю прослушивания не удалось сохранить: {error}", 7000
            )

    def _on_listening_session_error(self, generation: int, message: str) -> None:
        if generation != self._listening_generation or not self._listening_active:
            return
        if self._session_error_generation == generation:
            return
        self._session_error_generation = generation
        self.statusBar().showMessage(
            f"История прослушивания временно задержана: {message}", 7000
        )

    def _finish_listening_session(self, completed: bool = False) -> None:
        if not self._listening_active:
            return
        generation = self._listening_generation
        total_ms = max(0, int(self._listening_session_ms))
        track_path = self.current_path
        track_total_ms = self._listening_base_ms + total_ms
        self._session_writer.finish_session(
            generation, total_ms, completed=completed
        )
        self._listening_session_id = None
        self._listening_active = False
        self._listening_last_sample = None
        if self.current_track and self.current_track.get("file_path") == track_path:
            self.current_track["listened_ms"] = track_total_ms
        for queued in self.queue:
            if queued.get("file_path") == track_path:
                queued["listened_ms"] = track_total_ms
        if self.stack.currentWidget() is self.history_page:
            self.history_page.refresh()

    def _select_first_track(self) -> bool:
        """Select a sensible default track without opening VLC or starting playback."""
        try:
            tracks = fetch_all_tracks(
                self.music_folder,
                "Название",
                False,
            )
        except Exception as exc:
            self.statusBar().showMessage(f"Не удалось выбрать трек: {exc}", 5000)
            return False
        if not tracks:
            return False

        self._finish_listening_session(completed=False)
        if self.player is not None:
            try:
                self.player.stop()
            except Exception:
                pass
        self._player_media_path = ""
        self.queue = [self._normalise_track(track) for track in tracks]
        self.queue_index = 0
        self.history = [0]
        self.history_pos = 0
        self.shuffle_enabled = False
        self.player_bar.set_shuffle(False)
        track = self.queue[0]
        path = resolve_track_path(track, self.music_folder)
        if not path or not Path(path).is_file():
            return False

        track["file_path"] = path
        self.current_path = path
        self.current_track = track
        self.track_duration = int(track.get("duration_ms") or 0)
        self.player_bar.set_track(track)
        self.player_bar.set_playing(False)
        self.player_bar.set_queue(self.queue, self.queue_index)
        self.player_bar.set_progress(0, self.track_duration)
        self.home.set_now_playing(track, playing=False)
        self.album_page.track_list.set_playing_path(path)
        self.library.set_playing(path)
        self.settings["last_track_rel"] = track.get("relative_path", "")
        self._settings_timer.start()
        return True

    def _tick(self) -> None:
        if not self.current_path or self.player is None:
            return
        try:
            state = self.player.get_state()
            if state == vlc.State.Ended:
                self._finish_listening_session(completed=True)
                self.play_next()
                return

            current = max(0, self.player.get_time())
            total = self.player.get_length()
            if total <= 0:
                total = self.track_duration
            self.player_bar.set_progress(current, total)
            is_playing = state == vlc.State.Playing
            self.player_bar.set_playing(is_playing)

            if self._listening_active and is_playing:
                now = time.monotonic()
                if self._listening_last_sample is not None:
                    elapsed_ms = int((now - self._listening_last_sample) * 1000)
                    # Ignore long UI stalls rather than inflating listening time.
                    if 0 < elapsed_ms <= 1500:
                        self._listening_session_ms += elapsed_ms
                self._listening_last_sample = now
                if self._listening_session_ms - self._listening_last_flush_ms >= 5000:
                    # Never let periodic history writes wait on SQLite from the GUI.
                    self._session_writer.update_session(
                        self._listening_generation, self._listening_session_ms
                    )
                    self._listening_last_flush_ms = self._listening_session_ms
                    total_listened = self._listening_base_ms + self._listening_session_ms
                    if self.stack.currentWidget() is self.history_page:
                        self.history_page.refresh()
            else:
                self._listening_last_sample = None
        except Exception:
            pass

    # ---------------------------------------------------------------- favorites
    def on_favorite_changed(self, track_id: int, value: bool) -> None:
        if track_id:
            try:
                set_favorite(track_id, value)
            except Exception:
                pass

    # ---------------------------------------------------------------- utility
    def _open_current_folder(self) -> None:
        if not self.current_path:
            return
        try:
            from PyQt6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.current_path).parent)))
        except Exception:
            pass

    def _open_current_album(self) -> None:
        if not self.current_track:
            return
        # Находим альбом по совпадению названия + исполнителя.
        conn = db_connect()
        try:
            row = conn.execute(
                "SELECT id FROM albums WHERE artist=? AND album_name=? LIMIT 1",
                (self.current_track.get("artist"), self.current_track.get("album")),
            ).fetchone()
        finally:
            conn.close()
        if row:
            self.show_album(int(row[0]))

    # ---------------------------------------------------------------- keyboard
    def keyPressEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_K:
                self.search_header.edit.setFocus()
                self.search_header.edit.selectAll()
                return
            if event.key() == Qt.Key.Key_O:
                self.choose_music_folder()
                return
            if event.key() == Qt.Key.Key_R:
                self.start_scan()
                return

        if event.key() == Qt.Key.Key_Space:
            self.toggle_play_pause()
            return
        if event.key() == Qt.Key.Key_Left:
            try:
                current = max(0, self.player.get_time() - 5000)
                self.player.set_time(current)
            except Exception:
                pass
            return
        if event.key() == Qt.Key.Key_Right:
            try:
                current = max(0, self.player.get_time() + 5000)
                self.player.set_time(current)
            except Exception:
                pass
            return
        if event.key() == Qt.Key.Key_Up:
            self.player_bar.volume_slider.setValue(min(100, self.player_bar.volume_slider.value() + 5))
            return
        if event.key() == Qt.Key.Key_Down:
            self.player_bar.volume_slider.setValue(max(0, self.player_bar.volume_slider.value() - 5))
            return
        super().keyPressEvent(event)

    # ---------------------------------------------------------------- close
    def closeEvent(self, event) -> None:
        try:
            self.settings["shuffle"] = self.shuffle_enabled
            self.settings["repeat_mode"] = self.repeat_mode
            if self.current_track:
                self.settings["last_track_rel"] = self.current_track.get("relative_path", "")
            if not self.isMaximized():
                geometry = self.geometry()
                self.settings["geometry"] = {
                    "x": geometry.x(),
                    "y": geometry.y(),
                    "w": geometry.width(),
                    "h": geometry.height(),
                }
            save_settings(self.settings)
            self.playback_timer.stop()
            self._finish_listening_session(completed=False)
            self._session_writer.wait_for_idle(timeout=0.35)
            self._session_writer.shutdown()
            if self.player is not None:
                self.player.stop()
            if self.vlc_instance is not None:
                self.vlc_instance.release()
        except Exception:
            pass
        super().closeEvent(event)

    # ---------------------------------------------------------------- style
    def _apply_stylesheet(self) -> None:
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background: {BG};
                color: {TEXT};
                font-family: "Segoe UI";
                font-size: 13px;
            }}
            QWidget {{
                background-color: transparent;
                color: {TEXT};
                font-family: "Segoe UI";
                font-size: 13px;
            }}
            QLabel {{
                background: transparent;
            }}
            QToolTip {{
                background: #262628;
                color: white;
                border: 1px solid #3a3a3d;
                border-radius: 8px;
                padding: 7px 9px;
            }}
            #Sidebar {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #111114, stop:1 #0c0c0f);
                border-right: 1px solid #202023;
            }}
            #LogoMark {{
                color: {ACCENT};
                font-size: 28px;
                font-weight: 700;
            }}
            #LogoText {{
                color: white;
                font-weight: 700;
                letter-spacing: 2.2px;
                font-size: 13px;
            }}
            #SidebarSub {{ color: {DIM}; font-size: 10px; }}
            #SidebarCaption {{
                color: #65656a;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1.4px;
                padding: 0 10px 5px 10px;
            }}
            #SidebarPath {{
                color: #6f6f74;
                font-size: 9px;
                padding: 7px 10px 0 42px;
            }}
            #SidebarStatus {{
                color: #5f8c36;
                font-size: 9px;
                padding: 0 10px;
            }}
            #SideButton {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 11px;
                color: #96969c;
                text-align: left;
                padding: 0 14px;
            }}
            #SideButton:hover {{
                background: #171719;
                border-color: #242427;
                color: white;
            }}
            #SideButton:checked {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2b281b, stop:1 #1c1b17);
                border-color: #3a3520;
                color: {ACCENT};
                font-weight: 650;
            }}
            #Content {{ background: transparent; }}
            #PlayerBarContainer, #LibraryPage {{ background: transparent; }}
            #SearchIcon {{ color: #77777c; font-size: 22px; }}
            #SearchField {{
                background: #1c1c21;
                border: 1px solid transparent;
                border-radius: 20px;
                color: white;
                padding: 0 16px;
                selection-background-color: #3a3a3d;
                font-size: 13px;
            }}
            #SearchField:hover {{ background: #1b1b1c; }}
            #SearchField:focus {{
                background: #1c1c1f;
                border-color: #4a4429;
            }}
            #HeaderStatus {{ color: #66666b; font-size: 10px; min-width: 70px; }}
            #PageTitle {{ font-size: 29px; font-weight: 800; color: white; }}
            #PageTitleLarge {{ font-size: 32px; font-weight: 800; color: white; }}
            #SectionTitle {{ font-size: 19px; font-weight: 750; color: white; }}
            #MutedText {{ color: #b9b9bf; font-size: 11px; }}
            #DimText {{ color: #8d8d94; font-size: 11px; }}
            #AccentCaption {{
                color: {ACCENT};
                font-size: 9px;
                font-weight: 800;
                letter-spacing: 1.3px;
            }}
            #HeroTitle {{ font-size: 26px; font-weight: 800; color: white; }}
            #HeroArtist {{ font-size: 14px; font-weight: 650; color: #ececef; }}
            #HeroMeta {{ color: #b1b1b5; font-size: 11px; }}
            #HeroMetaSecondary {{ color: #77777c; font-size: 10px; }}
            #NowPlayingHero {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #302b1d, stop:0.38 #222124, stop:1 #19191d);
                border: 1px solid #494126;
                border-radius: 22px;
            }}
            #AlbumHero {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #2c291e, stop:0.48 #202024, stop:1 #18181c);
                border: 1px solid #443d27;
                border-radius: 22px;
            }}
            #AlbumCard {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #222227, stop:1 #19191e);
                border: 1px solid #393941;
                border-radius: 20px;
            }}
            #AlbumCard:hover {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #302c1c, stop:1 #222227);
                border-color: #6a5d27;
            }}
            #AlbumCard QLabel {{
                background: transparent;
            }}
            #AlbumPlayButton {{
                background: {ACCENT};
                color: #161616;
                border: none;
                border-radius: 24px;
                font-size: 17px;
                font-weight: 800;
            }}
            #AlbumPlayButton:hover {{ background: {ACCENT_HOVER}; }}
            #AccentButton {{
                background: {ACCENT};
                color: #181818;
                border: none;
                border-radius: 12px;
                padding: 10px 17px;
                font-weight: 750;
            }}
            #AccentButton:hover {{ background: {ACCENT_HOVER}; }}
            #AccentButton:pressed {{ background: {ACCENT_PRESSED}; }}
            #WaveButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #ffeb53, stop:0.55 #ffd928, stop:1 #ffb94b);
                color: #19160a;
                border: 1px solid #fff07b;
                border-radius: 13px;
                padding: 10px 16px;
                font-weight: 800;
            }}
            #WaveButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #fff28b, stop:0.55 #ffe761, stop:1 #ffc976);
                border-color: #fff8ba;
            }}
            #WaveButton:pressed {{ background: #e8b820; }}
            #GhostButton {{
                background: #252528;
                color: #f4f4f5;
                border: 1px solid #303034;
                border-radius: 12px;
                padding: 9px 14px;
            }}
            #GhostButton:hover {{
                background: #34332b;
                border-color: #4b4323;
                color: {ACCENT_HOVER};
            }}
            #Combo, #QueueCombo {{
                background: #181819;
                color: #e9e9eb;
                border: 1px solid #28282a;
                border-radius: 10px;
                padding: 6px 10px;
                min-height: 28px;
            }}
            #Combo:hover, #QueueCombo:hover {{ border-color: #38383b; }}
            #Combo QAbstractItemView, #QueueCombo QAbstractItemView {{
                background: #1b1b1d;
                color: white;
                border: 1px solid #323235;
                selection-background-color: #2b2b2e;
                selection-color: {ACCENT};
            }}
            #IconButton {{
                background: transparent;
                color: #9a9a9f;
                border: none;
                border-radius: 19px;
            }}
            #IconButton:hover {{ background: #242426; color: white; }}
            #IconButton:checked {{ background: #2a2926; color: {ACCENT}; }}
            #IconButton[repeatActive="true"] {{ color: {ACCENT}; }}
            #IconButton[repeatOne="true"] {{ background: #2a2926; color: {ACCENT}; }}
            #PlayButton {{
                background: {ACCENT};
                color: #131313;
                border: 1px solid #f2d300;
                border-radius: 25px;
                font-size: 18px;
                font-weight: 800;
            }}
            #PlayButton:hover {{ background: {ACCENT_HOVER}; }}
            #PlayButton:pressed {{ background: {ACCENT_PRESSED}; }}
            #TableHeader {{ color: #646469; font-size: 9px; text-transform: uppercase; letter-spacing: .7px; }}
            #TrackRow {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 12px;
            }}
            #TrackRow:hover {{
                background: #222227;
                border-color: #35353b;
            }}
            #TrackRow[playing="true"] {{
                background: #2a271a;
                border-color: #665b27;
            }}
            #RowNum {{ color: #85858c; font-size: 11px; }}
            #RowYear {{ color: #85858c; font-size: 10px; }}
            #RowListened {{ color: #a0a0a7; font-size: 11px; }}
            #TrackRow[playing="true"] #RowListened {{ color: {ACCENT}; font-weight: 650; }}
            #RowDuration {{ color: #b0b0b6; font-size: 11px; }}
            #PlayerBar {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #28261c, stop:0.20 #1d1d20, stop:0.72 #19191d, stop:1 #211e14);
                border: 1px solid #554a26;
                border-radius: 18px;
            }}
            #ShuffleButton {{
                background: transparent;
                color: #c1bfae;
                border: 1px solid transparent;
                border-radius: 18px;
                font-weight: 700;
            }}
            #ShuffleButton:hover {{
                background: #2c2819;
                border-color: #514522;
                color: {ACCENT_HOVER};
            }}
            #ShuffleButton:checked {{
                background: {ACCENT};
                color: #17150b;
                border: 1px solid #fff078;
            }}
            #ShuffleButton:checked:hover {{ background: {ACCENT_HOVER}; }}
            #TinyText {{ color: #808085; font-size: 9px; }}
            #VolumeIcon {{ color: #8a8a90; font-size: 15px; }}
            #ProgressSlider {{
                min-height: 24px;
                padding: 0;
            }}
            #ProgressSlider::groove:horizontal {{
                background: #39383a;
                border: 1px solid #514b35;
                height: 6px;
                border-radius: 4px;
            }}
            #ProgressSlider::sub-page:horizontal {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #e4c900, stop:1 {ACCENT_HOVER});
                height: 6px;
                border-radius: 4px;
            }}
            #ProgressSlider::handle:horizontal {{
                background: {ACCENT};
                border: 2px solid #141416;
                width: 16px;
                height: 16px;
                margin: -6px 0;
                border-radius: 9px;
            }}
            #ProgressSlider:hover::handle:horizontal {{
                background: {ACCENT_HOVER};
                border-color: #222224;
            }}
            #ProgressSlider:disabled::handle:horizontal {{
                background: #67676b;
            }}
            #VolumeSlider::groove:horizontal {{ background: #2c2c2f; height: 4px; border-radius: 2px; }}
            #VolumeSlider::sub-page:horizontal {{ background: #bdbdc1; height: 4px; border-radius: 2px; }}
            #VolumeSlider::handle:horizontal {{ background: white; width: 10px; height: 10px; margin: -3px 0; border-radius: 5px; }}
            #ScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{ background: transparent; width: 7px; margin: 2px; }}
            QScrollBar::handle:vertical {{ background: #303034; border-radius: 4px; min-height: 45px; }}
            QScrollBar::handle:vertical:hover {{ background: #47474b; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            #HistoryMetricCard {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #302d20, stop:1 #1f1f24);
                border: 1px solid #494126;
                border-radius: 18px;
            }}
            #HistoryMetricCaption {{
                color: #aaaaaf;
                font-size: 10px;
                font-weight: 750;
                letter-spacing: 1px;
            }}
            #HistoryMetricValue {{
                color: {ACCENT_HOVER};
                font-size: 24px;
                font-weight: 800;
            }}
            #EmptyState {{
                color: #a0a0a7;
                background: #161619;
                border: 1px dashed #343439;
                border-radius: 16px;
                padding: 26px;
            }}
            QTableWidget#HistoryTable {{
                background: #19191e;
                alternate-background-color: #202025;
                color: #f4f4f5;
                border: 1px solid #36363e;
                border-radius: 14px;
                padding: 4px;
                selection-background-color: #4a4123;
                selection-color: #ffffff;
                outline: 0;
            }}
            QTableWidget#HistoryTable::item {{
                padding: 8px 9px;
                border-bottom: 1px solid #242428;
            }}
            QTableWidget#HistoryTable::item:selected {{
                background: #40391f;
                color: #ffffff;
            }}
            QHeaderView::section {{
                background: #242429;
                color: #c1c1c8;
                border: none;
                border-bottom: 1px solid #45454d;
                padding: 11px 10px;
                font-size: 10px;
                font-weight: 700;
            }}
            #StatusBar {{ background: #111114; color: #a0a0a8; border-top: 1px solid #303036; }}
            #ScanOverlay {{ background: rgba(8, 8, 9, 230); }}
            #ScanCard {{ background: #19191b; border: 1px solid #333336; border-radius: 18px; }}
            #ScanTitle {{ font-size: 21px; font-weight: 800; }}
            QProgressBar {{
                background: #29292b;
                border: none;
                border-radius: 5px;
                text-align: center;
                height: 10px;
                color: #151515;
            }}
            QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
            QMenu {{
                background: #1b1b1d;
                color: white;
                border: 1px solid #353538;
                padding: 5px;
            }}
            QMenu::item {{ padding: 8px 22px; border-radius: 6px; }}
            QMenu::item:selected {{ background: #2c2c2f; color: {ACCENT}; }}
            """
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    try:
        window = MainWindow()
        window.showMaximized()
        return app.exec()
    except Exception as exc:
        QMessageBox.critical(None, "Ошибка запуска Local Music", str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
