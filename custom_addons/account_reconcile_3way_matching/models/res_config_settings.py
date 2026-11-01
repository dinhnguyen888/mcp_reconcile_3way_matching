from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    reconcile_block_unmatched_purchase_bills = fields.Boolean(
        string="Block Vendor Bills Without Three-Way Match",
        default=True,
    )


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    reconcile_block_unmatched_purchase_bills = fields.Boolean(
        related="company_id.reconcile_block_unmatched_purchase_bills", readonly=False
    )
