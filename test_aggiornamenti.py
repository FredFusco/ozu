"""Test del controllo degli aggiornamenti dei modelli locali (aggiornamenti.py), senza rete né Ollama.

    ~/.venvs/ozu/bin/python -m unittest test_aggiornamenti -v
"""

import io
import json
import unittest
from unittest import mock

import aggiornamenti
import motore_locale
import revisione


class ProvaModelliLocali(unittest.TestCase):
    def controlla(self, locali: dict, remote: dict, ricordate: dict):
        """locali: {modello: impronta in Ollama}; remote: {modello: impronta nel registro};
        ricordate: impronte del registro ricordate al download. Restituisce (voci, stato scritto)."""
        tags = json.dumps({"models": [{"name": m, "digest": d} for m, d in locali.items()]}).encode()
        stato = {"modelli_scaricati": dict(ricordate)}

        def scrivi(**campi):
            stato.update(campi)

        with mock.patch("aggiornamenti.urllib.request.urlopen", return_value=io.BytesIO(tags)), \
                mock.patch("aggiornamenti._impronta_remota", side_effect=lambda m: remote[m]), \
                mock.patch("aggiornamenti.leggi_stato", side_effect=lambda: stato), \
                mock.patch("aggiornamenti.scrivi_stato", side_effect=scrivi):
            return aggiornamenti.controlla_modello_locale(), stato

    def test_controlla_traduttore_e_revisore(self):
        tradu, revi = motore_locale.MODELLO, revisione.REVISORE
        voci, _ = self.controlla({tradu: "conv-a", revi: "conv-b"}, {tradu: "nuova-a", revi: "nuova-b"},
                                 {tradu: "vecchia-a", revi: "vecchia-b"})
        self.assertEqual([v["nome"] for v in voci], [tradu, revi])
        self.assertIn(revisione.NOME_REVISORE, voci[1]["descrizione"])

    def test_niente_se_il_registro_non_e_cambiato(self):
        tradu, revi = motore_locale.MODELLO, revisione.REVISORE
        voci, _ = self.controlla({tradu: "conv-a", revi: "conv-b"}, {tradu: "a", revi: "b"}, {tradu: "a", revi: "b"})
        self.assertEqual(voci, [])

    def test_al_primo_controllo_ricorda_senza_proporre(self):
        revi = revisione.REVISORE
        voci, stato = self.controlla({revi: "conv-b"}, {revi: "b"}, {})
        self.assertEqual(voci, [])
        self.assertEqual(stato["modelli_scaricati"], {revi: "b"})

    def test_un_modello_che_manca_non_si_propone(self):
        voci, _ = self.controlla({}, {}, {})
        self.assertEqual(voci, [])


if __name__ == "__main__":
    unittest.main()
