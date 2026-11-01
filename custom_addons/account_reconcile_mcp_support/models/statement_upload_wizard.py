# -*- coding: utf-8 -*-
import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StatementUploadWizard(models.TransientModel):
    _name = "account.statement.upload.wizard"
    _description = "Bank Statement & Vendor Bill Upload Wizard"

    document_type = fields.Selection(
        [
            ("bank_statement", "Bank Statement"),
            ("vendor_bill", "Vendor Bill"),
        ],
        string="Document Type",
        required=True,
        default=lambda self: self.env.context.get("default_document_type") or "bank_statement",
    )
    journal_id = fields.Many2one(
        "account.journal",
        string="Bank Journal",
        domain=[("type", "=", "bank")],
        required=False,
        default=lambda self: self._default_journal_id(),
    )
    document = fields.Binary(string="Document")
    file_name = fields.Char(string="File Name")
    attachment_id = fields.Many2one("ir.attachment", string="Attachment")
    import_batch_id = fields.Many2one("reconcile.import.batch", string="Import Batch")
    move_id = fields.Many2one(
        "account.move",
        string="Target Bill",
        default=lambda self: self.env.context.get("default_move_id"),
    )

    @api.model
    def _default_journal_id(self):
        if self.env.context.get("default_document_type") == "vendor_bill":
            return False
        journal_id = self.env.context.get("default_journal_id") or self.env.context.get("active_id")
        if journal_id and self.env.context.get("active_model") in (None, "account.journal"):
            journal = self.env["account.journal"].browse(journal_id)
            if journal.exists() and journal.type == "bank":
                return journal.id
        return self.env["account.journal"].search([
            ("type", "=", "bank"),
            ("company_id", "=", self.env.company.id),
        ], limit=1).id

    def _ensure_attachment(self):
        self.ensure_one()
        if self.attachment_id:
            return self.attachment_id
        res_model, res_id = "account.journal", self.journal_id.id if self.journal_id else False
        if self.document_type == "vendor_bill":
            res_model, res_id = "account.statement.upload.wizard", self.id
        attachment = self.env["ir.attachment"].create({
            "name": self.file_name or "uploaded_document",
            "type": "binary",
            "datas": self.document,
            "res_model": res_model,
            "res_id": res_id or self.id,
        })
        self.attachment_id = attachment.id
        return attachment

    def _ensure_import_batch(self):
        self.ensure_one()
        if self.import_batch_id:
            return self.import_batch_id
        attachment = self._ensure_attachment()
        Batch = self.env["reconcile.import.batch"]
        batch = Batch.create({
            "name": self.file_name or attachment.name,
            "company_id": self.env.company.id,
            "journal_id": self.journal_id.id if self.journal_id else False,
            "attachment_id": attachment.id,
            "document_type": self.document_type,
            "checksum": Batch.checksum_for(self.document),
        })
        self.import_batch_id = batch.id
        return batch

    def action_process(self):
        """Fallback when JS did not intercept (MCP down or no client assets)."""
        self.ensure_one()
        if not self.document:
            raise UserError(_("Please upload a bank statement or invoice file."))
        self._ensure_attachment()
        self._ensure_import_batch()
        if self.document_type == "vendor_bill":
            raise UserError(_(
                "Local MCP (http://127.0.0.1:8765) is required to scan vendor bills. "
                "Start mcp_client.py on this computer and try again."
            ))
        return self.action_open_preview()

    def action_apply_vendor_bill(self, mcp_data=None):
        """Match an existing vendor bill or fill/create a draft from MCP JSON."""
        self.ensure_one()
        if not self.document:
            raise UserError(_("Please upload a vendor bill file."))
        self._ensure_attachment()
        mcp_data = mcp_data or {}
        bill_data = {}
        if isinstance(mcp_data, dict):
            bill_data = mcp_data.get("bill") or mcp_data.get("result", {}).get("bill") or {}
            if not bill_data and mcp_data.get("document_type") == "vendor_bill":
                bill_data = mcp_data.get("data", {}).get("bill") or {}
        if not bill_data:
            raise UserError(_(
                "MCP did not extract invoice fields from this file. "
                "Check that the document is a vendor bill and that local AI is running."
            ))
        bill_data = dict(bill_data)
        bill_data["company_id"] = self.env.company.id

        fill_move = self.move_id
        if not fill_move:
            ctx_move_id = self.env.context.get("default_move_id") or (
                self.env.context.get("active_id") if self.env.context.get("active_model") == "account.move" else False
            )
            if ctx_move_id:
                fill_move = self.env["account.move"].browse(ctx_move_id)
        if not (fill_move and fill_move.exists() and fill_move.move_type in ("in_invoice", "in_refund") and fill_move.state == "draft"):
            fill_move = self.env["account.move"]

        service = self.env["mcp.reconcile.service"]
        result = service.find_or_create_vendor_bill_from_ai(
            bill_data,
            pdf_base64=self.document,
            filename=self.file_name,
            fill_move=fill_move,
        )
        bill = self.env["account.move"].browse(result["bill_id"])
        if self.attachment_id and bill:
            self.attachment_id.write({"res_model": "account.move", "res_id": bill.id})
        self.import_batch_id.write({"state": "completed", "imported_count": 1})
        return service._bill_action(bill, matched=result.get("matched"))

    def action_open_preview(self, mcp_data=None):
        self.ensure_one()
        if not self.document:
            raise UserError(_("Please upload a bank statement before previewing."))
        if not self.journal_id:
            raise UserError(_("Please select a bank journal."))

        attachment = self._ensure_attachment()
        batch = self._ensure_import_batch()
        records_data = []
        page_images = []
        engine = ""
        message = ""

        if mcp_data and isinstance(mcp_data, dict):
            records_data = mcp_data.get("records") or mcp_data.get("result", {}).get("records") or []
            page_images = mcp_data.get("page_images") or mcp_data.get("result", {}).get("page_images") or []
            engine = mcp_data.get("engine") or ""
            message = mcp_data.get("message") or ""

        if not records_data:
            records_data = self._fallback_pdf_table_records()

        total_pages = 1
        fn = (self.file_name or attachment.name or "").lower()
        if fn.endswith(".pdf") or not fn:
            try:
                import base64
                import fitz
                pdf_bytes = base64.b64decode(self.document)
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                total_pages = len(doc)
            except Exception as e:
                _logger.warning("Could not determine PDF page count: %s", e)
            page_images = []
        elif any(fn.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp"]):
            mime = "image/png" if fn.endswith(".png") else "image/jpeg"
            doc_b64 = self.document.decode("ascii") if isinstance(self.document, bytes) else self.document
            page_images = [f"data:{mime};base64,{doc_b64}"]
            total_pages = 1

        if mcp_data and isinstance(mcp_data, dict) and mcp_data.get("total_pages"):
            total_pages = mcp_data["total_pages"]

        preview = self.env["account.statement.ocr.preview"].create({
            "journal_id": self.journal_id.id,
            "company_id": self.env.company.id,
            "attachment_id": attachment.id,
            "import_batch_id": batch.id,
            "file_name": self.file_name or attachment.name,
            "engine": engine,
            "message": message or _("Extracted %s transactions.") % len(records_data),
            "page_images_json": json.dumps(page_images) if page_images else "[]",
            "raw_text": mcp_data.get("text") if isinstance(mcp_data, dict) else "",
        })

        PreviewLine = self.env["account.statement.ocr.preview.line"]
        line_vals = [
            PreviewLine._prepare_mcp_values(preview, record, index)
            for index, record in enumerate(records_data)
        ]
        if line_vals:
            PreviewLine.create(line_vals)
        preview._compute_totals()

        formatted_lines = [{
            "id": line.id,
            "temp_id": line.temp_id,
            "date": str(line.date),
            "partner_name": line.partner_name or "",
            "account_number": line.account_number or "",
            "ref": line.ref or "",
            "narration": line.narration or "",
            "amount": line.amount,
            "page_number": line.page_number,
        } for line in preview.line_ids]

        return {
            "type": "ir.actions.client",
            "tag": "statement_ocr_preview",
            "target": "new",
            "name": _("AI OCR Statement Preview & Verification - %s") % (self.file_name or ""),
            "params": {
                "preview_id": preview.id,
                "journal_id": self.journal_id.id,
                "journal_name": self.journal_id.name,
                "attachment_id": attachment.id,
                "file_name": self.file_name or attachment.name,
                "engine": engine,
                "message": preview.message,
                "total_credit": preview.total_credit,
                "total_debit": preview.total_debit,
                "total_pages": total_pages,
                "records": formatted_lines,
                "page_images": page_images,
                "pdf_url": f"/web/content/{attachment.id}?download=false",
            },
        }

    def _fallback_pdf_table_records(self):
        try:
            import base64
            import fitz
            from ..client.mcp_client import extract_pdf_table_records, normalize_records
            file_bytes = base64.b64decode(self.document)
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            return normalize_records(extract_pdf_table_records(doc))
        except Exception as e:
            _logger.warning("Local PDF table fallback failed: %s", e)
            return []

    def action_open_manual_create(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "account_reconcile_oca.action_bank_statement_line_create"
        )
        ctx = dict(self.env.context)
        if self.journal_id:
            ctx["default_journal_id"] = self.journal_id.id
        action["context"] = ctx
        return action
