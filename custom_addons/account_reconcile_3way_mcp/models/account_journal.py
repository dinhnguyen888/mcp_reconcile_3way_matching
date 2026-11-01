from odoo.exceptions import UserError, ValidationError

from odoo import _, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    def process_mcp_vendor_bill_uploads(self, uploads):
        """Map dashboard uploads to draft bills and their three-way matches."""
        self.ensure_one()
        self.check_access("read")
        if self.type != "purchase":
            raise UserError(_("MCP vendor bill upload requires a purchase journal."))
        if self.company_id not in self.env.companies:
            raise UserError(_("The purchase journal is outside the allowed companies."))
        if not isinstance(uploads, list) or not uploads:
            raise ValidationError(_("No scanned vendor bill was provided."))
        if len(uploads) > 20:
            raise ValidationError(_("Upload at most 20 vendor bills at a time."))

        action = False
        for upload in uploads:
            action = self._process_mcp_vendor_bill_upload(upload)
        return action

    def _process_mcp_vendor_bill_upload(self, upload):
        self.ensure_one()
        attachment_id = (
            upload.get("attachment_id") if isinstance(upload, dict) else False
        )
        mcp_data = upload.get("mcp_data") if isinstance(upload, dict) else False
        attachment = self.env["ir.attachment"].browse(attachment_id).exists()
        if not attachment:
            raise ValidationError(
                _("The uploaded vendor bill attachment no longer exists.")
            )
        attachment.check_access("read")
        attachment.check_access("write")
        if attachment.res_model or attachment.res_id:
            raise ValidationError(
                _("The uploaded document is already linked to another record.")
            )

        bill_data = self._extract_mcp_bill_data(mcp_data)
        bill_data["company_id"] = self.company_id.id
        move_model = self.env["account.move"].with_company(self.company_id)
        existing_bill = move_model.find_existing_vendor_bill(
            bill_data, company=self.company_id
        )
        if existing_bill and existing_bill.state != "draft":
            raise UserError(
                _(
                    "Vendor bill %(bill)s already exists and is posted. "
                    "The uploaded document was not linked."
                )
                % {"bill": existing_bill.display_name}
            )

        if existing_bill:
            bill = existing_bill
        else:
            result = move_model.create_vendor_bill_from_ai(bill_data)
            bill = move_model.browse(result["bill_id"])

        attachment.write({"res_model": "account.move", "res_id": bill.id})
        if not bill.message_main_attachment_id:
            bill.message_main_attachment_id = attachment
        bill.message_post(
            body=_("Vendor bill document scanned by the local MCP client."),
            attachment_ids=attachment.ids,
        )

        batch_model = self.env["reconcile.import.batch"]
        batch_model.create(
            {
                "name": attachment.name,
                "company_id": self.company_id.id,
                "journal_id": self.id,
                "attachment_id": attachment.id,
                "document_type": "vendor_bill",
                "checksum": batch_model.checksum_for(attachment.datas),
                "state": "completed",
                "imported_count": 1,
            }
        )
        match = bill._ensure_purchase_reconcile_match()
        match.action_compute()
        return match.action_open_workspace()

    def _extract_mcp_bill_data(self, mcp_data):
        if not isinstance(mcp_data, dict):
            raise ValidationError(_("MCP returned an invalid vendor bill response."))
        bill_data = mcp_data.get("bill") or mcp_data.get("result", {}).get("bill")
        if not bill_data and mcp_data.get("document_type") == "vendor_bill":
            bill_data = mcp_data.get("data", {}).get("bill")
        if not isinstance(bill_data, dict) or not bill_data:
            raise ValidationError(
                _("MCP did not extract vendor bill fields from the document.")
            )
        return dict(bill_data)
