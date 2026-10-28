import os
import sqlite3
from pathlib import Path
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3

def scan_music_directory():
    db_path = "music_collection.db"
    music_base_dir = Path(r"D:\Music")

    print("=== ЗАПУСК ВСЕЯДНОГО СКАНЕРА 3.0 (.MP3 + .M4A) ===")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Сбрасываем старые таблицы для полной пересборки базы
    cursor.execute("DROP TABLE IF EXISTS tracks")
    cursor.execute("DROP TABLE IF EXISTS albums")

    # Создаем таблицы заново
    cursor.execute("""
        CREATE TABLE albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist TEXT NOT NULL,
            album_name TEXT NOT NULL,
            year INTEGER DEFAULT NULL,
            UNIQUE(artist, album_name)
        )
    """)

    cursor.execute("""
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            album_id INTEGER,
            track_name TEXT NOT NULL,
            genre TEXT DEFAULT NULL,
            file_path TEXT NOT NULL UNIQUE,
            FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE
        )
    """)
    conn.commit()

    success_count = 0

    # Запускаем обход подпапок
    for root, dirs, files in os.walk(music_base_dir):
        for file_name in files:
            ext = file_name.lower().rsplit('.', 1)[-1]
            if ext not in ['mp3', 'm4a']:
                continue

            track_path = os.path.join(root, file_name)
            path_obj = Path(track_path)

            # --- ПЛАН Б: Извлечение данных из структуры папок ---
            try:
                rel_parts = path_obj.relative_to(music_base_dir).parts
                artist_from_path = rel_parts[0] if len(rel_parts) > 1 else "Разные исполнители"
                album_from_path = path_obj.parent.name if path_obj.parent.name != artist_from_path else "Синглы"
            except Exception:
                artist_from_path = "Неизвестный исполнитель"
                album_from_path = "Неизвестный альбом"

            title_from_path = file_name.rsplit('.', 1)[0]

            # --- ПЛАН А: Чтение тегов в зависимости от формата файла ---
            artist = artist_from_path
            album = album_from_path
            title = title_from_path
            genre = "Разное"
            year = None

            try:
                if ext == 'm4a':
                    # Для формата M4A (MP4 контейнер) теги имеют другие ключи
                    audio = MP4(track_path)
                    if audio.tags:
                        if '\xa9ART' in audio.tags and audio.tags['\xa9ART']:
                            artist = str(audio.tags['\xa9ART'][0]).strip()
                        if '\xa9alb' in audio.tags and audio.tags['\xa9alb']:
                            album = str(audio.tags['\xa9alb'][0]).strip()
                        if '\xa9nam' in audio.tags and audio.tags['\xa9nam']:
                            title = str(audio.tags['\xa9nam'][0]).strip()
                        if '\xa9gen' in audio.tags and audio.tags['\xa9gen']:
                            genre = str(audio.tags['\xa9gen'][0]).strip()
                        if '\xa9day' in audio.tags and audio.tags['\xa9day']:
                            try: year = int(str(audio.tags['\xa9day'][0])[:4])
                            except: year = None
                elif ext == 'mp3':
                    # Для стандартного MP3
                    audio = MP3(track_path)
                    if audio.tags:
                        if 'TPE1' in audio.tags and str(audio.tags['TPE1'].text).strip():
                            artist = str(audio.tags['TPE1'].text).strip()
                        if 'TALB' in audio.tags and str(audio.tags['TALB'].text).strip():
                            album = str(audio.tags['TALB'].text).strip()
                        if 'TIT2' in audio.tags and str(audio.tags['TIT2'].text).strip():
                            title = str(audio.tags['TIT2'].text).strip()
                        if 'TCON' in audio.tags and str(audio.tags['TCON'].text).strip():
                            genre = str(audio.tags['TCON'].text).strip()
                        if 'TDRC' in audio.tags and str(audio.tags['TDRC'].text).strip():
                            try: year = int(str(audio.tags['TDRC'].text)[:4])
                            except: year = None
            except Exception:
                # Если теги битые — оставляем данные из структуры папок
                pass

            # Финальная страховка от пустых строк
            if not artist: artist = artist_from_path
            if not album: album = album_from_path
            if not title: title = title_from_path

            # --- ЗАПИСЬ В БАЗУ ДАННЫХ ---
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO albums (artist, album_name, year) 
                    VALUES (?, ?, ?)
                """, (artist, album, year))

                cursor.execute("SELECT id FROM albums WHERE artist = ? AND album_name = ?", (artist, album))
                album_id_row = cursor.fetchone()
                album_id = album_id_row[0] if album_id_row else None

                cursor.execute("""
                    INSERT OR IGNORE INTO tracks (album_id, track_name, genre, file_path) 
                    VALUES (?, ?, ?, ?)
                """, (album_id, title, genre, track_path))

                success_count += 1
                if success_count % 500 == 0:
                    print(f"Успешно обработано треков... {success_count}")

            except sqlite3.Error:
                continue

        conn.commit()

    conn.close()
    print("-" * 60)
    print("=== СКАНИРОВАНИЕ ЗАВЕРШЕНО ===")
    print(f"Всего треков добавлено в вашу базу данных: {success_count}")

if __name__ == "__main__":
    scan_music_directory()
