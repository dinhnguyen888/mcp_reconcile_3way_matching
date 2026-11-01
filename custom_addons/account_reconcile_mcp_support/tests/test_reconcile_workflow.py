from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase


class TestReconcileWorkflow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.admin = cls.env.ref("base.user_admin")
        cls.approver_group = cls.env.ref(
            "account_reconcile_mcp_support.group_reconcile_approver"
        )
        cls.lock_group = cls.env.ref(
            "account_reconcile_mcp_support.group_reconcile_period_lock"
        )
        cls.admin.groups_id |= cls.approver_group | cls.lock_group
        cls.Case = cls.env["reconcile.case"]

    def test_case_lifecycle_and_audit(self):
        case = self.Case.create(
            {
                "reconcile_type": "bank",
                "company_id": self.company.id,
                "expected_amount": 100,
                "actual_amount": 101,
                "approver_id": self.admin.id,
            }
        )
        self.assertEqual(case.variance_amount, 1)
        self.assertEqual(case.state, "draft")
        self.assertTrue(case.transition_ids)
        case.action_start_review()
        case.with_user(self.admin).action_approve()
        case.action_reconcile()
        case.with_user(self.admin).action_lock()
        self.assertEqual(case.state, "locked")

        case.reopened_reason = "Bank reference correction"
        case.with_user(self.admin).action_reopen()
        self.assertEqual(case.state, "reopened")

    def test_case_cannot_reconcile_before_approval(self):
        case = self.Case.create(
            {"reconcile_type": "vendor_bill", "company_id": self.company.id}
        )
        with self.assertRaises(UserError):
            case.action_reconcile()

    def test_tolerance_rejects_negative_values(self):
        with self.assertRaises(ValidationError):
            self.env["reconcile.tolerance"].create(
                {
                    "name": "Invalid",
                    "company_id": self.company.id,
                    "amount_absolute": -1,
                }
            )

    def test_close_checklist_requires_no_failed_line(self):
        checklist = self.env["reconcile.close.checklist"].create(
            {
                "name": "September 2026",
                "company_id": self.company.id,
                "date_from": "2026-09-01",
                "date_to": "2026-09-30",
                "line_ids": [(0, 0, {"check_key": "open_cases", "state": "failed"})],
            }
        )
        checklist.action_start()
        with self.assertRaises(UserError):
            checklist.action_submit()

    def test_partner_confirmation_difference_creates_case(self):
        partner = self.env["res.partner"].create({"name": "Confirmation Partner"})
        confirmation = self.env["reconcile.partner.confirmation"].create(
            {
                "partner_id": partner.id,
                "date_from": "2026-09-01",
                "date_to": "2026-09-30",
                "closing_balance": 100,
                "partner_confirmed_balance": 90,
                "state": "sent",
                "attachment_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "confirmation.txt",
                            "datas": b"Y29uZmlybWVk",
                        },
                    )
                ],
            }
        )
        confirmation.action_record_response()
        self.assertEqual(confirmation.state, "difference")
        self.assertTrue(confirmation.case_id)
        self.assertEqual(confirmation.case_id.state, "needs_review")
