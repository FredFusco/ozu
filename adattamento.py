"""Il procedimento dell'adattatore, comune a Claude e al modello locale.

1. Analisi: il modello legge tutto il film (o tutti gli episodi di una serie) e compila la
   scheda: personaggi, rapporti e registro (tu/lei/voi), glossario, errori OCR del
   file sorgente. La scheda si può correggere prima di tradurre.
2. Traduzione: il film viene tradotto a blocchi, ma ogni richiesta contiene l'intero
   copione originale e la scheda (con Claude in cache, quindi letti a basso costo), più le ultime
   battute già tradotte: nomi, registri ed espressioni restano coerenti dall'inizio
   alla fine.
3. Revisione: le battute che violano i vincoli (righe troppo lunghe, più di due righe,
   tag persi) tornano al modello per essere sistemate.

Il motore è un oggetto con il metodo chiedi(blocchi, schema) -> dict e l'attributo costo:
vedi motore_claude.Sessione e motore_locale.Sessione.
"""

import json

import sottotitoli as st

BATTUTE_PER_BLOCCO = 120
CONTESTO_PRECEDENTE = 40
# Stime per l'avanzamento e per lo spazio di lavoro: nella risposta ogni battuta occupa più o meno
# quanto l'originale, più la cornice JSON; una battuta tipica è lunga circa 50 caratteri.
RISPOSTA_PER_CARATTERE = 1.1
CORNICE_JSON = 25
CARATTERI_PER_BATTUTA = 50

LINGUE = {
    "auto": "non specificata (deducila dal contenuto)",
    "en": "inglese", "fr": "francese", "es": "spagnolo", "de": "tedesco", "pt": "portoghese",
    "pt-BR": "portoghese brasiliano", "ru": "russo", "ja": "giapponese", "ko": "coreano",
    "zh": "cinese mandarino", "yue": "cantonese", "ar": "arabo", "tr": "turco", "pl": "polacco",
    "nl": "olandese", "sv": "svedese", "da": "danese", "no": "norvegese", "fi": "finlandese",
    "hi": "hindi", "th": "thailandese", "vi": "vietnamita", "id": "indonesiano", "fa": "persiano",
    "he": "ebraico", "el": "greco", "hu": "ungherese", "cs": "ceco", "ro": "rumeno", "uk": "ucraino",
}


# --------------------------------------------------------------------------
# Istruzioni
# --------------------------------------------------------------------------

SYSTEM = f"""Sei un adattatore professionista di sottotitoli per il cinema e la TV italiani. \
Il tuo lavoro non è tradurre riga per riga: hai letto tutto il film e scrivi sottotitoli italiani \
che uno spettatore legga con naturalezza, come farebbe un buon adattatore umano.

Principi del lavoro:

- Coerenza su tutto il film. Nomi, soprannomi, titoli, termini ricorrenti e modi di dire si rendono \
sempre allo stesso modo. Il registro tra ogni coppia di personaggi (tu, lei, voi) resta quello stabilito \
nella scheda del film, salvo che la storia lo cambi davvero (per esempio due personaggi che passano dal lei al tu).

- Lingua parlata originale. Spesso il file sorgente è a sua volta una traduzione (di solito inglese) \
di un film parlato in un'altra lingua. Quando la lingua dell'audio è indicata, traduci pensando a ciò \
che i personaggi dicono davvero in quella lingua: l'inglese appiattisce onorifici, forme di cortesia, \
gerarchie, toni affettuosi o bruschi. Recuperali in italiano con gli strumenti dell'italiano \
(lei/tu/voi, appellativi come "signore", "maestro", "fratello maggiore" quando suonano naturali, scelte \
lessicali più formali o più colloquiali). Non lasciare onorifici stranieri come "-san" o "-sama", a meno \
che la scheda non dica di mantenerli.

- Vincoli dei sottotitoli. Ogni battuta resta una battuta: non unire, non dividere, non spostare testo \
tra battute. Al massimo {st.MAX_RIGHE} righe per battuta e al massimo {st.MAX_CARATTERI_RIGA} caratteri \
per riga (spazi inclusi, tag esclusi). Accanto a ogni battuta trovi la durata: punta a una velocità di \
lettura di circa 15-17 caratteri al secondo, condensando quando serve come fa un adattatore (togli \
ridondanze, non il senso). Se una battuta è un dialogo con trattini a inizio riga, mantieni quella \
struttura. Lascia intatti e al loro posto i tag di formattazione come <i>, </i>, <b>, <font ...>: \
se una frase è in corsivo, la traduzione resta in corsivo.

- Errori del file sorgente. Il file può venire da OCR o da trascrizioni imprecise: correggi in silenzio \
gli errori evidenti ("ifs" per "it's", "ls" per "Is", "l" per "I", "rn" per "m", parole spezzate, \
punteggiatura saltata) e traduci quello che il testo voleva dire, non l'errore.

- Italiano naturale. Punteggiatura e convenzioni italiane (puntini di sospensione "...", virgolette \
quando servono, niente maiuscole all'inglese). Rendi i giochi di parole e le espressioni idiomatiche con \
equivalenti italiani, non alla lettera. Parolacce e insulti con la stessa forza dell'originale.
"""

ISTRUZIONI_ANALISI = """Leggi tutto il copione qui sopra e prepara la scheda per l'adattamento in \
italiano. Ti serve per mantenere le scelte coerenti in tutto il film (o in tutti gli episodi):

- personaggi: chi sono, genere, ruolo, come si rende il nome in italiano (di solito invariato);
- rapporti e registro: per ogni coppia di personaggi importante, se si danno del tu, del lei o del voi \
in italiano e perché (età, gerarchia, intimità, forme di cortesia della lingua originale); \
segnala se il registro cambia durante il film;
- glossario: termini, luoghi, istituzioni, gradi, cibi, espressioni e battute ricorrenti, e come \
renderli sempre allo stesso modo;
- errori OCR o di trascrizione ricorrenti che hai notato nel file sorgente, con la forma corretta;
- note generali: genere, epoca, tono, modo di parlare dei personaggi principali.

Sii essenziale: è un promemoria per chi traduce, non un saggio. Al massimo 20 personaggi (i principali),
20 rapporti, 30 voci di glossario e 15 errori; ogni descrizione o nota in una frase breve."""


def _oggetti(campi: dict) -> dict:
    return {"type": "array", "items": {"type": "object", "properties": {c: {"type": "string"} for c in campi},
                                       "required": list(campi), "additionalProperties": False}}


SCHEMA_SCHEDA = {
    "type": "object",
    "properties": {
        "lingua_sottotitoli": {"type": "string"},
        "lingua_audio": {"type": "string"},
        "genere_e_tono": {"type": "string"},
        "personaggi": _oggetti(["nome", "resa_italiana", "descrizione"]),
        "rapporti_e_registro": _oggetti(["tra", "registro", "note"]),
        "glossario": _oggetti(["originale", "italiano", "note"]),
        "errori_sorgente": _oggetti(["errato", "corretto"]),
        "note_adattamento": {"type": "string"},
    },
    "required": ["lingua_sottotitoli", "lingua_audio", "genere_e_tono", "personaggi", "rapporti_e_registro",
                 "glossario", "errori_sorgente", "note_adattamento"],
    "additionalProperties": False,
}

# Ogni battuta della risposta è {"n": numero, "t": testo}: nomi corti perché il modello li riscrive
# per ogni battuta (misurato su un film di prova: circa il 6% di tempo in meno rispetto a "id" e "testo").
SCHEMA_BATTUTE = {
    "type": "object",
    "properties": {"battute": {"type": "array", "items": {
        "type": "object", "properties": {"n": {"type": "integer"}, "t": {"type": "string"}},
        "required": ["n", "t"], "additionalProperties": False}}},
    "required": ["battute"],
    "additionalProperties": False,
}


def _battuta(i: int, testo: str, durata) -> str:
    return f"#{i + 1}" + (f" [{durata:.1f}s]" if durata else "") + f"\n{testo}"


def _copione(testi: list, durate: list) -> str:
    return "\n\n".join(_battuta(i, t, d) for i, (t, d) in enumerate(zip(testi, durate)))


def _intestazione(opzioni: dict) -> str:
    righe = [f"Lingua dei sottotitoli sorgente: {LINGUE.get(opzioni.get('lingua_sub', 'auto'))}.",
             f"Lingua parlata nel film (audio originale): {LINGUE.get(opzioni.get('lingua_audio', 'auto'))}."]
    if opzioni.get("note", "").strip():
        righe.append("Note dell'utente per l'adattamento (hanno la precedenza):\n" + opzioni["note"].strip())
    return "\n".join(righe)


# --------------------------------------------------------------------------
# 1. Scheda del film
# --------------------------------------------------------------------------

def analizza(sessione, film: list, opzioni: dict, avanza=None) -> dict:
    """film: [(nome, testi, durate), …]; più file insieme sono episodi della stessa serie.
    avanza(frazione) segue la scrittura della scheda."""
    if len(film) == 1:
        copioni = "<copione_originale>\n" + _copione(film[0][1], film[0][2]) + "\n</copione_originale>"
    else:
        copioni = "\n\n".join(f'<copione_originale file="{nome}">\n{_copione(t, d)}\n</copione_originale>'
                              for nome, t, d in film)
    battute = sum(len(t) for _, t, _ in film)
    return sessione.chiedi([{"type": "text", "text": _intestazione(opzioni) + "\n\n" + copioni},
                            {"type": "text", "text": ISTRUZIONI_ANALISI}], SCHEMA_SCHEDA,
                           attesi=attesi_scheda(battute), avanza=avanza)


def attesi_scheda(battute: int) -> int:
    """Lunghezza tipica di una scheda, in caratteri: cresce con il film, fino a un tetto."""
    return min(16000, 4000 + 6 * battute)


def peso_analisi(battute: int) -> int:
    """Quanto vale l'analisi nell'avanzamento generale, in battute: quante ne servirebbero per
    scrivere un testo lungo come la scheda."""
    return attesi_scheda(battute) // CARATTERI_PER_BATTUTA


def caratteri_massimi(film: list, opzioni: dict, scheda: dict = None) -> int:
    """La richiesta più lunga di un lavoro, in caratteri: l'analisi di tutti i file insieme, oppure
    un blocco di traduzione (copione del file, scheda, battute precedenti e battute da tradurre).
    film: [(nome, testi, durate), …]."""
    intestazione = len(_intestazione(opzioni)) + 200
    copioni = [len(_copione(t, d or [None] * len(t))) for _, t, d in film]
    analisi = intestazione + sum(copioni) + len(ISTRUZIONI_ANALISI) + 60 * len(film)
    if scheda:
        lunga_scheda = len(json.dumps(scheda, ensure_ascii=False, indent=1))
    else:
        lunga_scheda = attesi_scheda(sum(len(t) for _, t, _ in film))
    blocco = 0
    for (_, testi, _), copione in zip(film, copioni):
        per_battuta = copione / max(1, len(testi))
        da_tradurre = per_battuta * min(len(testi), BATTUTE_PER_BLOCCO)
        precedenti = per_battuta * min(len(testi), CONTESTO_PRECEDENTE)
        blocco = max(blocco, intestazione + copione + lunga_scheda + precedenti + da_tradurre + 600)
    return int(max(analisi, blocco))


# --------------------------------------------------------------------------
# 2-3. Traduzione e revisione
# --------------------------------------------------------------------------

def traduci(sessione, testi: list, durate: list, opzioni: dict, scheda: dict, progresso,
            fatte: dict = None, salva_parziale=None) -> tuple:
    """(traduzioni, avvisi). fatte: {indice: testo} già tradotte da un tentativo interrotto;
    salva_parziale(fatte) viene chiamata dopo ogni blocco, per poter riprendere."""
    n = len(testi)
    durate = durate or [None] * n
    prefisso = [
        {"type": "text", "text": _intestazione(opzioni) + "\n\n<copione_originale>\n"
                                 + _copione(testi, durate) + "\n</copione_originale>"},
        {"type": "text", "text": "<scheda_film>\n" + json.dumps(scheda, ensure_ascii=False, indent=1)
                                 + "\n</scheda_film>",
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
    ]
    tradotti = [None] * n
    for i, t in (fatte or {}).items():
        if 0 <= int(i) < n:
            tradotti[int(i)] = t

    for inizio in range(0, n, BATTUTE_PER_BLOCCO):
        ids = [i for i in range(inizio, min(inizio + BATTUTE_PER_BLOCCO, n)) if tradotti[i] is None]
        for _ in range(3):
            if not ids:
                break
            gia = sum(t is not None for t in tradotti)
            dentro = lambda f, gia=gia, quante=len(ids): progresso(gia + f * quante, n)
            for i, t in _blocco(sessione, prefisso, testi, durate, tradotti, ids, dentro).items():
                tradotti[i] = st.a_capo_automatico(t)
            ids = [i for i in ids if tradotti[i] is None]
        if ids:
            raise RuntimeError(f"Il modello non ha restituito le battute {[i + 1 for i in ids][:10]}.")
        if salva_parziale:
            salva_parziale({i: t for i, t in enumerate(tradotti) if t is not None})
        progresso(sum(t is not None for t in tradotti), n)

    da_rivedere = {i: p for i in range(n) if (p := st.problemi(testi[i], tradotti[i]))}
    if da_rivedere:
        progresso(n, n, f"Revisione di {len(da_rivedere)} battute")
        ids = sorted(da_rivedere)
        for k in range(0, len(ids), BATTUTE_PER_BLOCCO):
            elenco = "\n\n".join(
                _battuta(i, f"Originale:\n{testi[i]}\nTraduzione attuale:\n{tradotti[i]}\n"
                            f"Problemi: {'; '.join(da_rivedere[i])}", durate[i])
                for i in ids[k:k + BATTUTE_PER_BLOCCO])
            richiesta = ("Queste battute tradotte non rispettano i vincoli dei sottotitoli. Riscrivile in modo "
                         "che li rispettino, mantenendo senso, tono e coerenza con la scheda. Restituisci tutte e "
                         "solo queste battute, con il loro numero.\n\n" + elenco)
            for b in sessione.chiedi(prefisso + [{"type": "text", "text": richiesta}], SCHEMA_BATTUTE)["battute"]:
                if b["n"] - 1 in da_rivedere and b["t"].strip():
                    tradotti[b["n"] - 1] = st.a_capo_automatico(b["t"])

    avvisi = [f"#{i + 1}: {'; '.join(p)}" for i in range(n) if (p := st.problemi(testi[i], tradotti[i]))]
    return tradotti, avvisi


def _blocco(sessione, prefisso, testi, durate, tradotti, ids, avanza=None) -> dict:
    precedenti = [i for i in range(ids[0]) if tradotti[i] is not None][-CONTESTO_PRECEDENTE:]
    parti = []
    if precedenti:
        parti.append("<traduzione_precedente>\n" + "\n\n".join(f"#{i + 1}\n{tradotti[i]}" for i in precedenti)
                     + "\n</traduzione_precedente>")
    parti.append(f"Traduci in italiano le battute da #{ids[0] + 1} a #{ids[-1] + 1} del copione, una per una, "
                 "seguendo la scheda del film e continuando in modo coerente la traduzione precedente. "
                 "Restituisci ogni battuta con il suo numero.\n\n<da_tradurre>\n"
                 + "\n\n".join(_battuta(i, testi[i], durate[i]) for i in ids) + "\n</da_tradurre>")
    attesi = sum(int(len(testi[i]) * RISPOSTA_PER_CARATTERE) + CORNICE_JSON for i in ids)
    risposta = sessione.chiedi(prefisso + [{"type": "text", "text": "\n\n".join(parti)}], SCHEMA_BATTUTE,
                               attesi=attesi, avanza=avanza)
    validi = set(ids)
    return {b["n"] - 1: b["t"] for b in risposta["battute"] if b["n"] - 1 in validi}
