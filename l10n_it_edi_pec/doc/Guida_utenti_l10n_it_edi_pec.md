# Guida utente modulo `l10n_it_edi_pec`

Questa guida descrive come configurare il modulo **l10n_it_edi_pec** e come viene gestito lo scambio di fatture elettroniche via PEC:

- configurazione azienda e server PEC
- flusso delle **fatture di vendita** (invio a SdI e gestione delle risposte)
- flusso delle **fatture di acquisto** (import da XML ricevuti via PEC)

La guida presuppone che i moduli standard Odoo per l’EDI italiano siano installati, in particolare:

- `l10n_it_edi`
- eventuali moduli aggiuntivi ufficiali (withholding, DOI, ecc.) secondo le esigenze del database


## 1. Configurazione di base

### 1.1. Attivazione del canale PEC per l’EDI

Menu: **Impostazioni → Aziende → [Azienda] → E‑invoice PEC Configuration**

Campi principali introdotti dal modulo:

- **Use PEC for E‑invoices** (`l10n_it_edi_use_pec`)
  - Quando attivo, le fatture elettroniche vengono inviate allo SdI **via PEC**, invece che tramite il proxy standard di Odoo.

- **SdI PEC Email** (`l10n_it_edi_pec_sdi_email`)
  - Indirizzo PEC dello SdI.
  - Valore tipico: `sdi01@pec.fatturapa.it`.

- **PEC Server for E‑invoices** (`l10n_it_edi_pec_server_id`)
  - Server **in ingresso** (fetchmail) da cui leggere le mail PEC contenenti:
    - notifiche SdI relative alle fatture inviate;
    - fatture di acquisto ricevute via SdI.

- **PEC SMTP Server** (`l10n_it_edi_pec_smtp_server_id`)
  - Server di posta **in uscita** (record `ir.mail_server`) usato per spedire il file XML al SdI via PEC.

- **SdI User for PEC** (`l10n_it_edi_pec_sdi_user_id`)
  - Utente Odoo usato come creatore delle fatture fornitore generate automaticamente da XML ricevuti via PEC.


### 1.2. Configurazione del server PEC in ingresso

Menu: **Impostazioni → Tecnico → Email → Server di posta in entrata** (`fetchmail.server`)

Per il server dedicato allo scambio con SdI:

- attivare **E‑invoice PEC incoming** (`is_l10n_it_edi_pec`)
  - il modulo userà una gestione specializzata per questo server;
  - la casella deve contenere le mail SdI (tipicamente `@pec.fatturapa.it`).

- configurare correttamente:
  - protocollo (IMAP o POP3, preferibilmente **IMAP**);
  - host, porta, SSL;
  - utente e password PEC;
  - cartella da leggere (es. `INBOX/SdI`).

- nella scheda **PEC**:
  - **Last PEC Error Message** e **PEC error count** sono solo di diagnostica;
  - **Contacts to notify** (`e_inv_notify_partner_ids`) permette di indicare i contatti da avvisare in caso di errori ricorrenti sul server.

Nota operativa: il modulo è già configurato per **processare e marcare come lette solo le email provenienti da `@pec.fatturapa.it`**, lasciando non lette tutte le altre mail presenti sulla casella. Non è quindi obbligatorio creare regole di posta sul provider PEC. Eventuali regole lato provider (ad esempio spostare le mail SdI in una cartella `INBOX/SdI`) possono essere usate solo per organizzazione interna, purché la cartella scelta sia quella configurata nel server `fetchmail.server`.


### 1.3. Configurazione del server PEC in uscita

Menu: **Impostazioni → Tecnico → Email → Server di posta in uscita** (`ir.mail_server`)

Creare o configurare un server SMTP che utilizzi la casella PEC aziendale abilitata all’invio verso SdI:

- host e porta del provider PEC;
- credenziali PEC;
- TLS/SSL secondo impostazioni del provider.

Questo server viene selezionato nel campo **PEC SMTP Server** dell’azienda e sarà usato dal modulo per inviare i file XML allo SdI.


## 2. Fatture di vendita

### 2.1. Generazione dell’XML da Odoo

Su una fattura di vendita (o nota di credito) in stato **Confermata** (stato contabile `posted`) compare nel header il pulsante:

- **Genera XML** (`action_generate_e_invoice_xml`)

Effetti:

- viene eseguito il controllo di coerenza dati (anagrafiche, codici fiscali, indirizzi, ecc.);
- viene generato il file XML FatturaPA tramite il modulo standard `l10n_it_edi`;
- il modulo PEC:
  - assegna al file un nome del tipo `IT<IdPaese+IdCodice>_<Progressivo>.xml` con progressivo alfanumerico;
  - salva il file come `ir.attachment` collegato alla fattura (`l10n_it_edi_attachment_id`);
  - scrive un messaggio nel chatter con l’allegato XML.


### 2.2. Invio via PEC allo SdI

Una volta generato l’XML, compare il pulsante:

- **Invia PEC** (`action_l10n_it_edi_send`)

Effetti principali:

- viene recuperato l’allegato XML collegato alla fattura;
- il modulo costruisce un messaggio PEC verso l’indirizzo SdI configurato;
- il file XML viene allegato al messaggio e inviato tramite il server SMTP PEC configurato;
- lo stato EDI della fattura viene aggiornato:
  - `l10n_it_edi_state` passa a **Processing** (in elaborazione SdI);
  - `l10n_it_edi_pec_state` passa a **Sent**;
- nel chatter della fattura viene registrato un messaggio che indica l’invio via PEC allo SdI.


### 2.3. Gestione delle risposte SdI (codici RC, NS, MC, NE, DT, AT, MT)

Le notifiche SdI arrivano come email PEC con allegati XML il cui nome contiene il riferimento alla fattura originaria e un suffisso di tipo:

- `_RC_`, `_NS_`, `_MC_`, `_NE_`, `_DT_`, `_AT_`, `_MT_`.

Il modulo `l10n_it_edi_pec` legge queste PEC dal server configurato e per ogni notifica:

1. individua la fattura Odoo corrispondente a partire dal nome file;
2. aggiorna gli stati EDI della fattura;
3. allega la notifica XML al chatter della fattura.

Di seguito una panoramica dei codici principali e dell’effetto sugli stati Odoo.


#### RC – Ricevuta di consegna

- **Significato SdI**
  - Lo SdI ha preso in carico il file, lo ha ritenuto formalmente corretto e lo ha **consegnato al destinatario** (PA o privato).

- **Stato in Odoo**
  - `l10n_it_edi_state` passa a **forwarded** (SdI Accepted, Forwarded to Partner).
  - `l10n_it_edi_pec_state` passa a **delivered**.
  - Nel chatter appare un messaggio del tipo:
    - "Risposta SdI RC: stato forwarded (Id SdI: …) …" con l’allegato XML della notifica.


#### NS – Notifica di scarto

- **Significato SdI**
  - Lo SdI ha **scartato** la fattura per errori formali o incoerenze (dati mancanti, codici fiscali errati, ecc.).

- **Stato in Odoo**
  - `l10n_it_edi_state` passa a **rejected**.
  - `l10n_it_edi_pec_state` passa a **error**.
  - Nel chatter viene riportata la descrizione degli errori presenti nella notifica (campo `Descrizione`).


#### MC – Mancata consegna

- **Significato SdI**
  - Lo SdI ha accettato la fattura, ma **non è riuscito a consegnarla** al destinatario (ad esempio PEC del cliente non raggiungibile).

- **Stato in Odoo**
  - `l10n_it_edi_state` passa a **forward_failed** (SdI Accepted, Forward to Partner Failed).
  - `l10n_it_edi_pec_state` passa a **error**.
  - Il messaggio nel chatter riporta il motivo descritto nella notifica.


#### NE – Notifica esito committente

- **Significato SdI**
  - Il destinatario **PA** invia un esito ufficiale tramite SdI.
  - All’interno della notifica sono importanti i codici:
    - `Esito = EC01` → documento **accettato** dal committente;
    - `Esito = EC02` → documento **rifiutato** dal committente.

- **Stato in Odoo**
  - `Esito = EC01` → `l10n_it_edi_state = accepted_by_pa_partner`.
  - `Esito = EC02` → `l10n_it_edi_state = rejected_by_pa_partner`.
  - In assenza di questi codici specifici, lo stato rimane **processing**.
  - `l10n_it_edi_pec_state` viene aggiornato coerentemente (in genere **delivered** se l’esito è positivo, **error** se negativo).


#### DT – Decorrenza termini

- **Significato SdI**
  - Per fatture verso **PA**, il committente non ha inviato un esito entro i termini; lo SdI emette una notifica di **decorrenza termini**.

- **Stato in Odoo**
  - `l10n_it_edi_state` passa a **accepted_by_pa_partner_after_expiry`.
  - `l10n_it_edi_pec_state` passa a **delivered**.


#### AT – Attestazione di avvenuta trasmissione

- **Significato SdI**
  - Notifica che certifica l’avvenuta trasmissione della fattura allo SdI (tipicamente in particolari casi di consegna).

- **Stato in Odoo**
  - `l10n_it_edi_state` rimane in genere in **processing**, ma con l’informazione di attestazione salvata nel messaggio.
  - `l10n_it_edi_pec_state` viene lasciato coerente con l’ultimo stato di consegna (in genere **sent** o **delivered**).


#### MT – Metadati / altre notifiche tecniche

- **Significato SdI**
  - Notifiche tecniche (metadati) che completano le informazioni sul flusso di una fattura; il contenuto dipende dai casi.

- **Stato in Odoo**
  - `l10n_it_edi_state` viene mantenuto in **processing** se non è presente un esito più specifico.
  - `l10n_it_edi_pec_state` rimane coerente con la situazione attuale.
  - L’allegato XML viene comunque archiviato nel chatter della fattura.


### 2.4. Ricevute PEC di accettazione/consegna

Oltre alle notifiche SdI in XML, la casella PEC può ricevere anche le **ricevute PEC** standard del provider, con oggetto, ad esempio:

- "ACCETTAZIONE: …"
- "CONSEGNA: …"
- "MANCATA CONSEGNA: …"

Il modulo interpreta questi soggetti per aggiornare lo stato PEC tecnico della fattura:

- "ACCETTAZIONE" → `l10n_it_edi_pec_state = sent` (PEC accettata dal sistema);
- "CONSEGNA" → `l10n_it_edi_pec_state = delivered`;
- "MANCATA CONSEGNA" → `l10n_it_edi_pec_state = error`.

Anche queste ricevute vengono allegate nel chatter per avere uno storico completo degli eventi.


## 3. Fatture di acquisto (fatture fornitore)

### 3.1. Arrivo del file XML via PEC

Le fatture di acquisto ricevute tramite SdI arrivano sulla casella PEC configurata, tipicamente con allegati XML (o XML+p7m) che rispettano i tracciati FatturaPA.

Quando il server PEC configurato come **E‑invoice PEC incoming** legge nuove email:

- estrae gli allegati XML di fattura;
- li riconosce tramite le regole di naming FatturaPA;
- li passa alla logica del modulo tramite il modello `mail.thread`.


### 3.2. Creazione della fattura fornitore da XML

La funzione chiave è `create_invoice_from_attachment` sul modello `mail.thread`, che opera così:

1. **Identificazione dell’azienda**
   - A partire dal server PEC (`fetchmail_server_id`) viene individuata la `res.company` associata al flusso PEC.

2. **Creazione della fattura vuota**
   - Viene creato un nuovo `account.move` con:
     - `move_type = in_invoice`;
     - azienda = quella rilevata al punto precedente.

3. **Collegamento dell’allegato XML**
   - L’`ir.attachment` che contiene l’XML viene collegato alla fattura appena creata:
     - `res_model = account.move`;
     - `res_id = id della fattura`;
     - `res_field = l10n_it_edi_attachment_file`.

4. **Import dei dati dall’XML**
   - Viene chiamata la logica standard di import EDI (`_extend_with_attachments` / decoder `l10n_it_edi`), che:
     - legge i dati fiscali del fornitore e li abbina ad un partner esistente, oppure li propone secondo le regole di Odoo;
     - determina aliquote IVA, conti, eventuali reverse charge o ritenute sulla base dei moduli `l10n_it_edi` e relativi moduli opzionali;
     - popola le righe della fattura con descrizioni, quantità, prezzi unitari e imposte derivate dall’XML.

5. **Messaggio nel chatter**
   - Nella fattura viene registrato un messaggio che indica che il documento è stato generato da un file XML in ingresso e l’XML resta disponibile come allegato.

La fattura risultante è in genere in stato bozza e può essere verificata, eventualmente modificata e poi convalidata dall’utente.


### 3.3. Considerazioni sui decimali e sugli arrotondamenti

L’XML FatturaPA può contenere prezzi unitari con **più di due decimali**. Odoo utilizza:

- la precisione della **valuta** (rounding e numero di decimali) per gli importi monetari;
- la precisione delle **unità di misura** per le quantità.

Questo significa che, a parità di dati, piccoli scostamenti tra i totali dell’XML e quelli calcolati in Odoo possono dipendere da:

- numero di decimali configurato sulla valuta aziendale (es. 2 vs 4);
- arrotondamento delle quantità.

Eventuali adeguamenti devono quindi essere valutati a livello di configurazione della valuta e delle unità di misura, tenendo conto che tali modifiche hanno effetto sull’intero sistema contabile.


## 4. Riepilogo operativo

1. **Configurare l’azienda**
   - Attivare "Use PEC for E‑invoices".
   - Impostare SdI PEC Email, server PEC in ingresso e server SMTP PEC in uscita.

2. **Configurare il server PEC in ingresso**
   - Creare un server `fetchmail.server` dedicato, marcare "E‑invoice PEC incoming".
   - Collegarlo all’azienda tramite il campo "PEC Server for E‑invoices".

3. **Inviare fatture di vendita**
   - Postare la fattura.
   - Cliccare "Genera XML" per creare l’allegato.
   - Cliccare "Invia PEC" per inviare il file allo SdI.

4. **Monitorare le notifiche SdI**
   - Lasciare attivo il cron di lettura PEC.
   - Verificare nel chatter delle fatture le notifiche RC, NS, MC, NE, DT, AT, MT e gli stati EDI aggiornati.

5. **Gestire le fatture di acquisto**
   - Verificare che le fatture fornitore XML ricevute via SdI vengano create come bozze.
   - Controllare, completare e convalidare le fatture.
