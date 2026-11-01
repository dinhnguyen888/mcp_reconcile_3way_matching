import json

from odoo.tests.common import TransactionCase


class TestBankStatementImport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bank_journal = cls.env["account.journal"].create(
            {
                "name": "MCP Bank",
                "code": "MCPB",
                "type": "bank",
                "company_id": cls.env.company.id,
            }
        )

    def test_preview_values_preserve_coordinates(self):
        preview = self.env["account.statement.ocr.preview"].create(
            {"journal_id": self.bank_journal.id, "company_id": self.env.company.id}
        )
        values = self.env[
            "account.statement.ocr.preview.line"
        ]._prepare_mcp_values(
            preview,
            {
                "description": "Transfer",
                "amount": "125.5",
                "bounding_box": {"box_2d": [10, 20, 30, 40]},
                "field_coordinates": {"amount": [10, 20, 30, 40]},
            },
        )
        self.assertEqual(values["narration"], "Transfer")
        self.assertEqual(values["amount"], 125.5)
        self.assertEqual(
            [
                values["box_ymin"],
                values["box_xmin"],
                values["box_ymax"],
                values["box_xmax"],
            ],
            [10, 20, 30, 40],
        )
        self.assertEqual(
            json.loads(values["field_coords_json"])["amount"], [10, 20, 30, 40]
        )

    def test_statement_line_values_use_shared_mapping(self):
        partner = self.env["res.partner"].create(
            {"name": "Statement Partner", "vat": "STATEMENT-001"}
        )
        values = self.env[
            "mcp.reconcile.service"
        ]._prepare_bank_statement_line_values(
            {
                "date": "2026-09-15",
                "partner_name": partner.vat,
                "ref": "REF-001",
                "narration": "Customer payment",
                "amount": "250",
                "account_number": "0123456789",
            },
            self.bank_journal,
        )
        self.assertEqual(values["partner_id"], partner.id)
        self.assertEqual(values["payment_ref"], "REF-001 - Customer payment")
        self.assertEqual(values["amount"], 250.0)
        self.assertEqual(values["account_number"], "0123456789")
