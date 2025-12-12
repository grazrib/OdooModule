# Copyright 2025 Your Company
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Italy - EDI PEC (FatturaPA)",
    "version": "18.0.1.0.0",
    "category": "Accounting/Localizations/EDI",
    "summary": "Send and receive Italian e-invoices (FatturaPA) via PEC",
    "author": "Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/l10n-italy",
    "license": "AGPL-3",
    "depends": [
        "mail",
        "account",
        "l10n_it_edi",
        "l10n_it_edi_extension",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/config_parameter.xml",
        "views/company_view.xml",
        "views/ir_mail_server.xml",
        "views/account_move_view.xml",
    ],
    "installable": True,
    "auto_install": False,
    "external_dependencies": {
        "python": [],
    },
}
