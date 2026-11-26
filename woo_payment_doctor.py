"""WooCommerce payment audit CLI.

Reads a WooCommerce payment attempts CSV export (from the
store export, a payment gateway report, or a manual export)
and audits every payment attempt for the problems that
quietly cost a store sales: failed payments with no later
successful retry (lost revenue), card-testing bursts (one
email hammering the checkout with small failed attempts),
orders that failed three or more times, stale pending
payments stuck waiting for a gateway, gateways whose
failure rate is above the healthy limit, failed payments
with no decline reason recorded, and bad data such as
duplicate payment ids, malformed rows, and invalid amounts
or dates. It also prints a per-gateway table and the top
decline reasons, so a store owner can see at a glance
which gateway is failing and why.

Up to 15% of online checkout attempts fail, and 47% of
declines are insufficient funds while 20% are address
mismatches and 15% are expired cards. Failed orders are
also a fraud signal: store owners report hundreds of
failed orders in minutes when bots test stolen cards.
Payment monitoring lives in paid in-WordPress plugins,
but nobody audits the exported payments file first to say
which failed payments are recoverable and which gateway
needs attention today. This tool is that audit: it ranks
the failed payments in the export alone, and never
touches your store.

Each problem is a finding with a payment id, a rule name,
a severity (error or warning), and a message. Errors are
unrecovered failed payments, card-testing bursts, stale
pending payments, failing gateways, and data that cannot
be trusted; warnings are recovered failures, orders that
failed repeatedly, pending payments, and failed payments
with no decline reason. --check exits 1 when any error is
found, and --strict also fails on warnings.

Dates are parsed defensively. A bad date or amount is
reported as a finding and the row is still audited on the
fields that did parse, so one bad row never crashes the
run.

Zero dependencies, Python 3.10 or newer, standard library only.

Usage:
    python3 woo_payment_doctor.py examples/payments.csv
    python3 woo_payment_doctor.py payments.csv --json --check
    python3 woo_payment_doctor.py payments.csv --as-of 2026-11-18
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

__version__ = "1.0.0"

DEFAULT_STALE_DAYS = 7
DEFAULT_MAX_FAILURE_RATE = 20
DEFAULT_BURST_COUNT = 3
DEFAULT_BURST_HOURS = 24
DEFAULT_REPEAT_FAILURES = 3
DEFAULT_MIN_GATEWAY_ATTEMPTS = 5

KNOWN_STATUSES = {"succeeded", "failed", "pending", "refunded",
                  "cancelled", "canceled", "authorized"}

TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M",
    "%d/%m/%Y",
    "%m/%d/%Y",
)


class Finding:
    """One audit finding for one payment, or for one gateway."""

    def __init__(self, payment, rule, severity, message):
        self.payment = payment
        self.rule = rule
        self.severity = severity
        self.message = message

    def as_dict(self):
        """Return the finding as a plain dict."""
        return {"payment": self.payment, "rule": self.rule,
                "severity": self.severity, "message": self.message}

    def __str__(self):
        return "%s [%s] %s: %s" % (self.severity.upper(), self.rule,
                                   self.payment, self.message)


def parse_timestamp(text):
    """Parse a timestamp string into an aware datetime, or None.

    Accepts ISO 8601 with or without a timezone (a trailing Z is
    handled), plus a few common export formats. A naive timestamp
    is read as UTC. Returns None for an empty string or a value
    that matches no known format, so callers can report it
    instead of crashing.
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    candidate = text
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in TIMESTAMP_FORMATS:
            try:
                parsed = datetime.strptime(candidate, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_amount(text):
    """Parse an amount string into a Decimal, or None.

    Accepts plain numbers with an optional currency symbol or
    thousands separators (for example "$1,234.56" and "49.99").
    Returns None for an empty string, a value that is not a
    number, or a number that is zero or negative.
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if value <= 0:
        return None
    return value


def normalize_status(text):
    """Normalize a status value to a canonical status string."""
    if text is None:
        return ""
    status = text.strip().lower()
    if status == "success":
        return "succeeded"
    if status == "declined":
        return "failed"
    return status


def find_column(header, names):
    """Return the first of names present in header, or None."""
    for name in names:
        if name in header:
            return name
    return None


def parse_csv(path, as_of):
    """Parse a WooCommerce payment attempts CSV export.

    Returns (payments, findings). Each payment is a dict with
    the id, order id, gateway, amount, status, error code,
    customer email, attempt number, and the parsed date. Rows
    with the wrong number of fields are reported as
    malformed-row errors and skipped, never a crash. A UTF-8
    BOM header is handled. Extra columns are ignored.
    """
    findings = []
    payments = []
    try:
        handle = open(path, newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError("cannot read %s: %s" % (path, exc))
    with handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("%s is empty, no header row found" % path)
        except csv.Error as exc:
            raise ValueError("%s is not a readable CSV file: %s"
                             % (path, exc))
        header = [name.strip() for name in header]
        id_column = find_column(header, ("payment_id", "id",
                                         "transaction_id"))
        if id_column is None:
            raise ValueError(
                "%s has no payment_id or id column (header is: %s)"
                % (path, ", ".join(header)))
        order_column = find_column(header, ("order_id", "order"))
        if order_column is None:
            raise ValueError(
                "%s has no order_id column "
                "(header is: %s)" % (path, ", ".join(header)))
        gateway_column = find_column(
            header, ("gateway", "payment_method", "method"))
        if gateway_column is None:
            raise ValueError(
                "%s has no gateway column "
                "(header is: %s)" % (path, ", ".join(header)))
        amount_column = find_column(header, ("amount", "total"))
        if amount_column is None:
            raise ValueError(
                "%s has no amount column "
                "(header is: %s)" % (path, ", ".join(header)))
        status_column = find_column(header, ("status", "payment_status"))
        if status_column is None:
            raise ValueError(
                "%s has no status column "
                "(header is: %s)" % (path, ", ".join(header)))
        error_column = find_column(
            header, ("error_code", "decline_reason", "failure_reason",
                     "error", "decline_code"))
        email_column = find_column(
            header, ("customer_email", "email", "billing_email"))
        attempt_column = find_column(header, ("attempt", "attempt_no"))
        date_column = find_column(
            header, ("date", "created_at", "payment_date"))
        if date_column is None:
            raise ValueError(
                "%s has no date column "
                "(header is: %s)" % (path, ", ".join(header)))
        seen_ids = set()
        for line_number, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != len(header):
                findings.append(Finding(
                    "row %d" % line_number, "malformed-row", "error",
                    "row has %d fields but the header has %d, "
                    "so the row was skipped" % (len(row), len(header))))
                continue
            cells = dict(zip(header, (cell.strip() for cell in row)))
            payment_id = cells[id_column]
            if not payment_id:
                findings.append(Finding(
                    "row %d" % line_number, "missing-payment-id",
                    "error",
                    "row has no payment id, so the payment "
                    "cannot be matched to an order"))
                payment_id = "row %d" % line_number
            if payment_id in seen_ids:
                findings.append(Finding(
                    payment_id, "duplicate-payment-id", "error",
                    "payment id appears more than once in the export"))
            else:
                seen_ids.add(payment_id)
            order_id = cells[order_column]
            if not order_id:
                findings.append(Finding(
                    payment_id, "missing-order-id", "error",
                    "payment has no order id, so it cannot be "
                    "matched to a recovery"))
            gateway = cells[gateway_column]
            if not gateway:
                findings.append(Finding(
                    payment_id, "missing-gateway", "error",
                    "payment has no gateway, so it cannot be "
                    "counted in the per-gateway table"))
            amount_raw = cells[amount_column]
            amount = parse_amount(amount_raw)
            if amount is None:
                findings.append(Finding(
                    payment_id, "invalid-amount", "error",
                    "amount %r is not a positive number"
                    % amount_raw))
            status = normalize_status(cells[status_column])
            if status and status not in KNOWN_STATUSES:
                findings.append(Finding(
                    payment_id, "invalid-status", "error",
                    "status %r is not a known payment status"
                    % status))
            date_raw = cells[date_column]
            payment_date = None
            if date_raw:
                payment_date = parse_timestamp(date_raw)
                if payment_date is None:
                    findings.append(Finding(
                        payment_id, "invalid-timestamp", "error",
                        "date %r is not a readable date" % date_raw))
            if payment_date is not None and payment_date > as_of:
                findings.append(Finding(
                    payment_id, "future-timestamp", "error",
                    "date %s is after the as-of date %s"
                    % (payment_date.strftime("%Y-%m-%d %H:%M"),
                       as_of.strftime("%Y-%m-%d %H:%M"))))
            attempt_raw = cells[attempt_column] if attempt_column else ""
            try:
                attempt = int(attempt_raw) if attempt_raw else 1
            except ValueError:
                attempt = 1
            payments.append({
                "id": payment_id,
                "order_id": order_id,
                "gateway": gateway,
                "amount": amount,
                "amount_raw": amount_raw,
                "status": status,
                "error_code": (cells[error_column]
                               if error_column else ""),
                "email": (cells[email_column]
                          if email_column else ""),
                "attempt": attempt,
                "date": payment_date,
                "date_raw": date_raw,
                "line": line_number})
    return payments, findings


def audit(payments, as_of, stale_days=DEFAULT_STALE_DAYS,
          max_failure_rate=DEFAULT_MAX_FAILURE_RATE,
          burst_count=DEFAULT_BURST_COUNT,
          burst_hours=DEFAULT_BURST_HOURS,
          repeat_failures=DEFAULT_REPEAT_FAILURES,
          min_gateway_attempts=DEFAULT_MIN_GATEWAY_ATTEMPTS):
    """Audit a parsed payment list and return a list of findings."""
    findings = []
    successful_orders = set()
    for payment in payments:
        if payment["status"] == "succeeded" and payment["order_id"]:
            successful_orders.add(payment["order_id"])
    failed_by_order = {}
    for payment in payments:
        if payment["status"] == "failed" and payment["order_id"]:
            failed_by_order.setdefault(
                payment["order_id"], []).append(payment)
    failed_by_email = {}
    for payment in payments:
        if payment["status"] == "failed" and payment["email"] \
                and payment["date"] is not None:
            key = payment["email"].lower()
            failed_by_email.setdefault(key, []).append(payment)
    burst_counts = {}
    for email in failed_by_email:
        dated = sorted(failed_by_email[email],
                       key=lambda item: item["date"])
        for index in range(len(dated)):
            window = [item for item in dated
                      if item["date"] >= dated[index]["date"]
                      and (item["date"] - dated[index]["date"])
                      .total_seconds() <= burst_hours * 3600]
            if len(window) >= burst_count:
                for item in window:
                    burst_counts[item["id"]] = max(
                        burst_counts.get(item["id"], 0), len(window))
    for payment in payments:
        payment_id = payment["id"]
        status = payment["status"]
        payment_date = payment["date"]
        age_days = None
        if payment_date is not None:
            age_days = (as_of - payment_date).total_seconds() / 86400.0
        if status == "failed":
            if payment["order_id"] \
                    and payment["order_id"] in successful_orders:
                findings.append(Finding(
                    payment_id, "failed-payment-recovered", "warning",
                    "payment failed but order %s was later paid "
                    "successfully, so the customer recovered"
                    % payment["order_id"]))
            elif payment["order_id"]:
                findings.append(Finding(
                    payment_id, "failed-payment-unrecovered", "error",
                    "payment failed and order %s has no "
                    "successful payment, so $%s of revenue is "
                    "still lost"
                    % (payment["order_id"],
                       payment["amount"] if payment["amount"]
                       is not None else "?")))
            if not payment["error_code"]:
                findings.append(Finding(
                    payment_id, "missing-error-code", "warning",
                    "failed payment has no decline reason "
                    "recorded, so the cause cannot be diagnosed"))
            if payment_id in burst_counts:
                findings.append(Finding(
                    payment_id, "card-testing-burst", "error",
                    "customer %s has %d failed payments "
                    "within %d hours, which looks like card "
                    "testing"
                    % (payment["email"], burst_counts[payment_id],
                       burst_hours)))
            order_failures = failed_by_order.get(
                payment["order_id"], []) if payment["order_id"] else []
            if len(order_failures) >= repeat_failures:
                findings.append(Finding(
                    payment_id, "repeated-decline", "warning",
                    "order %s has failed %d times, so the "
                    "customer is stuck in a retry loop"
                    % (payment["order_id"], len(order_failures))))
        if status == "pending" and age_days is not None:
            if age_days > stale_days:
                findings.append(Finding(
                    payment_id, "pending-payment-stale", "error",
                    "payment has waited %.1f days for the "
                    "gateway, past the %d day stale window"
                    % (age_days, stale_days)))
            else:
                findings.append(Finding(
                    payment_id, "pending-payment", "warning",
                    "payment is still waiting for the gateway"))
    gateways = {}
    for payment in payments:
        if payment["gateway"]:
            gateways.setdefault(payment["gateway"], []).append(payment)
    for gateway in sorted(gateways):
        gateway_payments = gateways[gateway]
        decided = [item for item in gateway_payments
                   if item["status"] in ("succeeded", "failed")]
        failed = sum(1 for item in decided
                     if item["status"] == "failed")
        if len(decided) >= min_gateway_attempts and decided:
            rate = failed / len(decided) * 100.0
            if rate > max_failure_rate:
                findings.append(Finding(
                    "gateway %s" % gateway,
                    "high-gateway-failure-rate", "error",
                    "gateway failed %d of %d decided payments "
                    "(%.1f%%), above the %d%% limit"
                    % (failed, len(decided), rate,
                       max_failure_rate)))
    return findings


def summarize(payments, findings, as_of,
              stale_days=DEFAULT_STALE_DAYS,
              max_failure_rate=DEFAULT_MAX_FAILURE_RATE):
    """Return the summary counts for a payment list and findings."""
    errors = sum(1 for item in findings if item.severity == "error")
    warnings = sum(1 for item in findings
                   if item.severity == "warning")
    succeeded = sum(1 for item in payments
                    if item["status"] == "succeeded")
    failed = sum(1 for item in payments
                 if item["status"] == "failed")
    pending = sum(1 for item in payments
                  if item["status"] == "pending")
    decided = succeeded + failed
    failure_rate = (failed / decided * 100.0) if decided else 0.0
    unrecovered = sum(
        1 for item in findings
        if item.rule == "failed-payment-unrecovered")
    recovered = sum(
        1 for item in findings
        if item.rule == "failed-payment-recovered")
    unrecovered_value = Decimal("0")
    recovered_value = Decimal("0")
    for payment in payments:
        if payment["status"] != "failed" \
                or payment["amount"] is None:
            continue
        if payment["order_id"] and any(
                other["status"] == "succeeded"
                and other["order_id"] == payment["order_id"]
                for other in payments):
            recovered_value += payment["amount"]
        elif payment["order_id"]:
            unrecovered_value += payment["amount"]
    burst = sum(1 for item in findings
                if item.rule == "card-testing-burst")
    stale = sum(1 for item in findings
                if item.rule == "pending-payment-stale")
    per_gateway = []
    gateways = {}
    for payment in payments:
        if payment["gateway"]:
            gateways.setdefault(payment["gateway"], []).append(payment)
    for gateway in sorted(gateways):
        gateway_payments = gateways[gateway]
        gateway_succeeded = sum(
            1 for item in gateway_payments
            if item["status"] == "succeeded")
        gateway_failed = sum(
            1 for item in gateway_payments
            if item["status"] == "failed")
        gateway_pending = sum(
            1 for item in gateway_payments
            if item["status"] == "pending")
        gateway_decided = gateway_succeeded + gateway_failed
        gateway_rate = (gateway_failed / gateway_decided * 100.0
                        if gateway_decided else 0.0)
        gateway_unrecovered = Decimal("0")
        for payment in gateway_payments:
            if payment["status"] != "failed" \
                    or payment["amount"] is None \
                    or not payment["order_id"]:
                continue
            if not any(other["status"] == "succeeded"
                       and other["order_id"] == payment["order_id"]
                       for other in payments):
                gateway_unrecovered += payment["amount"]
        per_gateway.append({
            "gateway": gateway,
            "payments": len(gateway_payments),
            "succeeded": gateway_succeeded,
            "failed": gateway_failed,
            "pending": gateway_pending,
            "failure_rate": round(gateway_rate, 1),
            "unrecovered_value": float(gateway_unrecovered)})
    reasons = {}
    for payment in payments:
        if payment["status"] == "failed" and payment["error_code"]:
            code = payment["error_code"]
            reasons[code] = reasons.get(code, 0) + 1
    top_reasons = [{"reason": code, "count": count}
                   for code, count in sorted(
                       reasons.items(), key=lambda pair: (-pair[1],
                                                          pair[0]))]
    return {"payments": len(payments),
            "findings": len(findings),
            "errors": errors,
            "warnings": warnings,
            "succeeded": succeeded,
            "failed": failed,
            "pending": pending,
            "failure_rate": round(failure_rate, 1),
            "unrecovered_failed": unrecovered,
            "recovered_failed": recovered,
            "unrecovered_value": float(unrecovered_value),
            "recovered_value": float(recovered_value),
            "card_testing_burst_findings": burst,
            "stale_pending": stale,
            "per_gateway": per_gateway,
            "top_decline_reasons": top_reasons}


def render_text(payments, findings, summary):
    """Render the text report."""
    lines = [str(item) for item in findings]
    lines.append("")
    lines.append("Payments: %(payments)d, findings: %(findings)d, "
                 "errors: %(errors)d, warnings: %(warnings)d"
                 % summary)
    lines.append("Succeeded: %(succeeded)d, failed: %(failed)d, "
                 "pending: %(pending)d, failure rate: "
                 "%(failure_rate).1f%%" % summary)
    lines.append("Unrecovered failed payments: "
                 "%(unrecovered_failed)d ($%(unrecovered_value).2f), "
                 "recovered failed payments: "
                 "%(recovered_failed)d ($%(recovered_value).2f)"
                 % summary)
    lines.append("Card-testing burst findings: "
                 "%(card_testing_burst_findings)d, stale pending "
                 "payments: %(stale_pending)d" % summary)
    lines.append("")
    lines.append("Per-gateway summary:")
    for row in summary["per_gateway"]:
        lines.append(
            "%s: %d payments, %d succeeded, %d failed, %d "
            "pending, failure rate %.1f%%, unrecovered $%.2f"
            % (row["gateway"], row["payments"], row["succeeded"],
               row["failed"], row["pending"], row["failure_rate"],
               row["unrecovered_value"]))
    lines.append("")
    lines.append("Top decline reasons:")
    if summary["top_decline_reasons"]:
        for row in summary["top_decline_reasons"]:
            lines.append("%s: %d" % (row["reason"], row["count"]))
    else:
        lines.append("(none recorded)")
    if summary["findings"] == 0:
        lines.append("")
        lines.append("All payments clean.")
    return "\n".join(lines) + "\n"


def render_json(payments, findings, summary):
    """Render the JSON report."""
    report = dict(summary)
    report["findings"] = [item.as_dict() for item in findings]
    return json.dumps(report, indent=2) + "\n"


def build_parser():
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="woo-payment-doctor",
        description="Audit a WooCommerce payment attempts CSV "
                    "export for lost revenue, card-testing bursts, "
                    "and failing gateways.")
    parser.add_argument("csv_file", help="payment attempts CSV "
                        "export to audit")
    parser.add_argument("--stale-days", type=int,
                        default=DEFAULT_STALE_DAYS,
                        help="a pending payment older than this "
                        "many days is stale "
                        "(default: %d)" % DEFAULT_STALE_DAYS)
    parser.add_argument("--max-failure-rate", type=int,
                        default=DEFAULT_MAX_FAILURE_RATE,
                        help="a gateway failing more than this "
                        "percentage of decided payments is flagged "
                        "(default: %d)" % DEFAULT_MAX_FAILURE_RATE)
    parser.add_argument("--burst-count", type=int,
                        default=DEFAULT_BURST_COUNT,
                        help="failed payments from one email within "
                        "the burst window that count as card "
                        "testing (default: %d)" % DEFAULT_BURST_COUNT)
    parser.add_argument("--burst-hours", type=int,
                        default=DEFAULT_BURST_HOURS,
                        help="hours in the card-testing burst "
                        "window (default: %d)" % DEFAULT_BURST_HOURS)
    parser.add_argument("--as-of", default=None,
                        help="date to compare timestamps against, "
                        "for example 2026-11-18 (default: now)")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of text")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any error is found")
    parser.add_argument("--strict", action="store_true",
                        help="with --check, also exit 1 on warnings")
    parser.add_argument("--out", default=None,
                        help="write the report to a file")
    return parser


def main(argv=None):
    """Run the CLI and return the exit code."""
    args = build_parser().parse_args(argv)
    if args.stale_days < 0:
        print("error: --stale-days must be at least 0",
              file=sys.stderr)
        return 2
    if args.max_failure_rate < 0 or args.max_failure_rate > 100:
        print("error: --max-failure-rate must be from 0 to 100",
              file=sys.stderr)
        return 2
    if args.burst_count < 2:
        print("error: --burst-count must be at least 2",
              file=sys.stderr)
        return 2
    if args.burst_hours < 1:
        print("error: --burst-hours must be at least 1",
              file=sys.stderr)
        return 2
    if args.as_of is None:
        as_of = datetime.now(timezone.utc)
    else:
        as_of = parse_timestamp(args.as_of)
        if as_of is None:
            print("error: --as-of %r is not a readable date"
                  % args.as_of, file=sys.stderr)
            return 2
    try:
        payments, findings = parse_csv(args.csv_file, as_of)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    findings.extend(audit(payments, as_of,
                          stale_days=args.stale_days,
                          max_failure_rate=args.max_failure_rate,
                          burst_count=args.burst_count,
                          burst_hours=args.burst_hours))
    summary = summarize(payments, findings, as_of,
                        stale_days=args.stale_days,
                        max_failure_rate=args.max_failure_rate)
    if args.json:
        report = render_json(payments, findings, summary)
    else:
        report = render_text(payments, findings, summary)
    if args.out is not None:
        try:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(report)
        except OSError as exc:
            print("error: cannot write %s: %s" % (args.out, exc),
                  file=sys.stderr)
            return 2
    else:
        sys.stdout.write(report)
    if args.check:
        if summary["errors"] > 0:
            return 1
        if args.strict and summary["warnings"] > 0:
            return 1
    return 0


def main_entry():
    """Console script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    main_entry()
