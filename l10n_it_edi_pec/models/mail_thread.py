# Copyright 2025 Your Company
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from email import policy
from email.parser import BytesParser
import logging
import re
from types import SimpleNamespace

from lxml import etree

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

INVOICE_KEY_REGEX = (
    r"(IT[a-zA-Z0-9]{11,16}|(?!IT)[A-Z]{2}[a-zA-Z0-9]{2,28})"
    r"_(?P<progressive>[a-zA-Z0-9]{1,5})"
)

FATTURAPA_IN_REGEX = (
    rf"^{INVOICE_KEY_REGEX}"
    r"\.(xml|XML|Xml|zip|ZIP|Zip|p7m|P7M|P7m)"
    r"(\.(p7m|P7M|P7m))?$"
)
RESPONSE_MAIL_REGEX = (
    rf"^{INVOICE_KEY_REGEX}_[A-Z]{{2}}_[a-zA-Z0-9]{{0,3}}"
    r"\.(xml|XML|Xml)"
    r"(\.(p7m|P7M|P7m))?$"
)

fatturapa_regex = re.compile(FATTURAPA_IN_REGEX)
response_regex = re.compile(RESPONSE_MAIL_REGEX)
invoice_filename_search_regex = re.compile(
    r"(?P<filename>" + INVOICE_KEY_REGEX + r"\.(xml|XML|Xml)(\.(p7m|P7M|P7m))?)"
)


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _coerce_bytes(self, value):
        if not value:
            return b""
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if isinstance(value, str):
            return value.encode()
        return str(value).encode()

    def _decode_bytes_maybe_base64(self, value):
        raw = self._coerce_bytes(value)
        head = raw[:200].lstrip()
        if not head:
            return b""

        if b"<" in head or head.startswith(b"From:") or head.startswith(b"Received:"):
            return raw

        try:
            decoded = base64.b64decode(raw, validate=True)
        except Exception:
            try:
                decoded = base64.b64decode(raw)
            except Exception:
                return raw

        decoded_head = decoded[:200].lstrip()
        if b"<" in decoded_head or decoded_head.startswith(b"From:") or decoded_head.startswith(b"Received:"):
            return decoded
        return raw

    def _extract_pec_attachments_from_eml_bytes(self, eml_bytes, depth=0):
        if not eml_bytes:
            return "", []

        try:
            eml = BytesParser(policy=policy.default).parsebytes(eml_bytes)
        except Exception:
            return "", []

        subject = (eml.get("Subject") or "").strip()
        extracted = []

        for part in eml.walk():
            filename = part.get_filename() or ""
            content_type = (part.get_content_type() or "").lower()

            if content_type == "message/rfc822":
                if depth >= 2:
                    continue
                payload = part.get_payload()
                if isinstance(payload, list) and payload:
                    try:
                        nested_bytes = payload[0].as_bytes()
                    except Exception:
                        nested_bytes = b""
                    nested_subject, nested_attachments = self._extract_pec_attachments_from_eml_bytes(
                        nested_bytes, depth=depth + 1
                    )
                    if nested_subject and not subject:
                        subject = nested_subject
                    extracted.extend(nested_attachments)
                continue

            if part.is_multipart():
                continue

            if not filename:
                continue

            payload_bytes = part.get_payload(decode=True)
            if not payload_bytes:
                continue

            extracted.append(
                SimpleNamespace(
                    fname=filename,
                    content=base64.b64encode(payload_bytes),
                )
            )

        return subject, extracted

    def _extract_pec_attachments_from_eml_attachment(self, attachment):
        content = getattr(attachment, "content", None)
        raw = self._coerce_bytes(content)
        candidates = [raw]

        try:
            decoded = base64.b64decode(raw, validate=True)
            if decoded and decoded != raw:
                candidates.append(decoded)
        except Exception:
            try:
                decoded = base64.b64decode(raw)
                if decoded and decoded != raw:
                    candidates.append(decoded)
            except Exception:
                pass

        best_subject = ""
        best_attachments = []
        for candidate in candidates:
            subject, extracted = self._extract_pec_attachments_from_eml_bytes(candidate)
            if len(extracted) > len(best_attachments):
                best_subject = subject
                best_attachments = extracted
            elif extracted and not best_attachments and subject:
                best_subject = subject
                best_attachments = extracted

        return best_subject, best_attachments

    def _maybe_unwrap_pec_nested_eml(self, message_dict):
        attachments = message_dict.get("attachments", []) or []
        if not attachments:
            return

        def _att_name(att):
            fname = getattr(att, "fname", "")
            if not fname and isinstance(att, dict):
                fname = att.get("fname") or att.get("name") or ""
            if not fname and isinstance(att, (tuple, list)) and att:
                fname = att[0] or ""
            return fname or ""

        nested_emls = []
        for att in attachments:
            fname = _att_name(att)
            if (fname or "").lower().endswith(".eml"):
                nested_emls.append(att)

        if not nested_emls:
            return

        inner_subject = ""
        extracted = []
        for eml_att in nested_emls:
            subject, inner_attachments = self._extract_pec_attachments_from_eml_attachment(eml_att)
            if subject and not inner_subject:
                inner_subject = subject
            extracted.extend(inner_attachments)

        if not extracted:
            return

        existing_names = {
            (_att_name(a) or "").strip().lower()
            for a in attachments
            if _att_name(a)
        }
        deduped_extracted = []
        for a in extracted:
            name = (getattr(a, "fname", "") or "").strip().lower()
            if not name or name in existing_names:
                continue
            existing_names.add(name)
            deduped_extracted.append(a)

        if not deduped_extracted:
            return

        message_dict["attachments"] = list(attachments) + deduped_extracted

        current_subject = message_dict.get("subject") or ""
        current_invoice = self._extract_invoice_filename_from_text(current_subject)
        inner_invoice = self._extract_invoice_filename_from_text(inner_subject)
        if inner_subject and (current_subject.upper().startswith("POSTA CERTIFICATA") or (inner_invoice and not current_invoice)):
            message_dict["subject"] = inner_subject

        _logger.debug(
            "PEC nested eml unwrapped: extracted=%s inner_subject=%s",
            [getattr(a, "fname", "") for a in deduped_extracted],
            inner_subject,
        )

    def _log_pec_routing_debug(self, message, message_dict, fetchmail_server=None):
        if not _logger.isEnabledFor(logging.DEBUG):
            return

        headers = {
            "message_id": message.get("Message-Id"),
            "subject": (message_dict or {}).get("subject"),
            "from": message.get("From"),
            "reply_to": message.get("Reply-To"),
            "return_path": message.get("Return-Path"),
        }
        attachments = message_dict.get("attachments", []) or []
        attachment_names = [getattr(a, "fname", "") for a in attachments]
        matches = []
        for name in attachment_names:
            if not name:
                continue
            matches.append(
                {
                    "name": name,
                    "fatturapa": bool(fatturapa_regex.match(name)),
                    "response": bool(response_regex.match(name)),
                    "invoice_filename_in_text": self._extract_invoice_filename_from_text(name),
                }
            )

        subject_invoice_filename = self._extract_invoice_filename_from_text(headers.get("subject") or "")
        server_info = {
            "fetchmail_server_id": fetchmail_server.id if fetchmail_server else None,
            "fetchmail_server_name": fetchmail_server.name if fetchmail_server else None,
        }

        _logger.debug(
            "PEC routing debug: headers=%s server=%s attachments=%s subject_invoice_filename=%s",
            headers,
            server_info,
            matches,
            subject_invoice_filename,
        )

    def _normalize_invoice_xml_filename(self, filename):
        if not filename:
            return filename
        lowered = filename.lower()
        if lowered.endswith(".xml.p7m"):
            return filename[:-4]
        return filename

    def _extract_invoice_filename_from_text(self, text):
        if not text:
            return None
        match = invoice_filename_search_regex.search(text)
        if not match:
            return None
        return self._normalize_invoice_xml_filename(match.group("filename"))

    def _extract_invoice_filenames_from_notification_xml(self, attachment):
        content = getattr(attachment, "content", None)
        xml_bytes = self._decode_bytes_maybe_base64(content)
        if not xml_bytes:
            return []

        try:
            root = etree.fromstring(xml_bytes)
        except Exception:
            return []

        filename = root.xpath('string(//*[local-name()="NomeFile"][1])')
        filename = (filename or "").strip()
        if filename:
            return [self._normalize_invoice_xml_filename(filename)]
        return []

    def _invoice_filename_from_notification_filename(self, filename):
        filename = (filename or "").strip()
        if not filename:
            return None

        base = filename
        lower = base.lower()
        if lower.endswith(".xml.p7m"):
            base = base[:-8]
        elif lower.endswith(".p7m"):
            base = base[:-4]
        elif lower.endswith(".xml"):
            base = base[:-4]

        for sep in ("_RC_", "_NS_", "_MC_", "_NE_", "_DT_", "_AT_", "_MT_"):
            if sep in base:
                base = base.split(sep)[0]
                break

        if not base:
            return None
        return self._normalize_invoice_xml_filename(base + ".xml")

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

        self._maybe_unwrap_pec_nested_eml(message_dict)

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
        elif self.env.context.get("fetchmail_server_id", False):
            fetchmail_server = self.env["fetchmail.server"].sudo().browse(
                self.env.context["fetchmail_server_id"]
            )
            if fetchmail_server.is_l10n_it_edi_pec:
                self._log_pec_routing_debug(message, message_dict, fetchmail_server=fetchmail_server)
                # Try to find related invoice by SUBJECT
                invoice = self.find_invoice_by_subject(message_dict["subject"])
                if invoice:
                    return self.manage_pec_sdi_response(invoice, message_dict)
                
                # Try to find related invoice by ATTACHMENT (SdI notification)
                # This handles cases where sender is not @pec.fatturapa.it or subject format differs
                if any(response_regex.match(x.fname) for x in message_dict.get("attachments", [])):
                    return self.manage_pec_sdi_notification(message, message_dict)

                return self.manage_pec_sdi_notification(message, message_dict)
        
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
        parsed = invoice._l10n_it_edi_parse_pec_notification(message_dict)
        if not parsed:
            applied = invoice._l10n_it_edi_apply_pec_receipt(message_dict)
            _logger.info(
                "PEC response fallback applied=%s invoice_id=%s invoice_name=%s subject=%s",
                bool(applied),
                invoice.id,
                invoice.name,
                (message_dict or {}).get("subject"),
            )
        else:
            _logger.info(
                "PEC SdI notification parsed for invoice_id=%s invoice_name=%s",
                invoice.id,
                invoice.name,
            )
        self.clean_message_dict(message_dict)
        return []

    def manage_pec_sdi_notification(self, message, message_dict):
        """Handle SDI notification via PEC"""
        subject = message_dict.get("subject") or ""
        invoice_filename_from_subject = self._extract_invoice_filename_from_text(subject)
        _logger.debug(
            "PEC notification lookup start message_id=%s subject=%s invoice_filename_from_subject=%s",
            message.get("Message-Id"),
            subject,
            invoice_filename_from_subject,
        )

        invoice_from_subject = self.find_invoice_by_subject(subject)
        if invoice_from_subject:
            _logger.info(
                "PEC notification matched invoice by subject invoice_id=%s invoice_name=%s",
                invoice_from_subject.id,
                invoice_from_subject.name,
            )
            return self.manage_pec_sdi_response(invoice_from_subject, message_dict)

        for attachment in message_dict.get("attachments", []):
            fname = getattr(attachment, "fname", "")
            _logger.debug(
                "PEC notification attachment fname=%s fatturapa_match=%s response_match=%s",
                fname,
                bool(fatturapa_regex.match(fname or "")),
                bool(response_regex.match(fname or "")),
            )

            invoice_filename = self._invoice_filename_from_notification_filename(fname)
            if invoice_filename:
                _logger.debug(
                    "PEC notification derived invoice_filename=%s from notification fname=%s",
                    invoice_filename,
                    fname,
                )
                invoice = self._find_invoice_by_xml_filename(invoice_filename)
                if invoice:
                    _logger.info(
                        "PEC notification matched invoice by derived filename invoice_id=%s invoice_name=%s",
                        invoice.id,
                        invoice.name,
                    )
                    return self.manage_pec_sdi_response(invoice, message_dict)
                _logger.debug(
                    "PEC notification no invoice matched by derived filename=%s",
                    invoice_filename,
                )

            match = response_regex.match(fname)
            if match:
                country_or_vat = match.group(1) or match.group(2) or ""
                invoice_filename = f"{country_or_vat}_{match.group('progressive')}.xml"
                invoice_filename = self._normalize_invoice_xml_filename(invoice_filename)
                _logger.debug(
                    "PEC notification inferred invoice_filename=%s from response attachment=%s",
                    invoice_filename,
                    fname,
                )
                invoice = self._find_invoice_by_xml_filename(invoice_filename)
                if invoice:
                    _logger.info(
                        "PEC notification matched invoice by inferred filename invoice_id=%s invoice_name=%s",
                        invoice.id,
                        invoice.name,
                    )
                    return self.manage_pec_sdi_response(invoice, message_dict)
                _logger.debug(
                    "PEC notification no invoice matched by inferred filename=%s",
                    invoice_filename,
                )

            invoice_filename = self._extract_invoice_filename_from_text(fname)
            if invoice_filename:
                _logger.debug(
                    "PEC notification extracted invoice_filename=%s from attachment fname=%s",
                    invoice_filename,
                    fname,
                )
                invoice = self._find_invoice_by_xml_filename(invoice_filename)
                if invoice:
                    _logger.info(
                        "PEC notification matched invoice by extracted filename invoice_id=%s invoice_name=%s",
                        invoice.id,
                        invoice.name,
                    )
                    return self.manage_pec_sdi_response(invoice, message_dict)
                _logger.debug(
                    "PEC notification no invoice matched by extracted filename=%s",
                    invoice_filename,
                )

            invoice_filenames_from_xml = self._extract_invoice_filenames_from_notification_xml(attachment)
            if invoice_filenames_from_xml:
                _logger.debug(
                    "PEC notification extracted invoice_filenames_from_xml=%s attachment=%s",
                    invoice_filenames_from_xml,
                    fname,
                )

            for invoice_filename in invoice_filenames_from_xml:
                invoice = self._find_invoice_by_xml_filename(invoice_filename)
                if invoice:
                    _logger.info(
                        "PEC notification matched invoice by NomeFile invoice_id=%s invoice_name=%s",
                        invoice.id,
                        invoice.name,
                    )
                    return self.manage_pec_sdi_response(invoice, message_dict)
                _logger.info(
                    "PEC notification no invoice matched by NomeFile invoice_filename=%s",
                    invoice_filename,
                )

        _logger.info(
            "PEC notification discarded: no match found message_id=%s subject=%s attachments=%s",
            message.get("Message-Id"),
            subject,
            [getattr(a, "fname", "") for a in (message_dict.get("attachments", []) or [])],
        )

        fetchmail_server_id = self.env.context.get("fetchmail_server_id")
        if fetchmail_server_id:
            company = self.env["res.company"].sudo().search(
                [("l10n_it_edi_pec_server_id", "=", fetchmail_server_id)], limit=1
            )
            if company:
                msg_attachments = []
                seen = set()
                for att in (message_dict.get("attachments", []) or []):
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

                    msg_attachments.append((fname, self._decode_bytes_maybe_base64(content)))

                company.message_post(
                    body=_(
                        "Notifica PEC non associata ad alcuna fattura. Subject: %(subject)s - Message-Id: %(message_id)s"
                    )
                    % {
                        "subject": subject,
                        "message_id": message.get("Message-Id") or "",
                    },
                    attachments=msg_attachments,
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
        
        attachments = self.env["ir.attachment"].sudo().create(
            [
                {
                    "name": att.fname,
                    "type": "binary",
                    "raw": (
                        att.content
                        if isinstance(att.content, (bytes, bytearray))
                        else str(att.content or "").encode()
                    ),
                    "res_model": "account.move",
                    "res_id": 0,
                }
                for att in message_dict.get("attachments", [])
            ]
        )

        for attachment in attachments:
            if fatturapa_regex.match(attachment.name):
                self.create_invoice_from_attachment(attachment, message_dict)

        message_dict["attachment_ids"] = attachments.ids
        self.clean_message_dict(message_dict)
        
        # Remove model/res_id to avoid attaching to wrong record
        del message_dict["model"]
        del message_dict["res_id"]
        
        _logger.info(
            "Processed PEC incoming attachments (no chatter message created)"
        )
        
        return []

    def _find_invoice_by_xml_filename(self, filename):
        filename = (filename or "").strip()
        if not filename:
            return self.env["account.move"]

        fetchmail_server_id = self.env.context.get("fetchmail_server_id")
        company = False
        if fetchmail_server_id:
            company = (
                self.env["res.company"]
                .sudo()
                .search([("l10n_it_edi_pec_server_id", "=", fetchmail_server_id)], limit=1)
            )

        def _base_name(name):
            name = (name or "").strip()
            lower = name.lower()
            if lower.endswith(".xml.p7m"):
                return name[:-8]
            if lower.endswith(".p7m"):
                return name[:-4]
            if lower.endswith(".xml"):
                return name[:-4]
            return name

        def _move_from_attachment(att):
            if not att:
                return self.env["account.move"]
            if att.res_model == "account.move" and att.res_id:
                return self.env["account.move"].browse(att.res_id).exists()
            if att.res_model and att.res_id:
                try:
                    rec = self.env[att.res_model].sudo().browse(att.res_id).exists()
                except Exception:
                    rec = self.env[att.res_model]
                if rec and "move_id" in rec._fields:
                    return rec.move_id.exists()
                if rec and "account_move_id" in rec._fields:
                    return rec.account_move_id.exists()
                if rec and "res_model" in rec._fields and "res_id" in rec._fields:
                    if rec.res_model == "account.move" and rec.res_id:
                        return self.env["account.move"].browse(rec.res_id).exists()
            return self.env["account.move"]

        Move = self.env["account.move"].sudo()
        out_move_domain = [("move_type", "in", ("out_invoice", "out_refund", "out_receipt"))]
        if company:
            out_move_domain.append(("company_id", "=", company.id))

        Attachment = self.env["ir.attachment"].sudo()
        candidates = []
        base = _base_name(filename)
        key_match = re.match(INVOICE_KEY_REGEX, base)
        progressive = key_match.group("progressive") if key_match else ""
        candidates.extend(
            [
                filename,
                base + ".xml",
                base + ".xml.p7m",
                base + ".p7m",
            ]
        )
        if progressive and progressive != base:
            candidates.extend(
                [
                    progressive,
                    progressive + ".xml",
                    progressive + ".xml.p7m",
                    progressive + ".p7m",
                ]
            )

        for cand in [c for c in candidates if c]:
            move = Move.search(
                out_move_domain + [("l10n_it_edi_attachment_id.name", "=ilike", cand)],
                order="id desc",
                limit=1,
            )
            if move:
                return move

        like_patterns = [
            f"%_{base}.xml",
            f"%_{base}.xml.p7m",
            f"%_{base}.p7m",
            f"%{base}.xml%",
            f"%{base}.p7m%",
        ]
        if progressive and progressive != base:
            like_patterns.extend(
                [
                    f"%_{progressive}.xml",
                    f"%_{progressive}.xml.p7m",
                    f"%_{progressive}.p7m",
                    f"%{progressive}.xml%",
                    f"%{progressive}.p7m%",
                ]
            )
        for pat in like_patterns:
            move = Move.search(
                out_move_domain + [("l10n_it_edi_attachment_id.name", "ilike", pat)],
                order="id desc",
                limit=1,
            )
            if move:
                return move

        for cand in [c for c in candidates if c]:
            att = Attachment.search(
                [
                    ("name", "=ilike", cand),
                    ("res_id", "!=", 0),
                ],
                order="id desc",
                limit=1,
            )
            move = _move_from_attachment(att)
            if move:
                return move

        att = Attachment.search(
            [
                ("name", "ilike", base + "%"),
                ("res_id", "!=", 0),
            ],
            order="id desc",
            limit=10,
        )
        for a in att:
            move = _move_from_attachment(a)
            if move:
                return move

        att = Attachment.search(
            [
                ("name", "ilike", "%" + base + "%"),
                ("res_id", "!=", 0),
            ],
            order="id desc",
            limit=10,
        )
        for a in att:
            move = _move_from_attachment(a)
            if move:
                return move

        if key_match:
            vat_or_country = key_match.group(1) or key_match.group(2) or ""
            domain = [("move_type", "in", ("out_invoice", "out_refund", "out_receipt"))]
            if vat_or_country:
                domain.append(("company_id.vat", "ilike", vat_or_country))
            if progressive:
                domain.append("|")
                domain.extend(
                    [
                        ("name", "ilike", progressive),
                        ("payment_reference", "ilike", progressive),
                    ]
                )
            move = self.env["account.move"].sudo().search(domain, order="id desc", limit=1)
            if move:
                return move

        return self.env["account.move"]

    def find_invoice_by_subject(self, subject):
        """Find invoice by PEC subject"""
        subject = subject or ""
        filename = self._extract_invoice_filename_from_text(subject)
        if filename:
            invoice = self._find_invoice_by_xml_filename(filename)
            if invoice:
                return invoice

        if ":" in subject:
            tail = (subject.split(":")[-1] or "").strip()
            if tail:
                invoice = self._find_invoice_by_xml_filename(tail)
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
