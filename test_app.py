"""Test di app.py per Ozu.app: le finestre di scelta aperte dall'app nativa, senza finestre vere.

    ~/.venvs/ozu/bin/python -m unittest test_app -v
"""

import unittest
from unittest import mock

import app


class ProvaScelte(unittest.TestCase):
    def test_percorsi_dall_app_nativa(self):
        self.assertEqual(app.scegli("file", {"percorsi": ["/a/b.srt", "/c/"]}), ["/a/b.srt", "/c"])

    def test_scelta_annullata(self):
        self.assertIsNone(app.scegli("cartella", {"percorsi": []}))

    def test_senza_percorsi_in_servizio(self):
        with mock.patch("app.SERVIZIO", True):
            with self.assertRaises(RuntimeError):
                app.scegli("file", {})


if __name__ == "__main__":
    unittest.main()
