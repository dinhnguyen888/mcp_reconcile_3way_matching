from odoo.exceptions import UserError
from odoo.tools import float_compare

from odoo import _, api, fields, models


class ReconcileSaleMatch(models.Model):
    _name = "reconcile.sale.match"
    _description = "Sales Fulfilment Match"
    _inherit = [  # noqa: RUF012
        "account.reconcile.match.mixin",
        "mail.thread",
        "mail.activity.mixin",
    ]
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, default=lambda self: _("New Sales Match"))
    invoice_id = fields.Many2one(
        "account.move",
        required=True,
        ondelete="cascade",
        check_company=True,
        index=True,
    )
    company_id = fields.Many2one(
        related="invoice_id.company_id", store=True, index=True
    )
    partner_id = fields.Many2one(related="invoice_id.partner_id", store=True)
    currency_id = fields.Many2one(related="invoice_id.currency_id", store=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("matched", "Matched"),
            ("exception", "Exception"),
            ("approved", "Approved"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    line_ids = fields.One2many("reconcile.sale.match.line", "match_id", copy=False)
    exception_count = fields.Integer(compute="_compute_summary")
    variance_amount = fields.Monetary(
        compute="_compute_summary", currency_field="currency_id"
    )

    _sql_constraints = [  # noqa: RUF012
        (
            "invoice_unique",
            "unique(invoice_id)",
            "A customer invoice can have only one sales match.",
        )
    ]

    @api.depends("line_ids.result", "line_ids.variance_amount")
    def _compute_summary(self):
        for record in self:
            record.exception_count = len(
                record.line_ids.filtered(lambda line: line.result != "matched")
            )
            record.variance_amount = sum(record.line_ids.mapped("variance_amount"))

    def _validate_match_source(self):
        if self.invoice_id.move_type not in ("out_invoice", "out_refund"):
            raise UserError(
                _("Sales matching is only available for customer invoices.")
            )

    def _prepare_match_line_values(self):
        self.ensure_one()
        values = []
        for invoice_line in self.invoice_id.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        ):
            sale_lines = invoice_line.sale_line_ids
            ordered = sum(sale_lines.mapped("product_uom_qty"))
            delivered = sum(sale_lines.mapped("qty_delivered"))
            invoiced = invoice_line.quantity
            expected_price = sale_lines[:1].price_unit if sale_lines else 0.0
            result = "matched"
            quantity_comparison = float_compare(
                invoiced,
                delivered,
                precision_rounding=invoice_line.product_uom_id.rounding,
            )
            if not sale_lines:
                result = "missing_sale_order"
            elif quantity_comparison > 0:
                result = "invoiced_not_delivered"
            elif quantity_comparison < 0:
                result = "delivered_not_invoiced"
            elif (
                float_compare(
                    invoice_line.price_unit,
                    expected_price,
                    precision_rounding=self.currency_id.rounding,
                )
                != 0
            ):
                result = "price_difference"
            values.append(
                {
                    "match_id": self.id,
                    "invoice_line_id": invoice_line.id,
                    "sale_line_ids": [(6, 0, sale_lines.ids)],
                    "product_id": invoice_line.product_id.id,
                    "ordered_quantity": ordered,
                    "delivered_quantity": delivered,
                    "invoiced_quantity": invoiced,
                    "expected_amount": delivered * expected_price,
                    "actual_amount": invoice_line.price_subtotal,
                    "result": result,
                }
            )
        return values

    def _match_line_model(self):
        return "reconcile.sale.match.line"

    def _get_computed_match_state(self):
        self.ensure_one()
        return (
            "matched"
            if self.line_ids and all(line.result == "matched" for line in self.line_ids)
            else "exception"
        )


class ReconcileSaleMatchLine(models.Model):
    _name = "reconcile.sale.match.line"
    _description = "Sales Fulfilment Match Line"

    match_id = fields.Many2one(
        "reconcile.sale.match", required=True, ondelete="cascade"
    )
    invoice_line_id = fields.Many2one(
        "account.move.line", required=True, ondelete="cascade", check_company=True
    )
    sale_line_ids = fields.Many2many(
        "sale.order.line", string="Sales Order Lines", check_company=True
    )
    product_id = fields.Many2one("product.product", readonly=True)
    ordered_quantity = fields.Float(readonly=True)
    delivered_quantity = fields.Float(readonly=True)
    invoiced_quantity = fields.Float(readonly=True)
    currency_id = fields.Many2one(related="match_id.currency_id")
    expected_amount = fields.Monetary(currency_field="currency_id", readonly=True)
    actual_amount = fields.Monetary(currency_field="currency_id", readonly=True)
    variance_amount = fields.Monetary(
        compute="_compute_variance", currency_field="currency_id"
    )
    result = fields.Selection(
        [
            ("matched", "Matched"),
            ("missing_sale_order", "Missing Sales Order"),
            ("delivered_not_invoiced", "Delivered Not Invoiced"),
            ("invoiced_not_delivered", "Invoiced Not Delivered"),
            ("price_difference", "Price Difference"),
        ],
        required=True,
        readonly=True,
    )

    @api.depends("actual_amount", "expected_amount")
    def _compute_variance(self):
        for line in self:
            line.variance_amount = line.actual_amount - line.expected_amount


class AccountMove(models.Model):
    _inherit = "account.move"

    sale_reconcile_match_id = fields.Many2one("reconcile.sale.match", copy=False)

    def action_open_sale_reconcile_match(self):
        return self._open_reconcile_match(
            match_field="sale_reconcile_match_id",
            match_model="reconcile.sale.match",
            source_field="invoice_id",
            match_name=_("Sales Match - %s"),
            action_name=_("Sales Fulfilment Match"),
        )
