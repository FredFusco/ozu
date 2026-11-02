# Ozu

Traduce in italiano i sottotitoli di un film da qualsiasi lingua, lavorando come un adattatore che ha visto tutto il film:

Lo fa gratis con un modello locale (Gemma 4 26B in Ollama) o, a pagamento, con Claude. Il procedimento è lo stesso:

- **Contesto:** il modello legge l'intero copione e compila la *scheda del film* (personaggi, rapporti e registro tu/lei/voi, glossario, errori del file originale), che si può correggere. Poi traduce con tutto il copione e la scheda davanti, quindi nomi, registri e modi di dire restano coerenti dall'inizio alla fine.
- **Lingua parlata:** se i sottotitoli sono inglesi ma il film è giapponese, cinese o portoghese, recupera in italiano le forme di cortesia e i toni che l'inglese appiattisce.
- **Vincoli dei sottotitoli:** al massimo 2 righe da 42 caratteri, velocità di lettura in base alla durata, stessi blocchi e tempi, tag come `<i>` intatti. Le battute fuori limite tornano al modello per una revisione; gli a capo seguono le regole italiane (dopo la punteggiatura, mai dopo un articolo).
- **Errori del sorgente:** corregge gli errori di OCR evidenti («ifs» → «it's», «ls» → «Is») invece di tradurli alla lettera.

Formati: SRT, WebVTT, ASS/SSA e i sottotitoli testuali dentro MKV, MP4 e MOV (con ffmpeg).

## Motori

| Motore | Quando |
|---|---|
| Modello locale | predefinito: gratuito e privato, su questo Mac (Ollama, Gemma 4 26B; circa 5–10 minuti per film) |
| Claude Fable 5.1 | la qualità più alta, a pagamento (chiave API di Anthropic con credito) |
| Claude Opus 5.5 | ottima qualità, a pagamento, costa meno |
| DeepL | traduzione automatica (chiave DeepL, anche gratuita) |
| Google Traduttore | funziona sempre, senza chiavi; il meno affidabile |

Se il motore scelto non è disponibile, Ozu passa al migliore tra quelli che vengono dopo. Senza chiavi è completamente gratuito. Le chiavi si inseriscono in *Ozu › Impostazioni…* e restano in `~/Library/Application Support/Ozu/chiavi.json` (permessi 600). Le variabili `ANTHROPIC_API_KEY` e `DEEPL_API_KEY` hanno la precedenza.

## Uso

Doppio clic su **Ozu.app** (che sta in questa cartella). Al primo avvio su un Mac nuovo prepara l'ambiente `~/.venvs/ozu` con pywebview e anthropic.

Da Terminale:

```bash
~/.venvs/ozu/bin/python traduci.py FILM.en.srt --audio ja
~/.venvs/ozu/bin/python traduci.py EP01.srt EP02.srt EP03.srt --solo-scheda
~/.venvs/ozu/bin/python traduci.py EP*.srt --scheda EP01.scheda.json --note "Il capitano dà del voi ai soldati"
```

| Parametro | Cosa fa |
|---|---|
| `--motore` | `locale` (predefinito), `claude`, `deepl`, `google` |
| `--modello` | per Claude `claude-fable-5-1` o `claude-opus-5-5`; per il modello locale un nome di Ollama (predefinito `gemma4:26b`) |
| `--da` | lingua dei sottotitoli (predefinito: automatica) |
| `--audio` | lingua parlata nel film (es. `ja`, `zh`, `pt-BR`) |
| `--note`, `--note-file` | indicazioni per l'adattatore |
| `--scheda` | usa una scheda del film già pronta (anche corretta a mano) |
| `--solo-scheda` | prepara la scheda e si ferma |
| `--salva-scheda` | dove salvare la scheda |
| `--out` | cartella dei file tradotti (predefinito: accanto agli originali) |
| `--chiave`, `--chiave-deepl` | salvano le chiavi API |

Se l'elaborazione si interrompe, rilanciando lo stesso comando riparte da dove si era fermata (le battute già tradotte sono in `~/Library/Application Support/Ozu/parziali/`).

## Insieme a Vertov e Murnau

Usano Ollama con un altro modello (Qwen3-VL 30B), quindi se lavorassero insieme Ollama passerebbe di continuo da un modello all'altro. Ozu cede il passo: prima di ogni richiesta al modello controlla se Vertov o Murnau stanno lavorando (dalla loro finestra, porte 8765 e 8766, o dal processo `descrivi.py` / `murnau.py`) e aspetta. Le sue richieste tengono il modello in memoria 5 minuti invece di 30. A fine lavoro, se fermato o se Ozu viene chiuso, toglie dalla memoria la copia del modello caricata da lui, se nessun altro la sta usando. Se l'app si chiude in modo anomalo, `traduci.py` se ne accorge e si ferma da solo.

## Struttura

Come Vertov e Murnau: `app.py` apre la finestra (pywebview) e serve `interfaccia.html` su `127.0.0.1:8767`. Il lavoro vero lo fa `traduci.py` in un processo separato, che comunica l'avanzamento con righe `@@`. Gli altri moduli: `adattamento.py` (istruzioni, scheda, traduzione, revisione, stime), i motori `motore_locale.py` (Ollama), `motore_claude.py` (API di Anthropic), `motore_deepl.py` e `motore_google.py`, `motori.py` (elenco dei motori e ripiego), `chiavi.py` (chiavi API), `sottotitoli.py` (formati e vincoli), `aggiornamenti.py`, `pulizia.py` e `icona/genera_icona.py`.

Le sessioni sono salvate in `~/Library/Application Support/Ozu/sessioni/` (ogni scrittura è atomica e protetta da un lucchetto, perché l'interfaccia e il lavoro in corso le modificano insieme) e il registro in `ozu.log` nella stessa cartella.

## Aggiornamenti

- `aggiornamenti.py` controlla Ozu (annuncio all'indirizzo in `aggiornamenti.json`), ffmpeg e Ollama (Homebrew), il modello locale e le librerie Python dell'ambiente. L'app chiede prima di installare; se le librerie non si caricano più, tornano alla versione precedente.
- L'annuncio previsto è `https://github.com/FredFusco/ozu/releases/latest/download/ozu.json`. Il repository va ancora creato su GitHub.
- Per pubblicare una versione: `git commit` e `git push`, poi `python prepara_aggiornamento.py 1.1.0 "Novità…" --pubblica`.
- Su GitHub finiscono solo i file del programma ammessi da `.gitignore`, mai sottotitoli, video o schede dei film.
