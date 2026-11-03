"""Riallinea i tempi di sottotitoli scaricati a quelli dei sottotitoli di partenza.

Serve quando i sottotitoli italiani trovati sono giusti ma fatti su un'altra edizione del
film. Si confrontano i «ritmi» dei due file, cioè quando cominciano le battute, che non
dipendono dalla lingua, e si cercano:
- un cambio di frame rate (25 ↔ 23,976, 24 ↔ 23,976, 25 ↔ 24): un'accelerazione costante;
- uno spostamento fisso (cartelli o titoli di testa di durata diversa);
- tagli: blocchi di battute che si spostano a gradini (montaggi diversi).

Il controllo che non si salta mai: dopo il riallineamento i sottotitoli devono coprire tutti i
dialoghi della versione di partenza, finestra per finestra. Se manca una scena (per esempio
una scena che c'è solo nella versione da sottotitolare) il file viene scartato; le battute di
scene che nella versione di partenza non ci sono vengono tolte, perché finirebbero su
immagini sbagliate.

Tutto in Python puro: i file hanno al massimo qualche migliaio di battute.
"""

import bisect
import re
from collections import Counter

# Rapporti tra frame rate: tempo nella versione di partenza = tempo nel file scaricato × rapporto.
RAPPORTI = {1.0: None, 25 / 23.976: "25 → 23,976", 23.976 / 25: "23,976 → 25", 24 / 23.976: "24 → 23,976",
            23.976 / 24: "23,976 → 24", 25 / 24: "25 → 24", 24 / 25: "24 → 25"}
PASSO = 0.2               # secondi: risoluzione dei voti sugli spostamenti
RAGGIO = 600.0            # secondi: spostamento d'insieme massimo (titoli di testa)
RAGGIO_LOCALE = 300.0     # secondi: spostamento massimo di un blocco (tagli, scene in più o in meno)
BLOCCO = 300.0            # secondi: durata dei blocchi per gli spostamenti locali. Su blocchi più brevi,
                          # con file temporizzati da persone diverse, il caso vince sul segnale.
SOGLIE = {
    "copertura_min": 0.80,          # quota dei dialoghi di partenza coperta dai sottotitoli riallineati
    "copertura_finestra_min": 0.30, # per ogni finestra di 2 minuti con dialoghi
    "finestra": 120.0,
    "battute_finestra_min": 4,      # una finestra conta se ha almeno tante battute di partenza
    "tenute_min": 0.60,             # quota delle battute scaricate che trovano posto
    "tolleranza": 0.6,              # secondi: battute considerate «allo stesso posto»
}


# --------------------------------------------------------------------------
# Lettura e scrittura dei tempi
# --------------------------------------------------------------------------

_TEMPO = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def _secondi(testo: str) -> float:
    h, m, s, f = _TEMPO.search(testo).groups()
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(f) / 10 ** len(f)


def voci(testo: str) -> list:
    """[(inizio, fine, testo), …] da un SRT, un WebVTT o un ASS."""
    testo = testo.replace("\r\n", "\n").replace("\r", "\n")
    risultato = []
    if "[Events]" in testo:
        for riga in testo.split("\n"):
            if riga.startswith("Dialogue:"):
                campi = riga.split(":", 1)[1].split(",", 9)
                if len(campi) == 10:
                    risultato.append((_secondi(campi[1]), _secondi(campi[2]),
                                      re.sub(r"\{[^}]*\}", "", campi[9]).replace("\\N", "\n")))
        return sorted(risultato)
    for blocco in re.split(r"\n\s*\n", testo.strip()):
        righe = blocco.split("\n")
        i = next((k for k, r in enumerate(righe) if "-->" in r), None)
        if i is None:
            continue
        inizio, fine = righe[i].split("-->", 1)
        corpo = "\n".join(righe[i + 1:]).strip()
        if corpo and _TEMPO.search(inizio) and _TEMPO.search(fine):
            risultato.append((_secondi(inizio), _secondi(fine), corpo))
    return sorted(risultato)


def _srt_tempo(s: float) -> str:
    ms = max(0, round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def scrivi_srt(lista: list) -> str:
    return "\n\n".join(f"{n}\n{_srt_tempo(a)} --> {_srt_tempo(b)}\n{t}" for n, (a, b, t) in enumerate(lista, 1)) + "\n"


def tempo_leggibile(s: float) -> str:
    return f"{int(s) // 60}:{int(s) % 60:02d}"


# --------------------------------------------------------------------------
# Copertura: i sottotitoli coprono tutti i dialoghi della versione di partenza?
# --------------------------------------------------------------------------

def _unione(intervalli: list) -> list:
    uniti = []
    for a, b in sorted(intervalli):
        if uniti and a <= uniti[-1][1]:
            uniti[-1][1] = max(uniti[-1][1], b)
        else:
            uniti.append([a, b])
    return uniti


def _sovrapposizione(a: list, b: list) -> float:
    """Secondi in comune tra due liste di intervalli già unite e ordinate."""
    totale, i, j = 0.0, 0, 0
    while i < len(a) and j < len(b):
        totale += max(0.0, min(a[i][1], b[j][1]) - max(a[i][0], b[j][0]))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return totale


# Scritte in sovrimpressione (titoli di testa, date, luoghi, cartelli): i sottotitoli di partenza
# spesso le traducono, quelli italiani di un film già in italiano no. Non sono dialoghi, quindi non
# contano nel controllo di copertura.
_RUOLI = re.compile(r"\b(presents?|presenta|a film by|un film di|directed by|regia|written by|screenplay|"
                    r"music by|musiche|produced by|prodotto da|producer|production|director of|photography|"
                    r"editing|edited by|montaggio|set design\w*|costume\w*|cast|starring|with the participation)\b",
                    re.I)
_DATA = re.compile(r"^\s*(\d{1,2}(st|nd|rd|th)?\s+\w+\s+\d{4}|\w+\s+\d{1,2},?\s+\d{4}|\d{4}\b)")


def e_scritta(testo: str) -> bool:
    """La battuta è una scritta sullo schermo e non un dialogo?"""
    pulito = re.sub(r"<[^>]+>|\{[^}]*\}", "", testo or "").strip()
    lettere = [c for c in pulito if c.isalpha()]
    if not lettere:
        return True
    maiuscole = sum(c.isupper() for c in lettere) / len(lettere)
    return maiuscole > 0.8 and len(lettere) > 3 or bool(_RUOLI.search(pulito)) or bool(_DATA.match(pulito))


def copertura(partenza: list, scaricati: list, s: dict = None) -> tuple:
    """(quota coperta in tutto, [(inizio, fine) delle finestre scoperte]). partenza e scaricati: [(inizio,
    fine[, testo]), …]. Le scritte sullo schermo della partenza non contano (vedi e_scritta)."""
    s = s or SOGLIE
    margine = s["tolleranza"]
    partenza = [v for v in partenza if len(v) < 3 or not e_scritta(v[2])] or partenza
    p = _unione([(a, b) for a, b, *_ in partenza])
    c = _unione([(a - margine, b + margine) for a, b, *_ in scaricati])
    totale = sum(b - a for a, b in p) or 1.0
    quota = _sovrapposizione(p, c) / totale
    scoperte = []
    if partenza:
        inizio, fine = partenza[0][0], max(b for _, b, *_ in partenza)
        t = inizio
        while t < fine:
            dentro = [(max(a, t), min(b, t + s["finestra"])) for a, b, *_ in partenza if b > t and a < t + s["finestra"]]
            if len(dentro) >= s["battute_finestra_min"]:
                durata = sum(b - a for a, b in dentro) or 1.0
                if _sovrapposizione(_unione(dentro), c) / durata < s["copertura_finestra_min"]:
                    if scoperte and abs(scoperte[-1][1] - t) < 1:
                        scoperte[-1] = (scoperte[-1][0], t + s["finestra"])
                    else:
                        scoperte.append((t, t + s["finestra"]))
            t += s["finestra"] / 2
    return quota, scoperte


# --------------------------------------------------------------------------
# Allineamento
# --------------------------------------------------------------------------

def _voto(inizi_partenza: list, inizi: list, centro: float = 0.0, raggio: float = RAGGIO) -> tuple:
    """(spostamento più votato, quanti voti): per ogni battuta, le differenze con gli inizi di
    partenza vicini; lo spostamento giusto è quello che mette d'accordo più battute."""
    voti = Counter()
    for t in inizi:
        da = bisect.bisect_left(inizi_partenza, t + centro - raggio)
        a = bisect.bisect_right(inizi_partenza, t + centro + raggio)
        for r in inizi_partenza[da:a]:
            voti[round((r - t) / PASSO)] += 1
    if not voti:
        return centro, 0
    migliore, _ = voti.most_common(1)[0]
    # Si sommano i voti dei passi accanto: le battute non coincidono mai al decimo di secondo.
    sostegno = sum(voti[migliore + k] for k in (-2, -1, 0, 1, 2))
    vicini = [(migliore + k) * PASSO for k in (-2, -1, 0, 1, 2) for _ in range(voti[migliore + k])]
    return sum(vicini) / len(vicini), sostegno


def _accordo(inizi_partenza: list, inizi: list, spostamento: float, tolleranza: float = 1.0) -> int:
    """Quante battute, spostate di `spostamento`, cominciano entro la tolleranza da una battuta di partenza."""
    conta = 0
    for t in inizi:
        t += spostamento
        k = bisect.bisect_left(inizi_partenza, t)
        if any(0 <= j < len(inizi_partenza) and abs(inizi_partenza[j] - t) <= tolleranza for j in (k - 1, k)):
            conta += 1
    return conta


def _punteggio(parlato: list, inizi_p: list, blocco: list, spostamento: float) -> float:
    """Quanto combacia un blocco [(inizio, fine), …] spostato: quota del suo parlato che cade sul parlato
    di partenza, più quota delle battute che cominciano vicino a una di partenza (da 0 a 2). La prima
    parte non dipende da come le frasi sono divise in battute, la seconda è più precisa."""
    durata = sum(b - a for a, b in blocco) or 1.0
    spostati = _unione([(a + spostamento, b + spostamento) for a, b in blocco])
    return (_sovrapposizione(parlato, spostati) / durata
            + _accordo(inizi_p, [a for a, _ in blocco], spostamento) / len(blocco))


def _migliore(parlato: list, inizi_p: list, blocco: list, centro: float, raggio: float, quanti: int = 25) -> tuple:
    """(spostamento, punteggio) migliore entro centro ± raggio: si provano gli spostamenti più votati
    (vedi _voto) e il centro, poi si rifinisce al decimo di secondo."""
    voti = Counter()
    for a, _ in blocco:
        da = bisect.bisect_left(inizi_p, a + centro - raggio)
        fino = bisect.bisect_right(inizi_p, a + centro + raggio)
        for r in inizi_p[da:fino]:
            voti[round((r - a) / PASSO)] += 1
    candidati = {centro} | {k * PASSO for k, _ in voti.most_common(quanti)}
    migliore = max(candidati, key=lambda d: _punteggio(parlato, inizi_p, blocco, d))
    fini = [migliore + k * 0.1 for k in range(-4, 5)]
    migliore = max(fini, key=lambda d: _punteggio(parlato, inizi_p, blocco, d))
    return migliore, _punteggio(parlato, inizi_p, blocco, migliore)


def allinea(partenza: list, scaricati: list, s: dict = None):
    """Riallinea `scaricati` ([(inizio, fine, testo), …]) su `partenza` (stesso formato).

    Restituisce (voci riallineate, descrizione) oppure (None, motivo dello scarto)."""
    s = {**SOGLIE, **(s or {})}
    if len(partenza) < 10 or len(scaricati) < 10:
        return None, "troppo poche battute per riallineare i tempi"
    inizi_p = sorted(a for a, *_ in partenza)
    parlato = _unione([(a, b) for a, b, *_ in partenza])
    n = len(scaricati)

    # 1. Frame rate e spostamento d'insieme: la combinazione che combacia di più.
    migliore = None
    for rapporto in RAPPORTI:
        tutto = [(a * rapporto, b * rapporto) for a, b, *_ in scaricati]
        spostamento, punti = _migliore(parlato, inizi_p, tutto, 0.0, RAGGIO, 40)
        if migliore is None or punti > migliore[0]:
            migliore = (punti, rapporto, spostamento)
    _, rapporto, spostamento = migliore
    base = [(a * rapporto + spostamento, b * rapporto + spostamento) for a, b, *_ in scaricati]

    # 2. Spostamenti locali (tagli, deriva lenta di un frame rate non esatto). Il film si divide in
    # blocchi di cinque minuti; per ognuno si raccolgono gli spostamenti candidati. Scegliere blocco
    # per blocco non funziona: il ritmo dei dialoghi fa trovare coincidenze convincenti a minuti di
    # distanza. Si cerca invece il percorso migliore lungo tutto il film (programmazione dinamica,
    # come fanno i programmi di sincronizzazione): ogni salto costa, quindi vince solo un taglio vero,
    # che migliora molti blocchi di fila; una coincidenza su un solo blocco non ripaga i due salti.
    blocchi, inizio = [], 0
    for i in range(1, n + 1):
        if i == n or (base[i - 1][1] - base[inizio][0] >= BLOCCO and i - inizio >= 8):
            blocchi.append((inizio, i))
            inizio = i
    candidati, guadagni, casi = [], [], []
    for da, a in blocchi:
        blocco = base[da:a]
        voti = Counter()
        for x, _ in blocco:
            lo = bisect.bisect_left(inizi_p, x - RAGGIO_LOCALE)
            hi = bisect.bisect_right(inizi_p, x + RAGGIO_LOCALE)
            for r in inizi_p[lo:hi]:
                voti[round((r - x) / PASSO)] += 1
        # Candidati: i più votati, piccoli aggiustamenti attorno a zero e il migliore effettivo vicino
        # (entro un minuto) e lontano (entro RAGGIO_LOCALE), che tra i voti potrebbe non spiccare.
        insieme = ({0.0} | {k * PASSO for k, _ in voti.most_common(20)} | {k * 0.5 for k in range(-6, 7)}
                   | {_migliore(parlato, inizi_p, blocco, 0.0, 60.0)[0],
                      _migliore(parlato, inizi_p, blocco, 0.0, RAGGIO_LOCALE)[0]})
        lista = sorted(insieme)
        candidati.append(lista)
        guadagni.append([_punteggio(parlato, inizi_p, blocco, d) * len(blocco) for d in lista])
        casi.append(sum(_punteggio(parlato, inizi_p, blocco, d) for d in (-29.0, -17.0, 17.0, 29.0)) / 4)
    media = n / len(blocchi)
    salto = 0.6 * media           # costo di un taglio: più di quanto una coincidenza fa guadagnare a un blocco
    lieve = 0.05 * media          # costo per secondo dei piccoli aggiustamenti (deriva): basso, la deriva è normale
    ASSENTE = None                # stato «scena che nella versione di partenza non c'è»: il blocco si toglie
    for b, (da, a) in enumerate(blocchi):
        candidati[b].append(ASSENTE)
        guadagni[b].append((casi[b] + 0.05) * (a - da))

    def costo(b, x, y):
        """Passare dal blocco b-1 spostato di x al blocco b spostato di y. Le scene restano in ordine: un
        blocco non può cominciare molto prima della fine del precedente. Fino a mezzo blocco di
        sovrapposizione è ammesso: è un taglio a metà blocco, sistemato poi battuta per battuta."""
        if x is ASSENTE or y is ASSENTE:
            return salto / 2
        if base[blocchi[b][0]][0] + y < base[blocchi[b - 1][1] - 1][1] + x - BLOCCO / 2:
            return float("inf")
        d = abs(x - y)
        return d * lieve if d <= 1.5 else salto + 0.002 * d

    totale = [list(guadagni[0])]
    da_dove = [[None] * len(candidati[0])]
    for b in range(1, len(blocchi)):
        riga, origini = [], []
        for c, guadagno in zip(candidati[b], guadagni[b]):
            k = max(range(len(candidati[b - 1])), key=lambda k: totale[-1][k] - costo(b, candidati[b - 1][k], c))
            riga.append(totale[-1][k] - costo(b, candidati[b - 1][k], c) + guadagno)
            origini.append(k)
        totale.append(riga)
        da_dove.append(origini)
    scelta = [max(range(len(totale[-1])), key=lambda k: totale[-1][k])]
    for b in range(len(blocchi) - 1, 0, -1):
        scelta.append(da_dove[b][scelta[-1]])
    scelta.reverse()
    spostamenti = []
    for b, (da, a) in enumerate(blocchi):  # rifinitura al decimo di secondo attorno alla scelta
        d0 = candidati[b][scelta[b]]
        spostamenti.append(ASSENTE if d0 is ASSENTE else max(
            (d0 + k * 0.1 for k in range(-4, 5)), key=lambda d: _punteggio(parlato, inizi_p, base[da:a], d)))

    # 3. Spostamento di ogni battuta: quello del suo blocco. Dove c'è un taglio tra due blocchi, il
    # punto esatto è quello che fa combaciare di più le battute dei due blocchi. Un blocco che, anche
    # spostato, non combacia più del caso è una scena che nella versione di partenza non c'è: si toglie.
    correzioni = [0.0] * n
    for b, (da, a) in enumerate(blocchi):
        for i in range(da, a):
            correzioni[i] = "assente" if spostamenti[b] is ASSENTE else spostamenti[b]
    # Dove c'è un taglio tra due blocchi: il punto esatto, battuta per battuta. Se dopo il taglio lo
    # spostamento cala di D secondi, il file scaricato ha D secondi in più (una scena che nella versione
    # di partenza non c'è): si cerca il tratto lungo circa D che, tolto, fa combaciare di più.
    for b in range(1, len(blocchi)):
        x, y = spostamenti[b - 1], spostamenti[b]
        if x is ASSENTE or y is ASSENTE or abs(x - y) <= 1.5:
            continue
        da, a = blocchi[b - 1][0], blocchi[b][1]
        tratto = base[da:a]
        # Punteggio di ogni battuta da sola, meno quello che avrebbe per caso: così la somma conta
        # quanto le battute tenute combaciano davvero, e togliere una battuta buona costa.
        caso = sum(_punteggio(parlato, inizi_p, [v], d) for v in tratto for d in (-29.0, -17.0, 17.0, 29.0)) / (4 * len(tratto))
        con_x = [0.0]
        con_y = [0.0]
        for v in tratto:
            con_x.append(con_x[-1] + _punteggio(parlato, inizi_p, [v], x) - caso)
            con_y.append(con_y[-1] + _punteggio(parlato, inizi_p, [v], y) - caso)
        migliore, punto, fino = None, 0, 0
        for p in range(len(tratto) + 1):
            fini = [p]
            if x - y > 1.5:  # materiale in più: si toglie un tratto lungo circa x - y
                fini += [q for q in range(p + 1, len(tratto) + 1)
                         if 0.8 * (x - y) <= tratto[q - 1][1] - tratto[p][0] <= (x - y) + 5]
            for q in fini:
                valore = con_x[p] + (con_y[-1] - con_y[q])
                if q > p:  # il tratto tolto dovrebbe durare quanto la scena in più (x - y)
                    valore -= 0.3 * abs((tratto[q - 1][1] - tratto[p][0]) - (x - y))
                if migliore is None or valore > migliore:
                    migliore, punto, fino = valore, p, q
        if fino > punto:
            # Ai bordi del tratto tolto possono restare battute della scena in più che per caso
            # combaciano un po': si tolgono finché non combaciano più del caso.
            while punto > 0 and con_x[punto] - con_x[punto - 1] <= 0:
                punto -= 1
            while fino < len(tratto) and con_y[fino + 1] - con_y[fino] <= 0:
                fino += 1
        for k in range(da, a):
            correzioni[k] = x if k - da < punto else "assente" if k - da < fino else y

    riallineate, tolte = [], 0
    for (a, b), (_, _, testo), correzione in zip(base, scaricati, correzioni):
        if correzione == "assente":
            tolte += 1
            continue
        riallineate.append((a + correzione, b + correzione, testo))
    riallineate.sort()
    for i in range(len(riallineate) - 1):  # niente sovrapposizioni dopo i gradini
        a, b, t = riallineate[i]
        if b > riallineate[i + 1][0]:
            riallineate[i] = (a, max(a + 0.3, riallineate[i + 1][0] - 0.05), t)
    numeri = [c for c in correzioni if c != "assente"]
    gradini = sum(1 for x, y in zip(numeri, numeri[1:]) if abs(x - y) > 1.5)
    deriva = max(numeri) - min(numeri) if numeri else 0

    # 4. Il controllo che non si salta: tutta la versione di partenza deve essere coperta.
    if len(riallineate) < s["tenute_min"] * n:
        return None, f"tempi incompatibili: solo {len(riallineate)} battute su {n} trovano posto"
    quota, scoperte = copertura(partenza, riallineate, s)
    if scoperte:
        a, b = scoperte[0]
        altre = f" (e altri {len(scoperte) - 1} punti)" if len(scoperte) > 1 else ""
        return None, (f"non copre tutto il film: manca una scena tra {tempo_leggibile(a)} e "
                      f"{tempo_leggibile(b)}{altre}, forse una versione con un montaggio diverso")
    if quota < s["copertura_min"]:
        return None, f"copre solo il {quota:.0%} dei dialoghi anche riallineato"

    parti = []
    if RAPPORTI[rapporto]:
        parti.append(f"frame rate {RAPPORTI[rapporto]}")
    totale = spostamento + (numeri[0] if numeri else 0)
    if abs(totale) >= 0.3:
        parti.append(f"spostati di {totale:+.1f} s".replace(".", ","))
    if gradini:
        parti.append(f"{gradini} {'taglio' if gradini == 1 else 'tagli'} di montaggio")
    elif deriva >= 1.0:
        parti.append(f"deriva lenta corretta ({deriva:.1f} s)".replace(".", ","))
    if tolte:
        parti.append(f"{tolte} battute di scene assenti tolte")
    descrizione = ("tempi riallineati: " + ", ".join(parti)) if parti else "tempi già corretti"
    return riallineate, f"{descrizione}; copre il {quota:.0%} dei dialoghi"
