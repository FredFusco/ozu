"""Test della rilettura (revisione.py) e della tenuta alle risposte non valide, con un modello finto.

    ~/.venvs/ozu/bin/python -m unittest test_revisione -v

Nessuna richiesta a Ollama: il revisore è una finta che risponde come il modello.
"""

import contextlib
import io
import re
import unittest
from unittest import mock

import adattamento
import motore_locale
import revisione
import sottotitoli as st

ORIGINALI = [f"This is line {n}." for n in range(300)]
TRADOTTI = [f"Questa è la battuta {n}." for n in range(300)]


class FintoRevisore:
    """Risponde alla rilettura con le correzioni date ({numero di battuta: testo}); le richieste con più
    di `massimo` battute ricevono una risposta non valida, come un modello che si tronca."""

    def __init__(self, correzioni=None, massimo=10_000):
        self.correzioni, self.massimo, self.richieste = correzioni or {}, massimo, []

    def chiedi(self, blocchi, schema, attesi=0, avanza=None):
        numeri = [int(n) for n in re.findall(r"^#(\d+)", blocchi[-1]["text"], re.M)]
        self.richieste.append((numeri[0], numeri[-1]))
        if len(numeri) > self.massimo:
            raise adattamento.RispostaNonValida("risposta non valida")
        return {"correzioni": [{"n": n, "t": t, "motivo": "errore"} for n, t in self.correzioni.items()
                               if n in numeri]}


def rivedi(revisore, tradotti=TRADOTTI):
    with contextlib.redirect_stdout(io.StringIO()):
        return revisione.rivedi(revisore, ORIGINALI, [4.0] * len(ORIGINALI), list(tradotti), {}, {})


class ProvaRilettura(unittest.TestCase):
    def test_applica_le_correzioni_e_le_elenca(self):
        riletti, modifiche = rivedi(FintoRevisore({5: "Questa è la battuta cinque.", 250: "Correzione lontana."}))
        self.assertEqual(riletti[4], "Questa è la battuta cinque.")
        self.assertEqual(riletti[249], "Correzione lontana.")
        self.assertEqual([m["n"] for m in modifiche], [5, 250])
        self.assertEqual(modifiche[0]["prima"], "Questa è la battuta 4.")
        self.assertEqual(riletti[10], TRADOTTI[10])  # il resto non cambia

    def test_va_a_capo_da_sola(self):
        lunga = "Questa correzione è più lunga di una riga di sottotitolo"
        riletti, _ = rivedi(FintoRevisore({3: lunga}))
        self.assertIn("\n", riletti[2])

    def test_scarta_le_correzioni_che_rompono_i_vincoli(self):
        tradotti = list(TRADOTTI)
        tradotti[7] = "<i>In corsivo.</i>"
        originali_prima = ORIGINALI[7]
        ORIGINALI[7] = "<i>In italics.</i>"
        try:
            riletti, modifiche = rivedi(FintoRevisore({8: "Senza corsivo.", 9: "Una\ndue\ntre righe."}), tradotti)
        finally:
            ORIGINALI[7] = originali_prima
        self.assertEqual(riletti[7], "<i>In corsivo.</i>")
        self.assertEqual(riletti[8], TRADOTTI[8])
        self.assertEqual(modifiche, [])

    def test_ignora_le_correzioni_vuote_o_uguali(self):
        _, modifiche = rivedi(FintoRevisore({2: "  ", 3: TRADOTTI[2]}))
        self.assertEqual(modifiche, [])

    def test_divide_il_blocco_se_la_risposta_non_e_valida(self):
        revisore = FintoRevisore({100: "Corretta.", 200: "Anche questa."}, massimo=40)
        riletti, modifiche = rivedi(revisore)
        self.assertEqual([m["n"] for m in modifiche], [100, 200])
        self.assertIn((1, 120), revisore.richieste)  # prima intero, poi a metà
        self.assertIn((1, 30), revisore.richieste)

    def test_salta_un_blocco_che_non_passa_mai(self):
        riletti, modifiche = rivedi(FintoRevisore({5: "Corretta."}, massimo=5))
        self.assertEqual(riletti, TRADOTTI)  # nessun errore: la traduzione resta com'era
        self.assertEqual(modifiche, [])

    def test_correttore_ortografico(self):
        try:
            import AppKit  # noqa: F401
        except ImportError:
            self.skipTest("correttore di macOS non disponibile")
        originali = ["The Kervoazec is surrounded.", "Dozens of attacks."]
        tradotti = ["La Kervoazec è circondata.", "dozens di attentati."]
        dubbi = revisione.ortografia(originali, tradotti)
        self.assertEqual(dubbi, {1: ["dozens"]})  # «Kervoazec» è un nome dell'originale


    def test_ripropone_le_parole_ancora_sospette(self):
        try:
            import AppKit  # noqa: F401
        except ImportError:
            self.skipTest("correttore di macOS non disponibile")

        class Distratto(FintoRevisore):
            """Nella rilettura lunga non vede «dozens»; nella richiesta mirata sì."""
            def chiedi(self, blocchi, schema, attesi=0, avanza=None):
                if "ci sono parole sospette" not in blocchi[-1]["text"]:
                    return {"correzioni": []}
                return super().chiedi(blocchi, schema, attesi, avanza)

        tradotti = list(TRADOTTI)
        tradotti[41] = "Ci sono dozens di feriti."
        revisore = Distratto({42: "Ci sono decine di feriti."})
        riletti, modifiche = rivedi(revisore, tradotti)
        self.assertEqual(riletti[41], "Ci sono decine di feriti.")
        self.assertEqual(modifiche, [{"n": 42, "prima": "Ci sono dozens di feriti.", "dopo": "Ci sono decine di feriti.",
                                      "motivo": "errore"}])

class ProvaRisposteNonValide(unittest.TestCase):
    def test_json_con_barre_non_valide(self):
        self.assertEqual(motore_locale._json_nel_testo('Ecco: {"t": "l\\\'uomo \\"x\\"\\n"}'),
                         {"t": "l'uomo \"x\"\n"})

    def test_riconosce_vertov_lanciato_dalla_sua_cartella(self):
        righe = {"con il percorso": "/usr/bin/python3 -u /Users/x/vertov/descrivi.py film.mxf",
                 "dalla sua cartella": "/opt/homebrew/bin/Python -u descrivi.py /Users/x/RULLO 1/film.mxf --edl x.edl"}
        for come, riga in righe.items():
            with self.subTest(come), \
                    mock.patch("motore_locale.urllib.request.urlopen", side_effect=OSError("chiusa")), \
                    mock.patch("motore_locale.subprocess.run", return_value=mock.Mock(stdout=riga + "\n")):
                self.assertEqual(motore_locale.altre_app_al_lavoro(), ["Vertov"])
        with mock.patch("motore_locale.urllib.request.urlopen", side_effect=OSError("chiusa")), \
                mock.patch("motore_locale.subprocess.run", return_value=mock.Mock(stdout="vim descrivi.py.bak\n")):
            self.assertEqual(motore_locale.altre_app_al_lavoro(), [])

    def test_traduzione_divisa_a_meta(self):
        class FintoTraduttore:
            def __init__(self):
                self.richieste = []

            def chiedi(self, blocchi, schema, attesi=0, avanza=None):
                da_tradurre = blocchi[-1]["text"].split("<da_tradurre>")[1]
                numeri = [int(n) for n in re.findall(r"^#(\d+)", da_tradurre, re.M)]
                self.richieste.append(len(numeri))
                if len(numeri) > 50:
                    raise adattamento.RispostaNonValida("troncata")
                return {"battute": [{"n": n, "t": f"Battuta {n}"} for n in numeri]}

        sessione = FintoTraduttore()
        with contextlib.redirect_stdout(io.StringIO()):
            tradotti, _ = adattamento.traduci(sessione, ORIGINALI[:120], [2.0] * 120, {}, {}, lambda *a: None)
        self.assertEqual(tradotti, [f"Battuta {n}" for n in range(1, 121)])
        self.assertEqual(sessione.richieste[0], 120)
        self.assertTrue(all(r <= 60 for r in sessione.richieste[1:]))


# Un copione di prova con battute di lunghezza varia, numeri e nomi, come nei dialoghi veri.
PAROLE = ["Ardelan", "Ali", "sir", "tomorrow", "the house", "the strike", "now", "in the Kervoazec", "quickly",
          "at night", "with the others", "General", "never again", "listen to me", "Lev Arkadin"]
COPIONE = [" ".join(PAROLE[(i * 7 + k) % len(PAROLE)] for k in range(1 + (i * 5) % 6)) + (f" {i}" if i % 4 == 0 else "")
           for i in range(200)]


class ProvaSfasamenti(unittest.TestCase):
    def test_trova_il_tratto_fuori_posto(self):
        # Il modello salta la battuta 120 e da lì ogni traduzione è quella della battuta dopo.
        tradotti = COPIONE[:120] + COPIONE[121:] + [COPIONE[-1]]
        tratti = st.sfasamenti(COPIONE, tradotti)
        self.assertTrue(tratti)
        self.assertLessEqual(abs(tratti[0][0] - 120), 3)
        self.assertGreaterEqual(tratti[-1][1], 195)

    def test_niente_se_la_traduzione_e_a_posto(self):
        self.assertEqual(st.sfasamenti(COPIONE, list(COPIONE)), [])

    def test_ritraduce_il_tratto_fuori_posto(self):
        class Sfasato:
            """Al primo giro salta la battuta 50; quando ritraduce un tratto piccolo lo fa giusto."""
            def __init__(self):
                self.giri = 0

            def chiedi(self, blocchi, schema, attesi=0, avanza=None):
                testo = blocchi[-1]["text"].split("<da_tradurre>")[1]
                numeri = [int(n) for n in re.findall(r"^#(\d+)", testo, re.M)]
                self.giri += 1
                if len(numeri) > 40:
                    return {"battute": [{"n": n, "t": COPIONE[n - 1 if n < 50 else min(n, 199)]} for n in numeri]}
                return {"battute": [{"n": n, "t": COPIONE[n - 1]} for n in numeri]}

        sessione = Sfasato()
        with contextlib.redirect_stdout(io.StringIO()):
            tradotti, avvisi = adattamento.traduci(sessione, COPIONE, [6.0] * 200, {}, {}, lambda *a: None)
        self.assertEqual([t.replace("\n", " ") for t in tradotti], COPIONE)  # a capo a parte
        self.assertFalse([a for a in avvisi if "fuori posto" in a])

    def test_la_rilettura_non_sfasa(self):
        # Un revisore che «sposta» il testo di un tratto giusto: quelle correzioni vengono annullate.
        spostate = {n: COPIONE[n] for n in range(60, 100)}
        with contextlib.redirect_stdout(io.StringIO()):
            riletti, modifiche = revisione.rivedi(FintoRevisore(spostate), COPIONE, [6.0] * 200, list(COPIONE), {}, {})
        self.assertEqual(riletti, COPIONE)
        self.assertEqual(modifiche, [])


class ProvaControlli(unittest.TestCase):
    def test_velocita_di_lettura(self):
        self.assertEqual(st.problemi("Go!", "Vai!", 1.0), [])
        lenta = st.problemi("Goodbye, General", "Arrivederci, Ardelan. Ci vediamo\nal Comando questo pomeriggio", 1.4)
        self.assertTrue(any("troppo veloce" in p for p in lenta))
        self.assertEqual(st.problemi("Goodbye", "Arrivederci, a domani pomeriggio", None), [])  # durata ignota

    def test_parole_straniere(self):
        originali = ["A safe place to operate from", "He lives at 8, Rue des Lilas", "The house is surrounded",
                     "They attacked from the east"]
        tradotti = ["Un posto sicuro de cui operare", "Abita all'8, Rue des Lilas", "La casa est circondata",
                    "Hanno attaccato da est"]
        self.assertEqual(revisione.straniere(originali, tradotti), {0: ["de"], 2: ["est"]})


if __name__ == "__main__":
    unittest.main()
