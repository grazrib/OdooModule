from odoo import fields, models


class IrMailServer(models.Model):
    _inherit = "ir.mail_server"

    is_l10n_it_edi_pec = fields.Boolean(
        string="E-invoice PEC SMTP",
        help="Enable this if the server is used to send e-invoices via PEC",
    )

    pec_in_protocol = fields.Selection(
        selection=[("imap", "IMAP"), ("pop3", "POP3")],
        string="PEC incoming protocol",
        default="imap",
    )
    pec_in_host = fields.Char(string="PEC incoming server")
    pec_in_port = fields.Integer(string="PEC incoming port")
    pec_in_encryption = fields.Selection(
        selection=[("ssl", "SSL/TLS"), ("starttls", "STARTTLS"), ("none", "None")],
        string="PEC incoming encryption",
    )
    pec_in_use_smtp_credentials = fields.Boolean(
        string="Use SMTP credentials for incoming",
        default=True,
    )
    pec_in_user = fields.Char(string="PEC incoming user")
    pec_in_pass = fields.Char(string="PEC incoming password")
