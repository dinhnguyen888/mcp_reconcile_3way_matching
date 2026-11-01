from odoo import _, models


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_open_mcp_bill_upload(self):
        """Open the MCP scan wizard for vendor bills (list or form)."""
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "account_reconcile_mcp_support.action_vendor_bill_upload_wizard"
        )
        ctx = dict(self.env.context)
        ctx["default_document_type"] = "vendor_bill"
        if len(self) == 1 and self.move_type in ("in_invoice", "in_refund"):
            ctx["default_move_id"] = self.id
            ctx["active_id"] = self.id
            ctx["active_ids"] = self.ids
            ctx["active_model"] = "account.move"
        action["context"] = ctx
        action["name"] = _("Import Vendor Bill / Receipt")
        return action
