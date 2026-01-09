# Copyright 2025 Your Company
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from email import policy
from email.parser import BytesParser

from odoo import _, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)
MAX_POP_MESSAGES = 50


class FetchmailServer(models.Model):
    _inherit = "fetchmail.server"


    def _default_e_inv_notify_partner_ids(self):
        return [(6, 0, [self.env.user.partner_id.id])]

    is_l10n_it_edi_pec = fields.Boolean(
        string="E-invoice PEC incoming",
        help="Enable if this incoming server is used for SdI PEC notifications",
    )
    last_pec_error_message = fields.Text("Last PEC Error Message", readonly=True)
    pec_error_count = fields.Integer("PEC error count", readonly=True)
    e_inv_notify_partner_ids = fields.Many2many(
        "res.partner",
        string="Contacts to notify",
        help="Contacts to notify when PEC message can't be processed",
        domain=[("email", "!=", False)],
        default=_default_e_inv_notify_partner_ids,
    )

    def fetch_mail_server_type_imap(
        self, server, MailThread, error_messages, **additional_context
    ):
        """Fetch emails using IMAP protocol for PEC servers"""
        imap_server = None
        try:
            # Create IMAP connection using server configuration
            import imaplib
            import ssl
            
            host = server.server
            port = server.port or 993
            if server.is_ssl:
                imap_server = imaplib.IMAP4_SSL(host, port)
            else:
                imap_server = imaplib.IMAP4(host, server.port or 143)
            
            # Login to IMAP server
            if server.user:
                imap_server.login(server.user, server.password or "")
            
            imap_server.select()
            result, data = imap_server.search(None, "(UNSEEN)")
            
            for num in data[0].split():
                result, data = imap_server.fetch(num, "(RFC822)")
                raw_message = data[0][1] if data and data[0] else b""

                is_sdi_pec = False
                try:
                    eml = BytesParser(policy=policy.default).parsebytes(raw_message or b"")
                    headers_to_check = [
                        eml.get("Reply-To") or "",
                        eml.get("From") or "",
                        eml.get("Return-Path") or "",
                    ]
                    is_sdi_pec = any("@pec.fatturapa.it" in h for h in headers_to_check)
                except Exception:
                    is_sdi_pec = False

                if not is_sdi_pec:
                    continue

                try:
                    MailThread.with_context(**additional_context).message_process(
                        'mail.thread',
                        raw_message,
                        save_original=True,
                        strip_attachments=False,
                    )
                    server.last_pec_error_message = ""
                except Exception as e:
                    server.manage_pec_failure(e, error_messages)
                    continue

                imap_server.store(num, "+FLAGS", "\\Seen")
                self.env.cr.commit()
                
        except Exception as e:
            server.manage_pec_failure(e, error_messages)
        finally:
            if imap_server:
                try:
                    imap_server.close()
                    imap_server.logout()
                except:
                    pass

    def fetch_mail_server_type_pop(
        self, server, MailThread, error_messages, **additional_context
    ):
        """Fetch emails using POP3 protocol for PEC servers"""
        pop_server = None
        try:
            import poplib
            
            # Create POP3 connection using server configuration
            host = server.server
            port = server.port or 995
            if server.is_ssl:
                pop_server = poplib.POP3_SSL(host, port)
            else:
                pop_server = poplib.POP3(host, server.port or 110)
            
            # Login to POP3 server
            if server.user:
                pop_server.user(server.user)
                pop_server.pass_(server.password or "")
            
            while True:
                (num_messages, total_size) = pop_server.stat()
                pop_server.list()
                
                for num in range(1, min(MAX_POP_MESSAGES, num_messages) + 1):
                    (header, messages, octets) = pop_server.retr(num)
                    message = b"\n".join(messages)
                    try:
                        MailThread.with_context(**additional_context).message_process(
                            'mail.thread',  # Use generic model
                            message,
                            save_original=True,
                            strip_attachments=False,
                        )
                        pop_server.dele(num)
                        server.last_pec_error_message = ""
                    except Exception as e:
                        server.manage_pec_failure(e, error_messages)
                        continue
                    self.env.cr.commit()
                    
                if num_messages < MAX_POP_MESSAGES:
                    break
                    
        except Exception as e:
            server.manage_pec_failure(e, error_messages)
        finally:
            if pop_server:
                try:
                    pop_server.quit()
                except:
                    pass

    def fetch_mail(self, raise_exception=True):
        """Override to handle PEC email fetching for e-invoices"""
        for server in self:
            if not server.is_l10n_it_edi_pec:
                # For non-PEC servers, use standard behavior
                super(FetchmailServer, server).fetch_mail(
                    raise_exception=raise_exception
                )
                continue

            # PEC server handling
            try:
                additional_context = {"fetchmail_cron_running": True}
                server_ctx = server.with_context(**additional_context)
                server_sudo = server_ctx.sudo()
                MailThread = server_sudo.env["mail.thread"]
                _logger.debug(
                    "start checking for new e-invoices on PEC server %s",
                    server_ctx.name,
                )
                additional_context["fetchmail_server_id"] = server_ctx.id
                additional_context["server_type"] = server_ctx.server_type or "imap"
                error_messages = list()

                if (server_sudo.server_type or "imap") == "imap":
                    server_sudo.fetch_mail_server_type_imap(
                        server_sudo, MailThread, error_messages, **additional_context
                    )
                else:
                    server_sudo.fetch_mail_server_type_pop(
                        server_sudo, MailThread, error_messages, **additional_context
                    )

                if error_messages:
                    server_sudo.notify_or_log(error_messages)
                    server_sudo.pec_error_count += 1
                    max_retry = self.env["ir.config_parameter"].sudo().get_param(
                        "fetchmail.pec.max.retry", default="3"
                    )
                    if server_sudo.pec_error_count > int(max_retry):
                        server_sudo.active = False
                        server_sudo.notify_about_server_reset()
                else:
                    server_sudo.pec_error_count = 0
            except Exception as e:
                if raise_exception:
                    raise ValidationError(
                        _(
                            "Couldn't get your emails. Check out the error message below for more info:\n%s",
                            e,
                        )
                    ) from e
                _logger.warning(
                    "General failure when trying to fetch mail from %s server %s.",
                    server.server_type,
                    server.name,
                    exc_info=True,
                )

        return True

    def manage_pec_failure(self, exception, error_messages):
        self.ensure_one()
        _logger.warning(
            "Failure when fetching emails using %s server %s.",
            self.env.context.get("server_type", "imap"),
            self.name,
            exc_info=True,
        )
        exception_msg = str(exception)
        odoo_exc_string = getattr(exception, "name", None)
        if odoo_exc_string:
            exception_msg = odoo_exc_string
        self.last_pec_error_message = exception_msg
        error_messages.append(exception_msg)
        return True

    def notify_about_server_reset(self):
        self.ensure_one()
        self.notify_or_log(
            _(
                "PEC server %(name)s has been reset. "
                "Last error message is '%(error_message)s'"
            )
            % {"name": self.name, "error_message": self.last_pec_error_message}
        )

    def notify_or_log(self, message):
        self.ensure_one()
        if isinstance(message, list):
            message = "<br/>".join(message)
        if self.e_inv_notify_partner_ids:
            self.env["mail.mail"].create(
                {
                    "subject": _("Fetchmail PEC server [%s] error") % self.name,
                    "body_html": message,
                    "recipient_ids": [(6, 0, self.e_inv_notify_partner_ids.ids)],
                }
            ).send()
            _logger.debug(
                "Notifying partners %s about PEC server %s error",
                self.e_inv_notify_partner_ids.ids,
                self.name,
            )
        else:
            _logger.error("Can't notify anyone about PEC server %s error", self.name)
