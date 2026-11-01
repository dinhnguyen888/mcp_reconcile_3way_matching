# -*- coding: utf-8 -*-
from odoo import api, models


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    @api.model
    def action_new_line(self):
        """
        Override action_new_line from account_reconcile_oca:
        When the user clicks the 'Create' button on the reconciliation toolbar,
        the system opens the Dropzone statement upload wizard dialog
        instead of the simple manual line form.
        """
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "account_reconcile_mcp_support.action_statement_upload_wizard"
        )
        ctx = dict(self.env.context)
        action["context"] = ctx
        return action

    def action_open_upload_wizard(self):
        """Allows opening the upload wizard from other views"""
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "account_reconcile_mcp_support.action_statement_upload_wizard"
        )
        ctx = dict(self.env.context)
        if self and self.journal_id:
            ctx["default_journal_id"] = self.journal_id.id
        action["context"] = ctx
        return action
