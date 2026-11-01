import hashlib
import itertools
import re

from odoo.tools import float_compare

from odoo import _, api, fields, models


def _normalize_reference(value):
    return re.sub(r"[^0-9A-Z]", "", (value or "").upper())


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    reconcile_fingerprint = fields.Char(copy=False, index=True, readonly=True)
    duplicate_suspected = fields.Boolean(copy=False, readonly=True, index=True)
    duplicate_of_id = fields.Many2one(
        "account.bank.statement.line", copy=False, readonly=True, check_company=True
    )
    import_batch_id = fields.Many2one(
        "reconcile.import.batch", copy=False, readonly=True, index=True
    )

    @api.model
    def _fingerprint_values(self, values):
        journal = self.env["account.journal"].browse(values.get("journal_id"))
        parts = [
            str(journal.company_id.id or self.env.company.id),
            str(journal.id or ""),
            str(values.get("date") or ""),
            str(round(float(values.get("amount") or 0.0), 6)),
            _normalize_reference(values.get("payment_ref")),
            _normalize_reference(values.get("account_number")),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            values["reconcile_fingerprint"] = self._fingerprint_values(values)
            duplicate = self.search(
                [("reconcile_fingerprint", "=", values["reconcile_fingerprint"])],
                limit=1,
            )
            if duplicate:
                values.update(
                    {"duplicate_suspected": True, "duplicate_of_id": duplicate.id}
                )
        return super().create(vals_list)

    def _create_reconcile_match_run(self, case):
        self.ensure_one()
        normalized_ref = _normalize_reference(self.payment_ref)
        amount = abs(self.amount)
        candidates = self.env["account.move.line"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("parent_state", "=", "posted"),
                ("reconciled", "=", False),
                ("amount_residual", "!=", 0),
                ("account_type", "in", ("asset_receivable", "liability_payable")),
                ("statement_line_id", "!=", self.id),
            ],
            limit=200,
        )
        run = self.env["reconcile.match.run"].create(
            {
                "name": _("Bank match for %s") % self.display_name,
                "case_id": case.id,
                "algorithm_version": "bank-v1",
                "input_hash": self.reconcile_fingerprint,
            }
        )
        values = []
        scored_lines = []
        for line in candidates:
            score = 0.0
            breakdown = {}
            rounding = line.company_currency_id.rounding
            if (
                float_compare(
                    abs(line.amount_residual), amount, precision_rounding=rounding
                )
                == 0
            ):
                score += 35
                breakdown["amount"] = 35
            move_refs = " ".join(
                filter(None, (line.move_name, line.move_id.ref, line.name))
            )
            if normalized_ref and any(
                token and token in normalized_ref
                for token in map(_normalize_reference, move_refs.split())
                if len(token) >= 4
            ):
                score += 40
                breakdown["reference"] = 40
            if self.partner_id and self.partner_id == line.partner_id:
                score += 15
                breakdown["partner"] = 15
            candidate_date = line.date_maturity or line.date
            day_difference = abs((self.date - candidate_date).days)
            if day_difference <= 7:
                date_score = max(0, 10 - day_difference)
                score += date_score
                breakdown["date"] = date_score
            if score:
                scored_lines.append((line, score, breakdown))
                values.append(
                    {
                        "run_id": run.id,
                        "source_model": self._name,
                        "source_res_id": self.id,
                        "target_model": line._name,
                        "target_res_id": line.id,
                        "score": score,
                        "score_breakdown": breakdown,
                        "match_kind": "one_to_one",
                        "source_payload": [self.id],
                        "target_payload": [line.id],
                    }
                )
        values.extend(self._combination_candidates(run, scored_lines, amount))
        values.extend(self._many_payment_candidates(run, scored_lines, amount))
        if values:
            self.env["reconcile.match.candidate"].create(values)
        run.status = "completed"
        if values and max(item["score"] for item in values) >= 95:
            case._set_state("auto_matched", _("High-confidence bank match found"))
        elif case.state == "collecting_data":
            case._set_state("needs_review", _("Bank matching completed"))
        return run

    def _combination_candidates(self, run, scored_lines, statement_amount):
        """Suggest one bank line against two or three open-item lines."""
        self.ensure_one()
        values = []
        ranked = sorted(scored_lines, key=lambda item: item[1], reverse=True)[:12]
        top_lines = [item[0] for item in ranked]
        for size in (2, 3):
            for combination in itertools.combinations(top_lines, size):
                if len({line.currency_id for line in combination}) > 1:
                    continue
                if (
                    len({line.partner_id for line in combination if line.partner_id})
                    > 1
                ):
                    continue
                total = sum(abs(line.amount_residual) for line in combination)
                if float_compare(
                    total,
                    statement_amount,
                    precision_rounding=self.company_currency_id.rounding,
                ):
                    continue
                values.append(
                    {
                        "run_id": run.id,
                        "source_model": self._name,
                        "source_res_id": self.id,
                        "target_model": "account.move.line",
                        "target_res_id": combination[0].id,
                        "score": 85.0,
                        "score_breakdown": {
                            "combined_amount": 55,
                            "candidate_count": size,
                            "same_partner": 20,
                            "same_currency": 10,
                        },
                        "match_kind": "one_to_many",
                        "source_payload": [self.id],
                        "target_payload": [line.id for line in combination],
                    }
                )
                if len(values) >= 20:
                    return values
        return values

    def _many_payment_candidates(self, run, scored_lines, statement_amount):
        """Suggest multiple bank lines against one open item."""
        self.ensure_one()
        values = []
        other_statements = self.search(
            [
                ("id", "!=", self.id),
                ("company_id", "=", self.company_id.id),
                ("journal_id", "=", self.journal_id.id),
                ("is_reconciled", "=", False),
                ("date", "<=", self.date),
            ],
            order="date desc, id desc",
            limit=30,
        )
        ranked = sorted(scored_lines, key=lambda item: item[1], reverse=True)[:12]
        for target_line, _score, _breakdown in ranked:
            target_amount = abs(target_line.amount_residual)
            for other in other_statements:
                if float_compare(
                    statement_amount + abs(other.amount),
                    target_amount,
                    precision_rounding=self.company_currency_id.rounding,
                ):
                    continue
                values.append(
                    {
                        "run_id": run.id,
                        "source_model": self._name,
                        "source_res_id": self.id,
                        "target_model": target_line._name,
                        "target_res_id": target_line.id,
                        "score": 80.0,
                        "score_breakdown": {
                            "combined_amount": 55,
                            "source_count": 2,
                            "same_journal": 10,
                            "date": 15,
                        },
                        "match_kind": "many_to_one",
                        "source_payload": [self.id, other.id],
                        "target_payload": [target_line.id],
                    }
                )
                if len(values) >= 20:
                    return values
        return values
