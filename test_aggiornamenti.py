"""Test del controllo degli aggiornamenti dei modelli locali (aggiornamenti.py), senza rete né Ollama.

    ~/.venvs/ozu/bin/python -m unittest test_aggiornamenti -v
"""

import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
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


class ProvaInstallazioneApp(unittest.TestCase):
    """L'aggiornamento di Ozu sostituisce tutta Ozu.app; quella di prima va in versioni_precedenti."""

    def pacchetto(self, cartella, testo: str) -> bytes:
        app = cartella / "nuova" / "Ozu.app"
        for rel, contenuto in (("Contents/MacOS/Ozu", "eseguibile"), ("Contents/Resources/programma/app.py", testo)):
            (app / rel).parent.mkdir(parents=True, exist_ok=True)
            (app / rel).write_text(contenuto)
        (app / "Contents/MacOS/Ozu").chmod(0o755)
        dati = io.BytesIO()
        with zipfile.ZipFile(dati, "w") as z:
            for f in app.rglob("*"):
                if f.is_file():
                    z.write(f, "Ozu.app/" + str(f.relative_to(app)))
        return dati.getvalue()

    def test_sostituisce_l_app_e_conserva_la_vecchia(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            installata = d / "Applications" / "Ozu.app"
            (installata / "Contents/Resources/programma").mkdir(parents=True)
            (installata / "Contents/Resources/programma/app.py").write_text("vecchia")
            zip_ = self.pacchetto(d, "nuova")
            risposta = mock.MagicMock()
            risposta.__enter__.return_value = risposta
            risposta.headers = {"Content-Length": str(len(zip_))}
            risposta.read.side_effect = [zip_, b""]
            voce = {"nuova": "9.9.9", "pacchetto": "https://esempio.prova/ozu.zip",
                    "sha256": hashlib.sha256(zip_).hexdigest()}
            with mock.patch.object(aggiornamenti, "APP", installata), \
                    mock.patch.object(aggiornamenti, "PRECEDENTI", d / "precedenti"), \
                    mock.patch.object(aggiornamenti, "versione", return_value="1.0.0"), \
                    mock.patch("aggiornamenti.urllib.request.urlopen", return_value=risposta):
                aggiornamenti._installa_app(voce)
            self.assertEqual((installata / "Contents/Resources/programma/app.py").read_text(), "nuova")
            self.assertTrue(os.access(installata / "Contents/MacOS/Ozu", os.X_OK))
            self.assertEqual((d / "precedenti/1.0.0/Ozu.app/Contents/Resources/programma/app.py").read_text(), "vecchia")
            self.assertEqual(sorted(p.name for p in installata.parent.iterdir()), ["Ozu.app"])  # niente avanzi

    def test_dal_codice_sorgente_non_si_installa(self):
        with mock.patch.object(aggiornamenti, "APP", None):
            with self.assertRaises(RuntimeError):
                aggiornamenti._installa_app({"nuova": "9.9.9"})


if __name__ == "__main__":
    unittest.main()
