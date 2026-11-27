"""Tests for woo_payment_doctor."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import woo_payment_doctor as doctor

AS_OF = datetime(2026, 11, 18, tzinfo=timezone.utc)

HEADER = ("payment_id,order_id,gateway,amount,status,error_code,"
          "customer_email,attempt,date\n")


def write_csv(body, directory=None):
    """Write body to a temp CSV file and return its path."""
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False,
        dir=directory, encoding="utf-8")
    handle.write(body)
    handle.close()
    return handle.name


def row(payment_id="P-1", order="O-1", gateway="Stripe",
        amount="49.99", status="succeeded", error="",
        email="ana@example.com", attempt="1",
        date="2026-11-17 09:00"):
    """Build one CSV data row."""
    return ",".join((payment_id, order, gateway, amount, status,
                     error, email, attempt, date)) + "\n"


def run_cli(argv):
    """Run main with argv and capture stdout, return (code, text)."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = doctor.main(argv)
    return code, buffer.getvalue()


def parse_and_audit(body, **kwargs):
    """Parse body and audit it, return (payments, findings)."""
    path = write_csv(HEADER + body)
    try:
        payments, findings = doctor.parse_csv(path, AS_OF)
        findings.extend(doctor.audit(payments, AS_OF, **kwargs))
        return payments, findings
    finally:
        os.unlink(path)


def rules(findings):
    """Return the set of rule names in a findings list."""
    return {item.rule for item in findings}


class TestParseTimestamp(unittest.TestCase):
    def test_iso_with_time(self):
        parsed = doctor.parse_timestamp("2026-11-17 10:00")
        self.assertEqual(parsed, datetime(2026, 11, 17, 10, 0,
                                         tzinfo=timezone.utc))

    def test_iso_with_z(self):
        parsed = doctor.parse_timestamp("2026-11-17T10:00:00Z")
        self.assertEqual(parsed, datetime(2026, 11, 17, 10, 0,
                                         tzinfo=timezone.utc))

    def test_date_only(self):
        parsed = doctor.parse_timestamp("2026-11-17")
        self.assertEqual(parsed, datetime(2026, 11, 17,
                                         tzinfo=timezone.utc))

    def test_day_month_year(self):
        parsed = doctor.parse_timestamp("17/11/2026 09:00")
        self.assertEqual(parsed, datetime(2026, 11, 17, 9, 0,
                                         tzinfo=timezone.utc))

    def test_bad_value(self):
        self.assertIsNone(doctor.parse_timestamp("not-a-date"))

    def test_empty_value(self):
        self.assertIsNone(doctor.parse_timestamp(""))
        self.assertIsNone(doctor.parse_timestamp(None))


class TestParseAmount(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(doctor.parse_amount("49.99"),
                         doctor.Decimal("49.99"))

    def test_currency_symbol(self):
        self.assertEqual(doctor.parse_amount("$1,234.56"),
                         doctor.Decimal("1234.56"))

    def test_integer(self):
        self.assertEqual(doctor.parse_amount("50"),
                         doctor.Decimal("50"))

    def test_bad_value(self):
        self.assertIsNone(doctor.parse_amount("abc"))

    def test_zero(self):
        self.assertIsNone(doctor.parse_amount("0"))

    def test_negative(self):
        self.assertIsNone(doctor.parse_amount("-5.00"))

    def test_empty(self):
        self.assertIsNone(doctor.parse_amount(""))
        self.assertIsNone(doctor.parse_amount(None))


class TestNormalizeStatus(unittest.TestCase):
    def test_success_alias(self):
        self.assertEqual(doctor.normalize_status("success"),
                         "succeeded")

    def test_declined_alias(self):
        self.assertEqual(doctor.normalize_status("declined"),
                         "failed")

    def test_case_insensitive(self):
        self.assertEqual(doctor.normalize_status("Failed"), "failed")

    def test_empty(self):
        self.assertEqual(doctor.normalize_status(""), "")


class TestParseCsv(unittest.TestCase):
    def test_header_aliases(self):
        body = ("id,order,method,total,payment_status,decline_reason,"
                "email,attempt_no,created_at\n"
                "P-1,O-1,Stripe,10.00,failed,card_declined,"
                "a@example.com,1,2026-11-17 09:00\n")
        path = write_csv(body)
        try:
            payments, findings = doctor.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0]["gateway"], "Stripe")
        self.assertEqual(payments[0]["error_code"], "card_declined")
        self.assertEqual(payments[0]["status"], "failed")
        self.assertEqual(findings, [])

    def test_bom_header(self):
        path = write_csv("\ufeff" + HEADER + row())
        try:
            payments, findings = doctor.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertEqual(len(payments), 1)
        self.assertEqual(findings, [])

    def test_missing_payment_id_column(self):
        body = ("order_id,gateway,amount,status,date\n"
                "O-1,Stripe,10.00,succeeded,2026-11-17\n")
        path = write_csv(body)
        try:
            with self.assertRaises(ValueError):
                doctor.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)

    def test_missing_gateway_column(self):
        body = ("payment_id,order_id,amount,status,date\n"
                "P-1,O-1,10.00,succeeded,2026-11-17\n")
        path = write_csv(body)
        try:
            with self.assertRaises(ValueError):
                doctor.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)

    def test_empty_file(self):
        path = write_csv("")
        try:
            with self.assertRaises(ValueError):
                doctor.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)

    def test_missing_file(self):
        with self.assertRaises(ValueError):
            doctor.parse_csv("/nonexistent/payments.csv", AS_OF)

    def test_malformed_row_skipped(self):
        body = row() + "P-2,O-2,Stripe,10.00\n"
        payments, findings = parse_and_audit(body)
        self.assertEqual(len(payments), 1)
        self.assertIn("malformed-row", rules(findings))

    def test_blank_lines_skipped(self):
        body = row() + "\n" + row(payment_id="P-2", order="O-2")
        payments, findings = parse_and_audit(body)
        self.assertEqual(len(payments), 2)
        self.assertEqual(findings, [])

    def test_duplicate_payment_id(self):
        body = row() + row(order="O-2")
        payments, findings = parse_and_audit(body)
        self.assertIn("duplicate-payment-id", rules(findings))

    def test_missing_payment_id(self):
        body = row(payment_id="")
        payments, findings = parse_and_audit(body)
        self.assertIn("missing-payment-id", rules(findings))

    def test_missing_order_id(self):
        body = row(order="")
        payments, findings = parse_and_audit(body)
        self.assertIn("missing-order-id", rules(findings))

    def test_missing_gateway(self):
        body = row(gateway="")
        payments, findings = parse_and_audit(body)
        self.assertIn("missing-gateway", rules(findings))

    def test_invalid_amount(self):
        body = row(amount="abc")
        payments, findings = parse_and_audit(body)
        self.assertIn("invalid-amount", rules(findings))
        self.assertIsNone(payments[0]["amount"])

    def test_zero_amount(self):
        body = row(amount="0")
        payments, findings = parse_and_audit(body)
        self.assertIn("invalid-amount", rules(findings))

    def test_invalid_timestamp(self):
        body = row(date="not-a-date")
        payments, findings = parse_and_audit(body)
        self.assertIn("invalid-timestamp", rules(findings))
        self.assertIsNone(payments[0]["date"])

    def test_future_timestamp(self):
        body = row(date="2026-12-01 09:00")
        payments, findings = parse_and_audit(body)
        self.assertIn("future-timestamp", rules(findings))

    def test_invalid_status(self):
        body = row(status="weird")
        payments, findings = parse_and_audit(body)
        self.assertIn("invalid-status", rules(findings))

    def test_extra_columns_ignored(self):
        body = HEADER.replace("\n", ",notes\n") + row()[:-1] + ",hi\n"
        path = write_csv(body)
        try:
            payments, findings = doctor.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertEqual(len(payments), 1)
        self.assertEqual(findings, [])


class TestFailedPayments(unittest.TestCase):
    def test_unrecovered_failed_is_error(self):
        body = row(status="failed", error="card_declined")
        payments, findings = parse_and_audit(body)
        self.assertIn("failed-payment-unrecovered", rules(findings))
        item = [f for f in findings
                if f.rule == "failed-payment-unrecovered"][0]
        self.assertEqual(item.severity, "error")

    def test_recovered_failed_is_warning(self):
        body = (row(payment_id="P-1", status="failed",
                    error="card_declined")
                + row(payment_id="P-2", status="succeeded"))
        payments, findings = parse_and_audit(body)
        self.assertIn("failed-payment-recovered", rules(findings))
        self.assertNotIn("failed-payment-unrecovered",
                         rules(findings))
        item = [f for f in findings
                if f.rule == "failed-payment-recovered"][0]
        self.assertEqual(item.severity, "warning")

    def test_recovered_needs_same_order(self):
        body = (row(payment_id="P-1", status="failed",
                    error="card_declined")
                + row(payment_id="P-2", order="O-2",
                      status="succeeded"))
        payments, findings = parse_and_audit(body)
        self.assertIn("failed-payment-unrecovered", rules(findings))

    def test_missing_error_code(self):
        body = row(status="failed", error="")
        payments, findings = parse_and_audit(body)
        self.assertIn("missing-error-code", rules(findings))

    def test_error_code_present_is_clean(self):
        body = row(status="failed", error="card_declined")
        payments, findings = parse_and_audit(body)
        self.assertNotIn("missing-error-code", rules(findings))

    def test_success_is_clean(self):
        body = row()
        payments, findings = parse_and_audit(body)
        self.assertEqual(findings, [])

    def test_refunded_is_clean(self):
        body = row(status="refunded")
        payments, findings = parse_and_audit(body)
        self.assertEqual(findings, [])


class TestCardTestingBurst(unittest.TestCase):
    def burst_body(self, count=3):
        lines = ""
        for index in range(count):
            lines += row(
                payment_id="P-%d" % (index + 1),
                order="O-%d" % (index + 1),
                amount="9.99", status="failed",
                error="do_not_honor",
                date="2026-11-17 08:%02d" % (index * 5))
        return lines

    def test_burst_flagged(self):
        payments, findings = parse_and_audit(self.burst_body(3))
        self.assertEqual(
            len([f for f in findings
                 if f.rule == "card-testing-burst"]), 3)
        item = [f for f in findings
                if f.rule == "card-testing-burst"][0]
        self.assertEqual(item.severity, "error")
        self.assertIn("3 failed payments", item.message)

    def test_burst_below_threshold_clean(self):
        payments, findings = parse_and_audit(self.burst_body(2))
        self.assertNotIn("card-testing-burst", rules(findings))

    def test_burst_window_expiry(self):
        body = (row(payment_id="P-1", order="O-1", status="failed",
                    error="do_not_honor", date="2026-11-15 08:00")
                + row(payment_id="P-2", order="O-2", status="failed",
                      error="do_not_honor", date="2026-11-16 08:00")
                + row(payment_id="P-3", order="O-3", status="failed",
                      error="do_not_honor", date="2026-11-17 08:00"))
        payments, findings = parse_and_audit(body)
        self.assertNotIn("card-testing-burst", rules(findings))

    def test_burst_count_flag(self):
        payments, findings = parse_and_audit(
            self.burst_body(3), burst_count=4)
        self.assertNotIn("card-testing-burst", rules(findings))

    def test_burst_case_insensitive_email(self):
        body = (row(payment_id="P-1", order="O-1", status="failed",
                    error="do_not_honor",
                    email="Sam@Example.com",
                    date="2026-11-17 08:00")
                + row(payment_id="P-2", order="O-2", status="failed",
                      error="do_not_honor",
                      email="sam@example.com",
                      date="2026-11-17 08:05")
                + row(payment_id="P-3", order="O-3", status="failed",
                      error="do_not_honor",
                      email="SAM@EXAMPLE.COM",
                      date="2026-11-17 08:10"))
        payments, findings = parse_and_audit(body)
        self.assertEqual(
            len([f for f in findings
                 if f.rule == "card-testing-burst"]), 3)

    def test_burst_needs_date(self):
        body = (row(payment_id="P-1", order="O-1", status="failed",
                    error="do_not_honor", date="not-a-date")
                + row(payment_id="P-2", order="O-2", status="failed",
                      error="do_not_honor", date="2026-11-17 08:05")
                + row(payment_id="P-3", order="O-3", status="failed",
                      error="do_not_honor", date="2026-11-17 08:10"))
        payments, findings = parse_and_audit(body)
        self.assertNotIn("card-testing-burst", rules(findings))


class TestRepeatedDecline(unittest.TestCase):
    def test_three_failures_flagged(self):
        body = ""
        for index in range(3):
            body += row(payment_id="P-%d" % (index + 1),
                        status="failed", error="expired_card",
                        attempt=str(index + 1),
                        date="2026-11-%d 09:00" % (14 + index))
        payments, findings = parse_and_audit(body)
        self.assertEqual(
            len([f for f in findings
                 if f.rule == "repeated-decline"]), 3)
        item = [f for f in findings
                if f.rule == "repeated-decline"][0]
        self.assertEqual(item.severity, "warning")

    def test_two_failures_clean(self):
        body = (row(payment_id="P-1", status="failed",
                    error="expired_card", date="2026-11-14 09:00")
                + row(payment_id="P-2", status="failed",
                      error="expired_card", attempt="2",
                      date="2026-11-15 09:00"))
        payments, findings = parse_and_audit(body)
        self.assertNotIn("repeated-decline", rules(findings))


class TestPendingPayments(unittest.TestCase):
    def test_stale_pending_is_error(self):
        body = row(status="pending", date="2026-11-08 09:00")
        payments, findings = parse_and_audit(body)
        self.assertIn("pending-payment-stale", rules(findings))
        item = [f for f in findings
                if f.rule == "pending-payment-stale"][0]
        self.assertEqual(item.severity, "error")

    def test_fresh_pending_is_warning(self):
        body = row(status="pending", date="2026-11-17 12:00")
        payments, findings = parse_and_audit(body)
        self.assertIn("pending-payment", rules(findings))
        self.assertNotIn("pending-payment-stale", rules(findings))

    def test_stale_boundary(self):
        body = row(status="pending", date="2026-11-11 00:00")
        payments, findings = parse_and_audit(body)
        self.assertIn("pending-payment", rules(findings))
        self.assertNotIn("pending-payment-stale", rules(findings))

    def test_stale_days_flag(self):
        body = row(status="pending", date="2026-11-08 09:00")
        payments, findings = parse_and_audit(body, stale_days=30)
        self.assertIn("pending-payment", rules(findings))
        self.assertNotIn("pending-payment-stale", rules(findings))


class TestGatewayFailureRate(unittest.TestCase):
    def gateway_body(self, failed, succeeded):
        body = ""
        for index in range(failed):
            body += row(payment_id="F-%d" % index,
                        order="OF-%d" % index, status="failed",
                        error="card_declined")
        for index in range(succeeded):
            body += row(payment_id="S-%d" % index,
                        order="OS-%d" % index, status="succeeded")
        return body

    def test_high_rate_flagged(self):
        payments, findings = parse_and_audit(
            self.gateway_body(3, 7))
        self.assertIn("high-gateway-failure-rate", rules(findings))

    def test_low_rate_clean(self):
        payments, findings = parse_and_audit(
            self.gateway_body(1, 9))
        self.assertNotIn("high-gateway-failure-rate",
                         rules(findings))

    def test_small_gateway_not_flagged(self):
        payments, findings = parse_and_audit(
            self.gateway_body(4, 0))
        self.assertNotIn("high-gateway-failure-rate",
                         rules(findings))

    def test_max_failure_rate_flag(self):
        payments, findings = parse_and_audit(
            self.gateway_body(3, 7), max_failure_rate=50)
        self.assertNotIn("high-gateway-failure-rate",
                         rules(findings))


class TestSummarize(unittest.TestCase):
    def test_first_attempt_unrecovered_values(self):
        for attempt, expected in (("1", 49.99), ("", 49.99),
                                  ("unreadable", 49.99),
                                  ("2", 0.0), ("3", 0.0)):
            with self.subTest(attempt=attempt):
                payments, findings = parse_and_audit(
                    row(status="failed", attempt=attempt))
                summary = doctor.summarize(payments, findings, AS_OF)
                self.assertEqual(summary["per_gateway"], [{
                    "gateway": "Stripe", "payments": 1,
                    "succeeded": 0, "failed": 1, "pending": 0,
                    "failure_rate": 100.0, "unrecovered_value": 49.99,
                    "first_attempt_unrecovered_value": expected}])
                self.assertEqual(summary["unrecovered_value"], 49.99)

    def test_first_attempt_without_attempt_column(self):
        path = write_csv(
            "payment_id,order_id,gateway,amount,status,date\n"
            "P-1,O-1,Stripe,49.99,failed,2026-11-17\n")
        try:
            payments, findings = doctor.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        findings.extend(doctor.audit(payments, AS_OF))
        summary = doctor.summarize(payments, findings, AS_OF)
        self.assertEqual(summary["per_gateway"][0][
            "first_attempt_unrecovered_value"], 49.99)

    def test_first_attempt_excludes_nonqualifying_payments(self):
        body = (row(payment_id="P-1", status="failed")
                + row(payment_id="P-2", gateway="PayPal", attempt="2")
                + row(payment_id="P-3", order="", status="failed")
                + row(payment_id="P-4", order="O-4", status="failed",
                      amount="abc")
                + row(payment_id="P-5", order="O-5", status="pending")
                + row(payment_id="P-6", order="O-6", status="refunded"))
        payments, findings = parse_and_audit(body)
        summary = doctor.summarize(payments, findings, AS_OF)
        self.assertEqual(summary["recovered_value"], 49.99)
        self.assertEqual(summary["unrecovered_value"], 0.0)
        for gateway in summary["per_gateway"]:
            self.assertEqual(gateway["unrecovered_value"], 0.0)
            self.assertEqual(gateway["first_attempt_unrecovered_value"], 0.0)

    def test_first_attempt_decimal_sum(self):
        body = (row(payment_id="P-1", amount="0.10", status="failed")
                + row(payment_id="P-2", amount="0.20", status="failed")
                + row(payment_id="P-3", amount="0.40", status="failed",
                      attempt="2"))
        payments, findings = parse_and_audit(body)
        summary = doctor.summarize(payments, findings, AS_OF)
        gateway = summary["per_gateway"][0]
        self.assertEqual(gateway["unrecovered_value"], 0.7)
        self.assertEqual(gateway["first_attempt_unrecovered_value"], 0.3)

    def test_summary_totals(self):
        body = (row(payment_id="P-1", status="failed",
                    error="card_declined")
                + row(payment_id="P-2", order="O-2",
                      status="succeeded"))
        path = write_csv(HEADER + body)
        try:
            payments, findings = doctor.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        findings.extend(doctor.audit(payments, AS_OF))
        summary = doctor.summarize(payments, findings, AS_OF)
        self.assertEqual(summary["payments"], 2)
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["failure_rate"], 50.0)
        self.assertEqual(summary["unrecovered_failed"], 1)
        self.assertAlmostEqual(summary["unrecovered_value"], 49.99)
        self.assertEqual(summary["recovered_failed"], 0)
        self.assertEqual(len(summary["per_gateway"]), 1)
        self.assertEqual(summary["per_gateway"][0]["gateway"],
                         "Stripe")
        self.assertEqual(summary["top_decline_reasons"],
                         [{"reason": "card_declined", "count": 1}])

    def test_empty_summary(self):
        summary = doctor.summarize([], [], AS_OF)
        self.assertEqual(summary["payments"], 0)
        self.assertEqual(summary["failure_rate"], 0.0)
        self.assertEqual(summary["per_gateway"], [])
        self.assertEqual(summary["top_decline_reasons"], [])

    def test_unrecovered_value_excludes_invalid_amount(self):
        body = row(status="failed", amount="abc",
                   error="card_declined")
        path = write_csv(HEADER + body)
        try:
            payments, findings = doctor.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        findings.extend(doctor.audit(payments, AS_OF))
        summary = doctor.summarize(payments, findings, AS_OF)
        self.assertEqual(summary["unrecovered_failed"], 1)
        self.assertEqual(summary["unrecovered_value"], 0.0)


class TestOutput(unittest.TestCase):
    def test_text_output_sample(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            "examples", "payments.csv")
        code, text = run_cli([path, "--as-of", "2026-11-18"])
        self.assertEqual(code, 0)
        self.assertIn("Payments: 22, findings: 30, errors: 24, "
                      "warnings: 6", text)
        self.assertIn("Unrecovered failed payments: 11 ($316.45)",
                      text)
        self.assertIn("Stripe: 15 payments, 7 succeeded, 8 failed, "
                      "0 pending, failure rate 53.3%, "
                      "unrecovered $114.95 ($114.95 first attempt)", text)
        self.assertIn("Square: 3 payments, 0 succeeded, 3 failed, "
                      "0 pending, failure rate 100.0%, "
                      "unrecovered $177.00 ($59.00 first attempt)", text)
        self.assertIn("do_not_honor: 4", text)

    def test_json_output_sample_gateway_values(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "examples", "payments.csv")
        code, text = run_cli([path, "--as-of", "2026-11-18", "--json"])
        self.assertEqual(code, 0)
        report = json.loads(text)
        self.assertEqual(report["unrecovered_value"], 316.45)
        self.assertEqual(report["recovered_value"], 129.0)
        self.assertEqual(report["per_gateway"], [
            {"gateway": "PayPal", "payments": 4, "succeeded": 0,
             "failed": 1, "pending": 2, "failure_rate": 100.0,
             "unrecovered_value": 24.5,
             "first_attempt_unrecovered_value": 24.5},
            {"gateway": "Square", "payments": 3, "succeeded": 0,
             "failed": 3, "pending": 0, "failure_rate": 100.0,
             "unrecovered_value": 177.0,
             "first_attempt_unrecovered_value": 59.0},
            {"gateway": "Stripe", "payments": 15, "succeeded": 7,
             "failed": 8, "pending": 0, "failure_rate": 53.3,
             "unrecovered_value": 114.95,
             "first_attempt_unrecovered_value": 114.95}])

    def test_json_output(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            "examples", "warnings-only.csv")
        code, text = run_cli([path, "--as-of", "2026-11-18",
                              "--json"])
        self.assertEqual(code, 0)
        report = json.loads(text)
        self.assertEqual(report["payments"], 3)
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["warnings"], 2)
        self.assertEqual(len(report["findings"]), 2)
        for gateway in report["per_gateway"]:
            self.assertEqual(gateway["unrecovered_value"], 0.0)
            self.assertEqual(gateway["first_attempt_unrecovered_value"], 0.0)

    def test_clean_output(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            "examples", "clean.csv")
        code, text = run_cli([path, "--as-of", "2026-11-18"])
        self.assertEqual(code, 0)
        self.assertIn("All payments clean.", text)
        self.assertEqual(text.count(
            "unrecovered $0.00 ($0.00 first attempt)"), 3)

    def test_out_file(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            "examples", "clean.csv")
        with tempfile.TemporaryDirectory() as directory:
            out_path = os.path.join(directory, "report.txt")
            code, text = run_cli([path, "--as-of", "2026-11-18",
                                  "--out", out_path])
            self.assertEqual(code, 0)
            self.assertEqual(text, "")
            with open(out_path, encoding="utf-8") as handle:
                self.assertIn("All payments clean.", handle.read())

    def test_check_exit_codes(self):
        directory = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        sample = os.path.join(directory, "examples", "payments.csv")
        clean = os.path.join(directory, "examples", "clean.csv")
        warnings_only = os.path.join(
            directory, "examples", "warnings-only.csv")
        code, _ = run_cli([sample, "--as-of", "2026-11-18",
                           "--check"])
        self.assertEqual(code, 1)
        code, _ = run_cli([clean, "--as-of", "2026-11-18",
                           "--check"])
        self.assertEqual(code, 0)
        code, _ = run_cli([warnings_only, "--as-of", "2026-11-18",
                           "--check"])
        self.assertEqual(code, 0)
        code, _ = run_cli([warnings_only, "--as-of", "2026-11-18",
                           "--check", "--strict"])
        self.assertEqual(code, 1)

    def test_bad_flag_values(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            "examples", "clean.csv")
        code, _ = run_cli([path, "--stale-days", "-1"])
        self.assertEqual(code, 2)
        code, _ = run_cli([path, "--max-failure-rate", "101"])
        self.assertEqual(code, 2)
        code, _ = run_cli([path, "--burst-count", "1"])
        self.assertEqual(code, 2)
        code, _ = run_cli([path, "--as-of", "not-a-date"])
        self.assertEqual(code, 2)

    def test_max_failure_rate_flag(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
            "examples", "payments.csv")
        code, text = run_cli([path, "--as-of", "2026-11-18",
                              "--max-failure-rate", "100"])
        self.assertEqual(code, 0)
        self.assertIn("findings: 29", text)

    def test_version(self):
        self.assertEqual(doctor.__version__, "1.0.0")


if __name__ == "__main__":
    unittest.main()
