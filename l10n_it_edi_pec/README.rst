==============================
Italy - EDI PEC (FatturaPA)
==============================

This module adds PEC (Posta Elettronica Certificata) support for Italian e-invoicing (FatturaPA) on Odoo 18, integrating with the EDI stack.

Features
--------

- Send e-invoices via PEC to SdI using the configured `ir.mail_server`
- Receive SdI notifications via PEC (IMAP/POP) with `fetchmail.server`
- Route PEC messages and update EDI states on `account.move`
- Import incoming supplier e-invoices from PEC attachments
- Company-level configuration of PEC servers and usage flag

Configuration
-------------

Detailed setup (ITA/EN):

1. Create PEC channel in Accounting → Configuration → Settings → Electronic Invoices
2. Set PEC server for sending/receiving through SdI
3. Configure SMTP server with "E-invoice PEC SMTP" flag
4. Set SdI PEC email (initially sdi01@pec.fatturapa.it)
5. Specify user for automatic supplier e-bill creation
6. Ensure dedicated PEC email for e-invoicing only

Steps:
- Enable "Use PEC for E-invoices" in `Settings > Companies > E-invoice PEC Configuration`
- Set PEC SMTP server on `ir.mail_server` and flag "E-invoice PEC SMTP"
- Set PEC incoming server on `fetchmail.server` and flag "E-invoice PEC server"
- Set SdI PEC email address (default: sdi01@pec.fatturapa.it)
- Specify user for supplier e-bill creation in company settings
- Ensure the cron "Fetch E-invoice PEC Emails" is active

Usage
-----

- From an invoice, use EDI send; if PEC is enabled, the module routes to PEC
- Incoming SdI notifications are fetched by cron and applied to the related invoice
- Incoming e-invoices (supplier) received via PEC are imported and attached to bills

Dependencies
------------

- Odoo: `mail`, `fetchmail`
- Odoo/OCA: `l10n_it_edi`, `l10n_it_edi_extension`

Bug Tracker
-----------

Bugs are tracked on GitHub Issues for the OCA `l10n-italy` project.

Credits
-------

Authors
~~~~~~~
- Odoo Community Association (OCA)

Contributors
~~~~~~~~~~~~
- Community contributors

Maintainers
-----------

This module is maintained by the Odoo Community Association (OCA).

------------------

Italiano
========

Questo modulo aggiunge il supporto PEC (Posta Elettronica Certificata) per la Fatturazione Elettronica italiana (FatturaPA) su Odoo 18, integrandosi con lo stack EDI.

Caratteristiche
---------------

- Invio fatture elettroniche via PEC verso SdI usando il server `ir.mail_server` configurato
- Ricezione notifiche SdI via PEC (IMAP/POP) con `fetchmail.server`
- Instradamento dei messaggi PEC e aggiornamento degli stati EDI su `account.move`
- Import delle fatture passive ricevute via PEC a partire dagli allegati
- Configurazione a livello azienda dei server PEC e del flag di utilizzo

Configurazione
--------------

Configurazione dettagliata:

1. Crea canale PEC in Contabilità → Configurazione → Impostazioni → Fatture elettroniche
2. Imposta server PEC per invio/ricezione tramite SdI
3. Configura server SMTP con flag "E-invoice PEC SMTP"
4. Imposta email PEC SdI (inizialmente sdi01@pec.fatturapa.it)
5. Specifica utente per creazione automatica fatture fornitore
6. Assicurati di usare email PEC dedicata solo a fatturazione elettronica

Passaggi:
- Abilita "Usa PEC per Fatture" in `Impostazioni > Aziende > Configurazione PEC Fatture`
- Imposta il server PEC SMTP su `ir.mail_server` e spunta "E-invoice PEC SMTP"
- Imposta il server PEC in ricezione su `fetchmail.server` e spunta "E-invoice PEC server"
- Imposta indirizzo email PEC SdI (predefinito: sdi01@pec.fatturapa.it)
- Specifica utente per creazione fatture fornitore nelle impostazioni azienda
- Assicurati che il cron "Fetch E-invoice PEC Emails" sia attivo

Utilizzo
--------

- Dalla fattura attiva, usa l'invio EDI; se la PEC è abilitata, l'invio passa su PEC
- Le notifiche SdI in arrivo via PEC sono lette dal cron e applicate alla fattura
- Le fatture passive via PEC vengono importate e collegate ai documenti contabili

Dipendenze
----------

- Odoo: `mail`, `fetchmail`
- Odoo/OCA: `l10n_it_edi`, `l10n_it_edi_extension`

Bug Tracker
-----------

I bug sono tracciati su GitHub Issues del progetto OCA `l10n-italy`.

Crediti
-------

Autori
------
- Odoo Community Association (OCA)

Contributori
------------
- Contributori della community

Maintainers
-----------

Questo modulo è mantenuto dalla Odoo Community Association (OCA).