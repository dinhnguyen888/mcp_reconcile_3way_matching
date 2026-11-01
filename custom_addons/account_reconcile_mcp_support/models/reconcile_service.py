# -*- coding: utf-8 -*-
import logging
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


def _sanitize_vat(vat):
    return re.sub(r"[^0-9A-Za-z]", "", vat or "")


class McpReconcileService(models.AbstractModel):
    _name = "mcp.reconcile.service"
    _description = "Service handling AI & MCP Vendor Bill Creation and Bank Reconcile"

    def _find_vendor_partner(self, bill_data, create_if_missing=False):
        Partner = self.env["res.partner"]
        vendor_vat = _sanitize_vat(bill_data.get("vendor_vat"))
        vendor_name = (bill_data.get("vendor_name") or "").strip()
        partner = self.env["res.partner"]

        if vendor_vat:
            partners = Partner.search([("vat", "ilike", vendor_vat)], limit=20)
            exact = partners.filtered(lambda p: _sanitize_vat(p.vat) == vendor_vat)
            partner = exact[:1] or partners[:1]
        if not partner and vendor_name:
            partner = Partner.search([("name", "=ilike", vendor_name)], limit=1)
            if not partner:
                partner = Partner.search([("name", "ilike", vendor_name)], limit=1)

        if not partner and create_if_missing:
            partner = Partner.create({
                "name": vendor_name or _("Vendor from scanned bill"),
                "vat": bill_data.get("vendor_vat") or False,
                "street": bill_data.get("vendor_street") or False,
                "city": bill_data.get("vendor_city") or False,
                "phone": bill_data.get("vendor_phone") or False,
                "email": bill_data.get("vendor_email") or False,
                "supplier_rank": 1,
                "company_id": False,
            })
            _logger.info("AI created new vendor: %s (VAT: %s)", partner.name, partner.vat)
        return partner

    def find_existing_vendor_bill(self, bill_data, company=None):
        """Match an existing vendor bill by invoice number + vendor, then amount."""
        company = company or self.env.company
        invoice_number = (bill_data.get("invoice_number") or "").strip()
        if not invoice_number:
            return self.env["account.move"]

        Move = self.env["account.move"]
        domain = [
            ("company_id", "=", company.id),
            ("move_type", "in", ("in_invoice", "in_refund")),
            ("state", "!=", "cancel"),
            "|",
            ("ref", "=ilike", invoice_number),
            ("payment_reference", "=ilike", invoice_number),
        ]
        bills = Move.search(domain, order="state, id desc")
        partner = self._find_vendor_partner(bill_data, create_if_missing=False)
        if partner:
            by_partner = bills.filtered(lambda b: b.partner_id.id == partner.id)
            if by_partner:
                bills = by_partner
        amount = float(bill_data.get("amount_total") or 0.0)
        if amount and len(bills) > 1:
            tight = bills.filtered(
                lambda b: float_compare(
                    b.amount_total, amount, precision_rounding=b.currency_id.rounding
                ) == 0
            )
            if tight:
                bills = tight
        posted = bills.filtered(lambda b: b.state == "posted")
        return (posted or bills)[:1]

    def _prepare_invoice_lines(self, bill_data, company):
        Product = self.env["product.product"]
        Tax = self.env["account.tax"]
        invoice_lines = []
        for line in bill_data.get("lines") or []:
            prod_name = (line.get("name") or "").strip()
            prod_code = (line.get("default_code") or "").strip()
            qty = float(line.get("quantity") or 1.0) or 1.0
            price_unit = float(line.get("price_unit") or 0.0)
            tax_rate = float(line.get("tax_rate") or 0.0)

            product = Product.browse()
            if prod_code:
                product = Product.search([("default_code", "=", prod_code)], limit=1)
            if not product and prod_name:
                product = Product.search([("name", "=ilike", prod_name)], limit=1)

            line_vals = {
                "name": prod_name or (product.name if product else _("Goods / Services")),
                "quantity": qty,
                "price_unit": price_unit,
            }
            if product:
                line_vals["product_id"] = product.id
            if tax_rate:
                tax = Tax.search([
                    ("company_id", "=", company.id),
                    ("type_tax_use", "=", "purchase"),
                    ("amount", "=", tax_rate),
                ], limit=1)
                if tax:
                    line_vals["tax_ids"] = [(6, 0, [tax.id])]
            invoice_lines.append((0, 0, line_vals))

        if not invoice_lines:
            price = float(bill_data.get("amount_untaxed") or bill_data.get("amount_total") or 0.0)
            invoice_lines.append((0, 0, {
                "name": bill_data.get("invoice_number") or _("Scanned vendor bill"),
                "quantity": 1,
                "price_unit": price,
            }))
        return invoice_lines

    def _attach_bill_pdf(self, bill, pdf_base64=None, filename=None):
        if not pdf_base64 or not bill:
            return
        attachment_name = filename or f"Bill_{bill.ref or bill.id}.pdf"
        existing = self.env["ir.attachment"].search([
            ("res_model", "=", "account.move"),
            ("res_id", "=", bill.id),
            ("name", "=", attachment_name),
        ], limit=1)
        if existing:
            if not bill.message_main_attachment_id:
                bill.message_main_attachment_id = existing.id
            return existing
        mimetype = "application/pdf"
        lower = (filename or "").lower()
        if lower.endswith(".png"):
            mimetype = "image/png"
        elif lower.endswith((".jpg", ".jpeg")):
            mimetype = "image/jpeg"
        attachment = self.env["ir.attachment"].create({
            "name": attachment_name,
            "type": "binary",
            "datas": pdf_base64,
            "res_model": "account.move",
            "res_id": bill.id,
            "mimetype": mimetype,
        })
        bill.message_main_attachment_id = attachment.id
        return attachment

    def _bill_form_action(self, bill, matched=False):
        return {
            "name": _("Existing Vendor Bill") if matched else _("Draft Vendor Bill"),
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": bill.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
            "context": {"default_move_type": "in_invoice"},
        }

    def _bill_action(self, bill, matched=False):
        if matched:
            title = _("Existing Vendor Bill")
            message = _("This vendor bill is already in the system: %s") % (
                bill.ref or bill.display_name
            )
            ntype = "info"
        else:
            title = _("Draft Vendor Bill")
            message = _("Filled a draft vendor bill from the scan: %s") % (
                bill.ref or bill.display_name
            )
            ntype = "success"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": ntype,
                "sticky": False,
                "next": self._bill_form_action(bill, matched=matched),
            },
        }

    def _serialize_bill(self, bill, matched=False):
        return {
            "bill_id": bill.id,
            "bill_name": bill.name,
            "ref": bill.ref,
            "vendor": bill.partner_id.display_name,
            "amount_untaxed": bill.amount_untaxed,
            "amount_tax": bill.amount_tax,
            "amount_total": bill.amount_total,
            "status": bill.state,
            "matched": matched,
        }

    def _fill_draft_bill(self, bill, bill_data, company, pdf_base64=None, filename=None):
        partner = self._find_vendor_partner(bill_data, create_if_missing=True)
        journal = self.env["account.journal"].search([
            ("company_id", "=", company.id),
            ("type", "=", "purchase"),
        ], limit=1)
        invoice_lines = self._prepare_invoice_lines(bill_data, company)
        invoice_number = bill_data.get("invoice_number") or bill.ref or False
        vals = {
            "partner_id": partner.id if partner else False,
            "invoice_date": bill_data.get("invoice_date") or fields.Date.context_today(self),
            "ref": invoice_number,
            "payment_reference": invoice_number or bill.payment_reference,
        }
        due = bill_data.get("due_date") or False
        if due:
            vals["invoice_date_due"] = due
        if journal and not bill.journal_id:
            vals["journal_id"] = journal.id
        if bill.invoice_line_ids:
            bill.invoice_line_ids.unlink()
        vals["invoice_line_ids"] = invoice_lines
        bill.write(vals)
        self._attach_bill_pdf(bill, pdf_base64, filename)
        return bill

    @api.model
    def find_or_create_vendor_bill_from_ai(self, bill_data, pdf_base64=None, filename=None, fill_move=None):
        """
        After MCP scan: return an existing vendor bill when invoice number matches,
        otherwise fill `fill_move` (draft) or create a new draft bill.
        """
        company_id = bill_data.get("company_id")
        company = self.env["res.company"].browse(company_id) if company_id else self.env.company
        if not company.exists():
            company = self.env.company
        self = self.with_company(company)

        existing = self.find_existing_vendor_bill(bill_data, company=company)
        if existing:
            if existing.state == "draft":
                self._fill_draft_bill(existing, bill_data, company, pdf_base64, filename)
            else:
                self._attach_bill_pdf(existing, pdf_base64, filename)
            _logger.info(
                "Matched existing vendor bill %s (ref %s, state %s) from scan %s",
                existing.name, existing.ref, existing.state, filename,
            )
            return self._serialize_bill(existing, matched=True)

        if fill_move and fill_move.exists() and fill_move.move_type in ("in_invoice", "in_refund") and fill_move.state == "draft":
            bill = self._fill_draft_bill(fill_move, bill_data, company, pdf_base64, filename)
            return self._serialize_bill(bill, matched=False)

        return self.create_vendor_bill_from_ai(bill_data, pdf_base64=pdf_base64, filename=filename)

    @api.model
    def create_vendor_bill_from_ai(self, bill_data, pdf_base64=None, filename=None):
        """Create Vendor Bill in DRAFT from AI-extracted structured data."""
        company_id = bill_data.get("company_id")
        company = self.env["res.company"].browse(company_id) if company_id else self.env.company
        if not company.exists():
            company = self.env.company
        self = self.with_company(company)

        partner = self._find_vendor_partner(bill_data, create_if_missing=True)
        invoice_lines = self._prepare_invoice_lines(bill_data, company)
        journal = self.env["account.journal"].search([
            ("company_id", "=", company.id),
            ("type", "=", "purchase"),
        ], limit=1)

        invoice_number = bill_data.get("invoice_number") or False
        move_vals = {
            "move_type": "in_invoice",
            "partner_id": partner.id if partner else False,
            "invoice_date": bill_data.get("invoice_date") or fields.Date.context_today(self),
            "ref": invoice_number,
            "payment_reference": invoice_number,
            "company_id": company.id,
            "invoice_line_ids": invoice_lines,
        }
        due = bill_data.get("due_date") or False
        if due:
            move_vals["invoice_date_due"] = due
        if journal:
            move_vals["journal_id"] = journal.id

        bill = self.env["account.move"].create(move_vals)
        self._attach_bill_pdf(bill, pdf_base64, filename)
        _logger.info("Draft vendor bill created: %s (Ref: %s)", bill.name, bill.ref)
        return self._serialize_bill(bill, matched=False)

    @api.model
    def import_bank_statement_lines_from_ai(self, transactions, journal_id=None, company_id=None):
        if company_id:
            company = self.env["res.company"].browse(company_id)
        else:
            company = self.env.company
        self = self.with_company(company)

        Journal = self.env["account.journal"]
        if not journal_id:
            journal = Journal.search([
                ("company_id", "=", company.id),
                ("type", "=", "bank"),
            ], limit=1)
        else:
            journal = Journal.browse(journal_id)

        if not journal:
            raise UserError(_("No suitable Bank Journal found!"))

        created_lines = []
        Partner = self.env["res.partner"]
        StatementLine = self.env["account.bank.statement.line"]

        for txn in transactions:
            date = txn.get("date") or fields.Date.context_today(self)
            payment_ref = txn.get("description") or txn.get("label") or "Bank statement transaction"
            amount = float(txn.get("amount", 0.0))
            partner_name = (txn.get("partner_name") or "").strip()
            partner = Partner.search([("name", "ilike", partner_name)], limit=1) if partner_name else Partner.browse()
            stmt_line = StatementLine.create({
                "journal_id": journal.id,
                "date": date,
                "payment_ref": payment_ref,
                "amount": amount,
                "partner_id": partner.id if partner else False,
            })
            created_lines.append(stmt_line)

        return {
            "journal_id": journal.id,
            "journal_name": journal.name,
            "count": len(created_lines),
            "line_ids": [l.id for l in created_lines],
        }
