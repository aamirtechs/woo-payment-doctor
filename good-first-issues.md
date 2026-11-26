# Good first issues

Seeded issues for new contributors. File each one on GitHub with
the `good first issue` and `help wanted` labels.

## 1. Add an unrecovered revenue table

Title: Show the unrecovered failed payments as a table

Description: After the per-gateway summary, print a short table
of the unrecovered failed payments sorted by amount, largest
first (payment id, order, gateway, amount, decline reason),
so a store owner can work the recovery list top-down without
reading every finding. Payments with an unreadable amount go
last.

Difficulty: Easy

Acceptance criteria: the table appears in text output with the
correct order for the sample file (P-109, P-110, and P-111
first at $59.00 each, then P-101 at $49.99); JSON output is
unchanged; a test checks the table rows.

## 2. Add a configurable repeat-failure threshold

Title: Let the repeated-decline count be configurable

Description: Add a --repeat-failures flag that sets how many
failed attempts on one order count as a repeated decline. The
default stays at three.

Difficulty: Easy

Acceptance criteria: an order with two failures is flagged as
a repeated decline when --repeat-failures 2 is passed, and is
not flagged without the flag; a test covers both cases; README
documents the flag.

## 3. Report the worst gateway in the summary

Title: Print the gateway with the highest failure rate

Description: Add one line to the text summary naming the
gateway with the highest failure rate among gateways with at
least 5 decided payments (gateway, failure rate, failed and
decided counts), so a store owner can see the biggest gateway
problem at a glance. When no gateway reaches the minimum, the
line is omitted.

Difficulty: Easy

Acceptance criteria: the line appears for the sample file naming
Stripe at 53.3%; it is absent for the clean file, where no
gateway reaches 5 decided payments; JSON output is unchanged;
a test checks both cases.

## 4. Flag first-attempt versus retry failures

Title: Split unrecovered failures by attempt number

Description: Add the attempt split to the per-gateway summary
lines: the unrecovered value followed by how much of it came
from first attempts, for example "unrecovered $114.95 ($49.99
first attempt)". The attempt column already parses, so no
input change is needed. Payments with an unreadable attempt
count as first attempts.

Difficulty: Easy

Acceptance criteria: the sample file shows Stripe with $114.95
unrecovered ($114.95 first attempt) and Square with $177.00
unrecovered ($59.00 first attempt); JSON per-gateway rows gain
a first_attempt_unrecovered_value field with the same numbers;
a test checks both.
