# Copyright 2025 Your Company
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart

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

    def send_email(self, msg: MIMEMultipart):
        self.ensure_one()
        if not self.smtp_host or not self.smtp_port:
            raise ValueError("SMTP host/port not configured")

        use_ssl = self.smtp_encryption == "ssl"
        use_starttls = self.smtp_encryption == "starttls"

        if use_ssl:
            context = ssl.create_default_context()
            server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context)
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)

        try:
            server.ehlo()
            if use_starttls:
                context = ssl.create_default_context()
                server.starttls(context=context)
                server.ehlo()
            if self.smtp_user:
                server.login(self.smtp_user, self.smtp_pass or "")
            server.sendmail(msg["From"], [msg["To"]], msg.as_string())
        finally:
            try:
                server.quit()
            except Exception:
                server.close()
