from odoo.exceptions import UserError

from odoo import _, fields, models


class ReconcileMatchMixin(models.AbstractModel):
    _name = "account.reconcile.match.mixin"
    _description = "Common Reconciliation Match Behavior"

    case_id = fields.Many2one("reconcile.case", readonly=True, copy=False)

    def _validate_match_source(self):
        raise NotImplementedError

    def _prepare_match_line_values(self):
        raise NotImplementedError

    def _match_line_model(self):
        raise NotImplementedError

    def _get_computed_match_state(self):
        raise NotImplementedError

    def _prepare_case_values(self):
        raise NotImplementedError

    def action_compute(self):
        for record in self:
            record._validate_match_source()
            record.line_ids.unlink()
            values = record._prepare_match_line_values()
            if values:
                self.env[record._match_line_model()].create(values)
            record.state = record._get_computed_match_state()
            if record.state == "exception":
                record.action_create_case()
        return True

    def action_create_case(self):
        for record in self.filtered(lambda match: not match.case_id):
            record.case_id = self.env["reconcile.case"].create(
                record._prepare_case_values()
            )
        return True

    def action_approve(self):
        if not self.env.user.has_group(
            "account_reconcile_mcp_support.group_reconcile_approver"
        ):
            raise UserError(_("Only a reconciliation approver can approve this match."))
        self.write({"state": "approved"})
        return True


class AccountMove(models.Model):
    _inherit = "account.move"

    def _open_reconcile_match(
        self,
        *,
        match_field,
        match_model,
        source_field,
        match_name,
        action_name,
    ):
        self.ensure_one()
        match = self[match_field]
        if not match:
            match = self.env[match_model].create(
                {
                    "name": match_name % self.display_name,
                    source_field: self.id,
                }
            )
            self[match_field] = match
            match.action_compute()
        return {
            "type": "ir.actions.act_window",
            "name": action_name,
            "res_model": match_model,
            "res_id": match.id,
            "view_mode": "form",
            "target": "current",
        }
