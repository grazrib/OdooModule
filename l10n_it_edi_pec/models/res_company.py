# Copyright 2025 Your Company
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_it_edi_pec_server_id = fields.Many2one(
        comodel_name="fetchmail.server",
        string="PEC Server for E-invoices",
        help="Incoming mail server used to receive e-invoice notifications from SdI",
        domain="[('active','=',True)]",
    )
    l10n_it_edi_pec_smtp_server_id = fields.Many2one(
        comodel_name="ir.mail_server",
        string="PEC SMTP Server",
        help="SMTP server used to send e-invoices to SdI via PEC",
    )
    l10n_it_edi_use_pec = fields.Boolean(
        string="Use PEC for E-invoices",
        help="Enable PEC channel instead of proxy server for e-invoice exchange",
        default=False,
    )
    l10n_it_edi_pec_sdi_user_id = fields.Many2one(
        'res.users',
        string='SdI User for PEC',
        help="User used as creator of supplier e-bills automatically created from PEC"
    )
    l10n_it_edi_pec_sdi_email = fields.Char(
        string='SdI PEC Email',
        help="PEC email address of SdI (initially sdi01@pec.fatturapa.it)",
        default='sdi01@pec.fatturapa.it'
    )

    def _l10n_it_edi_export_check(self):
        errors = super()._l10n_it_edi_export_check()
        if not errors:
            return errors
        if self and all(self.mapped("l10n_it_edi_use_pec")):
            errors.pop("l10n_it_edi_settings_l10n_it_edi_proxy_user_id", None)
        return errors
