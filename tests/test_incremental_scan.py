import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

import main


class IncrementalScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.music = self.root / "library"
        self.music.mkdir()

        self.old_db_path = main.DB_PATH
        self.old_cover_dir = main.COVER_CACHE_DIR
        main.DB_PATH = self.root / "state" / "library.sqlite3"
        main.COVER_CACHE_DIR = self.root / "state" / "covers"
        main.COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        main.DB_PATH = self.old_db_path
        main.COVER_CACHE_DIR = self.old_cover_dir
        self.temp.cleanup()

    def make_track(self, folder: Path, name: str, data: bytes = b"not a complete audio file") -> Path:
        album = folder / "Test Artist" / "Test Album"
        album.mkdir(parents=True, exist_ok=True)
        track = album / name
        track.write_bytes(data)
        return track

    def run_scan(self, folder: Path):
        task = main.ScanTask(folder)
        errors = []
        finished = []
        task.signals.error.connect(errors.append)
        task.signals.finished.connect(lambda success, failed: finished.append((success, failed)))
        task.run()

        self.assertEqual(errors, [], f"Scanner emitted errors: {errors}")
        self.assertEqual(len(finished), 1, "Scanner did not emit exactly one finished signal")
        return finished[0]

    def get_tracks(self):
        conn = sqlite3.connect(main.DB_PATH)
        try:
            return conn.execute(
                "SELECT file_path, track_name, file_size, mtime FROM tracks ORDER BY file_path"
            ).fetchall()
        finally:
            conn.close()

    def test_unchanged_tracks_skip_metadata_parsing(self):
        track = self.make_track(self.music, "one.mp3")
        self.assertEqual(self.run_scan(self.music), (1, 0))
        self.assertEqual(len(self.get_tracks()), 1)

        # If a fingerprint matches, parse_media must not be called again.
        with mock.patch.object(
            main.ScanTask,
            "parse_media",
            side_effect=AssertionError("unchanged file was parsed again"),
        ):
            self.assertEqual(self.run_scan(self.music), (1, 0))

        self.assertEqual(len(self.get_tracks()), 1)
        self.assertEqual(self.get_tracks()[0][0], str(track.resolve()))

    def test_new_and_removed_files_are_synchronized(self):
        first = self.make_track(self.music, "first.mp3")
        self.assertEqual(self.run_scan(self.music), (1, 0))

        first.unlink()
        second = self.make_track(self.music, "second.flac", b"not a complete FLAC file")
        self.assertEqual(self.run_scan(self.music), (1, 0))

        tracks = self.get_tracks()
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0][0], str(second.resolve()))
        self.assertNotEqual(tracks[0][0], str(first.resolve()))

    def test_changed_file_is_reindexed(self):
        track = self.make_track(self.music, "changing.mp3", b"first")
        self.assertEqual(self.run_scan(self.music), (1, 0))
        old_size = self.get_tracks()[0][2]

        track.write_bytes(b"changed file payload with a different length")
        self.assertEqual(self.run_scan(self.music), (1, 0))

        rows = self.get_tracks()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0][2], old_size)

    def test_switching_root_replaces_library_transactionally(self):
        self.make_track(self.music, "old.mp3")
        self.assertEqual(self.run_scan(self.music), (1, 0))

        another = self.root / "other-library"
        new_track = self.make_track(another, "new.m4a")
        self.assertEqual(self.run_scan(another), (1, 0))

        rows = self.get_tracks()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], str(new_track.resolve()))

    def test_missing_root_does_not_destroy_existing_index(self):
        self.make_track(self.music, "keep.mp3")
        self.assertEqual(self.run_scan(self.music), (1, 0))
        before = self.get_tracks()

        self.assertFalse(self.music.joinpath("missing").exists())
        self.run_scan_missing = main.ScanTask(self.music / "missing")
        errors = []
        self.run_scan_missing.signals.error.connect(errors.append)
        self.run_scan_missing.run()

        self.assertEqual(len(errors), 1)
        self.assertEqual(self.get_tracks(), before)


    def test_album_query_task_returns_cached_album_summaries(self):
        self.make_track(self.music, "sample.mp3")
        self.assertEqual(self.run_scan(self.music), (1, 0))

        task = main.AlbumQueryTask(17, "Исполнитель", False)
        results = []
        task.signals.finished.connect(
            lambda generation, albums, error: results.append((generation, albums, error))
        )
        task.run()

        self.assertEqual(len(results), 1)
        generation, albums, error = results[0]
        self.assertEqual(generation, 17)
        self.assertEqual(error, "")
        self.assertEqual(len(albums), 1)
        self.assertEqual(albums[0]["album"], "Test Album")
        # The fake MP3 has no ID3 tags, so the scanner uses the folder fallback.
        self.assertEqual(albums[0]["artist"], "Разные исполнители")

    def test_unset_music_folder_is_not_treated_as_current_directory(self):
        self.assertIsNone(main.configured_music_folder({}))
        self.assertIsNone(main.configured_music_folder({"music_folder": ""}))
        self.assertIsNone(main.configured_music_folder({"music_folder": "   "}))
        configured = main.configured_music_folder({"music_folder": str(self.music)})
        self.assertEqual(configured, self.music)

    def test_cover_memory_cache_uses_byte_budget_and_lru(self):
        cache = main.CoverMemoryCache(max_bytes=1024)
        image_a = main.QImage(10, 10, main.QImage.Format.Format_RGBA8888)
        image_b = main.QImage(10, 10, main.QImage.Format.Format_RGBA8888)
        image_c = main.QImage(10, 10, main.QImage.Format.Format_RGBA8888)

        self.assertFalse(image_a.isNull())
        cache.put("a.jpg", 10, image_a)
        cache.put("b.jpg", 10, image_b)
        self.assertIsNotNone(cache.get("a.jpg", 10))  # A becomes most recently used.
        cache.put("c.jpg", 10, image_c)              # B should be evicted.

        self.assertIsNotNone(cache.get("a.jpg", 10))
        self.assertIsNone(cache.get("b.jpg", 10))
        self.assertIsNotNone(cache.get("c.jpg", 10))
        self.assertLessEqual(cache._total_bytes, cache.max_bytes)

    def test_legacy_schema_migrates_before_indexes_created(self):
        conn = sqlite3.connect(main.DB_PATH)
        try:
            conn.executescript(
                """
                CREATE TABLE albums (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist TEXT NOT NULL,
                    album_name TEXT NOT NULL,
                    year INTEGER DEFAULT NULL,
                    UNIQUE(artist, album_name)
                );
                CREATE TABLE tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_id INTEGER,
                    track_name TEXT NOT NULL,
                    genre TEXT DEFAULT 'Разное',
                    file_path TEXT NOT NULL UNIQUE,
                    FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

        # Previous releases may have tables without relative_path/duration/file_size/mtime.
        # Schema migration must add those columns before adding dependent indexes.
        main.ensure_database_schema()

        conn = sqlite3.connect(main.DB_PATH)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(tracks)")}
        finally:
            conn.close()

        self.assertTrue(
            {"relative_path", "duration_ms", "file_size", "mtime", "favorite"} <= columns
        )
        self.assertIn("idx_tracks_duration", indexes)
        self.assertIn("idx_tracks_relative_path", indexes)


    def test_listening_sessions_persist_time_and_rank_tracks(self):
        self.make_track(self.music, "first.mp3")
        self.make_track(self.music, "second.mp3")
        self.assertEqual(self.run_scan(self.music), (2, 0))

        tracks = main.fetch_all_tracks(self.music, "Название", False)
        by_name = {track["track_name"]: track for track in tracks}
        first_session = main.start_listening_session(by_name["first"])
        second_session = main.start_listening_session(by_name["second"])
        self.assertIsNotNone(first_session)
        self.assertIsNotNone(second_session)
        main.update_listening_session(first_session, 120_000, finished=True)
        main.update_listening_session(second_session, 300_000, finished=True, completed=True)

        summary = main.fetch_listening_summary()
        self.assertEqual(summary["listened_ms"], 420_000)
        self.assertEqual(summary["sessions"], 2)
        self.assertEqual(summary["tracks"], 2)

        ranked = main.fetch_all_tracks(self.music, "Время прослушивания", True)
        self.assertEqual(ranked[0]["track_name"], "second")
        self.assertEqual(ranked[0]["listened_ms"], 300_000)
        self.assertEqual(ranked[1]["track_name"], "first")
        self.assertEqual(ranked[1]["listened_ms"], 120_000)

        history = main.fetch_listening_view("История")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["title"], "second")
        self.assertTrue(history[0]["completed"])

        artists = main.fetch_listening_view("Исполнители")
        self.assertEqual(len(artists), 1)
        self.assertEqual(artists[0]["listened_ms"], 420_000)

        albums = main.fetch_listening_view("Альбомы")
        self.assertEqual(len(albums), 1)
        self.assertEqual(albums[0]["listened_ms"], 420_000)

    def test_album_default_sort_is_most_tracks_first(self):
        self.make_track(self.music, "single.mp3")
        larger_album = self.music / "Test Artist" / "Album With More Tracks"
        larger_album.mkdir(parents=True)
        (larger_album / "one.mp3").write_bytes(b"fake audio 1")
        (larger_album / "two.mp3").write_bytes(b"fake audio 2")
        self.assertEqual(self.run_scan(self.music), (3, 0))

        albums = main.fetch_albums()
        self.assertGreaterEqual(len(albums), 2)
        self.assertEqual(albums[0]["album"], "Album With More Tracks")
        self.assertEqual(albums[0]["track_count"], 2)
        self.assertEqual(albums[1]["track_count"], 1)

    def test_listening_time_format_is_readable(self):
        self.assertEqual(main.format_listening_ms(0), "—")
        self.assertEqual(main.format_listening_ms(30_000), "< 1 мин")
        self.assertEqual(main.format_listening_ms(45 * 60_000), "45 мин")
        self.assertEqual(main.format_listening_ms(3_660_000), "1 ч 01 мин")
        self.assertEqual(main.format_listening_ms(7_200_000), "2 ч")

    def test_local_wave_uses_all_tracks_and_avoids_consecutive_artists(self):
        tracks = [
            {"track_name": "A1", "artist": "Artist A", "listened_ms": 0, "favorite": False},
            {"track_name": "A2", "artist": "Artist A", "listened_ms": 300_000, "favorite": True},
            {"track_name": "B1", "artist": "Artist B", "listened_ms": 420_000, "favorite": False},
            {"track_name": "C1", "artist": "Artist C", "listened_ms": 30_000, "favorite": False},
        ]
        wave = main.build_local_wave(
            tracks,
            {"artist a": 600_000, "artist b": 900_000, "artist c": 60_000},
        )

        self.assertEqual({track["track_name"] for track in wave}, {"A1", "A2", "B1", "C1"})
        self.assertEqual(len(wave), len(tracks))
        for previous, current in zip(wave, wave[1:]):
            self.assertNotEqual(previous["artist"], current["artist"])


    def test_listening_session_writer_serializes_database_writes(self):
        track_path = self.make_track(self.music, "writer-test.mp3")
        main.ensure_database_schema()
        track = {
            "file_path": str(track_path.resolve()),
            "track_name": "writer-test",
            "artist": "Test Artist",
            "album": "Test Album",
        }

        writer = main.ListeningSessionWriter()
        try:
            writer.start_session(101, track)
            writer.update_session(101, 4_500)
            writer.finish_session(101, 7_250, completed=True)
            self.assertTrue(writer.wait_for_idle(timeout=3.0))

            summary = main.fetch_listening_summary()
            self.assertEqual(summary["sessions"], 1)
            self.assertEqual(summary["listened_ms"], 7_250)

            history = main.fetch_listening_view("История")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["listened_ms"], 7_250)
            self.assertTrue(history[0]["completed"])
        finally:
            writer.shutdown()
            writer._thread.join(timeout=1.0)


    def test_history_aggregates_repeat_plays_and_orders_artists_by_recency(self):
        main.ensure_database_schema()

        repeated_track = {
            "file_path": str((self.root / "shared-track.mp3").resolve()),
            "track_name": "Song A",
            "artist": "Artist A",
            "album": "Album A",
        }
        other_track = {
            "file_path": str((self.root / "other-track.mp3").resolve()),
            "track_name": "Song B",
            "artist": "Artist B",
            "album": "Album B",
        }
        frequent_track = {
            "file_path": str((self.root / "frequent-track.mp3").resolve()),
            "track_name": "Song C",
            "artist": "Artist A",
            "album": "Album A",
        }

        first = main.start_listening_session(repeated_track)
        main.update_listening_session(first, 600_000, finished=True)
        second = main.start_listening_session(repeated_track)
        main.update_listening_session(second, 600_000, finished=True, completed=True)
        third = main.start_listening_session(other_track)
        main.update_listening_session(third, 300_000, finished=True)
        frequent_sessions = []
        for duration in (30_000, 60_000, 90_000):
            session = main.start_listening_session(frequent_track)
            main.update_listening_session(session, duration, finished=True)
            frequent_sessions.append(session)

        # Simulate older sessions and a recent replay of the same track.
        conn = sqlite3.connect(main.DB_PATH)
        try:
            for session_id, timestamp in (
                (first, 100.0),
                (second, 300.0),
                (third, 200.0),
                (frequent_sessions[0], 110.0),
                (frequent_sessions[1], 150.0),
                (frequent_sessions[2], 250.0),
            ):
                conn.execute(
                    "UPDATE listening_sessions SET started_at=? WHERE id=?",
                    (timestamp, session_id),
                )
            conn.commit()
        finally:
            conn.close()

        history = main.fetch_listening_view("История")
        song_a = [row for row in history if row["title"] == "Song A"]
        self.assertEqual(len(song_a), 1)
        self.assertEqual(song_a[0]["sessions"], 2)
        self.assertEqual(song_a[0]["listened_ms"], 1_200_000)
        self.assertEqual(song_a[0]["last_played"], 300.0)

        artists = main.fetch_listening_view("Исполнители")
        self.assertEqual([row["title"] for row in artists], ["Artist A", "Artist B"])
        self.assertEqual(artists[0]["listened_ms"], 1_380_000)
        self.assertEqual(artists[0]["last_played"], 300.0)

        artist_tracks = main.fetch_artist_track_history("Artist A")
        self.assertEqual(len(artist_tracks), 2)
        # "Song C" has more separate plays, so it ranks above the longer,
        # but less frequently played "Song A".
        self.assertEqual(artist_tracks[0]["title"], "Song C")
        self.assertEqual(artist_tracks[0]["sessions"], 3)
        self.assertEqual(artist_tracks[0]["listened_ms"], 180_000)
        self.assertEqual(artist_tracks[1]["title"], "Song A")
        self.assertEqual(artist_tracks[1]["sessions"], 2)
        self.assertEqual(artist_tracks[1]["listened_ms"], 1_200_000)
        self.assertEqual(artist_tracks[1]["last_played"], 300.0)

    def test_track_cover_resolver_extracts_and_caches_embedded_art(self):
        track = self.make_track(self.music, "embedded-cover.mp3")
        image_buffer = BytesIO()
        main.Image.new("RGB", (40, 40), (230, 180, 20)).save(
            image_buffer, format="JPEG"
        )
        artwork = image_buffer.getvalue()

        with mock.patch.object(
            main, "extract_embedded_cover", return_value=artwork
        ) as extract:
            first = main.resolve_track_cover_path(str(track))
            second = main.resolve_track_cover_path(str(track))

        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertTrue(Path(first).is_file())
        self.assertGreater(Path(first).stat().st_size, 100)
        extract.assert_called_once_with(str(track.resolve()))

    def test_track_cover_resolver_uses_nearby_cover_when_no_embedded_art(self):
        track = self.make_track(self.music, "sidecar-cover.mp3")
        sidecar = track.parent / "cover.jpg"
        main.Image.new("RGB", (40, 40), (25, 80, 200)).save(
            sidecar, format="JPEG"
        )

        with mock.patch.object(
            main, "extract_embedded_cover", return_value=None
        ) as extract:
            cover_path = main.resolve_track_cover_path(str(track))
            cover_path_again = main.resolve_track_cover_path(str(track))

        self.assertEqual(Path(cover_path).resolve(), sidecar.resolve())
        self.assertEqual(cover_path_again, cover_path)
        extract.assert_called_once_with(str(track.resolve()))

    def test_track_cover_resolver_falls_back_to_album_cover(self):
        track = self.make_track(self.music, "no-cover.mp3")
        fallback = self.root / "album-cover.jpg"
        main.Image.new("RGB", (40, 40), (30, 180, 70)).save(
            fallback, format="JPEG"
        )

        with mock.patch.object(
            main, "extract_embedded_cover", return_value=None
        ):
            cover_path = main.resolve_track_cover_path(str(track), str(fallback))

        self.assertEqual(cover_path, str(fallback))



    def test_folder_cover_lookup_finds_nonstandard_image_filename(self):
        track = self.make_track(self.music, "track-with-art.mp3")
        artwork = track.parent / "Artist Album Cover.jpeg"
        main.Image.new("RGB", (40, 40), (200, 145, 20)).save(artwork, format="JPEG")

        found = main.find_folder_cover_path(track.parent)

        self.assertIsNotNone(found)
        self.assertEqual(found.resolve(), artwork.resolve())

    def test_track_cover_resolver_ignores_legacy_negative_cache(self):
        track = self.make_track(self.music, "legacy-no-art.mp3")
        stat = track.stat()
        old_identity = (
            f"{__import__('os').path.normcase(str(track.resolve()))}|"
            f"{stat.st_size}|{stat.st_mtime_ns}|"
        )
        old_key = main.hashlib.sha1(
            old_identity.encode("utf-8", "surrogatepass")
        ).hexdigest()
        track_cache = main.COVER_CACHE_DIR / "track-art"
        track_cache.mkdir(parents=True, exist_ok=True)
        (track_cache / f"{old_key}.none").write_text(
            "old negative cache entry", encoding="ascii"
        )

        image_buffer = BytesIO()
        main.Image.new("RGB", (40, 40), (210, 90, 35)).save(
            image_buffer, format="JPEG"
        )
        artwork = image_buffer.getvalue()

        with mock.patch.object(
            main, "extract_embedded_cover", return_value=artwork
        ) as extract:
            cover_path = main.resolve_track_cover_path(str(track))

        self.assertIsNotNone(cover_path)
        self.assertTrue(Path(cover_path).is_file())
        self.assertGreater(Path(cover_path).stat().st_size, 100)
        extract.assert_called_once_with(str(track.resolve()))


    def test_seek_slider_maps_mouse_to_custom_track_bounds(self):
        from_left = main.SeekSlider._value_from_x(
            11, 11, 989, 0, 1000, False
        )
        from_middle = main.SeekSlider._value_from_x(
            500, 11, 989, 0, 1000, False
        )
        from_right = main.SeekSlider._value_from_x(
            989, 11, 989, 0, 1000, False
        )
        self.assertEqual(from_left, 0)
        self.assertEqual(from_middle, 500)
        self.assertEqual(from_right, 1000)

        # The same visual coordinates remain correct for reversed layouts.
        self.assertEqual(
            main.SeekSlider._value_from_x(11, 11, 989, 0, 1000, True),
            1000,
        )
        self.assertEqual(
            main.SeekSlider._value_from_x(989, 11, 989, 0, 1000, True),
            0,
        )


if __name__ == "__main__":
    unittest.main()
