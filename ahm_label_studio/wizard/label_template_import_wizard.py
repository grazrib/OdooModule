import base64
import json
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class LabelTemplateImportWizard(models.TransientModel):
    _name = "label.template.import.wizard"
    _description = "Procedura guidata importazione modelli etichette"

    file = fields.Binary(required=True, string="File")
    filename = fields.Char(string="Nome file")
    create_copy = fields.Boolean(
        default=False,
        string="Crea come copia",
        help="Se attivo, importa il template con il suffisso '(copia)' senza toccare quello esistente.",
    )
    update_existing = fields.Boolean(
        default=False,
        string="Sovrascrivi modello esistente",
        help="Se attivo, il modello esistente con lo stesso nome viene CANCELLATO "
             "e sostituito completamente con quello importato.",
    )

    def _get_model(self, model_name):
        model = self.env["ir.model"].search([("model", "=", model_name)], limit=1)
        if not model:
            raise UserError(_("Modello non trovato: %s") % model_name)
        return model

    def _create_custom_field_if_missing(self, model, field_info):
        field_name = field_info.get("name")
        if not field_name:
            return False
        field = self.env["ir.model.fields"].search([
            ("model", "=", model.model),
            ("name", "=", field_name),
        ], limit=1)
        if field:
            return field
        ttype = field_info.get("ttype")
        if ttype not in ("char", "text", "integer", "float", "monetary", "boolean", "date", "datetime", "many2one", "selection"):
            raise UserError(_("Tipo campo non supportato per creazione automatica: %s (%s)") % (field_name, ttype))
        vals = {
            "name": field_name,
            "field_description": field_info.get("field_description") or field_name,
            "model_id": model.id,
            "ttype": ttype,
            "state": "manual",
            "store": True,
            "readonly": False,
            "required": False,
        }
        if ttype == "selection":
            vals["selection"] = field_info.get("selection") or "[]"
        if ttype == "many2one":
            relation = field_info.get("relation")
            if not relation:
                raise UserError(_("Relazione mancante per campo many2one: %s") % field_name)
            vals["relation"] = relation
        return self.env["ir.model.fields"].create(vals)

    def _build_lines(self, template, lines_data, model):
        """Crea le righe del template dal payload JSON."""
        for line in lines_data:
            field_info = line.get("field") or {}
            field = self._create_custom_field_if_missing(model, field_info) if field_info else False
            self.env["label.template.line"].create({
                "template_id": template.id,
                "field_id": field.id if field else False,
                "sequence": line.get("sequence"),
                "font_size": line.get("font_size"),
                "bold": line.get("bold"),
                "color": line.get("color"),
                "x_pos": line.get("x_pos"),
                "y_pos": line.get("y_pos"),
                "show_label": line.get("show_label"),
                "label_text": line.get("label_text"),
                "decimal_precision": line.get("decimal_precision"),
                "is_currency": line.get("is_currency"),
                "align": line.get("align"),
                "variable_key": line.get("variable_key"),
                "display_type": line.get("display_type"),
                "barcode_type": line.get("barcode_type"),
                "show_human_readable": line.get("show_human_readable"),
                "width_mm": line.get("width_mm"),
                "height_mm": line.get("height_mm"),
            })

    def action_import(self):
        self.ensure_one()
        if not self.file:
            raise UserError(_("Carica un file."))

        data = json.loads(base64.b64decode(self.file).decode("utf-8"))
        template_data = data.get("template") or {}
        lines_data = data.get("lines") or []
        model_name = template_data.get("model")
        model = self._get_model(model_name)
        name = template_data.get("name") or _("Modello etichetta importato")

        # Cerca paperformat per nome (fallback al formato dinamico del modulo)
        paperformat_id = False
        paperformat_name = template_data.get("paperformat_name")
        if paperformat_name:
            paperformat = self.env["report.paperformat"].search([("name", "=", paperformat_name)], limit=1)
            paperformat_id = paperformat.id or False
        if not paperformat_id:
            try:
                paperformat_id = self.env.ref("ahm_label_studio.paperformat_dynamic_label").id
            except ValueError:
                paperformat_id = False

        # Tutti i valori del template da importare
        template_vals = {
            "name": name,
            "model_id": model.id,
            "page_width_mm": template_data.get("page_width_mm"),
            "page_height_mm": template_data.get("page_height_mm"),
            "label_width_mm": template_data.get("label_width_mm"),
            "label_height_mm": template_data.get("label_height_mm"),
            "label_border_style": template_data.get("label_border_style"),
            "currency_symbol": template_data.get("currency_symbol"),
            "currency_position": template_data.get("currency_position"),
            "margin_left_mm": template_data.get("margin_left_mm"),
            "margin_top_mm": template_data.get("margin_top_mm"),
            "h_spacing_mm": template_data.get("h_spacing_mm"),
            "v_spacing_mm": template_data.get("v_spacing_mm"),
            "columns": template_data.get("columns"),
            "rows": template_data.get("rows"),
            "paperformat_id": paperformat_id,
            "default_barcode_type": template_data.get("default_barcode_type"),
            "show_barcode_human_readable": template_data.get("show_barcode_human_readable"),
        }

        existing = self.env["label.template"].search([("name", "=", name)], limit=1)

        if existing:
            if self.update_existing:
                # ── SOVRASCRITTURA confermata ──────────────────────────────────
                # Elimina le righe vecchie, aggiorna tutti i campi e ricrea le righe
                existing.field_line_ids.unlink()
                existing.write(template_vals)
                self._build_lines(existing, lines_data, model)
                return self._open_template(existing)

            elif self.create_copy:
                # ── CREA COPIA ─────────────────────────────────────────────────
                # Genera un nome univoco con suffisso "(copia)", "(copia 2)", ecc.
                copy_name = "%s (copia)" % name
                counter = 2
                while self.env["label.template"].search([("name", "=", copy_name)], limit=1):
                    copy_name = "%s (copia %d)" % (name, counter)
                    counter += 1
                template_vals["name"] = copy_name
                new_template = self.env["label.template"].create(template_vals)
                self._build_lines(new_template, lines_data, model)
                return self._open_template(new_template)

            else:
                # ── CONFLITTO: chiedi all'utente cosa fare ─────────────────────
                raise UserError(
                    _("Esiste già un modello con il nome '%s'.\n\n"
                      "Cosa vuoi fare?\n\n"
                      "• Attiva \"Crea come copia\" per importarlo con suffisso (copia) "
                      "senza toccare quello esistente.\n\n"
                      "• Attiva \"Sovrascrivi modello esistente\" per cancellare quello "
                      "esistente e sostituirlo completamente con quello importato.") % name
                )
        else:
            # ── NESSUN CONFLITTO: crea normalmente ────────────────────────────
            template = self.env["label.template"].create(template_vals)
            self._build_lines(template, lines_data, model)
            return self._open_template(template)

    def _open_template(self, template):
        return {
            "type": "ir.actions.act_window",
            "name": _("Modello etichetta"),
            "res_model": "label.template",
            "view_mode": "form",
            "res_id": template.id,
        }
