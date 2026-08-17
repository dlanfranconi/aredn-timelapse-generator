import os
import tempfile
import unittest

from fenetre.media_storage import (
    describe_media_location,
    ensure_media_storage_layout,
    relocate_media_storage,
)


class TestMediaStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = os.path.join(self.temp_dir.name, "work")
        self.media_dir = os.path.join(self.temp_dir.name, "media")
        os.makedirs(self.work_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write(self, path, content=b"x"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)

    def test_relocate_moves_existing_photos_and_symlinks(self):
        photo_path = os.path.join(self.work_dir, "photos", "cam1", "latest.jpg")
        self._write(photo_path, b"pic")

        report = relocate_media_storage({"work_dir": self.work_dir}, self.media_dir)

        self.assertTrue(report["ok"])
        photos_link = os.path.join(self.work_dir, "photos")
        self.assertTrue(os.path.islink(photos_link))
        self.assertEqual(
            os.path.realpath(photos_link),
            os.path.realpath(os.path.join(self.media_dir, "photos")),
        )
        moved_path = os.path.join(self.media_dir, "photos", "cam1", "latest.jpg")
        self.assertTrue(os.path.isfile(moved_path))
        with open(moved_path, "rb") as f:
            self.assertEqual(f.read(), b"pic")

    def test_relocate_creates_empty_dirs_when_nothing_exists(self):
        report = relocate_media_storage({"work_dir": self.work_dir}, self.media_dir)
        self.assertTrue(report["ok"])
        statuses = {a["subdir"]: a["status"] for a in report["actions"]}
        self.assertEqual(statuses, {"photos": "created", "launches": "created"})
        self.assertTrue(os.path.islink(os.path.join(self.work_dir, "photos")))
        self.assertTrue(os.path.isdir(os.path.join(self.media_dir, "photos")))

    def test_relocate_is_idempotent(self):
        self._write(os.path.join(self.work_dir, "photos", "cam1", "a.jpg"))
        relocate_media_storage({"work_dir": self.work_dir}, self.media_dir)

        second_report = relocate_media_storage(
            {"work_dir": self.work_dir}, self.media_dir
        )
        statuses = {a["subdir"]: a["status"] for a in second_report["actions"]}
        self.assertEqual(statuses["photos"], "already_linked")

    def test_dry_run_does_not_touch_filesystem(self):
        photo_path = os.path.join(self.work_dir, "photos", "cam1", "a.jpg")
        self._write(photo_path)

        report = relocate_media_storage(
            {"work_dir": self.work_dir}, self.media_dir, dry_run=True
        )
        self.assertTrue(report["ok"])
        self.assertFalse(os.path.islink(os.path.join(self.work_dir, "photos")))
        self.assertTrue(os.path.isfile(photo_path))
        self.assertFalse(os.path.exists(self.media_dir))

    def test_relocate_to_new_target_migrates_again(self):
        self._write(os.path.join(self.work_dir, "photos", "cam1", "a.jpg"))
        relocate_media_storage({"work_dir": self.work_dir}, self.media_dir)

        second_media_dir = os.path.join(self.temp_dir.name, "media2")
        report = relocate_media_storage({"work_dir": self.work_dir}, second_media_dir)
        statuses = {a["subdir"]: a["status"] for a in report["actions"]}
        self.assertEqual(statuses["photos"], "relinked")
        self.assertTrue(
            os.path.isfile(os.path.join(second_media_dir, "photos", "cam1", "a.jpg"))
        )
        self.assertEqual(
            os.path.realpath(os.path.join(self.work_dir, "photos")),
            os.path.realpath(os.path.join(second_media_dir, "photos")),
        )

    def test_relocate_rejects_media_dir_inside_work_dir_photos(self):
        nested = os.path.join(self.work_dir, "photos", "nope")
        with self.assertRaises(ValueError):
            relocate_media_storage({"work_dir": self.work_dir}, nested)

    def test_relocate_rejects_media_dir_equal_to_work_dir(self):
        with self.assertRaises(ValueError):
            relocate_media_storage({"work_dir": self.work_dir}, self.work_dir)

    def test_ensure_layout_links_when_nothing_exists(self):
        ensure_media_storage_layout(
            {"work_dir": self.work_dir, "media_dir": self.media_dir}
        )
        photos_link = os.path.join(self.work_dir, "photos")
        self.assertTrue(os.path.islink(photos_link))
        self.assertTrue(os.path.isdir(os.path.join(self.media_dir, "photos")))

    def test_ensure_layout_does_not_move_existing_real_directory(self):
        photo_path = os.path.join(self.work_dir, "photos", "cam1", "a.jpg")
        self._write(photo_path)

        ensure_media_storage_layout(
            {"work_dir": self.work_dir, "media_dir": self.media_dir}
        )

        self.assertFalse(os.path.islink(os.path.join(self.work_dir, "photos")))
        self.assertTrue(os.path.isfile(photo_path))
        self.assertFalse(os.path.exists(os.path.join(self.media_dir, "photos")))
        # launches had nothing pre-existing, so it is safely auto-linked.
        self.assertTrue(os.path.islink(os.path.join(self.work_dir, "launches")))

    def test_ensure_layout_noop_without_media_dir(self):
        ensure_media_storage_layout({"work_dir": self.work_dir})
        self.assertFalse(os.path.exists(os.path.join(self.work_dir, "photos")))

    def test_describe_media_location(self):
        relocate_media_storage({"work_dir": self.work_dir}, self.media_dir)
        status = describe_media_location(
            {"work_dir": self.work_dir, "media_dir": self.media_dir}
        )
        self.assertEqual(status["media_dir"], self.media_dir)
        self.assertTrue(status["dirs"]["photos"]["is_symlink"])
        self.assertTrue(status["dirs"]["photos"]["exists"])


if __name__ == "__main__":
    unittest.main()
