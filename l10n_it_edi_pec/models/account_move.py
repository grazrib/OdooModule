# Copyright 2025 Your Company
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import logging
import re
import secrets
import string
from email.message import EmailMessage

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

INVOICE_KEY_REGEX = r"(?:IT[a-zA-Z0-9]{11,16}|(?!IT)[A-Z]{2}[a-zA-Z0-9]{2,28})_[a-zA-Z0-9]{1,5}"
RESPONSE_MAIL_REGEX = (
    r"^" + INVOICE_KEY_REGEX + r"_[A-Z]{2}_[a-zA-Z0-9]{0,3}\.(xml|XML|Xml)(\.(p7m|P7M|P7m))?$"
)


class AccountMove(models.Model):
    _inherit = "account.move"

    def _l10n_it_edi_pec_generate_progressivo(self, size=5):
        alphabet = string.ascii_uppercase + string.digits + string.ascii_lowercase
        return "".join(secrets.choice(alphabet) for _i in range(size))

    def _l10n_it_edi_pec_extract_progressivo_from_filename(self, filename):
        filename = (filename or "").strip()
        if not filename:
            return None

        lower = filename.lower()
        if lower.endswith(".xml.p7m"):
            base = filename[:-8]
        elif lower.endswith(".p7m"):
            base = filename[:-4]
        elif lower.endswith(".xml"):
            base = filename[:-4]
        else:
            base = filename

        if "_" not in base:
            return None
        progressivo = base.split("_", 1)[1]
        if re.fullmatch(r"[A-Za-z0-9]{1,10}", progressivo or ""):
            return progressivo
        return None

    def _l10n_it_edi_pec_get_or_create_progressivo(self):
        self.ensure_one()

        existing = self._l10n_it_edi_pec_extract_progressivo_from_filename(
            self.l10n_it_edi_attachment_id.name if self.l10n_it_edi_attachment_id else ""
        )
        if existing:
            return existing

        company = self.company_id._l10n_it_get_edi_company()
        country_code = company.country_id.code
        codice = company.partner_id._l10n_it_edi_normalized_codice_fiscale()
        if not (country_code and codice):
            return self._l10n_it_edi_pec_generate_progressivo(size=5)

        prefix = f"{country_code}{codice}_"
        Attachment = self.env["ir.attachment"].sudo()
        for _try in range(20):
            progressivo = self._l10n_it_edi_pec_generate_progressivo(size=5)
            name = f"{prefix}{progressivo}.xml"
            if not Attachment.search_count([("name", "=", name), ("company_id", "=", company.id)]):
                return progressivo

        return self._l10n_it_edi_pec_generate_progressivo(size=5)

    def _l10n_it_edi_get_attachment_values(self, pdf_values=None):
        vals = super()._l10n_it_edi_get_attachment_values(pdf_values=pdf_values)
        if not self.company_id.l10n_it_edi_use_pec:
            return vals

        progressivo = self._l10n_it_edi_pec_get_or_create_progressivo()
        raw = vals.get("raw") or b""

        try:
            root = etree.fromstring(raw)
        except Exception:
            return vals

        nodes = root.xpath('//*[local-name()="ProgressivoInvio"][1]')
        if nodes:
            nodes[0].text = progressivo
        raw = etree.tostring(root, xml_declaration=True, encoding="UTF-8")

        company = self.company_id._l10n_it_get_edi_company()
        country_code = company.country_id.code
        codice = company.partner_id._l10n_it_edi_normalized_codice_fiscale()
        if country_code and codice:
            vals["name"] = f"{country_code}{codice}_{progressivo}.xml"
        vals["raw"] = raw
        return vals

    def _l10n_it_edi_pec_xml_text(self, root, node_name, default=""):
        try:
            return (root.xpath(f'string(//*[local-name()="{node_name}"][1])') or "").strip() or default
        except Exception:
            return default

    def _l10n_it_edi_pec_filename_from_attachment_xml(self, attachment):
        self.ensure_one()

        if not attachment:
            return None

        raw = attachment.raw
        if not raw and attachment.datas:
            try:
                raw = base64.b64decode(attachment.datas)
            except Exception:
                raw = b""
        if not raw:
            return None

        try:
            root = etree.fromstring(raw)
        except Exception:
            return None

        id_paese = (root.xpath('string(//*[local-name()="IdTrasmittente"]/*[local-name()="IdPaese"][1])') or "").strip()
        id_codice = (root.xpath('string(//*[local-name()="IdTrasmittente"]/*[local-name()="IdCodice"][1])') or "").strip()
        progressive = (
            root.xpath('string(//*[local-name()="DatiTrasmissione"]/*[local-name()="ProgressivoInvio"][1])')
            or ""
        ).strip()

        if not (id_paese and id_codice and progressive):
            return None

        return f"{id_paese}{id_codice}_{progressive}.xml"

    def _l10n_it_edi_pec_normalize_attachment_filename(self, attachment):
        self.ensure_one()
        if not attachment:
            return attachment

        desired = self._l10n_it_edi_pec_filename_from_attachment_xml(attachment)
        if desired and attachment.name != desired:
            attachment.sudo().write({"name": desired})
        return attachment

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
        self.check_access_rights("write")
        self.check_access_rule("write")
        if self and all(self.mapped("company_id.l10n_it_edi_use_pec")):
            sudo_self = self.sudo()
            return super(AccountMove, sudo_self).action_l10n_it_edi_send()
        return super().action_l10n_it_edi_send()

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
        self.check_access_rights("write")
        self.check_access_rule("write")
        move = self.sudo()
        if errors := move._l10n_it_edi_export_data_check():
            messages = []
            for error_key, error_data in errors.items():
                messages.append(error_data["message"].replace("\n", "<br/>"))
            move.l10n_it_edi_header = "<br/>".join(messages)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "message": move.l10n_it_edi_header,
                    "type": "danger",
                },
            }

        vals = move._l10n_it_edi_get_attachment_values(pdf_values=None)
        attachment = move.env["ir.attachment"].create(vals)
        attachment = move._l10n_it_edi_pec_normalize_attachment_filename(attachment)
        move.invalidate_recordset(
            fnames=["l10n_it_edi_attachment_id", "l10n_it_edi_attachment_file"]
        )
        move.l10n_it_edi_pec_state = "to_send"
        msg = _("XML FatturaPA generato: %s") % attachment.name
        move.message_post(body=msg, attachment_ids=[attachment.id])
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": msg,
                "type": "success",
                "next": {
                    "type": "ir.actions.client",
                    "tag": "reload",
                },
            },
        }

    def action_check_l10n_it_edi(self):
        self.ensure_one()
        self.check_access_rights("write")
        self.check_access_rule("write")
        company = self.company_id
        server = company.l10n_it_edi_pec_server_id
        if company.l10n_it_edi_use_pec and server:
            try:
                server.sudo().fetch_mail()
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
        self.check_access_rights("write")
        self.check_access_rule("write")
        attachment = self.l10n_it_edi_attachment_id
        if not attachment:
            raise UserError(_("Nessun allegato XML da cancellare"))
        attachment.sudo().unlink()
        move = self.sudo()
        move.invalidate_recordset(
            fnames=["l10n_it_edi_attachment_id", "l10n_it_edi_attachment_file"]
        )
        move.l10n_it_edi_pec_state = False
        msg = _("XML FatturaPA rimosso")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": msg,
                "type": "warning",
            },
        }

    def _l10n_it_edi_send(self, attachments_vals):
        pec_moves = self.filtered(lambda m: m._l10n_it_edi_ready_for_pec_send())
        other_moves = self - pec_moves
        results = {}

        if other_moves:
            results.update(super(AccountMove, other_moves)._l10n_it_edi_send(attachments_vals))

        for move in pec_moves:
            move.l10n_it_edi_header = False
            attachment_vals = attachments_vals[move]

            attachment = move.l10n_it_edi_attachment_id
            if not attachment:
                filename = attachment_vals["name"]
                attachment = self.env["ir.attachment"].sudo().search(
                    [
                        ("name", "=", filename),
                        ("res_model", "=", move._name),
                        ("res_id", "=", move.id),
                        ("res_field", "=", "l10n_it_edi_attachment_file"),
                    ],
                    order="id desc",
                    limit=1,
                )
            if not attachment:
                raw = attachment_vals.get("raw")
                if not raw and attachment_vals.get("datas"):
                    raw = base64.b64decode(attachment_vals["datas"])
                attachment = self.env["ir.attachment"].create(
                    {
                        "name": attachment_vals["name"],
                        "type": "binary",
                        "mimetype": "application/xml",
                        "company_id": move.company_id.id,
                        "res_model": move._name,
                        "res_id": move.id,
                        "res_field": "l10n_it_edi_attachment_file",
                        "raw": raw,
                    }
                )
                move.invalidate_recordset(
                    fnames=["l10n_it_edi_attachment_id", "l10n_it_edi_attachment_file"]
                )

            attachment = move._l10n_it_edi_pec_normalize_attachment_filename(attachment)
            filename = attachment.name

            try:
                move._send_einvoice_via_pec(attachment)
                move.l10n_it_edi_state = "processing"
                move.l10n_it_edi_transaction = False
                move.l10n_it_edi_pec_state = "sent"
                move.is_move_sent = True

                sdi_email = (
                    move.company_id.l10n_it_edi_pec_sdi_email
                    or self.env["ir.config_parameter"].sudo().get_param(
                        "l10n_it_edi_pec.sdi_email", default="sdi01@pec.fatturapa.it"
                    )
                )
                message = _(
                    "La fattura elettronica %s è stata inviata allo SdI per l'elaborazione via PEC (%s)."
                ) % (filename, sdi_email)
                header = message.replace("\n", "<br/>")
                move.sudo().message_post(body=header)
                move.l10n_it_edi_header = header
                results[filename] = {"id_transaction": "pec", "signed": False}

            except Exception as e:
                move.l10n_it_edi_state = False
                move.l10n_it_edi_transaction = False
                move.l10n_it_edi_pec_state = "error"
                err = _("Errore invio PEC per %s: %s") % (filename, str(e))
                header = err.replace("\n", "<br/>")
                move.sudo().message_post(body=header)
                move.l10n_it_edi_header = header
                results[filename] = {"error_message": err}

        return results

    def _send_einvoice_via_pec(self, attachment):
        """Send XML file via PEC to SdI"""
        self.ensure_one()

        smtp_server = self.company_id.l10n_it_edi_pec_smtp_server_id.sudo()
        if not smtp_server:
            raise UserError(_("PEC SMTP server not configured"))

        msg = EmailMessage()
        msg["From"] = smtp_server.smtp_user or self.company_id.email
        sdi_email = (
            self.company_id.l10n_it_edi_pec_sdi_email
            or self.env["ir.config_parameter"].sudo().get_param(
                "l10n_it_edi_pec.sdi_email", default="sdi01@pec.fatturapa.it"
            )
        )
        msg["To"] = sdi_email
        msg["Subject"] = attachment.name

        raw = attachment.raw
        if not raw and attachment.datas:
            raw = base64.b64decode(attachment.datas)
        msg.set_content("")
        msg.add_attachment(
            raw or b"",
            maintype="application",
            subtype="xml",
            filename=attachment.name,
        )

        self.env["ir.mail_server"].sudo().send_email(msg, mail_server_id=smtp_server.id)

        _logger.info(
            "E-invoice %s sent via PEC from company %s",
            attachment.name,
            self.company_id.name,
        )

    def _l10n_it_edi_parse_pec_notification(self, message_dict):
        """Parse PEC notification from SdI"""
        notification_tokens = ("_RC_", "_NS_", "_MC_", "_NE_", "_DT_", "_AT_", "_MT_")

        def _iter_attachments(msg):
            for att in (msg or {}).get("attachments", []) or []:
                fname = getattr(att, "fname", "")
                content = getattr(att, "content", None)
                if not fname and isinstance(att, dict):
                    fname = att.get("fname") or att.get("name") or ""
                    content = att.get("content")
                if not fname and isinstance(att, (tuple, list)) and att:
                    fname = att[0] or ""
                    content = att[1] if len(att) > 1 else None
                yield fname or "", content

        def _payload_bytes(raw):
            if raw is None:
                return b""
            if isinstance(raw, str):
                raw = raw.encode()
            elif isinstance(raw, (bytes, bytearray)):
                raw = bytes(raw)
            else:
                raw = str(raw or "").encode()

            try:
                decoded = base64.b64decode(raw, validate=True)
            except Exception:
                try:
                    decoded = base64.b64decode(raw)
                except Exception:
                    decoded = b""

            if decoded:
                head = decoded[:200].lstrip()
                if head.startswith(b"<") or b"<" in head:
                    return decoded
            return raw

        def _type_from_filename(fname):
            upper = (fname or "").upper()
            for token in notification_tokens:
                if token in upper:
                    return token.strip("_")
            return "UNKNOWN"

        notifications = []
        for fname, content in _iter_attachments(message_dict):
            upper = fname.upper()
            if not any(tok in upper for tok in notification_tokens):
                continue
            if not fname.lower().endswith((".xml", ".xml.p7m", ".p7m")):
                continue
            notifications.append((fname, content))

        if not notifications:
            return None

        # Parse notification XML
        notification_data = {}
        for fname, content in notifications:
            try:
                xml_bytes = _payload_bytes(content)
                msg_attachments = [(fname, xml_bytes)] if xml_bytes else []

                root = etree.fromstring(xml_bytes)
                notification_type = self._detect_notification_type(root)
                notification_data = {
                    "type": notification_type,
                    "filename": fname,
                    "xml": root,
                    "msg_attachments": msg_attachments,
                }

                self._process_sdi_notification(notification_data)
                
            except Exception as e:
                raw = _payload_bytes(content)
                notification_type = _type_from_filename(fname)

                self._process_sdi_notification_fallback(
                    notification_type,
                    fname,
                    msg_attachments=[(fname, raw)] if raw else [],
                    error=str(e),
                )

                notification_data = {
                    "type": notification_type,
                    "filename": fname,
                    "xml": None,
                    "msg_attachments": [(fname, raw)] if raw else [],
                }

                _logger.error(
                    "Error parsing PEC notification %s: %s",
                    fname,
                    str(e),
                )

        return notification_data

    def _process_sdi_notification_fallback(self, notification_type, filename, msg_attachments=None, error=None):
        self.ensure_one()

        state_mapping = {
            "NS": "rejected",
            "MC": "forward_failed",
            "RC": "forwarded",
            "DT": "accepted_by_pa_partner_after_expiry",
            "AT": "processing",
            "NE": "processing",
            "MT": "processing",
            "UNKNOWN": "processing",
        }
        new_state = state_mapping.get(notification_type or "UNKNOWN", "processing")
        self.l10n_it_edi_state = new_state
        if new_state in {"processing", "being_sent"}:
            self.l10n_it_edi_pec_state = "sent"
        elif new_state in {"forwarded", "accepted_by_pa_partner", "accepted_by_pa_partner_after_expiry"}:
            self.l10n_it_edi_pec_state = "delivered"
        elif new_state in {"forward_failed", "rejected", "rejected_by_pa_partner"}:
            self.l10n_it_edi_pec_state = "error"

        extra = f" - {error}" if error else ""
        msg = _("Risposta SdI %s: stato %s (file: %s)%s") % (
            notification_type,
            new_state,
            filename,
            extra,
        )
        self.message_post(body=msg, attachments=msg_attachments or [])

    def _l10n_it_edi_apply_pec_receipt(self, message_dict):
        self.ensure_one()
        subject = (message_dict or {}).get("subject") or ""
        subject_upper = subject.upper()

        if "MANCATA CONSEGNA" in subject_upper:
            pec_state = "error"
            label = _("Mancata consegna PEC")
        elif "CONSEGNA" in subject_upper:
            pec_state = "delivered"
            label = _("Consegna PEC")
        elif "ACCETTAZIONE" in subject_upper:
            pec_state = "sent"
            label = _("Accettazione PEC")
        else:
            return False

        msg_attachments = []
        seen = set()
        for att in (message_dict or {}).get("attachments", []) or []:
            fname = getattr(att, "fname", "")
            content = getattr(att, "content", None)
            if not fname and isinstance(att, dict):
                fname = att.get("fname") or att.get("name") or ""
                content = att.get("content")
            if not fname and isinstance(att, (tuple, list)) and att:
                fname = att[0] or ""
                content = att[1] if len(att) > 1 else None

            if not fname or not content:
                continue

            key = fname.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)

            if isinstance(content, str):
                content = content.encode()
            elif not isinstance(content, (bytes, bytearray)):
                content = str(content).encode()

            try:
                decoded = base64.b64decode(content, validate=True)
                if decoded:
                    content = decoded
            except Exception:
                pass

            msg_attachments.append((fname, content))

        if not self.l10n_it_edi_state:
            self.l10n_it_edi_state = "processing"
        self.l10n_it_edi_pec_state = pec_state

        self.message_post(body=_("%s: %s") % (label, subject), attachments=msg_attachments)
        return True

    def _detect_notification_type(self, root):
        """Detect type of SdI notification"""
        if self._l10n_it_edi_pec_xml_text(root, "ListaErrori"):
            return "NS"
        if self._l10n_it_edi_pec_xml_text(root, "DataOraConsegna"):
            return "RC"
        if self._l10n_it_edi_pec_xml_text(root, "EsitoCommittente"):
            return "NE"
        root_name = (getattr(root, "tag", "") or "")
        if root_name.endswith("DecorrenzaTermini") or self._l10n_it_edi_pec_xml_text(root, "DecorrenzaTermini"):
            return "DT"
        if root_name.endswith("AttestazioneTrasmissioneFattura") or self._l10n_it_edi_pec_xml_text(root, "AttestazioneTrasmissioneFattura"):
            return "AT"
        desc_text = self._l10n_it_edi_pec_xml_text(root, "Descrizione")
        if desc_text and "consegna" in desc_text.lower():
            return "MC"
        return "UNKNOWN"

    def _process_sdi_notification(self, notification_data):
        """Process SdI notification and post it to the related invoice."""
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
            esito_text = self._l10n_it_edi_pec_xml_text(root, "Esito")
            if esito_text == "EC01":
                new_state = "accepted_by_pa_partner"
            elif esito_text == "EC02":
                new_state = "rejected_by_pa_partner"
            else:
                new_state = "processing"
        else:
            new_state = state_mapping.get(notification_type, "processing")

        # Extract additional info
        id_sdi_text = self._l10n_it_edi_pec_xml_text(root, "IdentificativoSdI", default="N/A")
        
        detail = ""
        if notification_type == "NS":
            try:
                descs = [
                    (d or "").strip()
                    for d in root.xpath('//*[local-name()="Descrizione"]/text()')
                ]
            except Exception:
                descs = []
            detail = ", ".join([d for d in descs if d])
        elif notification_type == "NE":
            detail = self._l10n_it_edi_pec_xml_text(root, "Descrizione")
        elif notification_type == "MC":
            detail = self._l10n_it_edi_pec_xml_text(root, "Descrizione")

        if not detail:
            detail = self._l10n_it_edi_pec_xml_text(root, "Descrizione")
        msg = _(
            "Risposta SdI %s: stato %s (Id SdI: %s)%s"
        ) % (
            notification_type,
            new_state,
            id_sdi_text,
            (" - " + detail) if detail else "",
        )

        self.l10n_it_edi_state = new_state
        if new_state in {"processing", "being_sent"}:
            self.l10n_it_edi_pec_state = "sent"
        elif new_state in {
            "forwarded",
            "accepted_by_pa_partner",
            "accepted_by_pa_partner_after_expiry",
        }:
            self.l10n_it_edi_pec_state = "delivered"
        elif new_state in {"forward_failed", "rejected", "rejected_by_pa_partner"}:
            self.l10n_it_edi_pec_state = "error"

        msg_attachments = []
        seen = set()
        for name, content in (notification_data.get("msg_attachments") or []):
            if not name or not content:
                continue
            key = (name or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            if isinstance(content, str):
                content = content.encode()
            elif not isinstance(content, (bytes, bytearray)):
                content = str(content).encode()

            try:
                decoded = base64.b64decode(content, validate=True)
                if decoded:
                    content = decoded
            except Exception:
                pass
            msg_attachments.append((name, content))

        self.message_post(body=msg, attachments=msg_attachments)
        
        _logger.info(
            "Processed SdI notification %s for invoice %s: new state %s",
            notification_type,
            self.name,
            new_state,
        )
