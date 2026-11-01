# -*- coding: utf-8 -*-
import json
import logging
from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountStatementOcrPreview(models.TransientModel):
    _name = "account.statement.ocr.preview"
    _description = "AI OCR Statement Preview & Verification"

    journal_id = fields.Many2one(
        "account.journal",
        string="Bank Journal",
        domain=[("type", "=", "bank")],
        required=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    attachment_id = fields.Many2one("ir.attachment", string="Document Attachment")
    import_batch_id = fields.Many2one("reconcile.import.batch", string="Import Batch")
    file_name = fields.Char(string="File Name")
    engine = fields.Char(string="AI Engine", default="gemini-2.5-pro")
    message = fields.Char(string="Status Message")
    total_credit = fields.Float(string="Total Inflow", compute="_compute_totals", store=True)
    total_debit = fields.Float(string="Total Outflow", compute="_compute_totals", store=True)
    total_count = fields.Integer(string="Total Records", compute="_compute_totals", store=True)
    raw_text = fields.Text(string="Raw Document Text")
    page_images_json = fields.Text(string="Rendered Page Images (JSON)")
    line_ids = fields.One2many(
        "account.statement.ocr.preview.line",
        "preview_id",
        string="Statement Lines",
    )

    @api.depends("line_ids.amount")
    def _compute_totals(self):
        for rec in self:
            credit = 0.0
            debit = 0.0
            for line in rec.line_ids:
                if line.amount > 0:
                    credit += line.amount
                else:
                    debit += abs(line.amount)
            rec.total_credit = credit
            rec.total_debit = debit
            rec.total_count = len(rec.line_ids)

    def action_confirm(self, updated_records=None):
        """
        Commit verified preview lines into Odoo account.bank.statement.line.
        Accepts optional updated_records from the frontend client action.
        """
        self.ensure_one()
        StatementLine = self.env["account.bank.statement.line"]
        Partner = self.env["res.partner"]

        # If updated records passed from frontend JSON
        if updated_records and isinstance(updated_records, list):
            self.line_ids.unlink()
            for r in updated_records:
                box_2d = [0, 0, 0, 0]
                if isinstance(r.get("bounding_box"), dict):
                    box_2d = r["bounding_box"].get("box_2d") or [0, 0, 0, 0]
                elif isinstance(r.get("box_2d"), list):
                    box_2d = r.get("box_2d")

                self.env["account.statement.ocr.preview.line"].create({
                    "preview_id": self.id,
                    "temp_id": r.get("temp_id") or "rec",
                    "date": r.get("date") or fields.Date.context_today(self),
                    "partner_name": r.get("partner_name") or "",
                    "account_number": r.get("account_number") or "",
                    "ref": r.get("ref") or "",
                    "narration": r.get("narration") or r.get("description") or "",
                    "amount": float(r.get("amount") or 0.0),
                    "page_number": int(r.get("page_number") or 1),
                    "box_ymin": box_2d[0] if len(box_2d) > 0 else 0,
                    "box_xmin": box_2d[1] if len(box_2d) > 1 else 0,
                    "box_ymax": box_2d[2] if len(box_2d) > 2 else 0,
                    "box_xmax": box_2d[3] if len(box_2d) > 3 else 0,
                    "field_coords_json": json.dumps(r.get("field_coordinates") or {}),
                })

        if not self.line_ids:
            raise UserError(_("No transaction lines to import into bank journal."))

        created_lines = []
        for line in self.line_ids:
            partner = line.partner_id
            if not partner and line.partner_name:
                partner = Partner.search([
                    "|",
                    ("name", "ilike", line.partner_name.strip()),
                    ("vat", "=", line.partner_name.strip()),
                ], limit=1)

            # Build clean payment_ref
            narration = (line.narration or "").strip()
            ref = (line.ref or "").strip()
            if ref and narration and ref not in narration:
                payment_ref = f"{ref} - {narration}"
            elif narration:
                payment_ref = narration
            elif ref:
                payment_ref = ref
            else:
                payment_ref = _("AI extracted bank statement transaction")

            created_line = StatementLine.create({
                "journal_id": self.journal_id.id,
                "date": line.date or fields.Date.context_today(self),
                "payment_ref": payment_ref,
                "amount": line.amount,
                "partner_id": partner.id if partner else False,
                "account_number": line.account_number or False,
                "import_batch_id": self.import_batch_id.id or False,
            })
            created_lines.append(created_line.id)

        if self.import_batch_id:
            self.import_batch_id.write({
                "state": "completed",
                "imported_count": len(created_lines),
            })

        _logger.info(
            "Created %d bank statement lines from AI OCR Preview for journal %s",
            len(created_lines),
            self.journal_id.name,
        )

        # Try to redirect to OCA reconcile view if installed, or standard bank reconcile action
        action = self.env.ref("account_reconcile_oca.action_bank_statement_line_reconcile", raise_if_not_found=False)
        if action:
            res_action = action.read()[0]
            form_view = self.env.ref("account_reconcile_oca.bank_statement_line_form_reconcile_view", raise_if_not_found=False)
            res_action["domain"] = [("journal_id", "=", self.journal_id.id)]
            res_action["context"] = {
                "default_journal_id": self.journal_id.id,
                "active_id": self.journal_id.id,
                "active_ids": [self.journal_id.id],
                "active_model": "account.journal",
                "search_default_not_reconciled": True,
                "view_ref": "account_reconcile_oca.bank_statement_line_form_reconcile_view",
                "form_view_ref": "account_reconcile_oca.bank_statement_line_form_reconcile_view",
            }
            if form_view:
                views = [(v[0], v[1]) for v in res_action.get("views", []) if v[1] != "form"]
                views.append((form_view.id, "form"))
                res_action["views"] = views
            return res_action

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bank Statement Imported"),
                "message": _("Successfully imported %d transactions into bank journal %s.") % (
                    len(created_lines), self.journal_id.name
                ),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class AccountStatementOcrPreviewLine(models.TransientModel):
    _name = "account.statement.ocr.preview.line"
    _description = "AI OCR Statement Preview Line"
    _order = "page_number, id"

    preview_id = fields.Many2one(
        "account.statement.ocr.preview",
        string="Preview Wizard",
        required=True,
        ondelete="cascade",
    )
    temp_id = fields.Char(string="Temp ID", default="rec_001")
    date = fields.Date(string="Date", default=fields.Date.context_today)
    partner_name = fields.Char(string="Partner Name")
    partner_id = fields.Many2one("res.partner", string="Matched Partner")
    account_number = fields.Char(string="Account Number")
    ref = fields.Char(string="Transaction Reference")
    narration = fields.Text(string="Narration")
    amount = fields.Float(string="Amount", default=0.0)
    page_number = fields.Integer(string="Page Number", default=1)
    box_ymin = fields.Integer(string="Box Y Min", default=0)
    box_xmin = fields.Integer(string="Box X Min", default=0)
    box_ymax = fields.Integer(string="Box Y Max", default=0)
    box_xmax = fields.Integer(string="Box X Max", default=0)
    field_coords_json = fields.Text(string="Field Coordinates JSON", default="{}")
