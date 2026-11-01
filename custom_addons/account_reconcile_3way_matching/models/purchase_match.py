from odoo.exceptions import UserError
from odoo.tools import float_compare

from odoo import _, api, fields, models


class ReconcilePurchaseMatch(models.Model):
    _name = "reconcile.purchase.match"
    _description = "Purchase Three-Way Match"
    _inherit = [  # noqa: RUF012
        "account.reconcile.match.mixin",
        "mail.thread",
        "mail.activity.mixin",
    ]
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, default=lambda self: _("New Three-Way Match"))
    bill_id = fields.Many2one(
        "account.move",
        required=True,
        ondelete="cascade",
        check_company=True,
        index=True,
    )
    company_id = fields.Many2one(related="bill_id.company_id", store=True, index=True)
    partner_id = fields.Many2one(related="bill_id.partner_id", store=True)
    currency_id = fields.Many2one(related="bill_id.currency_id", store=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("matched", "Matched"),
            ("within_tolerance", "Within Tolerance"),
            ("exception", "Exception"),
            ("approved", "Approved"),
            ("posted", "Posted"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    line_ids = fields.One2many("reconcile.purchase.match.line", "match_id", copy=False)
    purchase_order_ids = fields.Many2many(
        "purchase.order", compute="_compute_related_documents"
    )
    picking_ids = fields.Many2many(
        "stock.picking", compute="_compute_related_documents"
    )
    purchase_order_count = fields.Integer(compute="_compute_related_documents")
    picking_count = fields.Integer(compute="_compute_related_documents")
    expected_amount = fields.Monetary(
        compute="_compute_totals", currency_field="currency_id"
    )
    actual_amount = fields.Monetary(
        compute="_compute_totals", currency_field="currency_id"
    )
    variance_amount = fields.Monetary(
        compute="_compute_totals", currency_field="currency_id"
    )
    exception_count = fields.Integer(compute="_compute_totals")
    bill_state = fields.Selection(related="bill_id.state", string="Bill Status")

    _sql_constraints = [  # noqa: RUF012
        (
            "bill_unique",
            "unique(bill_id)",
            "A vendor bill can have only one active three-way match.",
        ),
    ]

    @api.depends(
        "line_ids.expected_subtotal", "line_ids.actual_subtotal", "line_ids.result"
    )
    def _compute_totals(self):
        for record in self:
            record.expected_amount = sum(record.line_ids.mapped("expected_subtotal"))
            record.actual_amount = sum(record.line_ids.mapped("actual_subtotal"))
            record.variance_amount = record.actual_amount - record.expected_amount
            record.exception_count = len(
                record.line_ids.filtered(lambda line: line.result != "matched")
            )

    @api.depends("bill_id.invoice_line_ids.purchase_line_id")
    def _compute_related_documents(self):
        for record in self:
            orders = record.bill_id.invoice_line_ids.purchase_line_id.order_id
            record.purchase_order_ids = orders
            record.picking_ids = orders.picking_ids
            record.purchase_order_count = len(orders)
            record.picking_count = len(orders.picking_ids)

    def _related_document_action(self, xmlid, records, name):
        action = self.env["ir.actions.act_window"]._for_xml_id(xmlid)
        action["name"] = name
        if len(records) == 1:
            action.update(
                {"view_mode": "form", "res_id": records.id, "views": [(False, "form")]}
            )
        else:
            action["domain"] = [("id", "in", records.ids)]
        return action

    def action_open_purchase_orders(self):
        self.ensure_one()
        return self._related_document_action(
            "purchase.purchase_rfq", self.purchase_order_ids, _("Purchase Orders")
        )

    def action_open_receipts(self):
        self.ensure_one()
        return self._related_document_action(
            "stock.action_picking_tree_all", self.picking_ids, _("Receipts")
        )

    def _validate_match_source(self):
        if self.bill_id.move_type not in ("in_invoice", "in_refund"):
            raise UserError(_("Three-way matching is only available for vendor bills."))
        if self.bill_id.state != "draft":
            raise UserError(
                _("Three-way matching can only be confirmed on a draft vendor bill.")
            )

    def action_confirm(self):
        return self.action_compute()

    def action_approve(self):
        if self.filtered(lambda match: match.bill_id.state != "draft"):
            raise UserError(_("Only exceptions on draft vendor bills can be approved."))
        if self.filtered(lambda match: match.state != "exception"):
            raise UserError(_("Only a match with exceptions can be approved."))
        return super().action_approve()

    def _source_attachment(self):
        self.ensure_one()
        attachment = self.bill_id.message_main_attachment_id
        if attachment:
            return attachment
        return self.env["ir.attachment"].search(
            [
                ("res_model", "=", "account.move"),
                ("res_id", "=", self.bill_id.id),
                (
                    "mimetype",
                    "in",
                    ["application/pdf", "image/png", "image/jpeg", "image/webp"],
                ),
            ],
            order="id desc",
            limit=1,
        )

    def get_workspace_data(self):
        self.ensure_one()
        self.check_access("read")
        attachment = self._source_attachment()
        orders = self.purchase_order_ids
        pickings = self.picking_ids
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "bill_state": self.bill_id.state,
            "bill": {
                "id": self.bill_id.id,
                "name": self.bill_id.display_name,
                "ref": self.bill_id.ref or "",
                "vendor": self.partner_id.display_name,
                "invoice_date": str(self.bill_id.invoice_date or ""),
                "currency": self.currency_id.symbol,
                "amount_total": self.bill_id.amount_total,
            },
            "attachment": {
                "id": attachment.id,
                "name": attachment.name,
                "mimetype": attachment.mimetype or "",
                "url": f"/web/content/{attachment.id}?download=false"
                if attachment
                else "",
            },
            "orders": [
                {"id": order.id, "name": order.name, "state": order.state}
                for order in orders
            ],
            "receipts": [
                {"id": picking.id, "name": picking.name, "state": picking.state}
                for picking in pickings
            ],
            "summary": {
                "expected": self.expected_amount,
                "actual": self.actual_amount,
                "variance": self.variance_amount,
                "exceptions": self.exception_count,
            },
            "lines": [
                {
                    "id": line.id,
                    "product": line.product_id.display_name,
                    "ordered": line.ordered_quantity,
                    "received": line.received_quantity,
                    "billed": line.billed_quantity,
                    "po_price": line.expected_price_unit,
                    "bill_price": line.actual_price_unit,
                    "variance": line.variance_amount,
                    "result": line.result,
                }
                for line in self.line_ids
            ],
        }

    def action_open_workspace(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "purchase_3way_workspace",
            "name": _("Three-Way Matching"),
            "params": {"match_id": self.id},
        }

    def _prepare_match_line_values(self):
        self.ensure_one()
        values = []
        for invoice_line in self.bill_id.invoice_line_ids.filtered(
            lambda line: line.display_type == "product"
        ):
            purchase_line = invoice_line.purchase_line_id
            result = "matched"
            allowed_quantity = 0.0
            ordered_quantity = 0.0
            received_quantity = 0.0
            previously_billed = 0.0
            expected_price = 0.0
            if not purchase_line:
                result = "missing_po"
            else:
                ordered_quantity = purchase_line.product_qty
                received_quantity = purchase_line.qty_received
                previously_billed = max(
                    0.0, purchase_line.qty_invoiced - invoice_line.quantity
                )
                allowed_quantity = max(0.0, received_quantity - previously_billed)
                expected_price = purchase_line.price_unit
                rounding = purchase_line.product_uom.rounding
                if (
                    float_compare(
                        invoice_line.quantity,
                        allowed_quantity,
                        precision_rounding=rounding,
                    )
                    > 0
                ):
                    result = "quantity_difference"
                elif (
                    float_compare(
                        invoice_line.price_unit,
                        expected_price,
                        precision_rounding=self.currency_id.rounding,
                    )
                    != 0
                ):
                    result = "price_difference"
                elif set(invoice_line.tax_ids.ids) != set(purchase_line.taxes_id.ids):
                    result = "tax_difference"
            expected_subtotal = allowed_quantity * expected_price
            values.append(
                {
                    "match_id": self.id,
                    "invoice_line_id": invoice_line.id,
                    "purchase_line_id": purchase_line.id,
                    "product_id": invoice_line.product_id.id,
                    "ordered_quantity": ordered_quantity,
                    "received_quantity": received_quantity,
                    "previously_billed_quantity": previously_billed,
                    "allowed_quantity": allowed_quantity,
                    "billed_quantity": invoice_line.quantity,
                    "expected_price_unit": expected_price,
                    "actual_price_unit": invoice_line.price_unit,
                    "expected_subtotal": expected_subtotal,
                    "actual_subtotal": invoice_line.price_subtotal,
                    "result": result,
                }
            )
        return values

    def _match_line_model(self):
        return "reconcile.purchase.match.line"

    def _get_computed_match_state(self):
        self.ensure_one()
        results = set(self.line_ids.mapped("result"))
        if self.line_ids and results <= {"matched"}:
            return "matched"
        if self.line_ids and results <= {"matched", "within_tolerance"}:
            return "within_tolerance"
        return "exception"


class ReconcilePurchaseMatchLine(models.Model):
    _name = "reconcile.purchase.match.line"
    _description = "Purchase Three-Way Match Line"
    _order = "id"

    match_id = fields.Many2one(
        "reconcile.purchase.match", required=True, ondelete="cascade"
    )
    invoice_line_id = fields.Many2one(
        "account.move.line", required=True, ondelete="cascade", check_company=True
    )
    purchase_line_id = fields.Many2one(
        "purchase.order.line", ondelete="set null", check_company=True
    )
    product_id = fields.Many2one("product.product", readonly=True)
    ordered_quantity = fields.Float(readonly=True)
    received_quantity = fields.Float(readonly=True)
    previously_billed_quantity = fields.Float(readonly=True)
    allowed_quantity = fields.Float(readonly=True)
    billed_quantity = fields.Float(readonly=True)
    expected_price_unit = fields.Float(readonly=True)
    actual_price_unit = fields.Float(readonly=True)
    currency_id = fields.Many2one(related="match_id.currency_id")
    expected_subtotal = fields.Monetary(currency_field="currency_id", readonly=True)
    actual_subtotal = fields.Monetary(currency_field="currency_id", readonly=True)
    variance_amount = fields.Monetary(
        compute="_compute_variance", currency_field="currency_id"
    )
    result = fields.Selection(
        [
            ("matched", "Matched"),
            ("within_tolerance", "Within Tolerance"),
            ("missing_po", "Missing PO"),
            ("quantity_difference", "Quantity Difference"),
            ("price_difference", "Price Difference"),
            ("tax_difference", "Tax Difference"),
        ],
        required=True,
        readonly=True,
    )

    @api.depends("actual_subtotal", "expected_subtotal")
    def _compute_variance(self):
        for line in self:
            line.variance_amount = line.actual_subtotal - line.expected_subtotal


class AccountMove(models.Model):
    _inherit = "account.move"

    purchase_reconcile_match_id = fields.Many2one(
        "reconcile.purchase.match", copy=False
    )
    purchase_match_state = fields.Selection(
        [
            ("draft", "Draft"),
            ("matched", "Matched"),
            ("within_tolerance", "Within Tolerance"),
            ("exception", "Exception"),
            ("approved", "Approved"),
            ("posted", "Posted"),
        ],
        compute="_compute_purchase_match_state",
        string="3-Way Match",
    )

    @api.depends("purchase_reconcile_match_id.state", "state")
    def _compute_purchase_match_state(self):
        for move in self:
            move.purchase_match_state = (
                move.purchase_reconcile_match_id.state
                if move.purchase_reconcile_match_id
                else "draft"
            )

    def _ensure_purchase_reconcile_match(self):
        self.ensure_one()
        if self.move_type not in ("in_invoice", "in_refund"):
            raise UserError(_("Three-way matching is only available for vendor bills."))
        match = self.purchase_reconcile_match_id
        if not match:
            match = self.env["reconcile.purchase.match"].create(
                {
                    "name": _("Three-Way Match - %s") % self.display_name,
                    "bill_id": self.id,
                }
            )
            self.purchase_reconcile_match_id = match
        return match

    def action_open_purchase_reconcile_match(self):
        self.ensure_one()
        match = self._ensure_purchase_reconcile_match()
        if self.state == "draft" and match.state == "draft":
            match.action_compute()
        return match.action_open_workspace()

    def action_post(self):
        for move in self.filtered(
            lambda item: item.move_type in ("in_invoice", "in_refund")
        ):
            if move.company_id.reconcile_block_unmatched_purchase_bills:
                match = move.purchase_reconcile_match_id
                if not match:
                    raise UserError(
                        _("Complete the purchase three-way match before posting.")
                    )
                if match.state != "approved":
                    match.action_compute()
                if match.state not in (
                    "matched",
                    "within_tolerance",
                    "approved",
                ):
                    raise UserError(
                        _(
                            "Complete and approve the purchase three-way match before posting."
                        )
                    )
        result = super().action_post()
        self.filtered(
            lambda move: (
                move.move_type in ("in_invoice", "in_refund")
                and move.purchase_reconcile_match_id
                and move.state == "posted"
            )
        ).mapped("purchase_reconcile_match_id").write({"state": "posted"})
        return result


class StockPicking(models.Model):
    _inherit = "stock.picking"

    vendor_bill_ids = fields.Many2many("account.move", compute="_compute_vendor_bills")
    vendor_bill_count = fields.Integer(compute="_compute_vendor_bills")

    @api.depends("purchase_id.invoice_ids")
    def _compute_vendor_bills(self):
        for picking in self:
            bills = picking.purchase_id.invoice_ids.filtered(
                lambda move: (
                    move.move_type in ("in_invoice", "in_refund")
                    and move.state != "cancel"
                )
            )
            picking.vendor_bill_ids = bills
            picking.vendor_bill_count = len(bills)

    def action_open_vendor_bills(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "account.action_move_in_invoice_type"
        )
        if self.vendor_bill_count == 1:
            action.update(
                {
                    "view_mode": "form",
                    "res_id": self.vendor_bill_ids.id,
                    "views": [(False, "form")],
                }
            )
        else:
            action["domain"] = [("id", "in", self.vendor_bill_ids.ids)]
        return action
