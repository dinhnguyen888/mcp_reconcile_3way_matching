from odoo.exceptions import UserError

from odoo import _, api, fields, models


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    import_batch_id = fields.Many2one(
        "reconcile.import.batch",
        string="Import Batch",
        ondelete="set null",
        index=True,
        readonly=True,
    )

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

    @api.model
    def _find_statement_partner(self, partner_name):
        partner_name = (partner_name or "").strip()
        if not partner_name:
            return self.env["res.partner"]
        return self.env["res.partner"].search(
            [
                "|",
                ("name", "ilike", partner_name),
                ("vat", "=", partner_name),
            ],
            limit=1,
        )

    @api.model
    def _prepare_bank_statement_line_values(
        self, transaction, journal, import_batch=None
    ):
        partner_id = transaction.get("partner_id")
        partner = (
            self.env["res.partner"].browse(partner_id).exists()
            if partner_id
            else self.env["res.partner"]
        )
        if not partner:
            partner = self._find_statement_partner(transaction.get("partner_name"))
        narration = (
            transaction.get("narration")
            or transaction.get("description")
            or transaction.get("label")
            or ""
        ).strip()
        reference = (transaction.get("ref") or "").strip()
        if reference and narration and reference not in narration:
            payment_reference = f"{reference} - {narration}"
        else:
            payment_reference = narration or reference or _(
                "AI extracted bank statement transaction"
            )
        return {
            "journal_id": journal.id,
            "date": transaction.get("date") or fields.Date.context_today(self),
            "payment_ref": payment_reference,
            "amount": float(transaction.get("amount") or 0.0),
            "partner_id": partner.id or False,
            "account_number": transaction.get("account_number") or False,
            "import_batch_id": import_batch.id if import_batch else False,
        }
    @api.model
    def import_bank_statement_lines_from_ai(self, transactions, journal_id=None, company_id=None):
        company = self.env["res.company"].browse(company_id) if company_id else self.env.company
        model = self.with_company(company)

        Journal = model.env["account.journal"]
        if not journal_id:
            journal = Journal.search([
                ("company_id", "=", company.id),
                ("type", "=", "bank"),
            ], limit=1)
        else:
            journal = Journal.browse(journal_id)

        if not journal:
            raise UserError(_("No suitable Bank Journal found!"))

        values = [
            self._prepare_bank_statement_line_values(transaction, journal)
            for transaction in transactions
        ]
        created_lines = self.create(values)

        return {
            "journal_id": journal.id,
            "journal_name": journal.name,
            "count": len(created_lines),
            "line_ids": created_lines.ids,
        }
