# -*- coding: utf-8 -*-
import base64
import json
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

BARCODE_TYPES = [
    ("Code128", "Code 128"),
    ("Code39", "Code 39"),
    ("EAN13", "EAN-13"),
    ("EAN8", "EAN-8"),
    ("UPCA", "UPC-A"),
    ("UPCE", "UPC-E"),
    ("Codabar", "Codabar"),
    ("I2of5", "Interleaved 2 of 5"),
    ("MSI", "MSI"),
]
BARCODE_TYPE_LABELS = dict(BARCODE_TYPES)


class LabelTemplate(models.Model):
    _name = "label.template"
    _description = "Modello etichetta personalizzato"
    _order = "name"

    name = fields.Char(required=True)
    model_id = fields.Many2one("ir.model", string="Applies to Model", required=True, ondelete="cascade", default=lambda self: self.env.ref('product.model_product_template'), readonly=True)
    field_line_ids = fields.One2many("label.template.line", "template_id", string="Campi")


    page_width_mm = fields.Float("Larghezza pagina (mm)", default=210.0)
    page_height_mm = fields.Float("Altezza pagina (mm)", default=297.0)
    label_width_mm = fields.Float("Larghezza etichetta (mm)", required = True, default=57.0)
    label_height_mm = fields.Float("Altezza etichetta (mm)", required = True, default=32.0)
    label_border_style = fields.Selection([
        ("dashed", "Tratteggiato"),
        ("solid", "Continuo"),
        ("none", "Nessuno"),
    ], string="Bordo etichetta", default="dashed")
    currency_symbol = fields.Char("Simbolo valuta", default="$")
    currency_position = fields.Selection([
        ("before", "Prima importo"),
        ("after", "Dopo importo"),
    ], default="before", string="Posizione valuta")
    margin_left_mm = fields.Float("Margine sinistro (mm)", default=5.0)
    margin_top_mm = fields.Float("Margine superiore (mm)", default=5.0)
    h_spacing_mm = fields.Float("Spaziatura orizzontale (mm)", default=2.0)
    v_spacing_mm = fields.Float("Spaziatura verticale (mm)", default=2.0)
    columns = fields.Integer("Colonne", default=2)
    rows = fields.Integer("Righe", default=7)
    paperformat_id = fields.Many2one(
        "report.paperformat",
        string="Formato carta",
        required=True,
        help="Seleziona il formato carta da usare per questo modello di etichetta.",
    )
    default_barcode_type = fields.Selection(
        BARCODE_TYPES, string="Tipo barcode predefinito", default=BARCODE_TYPES[0][0]
    )
    show_barcode_human_readable = fields.Boolean("Mostra testo leggibile barcode", default=True)

    preview_html = fields.Html("Anteprima", compute="_compute_preview_html", sanitize=False)

    def _sync_line_barcode_types(self):
        for rec in self:
            default_type = rec.default_barcode_type or BARCODE_TYPES[0][0]
            for line in rec.field_line_ids.filtered(lambda l: l.display_type == "barcode"):
                if not line.barcode_type or line.barcode_type == default_type:
                    if line.id:
                        line.write({"barcode_type": default_type})
                    else:
                        line.barcode_type = default_type

    def _sync_line_barcode_readable(self):
        for rec in self:
            for line in rec.field_line_ids.filtered(lambda l: l.display_type == "barcode"):
                value = rec.show_barcode_human_readable
                if line.id:
                    line.write({"show_human_readable": value})
                else:
                    line.show_human_readable = value

   
    @api.onchange("default_barcode_type")
    def _onchange_default_barcode_type(self):
        self._sync_line_barcode_types()

    def _template_to_paperformat_field_map(self):
        return {
            "page_width_mm": "page_width",
            "page_height_mm": "page_height",
            "margin_left_mm": "margin_left",
            "margin_top_mm": "margin_top",
        }

    def _prepare_paperformat_dimensions(self, paperformat):
        if not paperformat:
            return {}
        return {
            template_field: getattr(
                paperformat, paper_field
            )
            for template_field, paper_field in self._template_to_paperformat_field_map().items()
        }

    @api.onchange("paperformat_id")
    def _onchange_paperformat_id(self):
        for rec in self:
            paper = rec.paperformat_id
            if paper:
                dims = rec._prepare_paperformat_dimensions(paper)
                for field, value in dims.items():
                    current = getattr(rec, field)
                    if current is None or current is False:
                        setattr(rec, field, value)
                rec._sync_dynamic_paperformat()

    @api.model_create_multi
    def create(self, vals_list):
        paper_model = self.env["report.paperformat"]
        for vals in vals_list:
            paper_id = vals.get("paperformat_id")
            if paper_id:
                paper = paper_model.browse(paper_id)
                dims = self._prepare_paperformat_dimensions(paper)
                for field, value in dims.items():
                    if vals.get(field) in (None, False):
                        vals[field] = value
        records = super().create(vals_list)
        records._sync_dynamic_paperformat()
        records._sync_line_barcode_types()
        if any("show_barcode_human_readable" in vals for vals in vals_list):
            records._sync_line_barcode_readable()
        return records

    def write(self, vals):
        vals = dict(vals)
        paper_id = vals.get("paperformat_id")
        res = super().write(vals)
        if paper_id or {"page_width_mm", "page_height_mm", "margin_top_mm", "margin_left_mm"}.intersection(vals):
            self._sync_dynamic_paperformat()
        if "default_barcode_type" in vals:
            self._sync_line_barcode_types()
        if "show_barcode_human_readable" in vals:
            self._sync_line_barcode_readable()
        return res

    def copy(self, default=None):
        self.ensure_one()
        default = dict(default or {})
        default.setdefault("name", _("%s (copia)") % self.name)
        return super().copy(default)

    def _sync_dynamic_paperformat(self):
        try:
            dynamic = self.env.ref("ahm_label_studio.paperformat_dynamic_label").sudo()
        except ValueError:
            return
        rec = self[:1]
        if not rec or not rec.paperformat_id:
            return
        paper = rec.paperformat_id.sudo()

        def _coalesce(value, fallback):
            return value if value else fallback

        dynamic.write({
            "format": paper.format,
            "page_width": _coalesce(rec.page_width_mm, paper.page_width),
            "page_height": _coalesce(rec.page_height_mm, paper.page_height),
            "margin_top": _coalesce(rec.margin_top_mm, paper.margin_top),
            "margin_left": _coalesce(rec.margin_left_mm, paper.margin_left),
            "margin_bottom": paper.margin_bottom,
            "margin_right": paper.margin_right,
            "orientation": paper.orientation,
            "dpi": paper.dpi or 300,
            "disable_shrinking": paper.disable_shrinking,
        })

    def _export_payload(self):
        self.ensure_one()
        lines = []
        for line in self.field_line_ids:
            field = line.field_id
            field_info = {}
            if field:
                field_info = {
                    "name": field.name,
                    "field_description": field.field_description,
                    "ttype": field.ttype,
                    "relation": field.relation,
                    "selection": field.selection,
                }
            lines.append({
                "sequence": line.sequence,
                "font_size": line.font_size,
                "bold": line.bold,
                "color": line.color,
                "x_pos": line.x_pos,
                "y_pos": line.y_pos,
                "show_label": line.show_label,
                "label_text": line.label_text,
                "decimal_precision": line.decimal_precision,
                "is_currency": line.is_currency,
                "align": line.align,
                "variable_key": line.variable_key,
                "display_type": line.display_type,
                "barcode_type": line.barcode_type,
                "show_human_readable": line.show_human_readable,
                "width_mm": line.width_mm,
                "height_mm": line.height_mm,
                "field": field_info,
            })
        return {
            "template": {
                "name": self.name,
                "model": self.model_id.model,
                "page_width_mm": self.page_width_mm,
                "page_height_mm": self.page_height_mm,
                "label_width_mm": self.label_width_mm,
                "label_height_mm": self.label_height_mm,
                "label_border_style": self.label_border_style,
                "currency_symbol": self.currency_symbol,
                "currency_position": self.currency_position,
                "margin_left_mm": self.margin_left_mm,
                "margin_top_mm": self.margin_top_mm,
                "h_spacing_mm": self.h_spacing_mm,
                "v_spacing_mm": self.v_spacing_mm,
                "columns": self.columns,
                "rows": self.rows,
                "paperformat_name": self.paperformat_id.name,
                "default_barcode_type": self.default_barcode_type,
                "show_barcode_human_readable": self.show_barcode_human_readable,
            },
            "lines": lines,
        }

    def action_export_template(self):
        self.ensure_one()
        payload = self._export_payload()
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        attachment = self.env["ir.attachment"].create({
            "name": f"{self.name}.json",
            "type": "binary",
            "datas": base64.b64encode(data),
            "mimetype": "application/json",
        })
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    
     
      # ---------------- Live Preview HTML ----------------
    @api.depends(
        "label_width_mm", "label_height_mm", "label_border_style",
        "field_line_ids",
        "field_line_ids.x_pos", "field_line_ids.y_pos",
        "field_line_ids.font_size",
        "field_line_ids.bold",
        "field_line_ids.color",
        "field_line_ids.display_type", "field_line_ids.field_id",
        "field_line_ids.barcode_type",
        "field_line_ids.show_human_readable",
        "default_barcode_type",
        "currency_symbol",
        "currency_position",
    )
    def _compute_preview_html(self):
        mm_to_px = 3.78
        for rec in self:
            def mm(val): return (val or 0.0) * mm_to_px

            def sample_value(line):
                field = line.field_id
                if not field:
                    return "Sample Text"
                ttype = field.ttype
                label = field.field_description or field.name or "Sample"
                if ttype in ("char", "text", "html"):
                    return f"{label}"
                if ttype == "integer":
                    return "123"
                if ttype in ("float", "monetary"):
                    return "123.45"
                if ttype == "boolean":
                    return "Yes"
                if ttype == "date":
                    return "2024-01-01"
                if ttype == "datetime":
                    return "2024-01-01 12:00"
                if ttype == "many2one":
                    return f"{label} Name"
                if ttype in ("many2many", "one2many"):
                    return f"{label}..."
                if ttype == "selection" and field.selection:
                    try:
                        selection = safe_eval(field.selection)
                    except Exception:
                        selection = []
                    if selection:
                        return selection[0][1]
                    return label
                return label

            border_styles = {
                "dashed": "1px dashed #bbb",
                "solid": "1px solid #555",
                "none": "0 none",
            }
            border_style = border_styles.get(rec.label_border_style or "dashed", "1px dashed #bbb")
            company_currency_symbol = (rec.env.company.currency_id.symbol or "").strip()
            if rec.currency_symbol is None:
                currency_symbol = company_currency_symbol
            else:
                currency_symbol = (rec.currency_symbol or "").strip()
            currency_after = rec.currency_position == "after"
            w, h = mm(rec.label_width_mm), mm(rec.label_height_mm)

            html = f"""
                <style>
                    .label-preview-wrapper {{
                        width: 100%;
                        display: flex;
                        justify-content: center;
                        align-items: flex-start;
                        background: #f9f9f9;
                        overflow: visible;
                        padding: 10px 0;
                    }}
                    .label-preview {{
                        position: relative;
                        border: {border_style};
                        background: #fff;
                        width: {w}px;
                        height: {h}px;
                        border-radius: 3px;
                        box-sizing: border-box;
                        margin: 0 auto;
                        transform: none !important;
                    }}
                    .label-preview * {{
                        box-sizing: border-box;
                        transform-origin: top left !important;
                    }}
                    .barcode-placeholder {{
                        background: repeating-linear-gradient(
                            90deg,
                            #000 0,
                            #000 3px,
                            transparent 3px,
                            transparent 6px
                        );
                        background-repeat: repeat;
                        background-size: 6px 100%;
                        background-position: left top;
                    }}
                    .qrcode-placeholder {{
                        background:
                            repeating-linear-gradient(90deg, #000 0, #000 6px, transparent 6px, transparent 12px),
                            repeating-linear-gradient(0deg, #000 0, #000 6px, transparent 6px, transparent 12px);
                        background-blend-mode: multiply;
                        background-size: 12px 12px;
                        background-repeat: repeat;
                        background-position: left top;
                    }}
                    .image-placeholder {{
                        background: repeating-linear-gradient(45deg, rgba(0,0,0,0.1) 0, rgba(0,0,0,0.1) 5px, transparent 5px, transparent 10px);
                        background-size: 10px 10px;
                        background-repeat: repeat;
                        background-position: left top;
                    }}
                </style>

                <div class="label-preview-wrapper">
                <div class="label-preview">
                """


            # elements
            for l in rec.field_line_ids.sorted('sequence'):
                left, top = mm(l.x_pos), mm(l.y_pos)
                base = f"position:absolute;left:{left}px;top:{top}px;"
                if l.display_type == "barcode":
                    placeholder = (
                        f'<div class="barcode-placeholder" '
                        f'style="{base}width:{mm(l.width_mm)}px;height:{mm(l.height_mm)}px;"></div>'
                    )
                    html += placeholder
                    if l.show_human_readable:
                        html += (
                            f'<div style="{base}top:{top + mm(l.height_mm) + 2}px;'
                            f'font-size:10px;color:#333;">123456789012</div>'
                        )
                elif l.display_type == "qrcode":
                    html += (
                        f'<div class="qrcode-placeholder" '
                        f'style="{base}width:{mm(l.width_mm)}px;height:{mm(l.height_mm)}px;"></div>'
                    )
                elif l.display_type == "image":
                    html += (
                        f'<div class="image-placeholder" '
                        f'style="{base}width:{mm(l.width_mm)}px;height:{mm(l.height_mm)}px;"></div>'
                    )
                elif l.display_type == "line":
                    color = l.color or "#000"
                    width_px = mm(l.width_mm)
                    height_px = mm(l.height_mm)
                    if width_px <= height_px:
                        length = height_px if height_px > 1 else 1
                        thickness = width_px if width_px > 1 else 1
                        html += (
                            f'<div style="{base}height:{length}px;width:0;'
                            f'border-left:{thickness}px solid {color};"></div>'
                        )
                    else:
                        length = width_px if width_px > 1 else 1
                        thickness = height_px if height_px > 1 else 1
                        html += (
                            f'<div style="{base}width:{length}px;height:0;'
                            f'border-top:{thickness}px solid {color};"></div>'
                        )
                else:
                    color = l.color or "#000"
                    weight = "font-weight:bold;" if l.bold else ""
                    width = mm(l.width_mm) if l.width_mm else mm(rec.label_width_mm)
                    align = l.align or "left"
                    text_style = (
                        f"{base}font-size:{l.font_size}px;color:{color};"
                        f"{weight}white-space:normal;width:{width}px;text-align:{align};"
                    )
                    val = sample_value(l)
                    if l.field_id and (l.field_id.ttype == "monetary" or l.is_currency):
                        sep = " "
                        val = f"{val}{sep}{currency_symbol}" if currency_after else f"{currency_symbol}{sep}{val}"
                    html += f'<div style="{text_style}">{val}</div>'

            html += "</div></div>"
            rec.preview_html = html





# ---------------- Label Template Line ----------------
class LabelTemplateLine(models.Model):
    _name = "label.template.line"
    _description = "Fields for Label Template"

    template_id = fields.Many2one("label.template", ondelete="cascade")
    template_model_name = fields.Char(related="template_id.model_id.model", store=False, readonly=True)
    field_id = fields.Many2one("ir.model.fields", string="Field")
    sequence = fields.Integer(default=10)
    show_label = fields.Boolean(string="Stampa etichetta")
    label_text = fields.Char(string="Testo etichetta")
    decimal_precision = fields.Integer(string="Decimali")
    is_currency = fields.Boolean(string="Valuta")
    align = fields.Selection(
        [("left", "Sinistra"), ("center", "Centro"), ("right", "Destra")],
        string="Allineamento",
        default="left",
    )
    font_size = fields.Integer(default=10)
    bold = fields.Boolean()
    color = fields.Char(default="#000000")
    x_pos = fields.Float(string="X (mm)", default=0.0)
    y_pos = fields.Float(string="Y (mm)", default=0.0)
    display_type = fields.Selection([
        ("text", "Text"),
        ("barcode", "Barcode"),
        ("image", "Image"),
        ("line", "Line"),
        ("variable", "Variabile"),
    ], string="Display As", default="text")
    variable_key = fields.Selection([
        ("today", "Data di oggi"),
        ("now", "Data/Ora attuale"),
    ], string="Variabile")
    barcode_type = fields.Selection(BARCODE_TYPES, string="Barcode Type")
    show_human_readable = fields.Boolean("Show Human Readable Text", default=True)

    width_mm = fields.Float(string="Width (mm)", default=20.0)
    height_mm = fields.Float(string="Height (mm)", default=15.0)

    @api.onchange("display_type")
    def _onchange_display_type(self):
        for line in self:
            if line.display_type == "barcode" and not line.barcode_type:
                line.barcode_type = line.template_id.default_barcode_type or BARCODE_TYPES[0][0]
                line.show_human_readable = line.template_id.show_barcode_human_readable

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("display_type") == "barcode" and not vals.get("barcode_type"):
                template = self.env["label.template"].browse(vals.get("template_id"))
                if template:
                    vals["barcode_type"] = template.default_barcode_type or BARCODE_TYPES[0][0]
                    vals.setdefault("show_human_readable", template.show_barcode_human_readable)
                else:
                    vals["barcode_type"] = BARCODE_TYPES[0][0]
                    vals.setdefault("show_human_readable", True)
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if "display_type" in vals and vals.get("display_type") == "barcode":
            for line in self.filtered(lambda l: l.display_type == "barcode" and not l.barcode_type):
                line.barcode_type = line.template_id.default_barcode_type or BARCODE_TYPES[0][0]
                line.show_human_readable = line.template_id.show_barcode_human_readable
        return res

    @api.constrains("field_id", "template_id")
    def _check_field_model(self):
        for line in self:
            if line.field_id and line.template_id and line.field_id.model != line.template_id.model_id.model:
                raise ValidationError(_("Selected field must belong to the same model as the template."))
