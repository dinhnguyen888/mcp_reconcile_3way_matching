from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class TestSaleMatch(AccountTestInvoicingCommon):
    def test_invoice_without_sales_order_creates_exception(self):
        invoice = self.init_invoice(
            "out_invoice",
            products=self.product_a,
            invoice_date="2026-09-10",
        )
        match = self.env["reconcile.sale.match"].create(
            {"name": "Missing SO", "invoice_id": invoice.id}
        )
        match.action_compute()
        self.assertEqual(match.state, "exception")
        self.assertEqual(match.line_ids.result, "missing_sale_order")
