"""Test delle misure del banco di prova (banco_di_prova.py), senza modelli.

    ~/.venvs/ozu/bin/python -m unittest test_banco -v
"""

import unittest

import banco_di_prova as banco

ORIGINALI = ["A safe place to operate from", "Dozens of attacks in town", "You can go home now"]
PRIMA = ["Un posto sicuro de cui operare", "dozens di attentati in città", "Ora potete andare a casa"]
CORRETTA = ["Un posto sicuro da cui operare", "Decine di attentati in città", "Ora potete tornare a casa"]


class ProvaMisure(unittest.TestCase):
    def test_chrf(self):
        self.assertAlmostEqual(banco.chrf(CORRETTA, CORRETTA), 100.0)
        self.assertEqual(banco.chrf(["xyz"], ["abc"]), 0.0)
        vicina, lontana = banco.chrf(PRIMA, CORRETTA), banco.chrf(["Qualcosa di diverso"] * 3, CORRETTA)
        self.assertGreater(vicina, lontana)
        self.assertLess(vicina, 100)

    def test_errori_noti_solo_parole_sospette(self):
        try:
            import AppKit  # noqa: F401
        except ImportError:
            self.skipTest("correttore di macOS non disponibile")
        noti = banco.errori_noti(ORIGINALI, PRIMA, CORRETTA)
        # «andare» → «tornare» è una scelta di stile: non è un errore noto.
        self.assertEqual(noti, {0: {"de"}, 1: {"dozens"}})
        self.assertEqual(banco.rifatti(noti, PRIMA), [0, 1])
        self.assertEqual(banco.rifatti(noti, CORRETTA), [])

    def test_confronto(self):
        base = {"caso": "prova", "quando": "2026-11-04 10:00", "modello": "a", "revisore": "b", "chrf": 70.0,
                "errori_noti": 2, "errori_noti_totali": 11, "sospette": 5, "fuori_posto_grezza": 1, "fuori_posto": 0,
                "da_controllare": 3, "secondi_traduzione": 350, "secondi_rilettura": 250, "correzioni_rilettura": 48}
        nuova = {**base, "chrf": 72.0, "errori_noti": 4, "secondi_traduzione": 355}
        testo = banco.confronto(nuova, base)
        self.assertIn("somiglianza al riferimento (chrF): 72.0  (prima 70.0: meglio)", testo)
        self.assertIn("errori noti rifatti: 4 su 11  (prima 2: PEGGIO)", testo)
        self.assertIn("secondi di traduzione: 355  (prima 350: uguale)", testo)  # entro il 10%
        self.assertNotIn("prima", banco.confronto(base))  # la prima prova non ha confronti


if __name__ == "__main__":
    unittest.main()
