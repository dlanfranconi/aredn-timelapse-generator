import os
import tempfile
import unittest

from fenetre.ui_utils import generate_index_html


class TestUiUtils(unittest.TestCase):
    def test_generate_index_html_uses_list_for_map_landing_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            list_path = os.path.join(tmpdir, "list.html")
            with open(list_path, "w") as f:
                f.write("<html>camera list</html>")

            generate_index_html(tmpdir, {"ui": {"landing_page": "map"}})

            with open(os.path.join(tmpdir, "index.html")) as f:
                self.assertEqual(f.read(), "<html>camera list</html>")
