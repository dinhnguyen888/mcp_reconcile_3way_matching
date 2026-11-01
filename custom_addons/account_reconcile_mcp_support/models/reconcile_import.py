import hashlib

from odoo.exceptions import ValidationError

from odoo import _, api, fields, models


class ReconcileImportBatch(models.Model):
    _name = "reconcile.import.batch"
    _description = "Reconciliation Document Import Batch"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, copy=False)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    journal_id = fields.Many2one("account.journal", check_company=True)
    attachment_id = fields.Many2one("ir.attachment", required=True, ondelete="restrict")
    document_type = fields.Selection(
        [("bank_statement", "Bank Statement"), ("vendor_bill", "Vendor Bill")],
        required=True,
    )
    checksum = fields.Char(required=True, index=True, copy=False)
    state = fields.Selection(
        [
            ("processing", "Processing"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ],
        default="processing",
        required=True,
        index=True,
    )
    imported_count = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)
    statement_line_ids = fields.One2many(
        "account.bank.statement.line", "import_batch_id", readonly=True
    )

    @api.model
    def checksum_for(self, document):
        if isinstance(document, str):
            document = document.encode()
        return hashlib.sha256(document or b"").hexdigest()

    @api.constrains("checksum", "company_id", "document_type", "journal_id", "state")
    def _check_completed_duplicate(self):
        for batch in self:
            if batch.document_type != "bank_statement":
                continue
            duplicate = self.search_count(
                [
                    ("id", "!=", batch.id),
                    ("company_id", "=", batch.company_id.id),
                    ("document_type", "=", batch.document_type),
                    ("journal_id", "=", batch.journal_id.id),
                    ("checksum", "=", batch.checksum),
                    ("state", "=", "completed"),
                ]
            )
            if duplicate:
                raise ValidationError(
                    _(
                        "This document was already imported successfully for this company and journal."
                    )
                )
