"""Ricerca di sottotitoli italiani già esistenti, da fare prima di tradurre.

Fonti, in quest'ordine, con i risultati uniti:
- OpenSubtitles, con la sua API REST ufficiale (chiave API; il login dà una quota di download
  più alta);
- SUBDL, con la sua API ufficiale (chiave API);
- Podnapisi: non ha un'API ufficiale documentata, quindi viene saltato (lo dice l'esito).
Una fonte senza chiave viene saltata con un avviso, non è un errore.

Cosa si scarta, e perché (ogni scarto finisce nell'esito e nel registro):
- i sottotitoli che la fonte segnala come tradotti automaticamente o con l'IA;
- quelli che nel nome, nel commento o nell'autore parlano di traduzione automatica;
- quelli a pagamento o da sbloccare;
- dopo il download, quelli con troppe righe vuote, righe non tradotte, caratteri di codifica
  sbagliati o una lunghezza media delle righe anomala. Le soglie sono in SOGLIE e si possono
  cambiare in ~/Library/Application Support/Ozu/ricerca.json.

I tempi si confrontano con i sottotitoli di partenza, che sono a tempo con il video da
sottotitolare. Un file che copre tutti i dialoghi così com'è vince subito. Uno con il testo
buono ma i tempi di un'altra edizione (frame rate diverso, cartelli in testa diversi, tagli
di montaggio) viene riallineato (allineamento.py) e tenuto di riserva: si usa solo se nessun
candidato ha già i tempi giusti, scegliendo quello che copre di più. In ogni caso i
sottotitoli devono coprire tutti i dialoghi della versione da sottotitolare: se manca una
scena il file viene scartato.

I candidati rimasti si ordinano: corrispondenza dell'hash del video, poi download e voti, poi
frame rate simile a quello del video. Si scarica il migliore (al massimo SOGLIE["scaricamenti_max"]
tentativi, per rispettare le quote), lo si salva in UTF-8 accanto al file di partenza come
«<nome del video> - Sottotitoli IT (scaricati).srt», senza mai sostituire un file esistente.
"""

import difflib
import io
import json
import re
import shutil
import struct
import subprocess
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import allineamento
import chiavi
import sottotitoli as st

LINGUA = "it"
FONTI = ["OpenSubtitles", "SUBDL", "Podnapisi"]
OPENSUBTITLES = "https://api.opensubtitles.com/api/v1"
SUBDL = "https://api.subdl.com/api/v1/subtitles"
SUBDL_DOWNLOAD = "https://dl.subdl.com"
CONFIGURAZIONE = chiavi.DATI / "ricerca.json"

SOGLIE = {
    "battute_min": 0.5,           # battute del file scaricato / battute di partenza (larghi: chi
    "battute_max": 2.0,           # sottotitola divide le frasi in modo diverso; i tempi si controllano a parte)
    "righe_vuote_max": 0.05,      # quota di battute vuote
    "non_tradotte_max": 0.10,     # quota di battute rimaste nella lingua di partenza
    "codifica_max": 0.01,         # quota di battute con caratteri di codifica sbagliati
    "lunghezza_media_min": 12,    # caratteri per riga
    "lunghezza_media_max": 60,
    "scaricamenti_max": 3,        # tentativi di download per ricerca
}

# Indizi di traduzione automatica nel nome del rilascio, nel commento o nell'autore.
# «AI»/«IA» solo in maiuscolo: «ai» minuscolo è una preposizione italiana («grazie ai traduttori»).
SIGLA_IA = re.compile(r"(?<![A-Za-z])(AI|A\.I\.|IA)(?![A-Za-z])")
AUTOMATICA = re.compile(
    r"\b(machine[\s._-]*translat\w*|auto[\s._-]*translat\w*|google[\s._-]*translat\w*|"
    r"deepl|chat[\s._-]*gpt|gpt[\s._-]*\d|openai|traduzion[ei][\s._-]*automatic[ah]e?|"
    r"tradott[oaie][\s._-]*(automaticamente|con[\s._-]*l'?ia|dall'?ia)|ia[\s._-]*generat\w*)\b", re.I)
A_PAGAMENTO = re.compile(r"\b(vip[\s._-]*only|only[\s._-]*vip|a[\s._-]*pagamento|premium|unlock|sblocca\w*)\b", re.I)
PAROLE_INGLESI = {"the", "you", "and", "to", "is", "what", "it's", "i'm", "don't", "that", "of", "are", "we",
                  "this", "have", "with", "for", "not", "your", "my", "be", "can"}
CODIFICA_ROTTA = re.compile(r"�|\w\?\w|Ã[\x80-\xbf¡-¿]|â€")


class Esito:
    """Quello che la ricerca ha fatto e trovato: si stampa come testo e si trasmette come JSON."""

    def __init__(self):
        self.fonti = []    # [{fonte, stato, risultati}]
        self.scarti = []   # [{fonte, nome, motivo}]
        self.trovato = None  # {fonte, nome, file, motivo}

    def fonte(self, fonte: str, stato: str, risultati: int = 0) -> None:
        if stato.startswith("interrogato"):
            stato += f" ({risultati} risultati in italiano)"
        self.fonti.append({"fonte": fonte, "stato": stato, "risultati": risultati})
        print(f"[ricerca] {fonte}: {stato}", flush=True)

    def scarta(self, candidato: dict, motivo: str) -> None:
        self.scarti.append({"fonte": candidato["fonte"], "nome": candidato["nome"], "motivo": motivo})
        print(f"[ricerca] scartato {candidato['fonte']} «{candidato['nome']}»: {motivo}", flush=True)

    def come_dati(self) -> dict:
        return {"trovato": self.trovato, "fonti": self.fonti, "scarti": self.scarti}

    def messaggio(self) -> str:
        if self.trovato:
            t = self.trovato
            return (f"Trovati sottotitoli italiani su {t['fonte']}: {Path(t['file']).name}\n"
                    f"Perché: {t['motivo']}\nLa traduzione non parte: confrontali con i sottotitoli di partenza "
                    "e, se non ti convincono, traduci comunque.")
        righe = ["Nessun sottotitolo italiano valido trovato. Fonti interrogate:"]
        righe += [f"- {f['fonte']}: {f['stato']}" for f in self.fonti]
        if self.scarti:
            righe.append("Scartati:")
            righe += [f"- {s['fonte']} «{s['nome']}»: {s['motivo']}" for s in self.scarti]
        return "\n".join(righe)


def soglie() -> dict:
    try:
        return {**SOGLIE, **json.loads(CONFIGURAZIONE.read_text())}
    except (OSError, ValueError):
        return dict(SOGLIE)


# --------------------------------------------------------------------------
# Richieste HTTP, con le pause e i 429
# --------------------------------------------------------------------------

USER_AGENT = "Ozu v1.1"
PAUSA_MINIMA = {"OpenSubtitles": 0.25, "SUBDL": 1.0}  # secondi tra due richieste alla stessa fonte
_ULTIMA = {}


class ErroreFonte(Exception):
    """Un problema con una fonte: si salta quella fonte e si va avanti con le altre."""


def _http(metodo: str, url: str, intestazioni: dict, corpo: bytes = None, timeout: float = 30):
    """(codice, intestazioni, corpo). Separata dal resto per poterla simulare nei test."""
    richiesta = urllib.request.Request(url, data=corpo, method=metodo, headers=intestazioni)
    try:
        with urllib.request.urlopen(richiesta, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def _chiedi(fonte: str, metodo: str, url: str, intestazioni: dict = None, corpo=None, tentativi: int = 3):
    """Una richiesta a una fonte, rispettando la pausa minima e i 429 (Retry-After, al massimo 60 s).
    Gli errori non riportano mai l'indirizzo, che per SUBDL contiene la chiave."""
    intestazioni = {"User-Agent": USER_AGENT, "Accept": "application/json", **(intestazioni or {})}
    dati = json.dumps(corpo).encode() if isinstance(corpo, dict) else corpo
    if isinstance(corpo, dict):
        intestazioni["Content-Type"] = "application/json"
    for tentativo in range(tentativi):
        attesa = PAUSA_MINIMA.get(fonte, 0.5) - (time.time() - _ULTIMA.get(fonte, 0))
        if attesa > 0:
            time.sleep(attesa)
        _ULTIMA[fonte] = time.time()
        try:
            codice, testate, risposta = _http(metodo, url, intestazioni, dati)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if tentativo == tentativi - 1:
                raise ErroreFonte(f"non risponde ({getattr(e, 'reason', e)})")
            time.sleep(2 * (tentativo + 1))
            continue
        if codice == 429:
            if tentativo == tentativi - 1:
                raise ErroreFonte("troppe richieste (429): riprova più tardi")
            ritardo = testate.get("Retry-After") or testate.get("retry-after") or 2 * (tentativo + 1)
            try:
                ritardo = min(60.0, float(ritardo))
            except ValueError:
                ritardo = 5.0
            print(f"[ricerca] {fonte}: limite di richieste, aspetto {ritardo:.0f} s", flush=True)
            time.sleep(ritardo)
            continue
        if codice >= 500 and tentativo < tentativi - 1:
            time.sleep(2 * (tentativo + 1))
            continue
        return codice, testate, risposta
    raise ErroreFonte("nessuna risposta valida")


def _json(risposta: bytes) -> dict:
    try:
        return json.loads(risposta or b"{}")
    except ValueError:
        raise ErroreFonte("risposta non valida")


# --------------------------------------------------------------------------
# Il film: video, hash, titolo e anno
# --------------------------------------------------------------------------

SUFFISSI_LINGUA = re.compile(r"([._ -]+(en|eng|english|it|ita|italian|fr|fre|es|spa|de|ger|ja|jpn|zh|chi|ko|kor|"
                             r"pt|por|ru|rus))+$|\s*-\s*subtitles?\b.*$|\s*-\s*sottotitoli\b.*$", re.I)
SPORCIZIA = re.compile(r"\b(\d{3,4}p|blu-?ray|bdrip|brrip|web-?dl|webrip|hdtv|dvdrip|x26[45]|h\.?26[45]|hevc|aac\d?"
                       r"|ac3|dts|10bit|remux|extended|unrated|repack|proper)\b.*$", re.I)


def base_del_nome(percorso: Path) -> str:
    """Il nome senza estensione né suffissi di lingua o «- Subtitles …»."""
    return SUFFISSI_LINGUA.sub("", percorso.stem).strip(" ._-")


def titolo_e_anno(percorso: Path) -> tuple:
    """(titolo, anno) ricavati dal nome del file, per esempio «Titolo di prova (2001)» o
    «Titolo.Di.Prova.2001.1080p.mkv». L'anno può mancare."""
    nome = base_del_nome(percorso)
    m = re.search(r"[(\[ ._-]((?:19|20)\d\d)(?:[)\] ._-]|$)", nome)
    anno = int(m.group(1)) if m else None
    titolo = nome[:m.start()] if m else SPORCIZIA.sub("", nome)
    titolo = re.sub(r"[._]+", " ", titolo)
    titolo = re.sub(r"\s+", " ", re.sub(r"[\[\](){}]", " ", titolo)).strip(" -")
    return titolo, anno


def trova_video(sottotitoli: Path) -> Path:
    """Il video accanto ai sottotitoli: stesso nome base, oppure stesso titolo e anno, oppure l'unico video
    della cartella. None se non c'è o se è ambiguo."""
    if sottotitoli.suffix.lower() in st.VIDEO:
        return sottotitoli
    video = sorted(f for f in sottotitoli.parent.iterdir() if f.suffix.lower() in st.VIDEO and f.is_file())
    base = base_del_nome(sottotitoli).lower()
    stessi = [v for v in video if base_del_nome(v).lower() == base]
    if len(stessi) == 1:
        return stessi[0]
    titolo = titolo_e_anno(sottotitoli)
    simili = [v for v in video if titolo_e_anno(v) == titolo and titolo[0]]
    if len(simili) == 1:
        return simili[0]
    return video[0] if len(video) == 1 else None


def hash_opensubtitles(video: Path):
    """L'hash di OpenSubtitles: dimensione del file più la somma, a parole di 64 bit, dei primi e
    degli ultimi 64 KB. None per i file troppo piccoli."""
    dimensione = video.stat().st_size
    blocco = 65536
    if dimensione < 2 * blocco:
        return None
    valore = dimensione
    with open(video, "rb") as f:
        for posizione in (0, dimensione - blocco):
            f.seek(posizione)
            for (parola,) in struct.iter_unpack("<Q", f.read(blocco)):
                valore = (valore + parola) & 0xFFFFFFFFFFFFFFFF
    return f"{valore:016x}"


def frame_rate(video: Path):
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                            "-of", "csv=p=0", str(video)], capture_output=True, text=True, timeout=30)
        num, _, den = r.stdout.strip().partition("/")
        return float(num) / float(den or 1)
    except (OSError, ValueError, ZeroDivisionError, subprocess.SubprocessError):
        return None


# --------------------------------------------------------------------------
# Fonti
# --------------------------------------------------------------------------

def _candidato(fonte: str, nome: str, **altro) -> dict:
    return {"fonte": fonte, "nome": nome or "(senza nome)", "hash": False, "download": 0, "voto": 0.0, "fps": None,
            "testi": [], **altro}


def cerca_opensubtitles(esito: Esito, hash_video, titolo: str, anno) -> tuple:
    """(candidati, sessione per il download)."""
    chiave = chiavi.leggi("opensubtitles")
    if not chiave:
        esito.fonte("OpenSubtitles", "saltato: manca la chiave API (Ozu › Impostazioni)")
        return [], None
    sessione = {"chiave": chiave, "base": OPENSUBTITLES, "token": None}
    utente, parola = chiavi.leggi("opensubtitles_utente"), chiavi.leggi("opensubtitles_password")
    try:
        if utente and parola:
            codice, _, risposta = _chiedi("OpenSubtitles", "POST", f"{OPENSUBTITLES}/login",
                                          {"Api-Key": chiave}, {"username": utente, "password": parola})
            if codice == 200:
                dati = _json(risposta)
                sessione["token"] = dati.get("token")
                if dati.get("base_url"):
                    sessione["base"] = f"https://{dati['base_url']}/api/v1"
            else:
                # Il messaggio dell'API non contiene le credenziali (per esempio: «usa il nome utente, non l'email»).
                motivo = _json(risposta).get("message", f"errore {codice}") if risposta else f"errore {codice}"
                print(f"[ricerca] OpenSubtitles: login non riuscito ({motivo.strip()}), continuo senza", flush=True)
        trovati = {}
        ricerche = []
        if hash_video:
            ricerche.append({"languages": LINGUA, "moviehash": hash_video})
        if titolo:
            ricerche.append({"languages": LINGUA, "query": titolo.lower(), **({"year": anno} if anno else {})})
        for parametri in ricerche:
            # L'API chiede i parametri in ordine alfabetico e in minuscolo.
            url = f"{sessione['base']}/subtitles?" + urllib.parse.urlencode(sorted(parametri.items()))
            codice, _, risposta = _chiedi("OpenSubtitles", "GET", url, _intestazioni_os(sessione))
            if codice in (401, 403):
                raise ErroreFonte("chiave API non valida o non autorizzata")
            if codice != 200:
                raise ErroreFonte(f"errore {codice}")
            for voce in _json(risposta).get("data", []):
                a = voce.get("attributes", {})
                for f in a.get("files", [])[:1]:
                    c = trovati.get(f.get("file_id"))
                    if c is None:
                        c = trovati[f.get("file_id")] = _candidato(
                            "OpenSubtitles", a.get("release") or f.get("file_name"), file_id=f.get("file_id"),
                            download=a.get("download_count") or 0, voto=float(a.get("ratings") or 0),
                            fps=a.get("fps") or None, lingua=a.get("language"),
                            ia=bool(a.get("ai_translated")), automatica=bool(a.get("machine_translated")),
                            testi=[a.get("release") or "", a.get("comments") or "", f.get("file_name") or "",
                                   (a.get("uploader") or {}).get("name") or ""], film=_film_os(a.get("feature_details")))
                    # Solo la conferma esplicita dell'API: anche cercando per hash arrivano risultati che non
                    # corrispondono (moviehash_match falso).
                    c["hash"] = c["hash"] or bool(a.get("moviehash_match"))
        esito.fonte("OpenSubtitles", "interrogato" + (" con il login" if sessione["token"] else ""), len(trovati))
        return list(trovati.values()), sessione
    except ErroreFonte as e:
        esito.fonte("OpenSubtitles", f"saltato: {e}")
        return [], None


def _film_os(dettagli) -> dict:
    """I titoli e l'anno del film a cui appartiene un risultato. movie_name è nella forma «2001 - Titolo»,
    con il titolo nella lingua di chi ha caricato i sottotitoli: utile quando il catalogo ha solo
    quello inglese."""
    d = dettagli or {}
    titoli = [d.get("title"), d.get("original_title"), re.sub(r"^\d{4}\s*-\s*", "", d.get("movie_name") or "")]
    return {"titoli": [t for t in titoli if t], "anno": d.get("year"), "id": d.get("imdb_id") or d.get("feature_id")}


def _semplice(testo: str) -> str:
    testo = unicodedata.normalize("NFKD", testo).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", testo).strip()


def stesso_film(c: dict, titolo: str, anno) -> bool:
    """Il risultato è dello stesso film? Titolo uguale o quasi (accenti e punteggiatura a parte) e
    anno uguale (o vicino di uno, per le uscite a cavallo d'anno). Senza dati sul film: sì."""
    film = c.get("film")
    if not film or not film["titoli"] or not titolo:
        return True
    if anno and film.get("anno") and abs(int(film["anno"]) - int(anno)) > 1:
        return False
    cercato = _semplice(titolo)
    return any(difflib.SequenceMatcher(None, cercato, _semplice(t)).ratio() >= 0.85 for t in film["titoli"])


def _intestazioni_os(sessione: dict) -> dict:
    testate = {"Api-Key": sessione["chiave"]}
    if sessione.get("token"):
        testate["Authorization"] = f"Bearer {sessione['token']}"
    return testate


def scarica_opensubtitles(sessione: dict, candidato: dict) -> bytes:
    codice, _, risposta = _chiedi("OpenSubtitles", "POST", f"{sessione['base']}/download", _intestazioni_os(sessione),
                                  {"file_id": candidato["file_id"]})
    dati = _json(risposta)
    if codice == 406 or (codice == 200 and dati.get("remaining", 1) < 0):
        raise ErroreFonte("quota di download esaurita" + (f" (si azzera {dati['reset_time']})"
                                                           if dati.get("reset_time") else ""))
    if codice in (401, 403):
        raise ErroreFonte("download non consentito: serve il login (utente e password nelle Impostazioni)")
    if codice != 200 or not dati.get("link"):
        raise ErroreFonte(f"download non riuscito ({codice})")
    codice, _, file = _chiedi("OpenSubtitles", "GET", dati["link"], {"Accept": "*/*"})
    if codice != 200:
        raise ErroreFonte(f"download non riuscito ({codice})")
    return file


def cerca_subdl(esito: Esito, titolo: str, anno) -> list:
    chiave = chiavi.leggi("subdl")
    if not chiave:
        esito.fonte("SUBDL", "saltato: manca la chiave API (Ozu › Impostazioni)")
        return []
    if not titolo:
        esito.fonte("SUBDL", "saltato: non ricavo il titolo dal nome del file")
        return []
    parametri = {"api_key": chiave, "film_name": titolo, "languages": LINGUA.upper(), "subs_per_page": 30,
                 "type": "movie", **({"year": anno} if anno else {})}
    try:
        codice, _, risposta = _chiedi("SUBDL", "GET", f"{SUBDL}?" + urllib.parse.urlencode(parametri))
        dati = _json(risposta)
        if codice in (401, 403) or dati.get("error") == "not_authorized":
            raise ErroreFonte("chiave API non valida")
        if codice != 200:
            raise ErroreFonte(f"errore {codice}")
        candidati = [_candidato("SUBDL", s.get("release_name") or s.get("name"), url=s.get("url"),
                                lingua=(s.get("language") or s.get("lang") or "").lower(),
                                testi=[s.get("release_name") or "", s.get("comment") or "", s.get("author") or "",
                                       s.get("name") or ""])
                     for s in dati.get("subtitles", []) if s.get("url")]
        esito.fonte("SUBDL", "interrogato", len(candidati))
        return candidati
    except ErroreFonte as e:
        esito.fonte("SUBDL", f"saltato: {e}")
        return []


def scarica_subdl(candidato: dict) -> bytes:
    codice, _, file = _chiedi("SUBDL", "GET", SUBDL_DOWNLOAD + candidato["url"], {"Accept": "*/*"})
    if codice != 200:
        raise ErroreFonte(f"download non riuscito ({codice})")
    if not file.startswith(b"PK"):
        return file
    with zipfile.ZipFile(io.BytesIO(file)) as z:
        srt = sorted((i for i in z.infolist() if i.filename.lower().endswith(".srt")), key=lambda i: -i.file_size)
        if not srt:
            raise ErroreFonte("l'archivio non contiene un file .srt")
        return z.read(srt[0])


# --------------------------------------------------------------------------
# Scarto, ordine e controllo del contenuto
# --------------------------------------------------------------------------

def motivo_prima_del_download(c: dict):
    if c.get("lingua") and c["lingua"] not in ("it", "italian", "ita"):
        return f"lingua {c['lingua']}, non italiano"
    if c.get("ia"):
        return "la fonte lo segnala come tradotto con l'IA"
    if c.get("automatica"):
        return "la fonte lo segnala come tradotto automaticamente"
    for testo in c["testi"]:
        indizio = AUTOMATICA.search(testo or "") or SIGLA_IA.search(testo or "")
        if indizio:
            return f"indizio di traduzione automatica: «{indizio.group(0)}»"
        if A_PAGAMENTO.search(testo or ""):
            return "a pagamento o da sbloccare"
    return None


def ordina(candidati: list, fps_video) -> list:
    def chiave(c):
        scarto_fps = abs(float(c["fps"]) - fps_video) if c.get("fps") and fps_video else 99
        return (not c["hash"], -(c["download"] or 0), -(c["voto"] or 0), scarto_fps)
    return sorted(candidati, key=chiave)


def _normale(testo: str) -> str:
    return re.sub(r"\W+", " ", st.senza_tag(testo)).strip().lower()


def motivo_dopo_il_download(testo: str, partenza: tuple, durata_video, s: dict):
    """Il motivo per scartare il file scaricato, o None se va bene. partenza: (testi, voci con i
    tempi) dei sottotitoli di partenza, o None se non ci sono. I tempi li controlla cerca."""
    testi, _, _ = st.parse("scaricati.srt", testo)
    fine = _ultima_fine(testo)
    if not testi:
        return "il file non contiene battute leggibili"
    vuote = sum(not t.strip() for t in testi) / len(testi)
    if vuote > s["righe_vuote_max"]:
        return f"troppe righe vuote ({vuote:.0%})"
    rotte = sum(bool(CODIFICA_ROTTA.search(t)) for t in testi) / len(testi)
    if rotte > s["codifica_max"]:
        return f"caratteri di codifica sbagliati in {rotte:.0%} delle battute"
    righe = [r for t in testi for r in st.senza_tag(t).split("\n") if r.strip()]
    media = sum(map(len, righe)) / max(1, len(righe))
    if not s["lunghezza_media_min"] <= media <= s["lunghezza_media_max"]:
        return f"lunghezza media delle righe anomala ({media:.0f} caratteri)"
    inglesi = sum(1 for t in testi if _quota_inglese(t) > 0.3) / len(testi)
    if partenza:
        testi_p = partenza[0]
        # I tempi li controlla il confronto con la partenza (vedi cerca); qui solo un numero di battute
        # così diverso che non può essere lo stesso film, nemmeno con tagli o battute divise diversamente.
        rapporto = len(testi) / max(1, len(testi_p))
        if not s["battute_min"] <= rapporto <= s["battute_max"]:
            return f"numero di battute troppo diverso ({len(testi)} contro {len(testi_p)})"
        uguali = {_normale(t) for t in testi_p if len(_normale(t)) > 3}
        identiche = sum(1 for t in testi if _normale(t) in uguali) / len(testi)
        inglesi = max(inglesi, identiche)
    elif durata_video and fine and abs(fine - durata_video) / durata_video > 0.25:
        return f"tempi troppo diversi dal video (finisce a {fine / 60:.0f} min su {durata_video / 60:.0f})"
    if inglesi > s["non_tradotte_max"]:
        return f"{inglesi:.0%} delle battute non è tradotto"
    return None


def _quota_inglese(testo: str) -> float:
    parole = re.findall(r"[a-z']+", st.senza_tag(testo).lower())
    return sum(p in PAROLE_INGLESI for p in parole) / len(parole) if parole else 0.0


def _ultima_fine(testo: str):
    tempi = re.findall(r"-->\s*(\d+:\d+:\d+[,.]\d+)", testo)
    return st._secondi(tempi[-1]) if tempi else None


def durata(video: Path):
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                           capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def nome_libero(cartella: Path, base: str) -> Path:
    """«<base> - Sottotitoli IT (scaricati).srt», o «… (2).srt» se esiste già: mai sostituire un file."""
    destinazione = cartella / f"{base} - Sottotitoli IT (scaricati).srt"
    n = 2
    while destinazione.exists():
        destinazione = cartella / f"{base} - Sottotitoli IT (scaricati) ({n}).srt"
        n += 1
    return destinazione


# --------------------------------------------------------------------------
# La ricerca
# --------------------------------------------------------------------------

def _salva(esito: Esito, c: dict, testo: str, percorso: Path, video, tempi: str) -> None:
    """Salva il candidato scelto in UTF-8 accanto al file, senza sostituire nulla, e lo segna come trovato."""
    base = video.stem if video else base_del_nome(percorso)
    destinazione = nome_libero(percorso.parent, base)
    destinazione.write_text(testo, encoding="utf-8")
    perche = ["corrisponde all'hash del video" if c["hash"] else "corrisponde a titolo e anno"]
    if c["download"]:
        perche.append(f"{c['download']} download")
    if c["voto"]:
        perche.append(f"voto {c['voto']:g}")
    if tempi:
        perche.append(tempi)
    esito.trovato = {"fonte": c["fonte"], "nome": c["nome"], "file": str(destinazione), "motivo": ", ".join(perche)}
    print(f"[ricerca] salvato: {destinazione}", flush=True)


def cerca(percorso: Path, video: Path = None, partenza: Path = None) -> Esito:
    """Cerca sottotitoli italiani per il file di partenza (sottotitoli o video). video: il video, se
    noto; altrimenti si cerca accanto. partenza: i sottotitoli da confrontare (di solito il file stesso)."""
    esito = Esito()
    s = soglie()
    video = video if video and video.exists() else trova_video(percorso)
    if partenza is None and percorso.suffix.lower() in st.ESTENSIONI:
        partenza = percorso
    dati_partenza = None
    if partenza and partenza.exists():
        try:
            testo_p = st.decodifica(partenza.read_bytes())
            dati_partenza = (st.parse(partenza.name, testo_p)[0], allineamento.voci(testo_p))
        except (OSError, ValueError):
            dati_partenza = None
    titolo, anno = titolo_e_anno(video or percorso)
    hash_video = hash_opensubtitles(video) if video else None
    fps_video = frame_rate(video) if video else None
    durata_video = durata(video) if video else None
    print(f"[ricerca] cerco: titolo «{titolo}»" + (f", anno {anno}" if anno else "")
          + (", con l'hash del video" if hash_video else ", senza video (solo titolo e anno)"), flush=True)

    candidati, sessione_os = cerca_opensubtitles(esito, hash_video, titolo, anno)
    candidati += cerca_subdl(esito, titolo, anno)
    esito.fonte("Podnapisi", "saltato: non ha un'API ufficiale documentata")

    # La ricerca per titolo restituisce anche altri film dello stesso anno: si tengono solo quelli dello
    # stesso film (o con l'hash del video). Se nessuno risulta dello stesso film (nome del file diverso
    # dal titolo), li si tiene tutti: decide il confronto con i sottotitoli di partenza.
    if any(c["hash"] or stesso_film(c, titolo, anno) for c in candidati if c.get("film")):
        for c in [c for c in candidati if c.get("film") and not c["hash"] and not stesso_film(c, titolo, anno)]:
            candidati.remove(c)
            esito.scarta(c, f"altro film: {c['film']['titoli'][0]} ({c['film'].get('anno') or '?'})")

    validi = []
    for c in candidati:
        motivo = motivo_prima_del_download(c)
        if motivo:
            esito.scarta(c, motivo)
        else:
            validi.append(c)

    tentativi, esaurite, riserve = 0, set(), []
    for c in ordina(validi, fps_video):
        if tentativi >= s["scaricamenti_max"]:
            break
        if c["fonte"] in esaurite:
            esito.scarta(c, f"non scaricato: quota di {c['fonte']} esaurita")
            continue
        tentativi += 1
        try:
            grezzo = scarica_opensubtitles(sessione_os, c) if c["fonte"] == "OpenSubtitles" else scarica_subdl(c)
        except ErroreFonte as e:
            esito.scarta(c, str(e))
            if "quota" in str(e):
                esaurite.add(c["fonte"])
            continue
        testo = st.decodifica(grezzo).replace("\r\n", "\n").replace("\r", "\n")
        motivo = motivo_dopo_il_download(testo, dati_partenza, durata_video, s)
        if motivo:
            esito.scarta(c, motivo)
            continue
        if not dati_partenza or not dati_partenza[1]:
            _salva(esito, c, testo, percorso, video, "")
            break
        # Tempi: prima così come sono, poi riallineati.
        partenza_voci, voci = dati_partenza[1], allineamento.voci(testo)
        quota, scoperte = allineamento.copertura(partenza_voci, voci)
        if not scoperte and quota >= allineamento.SOGLIE["copertura_min"]:
            _salva(esito, c, testo, percorso, video, f"tempi giusti, copre il {quota:.0%} dei dialoghi")
            break
        riallineate, descrizione = allineamento.allinea(partenza_voci, voci)
        if riallineate is None:
            esito.scarta(c, descrizione)
            continue
        quota, _ = allineamento.copertura(partenza_voci, riallineate)
        print(f"[ricerca] {c['fonte']} «{c['nome']}»: {descrizione}; lo tengo di riserva", flush=True)
        riserve.append((quota, c, allineamento.scrivi_srt(riallineate), descrizione))
    if not esito.trovato and riserve:
        quota, c, testo, descrizione = max(riserve, key=lambda r: r[0])
        _salva(esito, c, testo, percorso, video, descrizione)
    return esito
