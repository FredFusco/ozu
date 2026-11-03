#!/usr/bin/env python3
"""Ozu: l'app che traduce in italiano i sottotitoli di un film.

Apre una finestra propria (pywebview, WebKit di macOS) con l'interfaccia, servita
da un piccolo server interno raggiungibile solo da questo computer. L'interfaccia
fa scegliere i sottotitoli, prepara la scheda del film (personaggi, registri,
glossario) che si può correggere, lancia la traduzione (traduci.py), mostra
l'avanzamento e i risultati. Ogni lavoro è una sessione salvata, che si può
riaprire in seguito.

    ~/.venvs/ozu/bin/python app.py
"""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import adattamento
import aggiornamenti
import chiavi
import motore_claude
import motore_locale
import motori
import pulizia
import revisione
import sottotitoli as st

CARTELLA = Path(__file__).resolve().parent
PORTA = 8767  # Vertov usa 8765, Murnau 8766
DATI = chiavi.DATI
SESSIONI = DATI / "sessioni"
FILE_CORRENTE = DATI / "sessione_corrente.json"
REGISTRO = DATI / "ozu.log"
PREFERENZE = DATI / "preferenze.json"  # scelte dell'interfaccia, per esempio «non chiedere conferma all'uscita»

CAMPI = ["nome", "lingua_sub", "lingua_audio", "note", "motore", "modello", "out", "cerca_esistenti", "video",
         "rilettura"]
PREDEFINITI = {"lingua_sub": "auto", "lingua_audio": "auto", "note": "", "motore": motori.PREDEFINITO,
               "modello": motore_claude.MODELLO, "out": "", "file": [], "esiti": {}, "scheda": "", "costo": 0,
               # Ricerca di sottotitoli italiani esistenti prima di tradurre (ricerca.py): attiva di serie.
               # video: il video del film per la ricerca per hash; vuoto = quello accanto ai sottotitoli.
               "cerca_esistenti": True, "video": "", "ricerca": {},
               # Con il modello locale, rilettura di tutta la traduzione con un secondo modello (revisione.py).
               "rilettura": True}
CODICE_TROVATI = 10  # traduci.py esce così quando la ricerca trova sottotitoli italiani


# --------------------------------------------------------------------------
# Sessioni
# --------------------------------------------------------------------------
# Una sessione è un lavoro: i sottotitoli scelti, le lingue, le note per l'adattatore,
# la scheda del film e i risultati. Ogni modifica viene salvata subito.

# L'interfaccia e il lavoro in corso modificano le sessioni da thread diversi: una modifica alla volta.
BLOCCO_SESSIONI = threading.RLock()


def scrivi_atomico(percorso: Path, testo: str) -> None:
    """Scrive accanto e poi sostituisce: un'interruzione non lascia mai un file a metà."""
    percorso.parent.mkdir(parents=True, exist_ok=True)
    provvisorio = percorso.with_name(percorso.name + ".tmp")
    provvisorio.write_text(testo, encoding="utf-8")
    os.replace(provvisorio, percorso)


def _file_sessione(id_: str) -> Path:
    return SESSIONI / f"{id_}.json"


def _scrivi_sessione(p: dict, corrente: bool = True) -> None:
    with BLOCCO_SESSIONI:
        p["modificata"] = time.strftime("%Y-%m-%d %H:%M")
        scrivi_atomico(_file_sessione(p["id"]), json.dumps(p, ensure_ascii=False, indent=1))
        if corrente:
            scrivi_atomico(FILE_CORRENTE, json.dumps({"id": p["id"]}))


def modifica_sessione(id_: str, modifica, corrente: bool = True) -> dict:
    """Rilegge la sessione dal disco, applica modifica(p) e la salva, tutto sotto lucchetto: così
    l'interfaccia e il lavoro in corso non si cancellano a vicenda gli aggiornamenti."""
    with BLOCCO_SESSIONI:
        p = {**PREDEFINITI, **json.loads(_file_sessione(id_).read_text())}
        modifica(p)
        _scrivi_sessione(p, corrente)
        return p


def _id_corrente() -> str:
    try:
        return json.loads(FILE_CORRENTE.read_text())["id"]
    except (OSError, ValueError, KeyError):
        return ""


def nuova_sessione(nome: str = "") -> dict:
    id_ = time.strftime("%Y%m%d-%H%M%S")
    while _file_sessione(id_).exists():
        id_ += "b"
    p = {**PREDEFINITI, "id": id_, "nome": nome.strip() or "Nuova sessione", "creata": time.strftime("%Y-%m-%d %H:%M")}
    _scrivi_sessione(p)
    return p


def carica_sessione(id_: str) -> dict:
    return {**PREDEFINITI, **json.loads(_file_sessione(id_).read_text())}


def carica_progetto() -> dict:
    try:
        return {**PREDEFINITI, **json.loads(_file_sessione(_id_corrente()).read_text())}
    except (OSError, ValueError):
        return nuova_sessione()


def elenco_sessioni() -> list:
    sessioni = []
    for f in SESSIONI.glob("*.json"):
        try:
            p = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        tradotti = sum(1 for e in p.get("esiti", {}).values() if e.get("uscita"))
        sessioni.append({"id": p["id"], "nome": p.get("nome", ""), "modificata": p.get("modificata", ""),
                         "file": len(p.get("file", [])), "tradotti": tradotti})
    return sorted(sessioni, key=lambda s: s["modificata"], reverse=True)


def _nel_cestino(percorso: Path) -> None:
    """Sposta nel Cestino di macOS (recuperabile), senza cancellare definitivamente."""
    from Foundation import NSFileManager, NSURL
    ok, _, errore = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
        NSURL.fileURLWithPath_(str(percorso)), None, None)
    if not ok:
        raise RuntimeError(f"Non riesco a spostare nel Cestino {percorso.name}: {errore}")


def elimina_sessione(id_: str, risultati: bool) -> dict:
    """Toglie la sessione; con risultati=True mette nel Cestino anche traduzioni e scheda.
    I sottotitoli originali non vengono mai toccati."""
    if LAVORO.attivo() and LAVORO.sessione == id_:
        raise RuntimeError("C'è una traduzione in corso in questa sessione: fermala prima di eliminarla.")
    p = json.loads(_file_sessione(id_).read_text())
    spostati = 0
    if risultati:
        bersagli = [e["uscita"] for e in p.get("esiti", {}).values() if e.get("uscita")]
        bersagli += [p["scheda"]] if p.get("scheda") else []
        for b in bersagli:
            if Path(b).exists():
                _nel_cestino(Path(b))
                spostati += 1
    with BLOCCO_SESSIONI:
        _file_sessione(id_).unlink(missing_ok=True)
        if _id_corrente() == id_:
            altre = elenco_sessioni()
            if altre:
                scrivi_atomico(FILE_CORRENTE, json.dumps({"id": altre[0]["id"]}))
            else:
                nuova_sessione()
    return {"spostati": spostati}


# --------------------------------------------------------------------------
# Materiali
# --------------------------------------------------------------------------

def _gia_tradotto(percorso: Path) -> bool:
    return bool(re.search(r"\.it$", percorso.stem, re.I))


def aggiungi_materiali(id_: str, percorsi: list) -> tuple:
    """Aggiunge sottotitoli e video alla sessione; da una cartella prende i sottotitoli che contiene.
    Restituisce (esiti, sessione aggiornata)."""
    esiti = []
    p = modifica_sessione(id_, lambda p: _aggiungi(p, percorsi, esiti))
    return esiti, p


def _aggiungi(p: dict, percorsi: list, esiti: list) -> None:
    for x in map(Path, percorsi):
        candidati = sorted(f for f in x.rglob("*") if f.suffix.lower() in st.ESTENSIONI) if x.is_dir() else [x]
        if x.is_dir() and not candidati:
            esiti.append({"nome": x.name, "problema": "nella cartella non ci sono sottotitoli"})
        for f in candidati:
            ext = f.suffix.lower()
            if ext not in st.ESTENSIONI + st.VIDEO:
                esiti.append({"nome": f.name, "problema": "non è un file di sottotitoli né un video"})
            elif _gia_tradotto(f):
                esiti.append({"nome": f.name, "problema": "sembra già una traduzione italiana: lo salto"})
            elif str(f) in p["file"]:
                esiti.append({"nome": f.name, "problema": "c'è già"})
            else:
                p["file"].append(str(f))
                esiti.append({"nome": f.name, "ok": True})
    if p["file"] and p.get("nome") in ("", "Nuova sessione"):
        p["nome"] = Path(p["file"][0]).stem


_SCHEDA_LETTA = {"chiave": None, "scheda": None}


def leggi_scheda(p: dict):
    """La scheda della sessione; si rilegge dal disco solo se il file è cambiato (l'interfaccia la
    chiede ogni secondo durante un lavoro)."""
    if not p.get("scheda"):
        return None
    try:
        chiave = (p["scheda"], Path(p["scheda"]).stat().st_mtime_ns)
        if chiave != _SCHEDA_LETTA["chiave"]:
            _SCHEDA_LETTA.update(chiave=chiave, scheda=json.loads(Path(p["scheda"]).read_text(encoding="utf-8")))
        return _SCHEDA_LETTA["scheda"]
    except (OSError, ValueError):
        return None


def da_tradurre(p: dict) -> list:
    """I file della sessione che non hanno ancora una traduzione (ancora presente sul disco)."""
    return [f for f in p["file"] if not (p["esiti"].get(f, {}).get("uscita")
                                         and Path(p["esiti"][f]["uscita"]).exists())]


def comando(p: dict, solo_scheda: bool = False, tutti: bool = False, cerca: bool = True) -> list:
    file = p["file"] if solo_scheda or tutti else da_tradurre(p)
    cmd = [sys.executable, "-u", str(CARTELLA / "traduci.py"), *file, "--motore", p["motore"],
           "--da", p["lingua_sub"], "--audio", p["lingua_audio"]]
    if p["motore"] == "claude":
        cmd += ["--modello", p["modello"]]
    if p.get("note", "").strip():
        cmd += ["--note", p["note"]]
    if not p.get("rilettura", True):
        cmd.append("--senza-rilettura")
    if p.get("out"):
        cmd += ["--out", p["out"]]
    if p.get("scheda") and Path(p["scheda"]).exists() and not solo_scheda:
        cmd += ["--scheda", p["scheda"]]
    elif p.get("scheda"):
        cmd += ["--salva-scheda", p["scheda"]]
    if solo_scheda:
        cmd.append("--solo-scheda")
    elif cerca and p.get("cerca_esistenti"):
        cmd += ["--cerca", *_video(p, file)]
    return cmd


def _video(p: dict, file: list) -> list:
    """--video VIDEO se la sessione ne indica uno e c'è un solo file; altrimenti lo si cerca accanto."""
    return ["--video", p["video"]] if p.get("video") and len(file) == 1 else []


def comando_ricerca(p: dict) -> list:
    return [sys.executable, "-u", str(CARTELLA / "traduci.py"), "cerca", *p["file"], *_video(p, p["file"])]


# --------------------------------------------------------------------------
# Lavoro in corso (un solo processo alla volta)
# --------------------------------------------------------------------------

class Lavoro:
    def __init__(self):
        self.processo = None
        self.tipo = ""
        self.log = deque(maxlen=300)
        self.fase = ""
        self.avanz = {}
        self.file = {}
        self.motore = ""
        self.costo = 0.0
        self.inizio = 0.0
        self.codice = None
        self.sessione = ""
        self.blocco = threading.Lock()

    def attivo(self) -> bool:
        return self.processo is not None and self.processo.poll() is None

    def avvia(self, cmd: list, tipo: str, sessione: str) -> None:
        with self.blocco:
            if self.attivo():
                raise RuntimeError("C'è già una traduzione in corso.")
            self.tipo, self.fase, self.avanz, self.file, self.codice = tipo, "avvio", {}, {}, None
            self.sessione, self.motore, self.costo = sessione, "", 0.0
            self.log.clear()
            self.inizio = time.time()
            # Il registro dell'app (ozu.log) conserva tutto, anche dopo aver chiuso Ozu.
            print(f"\n== {time.strftime('%Y-%m-%d %H:%M:%S')} {tipo}, sessione {sessione}\n   "
                  + " ".join(cmd[2:]), flush=True)
            self.processo = subprocess.Popen(
                cmd, cwd=CARTELLA, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, start_new_session=True,
                env={**os.environ, "PYTHONWARNINGS": "ignore", "PYTHONIOENCODING": "utf-8"})
        threading.Thread(target=self._leggi, daemon=True).start()

    def _registra(self, aggiorna) -> None:
        """Modifica la sessione del lavoro, anche se nel frattempo l'utente ne ha aperta un'altra."""
        try:
            modifica_sessione(self.sessione, aggiorna, corrente=False)
        except (OSError, ValueError) as e:
            print(f"Non riesco ad aggiornare la sessione {self.sessione}: {e}", flush=True)

    def _leggi(self) -> None:
        # Questo thread deve leggere fino in fondo: se si fermasse, il canale si riempirebbe e
        # traduci.py resterebbe bloccato. Per questo ogni riga è gestita a sé.
        for riga in self.processo.stdout:
            try:
                self._riga(riga.rstrip())
            except Exception as e:
                print(f"Riga non capita ({e}): {riga.rstrip()[:200]}", flush=True)
        self.codice = self.processo.wait()
        print(f"{time.strftime('%H:%M:%S')} == fine, codice {self.codice}", flush=True)
        if self.costo:
            self._registra(lambda p: p.update(costo=round(p.get("costo", 0) + self.costo, 2)))
        # L'ultimo errore resta nella sessione, per mostrarlo anche dopo aver riaperto Ozu.
        errore = None
        if self.codice not in (0, -signal.SIGTERM, CODICE_TROVATI):
            errore = {"quando": time.strftime("%Y-%m-%d %H:%M"), "fase": self.fase, "righe": list(self.log)[-12:]}
        self._registra(lambda p: p.update(ultimo_errore=errore))
        avvisa_fine(self.tipo, self.codice)

    def _riga(self, riga: str) -> None:
        if not riga.startswith("@@avanz "):
            print(f"{time.strftime('%H:%M:%S')} {riga}", flush=True)
        if riga.startswith("@@fase "):
            self.fase = riga.split(" ", 1)[1]
        elif riga.startswith("@@avanz "):
            _, nome, fatti, totale = riga.split()
            precedente = self.avanz.get(nome, {})
            primo = precedente.get("primo") if precedente.get("totale") == int(totale) else None
            self.avanz[nome] = {"fatti": int(fatti), "totale": int(totale),
                                "primo": primo or (int(fatti), time.time()), "ora": time.time()}
        elif riga.startswith("@@"):
            tipo, _, dati = riga[2:].partition(" ")
            dati = json.loads(dati)
            if tipo == "file":
                self.file = dati
                self.avanz.pop("battute", None)
            elif tipo == "motore":
                self.motore = dati
            elif tipo == "costo":
                self.costo = dati
            elif tipo == "scheda":
                self._registra(lambda p: p.update(scheda=dati))
            elif tipo == "ricerca":
                self._registra(lambda p: p["ricerca"].update({dati["file"]: {**dati, "quando": time.strftime(
                    "%Y-%m-%d %H:%M")}}))
            elif tipo == "esito":
                quando = time.strftime("%Y-%m-%d %H:%M")
                self._registra(lambda p: p["esiti"].update({dati["file"]: {**dati, "quando": quando}}))
        elif riga:
            self.log.append(riga)

    def ferma(self) -> None:
        if self.attivo():
            os.killpg(self.processo.pid, signal.SIGTERM)

    def stato(self) -> dict:
        avanz = {}
        for nome, a in self.avanz.items():
            fatti0, t0 = a["primo"]
            eta = None
            if fatti0 < a["fatti"] < a["totale"]:
                eta = (a["ora"] - t0) / (a["fatti"] - fatti0) * (a["totale"] - a["fatti"])
            avanz[nome] = {"fatti": a["fatti"], "totale": a["totale"], "eta": eta}
        return {"attivo": self.attivo(), "tipo": self.tipo, "fase": self.fase, "avanzamento": avanz,
                "file": self.file, "motore": self.motore, "costo": self.costo,
                "durata": time.time() - self.inizio if self.inizio else 0, "codice": self.codice,
                "sessione": self.sessione, "log": list(self.log)[-80:]}


LAVORO = Lavoro()


def avvisa_fine(tipo: str, codice: int) -> None:
    """Suono e notifica di macOS quando un lavoro finisce (non se l'ha fermato l'utente)."""
    if codice == -signal.SIGTERM:
        return
    if codice == CODICE_TROVATI:
        suono, testo = "Glass", "Trovati sottotitoli italiani già esistenti: controllali prima di tradurre."
    elif codice == 0 and tipo == "ricerca":
        suono, testo = "Glass", "Nessun sottotitolo italiano valido trovato."
    elif codice == 0 and tipo == "scheda":
        suono, testo = "Glass", "Scheda del film pronta: controllala prima di tradurre."
    elif codice == 0:
        suono, testo = "Hero", "Traduzione completata."
    else:
        suono, testo = "Basso", "La traduzione si è interrotta con un errore."
    subprocess.Popen(["afplay", f"/System/Library/Sounds/{suono}.aiff"])
    subprocess.Popen(["osascript", "-e", f'display notification "{testo}" with title "Ozu"'])


# --------------------------------------------------------------------------
# Impostazioni e controlli
# --------------------------------------------------------------------------

def impostazioni() -> dict:
    def mascherata(servizio):
        c = chiavi.leggi(servizio)
        if not c:
            return ""
        # La password non si mostra nemmeno in parte; l'utente sì.
        return "impostata" if servizio == "opensubtitles_password" else c if servizio == "opensubtitles_utente" \
            else f"…{c[-6:]}"
    da_ambiente = {s: bool(os.environ.get(v)) for s, v in chiavi.VARIABILI.items()}
    return {**{s: mascherata(s) for s in chiavi.VARIABILI}, "da_ambiente": da_ambiente,
            "conferma_uscita": not leggi_preferenze().get("non_chiedere_uscita")}


def salva_impostazioni(dati: dict) -> None:
    for servizio in chiavi.VARIABILI:
        if servizio in dati:
            chiavi.salva(servizio, dati[servizio])
    if "conferma_uscita" in dati:
        scrivi_preferenze(non_chiedere_uscita=not dati["conferma_uscita"])


def leggi_preferenze() -> dict:
    try:
        return json.loads(PREFERENZE.read_text())
    except (OSError, ValueError):
        return {}


def scrivi_preferenze(**valori) -> None:
    scrivi_atomico(PREFERENZE, json.dumps({**leggi_preferenze(), **valori}, ensure_ascii=False))


def conferma_uscita():
    """Prima di chiudere la finestra o uscire da Ozu: il dialogo di Ozu, con il suo logo e la casella
    «Non mostrare più» (quello di pywebview mostrava l'icona di Python e non la permetteva).
    pywebview la chiama sul thread principale; restituire False annulla la chiusura."""
    if leggi_preferenze().get("non_chiedere_uscita"):
        return True
    import AppKit
    AppKit.NSRunningApplication.currentApplication().activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
    avviso = AppKit.NSAlert.alloc().init()
    avviso.setMessageText_("Chiudere Ozu?")
    avviso.setInformativeText_("Se c'è una traduzione in corso verrà fermata: rilanciandola riprenderà dal punto "
                               "in cui si è interrotta.")
    avviso.addButtonWithTitle_("Esci da Ozu")
    avviso.addButtonWithTitle_("Annulla")
    icona = AppKit.NSImage.alloc().initWithContentsOfFile_(str(CARTELLA / "icona_ozu.png"))
    if icona:
        avviso.setIcon_(icona)
    avviso.setShowsSuppressionButton_(True)
    avviso.suppressionButton().setTitle_("Non mostrare più")
    esci = avviso.runModal() == AppKit.NSAlertFirstButtonReturn
    if esci and avviso.suppressionButton().state() == AppKit.NSControlStateValueOn:
        scrivi_preferenze(non_chiedere_uscita=True)
    return esci


_PREREQUISITI = {"quando": 0.0, "valore": None}


def prerequisiti(subito: bool = False) -> dict:
    """Motori, ffmpeg e Ollama. L'interfaccia chiede lo stato ogni secondo: il controllo vero (che
    interroga Ollama) si rifà al massimo ogni 30 secondi, o subito dopo aver cambiato le chiavi."""
    if subito or time.time() - _PREREQUISITI["quando"] > 30:
        ffmpeg = any(Path(d, "ffmpeg").exists() for d in ("/opt/homebrew/bin", "/usr/local/bin"))
        _PREREQUISITI["valore"] = {"motori": motori.configurati(), "ffmpeg": ffmpeg,
                                   "modello_locale": motore_locale.NOME, "locale_pronto": motore_locale.pronto(),
                                   "revisore_locale": revisione.NOME_REVISORE}
        _PREREQUISITI["quando"] = time.time()
    return _PREREQUISITI["valore"]


FINESTRA = None  # la finestra dell'app, per le finestre di scelta dei file


def scegli(tipo: str):
    """Finestra di scelta nativa; restituisce una lista di percorsi o None se annullata."""
    import webview
    finestra_dialogo = getattr(webview, "FileDialog", None)
    if finestra_dialogo:
        modo = {"cartella": finestra_dialogo.FOLDER, "salva": finestra_dialogo.SAVE}.get(tipo, finestra_dialogo.OPEN)
    else:
        modo = {"cartella": webview.FOLDER_DIALOG, "salva": webview.SAVE_DIALOG}.get(tipo, webview.OPEN_DIALOG)
    scelti = FINESTRA.create_file_dialog(modo, allow_multiple=tipo == "file")
    if not scelti:
        return None
    return [s.rstrip("/") for s in (scelti if isinstance(scelti, (list, tuple)) else [scelti])]


# --------------------------------------------------------------------------
# Server dell'interfaccia
# --------------------------------------------------------------------------

class Gestore(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _invia(self, corpo: bytes, tipo: str, codice: int = 200):
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, dati, codice=200):
        self._invia(json.dumps(dati, ensure_ascii=False).encode(), "application/json; charset=utf-8", codice)

    def _corpo(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        progetto = carica_progetto()
        if url.path == "/":
            self._invia((CARTELLA / "interfaccia.html").read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/aiuto":
            self._invia((CARTELLA / "aiuto.html").read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/icona.png":
            self._invia((CARTELLA / "icona_ozu.png").read_bytes(), "image/png")
        elif url.path == "/api/stato":
            mancanti = [f for f in progetto["file"] if not Path(f).exists()]
            self._json({"progetto": progetto, "lavoro": LAVORO.stato(), "mancanti": mancanti,
                        "scheda": leggi_scheda(progetto), "prerequisiti": prerequisiti(),
                        "versione": aggiornamenti.versione()})
        elif url.path == "/api/sessioni":
            self._json({"corrente": progetto["id"], "sessioni": elenco_sessioni()})
        elif url.path == "/api/impostazioni":
            self._json(impostazioni())
        elif url.path == "/api/aggiornamenti":
            self._json({"installazione": aggiornamenti.INSTALLAZIONE, "versione": aggiornamenti.versione(),
                        "automatico": aggiornamenti.leggi_stato().get("automatico", True)})
        elif url.path == "/api/pulizia":
            self._json({"voci": pulizia.voci()})
        elif url.path == "/api/lingue":
            self._json({k: v.split(" (")[0] for k, v in adattamento.LINGUE.items()})
        else:
            self.send_error(404)

    def do_POST(self):
        url = urlparse(self.path)
        dati = self._corpo()
        progetto = carica_progetto()
        try:
            if url.path == "/api/sfoglia":
                # La finestra di scelta resta aperta finché l'utente non sceglie: nessun lucchetto intanto.
                scelti = scegli(dati.get("tipo", "file"))
                esiti, progetto = aggiungi_materiali(progetto["id"], scelti) if scelti else ([], progetto)
                self._json({"esiti": esiti, "progetto": progetto})
            elif url.path == "/api/aggiungi":
                esiti, progetto = aggiungi_materiali(progetto["id"], dati.get("percorsi", []))
                self._json({"esiti": esiti, "progetto": progetto})
            elif url.path == "/api/togli":
                progetto = modifica_sessione(progetto["id"], lambda p: p.update(
                    file=[f for f in p["file"] if f != dati.get("percorso")]))
                self._json({"ok": True, "progetto": progetto})
            elif url.path == "/api/progetto":
                progetto = modifica_sessione(progetto["id"], lambda p: p.update(
                    {k: dati[k] for k in CAMPI if k in dati}))
                self._json({"ok": True, "progetto": progetto})
            elif url.path == "/api/cartella-uscita":
                scelta = scegli("cartella")
                if scelta:
                    progetto = modifica_sessione(progetto["id"], lambda p: p.update(out=scelta[0]))
                self._json({"ok": True, "progetto": progetto})
            elif url.path == "/api/sessione":
                azione = dati.get("azione")
                if azione in ("nuova", "apri") and LAVORO.attivo():
                    raise RuntimeError("C'è una traduzione in corso: fermala prima di cambiare sessione.")
                if azione == "nuova":
                    progetto = nuova_sessione(dati.get("nome", ""))
                elif azione == "apri":
                    progetto = {**PREDEFINITI, **json.loads(_file_sessione(dati["id"]).read_text())}
                    _scrivi_sessione(progetto)
                elif azione == "elimina":
                    esito = elimina_sessione(dati["id"], bool(dati.get("risultati")))
                    self._json({"ok": True, "progetto": carica_progetto(), **esito})
                    return
                elif azione == "salva":
                    nome = dati.get("nome", "").strip()
                    progetto = modifica_sessione(progetto["id"], lambda p: p.update(nome=nome or p["nome"]))
                self._json({"ok": True, "progetto": progetto})
            elif url.path == "/api/scheda":
                azione = dati.get("azione")
                if azione == "salva":
                    if not progetto.get("scheda"):
                        raise RuntimeError("Non c'è ancora una scheda da salvare.")
                    scrivi_atomico(Path(progetto["scheda"]), json.dumps(dati["scheda"], ensure_ascii=False, indent=2))
                elif azione == "usa":
                    scelta = scegli("file")
                    if scelta:
                        json.loads(Path(scelta[0]).read_text(encoding="utf-8"))  # deve essere una scheda valida
                        progetto = modifica_sessione(progetto["id"], lambda p: p.update(scheda=scelta[0]))
                elif azione == "dimentica":
                    progetto = modifica_sessione(progetto["id"], lambda p: p.update(scheda=""))
                self._json({"ok": True, "progetto": progetto, "scheda": leggi_scheda(progetto)})
            elif url.path == "/api/avvia":
                if not progetto["file"]:
                    raise RuntimeError("Aggiungi prima i sottotitoli da tradurre.")
                mancanti = [Path(f).name for f in progetto["file"] if not Path(f).exists()]
                if mancanti:
                    raise RuntimeError("Non trovo più " + ", ".join(mancanti) + ": aggiungili di nuovo o toglili.")
                solo_scheda = dati.get("fase") == "scheda"
                if solo_scheda and not motori.adatta(motori.disponibile(progetto["motore"])):
                    raise RuntimeError("La scheda del film la preparano il modello locale o Claude: "
                                       "scegli uno dei due come motore.")
                if not solo_scheda and not dati.get("tutti") and not da_tradurre(progetto):
                    self._json({"gia_tradotti": True})
                    return
                if solo_scheda:  # una scheda nuova al posto di quella vecchia
                    progetto = modifica_sessione(progetto["id"], lambda p: p.update(scheda=""))
                # «Traduci comunque»: la ricerca c'è già stata e l'utente ha scartato quello che ha trovato.
                cerca = not dati.get("traduci_comunque")
                if cerca and progetto.get("cerca_esistenti") and not solo_scheda:
                    progetto = modifica_sessione(progetto["id"], lambda p: p.update(ricerca={}))
                LAVORO.avvia(comando(progetto, solo_scheda, bool(dati.get("tutti")), cerca),
                             "scheda" if solo_scheda else "traduzione", progetto["id"])
                self._json({"ok": True})
            elif url.path == "/api/cerca-sottotitoli":
                # Solo la ricerca, per l'interfaccia o per altri strumenti. Con "attendi": true risponde a
                # ricerca finita, con l'esito; altrimenti subito, e l'esito arriva in /api/stato.
                if dati.get("file"):
                    progetto = {**progetto, "file": [dati["file"]], "video": dati.get("video", "")}
                if not progetto["file"]:
                    raise RuntimeError("Indica un file di sottotitoli (o aggiungilo alla sessione).")
                mancanti = [Path(f).name for f in progetto["file"] if not Path(f).exists()]
                if mancanti:
                    raise RuntimeError("Non trovo " + ", ".join(mancanti) + ".")
                modifica_sessione(progetto["id"], lambda p: p.update(ricerca={}))
                LAVORO.avvia(comando_ricerca(progetto), "ricerca", progetto["id"])
                if not dati.get("attendi"):
                    self._json({"ok": True})
                    return
                for _ in range(1800):  # al massimo 15 minuti
                    if LAVORO.codice is not None:
                        break
                    time.sleep(0.5)
                esito = carica_sessione(progetto["id"]).get("ricerca", {})
                self._json({"ok": True, "trovati": LAVORO.codice == CODICE_TROVATI, "ricerca": esito,
                            "codice": LAVORO.codice})
            elif url.path == "/api/scegli-video":
                scelta = scegli("file")
                if scelta:
                    progetto = modifica_sessione(progetto["id"], lambda p: p.update(video=scelta[0]))
                self._json({"ok": True, "progetto": progetto})
            elif url.path == "/api/ferma":
                LAVORO.ferma()
                self._json({"ok": True})
            elif url.path == "/api/impostazioni":
                salva_impostazioni(dati)
                self._json({"ok": True, **impostazioni(), "prerequisiti": prerequisiti(subito=True)})
            elif url.path == "/api/aggiornamenti":
                azione = dati.get("azione")
                if azione == "controlla":
                    manuale = bool(dati.get("manuale"))
                    if not manuale and not aggiornamenti.da_controllare():
                        self._json({"da_fare": False})
                    else:
                        self._json(aggiornamenti.controlla(manuale))
                elif azione == "installa":
                    if LAVORO.attivo():
                        raise RuntimeError("Aspetta che finisca la traduzione in corso, o fermala, prima di aggiornare.")
                    aggiornamenti.installa(dati.get("voci") or [])
                    self._json({"ok": True})
                elif azione == "salta":
                    aggiornamenti.salta(dati.get("voci") or [])
                    self._json({"ok": True})
                elif azione == "automatico":
                    aggiornamenti.scrivi_stato(automatico=bool(dati.get("valore")))
                    self._json({"ok": True})
                elif azione == "riavvia":
                    self._json({"ok": True})
                    aggiornamenti.riavvia(PORTA)
                    threading.Timer(0.3, FINESTRA.destroy).start()
                else:
                    self.send_error(404)
            elif url.path == "/api/pulizia":
                if LAVORO.attivo() or aggiornamenti.INSTALLAZIONE["attiva"]:
                    raise RuntimeError("Aspetta che finisca il lavoro in corso prima di liberare spazio.")
                self._json({"liberati": pulizia.cancella(dati.get("chiavi") or [])})
            elif url.path == "/api/apri":
                # Solo file che appartengono alla sessione: originali, traduzioni, scheda.
                ammessi = set(progetto["file"]) | {progetto.get("scheda", "")} | {
                    e.get("uscita", "") for e in progetto["esiti"].values()} | {
                    (r.get("trovato") or {}).get("file", "") for r in progetto.get("ricerca", {}).values()}
                percorso = dati.get("percorso", "")
                if percorso not in ammessi - {""} or not Path(percorso).exists():
                    raise RuntimeError("Il file non c'è più.")
                subprocess.run(["open", "-R", percorso] if dati.get("mostra") else ["open", "-t", percorso])
                self._json({"ok": True})
            else:
                self.send_error(404)
        except Exception as e:
            self._json({"errore": str(e)}, 400)


# --------------------------------------------------------------------------
# Finestra e integrazione con macOS
# --------------------------------------------------------------------------

def identita_mac() -> None:
    """Nome e icona di Ozu nel Dock e nella barra dei menu (invece di "Python")."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = "Ozu"
        info["CFBundleShortVersionString"] = aggiornamenti.versione()
        icona = NSImage.alloc().initWithContentsOfFile_(str(CARTELLA / "icona_ozu.png"))
        if icona:
            NSApplication.sharedApplication().setApplicationIconImage_(icona)
    except Exception:
        pass


def collega_trascinamento(finestra) -> None:
    """File e cartelle lasciati sulla finestra: percorsi completi dal lato nativo."""
    from webview.dom import DOMEventHandler

    def lasciati(evento):
        percorsi = [f.get("pywebviewFullPath") for f in evento.get("dataTransfer", {}).get("files", [])]
        percorsi = [x for x in percorsi if x]
        if not percorsi:
            return
        try:
            esiti, _ = aggiungi_materiali(carica_progetto()["id"], percorsi)
        except Exception as e:
            esiti = [{"nome": "", "problema": str(e)}]
        finestra.evaluate_js(f"materialiAggiunti({json.dumps(esiti, ensure_ascii=False)})")

    finestra.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True, debounce=500)
    finestra.dom.document.events.drop += DOMEventHandler(lasciati, True, True)


def _js(comando: str):
    """Azione di menu che esegue un comando dell'interfaccia (ozuComando in interfaccia.html)."""
    def azione():
        if FINESTRA:
            FINESTRA.evaluate_js(f"ozuComando({json.dumps(comando)})")
    azione.__name__ = "menu_" + re.sub(r"\W", "_", comando)
    return azione


def apri_aiuto():
    import webview
    webview.create_window("Guida di Ozu", f"http://127.0.0.1:{PORTA}/aiuto", width=820, height=820,
                          background_color="#f3eee4", text_select=True)


def apri_registro():
    subprocess.run(["open", "-a", "Console", str(REGISTRO)] if REGISTRO.exists() else ["open", str(DATI)])


def crea_menu() -> list:
    from webview.menu import Menu, MenuAction, MenuSeparator
    return [
        Menu("File", [
            MenuAction("Nuova sessione", _js("nuova")),
            MenuAction("Apri sessione…", _js("apri")),
            MenuAction("Salva sessione", _js("salva")),
            MenuAction("Elimina sessione…", _js("elimina")),
            MenuSeparator(),
            MenuAction("Aggiungi sottotitoli…", _js("aggiungi-file")),
            MenuAction("Aggiungi una cartella…", _js("aggiungi-cartella")),
            MenuAction("Usa una scheda esistente…", _js("usa-scheda")),
            MenuSeparator(),
            MenuAction("Libera spazio…", _js("pulizia")),
            MenuAction("Impostazioni…", _js("impostazioni")),
        ]),
        Menu("Vista", [
            MenuAction("Sottotitoli", _js("vista-materiali")),
            MenuAction("Scheda del film", _js("vista-scheda")),
            MenuAction("Traduzione", _js("vista-traduzione")),
            MenuSeparator(),
            MenuAction("Dettagli dell'elaborazione", _js("dettagli")),
        ]),
        Menu("Traduzione", [
            MenuAction("Prepara la scheda del film", _js("scheda")),
            MenuAction("Traduci", _js("traduci")),
            MenuAction("Ferma", _js("ferma")),
        ]),
        Menu("Aiuto", [
            MenuAction("Guida di Ozu", apri_aiuto),
            MenuAction("Controlla aggiornamenti…", _js("aggiornamenti")),
            MenuAction("Apri il registro di Ozu", apri_registro),
        ]),
    ]


# Scorciatoie da tastiera dei menu: titolo -> (tasto, maiuscole)
SCORCIATOIE = {
    "Nuova sessione": ("n", False), "Apri sessione…": ("o", False), "Salva sessione": ("s", False),
    "Aggiungi sottotitoli…": ("o", True), "Impostazioni…": (",", False), "Sottotitoli": ("1", False),
    "Scheda del film": ("2", False), "Traduzione": ("3", False), "Prepara la scheda del film": ("r", True),
    "Traduci": ("r", False), "Ferma": (".", False), "Guida di Ozu": ("?", False),
}
# Voci che su macOS stanno nel menu dell'app, sotto «Informazioni su».
NEL_MENU_APP = ["Impostazioni…", "Controlla aggiornamenti…"]


def sistema_menu() -> None:
    """Completa la barra dei menu con le parti native: Modifica, Finestra, scorciatoie, ricerca nell'Aiuto."""
    import AppKit
    app = AppKit.NSApplication.sharedApplication()
    barra = app.mainMenu()
    if barra is None:
        return

    def menu_nativo(titolo, voci):
        menu = AppKit.NSMenu.alloc().initWithTitle_(titolo)
        for voce in voci:
            if voce is None:
                menu.addItem_(AppKit.NSMenuItem.separatorItem())
                continue
            nome, azione, tasto, *mod = voce
            item = menu.addItemWithTitle_action_keyEquivalent_(nome, azione, tasto)
            if mod:
                item.setKeyEquivalentModifierMask_(mod[0])
        contenitore = AppKit.NSMenuItem.alloc().init()
        contenitore.setTitle_(titolo)
        contenitore.setSubmenu_(menu)
        return contenitore, menu

    cmd, maiusc, ctrl = AppKit.NSCommandKeyMask, AppKit.NSShiftKeyMask, AppKit.NSControlKeyMask
    titoli = [barra.itemAtIndex_(i).title() for i in range(barra.numberOfItems())]
    if "Modifica" not in titoli:
        voce, _ = menu_nativo("Modifica", [
            ("Annulla", "undo:", "z"), ("Ripeti", "redo:", "z", cmd | maiusc), None,
            ("Taglia", "cut:", "x"), ("Copia", "copy:", "c"), ("Incolla", "paste:", "v"),
            ("Seleziona tutto", "selectAll:", "a")])
        barra.insertItem_atIndex_(voce, min(2, barra.numberOfItems()))
    if "Finestra" not in titoli:
        voce, menu = menu_nativo("Finestra", [
            ("Contrai", "performMiniaturize:", "m"), ("Ridimensiona", "performZoom:", ""), None,
            ("Attiva modalità a tutto schermo", "toggleFullScreen:", "f", cmd | ctrl), None,
            ("Porta tutto in primo piano", "arrangeInFront:", "")])
        indice_aiuto = next((i for i in range(barra.numberOfItems()) if barra.itemAtIndex_(i).title() == "Aiuto"),
                            barra.numberOfItems())
        barra.insertItem_atIndex_(voce, indice_aiuto)
        app.setWindowsMenu_(menu)
    menu_app = barra.itemAtIndex_(0).submenu()
    for posizione, titolo in enumerate(NEL_MENU_APP, 1):
        for i in range(1, barra.numberOfItems()):
            sotto = barra.itemAtIndex_(i).submenu()
            voce = sotto.itemWithTitle_(titolo) if sotto is not None else None
            if voce is not None and menu_app is not None:
                sotto.removeItem_(voce)
                menu_app.insertItem_atIndex_(voce, posizione)
                break
    for i in range(barra.numberOfItems()):
        sotto = barra.itemAtIndex_(i).submenu()
        if sotto is None:
            continue
        if barra.itemAtIndex_(i).title() == "Aiuto":
            app.setHelpMenu_(sotto)
        visti = set()
        for j in range(sotto.numberOfItems()):
            item = sotto.itemAtIndex_(j)
            titolo = item.title()
            if titolo in SCORCIATOIE and titolo not in visti and item.submenu() is None:
                tasto, con_maiusc = SCORCIATOIE[titolo]
                item.setKeyEquivalent_(tasto)
                item.setKeyEquivalentModifierMask_(cmd | (maiusc if con_maiusc else 0))
                visti.add(titolo)


def main():
    global FINESTRA
    import webview
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORTA), Gestore)
    except OSError:
        # Ozu è già aperto: si porta in primo piano la sua finestra.
        subprocess.run(["osascript", "-e", 'tell application "System Events" to set frontmost of '
                        '(first process whose name is "Ozu" or name is "Python") to true'], capture_output=True)
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    identita_mac()
    FINESTRA = webview.create_window(
        "Ozu", f"http://127.0.0.1:{PORTA}", width=1100, height=860, min_size=(720, 560),
        background_color="#f3eee4", text_select=True)
    FINESTRA.events.closing += conferma_uscita
    # Chiudendo la finestra si ferma un'eventuale traduzione: si potrà riprendere.
    FINESTRA.events.closed += LAVORO.ferma

    def all_avvio():
        identita_mac()
        collega_trascinamento(FINESTRA)
        from PyObjCTools import AppHelper
        AppHelper.callAfter(sistema_menu)

    webview.settings["SHOW_DEFAULT_MENUS"] = False
    webview.start(all_avvio, menu=crea_menu(), localization={
        "cocoa.menu.about": "Informazioni su", "cocoa.menu.services": "Servizi", "cocoa.menu.hide": "Nascondi",
        "cocoa.menu.hideOthers": "Nascondi altre", "cocoa.menu.showAll": "Mostra tutte", "cocoa.menu.quit": "Esci da",
        "global.ok": "Chiudi", "global.cancel": "Annulla",
    })
    LAVORO.ferma()
    server.shutdown()


if __name__ == "__main__":
    main()
