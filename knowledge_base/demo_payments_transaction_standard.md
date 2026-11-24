---
title: Demo Bank Payments and Transaction Processing Standard
source_organisation: ReqPilot Demo Bank (fictional)
jurisdiction: India
version: 1.0
effective_date: 2026-01-01
doc_type: organisational_policy
authenticity: illustrative
applicability: All payment channels including UPI, IMPS, NEFT, RTGS, card and wallet transactions
---
# Demo Bank Payments and Transaction Processing Standard

> ILLUSTRATIVE DEMONSTRATION POLICY. This is a fictional organisational policy written for the ReqPilot demo. It is not a law, regulation or official guideline.

## PAY-01 Transaction limits and velocity checks
Topics: transaction_integrity, fraud_monitoring
Every payment channel shall enforce configurable per-transaction, daily and monthly limits per customer. Default retail limits shall be INR 1,00,000 per UPI transaction, INR 2,00,000 per day across UPI, and INR 5,00,000 per day across IMPS and NEFT combined. For the first 24 hours after a new beneficiary is added, transfers to that beneficiary shall be capped at INR 25,000. Velocity rules shall block more than 10 outgoing transactions within 5 minutes from a single account pending review. Limit changes requested by customers shall require step-up authentication.

## PAY-02 Step-up authentication thresholds
Topics: authentication, transaction_integrity
Transactions of INR 50,000 or more, transactions initiated from a device registered within the previous 24 hours, and transactions flagged as medium risk by the fraud engine shall require an additional authentication factor before execution. The authentication challenge shall display the amount and beneficiary name so that the customer can verify what is being approved. Transactions shall not be executed if step-up authentication fails or times out after 180 seconds.

## PAY-03 Idempotency and duplicate prevention
Topics: transaction_integrity
Every payment request shall carry a unique client-generated idempotency key, and the payment service shall return the original result for any repeated request bearing the same key within 24 hours rather than executing a second debit. Requests that time out at the switch or network shall be resolved through status enquiry before any retry is attempted. Debits and corresponding ledger postings shall be atomic, so that a failed credit leg results in automatic reversal within 1 business day.

## PAY-04 Reconciliation and settlement
Topics: transaction_integrity, audit_logging
Transactions processed through each payment network shall be reconciled against the network settlement files and the core banking ledger at least once per settlement cycle and in any case daily. Unreconciled items shall be aged and escalated to Operations if not cleared within 2 business days. Reconciliation reports, break lists and resolution actions shall be retained as audit evidence for 10 years. Suspense and nostro accounts shall be reviewed daily.

## PAY-05 Real-time fraud monitoring
Topics: fraud_monitoring, transaction_integrity
All digital payments shall be scored by the fraud risk engine before authorisation, and the scoring decision shall be returned within 200 milliseconds at the 95th percentile. High-risk transactions shall be held and routed to the fraud operations queue, which shall be staffed 24x7. Rules and models shall consider at least device fingerprint, location, beneficiary history, amount deviation and transaction velocity. If the fraud engine is unavailable, payments above INR 10,000 shall fail safe by requiring step-up authentication.

## PAY-06 Customer transaction alerts
Topics: transaction_integrity, fraud_monitoring
Customers shall receive an SMS and in-app notification for every debit and credit transaction within 30 seconds of posting, showing amount, channel, masked account number, available balance and a fraud-reporting contact. Email alerts shall additionally be sent where an email address is registered. Alerts for debit transactions shall not be suppressible by the customer. Each alert message shall include a direct link or number to report an unauthorised transaction.

## PAY-07 Disputes, reversals and turnaround times
Topics: customer_grievance, transaction_integrity
Customers shall be able to raise a dispute for any transaction from the mobile app, internet banking, contact centre or branch, and shall receive a complaint reference immediately. Failed transactions where the account was debited but the beneficiary was not credited shall be auto-reversed within 1 business day (T+1). Disputes about unauthorised transactions reported within 3 days shall be credited provisionally within 10 working days while investigation is completed. Every dispute shall be resolved within 30 days and the customer informed of the outcome in writing.

## PAY-08 Transaction audit trail
Topics: audit_logging, transaction_integrity
Each payment shall have an end-to-end audit trail linking the customer request, authentication result, fraud score, network reference number (such as UTR or RRN), ledger posting and notification. Audit records shall be immutable, time-stamped and retained for 10 years. Authorised auditors shall be able to retrieve the full trail of any transaction by reference number within 5 minutes. Manual interventions on transactions, including force-posting and reversals, shall require maker-checker approval and shall be recorded in the trail.
