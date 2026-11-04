# Ozu

Traduce in italiano i sottotitoli di un film da qualsiasi lingua, lavorando come un adattatore che ha visto tutto il film:

Lo fa gratis con un modello locale (Gemma 4 26B in Ollama) o, a pagamento, con Claude. Il procedimento è lo stesso:

- **Contesto:** il modello legge l'intero copione e compila la *scheda del film* (personaggi, rapporti e registro tu/lei/voi, glossario, errori del file originale), che si può correggere. Poi traduce con tutto il copione e la scheda davanti, quindi nomi, registri e modi di dire restano coerenti dall'inizio alla fine.
- **Lingua parlata:** se i sottotitoli sono inglesi ma il film è giapponese, cinese o portoghese, recupera in italiano le forme di cortesia e i toni che l'inglese appiattisce.
- **Vincoli dei sottotitoli:** al massimo 2 righe da 42 caratteri, velocità di lettura in base alla durata (si punta a 15–17 caratteri al secondo; oltre 20 la battuta torna al modello per essere asciugata), stessi blocchi e tempi, tag come `<i>` intatti. Le battute fuori limite tornano al modello per una revisione; gli a capo seguono le regole italiane (dopo la punteggiatura, mai dopo un articolo).
- **Ogni battuta al suo posto:** a volte un modello salta una battuta e continua con la numerazione sfasata: il testo è buono ma compare al momento sbagliato, per decine di battute. Ozu confronta ogni battuta tradotta con l'originale e con le vicine (lunghezza, numeri, nomi) e ritraduce i tratti fuori posto; se ne resta uno, lo segnala nei risultati. La rilettura non può sfasare un tratto giusto.
- **Errori del sorgente:** corregge gli errori di OCR evidenti («ifs» → «it's», «ls» → «Is») invece di tradurli alla lettera.
- **Rilettura (modello locale):** finita la traduzione, un secondo modello, Qwen 3.8 27B, rilegge tutte le battute con l'originale e la scheda davanti e corregge solo gli errori veri: parole straniere scappate nell'italiano, grammatica, senso, registro. Il correttore ortografico italiano di macOS gli segnala le parole che non conosce, Ozu quelle brevi di un'altra lingua che il correttore non vede («de cui», «est circondata»), e quelle ancora sospette dopo la rilettura gli vengono riproposte con una richiesta mirata. Le battute corrette sono elencate nei risultati. Costa qualche minuto in più (un cambio di modello solo, alla fine); si può spegnere.

Formati: SRT, WebVTT, ASS/SSA e i sottotitoli testuali dentro MKV, MP4 e MOV (con ffmpeg).

## Motori

| Motore | Quando |
|---|---|
| Modello locale | predefinito: gratuito e privato, su questo Mac (Ollama, Gemma 4 26B, riletto da Qwen 3.8 27B; circa 8–10 minuti per un film) |
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
| `--senza-rilettura` | con il modello locale, salta la rilettura con Qwen 3.8 27B |
| `--chiave`, `--chiave-deepl` | salvano le chiavi API |

Se l'elaborazione si interrompe, rilanciando lo stesso comando riparte da dove si era fermata (le battute già tradotte sono in `~/Library/Application Support/Ozu/parziali/`). Se un modello locale risponde con un JSON rotto o troncato per tre volte, Ozu ripara le barre rovesciate non valide e riprova con il blocco diviso a metà, invece di fermarsi.

### Perché la rilettura con Qwen 3.8 27B

Misurata il 3 novembre 2026 su un film di 938 battute tradotto da Gemma 4 26B e corretto a mano (22 correzioni): Qwen 3.8 27B ha proposto 28 correzioni, 25 giuste, 2 indifferenti e 1 peggiore; ne ha trovate 11 delle 22 e altre 14 sfuggite alla correzione a mano. Qwen 3.5 35B era più veloce ma un terzo delle sue modifiche peggiorava la battuta; Gemma che rilegge sé stessa correggeva poco e rompeva qualcosa. Una correzione si applica solo se rispetta i vincoli dei sottotitoli almeno quanto la battuta che sostituisce. Ozu usa la versione DFlash dello stesso modello, `qwen3.8:27b-dflash2-nvfp4`: sullo stesso film ha riletto in 249 secondi invece di 536, con correzioni di qualità pari. Si scarica con `ollama pull qwen3.8:27b-dflash2-nvfp4`: se manca, la rilettura viene saltata con un avviso.

## Ricerca di sottotitoli italiani esistenti

Prima di tradurre, Ozu cerca se esistono già sottotitoli italiani fatti da persone (casella nella sessione, attiva di serie). Fonti: OpenSubtitles (API REST ufficiale) e SUBDL (API ufficiale); Podnapisi non ha un'API ufficiale documentata e viene saltato. Si cerca per hash del video (il video indicato nella sessione o quello accanto ai sottotitoli), poi per titolo e anno ricavati dal nome del file.

Si scartano i sottotitoli segnalati come tradotti automaticamente o con l'IA, quelli che lo dicono nel nome o nel commento, quelli a pagamento, e, dopo il download, quelli con un numero di battute incompatibile, righe vuote o non tradotte, codifica rotta o righe di lunghezza anomala (soglie in `ricerca.SOGLIE`, modificabili in `~/Library/Application Support/Ozu/ricerca.json`).

I tempi si confrontano con i sottotitoli di partenza, che sono a tempo con il video. Un file che copre già tutti i dialoghi vince subito. Uno con il testo buono ma i tempi di un'altra edizione viene riallineato da `allineamento.py`: riconosce il frame rate (25, 24, 23,976), lo spostamento dei cartelli in testa, la deriva lenta e i tagli di montaggio, toglie le battute delle scene che nella nostra versione non ci sono e resta di riserva, usato solo se nessun candidato aveva già i tempi giusti. In ogni caso i sottotitoli devono coprire tutti i dialoghi della versione da sottotitolare (finestre di 2 minuti, scritte in sovrimpressione e titoli di coda esclusi): se manca una scena il file è scartato. Limite noto: al punto esatto di un taglio può restare o mancare una battuta.

Il migliore viene salvato in UTF-8 accanto al file come `<nome del video> - Sottotitoli IT (scaricati).srt`, senza mai sostituire un file esistente, e la traduzione non parte: nell'app c'è «Traduci comunque».

Chiavi (Ozu › Impostazioni, o variabili d'ambiente): `OPENSUBTITLES_API_KEY` (opensubtitles.com › Profilo › API consumers), `OPENSUBTITLES_USERNAME` e `OPENSUBTITLES_PASSWORD` (facoltativi, per più download al giorno), `SUBDL_API_KEY` (subdl.com › pannello › API). Senza chiave la fonte viene saltata con un avviso.

```bash
~/.venvs/ozu/bin/python traduci.py cerca FILM.en.srt [--video FILM.mkv]
~/.venvs/ozu/bin/python traduci.py FILM.en.srt --cerca
curl -s -X POST -d '{"attendi": true}' http://127.0.0.1:8767/api/cerca-sottotitoli
~/.venvs/ozu/bin/python -m unittest test_ricerca -v
```

## Insieme a Vertov e Murnau

Usano Ollama con un altro modello (Qwen3-VL 30B), quindi se lavorassero insieme Ollama passerebbe di continuo da un modello all'altro. Ozu cede il passo: prima di ogni richiesta al modello controlla se Vertov o Murnau stanno lavorando (dalla loro finestra, porte 8765 e 8766, o dal processo `descrivi.py` / `murnau.py`) e aspetta. Le sue richieste tengono il modello in memoria 5 minuti invece di 30. A fine lavoro, se fermato o se Ozu viene chiuso, toglie dalla memoria la copia del modello caricata da lui, se nessun altro la sta usando. Se l'app si chiude in modo anomalo, `traduci.py` se ne accorge e si ferma da solo.

## Struttura

Come Vertov e Murnau: `app.py` apre la finestra (pywebview) e serve `interfaccia.html` su `127.0.0.1:8767`. Il lavoro vero lo fa `traduci.py` in un processo separato, che comunica l'avanzamento con righe `@@`. Gli altri moduli: `adattamento.py` (istruzioni, scheda, traduzione, revisione, stime), i motori `motore_locale.py` (Ollama), `motore_claude.py` (API di Anthropic), `motore_deepl.py` e `motore_google.py`, `motori.py` (elenco dei motori e ripiego), `chiavi.py` (chiavi API), `sottotitoli.py` (formati, vincoli, battute fuori posto), `revisione.py` (rilettura con il secondo modello), `allineamento.py` (tempi dei sottotitoli scaricati), `ricerca.py`, `banco_di_prova.py`, `aggiornamenti.py`, `pulizia.py` e `icona/genera_icona.py`.

Le sessioni sono salvate in `~/Library/Application Support/Ozu/sessioni/` (ogni scrittura è atomica e protetta da un lucchetto, perché l'interfaccia e il lavoro in corso le modificano insieme) e il registro in `ozu.log` nella stessa cartella.

## Banco di prova dei modelli

Prima di adottare un modello nuovo o aggiornato (Gemma, Qwen, o un altro in Ollama), `banco_di_prova.py` lo fa lavorare su film già tradotti e corretti a mano e lo confronta con le prove precedenti:

```bash
~/.venvs/ozu/bin/python banco_di_prova.py aggiungi NOME FILM.en.srt FILM.corretta.it.srt --prima FILM.it.srt --scheda FILM.scheda.json --da en --audio fr
~/.venvs/ozu/bin/python banco_di_prova.py prova                       # i modelli di adesso
~/.venvs/ozu/bin/python banco_di_prova.py prova --modello NOME_OLLAMA  # un candidato
~/.venvs/ozu/bin/python banco_di_prova.py storia
```

Per ogni prova misura i tempi di traduzione e di rilettura, la somiglianza al riferimento corretto a mano (chrF, 0–100), gli **errori noti** rifatti (le parole straniere o storpiate che hai tolto correggendo: «dozens», «de cui»), le battute con parole sospette, i tratti fuori posto (prima e dopo il controllo) e i punti da controllare, e dice per ognuna se va meglio, peggio o uguale rispetto alla prova precedente. La somiglianza al riferimento favorisce un po' lo stile del modello che l'ha prodotto: gli errori noti, le parole sospette e i tratti fuori posto no. Casi, storia e traduzioni di ogni prova restano in `~/Library/Application Support/Ozu/banco_di_prova/`, mai su GitHub.

## Aggiornamenti

- `aggiornamenti.py` controlla Ozu (annuncio all'indirizzo in `aggiornamenti.json`), ffmpeg e Ollama (Homebrew), i due modelli locali (Gemma 4 26B che traduce, Qwen 3.8 27B che rilegge) e le librerie Python dell'ambiente. L'app chiede prima di installare; se le librerie non si caricano più, tornano alla versione precedente.
- L'annuncio è `https://github.com/FredFusco/ozu/releases/latest/download/ozu.json`. Prima di adottare un aggiornamento dei modelli, conviene passarlo dal banco di prova.
- Per pubblicare una versione: `git commit` e `git push`, poi `python prepara_aggiornamento.py 1.1.0 "Novità…" --pubblica`.
- Su GitHub finiscono solo i file del programma ammessi da `.gitignore`, mai sottotitoli, video o schede dei film.
