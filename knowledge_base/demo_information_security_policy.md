---
title: Demo Bank Information Security Policy
source_organisation: ReqPilot Demo Bank (fictional)
jurisdiction: India
version: 1.0
effective_date: 2026-01-01
doc_type: security_standard
authenticity: illustrative
applicability: All customer-facing and internal financial applications
---
# Demo Bank Information Security Policy

> ILLUSTRATIVE DEMONSTRATION POLICY. This is a fictional organisational policy written for the ReqPilot demo. It is not a law, regulation or official guideline.

## ISP-01 Access control and least privilege
Topics: access_control
Every application shall implement role-based access control in which each role is granted only the permissions required for its documented business function. Access rights shall be approved by the data owner before provisioning and revoked within 24 hours of a staff exit or role change. Privileged and administrative accounts shall be individually named, shall not be shared, and shall be reviewed by the system owner at least once every 90 days. Default, generic and vendor accounts shall be disabled or renamed before go-live.

## ISP-02 Multi-factor and step-up authentication
Topics: authentication, transaction_integrity
Customer login to internet and mobile banking shall require at least two independent authentication factors, such as a device-bound credential and a one-time password or biometric. Any single fund transfer of INR 50,000 or more, the addition of a new beneficiary, and any change to registered mobile number or email address shall trigger step-up authentication with a fresh one-time password. One-time passwords shall expire after 180 seconds and shall be invalidated after a single successful use or three failed attempts. Staff access to production consoles shall always require multi-factor authentication.

## ISP-03 Password and session management
Topics: authentication, access_control
Passwords and PINs shall have a minimum length of 8 characters for customers and 12 characters for staff, shall be checked against a list of known-compromised values, and shall be stored only as salted hashes using an adaptive algorithm such as bcrypt or Argon2. Accounts shall be locked for 30 minutes after 5 consecutive failed login attempts. Customer sessions shall expire after an idle timeout of 10 minutes and an absolute timeout of 60 minutes, and staff sessions after an idle timeout of 15 minutes. Session tokens shall be regenerated after login and invalidated on logout.

## ISP-04 Encryption in transit and at rest
Topics: encryption
All data in transit over public or internal networks shall be protected with TLS 1.2 or higher; TLS 1.0, TLS 1.1 and SSL shall be disabled. Mobile applications shall implement certificate pinning for bank-owned endpoints. Customer personal data, authentication secrets, card data and transaction records stored in databases, file stores and backups shall be encrypted at rest using AES-256 or an equivalent approved algorithm. Sensitive fields such as account numbers and identity document numbers shall be masked in user interfaces unless full display is required for the task.

## ISP-05 Cryptographic key management
Topics: encryption, access_control
Master and key-encryption keys protecting payment and authentication data shall be generated and stored in a FIPS 140-2 Level 3 (or higher) hardware security module. Data-encryption keys shall be rotated at least every 12 months, and immediately on suspected compromise. No cryptographic key, password or API secret shall be hard-coded in source code or stored in configuration files in plain text; secrets shall be retrieved from an approved secrets vault at runtime. Key custodian duties shall be split so that no single person can reconstruct a master key.

## ISP-06 Logging, monitoring and tamper-evident audit trails
Topics: audit_logging
Applications shall log authentication events, authorisation failures, administrative actions, changes to customer master data and all financial transactions with user ID, timestamp synchronised to a reference time source, source IP or device ID and outcome. Logs shall not contain passwords, full card numbers, CVV or one-time passwords. Security logs shall be forwarded to the central Security Operations Centre in near real time and retained for at least 180 days online and 5 years in archive. Audit trails shall be tamper-evident, for example through write-once storage or hash chaining, and shall be readable but not modifiable by application administrators.

## ISP-07 Secure software development lifecycle
Topics: secure_development, change_management
Every new system and every major release shall undergo threat modelling during design. Static application security testing (SAST) and software composition analysis (SCA) shall run on every build in the CI pipeline, and dynamic application security testing (DAST) shall run before every production release. Builds with unresolved critical or high findings shall not be promoted to production without a documented risk acceptance signed by the CISO. An independent penetration test shall be completed and all critical and high findings closed before the first release of any internet-facing application and at least annually thereafter.

## ISP-08 Vulnerability and patch management
Topics: secure_development, change_management
All internet-facing systems shall be scanned for vulnerabilities at least monthly and internal systems at least quarterly. Security patches shall be applied within 7 days for critical vulnerabilities, 30 days for high, and 90 days for medium severity, measured from vendor release or discovery. Where a patch cannot be applied in time, a compensating control and an exception approved by the CISO shall be recorded. Unsupported operating systems, databases and libraries shall not be used in production.

## ISP-09 Third-party and API integration security
Topics: third_party_api_security, authentication, encryption
Server-to-server integrations with payment networks, credit bureaus, KYC providers and other third parties shall use mutual TLS authentication. Externally exposed APIs shall be published only through the API gateway and shall use OAuth 2.0 with scoped, short-lived access tokens valid for no more than 15 minutes. The gateway shall enforce rate limiting of 100 requests per minute per client by default, schema validation of requests, and payload size limits. Third-party providers handling bank data shall complete a security assessment before onboarding and annually thereafter.

## ISP-10 Security incident response
Topics: breach_incident_reporting, audit_logging
The bank shall maintain a documented security incident response plan with defined roles, severity levels and escalation paths. Suspected security incidents shall be reported to the Security Operations Centre immediately on detection, and severity 1 incidents shall be escalated to the CISO within 1 hour. Incidents that meet regulatory reporting criteria shall be reported to the relevant authorities within 6 hours of detection. Evidence including logs and system images shall be preserved for forensic analysis, and a post-incident review shall be completed within 10 working days.

## ISP-11 Business continuity and disaster recovery
Topics: business_continuity
Systems classified as critical, including internet banking, mobile banking and payment processing, shall have a recovery time objective (RTO) of 2 hours and a recovery point objective (RPO) of 15 minutes. A disaster recovery site shall be maintained in a separate seismic zone within India. Disaster recovery drills involving actual switchover of critical systems shall be conducted at least twice a year, and results including any missed objectives shall be reported to the IT Strategy Committee.
