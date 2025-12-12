# Copyright 2025 Your Company
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import logging
import re
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

RESPONSE_MAIL_REGEX = (
    "[A-Z]{2}[a-zA-Z0-9]{11,16}_[a-zA-Z0-9]{,5}_[A-Z]{2}_[a-zA-Z0-9]{,3}"
)


class AccountMove(models.Model):
    _inherit = "account.move"

    l10n_it_edi_pec_state = fields.Selection(
        selection=[
            ("to_send", "To Send via PEC"),
            ("sent", "Sent via PEC"),
            ("delivered", "Delivered"),
            ("error", "Error"),
        ],
        string="PEC State",
        copy=False,
        help="Technical field to track PEC sending state",
    )
    l10n_it_edi_pec_force_state = fields.Boolean(
        string="Force PEC State",
        help="Allow to force the supplier e-bill PEC export state",
        default=False,
    )

    def _l10n_it_edi_ready_for_pec_send(self):
        """Check if invoice is ready to be sent via PEC instead of proxy"""
        self.ensure_one()
        return (
            self.company_id.l10n_it_edi_use_pec
            and self.company_id.l10n_it_edi_pec_smtp_server_id
            and self._l10n_it_edi_ready_for_xml_export()
        )

    def action_l10n_it_edi_send(self):
        """Override to use PEC channel if configured"""
        self.ensure_one()
        
        # Check if we should use PEC
        if self._l10n_it_edi_ready_for_pec_send():
            return self._l10n_it_edi_send_via_pec()
        
        # Otherwise use standard proxy method
        return super().action_l10n_it_edi_send()

    def _l10n_it_edi_send_via_pec(self):
        self.ensure_one()
        if errors := self._l10n_it_edi_export_data_check():
            messages = []
            for error_key, error_data in errors.items():
                messages.append(error_data["message"].replace("\n", "<br/>"))
            self.l10n_it_edi_header = "<br/>".join(messages)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {"message": self.l10n_it_edi_header, "type": "danger"},
            }

        attachment = self.l10n_it_edi_attachment_id
        if not attachment:
            raise UserError(_("Genera prima l'XML FatturaPA"))

        try:
            self._send_einvoice_via_pec(attachment)
            self.l10n_it_edi_state = "being_sent"
            self.l10n_it_edi_pec_state = "sent"
            self.is_move_sent = True
            message = _(
                "E-invoice %s sent via PEC to %s"
            ) % (attachment.name, self.company_id.l10n_it_edi_pec_sdi_email or self.env["ir.config_parameter"].sudo().get_param("l10n_it_edi_pec.sdi_email", default="sdi01@pec.fatturapa.it"))
            self.l10n_it_edi_header = message.replace("\n", "<br/>")
            self.message_post(body=message)
        except Exception as e:
            self.l10n_it_edi_pec_state = "error"
            error_msg = _(
                "Error sending e-invoice via PEC: %s"
            ) % str(e)
            self.l10n_it_edi_header = error_msg.replace("\n", "<br/>")
            self.message_post(body=error_msg)
            raise UserError(error_msg) from e

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": message,
                "type": "success",
            },
        }

    def action_l10n_it_edi_export(self):
        self.ensure_one()
        if self.company_id.l10n_it_edi_use_pec:
            return self.action_generate_e_invoice_xml()
        return super().action_l10n_it_edi_export()

    def fields_view_get(self, view_id=None, view_type="form", toolbar=False, submenu=False):
        res = super().fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)
        if view_type == "form" and res.get("arch"):
            try:
                doc = etree.fromstring(res["arch"])
                modified = False
                for btn in doc.xpath("//header//button[@name='action_l10n_it_edi_export']"):
                    parent = btn.getparent()
                    if parent is not None:
                        parent.remove(btn)
                        modified = True
                for btn in doc.xpath("//header//button[@name]"):
                    name = btn.get("name", "")
                    lname = name.lower()
                    if ("edi" in lname) and ("check" in lname or "status" in lname or "update" in lname):
                        parent = btn.getparent()
                        if parent is not None:
                            parent.remove(btn)
                            modified = True
                if modified:
                    res["arch"] = etree.tostring(doc, encoding="unicode")
            except Exception:
                pass
        return res

    def action_generate_e_invoice_xml(self):
        self.ensure_one()
        if errors := self._l10n_it_edi_export_data_check():
            messages = []
            for error_key, error_data in errors.items():
                messages.append(error_data["message"].replace("\n", "<br/>"))
            self.l10n_it_edi_header = "<br/>".join(messages)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "message": self.l10n_it_edi_header,
                    "type": "danger",
                },
            }

        vals = self._l10n_it_edi_get_attachment_values(pdf_values=None)
        attachment = self.env["ir.attachment"].create(vals)
        self.invalidate_recordset(
            fnames=["l10n_it_edi_attachment_id", "l10n_it_edi_attachment_file"]
        )
        self.l10n_it_edi_pec_state = "to_send"
        msg = _("XML FatturaPA generato: %s") % attachment.name
        self.message_post(body=msg)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": msg,
                "type": "success",
            },
        }

    def action_check_l10n_it_edi(self):
        self.ensure_one()
        company = self.company_id
        server = company.l10n_it_edi_pec_server_id
        if company.l10n_it_edi_use_pec and server:
            try:
                server.fetch_mail()
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "message": _("Verifica stato PEC completata"),
                        "type": "success",
                    },
                }
            except Exception as e:
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "message": _("Errore verifica stato PEC: %s") % str(e),
                        "type": "danger",
                    },
                }
        return super().action_check_l10n_it_edi()

    def action_download_e_invoice_attachment(self):
        self.ensure_one()
        attachment = self.l10n_it_edi_attachment_id
        if not attachment:
            raise UserError(_("Nessun allegato XML disponibile"))
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "new",
        }

    def action_open_e_invoice_attachment(self):
        self.ensure_one()
        attachment = self.l10n_it_edi_attachment_id
        if not attachment:
            raise UserError(_("Nessun allegato XML disponibile"))
        return {
            "type": "ir.actions.act_window",
            "name": "Allegato FatturaPA",
            "res_model": "ir.attachment",
            "view_mode": "form",
            "res_id": attachment.id,
            "target": "current",
        }

    def action_delete_e_invoice_attachment(self):
        self.ensure_one()
        attachment = self.l10n_it_edi_attachment_id
        if not attachment:
            raise UserError(_("Nessun allegato XML da cancellare"))
        attachment.unlink()
        self.invalidate_recordset(
            fnames=["l10n_it_edi_attachment_id", "l10n_it_edi_attachment_file"]
        )
        self.l10n_it_edi_pec_state = False
        msg = _("XML FatturaPA rimosso")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": msg,
                "type": "warning",
            },
        }

    def _l10n_it_edi_upload(self, attachments_vals):
        self.ensure_one()
        if self._l10n_it_edi_ready_for_pec_send():
            if errors := self._l10n_it_edi_export_data_check():
                messages = []
                for error_key, error_data in errors.items():
                    messages.append(error_data["message"].replace("\n", "<br/>"))
                self.l10n_it_edi_header = "<br/>".join(messages)
                return {"status": "error", "message": self.l10n_it_edi_header}

            results = {}
            xml_items = attachments_vals or []
            if not xml_items:
                xml_items = [self._l10n_it_edi_get_attachment_values(pdf_values=None)]

            for vals in xml_items:
                input_name = vals.get("name") or vals.get("filename") or vals.get("attachment_name")
                xml_name = input_name
                datas = vals.get("datas") or vals.get("content") or vals.get("data")
                xml_vals = {
                    "name": xml_name,
                    "datas": datas,
                    "mimetype": (vals.get("mimetype") or "application/xml"),
                    "res_model": self._name,
                    "res_id": self.id,
                }

                attachment = self.env["ir.attachment"].create(xml_vals)
                self.invalidate_recordset(
                    fnames=["l10n_it_edi_attachment_id", "l10n_it_edi_attachment_file"]
                )
                pass

                try:
                    self._send_einvoice_via_pec(attachment)
                    self.l10n_it_edi_state = "being_sent"
                    self.l10n_it_edi_pec_state = "sent"
                    self.is_move_sent = True
                    sdi_email = (
                        self.company_id.l10n_it_edi_pec_sdi_email
                        or self.env["ir.config_parameter"].sudo().get_param(
                            "l10n_it_edi_pec.sdi_email", default="sdi01@pec.fatturapa.it"
                        )
                    )
                    smtp_server = self.company_id.l10n_it_edi_pec_smtp_server_id
                    log_msg = _(
                        "Inviata FatturaPA via PEC: %s → SdI %s, server %s"
                    ) % (xml_name, sdi_email, (smtp_server.name or smtp_server.smtp_host or "SMTP"))
                    results[xml_name] = {"status": "success", "attachment_id": attachment.id}
                except Exception as e:
                    self.l10n_it_edi_pec_state = "error"
                    err = str(e)
                    self.l10n_it_edi_header = err.replace("\n", "<br/>")
                    results[xml_name] = {"status": "error", "message": err}

            return results

        parent = super(AccountMove, self)
        return (
            parent._l10n_it_edi_upload(attachments_vals)
            if hasattr(parent, "_l10n_it_edi_upload")
            else {"status": "noop"}
        )

    def _send_einvoice_via_pec(self, attachment):
        """Send XML file via PEC to SdI"""
        self.ensure_one()
        
        smtp_server = self.company_id.l10n_it_edi_pec_smtp_server_id
        if not smtp_server:
            raise UserError(_("PEC SMTP server not configured"))

        # Create email
        msg = MIMEMultipart()
        msg["From"] = smtp_server.smtp_user or self.company_id.email
        sdi_email = (
            self.company_id.l10n_it_edi_pec_sdi_email
            or self.env["ir.config_parameter"].sudo().get_param(
                "l10n_it_edi_pec.sdi_email", default="sdi01@pec.fatturapa.it"
            )
        )
        msg["To"] = sdi_email
        msg["Subject"] = attachment.name

        # Attach XML file
        part = MIMEBase("application", "xml")
        part.set_payload(base64.b64decode(attachment.datas))
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f"attachment; filename={attachment.name}",
        )
        msg.attach(part)

        # Send via SMTP
        smtp_server.send_email(msg)
        
        _logger.info(
            "E-invoice %s sent via PEC from company %s",
            attachment.name,
            self.company_id.name,
        )

    def _l10n_it_edi_parse_pec_notification(self, message_dict):
        """Parse PEC notification from SdI"""
        regex = re.compile(RESPONSE_MAIL_REGEX)
        notifications = [
            x for x in message_dict.get("attachments", []) 
            if regex.match(x.fname)
        ]

        if not notifications:
            return None

        # Parse notification XML
        notification_data = {}
        for notification in notifications:
            try:
                xml_content = base64.b64decode(notification.content)
                root = etree.fromstring(xml_content)
                
                # Extract notification type and data
                notification_type = self._detect_notification_type(root)
                notification_data = {
                    "type": notification_type,
                    "filename": notification.fname,
                    "xml": root,
                }
                
                # Update invoice state based on notification
                self._process_sdi_notification(notification_data)
                
            except Exception as e:
                _logger.error(
                    "Error parsing PEC notification %s: %s",
                    notification.fname,
                    str(e),
                )

        return notification_data

    def _detect_notification_type(self, root):
        """Detect type of SdI notification"""
        # NS = Notifica Scarto (rejection)
        if root.find(".//ListaErrori") is not None:
            return "NS"
        # MC = Mancata Consegna (failed delivery)
        if root.find(".//Descrizione") is not None:
            desc_text = root.find(".//Descrizione").text or ""
            if "consegna" in desc_text.lower():
                return "MC"
        # RC = Ricevuta Consegna (delivery receipt)
        if root.find(".//DataOraConsegna") is not None:
            return "RC"
        # NE = Notifica Esito (outcome notification)
        if root.find(".//EsitoCommittente") is not None:
            return "NE"
        # DT = Decorrenza Termini (deadline)
        if root.tag.endswith("DecorrenzaTermini"):
            return "DT"
        # AT = Attestazione Trasmissione (transmission attestation)
        if root.tag.endswith("AttestazioneTrasmissioneFattura"):
            return "AT"
        return "UNKNOWN"

    def _process_sdi_notification(self, notification_data):
        """Process SdI notification and update invoice state"""
        self.ensure_one()
        
        notification_type = notification_data.get("type")
        root = notification_data.get("xml")
        
        state_mapping = {
            "NS": "rejected",
            "MC": "forward_failed",
            "RC": "forwarded",
            "DT": "accepted_by_pa_partner_after_expiry",
            "AT": "processing",
        }
        
        if notification_type == "NE":
            # Check outcome
            esito = root.find(".//EsitoCommittente/Esito")
            if esito is not None:
                if esito.text == "EC01":
                    new_state = "accepted_by_pa_partner"
                elif esito.text == "EC02":
                    new_state = "rejected_by_pa_partner"
                else:
                    new_state = "processing"
            else:
                new_state = "processing"
        else:
            new_state = state_mapping.get(notification_type, "processing")

        # Extract additional info
        id_sdi = root.find(".//IdentificativoSdI")
        id_sdi_text = id_sdi.text if id_sdi is not None else "N/A"
        
        # Update state
        self.l10n_it_edi_state = new_state
        self.l10n_it_edi_pec_state = (
            "delivered"
            if new_state in ["forwarded", "accepted_by_pa_partner"]
            else "error"
        )
        
        detail = ""
        if notification_type == "NS":
            descs = [x.text or "" for x in root.findall(".//Descrizione")] or [""]
            detail = ", ".join([d for d in descs if d])
        elif notification_type == "NE":
            d = root.find(".//EsitoCommittente/Descrizione")
            detail = (d.text or "") if d is not None else ""
        elif notification_type == "MC":
            d = root.find(".//Descrizione")
            detail = (d.text or "") if d is not None else ""
        msg = _(
            "Risposta SdI %s: stato %s (Id SdI: %s)%s"
        ) % (
            notification_type,
            new_state,
            id_sdi_text,
            (" - " + detail) if detail else "",
        )
        self.message_post(body=msg)
        
        _logger.info(
            "Processed SdI notification %s for invoice %s: new state %s",
            notification_type,
            self.name,
            new_state,
        )
