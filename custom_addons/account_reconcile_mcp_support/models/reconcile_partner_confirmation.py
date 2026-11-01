from odoo.exceptions import UserError
from odoo.tools.float_utils import float_is_zero

from odoo import _, api, fields, models


class ReconcilePartnerConfirmation(models.Model):
    _name = "reconcile.partner.confirmation"
    _description = "Partner Balance Confirmation"
    _inherit = ["mail.thread", "mail.activity.mixin"]  # noqa: RUF012
    _order = "date_to desc, id desc"

    name = fields.Char(required=True, copy=False, default=lambda self: _("New"))
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    partner_id = fields.Many2one(
        "res.partner", required=True, check_company=True, index=True
    )
    account_type = fields.Selection(
        [("asset_receivable", "Receivable"), ("liability_payable", "Payable")],
        required=True,
        default="asset_receivable",
    )
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    currency_id = fields.Many2one(related="company_id.currency_id", store=True)
    opening_balance = fields.Monetary(currency_field="currency_id", readonly=True)
    debit = fields.Monetary(currency_field="currency_id", readonly=True)
    credit = fields.Monetary(currency_field="currency_id", readonly=True)
    closing_balance = fields.Monetary(currency_field="currency_id", readonly=True)
    partner_confirmed_balance = fields.Monetary(currency_field="currency_id")
    variance_amount = fields.Monetary(
        compute="_compute_variance", store=True, currency_field="currency_id"
    )
    state = fields.Selection(
        [
            ("not_sent", "Not Sent"),
            ("sent", "Sent"),
            ("matched", "Partner Confirmed"),
            ("difference", "Partner Reported Difference"),
            ("investigating", "Investigating"),
            ("adjusted", "Adjusted"),
            ("closed", "Closed"),
        ],
        default="not_sent",
        required=True,
        tracking=True,
    )
    sent_at = fields.Datetime(readonly=True, copy=False)
    confirmed_at = fields.Datetime(readonly=True, copy=False)
    case_id = fields.Many2one("reconcile.case", readonly=True, copy=False)
    attachment_ids = fields.Many2many("ir.attachment", string="Confirmation Evidence")
    notes = fields.Text()

    _sql_constraints = [  # noqa: RUF012
        (
            "name_company_unique",
            "unique(name, company_id)",
            "The confirmation number must be unique per company.",
        )
    ]

    @api.depends("closing_balance", "partner_confirmed_balance")
    def _compute_variance(self):
        for confirmation in self:
            confirmation.variance_amount = (
                confirmation.partner_confirmed_balance - confirmation.closing_balance
            )

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for confirmation in self:
            if confirmation.date_from > confirmation.date_to:
                raise UserError(_("The start date must not be after the end date."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "reconcile.partner.confirmation"
                ) or _("New")
        return super().create(vals_list)

    def action_calculate(self):
        for confirmation in self:
            common = [
                ("company_id", "=", confirmation.company_id.id),
                (
                    "partner_id",
                    "child_of",
                    confirmation.partner_id.commercial_partner_id.id,
                ),
                ("account_type", "=", confirmation.account_type),
                ("parent_state", "=", "posted"),
            ]
            opening_lines = self.env["account.move.line"].search(
                common + [("date", "<", confirmation.date_from)]
            )
            period_lines = self.env["account.move.line"].search(
                common
                + [
                    ("date", ">=", confirmation.date_from),
                    ("date", "<=", confirmation.date_to),
                ]
            )
            opening = sum(opening_lines.mapped("balance"))
            debit = sum(period_lines.mapped("debit"))
            credit = sum(period_lines.mapped("credit"))
            confirmation.write(
                {
                    "opening_balance": opening,
                    "debit": debit,
                    "credit": credit,
                    "closing_balance": opening + debit - credit,
                }
            )
        return True

    def action_mark_sent(self):
        for confirmation in self:
            confirmation.write({"state": "sent", "sent_at": fields.Datetime.now()})
        return True

    def action_record_response(self):
        for confirmation in self:
            if confirmation.state != "sent":
                raise UserError(_("Only a sent confirmation can receive a response."))
            if not confirmation.attachment_ids:
                raise UserError(_("Attach the partner's confirmation evidence first."))
            matched = float_is_zero(
                confirmation.variance_amount,
                precision_rounding=confirmation.currency_id.rounding,
            )
            confirmation.write(
                {
                    "state": "matched" if matched else "difference",
                    "confirmed_at": fields.Datetime.now(),
                }
            )
            if not matched:
                confirmation._ensure_case()
        return True

    def _ensure_case(self):
        self.ensure_one()
        if self.case_id:
            return self.case_id
        case = self.env["reconcile.case"].create(
            {
                "reconcile_type": "bank",
                "company_id": self.company_id.id,
                "partner_id": self.partner_id.id,
                "period_date": self.date_to,
                "department": "accounting",
                "reason_code": "other",
                "expected_amount": self.closing_balance,
                "actual_amount": self.partner_confirmed_balance,
                "reason": _("Partner balance confirmation difference: %s") % self.name,
            }
        )
        case.action_start_review()
        self.case_id = case
        return case

    def action_investigate(self):
        for confirmation in self:
            if confirmation.state != "difference":
                raise UserError(_("Only a difference can be investigated."))
            confirmation.state = "investigating"
        return True

    def action_mark_adjusted(self):
        if not self.env.user.has_group(
            "account_reconcile_mcp_support.group_reconcile_approver"
        ):
            raise UserError(_("Only an approver can confirm an adjustment."))
        for confirmation in self:
            if confirmation.state != "investigating" or not confirmation.notes:
                raise UserError(
                    _("Document the investigation before marking it adjusted.")
                )
            confirmation.state = "adjusted"
        return True

    def action_close(self):
        if not self.env.user.has_group(
            "account_reconcile_mcp_support.group_reconcile_approver"
        ):
            raise UserError(_("Only an approver can close a confirmation."))
        for confirmation in self:
            if confirmation.state not in ("matched", "adjusted"):
                raise UserError(
                    _("Only matched or adjusted confirmations can be closed.")
                )
            confirmation.state = "closed"
        return True
