from odoo.exceptions import UserError

from odoo import _, models


class ReconcileMatchMixin(models.AbstractModel):
    _name = "account.reconcile.match.mixin"
    _description = "Common Reconciliation Match Behavior"

    def _validate_match_source(self):
        raise NotImplementedError

    def _prepare_match_line_values(self):
        raise NotImplementedError

    def _match_line_model(self):
        raise NotImplementedError

    def _get_computed_match_state(self):
        raise NotImplementedError

    def action_compute(self):
        for record in self:
            record._validate_match_source()
            record.line_ids.unlink()
            values = record._prepare_match_line_values()
            if values:
                self.env[record._match_line_model()].create(values)
            record.state = record._get_computed_match_state()
        return True

    def action_approve(self):
        if not self.env.user.has_group("purchase.group_purchase_manager"):
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
