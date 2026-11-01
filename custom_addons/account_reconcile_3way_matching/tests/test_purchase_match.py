from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class TestPurchaseMatch(AccountTestInvoicingCommon):
    def test_bill_without_purchase_order_creates_exception(self):
        bill = self.init_invoice(
            "in_invoice",
            products=self.product_a,
            invoice_date="2026-09-10",
        )
        match = self.env["reconcile.purchase.match"].create(
            {"name": "Missing PO", "bill_id": bill.id}
        )
        match.action_compute()
        self.assertEqual(match.state, "exception")
        self.assertEqual(match.line_ids.result, "missing_po")
