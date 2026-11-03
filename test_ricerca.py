"""Test della ricerca di sottotitoli esistenti (ricerca.py), con le API simulate.

    ~/.venvs/ozu/bin/python -m unittest test_ricerca -v

Nessuna richiesta esce dal computer: _http è sostituita da una finta che risponde come le API.
I dati sono di prova («Film di prova», anno 2001).
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

import allineamento
import ricerca

CHIAVI = {"opensubtitles": "chiave-os-segreta", "subdl": "chiave-subdl-segreta",
          "opensubtitles_utente": None, "opensubtitles_password": None}


def srt(battute: list, passo: float = 3.0) -> str:
    """Un SRT con le battute date, una ogni `passo` secondi."""
    blocchi = []
    for i, testo in enumerate(battute):
        inizio, fine = i * passo + 1, i * passo + 2.5
        t = lambda s: f"00:{int(s) // 60:02d}:{int(s) % 60:02d},{int(s * 1000) % 1000:03d}"  # noqa: E731
        blocchi.append(f"{i + 1}\n{t(inizio)} --> {t(fine)}\n{testo}")
    return "\n\n".join(blocchi) + "\n"


INGLESE = [f"This is line number {n} of the test film." for n in range(40)]
ITALIANO = [f"Questa è la battuta numero {n} del film di prova." for n in range(40)]


def film_di_prova(battute: int = 600, seme: int = 7) -> list:
    """Le battute (inizio, fine, numero) di un film di prova lungo, con pause irregolari come nei dialoghi veri."""
    caso, t, voci = __import__("random").Random(seme), 30.0, []
    for i in range(battute):
        d = caso.uniform(1.0, 4.5)
        voci.append((t, t + d, i))
        t += d + caso.choice([0.1, 0.3, 0.8, 1.5, 3, 6, 12])
    return voci


FILM = film_di_prova()
PAL = 23.976 / 25  # i tempi di un'edizione a 25 fotogrammi al secondo visti su una a 23,976


def srt_film(voci: list, lingua: str = "it") -> str:
    """Un SRT del film di prova con i tempi dati; il testo dipende solo dal numero della battuta."""
    frase = "Questa è la battuta numero {} del film di prova." if lingua == "it" else "This is line {} of the test film."
    return allineamento.scrivi_srt([(a, b, frase.format(n) if isinstance(n, int) else n) for a, b, n in voci])


def risultato_os(file_id, nome, **attributi):
    return {"id": str(file_id), "type": "subtitle", "attributes": {
        "language": "it", "release": nome, "download_count": 100, "ratings": 8.0, "fps": 23.976,
        "ai_translated": False, "machine_translated": False, "comments": "", "uploader": {"name": "prova"},
        "files": [{"file_id": file_id, "file_name": nome + ".srt"}], **attributi}}


def zip_con(nome: str, testo: str) -> bytes:
    dati = io.BytesIO()
    with zipfile.ZipFile(dati, "w") as z:
        z.writestr(nome, testo.encode("cp1252"))
    return dati.getvalue()


class FintaAPI:
    """Risponde come OpenSubtitles e SUBDL. os_ricerca, subdl_ricerca: liste di risultati;
    file: {file_id o url: contenuto}; errori: {fonte: [codice, …]} restituiti prima delle risposte vere."""

    def __init__(self, os_ricerca=(), subdl_ricerca=(), file=None, errori=None, rete_giu=False):
        self.os_ricerca, self.subdl_ricerca = list(os_ricerca), list(subdl_ricerca)
        self.file, self.errori, self.rete_giu = file or {}, {k: list(v) for k, v in (errori or {}).items()}, rete_giu
        self.chiamate = []

    def __call__(self, metodo, url, intestazioni, corpo=None, timeout=30):
        self.chiamate.append((metodo, url))
        if self.rete_giu:
            raise urllib.error.URLError("rete assente")
        fonte = "SUBDL" if "subdl" in url else "OpenSubtitles"
        if self.errori.get(fonte):
            return self.errori[fonte].pop(0), {"Retry-After": "1"}, b'{"message":"Too many requests"}'
        if url.startswith(ricerca.OPENSUBTITLES + "/subtitles"):
            return 200, {}, json.dumps({"total_count": len(self.os_ricerca), "data": self.os_ricerca}).encode()
        if url == ricerca.OPENSUBTITLES + "/download":
            file_id = json.loads(corpo)["file_id"]
            return 200, {}, json.dumps({"link": f"https://scarica.prova/{file_id}.srt", "remaining": 9}).encode()
        if url.startswith("https://scarica.prova/"):
            contenuto = self.file[int(url.rsplit("/", 1)[1].split(".")[0])]
            return 200, {}, contenuto if isinstance(contenuto, bytes) else contenuto.encode("cp1252")
        if url.startswith(ricerca.SUBDL):
            return 200, {}, json.dumps({"status": True, "subtitles": self.subdl_ricerca}).encode()
        if url.startswith(ricerca.SUBDL_DOWNLOAD):
            percorso = url[len(ricerca.SUBDL_DOWNLOAD):]
            return 200, {}, zip_con("prova.srt", self.file[percorso])
        return 404, {}, b"{}"


class ProvaRicerca(unittest.TestCase):
    def setUp(self):
        self.cartella = Path(tempfile.mkdtemp())
        self.partenza = self.cartella / "Film di prova (2001) - Subtitles EN.srt"
        self.partenza.write_text(srt(INGLESE), encoding="utf-8")
        self.video = self.cartella / "Film di prova (2001).mkv"
        self.video.write_bytes(os.urandom(200_000))
        self.chiavi = dict(CHIAVI)
        self.patch = [mock.patch("ricerca.chiavi.leggi", side_effect=lambda s: self.chiavi.get(s)),
                      mock.patch("ricerca.time.sleep"),
                      mock.patch("ricerca.frame_rate", return_value=23.976),
                      mock.patch("ricerca.durata", return_value=125.0),
                      mock.patch("ricerca.CONFIGURAZIONE", self.cartella / "nessuna.json")]
        for p in self.patch:
            p.start()

    def tearDown(self):
        for p in self.patch:
            p.stop()

    def cerca(self, api):
        uscita = io.StringIO()
        with mock.patch("ricerca._http", api), contextlib.redirect_stdout(uscita):
            esito = ricerca.cerca(self.partenza)
        registro = uscita.getvalue() + esito.messaggio()
        # Le chiavi non devono mai comparire nel registro né nei messaggi.
        for chiave in ("chiave-os-segreta", "chiave-subdl-segreta"):
            self.assertNotIn(chiave, registro)
        return esito, registro

    # ----------------------------------------------------------------- casi richiesti

    def test_trovato(self):
        api = FintaAPI(os_ricerca=[risultato_os(1, "Film.di.Prova.2001.AI.translated", ai_translated=True),
                                   risultato_os(2, "Film.di.Prova.2001.1080p", moviehash_match=True)],
                       file={2: srt(ITALIANO)})
        esito, _ = self.cerca(api)
        self.assertIsNotNone(esito.trovato)
        self.assertEqual(esito.trovato["fonte"], "OpenSubtitles")
        self.assertIn("hash", esito.trovato["motivo"])
        salvato = Path(esito.trovato["file"])
        self.assertEqual(salvato.name, "Film di prova (2001) - Sottotitoli IT (scaricati).srt")
        self.assertIn("è la battuta", salvato.read_text(encoding="utf-8"))  # convertito da cp1252 a UTF-8
        self.assertTrue(any("IA" in s["motivo"] for s in esito.scarti))

    def test_tiene_solo_lo_stesso_film(self):
        altro = risultato_os(1, "Altro.Film.2001", download_count=9000,
                             feature_details={"title": "Another Film", "movie_name": "2001 - Another Film", "year": 2001})
        giusto = risultato_os(2, "Release.2001", download_count=50,
                              feature_details={"title": "Test Film", "movie_name": "2001 - Film di prova", "year": 2001})
        esito, _ = self.cerca(FintaAPI(os_ricerca=[altro, giusto], file={1: srt(ITALIANO), 2: srt(ITALIANO)}))
        self.assertEqual(esito.trovato["nome"], "Release.2001")
        self.assertIn("altro film: Another Film", esito.scarti[0]["motivo"])

    def test_login_non_riuscito_spiegato_senza_credenziali(self):
        self.chiavi.update(opensubtitles_utente="utente-di-prova", opensubtitles_password="parola-segreta")
        api = FintaAPI(os_ricerca=[risultato_os(2, "Film.di.Prova")], file={2: srt(ITALIANO)})
        originale = api.__call__

        def chiamata(metodo, url, intestazioni, corpo=None, timeout=30):
            if url.endswith("/login"):
                return 400, {}, b'{"message": "invalid username/password - use your username, not your email"}'
            return originale(metodo, url, intestazioni, corpo, timeout)
        esito, registro = self.cerca(chiamata)
        self.assertIn("use your username", registro)
        self.assertNotIn("parola-segreta", registro)
        self.assertIsNotNone(esito.trovato)  # si va avanti senza login

    def test_non_sostituisce_un_file_esistente(self):
        esistente = self.cartella / "Film di prova (2001) - Sottotitoli IT (scaricati).srt"
        esistente.write_text("mio", encoding="utf-8")
        esito, _ = self.cerca(FintaAPI(os_ricerca=[risultato_os(2, "Film.di.Prova")], file={2: srt(ITALIANO)}))
        self.assertEqual(esistente.read_text(encoding="utf-8"), "mio")
        self.assertTrue(esito.trovato["file"].endswith("(scaricati) (2).srt"))

    def test_nessun_risultato(self):
        esito, registro = self.cerca(FintaAPI())
        self.assertIsNone(esito.trovato)
        self.assertEqual([f["fonte"] for f in esito.fonti], ["OpenSubtitles", "SUBDL", "Podnapisi"])
        self.assertIn("Nessun sottotitolo italiano valido", registro)
        self.assertIn("Podnapisi: saltato", registro)

    def test_solo_traduzioni_automatiche(self):
        api = FintaAPI(os_ricerca=[risultato_os(1, "Film.di.Prova", machine_translated=True),
                                   risultato_os(2, "Film.di.Prova", comments="tradotto con DeepL")],
                       subdl_ricerca=[{"release_name": "Film.di.Prova.Google.Translate", "url": "/subtitle/1.zip",
                                       "language": "IT"}])
        esito, _ = self.cerca(api)
        self.assertIsNone(esito.trovato)
        self.assertEqual(len(esito.scarti), 3)
        motivi = " ".join(s["motivo"] for s in esito.scarti)
        self.assertIn("automaticamente", motivi)
        self.assertIn("DeepL", motivi)
        self.assertIn("Google", motivi)
        self.assertFalse(any("download" in u for _, u in api.chiamate))  # niente scaricato

    def test_errore_di_rete(self):
        esito, _ = self.cerca(FintaAPI(rete_giu=True))
        self.assertIsNone(esito.trovato)
        self.assertTrue(all("non risponde" in f["stato"] for f in esito.fonti[:2]))

    def test_limite_di_richieste_poi_ok(self):
        api = FintaAPI(os_ricerca=[risultato_os(2, "Film.di.Prova")], file={2: srt(ITALIANO)},
                       errori={"OpenSubtitles": [429]})
        with mock.patch("ricerca.time.sleep") as pausa:
            esito, registro = self.cerca(api)
            self.assertIn(mock.call(1.0), pausa.call_args_list)  # rispettato Retry-After
        self.assertIsNotNone(esito.trovato)
        self.assertIn("limite di richieste", registro)

    def test_limite_di_richieste_persistente(self):
        esito, _ = self.cerca(FintaAPI(errori={"OpenSubtitles": [429] * 10, "SUBDL": [429] * 10}))
        self.assertIsNone(esito.trovato)
        self.assertTrue(all("429" in f["stato"] for f in esito.fonti[:2]))

    def test_chiave_mancante(self):
        self.chiavi = {}
        api = FintaAPI()
        esito, _ = self.cerca(api)
        self.assertEqual(api.chiamate, [])  # nessuna richiesta senza chiavi
        self.assertTrue(all("manca la chiave" in f["stato"] for f in esito.fonti[:2]))

    # ----------------------------------------------------------------- controlli del contenuto

    def test_scarta_il_non_tradotto_e_prende_il_successivo(self):
        api = FintaAPI(os_ricerca=[risultato_os(1, "Film.di.Prova.A", moviehash_match=True),
                                   risultato_os(2, "Film.di.Prova.B")],
                       file={1: srt(INGLESE), 2: srt(ITALIANO)})
        esito, _ = self.cerca(api)
        self.assertIn("non è tradotto", esito.scarti[0]["motivo"])
        self.assertEqual(esito.trovato["nome"], "Film.di.Prova.B")

    def test_scarta_battute_e_tempi_diversi(self):
        api = FintaAPI(subdl_ricerca=[{"release_name": "Prova.corta", "url": "/subtitle/1.zip", "language": "IT"},
                                      {"release_name": "Prova.lenta", "url": "/subtitle/2.zip", "language": "IT"}],
                       file={"/subtitle/1.zip": srt(ITALIANO[:15]), "/subtitle/2.zip": srt(ITALIANO, passo=4.0)})
        esito, _ = self.cerca(api)
        self.assertIsNone(esito.trovato)
        self.assertIn("numero di battute", esito.scarti[0]["motivo"])
        self.assertIn("copre", esito.scarti[1]["motivo"])

    # ----------------------------------------------------------------- tempi da riallineare

    def cerca_film(self, *scaricati):
        """Cerca con il film di prova lungo come partenza; scaricati: (nome, voci) in ordine di classifica."""
        self.partenza.write_text(srt_film(FILM, "en"), encoding="utf-8")
        api = FintaAPI(os_ricerca=[risultato_os(i + 1, nome) for i, (nome, _) in enumerate(scaricati)],
                       file={i + 1: srt_film(voci) for i, (_, voci) in enumerate(scaricati)})
        with mock.patch("ricerca.durata", return_value=FILM[-1][1] + 30):
            return self.cerca(api)

    def errore_medio(self, esito):
        """Di quanto si discostano, in media, i tempi salvati da quelli giusti del film di prova."""
        giusti = {n: a for a, _, n in FILM}
        salvate = allineamento.voci(Path(esito.trovato["file"]).read_text(encoding="utf-8"))
        scarti = [abs(a - giusti[int(t.split()[5])]) for a, _, t in salvate if "battuta numero" in t]
        return sum(scarti) / len(scarti)

    def test_tempi_giusti_presi_cosi_come_sono(self):
        esito, _ = self.cerca_film(("Film.di.Prova.giusto", FILM))
        self.assertIn("tempi giusti", esito.trovato["motivo"])
        self.assertLess(self.errore_medio(esito), 0.01)

    def test_riallinea_frame_rate_e_cartelli(self):
        # Edizione a 25 fps con 12 secondi di cartelli in meno in testa.
        esito, registro = self.cerca_film(("Film.di.Prova.PAL", [((a - 12) * PAL, (b - 12) * PAL, n) for a, b, n in FILM]))
        self.assertIsNotNone(esito.trovato, registro)
        self.assertIn("frame rate", esito.trovato["motivo"])
        self.assertLess(self.errore_medio(esito), 0.3)

    def test_toglie_la_scena_che_qui_non_ce(self):
        # Il file scaricato viene da un montaggio con una scena in più, che nella nostra versione manca.
        dopo = FILM[300][1]
        extra = [(dopo + 1 + i * 3.4, dopo + 3 + i * 3.4, f"Battuta di una scena in più, la numero {i}.") for i in range(25)]
        voci = FILM[:301] + extra + [(a + 90, b + 90, n) for a, b, n in FILM[301:]]
        esito, registro = self.cerca_film(("Film.di.Prova.lungo", voci))
        self.assertIsNotNone(esito.trovato, registro)
        self.assertIn("tagli", esito.trovato["motivo"])
        self.assertLess(self.errore_medio(esito), 0.3)
        tenute = Path(esito.trovato["file"]).read_text(encoding="utf-8").count("scena in più")
        self.assertLessEqual(tenute, 2)  # al punto del taglio può restarne una

    def test_scarta_se_manca_una_scena(self):
        # Il file scaricato viene da un montaggio più corto: una scena della nostra versione resterebbe muta.
        salto = FILM[260][0] - FILM[200][0]
        esito, _ = self.cerca_film(("Film.di.Prova.corto", FILM[:200] + [(a - salto, b - salto, n) for a, b, n in FILM[260:]]))
        self.assertIsNone(esito.trovato)
        self.assertIn("non copre tutto il film", esito.scarti[0]["motivo"])

    def test_scarta_un_file_che_non_corrisponde(self):
        caso = __import__("random").Random(3)
        voci = sorted((a, a + 2, n) for a, (_, _, n) in zip((caso.uniform(0, 3000) for _ in FILM), FILM))
        esito, _ = self.cerca_film(("Film.di.Prova.altro", voci))
        self.assertIsNone(esito.trovato)

    def test_preferisce_i_tempi_giusti_al_riallineato(self):
        # Il riallineato è primo in classifica, ma uno con i tempi già giusti vince.
        esito, _ = self.cerca_film(("Film.di.Prova.PAL", [(a * PAL, b * PAL, n) for a, b, n in FILM]),
                                   ("Film.di.Prova.giusto", FILM))
        self.assertEqual(esito.trovato["nome"], "Film.di.Prova.giusto")
        self.assertIn("tempi giusti", esito.trovato["motivo"])

    def test_scarta_la_codifica_rotta(self):
        rotto = srt([t.replace("è", "\ufffd") for t in ITALIANO]).encode("utf-8")
        esito, _ = self.cerca(FintaAPI(os_ricerca=[risultato_os(2, "Film.di.Prova")], file={2: rotto}))
        self.assertIsNone(esito.trovato)
        self.assertIn("codifica", esito.scarti[0]["motivo"])

    # ----------------------------------------------------------------- nome e hash

    def test_titolo_e_anno(self):
        self.assertEqual(ricerca.titolo_e_anno(Path("Film di prova (2001) - Subtitles EN clean.srt")),
                         ("Film di prova", 2001))
        self.assertEqual(ricerca.titolo_e_anno(Path("Film.Di.Prova.2001.1080p.BluRay.x264.mkv")),
                         ("Film Di Prova", 2001))
        self.assertEqual(ricerca.titolo_e_anno(Path("Film di prova.en.srt")), ("Film di prova", None))

    def test_video_accanto_e_hash(self):
        self.assertEqual(ricerca.trova_video(self.partenza), self.video)
        self.assertRegex(ricerca.hash_opensubtitles(self.video), r"^[0-9a-f]{16}$")
        piccolo = self.cartella / "piccolo.mkv"
        piccolo.write_bytes(b"x" * 1000)
        self.assertIsNone(ricerca.hash_opensubtitles(piccolo))

    def test_ai_minuscolo_non_e_un_indizio(self):
        c = ricerca._candidato("SUBDL", "Film.di.Prova", testi=["Grazie ai traduttori della community"])
        self.assertIsNone(ricerca.motivo_prima_del_download(c))


if __name__ == "__main__":
    unittest.main()
