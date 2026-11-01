from typing import ClassVar

from odoo.exceptions import UserError, ValidationError

from odoo import _, api, fields, models


class ReconcileCase(models.Model):
    _name = "reconcile.case"
    _description = "Reconciliation Exception Case"
    _inherit = ["mail.thread", "mail.activity.mixin"]  # noqa: RUF012
    _order = "priority desc, due_date, id desc"

    name = fields.Char(
        required=True, copy=False, default=lambda self: _("New"), tracking=True
    )
    reconcile_type = fields.Selection(
        [
            ("bank", "Bank / Receivable / Payable"),
            ("vendor_bill", "Vendor Bill"),
            ("purchase", "Purchase Three-Way Match"),
            ("sale", "Sales Fulfilment Match"),
            ("stock", "Stock Count / Valuation"),
            ("period_close", "Period Close"),
        ],
        required=True,
        default="bank",
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    currency_id = fields.Many2one(related="company_id.currency_id", store=True)
    partner_id = fields.Many2one("res.partner", check_company=True, tracking=True)
    owner_id = fields.Many2one(
        "res.users", tracking=True, default=lambda self: self.env.user
    )
    approver_id = fields.Many2one("res.users", tracking=True)
    due_date = fields.Date(tracking=True)
    period_date = fields.Date(
        default=fields.Date.context_today, required=True, index=True
    )
    priority = fields.Selection(
        [("0", "Normal"), ("1", "High"), ("2", "Urgent")], default="0", index=True
    )
    risk_level = fields.Selection(
        [
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("critical", "Critical"),
        ],
        default="low",
        tracking=True,
    )
    reason_code = fields.Selection(
        [
            ("data_error", "Data Entry Error"),
            ("timing", "Timing Difference"),
            ("bank_fee", "Bank Fee / Interest"),
            ("quantity", "Quantity Difference"),
            ("price", "Price Difference"),
            ("tax", "Tax Difference"),
            ("duplicate", "Possible Duplicate"),
            ("currency", "Currency / Exchange Rate"),
            ("missing_document", "Missing Document"),
            ("other", "Other"),
        ],
        tracking=True,
    )
    department = fields.Selection(
        [
            ("accounting", "Accounting"),
            ("warehouse", "Warehouse"),
            ("purchase", "Purchase"),
            ("sales", "Sales"),
            ("partner", "Partner"),
        ],
        default="accounting",
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("collecting_data", "Collecting Data"),
            ("auto_matched", "Auto Matched"),
            ("needs_review", "Needs Review"),
            ("waiting_warehouse", "Waiting Warehouse"),
            ("waiting_accounting", "Waiting Accounting"),
            ("waiting_partner", "Waiting Partner"),
            ("approved", "Approved"),
            ("reconciled", "Reconciled"),
            ("locked", "Locked"),
            ("reopened", "Reopened"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
            ("carried_forward", "Carried Forward"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    expected_amount = fields.Monetary(currency_field="currency_id", tracking=True)
    actual_amount = fields.Monetary(currency_field="currency_id", tracking=True)
    variance_amount = fields.Monetary(compute="_compute_variance", store=True)
    expected_quantity = fields.Float(tracking=True)
    actual_quantity = fields.Float(tracking=True)
    variance_quantity = fields.Float(compute="_compute_variance", store=True)
    confidence = fields.Float(digits=(5, 2), tracking=True)
    reason = fields.Text(tracking=True)
    resolution = fields.Text(tracking=True)
    evidence_count = fields.Integer(compute="_compute_evidence_count")
    source_line_ids = fields.One2many("reconcile.case.line", "case_id", copy=True)
    match_run_ids = fields.One2many("reconcile.match.run", "case_id", copy=False)
    transition_ids = fields.One2many("reconcile.case.transition", "case_id", copy=False)
    attachment_ids = fields.Many2many("ir.attachment", string="Evidence")
    approval_date = fields.Datetime(readonly=True, copy=False)
    locked_date = fields.Datetime(readonly=True, copy=False)
    reopened_reason = fields.Text(copy=False)
    active = fields.Boolean(default=True)

    _ALLOWED_TRANSITIONS: ClassVar = {
        "draft": {"collecting_data", "needs_review", "cancelled"},
        "collecting_data": {"auto_matched", "needs_review", "cancelled"},
        "auto_matched": {"approved", "needs_review"},
        "needs_review": {
            "waiting_warehouse",
            "waiting_accounting",
            "waiting_partner",
            "approved",
            "rejected",
            "cancelled",
            "carried_forward",
        },
        "waiting_warehouse": {
            "needs_review",
            "approved",
            "rejected",
            "carried_forward",
        },
        "waiting_accounting": {
            "needs_review",
            "approved",
            "rejected",
            "carried_forward",
        },
        "waiting_partner": {"needs_review", "approved", "rejected", "carried_forward"},
        "approved": {"reconciled", "needs_review"},
        "reconciled": {"locked"},
        "locked": {"reopened"},
        "reopened": {"needs_review", "cancelled"},
        "rejected": {"needs_review", "cancelled"},
        "carried_forward": {"needs_review", "cancelled"},
        "cancelled": set(),
    }

    _sql_constraints = [  # noqa: RUF012
        (
            "name_company_unique",
            "unique(name, company_id)",
            "The case number must be unique per company.",
        ),
    ]

    @api.depends(
        "expected_amount", "actual_amount", "expected_quantity", "actual_quantity"
    )
    def _compute_variance(self):
        for record in self:
            record.variance_amount = record.actual_amount - record.expected_amount
            record.variance_quantity = record.actual_quantity - record.expected_quantity

    def _compute_evidence_count(self):
        for record in self:
            record.evidence_count = len(record.attachment_ids)

    @api.constrains("company_id", "partner_id")
    def _check_partner_company(self):
        for record in self:
            if (
                record.partner_id
                and record.partner_id.company_id
                and record.partner_id.company_id != record.company_id
            ):
                raise ValidationError(_("The partner belongs to another company."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "reconcile.case"
                ) or _("New")
        records = super().create(vals_list)
        for record in records:
            record._record_transition(False, "draft", _("Case created"))
        return records

    def _record_transition(self, from_state, to_state, note=False):
        self.ensure_one()
        self.env["reconcile.case.transition"].create(
            {
                "case_id": self.id,
                "from_state": from_state or "new",
                "to_state": to_state,
                "note": note,
                "user_id": self.env.user.id,
            }
        )

    def _set_state(self, state, note=None):
        self.ensure_one()
        previous = self.state
        if state not in self._ALLOWED_TRANSITIONS.get(previous, set()):
            raise UserError(
                _("The transition from %(from_state)s to %(to_state)s is not allowed.")
                % {"from_state": previous, "to_state": state}
            )
        self.with_context(allow_reconcile_state_write=True).write({"state": state})
        self._record_transition(
            previous, state, note or _("State changed from %s") % previous
        )
        return True

    def write(self, vals):
        if "state" in vals and not self.env.context.get("allow_reconcile_state_write"):
            raise UserError(_("Use the workflow buttons to change the case state."))
        locked = self.filtered(lambda record: record.state == "locked")
        protected = set(vals) - {"reopened_reason", "message_follower_ids"}
        if self.env.context.get("allow_reconcile_state_write"):
            protected.discard("state")
        if locked and protected:
            raise UserError(_("A locked case cannot be edited. Reopen it first."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_open_cases(self):
        if self.filtered(lambda record: record.state not in ("draft", "cancelled")):
            raise UserError(_("Only draft or cancelled cases can be deleted."))

    def action_start_review(self):
        for record in self:
            record._set_state("needs_review", _("Review started"))
        return True

    def action_collect_data(self):
        for record in self:
            record._set_state("collecting_data", _("Data collection started"))
        return True

    def action_wait_warehouse(self):
        for record in self:
            record._set_state("waiting_warehouse", _("Waiting for warehouse"))
        return True

    def action_wait_accounting(self):
        for record in self:
            record._set_state("waiting_accounting", _("Waiting for accounting"))
        return True

    def action_wait_partner(self):
        for record in self:
            record._set_state("waiting_partner", _("Waiting for partner"))
        return True

    def action_return_review(self):
        for record in self:
            record._set_state("needs_review", _("Returned to review"))
        return True

    def action_approve(self):
        self.check_access("write")
        for record in self:
            if record.state not in (
                "needs_review",
                "waiting_warehouse",
                "waiting_accounting",
                "waiting_partner",
                "auto_matched",
            ):
                raise UserError(_("Only reviewed cases can be approved."))
            if self.env.user != record.approver_id and not self.env.user.has_group(
                "account_reconcile_mcp_support.group_reconcile_approver"
            ):
                raise UserError(
                    _(
                        "Only the assigned approver or an approval user can approve this case."
                    )
                )
            record.write({"approval_date": fields.Datetime.now()})
            record._set_state("approved", _("Approved by %s") % self.env.user.name)
        return True

    def action_reject(self):
        if not self.env.user.has_group(
            "account_reconcile_mcp_support.group_reconcile_approver"
        ):
            raise UserError(_("Only an approver can reject a case."))
        for record in self:
            if not record.reason:
                raise UserError(_("Enter a reason before rejecting the case."))
            record._set_state("rejected", record.reason)
        return True

    def action_cancel(self):
        for record in self:
            if not record.reason:
                raise UserError(_("Enter a reason before cancelling the case."))
            record._set_state("cancelled", record.reason)
        return True

    def action_carry_forward(self):
        for record in self:
            if not record.resolution or not record.due_date:
                raise UserError(
                    _("A resolution and a new due date are required to carry forward.")
                )
            record._set_state("carried_forward", record.resolution)
        return True

    def action_run_matching(self):
        for record in self:
            if record.reconcile_type != "bank":
                raise UserError(
                    _("Automatic matching is currently available for bank cases.")
                )
            statement_sources = record.source_line_ids.filtered(
                lambda line: line.source_model == "account.bank.statement.line"
            )
            if not statement_sources:
                raise UserError(
                    _("Add a bank statement line to Source Documents first.")
                )
            if record.state == "draft":
                record._set_state("collecting_data", _("Bank matching started"))
            elif record.state not in ("collecting_data", "needs_review"):
                raise UserError(
                    _("Matching can only run while collecting or reviewing data.")
                )
            statement_line = (
                self.env["account.bank.statement.line"]
                .browse(statement_sources[0].source_res_id)
                .exists()
            )
            statement_line.check_access("read")
            statement_line._create_reconcile_match_run(record)
        return True

    def action_reconcile(self):
        for record in self:
            if record.state != "approved":
                raise UserError(_("Only approved cases can be reconciled."))
            record._set_state("reconciled", _("Reconciliation completed"))
        return True

    def action_lock(self):
        if not self.env.user.has_group(
            "account_reconcile_mcp_support.group_reconcile_period_lock"
        ):
            raise UserError(
                _("You do not have permission to lock reconciliation cases.")
            )
        for record in self:
            if record.state != "reconciled":
                raise UserError(_("Only reconciled cases can be locked."))
            record.write({"locked_date": fields.Datetime.now()})
            record._set_state("locked", _("Locked for audit"))
        return True

    def action_reopen(self):
        if not self.env.user.has_group(
            "account_reconcile_mcp_support.group_reconcile_approver"
        ):
            raise UserError(_("Only an approver can reopen a case."))
        for record in self:
            if record.state != "locked":
                raise UserError(_("Only locked cases can be reopened."))
            if not record.reopened_reason:
                raise UserError(
                    _("A reason is required before reopening a locked case.")
                )
            record._set_state("reopened", record.reopened_reason)
        return True

    @api.model
    def _cron_schedule_overdue_activities(self):
        today = fields.Date.context_today(self)
        overdue = self.search(
            [
                ("due_date", "<", today),
                ("state", "not in", ("reconciled", "locked", "cancelled", "rejected")),
                ("owner_id", "!=", False),
            ]
        )
        activity_type = self.env.ref("mail.mail_activity_data_todo")
        for case in overdue:
            exists = self.env["mail.activity"].search_count(
                [
                    ("res_model", "=", case._name),
                    ("res_id", "=", case.id),
                    ("activity_type_id", "=", activity_type.id),
                    ("summary", "=", _("Overdue reconciliation case")),
                ]
            )
            if not exists:
                case.activity_schedule(
                    activity_type_id=activity_type.id,
                    user_id=case.owner_id.id,
                    date_deadline=today,
                    summary=_("Overdue reconciliation case"),
                    note=_("The reconciliation SLA due date has passed."),
                )


class ReconcileCaseLine(models.Model):
    _name = "reconcile.case.line"
    _description = "Reconciliation Case Source"

    case_id = fields.Many2one(
        "reconcile.case", required=True, ondelete="cascade", index=True
    )
    source_model = fields.Char(required=True)
    source_res_id = fields.Integer(required=True)
    source_line_id = fields.Integer()
    source_role = fields.Selection(
        [
            ("order", "Order"),
            ("receipt", "Receipt"),
            ("delivery", "Delivery"),
            ("invoice", "Invoice"),
            ("payment", "Payment"),
            ("statement", "Statement"),
            ("valuation", "Valuation"),
            ("journal", "Journal"),
        ],
        required=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="case_id.currency_id", store=True
    )
    expected_amount = fields.Monetary(currency_field="currency_id")
    actual_amount = fields.Monetary(currency_field="currency_id")
    expected_quantity = fields.Float()
    actual_quantity = fields.Float()
    note = fields.Text()


class ReconcileCaseTransition(models.Model):
    _name = "reconcile.case.transition"
    _description = "Reconciliation Case State Transition"
    _order = "create_date desc, id desc"

    case_id = fields.Many2one(
        "reconcile.case", required=True, ondelete="cascade", index=True
    )
    from_state = fields.Char(required=True)
    to_state = fields.Char(required=True)
    note = fields.Text()
    user_id = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.user
    )


class ReconcileTolerance(models.Model):
    _name = "reconcile.tolerance"
    _description = "Reconciliation Tolerance"
    _order = "sequence, id"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    case_type = fields.Selection(
        [
            ("bank", "Bank"),
            ("vendor_bill", "Vendor Bill"),
            ("purchase", "Purchase"),
            ("sale", "Sale"),
            ("stock", "Stock"),
            ("period_close", "Period Close"),
        ],
        required=True,
        default="bank",
    )
    partner_id = fields.Many2one("res.partner", check_company=True)
    currency_id = fields.Many2one("res.currency")
    amount_absolute = fields.Monetary(currency_field="currency_id")
    percentage = fields.Float(digits=(5, 2))
    quantity = fields.Float()
    days = fields.Integer()
    approval_level = fields.Selection(
        [
            ("0", "Auto"),
            ("1", "Reviewer"),
            ("2", "Approver"),
            ("3", "CFO / Delegate"),
        ],
        default="1",
        required=True,
    )
    writeoff_account_id = fields.Many2one("account.account", check_company=True)

    @api.constrains("amount_absolute", "percentage", "quantity", "days")
    def _check_non_negative(self):
        for record in self:
            if (
                min(
                    record.amount_absolute,
                    record.percentage,
                    record.quantity,
                    record.days,
                )
                < 0
            ):
                raise ValidationError(_("Tolerance values cannot be negative."))

    @api.model
    def find_applicable(self, case):
        """Return the most specific active tolerance for a case."""
        domain = [
            ("active", "=", True),
            ("company_id", "=", case.company_id.id),
            ("case_type", "=", case.reconcile_type),
            ("partner_id", "in", [False, case.partner_id.id]),
            ("currency_id", "in", [False, case.currency_id.id]),
        ]
        tolerances = self.search(domain)
        return tolerances.sorted(
            key=lambda tolerance: (
                not bool(tolerance.partner_id),
                not bool(tolerance.currency_id),
                tolerance.sequence,
                tolerance.id,
            )
        )[:1]

    def case_is_within_tolerance(self, case):
        self.ensure_one()
        amount_ok = (
            not self.amount_absolute
            or abs(case.variance_amount) <= self.amount_absolute
        )
        percentage = (
            abs(case.variance_amount) / abs(case.expected_amount) * 100
            if case.expected_amount
            else 0.0
        )
        percentage_ok = not self.percentage or percentage <= self.percentage
        quantity_ok = not self.quantity or abs(case.variance_quantity) <= self.quantity
        return amount_ok and percentage_ok and quantity_ok


class ReconcileMatchRun(models.Model):
    _name = "reconcile.match.run"
    _description = "Reconciliation Matching Run"
    _order = "create_date desc, id desc"

    name = fields.Char(
        required=True, copy=False, default=lambda self: _("Matching Run")
    )
    case_id = fields.Many2one(
        "reconcile.case", required=True, ondelete="cascade", index=True
    )
    algorithm_version = fields.Char(required=True, default="v1")
    input_hash = fields.Char(index=True)
    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ],
        default="draft",
        required=True,
    )
    candidate_count = fields.Integer(compute="_compute_candidate_count")
    candidate_ids = fields.One2many("reconcile.match.candidate", "run_id")
    error_message = fields.Text()

    def _compute_candidate_count(self):
        for record in self:
            record.candidate_count = len(record.candidate_ids)


class ReconcileMatchCandidate(models.Model):
    _name = "reconcile.match.candidate"
    _description = "Reconciliation Match Candidate"
    _order = "score desc, id"

    run_id = fields.Many2one(
        "reconcile.match.run", required=True, ondelete="cascade", index=True
    )
    source_model = fields.Char(required=True)
    source_res_id = fields.Integer(required=True)
    target_model = fields.Char(required=True)
    target_res_id = fields.Integer(required=True)
    match_kind = fields.Selection(
        [
            ("one_to_one", "One to One"),
            ("one_to_many", "One to Many"),
            ("many_to_one", "Many to One"),
            ("partial", "Partial"),
        ],
        default="one_to_one",
        required=True,
    )
    source_payload = fields.Json(default=list)
    target_payload = fields.Json(default=list)
    score = fields.Float(digits=(5, 2), index=True)
    score_breakdown = fields.Json(default=dict)
    decision = fields.Selection(
        [
            ("suggested", "Suggested"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
        ],
        default="suggested",
        required=True,
    )
    override_reason = fields.Text()

    def action_accept(self):
        for candidate in self:
            candidate.run_id.candidate_ids.filtered(
                lambda other, current=candidate: (
                    other != current and other.decision == "accepted"
                )
            ).write({"decision": "suggested"})
            candidate.decision = "accepted"
        return True

    def action_reject(self):
        for candidate in self:
            if not candidate.override_reason:
                raise UserError(
                    _("Enter an override reason before rejecting a candidate.")
                )
            candidate.decision = "rejected"
        return True
