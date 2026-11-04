"""Un modello che gira su questo Mac (Ollama) come motore dell'adattamento: vedi adattamento.py.

Gratuito e privato: niente esce dal computer. Il modello è Gemma 4 26B (Google): nel
confronto del 2 novembre 2026 su un film e su un campione giapponese è stato il più
veloce (circa 125 token al secondo, il doppio di Qwen3-VL 30B) e quello con l'italiano
più naturale, a parità di errori, davanti a Qwen 3.5 35B, Qwen 3.8 27B e Gemma 4 31B.
Ollama lo fa girare con il suo motore MLX. Se Ollama è spento lo avvia; se il modello
manca lo scarica.

Convivenza con Vertov e Murnau, che usano Ollama con un altro modello: se lavorassero
insieme a Ozu, Ollama passerebbe da un modello all'altro e andrebbero tutti piano. Così
Ozu cede il passo: prima di ogni richiesta chiede alle altre app se stanno lavorando, e
aspetta. Alla fine toglie il suo modello dalla memoria, se nessun altro lavora.
"""

import copy
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

import adattamento
import progresso

OLLAMA = "http://localhost:11434"
MODELLO = "gemma4:26b"
NOME = "Gemma 4 26B"
# Le altre app della famiglia e la porta della loro interfaccia (vedi i loro app.py).
ALTRE_APP = {"Vertov": 8765, "Murnau": 8766}
# I loro programmi che usano il modello (vedi i loro app.py: li lanciano in un processo a parte).
PROGRAMMI_DI_ANALISI = {"Vertov": "vertov/descrivi.py", "Murnau": "murnau/murnau.py"}
TENUTA = "5m"  # quanto Ollama tiene il modello in memoria dopo l'ultima richiesta di Ozu
# Lo spazio di lavoro (contesto) si decide a inizio lavoro, a multipli di 8.192 token: Ollama
# ricarica il modello quando cambia, e ogni token in più occupa memoria (circa 100 KB).
PASSO_CONTESTO = 8192
CONTESTO_MINIMO = 16384
CONTESTO_MASSIMO = 131072
MARGINE_RISPOSTA = 12000  # token lasciati liberi per la risposta
CARATTERI_PER_TOKEN = 3


def _chiama(percorso: str, corpo: dict = None, timeout: float = 3):
    """Una richiesta a Ollama: GET senza corpo, POST con il corpo in JSON. Restituisce la risposta aperta."""
    dati = json.dumps(corpo).encode() if corpo is not None else None
    return urllib.request.urlopen(urllib.request.Request(f"{OLLAMA}{percorso}", data=dati,
                                                         headers={"Content-Type": "application/json"}),
                                  timeout=timeout)


def _modelli() -> list:
    with _chiama("/api/tags") as r:
        return [m["name"] for m in json.load(r).get("models", [])]


def installato() -> bool:
    return os.path.exists("/Applications/Ollama.app") or bool(shutil.which("ollama")) \
        or os.path.exists("/opt/homebrew/bin/ollama")


def configurato() -> bool:
    """Basta che Ollama sia installato: se è spento lo si avvia, se il modello manca lo si scarica."""
    return installato()


def pronto(modello: str = MODELLO) -> bool:
    """Ollama acceso e modello scaricato."""
    try:
        return any(m == modello or m.startswith(modello + ":") for m in _modelli())
    except Exception:
        return False


def avvia_ollama() -> None:
    try:
        _modelli()
        return
    except Exception:
        pass
    if not installato():
        raise RuntimeError("Per il modello locale serve Ollama (brew install ollama, oppure l'app da ollama.com).")
    progresso.fase("Avvio di Ollama")
    if os.path.exists("/Applications/Ollama.app"):
        subprocess.run(["open", "-g", "-a", "Ollama"])
    else:
        subprocess.Popen([shutil.which("ollama") or "/opt/homebrew/bin/ollama", "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(60):
        time.sleep(1)
        try:
            _modelli()
            return
        except Exception:
            pass
    raise RuntimeError("Non riesco ad avviare Ollama: aprilo a mano (app Ollama) e riprova.")


def scarica_modello(modello: str) -> None:
    if pronto(modello):
        return
    progresso.fase("Scarico il modello locale (una volta sola, circa 20 GB)")
    with _chiama("/api/pull", {"model": modello, "stream": True}, timeout=None) as r:
        for riga in r:
            stato = json.loads(riga or b"{}")
            if stato.get("error"):
                raise RuntimeError(f"Download del modello non riuscito: {stato['error']}")
            if stato.get("total") and stato.get("completed") is not None:
                progresso.avanzamento("modello", stato["completed"] // 2**20, stato["total"] // 2**20)


# I modelli locali, a differenza di Claude, a volte si «incantano» e allungano le liste senza fine:
# lo schema della risposta mette un tetto a liste e testi, e il modello è obbligato a rispettarlo.
MASSIMI = {"personaggi": 20, "rapporti_e_registro": 20, "glossario": 30, "errori_sorgente": 15}
LUNGHEZZA_NOTE = 240


def _con_limiti(schema: dict) -> dict:
    schema = copy.deepcopy(schema)
    for nome, campo in schema.get("properties", {}).items():
        if campo.get("type") == "array" and nome in MASSIMI:
            campo["maxItems"] = MASSIMI[nome]
            for interno in campo["items"]["properties"].values():
                interno["maxLength"] = LUNGHEZZA_NOTE
        elif campo.get("type") == "string" and nome in ("genere_e_tono", "note_adattamento"):
            campo["maxLength"] = LUNGHEZZA_NOTE * 3
    return schema


def _rispetta_limiti(dati, schema: dict):
    """Senza scrittura vincolata il modello potrebbe allungare liste e note: si ritagliano ai limiti
    (gli stessi di _con_limiti) e si controlla che la risposta abbia la forma attesa."""
    if not isinstance(dati, dict):
        raise ValueError("la risposta non è un oggetto JSON")
    for nome, campo in schema["properties"].items():
        if nome not in dati:
            raise KeyError(nome)
        if campo.get("type") == "array":
            if not isinstance(dati[nome], list):
                raise ValueError(f"{nome} non è una lista")
            interni = campo["items"]["properties"]
            dati[nome] = [{k: (v[k] if interni[k]["type"] != "string" else str(v[k])[:LUNGHEZZA_NOTE * 4])
                           for k in interni} for v in dati[nome][:MASSIMI.get(nome, None)]
                          if isinstance(v, dict) and all(k in v for k in interni)]
        elif campo.get("type") == "string":
            dati[nome] = str(dati[nome])[:LUNGHEZZA_NOTE * 3]
    return dati


BARRE_NON_VALIDE = re.compile(r'\\(?=[^"\\/bfnrtu])')


def _json_nel_testo(testo: str):
    """Il primo oggetto JSON della risposta, anche se il modello l'ha messo tra ```json e ```. Senza
    scrittura vincolata i modelli a volte scrivono \\' (come in Python) o un'altra barra rovesciata non
    valida in JSON: se la lettura fallisce, quelle barre si tolgono e si riprova."""
    inizio, fine = testo.find("{"), testo.rfind("}")
    if inizio < 0 or fine < inizio:
        raise ValueError("nessun JSON nella risposta")
    try:
        return json.loads(testo[inizio:fine + 1])
    except ValueError:
        return json.loads(BARRE_NON_VALIDE.sub("", testo[inizio:fine + 1]))


def altre_app_al_lavoro() -> list:
    """Le app della famiglia che in questo momento hanno un'elaborazione in corso: lo dice la loro
    finestra, oppure il loro programma di analisi in esecuzione (anche se lanciato da Terminale)."""
    occupate = []
    for nome, porta in ALTRE_APP.items():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{porta}/api/stato", timeout=5) as r:
                if json.load(r).get("lavoro", {}).get("attivo"):
                    occupate.append(nome)
        except Exception:
            pass  # app chiusa, o un altro programma su quella porta
    try:
        processi = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        processi = ""
    for nome, programma in PROGRAMMI_DI_ANALISI.items():
        # Solo Python che esegue proprio quel programma, non un comando che ne nomina il percorso. Il programma
        # può essere lanciato con il percorso («vertov/descrivi.py») o dalla sua cartella («descrivi.py»).
        nome_file = re.escape(programma.rsplit("/", 1)[-1])
        in_esecuzione = any("python" in riga.lower() and re.search(r"(^|[\s/])" + nome_file + r"(\s|$)", riga)
                            for riga in processi.splitlines())
        if nome not in occupate and in_esecuzione:
            occupate.append(nome)
    return occupate


def aspetta_il_turno() -> None:
    """Se Vertov o Murnau stanno usando il modello, aspetta che finiscano."""
    occupate = altre_app_al_lavoro()
    if not occupate:
        return
    ripresa = progresso.ULTIMA["fase"]
    while occupate:
        progresso.fase(f"In attesa: {' e '.join(occupate)} sta usando il modello", ricorda=False)
        time.sleep(15)
        occupate = altre_app_al_lavoro()
    progresso.fase(ripresa)


def libera(contesto: int, modello: str = MODELLO) -> None:
    """Toglie il modello dalla memoria se è la copia caricata da Ozu e nessun'altra app lavora."""
    try:
        with _chiama("/api/ps") as r:
            caricati = json.load(r).get("models", [])
        nostro = any(m.get("name") == modello and m.get("context_length") == contesto for m in caricati)
        if not nostro or altre_app_al_lavoro():
            return
        _chiama("/api/generate", {"model": modello, "keep_alive": 0}, timeout=30).close()
    except Exception:
        pass  # Ollama spento o già scarico: niente da liberare


class Sessione:
    """Stessa interfaccia di motore_claude.Sessione; il costo resta zero."""

    def __init__(self, modello: str = None):
        # Il modello di Claude scelto in un'altra sessione non vale qui.
        self.modello = modello if modello and not modello.startswith("claude") else MODELLO
        self.nome = NOME if self.modello == MODELLO else self.modello
        self.costo = 0.0
        self.contesto = CONTESTO_MINIMO
        # Con il motore MLX di Ollama la scrittura vincolata (xgrammar) può mancare: allora lo schema
        # va nelle istruzioni e la risposta si controlla qui (vedi chiedi).
        self.vincolato = True
        avvia_ollama()
        scarica_modello(self.modello)

    def libera(self) -> None:
        """A fine lavoro: toglie dalla memoria la copia del modello caricata da Ozu (vedi libera)."""
        libera(self.contesto, self.modello)

    def prepara(self, caratteri: int) -> None:
        """Fissa lo spazio di lavoro per la richiesta più lunga del lavoro, così il modello si carica una
        volta sola e occupa solo la memoria che serve."""
        self.contesto = self._contesto_per(caratteri)

    def _contesto_per(self, caratteri: int) -> int:
        servono = (len(adattamento.SYSTEM) + caratteri) // CARATTERI_PER_TOKEN + MARGINE_RISPOSTA
        arrotondato = -(-servono // PASSO_CONTESTO) * PASSO_CONTESTO
        return max(CONTESTO_MINIMO, min(arrotondato, CONTESTO_MASSIMO))

    def chiedi(self, blocchi: list, schema: dict, attesi: int = 0, avanza=None) -> dict:
        """attesi: quanti caratteri ci si aspetta nella risposta; avanza(frazione) segue la scrittura."""
        testo = "\n\n".join(b["text"] for b in blocchi)
        # Una richiesta più lunga del previsto allarga lo spazio (e il modello si ricarica): solo per sicurezza.
        self.contesto = max(self.contesto, self._contesto_per(len(testo)))
        # Tetto alla risposta: il doppio di quella attesa, mai oltre un terzo del contesto.
        tetto = min(self.contesto // 3, max(2048, attesi * 2 // CARATTERI_PER_TOKEN)) if attesi else -1
        corpo = self._corpo(testo, schema, tetto)
        for tentativo in range(3):
            aspetta_il_turno()
            try:
                risposta = self._scrivi(corpo, attesi, avanza)
                if self.vincolato:
                    return json.loads(risposta)
                return _rispetta_limiti(_json_nel_testo(risposta), schema)
            except urllib.error.HTTPError as e:
                dettaglio = e.read().decode(errors="replace")[:200]
                if e.code == 501 and "structured output" in dettaglio and self.vincolato:
                    self.vincolato = False  # vale per tutto il lavoro
                    corpo = self._corpo(testo, schema, tetto)
                    continue
                # Una richiesta sbagliata (4xx) resterebbe sbagliata: inutile riprovare.
                if tentativo == 2 or e.code < 500:
                    raise RuntimeError(f"Ollama ha risposto con un errore ({e.code}): {dettaglio}")
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if tentativo == 2:
                    raise RuntimeError("Ollama non risponde: controlla che sia acceso.")
                avvia_ollama()
            except (ValueError, KeyError):
                # Di solito una risposta interrotta dal tetto: si riprova con meno fantasia.
                if tentativo == 2:
                    raise adattamento.RispostaNonValida("Il modello locale ha dato una risposta incompleta o "
                                                        "non valida per tre volte di seguito.")
                corpo["options"]["temperature"] = 0.1
            time.sleep(3)

    def _corpo(self, testo: str, schema: dict, tetto: int) -> dict:
        corpo = {"model": self.modello, "stream": True, "think": False, "keep_alive": TENUTA,
                 "options": {"num_ctx": self.contesto, "temperature": 0.3, "num_predict": tetto},
                 "messages": [{"role": "system", "content": adattamento.SYSTEM},
                              {"role": "user", "content": testo}]}
        if self.vincolato:
            corpo["format"] = _con_limiti(schema)
        else:
            corpo["messages"][1]["content"] += (
                "\n\nRispondi solo con un oggetto JSON valido, senza altro testo, che segua questo schema "
                "(rispetta i limiti di lunghezza delle liste e dei testi):\n"
                + json.dumps(_con_limiti(schema), ensure_ascii=False))
        return corpo

    def _scrivi(self, corpo: dict, attesi: int, avanza) -> str:
        """Riceve la risposta a pezzi mentre il modello la scrive: così si vede a che punto è."""
        pezzi, scritti, ultimo = [], 0, 0.0
        with _chiama("/api/chat", corpo, timeout=1800) as r:
            for riga in r:
                pezzo = json.loads(riga or b"{}")
                if pezzo.get("error"):
                    raise RuntimeError(f"Ollama: {pezzo['error']}")
                testo = pezzo.get("message", {}).get("content", "")
                pezzi.append(testo)
                scritti += len(testo)
                if avanza and attesi and time.time() - ultimo > 1:
                    avanza(min(.95, scritti / attesi))
                    ultimo = time.time()
        return "".join(pezzi)
