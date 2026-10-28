import sqlite3
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
