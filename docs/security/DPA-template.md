# Data Processing Addendum (template)

> This is a starting template, **not legal advice**. GaleQEA is self-hosted, so in
> most deployments the vendor is not a data processor. You (the operator) are the
> controller, and any hosted LLM/storage/identity provider you configure is your
> sub-processor. Have counsel review before use.

**1. Roles.** The Customer is the data **controller**. Self-hosted GaleQEA processes
personal data solely within the Customer's own infrastructure. Third-party services
the Customer configures (LLM provider, object storage, identity provider) act as the
Customer's **sub-processors** under the Customer's own agreements with them.

**2. Subject matter & duration.** Processing of the personal data described in
`docs/security/data-flow.md` for the duration of the Customer's use, plus any
retention the Customer configures (`retention_days`).

**3. Nature & purpose.** Test automation: authentication, authoring and running
tests, storing results and an audit trail.

**4. Categories of data subjects & data.** GaleQEA users (name, email, role) and any
personal data present in the Customer's application under test or test data.

**5. Sub-processors.** As listed in `docs/security/sub-processors.md`, each engaged
only when the Customer configures it. With the default offline configuration there are
none.

**6. Security measures.** argon2id password hashing; encrypted credential vault;
role-based access; HttpOnly/SameSite cookies + CSRF; strict CSP; signed, SBOM-attested
images; continuous vulnerability scanning; a hash-chained audit ledger.

**7. Data-subject rights.** Export (`GET /api/gdpr/users/{id}/export`) and erasure
(`POST /api/gdpr/users/{id}/erase`) endpoints support access and erasure requests;
erasure anonymises personal data while preserving audit-log integrity.

**8. International transfers.** None occur unless the Customer configures a service in
another region; then the Customer's agreement with that provider governs.

**9. Deletion/return on termination.** The Customer controls all data in its own
infrastructure and can export or erase it at any time.
