# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang
import base64

class LabelPrintWizard(models.TransientModel):
    _name = "label.print.wizard"
    _description = "Procedura guidata stampa etichette"

    template_id = fields.Many2one(
        "label.template", required=True, string="Modello etichetta"
    )
    quantity = fields.Integer(
        default=1, string="Quantità per record",
        help="Numero di etichette da stampare per ogni record selezionato."
    )
    start_position = fields.Integer(
        default=1,
        string="Inizia da",
        help="Posizione etichetta di partenza (1 = prima etichetta del foglio).",
    )
    update_label_flag = fields.Boolean(
        string="Aggiorna flag 'Etichetta stampata'",
        default=True,
        help="Se attivo, dopo la stampa i prodotti selezionati vengono marcati come "
             "'Etichetta stampata'. Disattivare per stampe promo, di test o parziali.",
    )
    preview_note = fields.Html(
        compute="_compute_preview_note", sanitize=False,
        string="Anteprima modello",
        help="Anteprima rapida del modello di etichetta selezionato."
    )

    # ----------------------------
    # COMPUTES / ONCHANGE
    # ----------------------------
    # @api.depends("template_id")
    # def _compute_preview_note(self):
    #     for rec in self:
    #         rec.preview_note = rec.template_id.preview_html or "<p class='text-muted'>Select a template to preview.</p>"

    @api.depends("template_id")
    def _compute_preview_note(self):
        for rec in self:
            html = rec.template_id.preview_html or ""
            if not html.strip():
                html = "<p class='text-muted'>Seleziona un modello da visualizzare in anteprima.</p>"

            rec.preview_note = f"<div class='label-preview-wrapper'>{html}</div>"




    # ----------------------------
    # PRINT ACTION
    # ----------------------------
    def action_print(self):
        self.ensure_one()
        active_model = self.env.context.get("active_model")
        active_ids = self.env.context.get("active_ids", [])

        if not active_model or not active_ids:
            raise UserError(_("No active records selected to print."))

        template = self.template_id
        template._sync_dynamic_paperformat()
        dyn_pf = self.env.ref("ahm_label_studio.paperformat_dynamic_label").sudo()

        data = {
            "active_model": active_model,
            "active_ids": active_ids,
            "template_id": template.id,
            "quantity": self.quantity,
            "paperformat_id": dyn_pf.id,
            "start_position": max(self.start_position, 1),
        }

        ctx = dict(self.env.context, report_paperformat_id=dyn_pf.id)
        action = self.env.ref("ahm_label_studio.action_dynamic_label_report").with_context(ctx)

        # Aggiorna il flag 'etichetta stampata' solo se richiesto
        if self.update_label_flag and active_model == 'product.template' and active_ids:
            products = self.env['product.template'].browse(active_ids)
            products.write({
                'label_printed': True,
                'label_printed_date': fields.Datetime.now(),
            })

        return action.report_action(self.with_context(ctx), data=data)

    # ----------------------------
# Report backend
# ----------------------------
# =======================================================================
#                          REPORT BACKEND
# =======================================================================
class ReportDynamicLabels(models.AbstractModel):
    _name = "report.ahm_label_studio.dynamic_label_template"
    _description = "Report dinamico Studio Etichette"

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        model = data.get("active_model")
        records = self.env[model].browse(data.get("active_ids", []))
        template = self.env["label.template"].browse(data.get("template_id"))
        qty = max(int(data.get("quantity", 1)), 1)

        # Paperformat selection
        paperformat = False
        if data.get("paperformat_id"):
            paperformat = self.env["report.paperformat"].browse(data["paperformat_id"])
        if not paperformat:
            paperformat = template.paperformat_id

        cols = max(template.columns, 1)
        rows = max(template.rows, 1)
        labels_per_page = cols * rows

        # Repeat records according to quantity
        start_offset = max(int(data.get("start_position", 1)) - 1, 0)
        cells = [False] * start_offset
        for rec in records:
            for _ in range(qty):
                cells.append({"rec": rec})

        # Paginate
        pages = []
        for i in range(0, len(cells), labels_per_page):
            chunk = cells[i:i + labels_per_page]
            matrix = []
            for r in range(rows):
                start = r * cols
                row = chunk[start:start + cols]
                if not row:
                    break
                if len(row) < cols:
                    row = row + [False] * (cols - len(row))
                matrix.append(row)
            if matrix:
                pages.append(matrix)

        # Utility: read field value dynamically
        def value_for(rec, line):
            if getattr(line, "display_type", "") == "variable":
                key = getattr(line, "variable_key", "") or ""
                if not key or key == "today":
                    try:
                        d = fields.Date.context_today(self)
                    except Exception:
                        d = fields.Date.today()
                    if isinstance(d, str):
                        try:
                            d = fields.Date.from_string(d)
                        except Exception:
                            return d
                    return d.strftime("%d-%m-%Y")
                if key == "now":
                    try:
                        dt = fields.Datetime.context_timestamp(self, fields.Datetime.now())
                    except Exception:
                        dt = fields.Datetime.now()
                    if isinstance(dt, str):
                        return dt
                    return dt.strftime("%d-%m-%Y %H:%M")
            val = rec
            try:
                for part in (line.field_id.name or "").split('.'):
                    val = getattr(val, part)
                if isinstance(val, (int, float)):
                    return val
                if hasattr(val, "display_name"):
                    return val.display_name
                if isinstance(val, bytes):
                    return val
                return str(val or "")
            except Exception:
                return ""        

        def format_number(rec, value, ttype, line):
            try:
                amount = float(value)
            except Exception:
                return value or ""

            if ttype == "monetary":
                currency = self.env.company.currency_id
                precision = line.decimal_precision or getattr(currency, "decimal_places", None) or 2
                try:
                    base = formatLang(self.env, amount, digits=(16, precision))
                except Exception:
                    base = formatLang(self.env, amount)
                base = base.replace("\u00a0", " ").replace("\u202f", " ")
                raw = getattr(template, "currency_symbol", None)
                if raw is None:
                    symbol = (getattr(currency, "symbol", "") or "").strip()
                else:
                    symbol = (raw or "").strip()
                position = template.currency_position or ("after" if getattr(currency, "position", "after") == "after" else "before")
                space = " "
                if symbol:
                    if position == "before":
                        return f"{symbol}{space}{base}"
                    else:
                        return f"{base}{space}{symbol}"
                return base

            if ttype == "float":
                field = rec._fields.get(line.field_id.name or "")
                precision = line.decimal_precision or 2
                if not line.decimal_precision and field and hasattr(field, "get_digits"):
                    digits = field.get_digits(rec.env)
                    if isinstance(digits, tuple) and len(digits) > 1 and digits[1] is not None:
                        precision = digits[1]
                try:
                    text = formatLang(self.env, amount, digits=(16, precision))
                except Exception:
                    text = formatLang(self.env, amount)
                text = text.replace("\u00a0", " ").replace("\u202f", " ")
                if line.decimal_precision is not None:
                    s = str(text)
                    dec = "," if "," in s else "." if "." in s else None
                    if not dec and line.decimal_precision > 0:
                        dec = "," if (self.env.user.lang or "").lower().startswith("it") else "."
                        return s + dec + ("0" * line.decimal_precision)
                    if dec:
                        parts = s.rsplit(dec, 1)
                        intp = parts[0]
                        fracp = parts[1] if len(parts) > 1 else ""
                        need = max(line.decimal_precision - len(fracp), 0)
                        if need > 0:
                            fracp = fracp + ("0" * need)
                        elif len(fracp) > line.decimal_precision:
                            fracp = fracp[:line.decimal_precision]
                        return intp + dec + fracp
                return text

            return str(value or "")

        return {
            "docs": records,
            "template": template,
            "pages": pages,
            "value_for": value_for,
            "format_number": format_number,
            "paperformat": paperformat,
        }
