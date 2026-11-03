"""La rilettura: un secondo modello rilegge la traduzione e corregge gli errori veri.

Dopo la traduzione con il modello locale, Qwen 3.8 27B rilegge tutte le battute, a blocchi, con
l'originale accanto e la scheda del film, e propone una correzione solo dove trova un errore:
parole straniere o storpiate, grammatica, senso sbagliato, registro o nomi incoerenti con la scheda.
Prima della rilettura il correttore ortografico italiano di macOS segnala le parole che non conosce
(gratuito e istantaneo): il revisore le trova accanto alle battute.

Misurato il 3 novembre 2026 su un film di 938 battute, con le correzioni a mano dell'utente come
riferimento: Gemma 4 26B traduce bene ma ogni tanto lascia passare parole straniere («dozens»,
«de cui», «est circondata») o sbaglia il senso; Qwen 3.8 27B ha proposto 28 correzioni, 25 giuste,
2 indifferenti e 1 peggiore. La sua versione DFlash fa lo stesso lavoro in circa la metà del tempo
(4 minuti per un film di 938 battute). Qwen 3.5 35B era più veloce ma un terzo delle sue
modifiche peggiorava la battuta; Gemma che rilegge sé stessa correggeva poco.

Una correzione si applica solo se rispetta i vincoli dei sottotitoli almeno quanto la battuta che
sostituisce. Le battute corrette sono elencate nel resoconto, per poterle controllare.
"""

import json
import re

import adattamento
import sottotitoli as st

# La versione DFlash (decodifica speculativa, pesi NVFP4) dello stesso modello: sullo stesso film ha riletto
# in 249 secondi invece di 536, con correzioni di qualità pari (51 contro 43, 2-3 sbagliate per parte).
REVISORE = "qwen3.8:27b-dflash2-nvfp4"
NOME_REVISORE = "Qwen 3.8 27B"
BLOCCO = 120
BLOCCO_MINIMO = 15  # sotto questa misura un blocco che non passa si salta (la rilettura è un di più)
# Quanto pesa la rilettura nell'avanzamento, in battute tradotte: misurata, circa il doppio della traduzione.
PESO = 2

SCHEMA = {"type": "object", "properties": {"correzioni": {"type": "array", "items": {
    "type": "object", "properties": {"n": {"type": "integer"}, "t": {"type": "string"}, "motivo": {"type": "string"}},
    "required": ["n", "t", "motivo"], "additionalProperties": False}}},
    "required": ["correzioni"], "additionalProperties": False}

RICHIESTA = """Ora sei il revisore. Qui sotto ci sono battute già tradotte in italiano da un altro adattatore, \
con l'originale accanto. Segnala SOLO le battute con un errore vero:
- parole non italiane, storpiate o inesistenti, errori di ortografia, di grammatica o di accordo;
- senso sbagliato rispetto all'originale;
- registro (tu/lei/voi), nomi o termini incoerenti con la scheda del film;
- errori del file sorgente (OCR) tradotti alla lettera.
Non segnalare scelte di stile accettabili, sinonimi o riformulazioni equivalenti: se la battuta va bene, \
lasciala fuori. Accanto ad alcune battute trovi le parole sospette: sconosciute al correttore ortografico o \
tipiche di un'altra lingua. Possono essere errori o nomi propri, decidi tu. Per ogni battuta segnalata scrivi la versione corretta completa \
(stessi vincoli dei sottotitoli, stessi a capo e stessi tag quando possibile) e il motivo in al massimo \
otto parole. Se non c'è niente da correggere, restituisci una lista vuota."""

RICHIESTA_PAROLE = """Ora sei il revisore. In queste battute tradotte in italiano ci sono parole sospette: \
sconosciute al correttore ortografico o tipiche di un'altra lingua. Per ognuna decidi: se è un errore (parola straniera, storpiata o inesistente in \
italiano) scrivi la battuta corretta completa, con gli stessi a capo e gli stessi tag; se è un nome proprio o una \
parola italiana corretta, lascia fuori la battuta. Il motivo in al massimo otto parole. Se va tutto bene, \
restituisci una lista vuota."""


# --------------------------------------------------------------------------
# Correttore ortografico di macOS
# --------------------------------------------------------------------------

def ortografia(testi: list, tradotti: list, scheda: dict = None) -> dict:
    """{indice: [parole sconosciute]} delle battute tradotte. Si ignorano i nomi: le parole con la
    maiuscola che compaiono nell'originale o nella scheda. Senza il correttore (fuori da macOS) è vuoto."""
    try:
        from AppKit import NSSpellChecker
    except ImportError:
        return {}
    correttore = NSSpellChecker.sharedSpellChecker()
    nomi = {w for t in testi for w in re.findall(r"[\w']+", t.lower())}
    nomi |= set(re.findall(r"[\w']+", json.dumps(scheda or {}, ensure_ascii=False).lower()))
    segnalate = {}
    for i, t in enumerate(tradotti):
        testo, inizio, sconosciute = st.senza_tag(t or ""), 0, []
        while inizio < len(testo):
            r, _ = correttore.checkSpellingOfString_startingAt_language_wrap_inSpellDocumentWithTag_wordCount_(
                testo, inizio, "it", False, 0, None)
            if r.length == 0 or r.location >= len(testo):
                break
            parola = testo[r.location:r.location + r.length]
            if not (parola[:1].isupper() and parola.lower() in nomi):
                sconosciute.append(parola)
            inizio = r.location + r.length
        if sconosciute:
            segnalate[i] = sconosciute
    return segnalate


# Parole brevi di altre lingue che a volte scappano nell'italiano («de cui», «la casa est circondata»,
# «elle vostre case»): il correttore non le segnala perché esistono nei nomi («Rue des Lilas»).
STRANIERE = {"de", "des", "du", "les", "elle", "est", "et", "avec", "pour", "dans", "sont", "mais", "pas", "je",
             "the", "and", "of", "with", "you", "is", "are", "it", "that", "this", "for", "but", "not", "what",
             "el", "los", "las", "y", "que", "muy", "esta", "este", "der", "die", "das", "und", "ist"}
# «est» è anche italiano: «a est», «verso est», «nord-est».
_EST_ITALIANO = {"a", "ad", "verso", "nord", "sud", "da", "l'", "dell'", "all'"}


def straniere(testi: list, tradotti: list, scheda: dict = None) -> dict:
    """{indice: [parole]} delle battute tradotte con parole brevi di un'altra lingua. Non contano quelle
    che fanno parte di un nome copiato dall'originale o dalla scheda (stessa coppia di parole)."""
    riferimento = json.dumps(scheda or {}, ensure_ascii=False).lower()
    segnalate = {}
    for i, t in enumerate(tradotti):
        parole = re.findall(r"[\w']+", st.senza_tag(t or "").lower())
        originale = " ".join(re.findall(r"[\w']+", testi[i].lower()))
        sospette = []
        for k, p in enumerate(parole):
            if p not in STRANIERE:
                continue
            vicine = [" ".join(parole[max(0, k - 1):k + 1]), " ".join(parole[k:k + 2])]
            if any(len(v.split()) == 2 and (v in originale or v in riferimento) for v in vicine):
                continue  # parte di un nome: «Rue des Lilas», «Maison des Roses»
            if p == "est" and (k > 0 and parole[k - 1] in _EST_ITALIANO or "-est" in (t or "").lower()):
                continue
            sospette.append(p)
        if sospette:
            segnalate[i] = sospette
    return segnalate


def sospette(testi: list, tradotti: list, scheda: dict = None) -> dict:
    """Le parole da far guardare al revisore: sconosciute al correttore o tipiche di un'altra lingua."""
    unite = ortografia(testi, tradotti, scheda)
    for i, parole in straniere(testi, tradotti, scheda).items():
        unite[i] = unite.get(i, []) + parole
    return unite


# --------------------------------------------------------------------------
# Rilettura
# --------------------------------------------------------------------------

def caratteri_massimi(testi: list, tradotti: list, scheda: dict, opzioni: dict) -> int:
    """La richiesta più lunga della rilettura, in caratteri: per fissare lo spazio di lavoro del revisore."""
    fisso = len(adattamento._intestazione(opzioni)) + len(json.dumps(scheda or {}, ensure_ascii=False, indent=1)) \
        + len(RICHIESTA) + 400
    blocchi = [sum(len(testi[i]) + len(tradotti[i] or "") + 90 for i in range(k, min(k + BLOCCO, len(testi))))
               for k in range(0, len(testi), BLOCCO)]
    return fisso + max(blocchi, default=0)


def rivedi(sessione, testi: list, durate: list, tradotti: list, scheda: dict, opzioni: dict,
           avanza=None) -> tuple:
    """(traduzioni rilette, modifiche). modifiche: [{"n", "prima", "dopo", "motivo"}, …], in ordine.
    avanza(frazione) segue il lavoro."""
    n = len(testi)
    durate = durate or [None] * n
    dubbi = sospette(testi, tradotti, scheda)
    prefisso = [{"type": "text", "text": adattamento._intestazione(opzioni) + "\n\n<scheda_film>\n"
                 + json.dumps(scheda or {}, ensure_ascii=False, indent=1) + "\n</scheda_film>"}]
    riletti, modifiche = list(tradotti), {}

    def applica(correzioni, attuali):
        for c in correzioni:
            i, prima = c["n"] - 1, attuali[c["n"] - 1]
            dopo = st.a_capo_automatico(st.ritocca_italiano(c["t"]))
            if not dopo.strip() or dopo == prima:
                continue
            if len(st.problemi(testi[i], dopo, durate[i])) > len(st.problemi(testi[i], prima, durate[i])):
                continue  # la correzione romperebbe i vincoli: resta la traduzione
            riletti[i] = dopo
            modifiche[i] = {"n": i + 1, "prima": tradotti[i], "dopo": dopo, "motivo": c["motivo"].strip()}

    for inizio in range(0, n, BLOCCO):
        ids = list(range(inizio, min(inizio + BLOCCO, n)))
        applica(_blocco(sessione, prefisso, testi, durate, tradotti, dubbi, ids, RICHIESTA), tradotti)
        if avanza:
            avanza(ids[-1] + 1, n)
    # Nella rilettura lunga il revisore a volte lascia passare una parola che il correttore aveva segnalato
    # («danger»): le battute ancora sospette si ripropongono da sole, con una richiesta mirata (pochi secondi).
    ancora = sospette(testi, riletti, scheda)
    if ancora:
        attuali = list(riletti)
        applica(_blocco(sessione, prefisso, testi, durate, attuali, ancora, sorted(ancora)[:BLOCCO], RICHIESTA_PAROLE),
                attuali)
    # Il revisore a volte sposta testo da una battuta all'altra: va bene se rimette a posto una traduzione
    # sfasata, non se ne sfasa una giusta. Le battute dei tratti che diventano fuori posto tornano com'erano.
    gia = {i for a, b in st.sfasamenti(testi, tradotti) for i in range(a, b + 1)}
    for a, b in st.sfasamenti(testi, riletti):
        for i in range(a, b + 1):
            if i not in gia:
                riletti[i] = tradotti[i]
                modifiche.pop(i, None)
    modifiche = [m for _, m in sorted(modifiche.items()) if m["dopo"] != m["prima"]]
    return riletti, modifiche


def _blocco(sessione, prefisso, testi, durate, tradotti, dubbi, ids, richiesta) -> list:
    """Le correzioni proposte per le battute ids. Se la risposta non è valida riprova con le due metà;
    sotto BLOCCO_MINIMO battute lascia stare (meglio una battuta non riletta che un lavoro fermo)."""
    elenco = "\n\n".join(
        adattamento._battuta(i, f"Originale:\n{testi[i]}\nTraduzione:\n{tradotti[i]}"
                                + (f"\nParole sospette: {', '.join(dubbi[i])}" if i in dubbi else ""),
                             durate[i])
        for i in ids)
    try:
        risposta = sessione.chiedi(prefisso + [{"type": "text", "text": richiesta + "\n\n<battute>\n" + elenco
                                                + "\n</battute>"}], SCHEMA, attesi=60 * len(ids))
    except adattamento.RispostaNonValida:
        if len(ids) < 2 * BLOCCO_MINIMO:
            print(f"Rilettura saltata per le battute #{ids[0] + 1}-#{ids[-1] + 1}: risposta non valida.", flush=True)
            return []
        meta = len(ids) // 2
        return (_blocco(sessione, prefisso, testi, durate, tradotti, dubbi, ids[:meta], richiesta)
                + _blocco(sessione, prefisso, testi, durate, tradotti, dubbi, ids[meta:], richiesta))
    validi = set(ids)
    return [c for c in risposta["correzioni"] if isinstance(c.get("n"), int) and c["n"] - 1 in validi
            and isinstance(c.get("t"), str)]
