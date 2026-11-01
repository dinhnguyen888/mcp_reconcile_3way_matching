from odoo.exceptions import UserError

from odoo import _, api, fields, models


class ReconcileCloseChecklist(models.Model):
    _name = "reconcile.close.checklist"
    _description = "Reconciliation Period Close Checklist"
    _inherit = ["mail.thread", "mail.activity.mixin"]  # noqa: RUF012
    _order = "date_to desc, id desc"

    name = fields.Char(required=True, copy=False)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    responsible_id = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.user
    )
    approved_by = fields.Many2one("res.users", readonly=True, copy=False)
    approved_at = fields.Datetime(readonly=True, copy=False)
    locked_at = fields.Datetime(readonly=True, copy=False)
    reopened_reason = fields.Text(copy=False)
    snapshot_json = fields.Json(readonly=True, copy=False)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("in_progress", "In Progress"),
            ("pending_approval", "Pending Approval"),
            ("approved", "Approved"),
            ("locked", "Locked"),
            ("reopened", "Reopened"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    line_ids = fields.One2many(
        "reconcile.close.checklist.line", "checklist_id", copy=True
    )
    unresolved_count = fields.Integer(compute="_compute_summary")
    unresolved_value = fields.Monetary(
        compute="_compute_summary", currency_field="currency_id"
    )
    currency_id = fields.Many2one(related="company_id.currency_id", store=True)
    notes = fields.Text()

    @api.depends("line_ids.state", "line_ids.impact_value")
    def _compute_summary(self):
        for record in self:
            pending = record.line_ids.filtered(lambda line: line.state != "passed")
            record.unresolved_count = len(pending)
            record.unresolved_value = sum(pending.mapped("impact_value"))

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for record in self:
            if record.date_from > record.date_to:
                raise UserError(
                    _("The checklist start date must be before its end date.")
                )

    def action_start(self):
        self.write({"state": "in_progress"})
        return True

    def write(self, vals):
        locked = self.filtered(lambda checklist: checklist.state == "locked")
        allowed = {
            "state",
            "reopened_reason",
            "message_follower_ids",
            "message_partner_ids",
        }
        if locked and set(vals) - allowed:
            raise UserError(_("A locked checklist cannot be edited. Reopen it first."))
        return super().write(vals)

    def _get_reconciliation_checks(self):
        self.ensure_one()
        statement_lines = self.env["account.bank.statement.line"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("date", ">=", self.date_from),
                ("date", "<=", self.date_to),
                ("is_reconciled", "=", False),
            ]
        )
        draft_moves = self.env["account.move"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("date", ">=", self.date_from),
                ("date", "<=", self.date_to),
                ("state", "=", "draft"),
            ]
        )
        open_cases = self.env["reconcile.case"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("period_date", ">=", self.date_from),
                ("period_date", "<=", self.date_to),
                ("state", "not in", ("reconciled", "locked", "cancelled")),
            ]
        )
        return {
            "unmatched_bank": {
                "count": len(statement_lines),
                "impact_value": sum(abs(line.amount) for line in statement_lines),
            },
            "draft_moves": {
                "count": len(draft_moves),
                "impact_value": sum(
                    abs(move.amount_total_signed) for move in draft_moves
                ),
            },
            "open_cases": {
                "count": len(open_cases),
                "impact_value": sum(abs(case.variance_amount) for case in open_cases),
            },
        }

    def action_refresh_checks(self):
        for record in self:
            if record.state not in ("draft", "in_progress", "reopened"):
                raise UserError(_("Checks can only be refreshed before approval."))
            checks = record._get_reconciliation_checks()
            for key, values in checks.items():
                line = record.line_ids.filtered(
                    lambda item, check_key=key: item.check_key == check_key
                )[:1]
                values = {
                    **values,
                    "state": "passed" if not values["count"] else "failed",
                    "last_checked_at": fields.Datetime.now(),
                    "is_automatic": True,
                }
                if line:
                    line.write(values)
                else:
                    values.update({"checklist_id": record.id, "check_key": key})
                    self.env["reconcile.close.checklist.line"].create(values)
            record.snapshot_json = checks
        return True

    def action_submit(self):
        for record in self:
            if not record.line_ids:
                raise UserError(_("Add or generate checklist lines before submitting."))
            if any(line.state in ("pending", "failed") for line in record.line_ids):
                raise UserError(
                    _(
                        "Resolve pending and failed checks before submitting the checklist."
                    )
                )
            record.state = "pending_approval"
        return True

    def action_approve(self):
        if not self.env.user.has_group(
            "account_reconcile_mcp_support.group_reconcile_approver"
        ):
            raise UserError(_("Only an approver can approve a close checklist."))
        self.write(
            {
                "state": "approved",
                "approved_by": self.env.user.id,
                "approved_at": fields.Datetime.now(),
            }
        )
        return True

    def action_lock(self):
        if not self.env.user.has_group(
            "account_reconcile_mcp_support.group_reconcile_period_lock"
        ):
            raise UserError(_("Only a period-lock user can lock a close checklist."))
        for record in self:
            if record.state != "approved":
                raise UserError(_("Only approved checklists can be locked."))
            record.write({"state": "locked", "locked_at": fields.Datetime.now()})
        return True

    def action_reopen(self):
        if not self.env.user.has_group(
            "account_reconcile_mcp_support.group_reconcile_approver"
        ):
            raise UserError(_("Only an approver can reopen a close checklist."))
        for record in self:
            if record.state != "locked" or not record.reopened_reason:
                raise UserError(
                    _("A locked checklist and a reopen reason are required.")
                )
            record.state = "reopened"
        return True


class ReconcileCloseChecklistLine(models.Model):
    _name = "reconcile.close.checklist.line"
    _description = "Reconciliation Close Checklist Check"
    _order = "sequence, id"

    checklist_id = fields.Many2one(
        "reconcile.close.checklist", required=True, ondelete="cascade"
    )
    sequence = fields.Integer(default=10)
    check_key = fields.Selection(
        [
            ("stock_pending", "Pending Stock Moves"),
            ("stock_valuation", "Stock Valuation"),
            ("received_not_billed", "Received Not Billed"),
            ("delivered_not_invoiced", "Delivered Not Invoiced"),
            ("unmatched_bank", "Unmatched Bank Lines"),
            ("draft_moves", "Draft Moves"),
            ("open_cases", "Open Material Cases"),
        ],
        required=True,
    )
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("passed", "Passed"),
            ("failed", "Failed"),
            ("waived", "Waived"),
        ],
        default="pending",
        required=True,
    )
    count = fields.Integer()
    currency_id = fields.Many2one(related="checklist_id.currency_id", store=True)
    impact_value = fields.Monetary(currency_field="currency_id")
    explanation = fields.Text()
    evidence_ids = fields.Many2many("ir.attachment", string="Evidence")
    is_automatic = fields.Boolean(readonly=True)
    last_checked_at = fields.Datetime(readonly=True)

    def action_pass(self):
        self.write({"state": "passed"})
        return True

    def action_fail(self):
        self.write({"state": "failed"})
        return True

    def action_waive(self):
        if not self.env.user.has_group(
            "account_reconcile_mcp_support.group_reconcile_approver"
        ):
            raise UserError(_("Only an approver can waive a close check."))
        for record in self:
            if not record.explanation or not record.evidence_ids:
                raise UserError(
                    _("An explanation and evidence are required to waive a check.")
                )
            record.state = "waived"
        return True
