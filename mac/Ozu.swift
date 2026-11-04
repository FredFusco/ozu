// Ozu.app: la finestra nativa per Mac di Ozu.
//
// Il motore resta in Python: questa app avvia `app.py --servizio` (il server dell'interfaccia, con la
// traduzione, la ricerca, la rilettura e gli aggiornamenti) e mostra l'interfaccia in una finestra WebKit.
// Del Mac si occupa lei: si apre subito, ha menu e scorciatoie native, apre le finestre di scelta dei file,
// riceve i file trascinati sulla finestra o sull'icona nel Dock e quelli aperti dal Finder con «Apri con»,
// chiede conferma all'uscita con il logo di Ozu e chiude il server quando si esce.
//
// Si compila con mac/compila.sh (bastano gli strumenti da riga di comando di Xcode).

import AppKit
import WebKit

let PORTA = 8767  // la stessa di app.py: Vertov e Murnau riconoscono Ozu da qui
let INDIRIZZO = URL(string: "http://127.0.0.1:\(PORTA)/")!
let CODICE_RIAVVIO: Int32 = 75  // app.py --servizio dopo un aggiornamento: ci si chiude senza chiedere
let CODICE_PORTA_OCCUPATA: Int32 = 3  // c'è già un altro Ozu aperto (per esempio dal Terminale)

let CASA = FileManager.default.homeDirectoryForCurrentUser
let DATI = CASA.appendingPathComponent("Library/Application Support/Ozu")
// Dove sta il programma (app.py e il resto): accanto all'app, oppure, per la copia nella cartella Applicazioni
// (quella che il Mac elenca tra le app), dove l'ha annotato l'ultima volta l'app che sta nel programma.
let SEGNAPOSTO = DATI.appendingPathComponent("cartella_programma")
let CARTELLA = trovaProgramma()
// La copia di Ozu.app nella cartella Applicazioni dell'utente: si tiene uguale a quella del programma.
let COPIA_IN_APPLICAZIONI = CASA.appendingPathComponent("Applications/Ozu.app")

func trovaProgramma() -> URL {
    let accanto = Bundle.main.bundleURL.deletingLastPathComponent()
    let gestore = FileManager.default
    if gestore.fileExists(atPath: accanto.appendingPathComponent("app.py").path) {
        try? gestore.createDirectory(at: DATI, withIntermediateDirectories: true)
        try? accanto.path.write(to: SEGNAPOSTO, atomically: true, encoding: .utf8)
        return accanto
    }
    if let annotata = try? String(contentsOf: SEGNAPOSTO, encoding: .utf8) {
        let cartella = URL(fileURLWithPath: annotata.trimmingCharacters(in: .whitespacesAndNewlines))
        if gestore.fileExists(atPath: cartella.appendingPathComponent("app.py").path) { return cartella }
    }
    return accanto
}

/// Se c'è la copia nella cartella Applicazioni ed è diversa da Ozu.app del programma (per esempio dopo un
/// aggiornamento), la si sostituisce con una copia nuova. Un'app aperta continua a girare con il suo file.
func allineaCopiaInApplicazioni() {
    let gestore = FileManager.default
    let originale = CARTELLA.appendingPathComponent("Ozu.app")
    guard originale.standardizedFileURL.path != COPIA_IN_APPLICAZIONI.standardizedFileURL.path,
          gestore.fileExists(atPath: COPIA_IN_APPLICAZIONI.path), gestore.fileExists(atPath: originale.path) else { return }
    let parti = ["Contents/MacOS/Ozu", "Contents/Info.plist", "Contents/Resources/ozu.icns"]
    let uguali = parti.allSatisfy { parte in
        gestore.contentsEqual(atPath: originale.appendingPathComponent(parte).path,
                              andPath: COPIA_IN_APPLICAZIONI.appendingPathComponent(parte).path)
    }
    guard !uguali else { return }
    let nuova = COPIA_IN_APPLICAZIONI.deletingLastPathComponent().appendingPathComponent(".Ozu.app.nuova")
    do {
        try? gestore.removeItem(at: nuova)
        try gestore.copyItem(at: originale, to: nuova)
        _ = try gestore.replaceItemAt(COPIA_IN_APPLICAZIONI, withItemAt: nuova)
        annota("aggiornata la copia di Ozu.app nella cartella Applicazioni")
    } catch {
        try? gestore.removeItem(at: nuova)
        annota("non riesco ad aggiornare la copia nella cartella Applicazioni: \(error.localizedDescription)")
    }
}
let REGISTRO = DATI.appendingPathComponent("ozu.log")
let PREFERENZE = DATI.appendingPathComponent("preferenze.json")
let AMBIENTE = CASA.appendingPathComponent(".venvs/ozu")
let PYTHON = AMBIENTE.appendingPathComponent("bin/python")

// I colori della carta dell'interfaccia (--carta in interfaccia.html), chiaro e scuro.
let CARTA = NSColor(name: nil) { aspetto in
    aspetto.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(srgbRed: 0x1d / 255, green: 0x1e / 255, blue: 0x23 / 255, alpha: 1)
        : NSColor(srgbRed: 0xf3 / 255, green: 0xee / 255, blue: 0xe4 / 255, alpha: 1)
}

/// Il registro di Ozu aperto in aggiunta: l'app e il motore ci scrivono insieme senza sovrapporsi.
func registroInAggiunta() -> FileHandle? {
    try? FileManager.default.createDirectory(at: DATI, withIntermediateDirectories: true)
    let descrittore = open(REGISTRO.path, O_WRONLY | O_APPEND | O_CREAT, 0o644)
    return descrittore < 0 ? nil : FileHandle(fileDescriptor: descrittore, closeOnDealloc: true)
}

func annota(_ testo: String) {
    let riga = "\(Date().formatted(date: .omitted, time: .standard)) Ozu.app: \(testo)\n"
    if let maniglia = registroInAggiunta() {
        maniglia.write(riga.data(using: .utf8)!)
        try? maniglia.close()
    }
}

// Le scelte dell'interfaccia, in comune con app.py (leggi_preferenze, scrivi_preferenze).
func leggiPreferenze() -> [String: Any] {
    guard let dati = try? Data(contentsOf: PREFERENZE),
          let valori = try? JSONSerialization.jsonObject(with: dati) as? [String: Any] else { return [:] }
    return valori
}

func scriviPreferenza(_ chiave: String, _ valore: Any) {
    var valori = leggiPreferenze()
    valori[chiave] = valore
    if let dati = try? JSONSerialization.data(withJSONObject: valori, options: [.prettyPrinted, .sortedKeys]) {
        try? dati.write(to: PREFERENZE, options: .atomic)
    }
}

func versione() -> String {
    (try? String(contentsOf: CARTELLA.appendingPathComponent("VERSIONE"), encoding: .utf8))?
        .trimmingCharacters(in: .whitespacesAndNewlines) ?? "?"
}

func iconaOzu() -> NSImage? {
    NSImage(contentsOf: CARTELLA.appendingPathComponent("icona_ozu.png")) ?? NSApp.applicationIconImage
}

// --------------------------------------------------------------------------
// Il motore: app.py --servizio
// --------------------------------------------------------------------------

final class Motore {
    private var processo: Process?
    var finito: ((Int32) -> Void)?

    /// L'ambiente Python c'è già? Si guarda solo se esistono Python e la libreria di Anthropic (istantaneo).
    var ambientePronto: Bool {
        guard FileManager.default.isExecutableFile(atPath: PYTHON.path) else { return false }
        let lib = AMBIENTE.appendingPathComponent("lib")
        let versioni = (try? FileManager.default.contentsOfDirectory(atPath: lib.path)) ?? []
        return versioni.contains { FileManager.default.fileExists(
            atPath: lib.appendingPathComponent("\($0)/site-packages/anthropic").path) }
    }

    /// Primo avvio su un Mac nuovo: prepara l'ambiente Python, come faceva il vecchio lanciatore.
    func preparaAmbiente(_ fatto: @escaping (Bool) -> Void) {
        annota("primo avvio: preparo l'ambiente Python in \(AMBIENTE.path)")
        let comandi = """
        export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
        /opt/homebrew/bin/python3 -m venv "$HOME/.venvs/ozu" || python3 -m venv "$HOME/.venvs/ozu" || exit 1
        "$HOME/.venvs/ozu/bin/python" -m pip install -q --disable-pip-version-check pywebview anthropic
        """
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-c", comandi]
        collegaRegistro(p)
        p.terminationHandler = { fine in DispatchQueue.main.async { fatto(fine.terminationStatus == 0) } }
        do { try p.run() } catch { fatto(false) }
    }

    func avvia() {
        let p = Process()
        p.executableURL = PYTHON
        p.arguments = [CARTELLA.appendingPathComponent("app.py").path, "--servizio"]
        p.currentDirectoryURL = CARTELLA
        var ambiente = ProcessInfo.processInfo.environment
        ambiente["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (ambiente["PATH"] ?? "/usr/bin:/bin")
        ambiente["PYTHONUNBUFFERED"] = "1"
        ambiente["OZU_APP"] = Bundle.main.bundleURL.path  // dopo un aggiornamento app.py riapre questa stessa app
        p.environment = ambiente
        collegaRegistro(p)
        p.terminationHandler = { [weak self] fine in
            DispatchQueue.main.async {
                guard let self, self.processo === fine else { return }
                self.processo = nil
                self.finito?(fine.terminationStatus)
            }
        }
        do {
            try p.run()
            processo = p
            annota("avviato il motore (app.py --servizio, processo \(p.processIdentifier))")
        } catch {
            annota("non riesco ad avviare il motore: \(error.localizedDescription)")
            finito?(-1)
        }
    }

    /// Ferma il motore (che ferma a sua volta un'eventuale traduzione: si potrà riprendere).
    func ferma() {
        guard let p = processo, p.isRunning else { return }
        processo = nil
        p.terminate()  // SIGTERM: app.py ferma il lavoro ed esce
        let limite = Date().addingTimeInterval(5)
        while p.isRunning && Date() < limite { usleep(50_000) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        annota("motore fermato")
    }

    private func collegaRegistro(_ p: Process) {
        if let maniglia = registroInAggiunta() {
            p.standardOutput = maniglia
            p.standardError = maniglia
        }
    }
}

/// Aspetta che il server risponda (al massimo `secondi`), poi chiama `fatto(true)`; altrimenti `fatto(false)`.
func aspettaServer(secondi: Double, _ fatto: @escaping (Bool) -> Void) {
    let limite = Date().addingTimeInterval(secondi)
    func prova() {
        var richiesta = URLRequest(url: INDIRIZZO.appendingPathComponent("api/sessioni"))
        richiesta.timeoutInterval = 1
        URLSession.shared.dataTask(with: richiesta) { _, risposta, _ in
            DispatchQueue.main.async {
                if (risposta as? HTTPURLResponse)?.statusCode == 200 { fatto(true) }
                else if Date() > limite { fatto(false) }
                else { DispatchQueue.main.asyncAfter(deadline: .now() + 0.1, execute: prova) }
            }
        }.resume()
    }
    prova()
}

// --------------------------------------------------------------------------
// La pagina: WebKit con il trascinamento dei file
// --------------------------------------------------------------------------

final class PaginaOzu: WKWebView {
    var fileLasciati: (([String]) -> Void)?

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let opzioni: [NSPasteboard.ReadingOptionKey: Any] = [.urlReadingFileURLsOnly: true]
        let file = (sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: opzioni) as? [URL]) ?? []
        // Prima la pagina (toglie l'evidenziazione della zona di arrivo), poi i percorsi completi al server.
        let gestito = super.performDragOperation(sender)
        if !file.isEmpty {
            fileLasciati?(file.map(\.path))
            return true
        }
        return gestito
    }
}

// --------------------------------------------------------------------------
// L'app
// --------------------------------------------------------------------------

@MainActor
final class Ozu: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate,
                 WKScriptMessageHandlerWithReply {
    let motore = Motore()
    var finestra: NSWindow!
    var pagina: PaginaOzu!
    var attesa: NSView!
    var scritta: NSTextField!
    var guida: NSWindow?
    var paginaPronta = false
    var daAggiungere: [String] = []  // file aperti dal Finder prima che la pagina fosse pronta
    var inUscita = false

    // ----- avvio

    func applicationDidFinishLaunching(_ notification: Notification) {
        annota("avvio, versione \(versione()), da \(Bundle.main.bundleURL.path)")
        NSApp.mainMenu = barraDeiMenu()
        creaFinestra()
        guard FileManager.default.fileExists(atPath: CARTELLA.appendingPathComponent("app.py").path) else {
            errore("Non trovo il programma di Ozu.",
                   "Apri una volta Ozu.app dalla cartella del programma (quella con app.py): da lì in poi Ozu si apre anche da qui.",
                   soloEsci: true)
            return
        }
        DispatchQueue.global(qos: .utility).async { allineaCopiaInApplicazioni() }
        motore.finito = { [weak self] codice in self?.motoreFermato(codice) }
        if motore.ambientePronto {
            avviaMotore()
        } else {
            scritta.stringValue = "Primo avvio: preparo Ozu, ci vuole qualche minuto…"
            motore.preparaAmbiente { [weak self] riuscito in
                guard let self else { return }
                if riuscito { self.avviaMotore() } else {
                    self.errore("Non riesco a preparare l'ambiente Python di Ozu.",
                                "Serve Python 3 (per esempio con Homebrew: brew install python). I dettagli sono nel registro di Ozu.")
                }
            }
        }
    }

    func avviaMotore() {
        scritta.stringValue = "Avvio di Ozu…"
        motore.avvia()
        aspettaServer(secondi: 60) { [weak self] pronto in
            guard let self, !self.inUscita else { return }
            if pronto { self.pagina.load(URLRequest(url: INDIRIZZO)) }
            else { self.errore("Il motore di Ozu non risponde.", "I dettagli sono nel registro di Ozu (Aiuto › Apri il registro di Ozu).") }
        }
    }

    func motoreFermato(_ codice: Int32) {
        guard !inUscita else { return }
        annota("il motore si è fermato (codice \(codice))")
        if codice == CODICE_RIAVVIO {  // aggiornamento installato: app.py riapre Ozu appena ci chiudiamo
            inUscita = true
            NSApp.terminate(nil)
        } else if codice == CODICE_PORTA_OCCUPATA {
            errore("C'è già un altro Ozu aperto.",
                   "Forse è stato avviato dal Terminale (app.py). Chiudilo e riapri Ozu.", soloEsci: true)
        } else {
            let avviso = NSAlert()
            avviso.icon = iconaOzu()
            avviso.messageText = "Il motore di Ozu si è fermato."
            avviso.informativeText = "Una traduzione in corso riprenderà da dove si era fermata. I dettagli sono nel registro di Ozu."
            avviso.addButton(withTitle: "Riavvia")
            avviso.addButton(withTitle: "Esci da Ozu")
            if avviso.runModal() == .alertFirstButtonReturn {
                paginaPronta = false
                mostraAttesa(true)
                avviaMotore()
            } else {
                inUscita = true
                NSApp.terminate(nil)
            }
        }
    }

    func errore(_ messaggio: String, _ dettaglio: String, soloEsci: Bool = false) {
        let avviso = NSAlert()
        avviso.icon = iconaOzu()
        avviso.messageText = messaggio
        avviso.informativeText = dettaglio
        avviso.addButton(withTitle: "Esci da Ozu")
        if !soloEsci { avviso.addButton(withTitle: "Apri il registro") }
        if avviso.runModal() == .alertSecondButtonReturn { apriRegistro(nil) }
        inUscita = true
        NSApp.terminate(nil)
    }

    // ----- finestra

    func creaFinestra() {
        finestra = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 860),
                            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        finestra.title = "Ozu"
        finestra.minSize = NSSize(width: 720, height: 560)
        finestra.backgroundColor = CARTA
        finestra.tabbingMode = .disallowed
        finestra.isReleasedWhenClosed = false
        finestra.delegate = self
        finestra.center()
        finestra.setFrameAutosaveName("Ozu finestra principale")  // ricorda posizione e misura

        let configurazione = WKWebViewConfiguration()
        configurazione.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "ozu")
        pagina = PaginaOzu(frame: .zero, configuration: configurazione)
        pagina.navigationDelegate = self
        pagina.uiDelegate = self
        pagina.allowsMagnification = false
        pagina.allowsBackForwardNavigationGestures = false
        pagina.setValue(false, forKey: "drawsBackground")  // niente lampo bianco mentre si carica
        pagina.underPageBackgroundColor = CARTA
        pagina.fileLasciati = { [weak self] percorsi in self?.aggiungi(percorsi) }

        // Mentre il motore parte: il logo e una riga di testo, sulla carta di Ozu.
        let logo = NSImageView(image: iconaOzu() ?? NSImage())
        logo.imageScaling = .scaleProportionallyUpOrDown
        logo.widthAnchor.constraint(equalToConstant: 96).isActive = true
        logo.heightAnchor.constraint(equalToConstant: 96).isActive = true
        scritta = NSTextField(labelWithString: "Avvio di Ozu…")
        scritta.font = NSFont(name: "Hiragino Mincho ProN", size: 15) ?? .systemFont(ofSize: 15)
        scritta.textColor = .secondaryLabelColor
        let pila = NSStackView(views: [logo, scritta])
        pila.orientation = .vertical
        pila.spacing = 14
        pila.translatesAutoresizingMaskIntoConstraints = false
        attesa = NSView()
        attesa.addSubview(pila)
        NSLayoutConstraint.activate([pila.centerXAnchor.constraint(equalTo: attesa.centerXAnchor),
                                     pila.centerYAnchor.constraint(equalTo: attesa.centerYAnchor)])

        let contenuto = NSView()
        for vista in [pagina!, attesa!] as [NSView] {
            vista.translatesAutoresizingMaskIntoConstraints = false
            contenuto.addSubview(vista)
            NSLayoutConstraint.activate([vista.leadingAnchor.constraint(equalTo: contenuto.leadingAnchor),
                                         vista.trailingAnchor.constraint(equalTo: contenuto.trailingAnchor),
                                         vista.topAnchor.constraint(equalTo: contenuto.topAnchor),
                                         vista.bottomAnchor.constraint(equalTo: contenuto.bottomAnchor)])
        }
        finestra.contentView = contenuto
        mostraAttesa(true)
        finestra.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func mostraAttesa(_ si: Bool) {
        attesa.isHidden = !si
        pagina.isHidden = si
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        finestra.makeKeyAndOrderFront(nil)  // clic sull'icona nel Dock
        return true
    }

    // ----- uscita: il dialogo di Ozu, con il suo logo e «Non mostrare più» (la preferenza è quella di app.py)

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if sender === finestra { NSApp.terminate(nil); return false }
        return true
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if !inUscita && !(leggiPreferenze()["non_chiedere_uscita"] as? Bool ?? false) {
            NSApp.activate(ignoringOtherApps: true)
            let avviso = NSAlert()
            avviso.icon = iconaOzu()
            avviso.messageText = "Chiudere Ozu?"
            avviso.informativeText = "Se c'è una traduzione in corso verrà fermata: rilanciandola riprenderà dal punto in cui si è interrotta."
            avviso.addButton(withTitle: "Esci da Ozu")
            avviso.addButton(withTitle: "Annulla")
            avviso.showsSuppressionButton = true
            avviso.suppressionButton?.title = "Non mostrare più"
            guard avviso.runModal() == .alertFirstButtonReturn else { return .terminateCancel }
            if avviso.suppressionButton?.state == .on { scriviPreferenza("non_chiedere_uscita", true) }
        }
        inUscita = true
        motore.ferma()
        return .terminateNow
    }

    // ----- file dal Finder e dal Dock («Apri con», trascinamento sull'icona)

    func application(_ application: NSApplication, open urls: [URL]) {
        aggiungi(urls.filter(\.isFileURL).map(\.path))
    }

    func aggiungi(_ percorsi: [String]) {
        guard !percorsi.isEmpty else { return }
        guard paginaPronta else { daAggiungere += percorsi; return }
        if let json = try? JSONSerialization.data(withJSONObject: percorsi),
           let testo = String(data: json, encoding: .utf8) {
            pagina.evaluateJavaScript("aggiungiPercorsi(\(testo))")
        }
        finestra.makeKeyAndOrderFront(nil)
    }

    // ----- finestre di scelta dei file, chieste dalla pagina (api() in interfaccia.html)

    func userContentController(_ controller: WKUserContentController, didReceive messaggio: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        guard let corpo = messaggio.body as? [String: Any], let scelta = corpo["scegli"] as? [String: Any],
              let tipo = scelta["tipo"] as? String else { replyHandler([String](), nil); return }
        let pannello = NSOpenPanel()
        pannello.canChooseFiles = tipo != "cartella"
        pannello.canChooseDirectories = tipo == "cartella"
        pannello.allowsMultipleSelection = scelta["multipli"] as? Bool ?? false
        pannello.canCreateDirectories = tipo == "cartella"
        switch tipo {
        case "cartella": pannello.message = "Scegli una cartella"
        case "video": pannello.message = "Scegli il video del film (serve per cercare i sottotitoli per hash)"
        case "scheda": pannello.message = "Scegli una scheda del film (.scheda.json)"; pannello.allowedContentTypes = [.json]
        default: pannello.message = "Scegli i sottotitoli (o i video con i sottotitoli dentro)"
        }
        pannello.prompt = "Scegli"
        let finestraPagina = messaggio.webView?.window ?? finestra!
        pannello.beginSheetModal(for: finestraPagina) { risposta in
            replyHandler(risposta == .OK ? pannello.urls.map(\.path) : [String](), nil)
        }
    }

    // ----- navigazione: solo Ozu dentro la finestra, il resto nel browser

    func webView(_ webView: WKWebView, decidePolicyFor azione: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = azione.request.url else { decisionHandler(.cancel); return }
        if url.host == "127.0.0.1" && url.port == PORTA || url.scheme == "about" || url.scheme == "data" {
            decisionHandler(.allow)
        } else {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for azione: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = azione.request.url { NSWorkspace.shared.open(url) }  // link con target="_blank"
        return nil
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard webView === pagina else { return }
        paginaPronta = true
        mostraAttesa(false)
        let attesi = daAggiungere
        daAggiungere = []
        aggiungi(attesi)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        annota("pagina non caricata: \(error.localizedDescription)")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        annota("WebKit si è fermato: ricarico la pagina")
        webView.reload()
    }

    // ----- alert(), confirm() e prompt() della pagina, come finestre del Mac

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage messaggio: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let avviso = NSAlert()
        avviso.icon = iconaOzu()
        avviso.messageText = messaggio
        avviso.addButton(withTitle: "OK")
        avviso.beginSheetModal(for: webView.window ?? finestra) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage messaggio: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let avviso = NSAlert()
        avviso.icon = iconaOzu()
        avviso.messageText = messaggio
        avviso.addButton(withTitle: "OK")
        avviso.addButton(withTitle: "Annulla")
        avviso.beginSheetModal(for: webView.window ?? finestra) { r in completionHandler(r == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt richiesta: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let avviso = NSAlert()
        avviso.icon = iconaOzu()
        avviso.messageText = richiesta
        let campo = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        campo.stringValue = defaultText ?? ""
        avviso.accessoryView = campo
        avviso.addButton(withTitle: "OK")
        avviso.addButton(withTitle: "Annulla")
        avviso.beginSheetModal(for: webView.window ?? finestra) { r in
            completionHandler(r == .alertFirstButtonReturn ? campo.stringValue : nil)
        }
    }

    // ----- menu

    /// Una voce di menu che esegue un comando dell'interfaccia (ozuComando in interfaccia.html).
    func comando(_ titolo: String, _ comando: String, _ tasto: String = "", _ maiuscole: Bool = false) -> NSMenuItem {
        let item = NSMenuItem(title: titolo, action: #selector(eseguiComando(_:)), keyEquivalent: tasto)
        item.representedObject = comando
        item.target = self
        if maiuscole { item.keyEquivalentModifierMask = [.command, .shift] }
        return item
    }

    func voce(_ titolo: String, _ azione: Selector, _ tasto: String = "",
              _ modificatori: NSEvent.ModifierFlags = .command, bersaglio: AnyObject? = nil) -> NSMenuItem {
        let item = NSMenuItem(title: titolo, action: azione, keyEquivalent: tasto)
        item.keyEquivalentModifierMask = modificatori
        item.target = bersaglio
        return item
    }

    func menu(_ titolo: String, _ voci: [NSMenuItem]) -> NSMenuItem {
        let menu = NSMenu(title: titolo)
        voci.forEach(menu.addItem)
        let contenitore = NSMenuItem(title: titolo, action: nil, keyEquivalent: "")
        contenitore.submenu = menu
        return contenitore
    }

    func barraDeiMenu() -> NSMenu {
        let barra = NSMenu()
        let servizi = NSMenuItem(title: "Servizi", action: nil, keyEquivalent: "")
        servizi.submenu = NSMenu(title: "Servizi")
        NSApp.servicesMenu = servizi.submenu
        barra.addItem(menu("Ozu", [
            voce("Informazioni su Ozu", #selector(informazioni(_:)), bersaglio: self), .separator(),
            comando("Impostazioni…", "impostazioni", ","), comando("Controlla aggiornamenti…", "aggiornamenti"),
            .separator(), servizi, .separator(),
            voce("Nascondi Ozu", #selector(NSApplication.hide(_:)), "h"),
            voce("Nascondi altre", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]),
            voce("Mostra tutte", #selector(NSApplication.unhideAllApplications(_:)), "", []), .separator(),
            voce("Esci da Ozu", #selector(NSApplication.terminate(_:)), "q"),
        ]))
        barra.addItem(menu("File", [
            comando("Nuova sessione", "nuova", "n"), comando("Apri sessione…", "apri", "o"),
            comando("Salva sessione", "salva", "s"), comando("Elimina sessione…", "elimina"), .separator(),
            comando("Aggiungi sottotitoli…", "aggiungi-file", "o", true), comando("Aggiungi una cartella…", "aggiungi-cartella"),
            comando("Usa una scheda esistente…", "usa-scheda"), .separator(),
            comando("Libera spazio…", "pulizia"), .separator(),
            voce("Chiudi finestra", #selector(NSWindow.performClose(_:)), "w"),
        ]))
        barra.addItem(menu("Modifica", [
            voce("Annulla", Selector(("undo:")), "z"), voce("Ripeti", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            voce("Taglia", #selector(NSText.cut(_:)), "x"), voce("Copia", #selector(NSText.copy(_:)), "c"),
            voce("Incolla", #selector(NSText.paste(_:)), "v"), voce("Seleziona tutto", #selector(NSText.selectAll(_:)), "a"),
        ]))
        barra.addItem(menu("Vista", [
            comando("Sottotitoli", "vista-materiali", "1"), comando("Scheda del film", "vista-scheda", "2"),
            comando("Traduzione", "vista-traduzione", "3"), .separator(),
            comando("Dettagli dell'elaborazione", "dettagli"), .separator(),
            voce("Attiva modalità a tutto schermo", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ]))
        barra.addItem(menu("Traduzione", [
            comando("Prepara la scheda del film", "scheda", "r", true), comando("Traduci", "traduci", "r"),
            comando("Ferma", "ferma", "."),
        ]))
        let finestre = menu("Finestra", [
            voce("Contrai", #selector(NSWindow.performMiniaturize(_:)), "m"),
            voce("Ridimensiona", #selector(NSWindow.performZoom(_:)), "", []), .separator(),
            voce("Porta tutto in primo piano", #selector(NSApplication.arrangeInFront(_:)), "", []),
        ])
        barra.addItem(finestre)
        NSApp.windowsMenu = finestre.submenu
        let aiuto = menu("Aiuto", [
            voce("Guida di Ozu", #selector(apriGuida(_:)), "?", bersaglio: self),
            voce("Apri il registro di Ozu", #selector(apriRegistro(_:)), "", [], bersaglio: self),
        ])
        barra.addItem(aiuto)
        NSApp.helpMenu = aiuto.submenu
        return barra
    }

    @objc func eseguiComando(_ item: NSMenuItem) {
        guard paginaPronta, let comando = item.representedObject as? String else { return }
        finestra.makeKeyAndOrderFront(nil)
        pagina.evaluateJavaScript("ozuComando(\(String(reflecting: comando)))")
    }

    @objc func informazioni(_ sender: Any?) {
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Ozu", .applicationVersion: versione(), .version: "",
            .applicationIcon: iconaOzu() ?? NSImage(),
            .credits: NSAttributedString(string: "Sottotitoli in italiano da qualsiasi lingua.\nTitolo in Mourier (Velvetyne, licenza SIL OFL).",
                                         attributes: [.font: NSFont.systemFont(ofSize: 11)]),
        ])
    }

    @objc func apriGuida(_ sender: Any?) {
        if let guida { guida.makeKeyAndOrderFront(nil); return }
        let finestraGuida = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 820, height: 820),
                                     styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        finestraGuida.title = "Guida di Ozu"
        finestraGuida.backgroundColor = CARTA
        finestraGuida.isReleasedWhenClosed = false
        finestraGuida.setFrameAutosaveName("Ozu guida")
        let vista = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        vista.navigationDelegate = self
        vista.uiDelegate = self
        vista.setValue(false, forKey: "drawsBackground")
        vista.load(URLRequest(url: INDIRIZZO.appendingPathComponent("aiuto")))
        finestraGuida.contentView = vista
        finestraGuida.center()
        finestraGuida.makeKeyAndOrderFront(nil)
        guida = finestraGuida
    }

    @objc func apriRegistro(_ sender: Any?) {
        if FileManager.default.fileExists(atPath: REGISTRO.path) {
            NSWorkspace.shared.open([REGISTRO], withApplicationAt: URL(fileURLWithPath: "/System/Applications/Utilities/Console.app"),
                                    configuration: NSWorkspace.OpenConfiguration())
        } else {
            NSWorkspace.shared.open(DATI)
        }
    }
}

// Il codice qui sopra gira tutto sul thread principale, come vuole AppKit.
MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegato = Ozu()
    app.delegate = delegato
    app.setActivationPolicy(.regular)
    app.run()
}
