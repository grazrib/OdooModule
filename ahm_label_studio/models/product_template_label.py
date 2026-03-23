# -*- coding: utf-8 -*-
from odoo import api, fields, models

# Campi che, se modificati, resettano il flag "etichetta stampata"
LABEL_TRACKED_FIELDS = {
    'list_price',
    'standard_price',
    'taxes_id',
    'seller_ids',
}


class ProductTemplateLabelTracking(models.Model):
    _inherit = 'product.template'

    label_printed = fields.Boolean(
        string="Etichetta stampata",
        default=False,
        copy=False,
        help="Indica se l'etichetta è già stata stampata con Studio Etichette. "
             "Il flag si azzera automaticamente alla modifica del prezzo.",
    )
    label_printed_date = fields.Datetime(
        string="Data ultima stampa",
        readonly=True,
        copy=False,
        help="Data e ora dell'ultima stampa etichetta tramite Studio Etichette.",
    )

    def write(self, vals):
        res = super().write(vals)
        # Se skip_label_reset è True nel contesto, non rientrare in ricorsione
        if self.env.context.get('skip_label_reset'):
            return res
        # Se uno dei campi "sensibili" è stato modificato, azzeriamo il flag
        if any(f in vals for f in LABEL_TRACKED_FIELDS):
            to_reset = self.filtered('label_printed')
            if to_reset:
                to_reset.with_context(skip_label_reset=True).write({
                    'label_printed': False,
                    'label_printed_date': False,
                })
        return res

    def action_label_reset_printed(self):
        """Toggling del flag dalla form prodotto (il pulsante stat_button nella form)."""
        for rec in self:
            if rec.label_printed:
                rec.with_context(skip_label_reset=True).write({
                    'label_printed': False,
                    'label_printed_date': False,
                })
            else:
                rec.with_context(skip_label_reset=True).write({
                    'label_printed': True,
                    'label_printed_date': fields.Datetime.now(),
                })

