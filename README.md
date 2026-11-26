# woo-payment-doctor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/aamirtechs/woo-payment-doctor/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/aamirtechs/woo-payment-doctor/issues)

A zero-dependency Python CLI that audits a WooCommerce
payment attempts CSV export for the problems that quietly
cost a store sales: failed payments with no later
successful retry (lost revenue), card-testing bursts (one
email hammering the checkout with small failed attempts),
orders that failed three or more times, stale pending
payments stuck waiting for a gateway, gateways whose
failure rate is above the healthy limit, failed payments
with no decline reason recorded, and bad data such as
duplicate payment ids, malformed rows, invalid amounts,
and invalid or future dates. It also prints a per-gateway
table and the top decline reasons, so a store owner can
see at a glance which gateway is failing and why.

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

## Install

Requires Python 3.10 or newer. No dependencies beyond the
standard library.

```bash
pip install woo-payment-doctor
```

Or run it straight from the source checkout:

```bash
python3 woo_payment_doctor.py examples/payments.csv
```

## Input format

A CSV export with a header row, from the store export, a
payment gateway report, or a manual export. Columns used:
`payment_id` (or `id`), `order_id` (or `order`),
`gateway` (or `payment_method`), `amount` (or `total`),
`status` (or `payment_status`), `error_code` (or
`decline_reason`), `customer_email` (or `email`),
`attempt`, and `date` (or `created_at`). Extra columns
are ignored, so a full payments export works as-is. A
UTF-8 BOM header is handled. Only `payment_id`,
`order_id`, `gateway`, `amount`, `status`, and `date` are
required; the other columns are optional, and the checks
that need a missing column are skipped.

`date` also accepts `payment_date` as the column name,
`gateway` also accepts `method`, `error_code` also
accepts `failure_reason`, `error`, or `decline_code`,
`customer_email` also accepts `billing_email`, and
`attempt` also accepts `attempt_no`.

Dates accept ISO 8601 with or without a timezone
(`2026-11-17 10:00`, `2026-11-17T10:00:00Z`,
`2026-11-17`), plus day/month/year and month/day/year
forms. A date with no timezone is read as UTC. Amounts
accept plain numbers with an optional currency symbol or
thousands separators (`49.99`, `$1,234.56`). A status of
`success` counts as `succeeded` and `declined` counts as
`failed`.

```csv
payment_id,order_id,gateway,amount,status,error_code,customer_email,attempt,date
P-101,O-201,Stripe,49.99,failed,insufficient_funds,ana@example.com,1,2026-11-17 10:00
P-102,O-202,Stripe,129.00,failed,card_declined,ben@example.com,1,2026-11-16 09:00
P-103,O-202,Stripe,129.00,succeeded,,ben@example.com,2,2026-11-16 10:00
P-104,O-203,PayPal,24.50,failed,,cara@example.com,1,2026-11-15 09:00
P-105,O-204,Stripe,9.99,failed,do_not_honor,sam@example.com,1,2026-11-17 08:00
```

## What it checks

Errors, the ones that mean money is lost now or data that
cannot be trusted:

* `failed-payment-unrecovered`: a failed payment whose
  order has no later successful payment, so the revenue
  is still lost. The message names the lost amount.
* `card-testing-burst`: one customer email with
  `--burst-count` (default 3) or more failed payments
  inside `--burst-hours` (default 24), which looks like
  card testing.
* `pending-payment-stale`: a pending payment older than
  `--stale-days` (default 7).
* `high-gateway-failure-rate`: a gateway that failed more
  than `--max-failure-rate` (default 20%) of its decided
  payments, counted only for gateways with at least 5
  decided payments.
* `duplicate-payment-id`: the same payment id twice in
  the export.
* `missing-payment-id`: a row with no payment id, so the
  payment cannot be matched to an order.
* `missing-order-id`: a payment with no order id, so it
  cannot be matched to a recovery.
* `missing-gateway`: a payment with no gateway, so it
  cannot be counted in the per-gateway table.
* `invalid-amount`: an amount that is not a positive
  number. The row is still audited on the fields that did
  parse.
* `invalid-timestamp`: a date field that matches no known
  format. The row is still audited on the fields that did
  parse.
* `future-timestamp`: a `date` after `--as-of` (default
  now).
* `invalid-status`: a status that is not a known payment
  status (succeeded, failed, pending, refunded,
  cancelled, canceled, authorized).
* `malformed-row`: a row with the wrong number of fields.
  The row is skipped and reported, never a crash.

Warnings, payments worth a closer look:

* `failed-payment-recovered`: a failed payment whose
  order was later paid successfully, so the customer
  recovered on a retry.
* `missing-error-code`: a failed payment with no decline
  reason recorded, so the cause cannot be diagnosed.
* `repeated-decline`: an order that failed
  `--repeat-failures` (default 3) or more times, so the
  customer is stuck in a retry loop.
* `pending-payment`: a pending payment still inside the
  stale window.

The summary reports the succeeded, failed, and pending
counts, the overall failure rate, the unrecovered and
recovered failed payment counts and values, the
card-testing burst finding count, the stale pending
count, a per-gateway table (payments, succeeded, failed,
pending, failure rate, unrecovered value), and the top
decline reasons.

## Usage

```bash
python3 woo_payment_doctor.py examples/payments.csv --as-of 2026-11-18
```

Captured output:

```
ERROR [invalid-amount] P-117: amount 'abc' is not a positive number
ERROR [invalid-timestamp] P-118: date 'not-a-date' is not a readable date
ERROR [future-timestamp] P-119: date 2026-12-01 09:00 is after the as-of date 2026-11-18 00:00
ERROR [duplicate-payment-id] P-101: payment id appears more than once in the export
ERROR [malformed-row] row 22: row has 10 fields but the header has 9, so the row was skipped
ERROR [missing-payment-id] row 23: row has no payment id, so the payment cannot be matched to an order
ERROR [missing-order-id] P-121: payment has no order id, so it cannot be matched to a recovery
ERROR [failed-payment-unrecovered] P-101: payment failed and order O-201 has no successful payment, so $49.99 of revenue is still lost
WARNING [failed-payment-recovered] P-102: payment failed but order O-202 was later paid successfully, so the customer recovered
ERROR [failed-payment-unrecovered] P-104: payment failed and order O-203 has no successful payment, so $24.50 of revenue is still lost
WARNING [missing-error-code] P-104: failed payment has no decline reason recorded, so the cause cannot be diagnosed
ERROR [failed-payment-unrecovered] P-105: payment failed and order O-204 has no successful payment, so $9.99 of revenue is still lost
ERROR [card-testing-burst] P-105: customer sam@example.com has 4 failed payments within 24 hours, which looks like card testing
ERROR [failed-payment-unrecovered] P-106: payment failed and order O-205 has no successful payment, so $9.99 of revenue is still lost
ERROR [card-testing-burst] P-106: customer sam@example.com has 4 failed payments within 24 hours, which looks like card testing
ERROR [failed-payment-unrecovered] P-107: payment failed and order O-206 has no successful payment, so $9.99 of revenue is still lost
ERROR [card-testing-burst] P-107: customer sam@example.com has 4 failed payments within 24 hours, which looks like card testing
ERROR [failed-payment-unrecovered] P-108: payment failed and order O-207 has no successful payment, so $9.99 of revenue is still lost
ERROR [card-testing-burst] P-108: customer sam@example.com has 4 failed payments within 24 hours, which looks like card testing
ERROR [failed-payment-unrecovered] P-109: payment failed and order O-209 has no successful payment, so $59.00 of revenue is still lost
WARNING [repeated-decline] P-109: order O-209 has failed 3 times, so the customer is stuck in a retry loop
ERROR [failed-payment-unrecovered] P-110: payment failed and order O-209 has no successful payment, so $59.00 of revenue is still lost
WARNING [repeated-decline] P-110: order O-209 has failed 3 times, so the customer is stuck in a retry loop
ERROR [failed-payment-unrecovered] P-111: payment failed and order O-209 has no successful payment, so $59.00 of revenue is still lost
WARNING [repeated-decline] P-111: order O-209 has failed 3 times, so the customer is stuck in a retry loop
ERROR [pending-payment-stale] P-112: payment has waited 9.6 days for the gateway, past the 7 day stale window
WARNING [pending-payment] P-113: payment is still waiting for the gateway
ERROR [failed-payment-unrecovered] P-117: payment failed and order O-215 has no successful payment, so $? of revenue is still lost
ERROR [failed-payment-unrecovered] P-118: payment failed and order O-216 has no successful payment, so $25.00 of revenue is still lost
ERROR [high-gateway-failure-rate] gateway Stripe: gateway failed 8 of 15 decided payments (53.3%), above the 20% limit

Payments: 22, findings: 30, errors: 24, warnings: 6
Succeeded: 7, failed: 12, pending: 2, failure rate: 63.2%
Unrecovered failed payments: 11 ($316.45), recovered failed payments: 1 ($129.00)
Card-testing burst findings: 4, stale pending payments: 1

Per-gateway summary:
PayPal: 4 payments, 0 succeeded, 1 failed, 2 pending, failure rate 100.0%, unrecovered $24.50
Square: 3 payments, 0 succeeded, 3 failed, 0 pending, failure rate 100.0%, unrecovered $177.00
Stripe: 15 payments, 7 succeeded, 8 failed, 0 pending, failure rate 53.3%, unrecovered $114.95

Top decline reasons:
do_not_honor: 4
card_declined: 3
expired_card: 3
insufficient_funds: 1
```

The sample has 23 rows; the malformed row 22 is skipped,
so 22 payments are audited. The headline numbers are 11
unrecovered failed payments worth $316.45, 1 recovered
failure worth $129.00, a 63.2% failure rate, 4
card-testing burst findings (Sam tried four $9.99
payments in 15 minutes), 3 repeated-decline warnings
(Dan failed the same order 3 times on an expired card),
1 stale pending payment, and Stripe failing 53.3% of its
decided payments. The worst gateway is Square at a 100%
failure rate, but with only 3 decided payments it stays
below the 5 payment minimum for the gateway flag.

Use it as a CI gate: `--check` exits 1 when any error is
found, and 0 otherwise. On the sample above it exits 1:

```bash
python3 woo_payment_doctor.py examples/payments.csv --as-of 2026-11-18 --check
```

On `examples/clean.csv`, five clean payments, it exits 0:

```
Payments: 5, findings: 0, errors: 0, warnings: 0
Succeeded: 4, failed: 0, pending: 0, failure rate: 0.0%
Unrecovered failed payments: 0 ($0.00), recovered failed payments: 0 ($0.00)
Card-testing burst findings: 0, stale pending payments: 0

Per-gateway summary:
PayPal: 1 payments, 1 succeeded, 0 failed, 0 pending, failure rate 0.0%, unrecovered $0.00
Square: 1 payments, 0 succeeded, 0 failed, 0 pending, failure rate 0.0%, unrecovered $0.00
Stripe: 3 payments, 3 succeeded, 0 failed, 0 pending, failure rate 0.0%, unrecovered $0.00

Top decline reasons:
(none recorded)

All payments clean.
```

With `--strict`, `--check` also fails on warnings.
`examples/warnings-only.csv` has one recovered failure
and one fresh pending payment, so it passes `--check`
(exit 0) and fails `--check --strict` (exit 1).
With a higher burst threshold the card-testing findings
drop out:

```bash
python3 woo_payment_doctor.py examples/payments.csv --as-of 2026-11-18 --burst-count 5
```

Captured output (the 4 card-testing burst lines drop out;
the rest of the output is identical to the default run):

```
Payments: 22, findings: 26, errors: 20, warnings: 6
Succeeded: 7, failed: 12, pending: 2, failure rate: 63.2%
Unrecovered failed payments: 11 ($316.45), recovered failed payments: 1 ($129.00)
Card-testing burst findings: 0, stale pending payments: 1

Per-gateway summary:
PayPal: 4 payments, 0 succeeded, 1 failed, 2 pending, failure rate 100.0%, unrecovered $24.50
Square: 3 payments, 0 succeeded, 3 failed, 0 pending, failure rate 100.0%, unrecovered $177.00
Stripe: 15 payments, 7 succeeded, 8 failed, 0 pending, failure rate 53.3%, unrecovered $114.95

Top decline reasons:
do_not_honor: 4
card_declined: 3
expired_card: 3
insufficient_funds: 1
```

`--json` emits the summary and the findings as JSON, and
`--out` writes the report to a file. JSON for the
warnings-only file:

```json
{
  "payments": 3,
  "findings": [
    {
      "payment": "P-401",
      "rule": "failed-payment-recovered",
      "severity": "warning",
      "message": "payment failed but order O-401 was later paid successfully, so the customer recovered"
    },
    {
      "payment": "P-403",
      "rule": "pending-payment",
      "severity": "warning",
      "message": "payment is still waiting for the gateway"
    }
  ],
  "errors": 0,
  "warnings": 2,
  "succeeded": 1,
  "failed": 1,
  "pending": 1,
  "failure_rate": 50.0,
  "unrecovered_failed": 0,
  "recovered_failed": 1,
  "unrecovered_value": 0.0,
  "recovered_value": 49.99,
  "card_testing_burst_findings": 0,
  "stale_pending": 0,
  "per_gateway": [
    {
      "gateway": "PayPal",
      "payments": 1,
      "succeeded": 0,
      "failed": 0,
      "pending": 1,
      "failure_rate": 0.0,
      "unrecovered_value": 0.0
    },
    {
      "gateway": "Stripe",
      "payments": 2,
      "succeeded": 1,
      "failed": 1,
      "pending": 0,
      "failure_rate": 50.0,
      "unrecovered_value": 0.0
    }
  ],
  "top_decline_reasons": [
    {
      "reason": "card_declined",
      "count": 1
    }
  ]
}
```

## Options

| Flag | Meaning |
| --- | --- |
| `--stale-days` | A pending payment older than this many days is stale (default: 7) |
| `--max-failure-rate` | A gateway failing more than this percentage of decided payments is flagged (default: 20) |
| `--burst-count` | Failed payments from one email within the burst window that count as card testing (default: 3) |
| `--burst-hours` | Hours in the card-testing burst window (default: 24) |
| `--as-of` | Date to compare timestamps against, for example 2026-11-18 (default: now) |
| `--json` | Emit JSON instead of text |
| `--check` | Exit 1 if any error is found |
| `--strict` | With --check, also exit 1 on warnings |
| `--out` | Write the report to a file |

## Edge cases

* A row with too many or too few fields is a malformed-row
  error and is skipped; the rest of the file is still
  audited.
* A bad amount or date is an error, and the row is still
  audited on the fields that did parse, so one bad value
  never hides the other problems on the payment. A payment
  with an unreadable amount is left out of the value
  totals (its unrecovered message shows `$?`), and a
  payment with an unreadable date is left out of the
  burst and pending checks.
* A failed payment with no order id gets no
  unrecovered or recovered finding, since recovery cannot
  be checked; it still gets the missing-order-id error.
* The gateway failure rate counts only decided payments
  (succeeded and failed). Pending and refunded payments
  do not count, and a gateway with fewer than 5 decided
  payments is never flagged, so a tiny gateway with one
  failure is not called out.
* The burst check groups by customer email,
  case-insensitive, and uses a sliding window, so four
  failures in 15 minutes count even when the first and
  last are the only ones inside any single hour.
  Payments with no email or no date are never grouped
  into a burst.
* A blank line in the file is skipped silently.
* `--as-of` makes runs reproducible: without it, the
  future-timestamp and pending-age checks compare against
  the current time.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

69 tests, all passing: timestamp and amount parsing,
status normalization, header aliases and BOM handling,
malformed rows, duplicate and missing payment ids,
missing order ids and gateways, invalid amounts and
timestamps, future timestamps, unrecovered and recovered
failures with the same-order rule, missing error codes,
card-testing bursts with the window and threshold,
repeated declines, stale and fresh pending payments,
gateway failure rates with the minimum-attempts rule,
summary totals and value math, per-gateway tables and
top decline reasons, text and JSON output, `--out` file
output, `--stale-days`, `--max-failure-rate`, and
`--burst-count` flags, and `--check` and `--strict` exit
codes. The same 69 tests pass under pytest.

## License

MIT. See [LICENSE](LICENSE).
