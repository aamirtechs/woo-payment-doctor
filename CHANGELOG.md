# Changelog

## 1.0.0 - 2026-11-21

Initial release.

* Failed payment audit over a WooCommerce payment
  attempts CSV export: failed payments with no later
  successful retry (lost revenue, with the lost amount),
  recovered failures, orders that failed three or more
  times, and stale pending payments.
* Card-testing burst detection: one customer email with
  three or more failed payments inside 24 hours, with a
  configurable count and window.
* Per-gateway failure rate audit with a configurable
  limit and a 5 decided payment minimum, plus a
  per-gateway table and the top decline reasons.
* Data quality checks: duplicate and missing payment
  ids, missing order ids and gateways, malformed rows,
  invalid amounts and timestamps, future timestamps, and
  unknown statuses.
* Defensive timestamp parsing (ISO 8601 with or without
  a timezone, plus common day/month/year forms) and
  amount parsing (plain numbers, currency symbols,
  thousands separators); a bad row is reported as a
  finding and never crashes the run. UTF-8 BOM headers
  and extra columns handled.
* Findings carry a payment id, a rule name, a severity
  (error or warning), and a message; errors and warnings
  are counted in the summary.
* Text and --json output, --out file output,
  --stale-days, --max-failure-rate, --burst-count,
  --burst-hours, and --as-of thresholds, and a --check
  CI gate that exits 1 on errors (and on warnings with
  --strict).
* Zero dependencies, Python 3.10 or newer.
