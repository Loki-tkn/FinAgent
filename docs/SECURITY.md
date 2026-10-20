# Security Self-Assessment & Architecture Documentation
## AI Personal Financial Agent (Fintechathon)

### 1. Executive Summary & Security Objectives
The **AI Personal Financial Agent** is designed as a secure, privacy-first personal financial advisory simulator. The system enforces strict role-based access control (RBAC), operates completely within a sandboxed execution paradigm, and guarantees that sensitive financial payment credentials are never collected, processed, or stored.

---

### 2. Role-Based Access Control (RBAC) Matrix

| Capability / API Endpoint | Viewer Tier | User Tier | Admin Tier |
| :--- | :---: | :---: | :---: |
| View Financial Dashboard & KPIs | :white_check_mark: | :white_check_mark: | :white_check_mark: |
| Run Read-Only What-If Simulations | :white_check_mark: | :white_check_mark: | :white_check_mark: |
| View Spending Anomaly Reports | :white_check_mark: | :white_check_mark: | :white_check_mark: |
| View Audit Logs | :white_check_mark: | :white_check_mark: | :white_check_mark: |
| Add / Delete Simulated Transactions | :x: | :white_check_mark: | :white_check_mark: |
| Create Financial Goals | :x: | :white_check_mark: | :white_check_mark: |
| Stage Sandbox Proposals | :x: | :white_check_mark: | :white_check_mark: |
| Approve / Reject Financial Proposals | :x: | :white_check_mark: | :white_check_mark: |
| Reset Demo Database & Reseed Data | :x: | :x: | :white_check_mark: |

---

### 3. Credential & Data Protection Policy

1. **Zero Financial Credential Storage**:
   - The application strictly **never** asks for, receives, or stores bank login credentials, OTP codes, card numbers, or CVVs.
2. **Deterministic Processing**:
   - All AI decision rules and statistical anomaly scans run locally without sending unencrypted user financial data to third-party public AI endpoints.
3. **Pydantic Validation**:
   - Every input payload is strictly typed and validated using Pydantic models with range checks (e.g. non-negative currency amounts, valid date strings).

---

### 4. Sandbox Isolation Architecture

All financial optimizations (e.g., budget category cuts, plan reallocations) follow a **Two-Phase Commit** design:

```
[ AI Rule Engine ] 
        │
        ▼ (Stage Staged Proposal)
[ Sandbox Isolation Hub ] ── Status: PENDING ──► [ User Review & Diff ]
        │                                                │
        │                                  ┌─────────────┴─────────────┐
        │                                  ▼                           ▼
        ▼                             [ APPROVE ]                 [ REJECT ]
[ Immutable Audit Trail ]                  │                           │
                                           ▼                           ▼
                               [ Apply to Active Plan ]    [ Mark Rejected / Close ]
```

- **Phase 1 (Staging)**: The engine generates a structured proposal (`action_type`, `impact_summary`, `payload_json`) with status `PENDING`. No live budget or goal parameters are mutated.
- **Phase 2 (User Authorization)**: The user inspects the impact diff and explicitly triggers `APPROVE` or `REJECT`.
- **Phase 3 (Audit Trail)**: Every transition is permanently recorded in the `audit_logs` SQLite table.

---

### 5. Known Risk Register & Mitigations

| Risk ID | Threat Scenario | Severity | Mitigation Strategy |
| :--- | :--- | :---: | :--- |
| **RISK-01** | User misinterprets simulated balance as real bank account balance | Medium | Explicit `SIMULATED SANDBOX` top banners, persistent role tags, and clear UI warnings. |
| **RISK-02** | Over-aggressive budgeting advice leading to unrealistic lifestyle constraints | Medium | Multi-tiered plan selection (Conservative, Balanced, Aggressive) with transparent category caps and feasibility scores. |
| **RISK-03** | Injection of negative or malformed transaction amounts | Low | Pydantic validation schemas with `gt=0` constraint and sanitized SQL parameter binding (`?` placeholders). |
| **RISK-04** | Unauthorized action execution in multi-user demo environments | High | Role-based dependency injection (`require_role("User")` / `require_role("Admin")`). |
