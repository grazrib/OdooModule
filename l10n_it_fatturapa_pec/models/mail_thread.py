# Copyright 2025 Your Company
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import logging
import re

from lxml import etree

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

FATTURAPA_IN_REGEX = (
    "^(IT[a-zA-Z0-9]{11,16}|"
    "(?!IT)[A-Z]{2}[a-zA-Z0-9]{2,28})"
    "_[a-zA-Z0-9]{1,5}"
    "\\.(xml|XML|Xml|zip|ZIP|Zip|p7m|P7M|P7m)"
    "(\\.(p7m|P7M|P7m))?$"
)
RESPONSE_MAIL_REGEX = (
    "(IT[a-zA-Z0-9]{11,16}|"
    "(?!IT)[A-Z]{2}[a-zA-Z0-9]{2,28})"
    "_[a-zA-Z0-9]{1,5}"
    "_[A-Z]{2}_[a-zA-Z0-9]{,3}"
)

fatturapa_regex = re.compile(FATTURAPA_IN_REGEX)
response_regex = re.compile(RESPONSE_MAIL_REGEX)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def clean_message_dict(self, message_dict):
        """Clean message dict from unnecessary fields"""
        fields_to_clean = [
            "attachments",
            "cc",
            "from",
            "to",
            "recipients",
            "references",
            "in_reply_to",
            "bounced_email",
            "bounced_partner",
            "bounced_msg_id",
            "bounced_message",
        ]
        for field in fields_to_clean:
            message_dict.pop(field, None)

    @api.model
    def message_route(
        self, message, message_dict, model=None, thread_id=None, custom_values=None
    ):
        """Route PEC messages to appropriate handlers"""
        
        # Check if this is a PEC message from SdI
        if any(
            "@pec.fatturapa.it" in x
            for x in [
                message.get("Reply-To", ""),
                message.get("From", ""),
                message.get("Return-Path", ""),
            ]
        ):
            _logger.info(
                "Processing FatturaPA PEC with Message-Id: %s",
                message.get("Message-Id"),
            )
            
            fatturapa_attachments = [
                x
                for x in message_dict.get("attachments", [])
                if fatturapa_regex.match(x.fname)
            ]
            response_attachments = [
                x
                for x in message_dict.get("attachments", [])
                if response_regex.match(x.fname)
            ]
            
            # Incoming invoice with notification
            if response_attachments and fatturapa_attachments:
                return self.manage_pec_fe_attachments(
                    message, message_dict, response_attachments, fatturapa_attachments
                )
            # SDI notification only
            else:
                return self.manage_pec_sdi_notification(message, message_dict)

        # Check if fetchmail context is set and server is PEC
        elif self._context.get("fetchmail_server_id", False):
            fetchmail_server = self.env["fetchmail.server"].browse(
                self._context["fetchmail_server_id"]
            )
            if fetchmail_server.is_l10n_it_edi_pec:
                # Try to find related invoice
                invoice = self.find_invoice_by_subject(message_dict["subject"])
                if invoice:
                    return self.manage_pec_sdi_response(invoice, message_dict)
                
                # Message not related to e-invoice
                raise UserError(
                    _(
                        'PEC message "%(subject)s" has been read '
                        "but not processed, as not related to an "
                        "e-invoice.\n"
                        "Please check PEC mailbox %(fetchmail_name)s."
                    )
                    % {
                        "subject": message_dict["subject"],
                        "fetchmail_name": fetchmail_server.name,
                    }
                )
        
        return super().message_route(
            message,
            message_dict,
            model=model,
            thread_id=thread_id,
            custom_values=custom_values,
        )

    def manage_pec_sdi_response(self, invoice, message_dict):
        """Handle PEC response related to sent invoice"""
        message_dict["model"] = "account.move"
        message_dict["res_id"] = invoice.id
        self.clean_message_dict(message_dict)
        
        # Process notification
        invoice._l10n_it_edi_parse_pec_notification(message_dict)
        
        return []
        
        return []

    def manage_pec_sdi_notification(self, message, message_dict):
        """Handle SDI notification via PEC"""
        # Find related invoice by filename in notification
        for attachment in message_dict.get("attachments", []):
            if response_regex.match(attachment.fname):
                # Extract invoice filename from notification filename
                # Format: IT01234567890_00001_MT_001.xml
                parts = attachment.fname.split("_")
                if len(parts) >= 2:
                    invoice_filename = f"{parts[0]}_{parts[1]}.xml"
                    invoice = self.env["account.move"].search([
                        ("l10n_it_edi_attachment_id.name", "=", invoice_filename)
                    ], limit=1)
                    
                    if invoice:
                        return self.manage_pec_sdi_response(invoice, message_dict)
        
        # No invoice found, just log
        _logger.info(
            "Routing FatturaPA PEC notification with Message-Id: %s",
            message.get("Message-Id"),
        )
        return []

    def manage_pec_fe_attachments(
        self, message, message_dict, response_attachments, fatturapa_attachments
    ):
        """Handle incoming invoice via PEC"""
        if len(response_attachments) > 1:
            _logger.info("More than 1 notification found in incoming invoice mail")

        message_dict["model"] = "account.move"
        message_dict["record_name"] = message_dict["subject"]
        message_dict["res_id"] = 0
        
        # Process attachments
        attachment_ids = self._message_post_process_attachments(
            message_dict["attachments"], [], message_dict
        ).get("attachment_ids", [])
        
        # Create invoice from XML
        for attachment in self.env["ir.attachment"].browse(
            [att_id for m, att_id in attachment_ids]
        ):
            if fatturapa_regex.match(attachment.name):
                self.create_invoice_from_attachment(attachment, message_dict)
        
        message_dict["attachment_ids"] = attachment_ids
        self.clean_message_dict(message_dict)
        
        # Remove model/res_id to avoid attaching to wrong record
        del message_dict["model"]
        del message_dict["res_id"]
        
        _logger.info(
            "Processed PEC incoming attachments (no chatter message created)"
        )
        
        return []

    def find_invoice_by_subject(self, subject):
        """Find invoice by PEC subject"""
        # PEC subjects: "CONSEGNA: filename.xml" or "ACCETTAZIONE: filename.xml"
        for prefix in ["CONSEGNA: ", "ACCETTAZIONE: "]:
            if prefix in subject:
                filename = subject.replace(prefix, "")
                invoice = self.env["account.move"].search([
                    ("l10n_it_edi_attachment_id.name", "=", filename)
                ], limit=1)
                if invoice:
                    return invoice
        return self.env["account.move"]

    def create_invoice_from_attachment(self, attachment, message_dict=None):
        """Create invoice from incoming e-invoice XML"""
        fetchmail_server_id = self.env.context.get("fetchmail_server_id")
        received_date = False
        if message_dict and "date" in message_dict:
            received_date = message_dict["date"]
        
        company_id = False
        if fetchmail_server_id:
            # Find company from fetchmail server
            company = self.env["res.company"].search([
                ("l10n_it_edi_pec_server_id", "=", fetchmail_server_id)
            ], limit=1)
            if company:
                company_id = company.id

        # Create empty move
        move = self.env["account.move"].with_company(company_id or self.env.company.id).create({
            "move_type": "in_invoice",
        })
        
        # Attach file
        attachment.write({
            "res_model": "account.move",
            "res_id": move.id,
            "res_field": "l10n_it_edi_attachment_file",
        })
        
        # Import from XML
        try:
            move_ctx = move.with_context(
                account_predictive_bills_disable_prediction=True,
                no_new_invoice=True,
            )
            move_ctx._extend_with_attachments(attachment, new=True)
            move.message_post(body=_("Fattura fornitore generata da file in ingresso: %s") % attachment.name)
            
        except Exception as e:
            error_msg = _("Error importing e-invoice: %s") % str(e)
            _logger.error("Error importing e-invoice %s: %s", attachment.name, str(e))
            raise
        
        return move
