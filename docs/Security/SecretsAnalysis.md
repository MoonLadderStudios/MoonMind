# Executive Summary  
MoonMind must securely handle user-provided secrets (API keys, tokens, credentials) whether supplied via environment or a UI. In surveyed agent platforms, best practice is to avoid plaintext storage: OpenClaw introduced a *SecretRef* system allowing env-file, file, or exec-provider (e.g. Vault, 1Password) sources【10†L231-L239】【34†L360-L368】. KiloClaw (a managed OpenClaw hosting) stores secrets encrypted (RSA-OAEP + AES-256-GCM) in its database, decrypting them only inside isolated VMs【7†L147-L155】【7†L161-L164】. DeerFlow and CrewAI rely on environment variables (.env) with strict file‐ignore policies; neither provides built-in vaults or encryption【23†L557-L566】【27†L245-L248】.  

Currently MoonMind uses a simple `.env` approach (see `.env-template` listing many API keys【50†L1-L8】【50†L49-L57】), with credentials passed to Docker/FastAPI and sanitized from logs. No encryption-at-rest or secret-manager integration exists.  

We recommend a hybrid design: allow both `.env` (for bootstrap) and secure UI input. Store secrets in the database **encrypted at rest** using a cloud KMS or Vault key. Decryption happens only in application memory when needed. All transport (API/UI calls, database connection) uses TLS. Implement role-based access so only authorized code paths can read secrets. Provide audit logs for secret creation/rotation. Use short-lived credentials or refresh tokens when possible and enforce rotation policies.  

**Key Proposal:** Implement MoonMind secret storage as (encrypted) fields in the database, with keys managed by a KMS (AWS/GCP/Azure) or HashiCorp Vault. The application reads secrets via a secure API endpoint (protected by authentication) that decrypts them on-the-fly using the KMS. UI users can submit secrets via the web dashboard, which stores them encrypted. Include migration scripts to export/import `.env` secrets.  

**Comparison:** Below is a summary of approaches. Our recommended design (Encrypted DB + KMS) scores high on security and moderate on complexity. 

| Approach                      | Storage              | Encryption            | Rotation                    | Complexity           | Cost              | Suitability (MoonMind)              |
|-------------------------------|----------------------|-----------------------|-----------------------------|----------------------|-------------------|-------------------------------------|
| **.env file**                 | Local filesystem     | None by default       | Manual (edit `.env`)       | Low                  | Very Low          | Low (good for dev; insecure in prod) |
| **UI + plain DB fields**      | Application DB       | None                  | Manual (reset value)       | Moderate             | Low               | Very Low (exposes plaintext)         |
| **Encrypted DB fields**       | DB (local/Cloud)     | AES-256 (via KMS key) | Via KMS/Vault (rotate master) | Moderate             | Low-Medium        | High (encrypts at rest)             |
| **Cloud Secrets Manager**     | Secret store service | Provider-managed (AES-256) | Built-in rotation (configurable) | Medium-High        | Medium-High       | High (secure, centralized)          |
| **Vault (self-hosted)**       | Vault key-value store| AES-256 (Vault keys)  | Built-in rotation (policies) | High                | Medium            | High (very secure, self-managed)    |
| **Hybrid (Vault + DB)**       | DB (encrypted) + Vault for key | AES-256 (Vault) | Vault rotation           | High                | Medium            | Very High (defense-in-depth)         |

A concise risk checklist is also included to highlight residual threats and mitigations. The design favors strong encryption, key-management, and audit controls, balancing security with operational feasibility.

---

## 1. Secret Handling in Agent Platforms  

### OpenClaw (open-source AI agent framework)  
- **Defaults:** By default OpenClaw stores all keys/tokens **in plaintext** under `~/.openclaw/openclaw.json` or `auth-profiles.json`【8†L122-L130】. This poses leakage risk (e.g. via backups or Git commits).  
- **Env/.env support:** OpenClaw allows `${VAR}` expansion in configs. It searches env vars in this order: process env, local `.env`, `~/.openclaw/.env`, inline config env block【8†L169-L177】【8†L179-L183】. A recommended setup is to put secrets in `~/.openclaw/.env` (out of VCS)【8†L179-L183】.  
- **SecretRef system:** OpenClaw’s *SecretRef* lets you define secret providers so that values never touch config files【10†L231-L239】. Built-in providers include:
  - **`env` provider:** reads allowed env vars (with an allowlist)【34†L305-L310】.
  - **`file` provider:** reads JSON or single-value files (path must be secured)【34†L312-L320】.
  - **`exec` provider:** runs a command/CLI to retrieve secrets (supports allowlist, trustedDirs)【34†L324-L333】. This is the bridge to external vaults or CLI-based secrets. 
- **External stores:** Examples (config JSON fragments):
  - **HashiCorp Vault CLI:**  
    ```json
    {
      "secrets": {
        "providers": {
          "vault_openai": {
            "source": "exec",
            "command": "/usr/bin/vault",
            "args": ["kv","get","-field=token","secret/openai-api"],
            "passEnv": ["VAULT_TOKEN","VAULT_ADDR"]
          }
        }
      },
      "models": {
        "providers": {
          "openai": {
            "apiKey": { "source": "exec", "provider": "vault_openai", "id": "value" }
          }
        }
      }
    }
    ```  
    *Example from OpenClaw docs【34†L393-L402】【34†L411-L419】.* 
  - **AWS SecretsManager:** Use `aws secretsmanager get-secret-value ...` via exec. E.g.  
    ```yaml
    secrets:
      providers:
        aws_secrets:
          source: "exec"
          command: "aws"
          args: ["secretsmanager","get-secret-value","--secret-id","openclaw/anthropic","--query","SecretString","--output","text"]
    ```
  - **1Password CLI:** As demonstrated by a user, store secrets in 1Password vault and fetch via `op` CLI【39†L107-L116】【39†L129-L138】.  
- **Access & Encryption:** OpenClaw itself does **not** encrypt secrets at rest; it relies on underlying OS permissions. (Issue #7916 proposes age/sops or keychain integration as enhancements【35†L235-L244】【35†L262-L271】.) Secrets are decrypted only in memory after resolution. OpenClaw’s CLI commands support **audit** (`openclaw secrets audit`) to detect plaintext secrets and **reload** safely【28†L129-L138】【30†L0-L1】.  
- **Rotation & Audit:** Rotation is manual: update the store (env, Vault, etc.), restart `gateway`, verify via `openclaw secrets list` and `openclaw channels status`【10†L413-L421】【39†L151-L159】. The CLI audit tool identifies leftover plaintext (e.g. OAuth tokens may remain if auto-rotating)【39†L151-L159】.  
- **Libraries/Tools:** OpenClaw uses its own config parser and internal SecretRef resolver. Integrations via `exec` allow any CLI (vault, AWS CLI, sops, bw, op) as providers【34†L360-L368】【39†L107-L116】.

### KiloClaw (managed OpenClaw SaaS)  
- **Architecture:** Each customer runs OpenClaw in a dedicated micro-VM (Firecracker) on Kilo’s infrastructure【7†L57-L65】【7†L125-L133】. Tenant data (VM disk volumes) are isolated and encrypted.  
- **Secret storage:** Customer API keys and chat tokens are **encrypted in Kilo’s database** using strong crypto (RSA-OAEP + AES-256-GCM)【7†L147-L155】. They remain encrypted at rest. Keys are decrypted only when the customer’s VM boots, making them available inside that isolated VM【7†L147-L155】. Thus secrets are never exposed to the multitenant control plane in plaintext.  
- **Transmission & storage:** All external traffic uses TLS【7†L159-L164】. Storage volumes are encrypted at infrastructure level【7†L159-L164】.  
- **Access Control:** Tenant isolation is enforced via five layers (identity-based routing, dedicated app enclaves, wireguard mesh, VM boundary, dedicated encrypted storage)【7†L87-L96】【7†L137-L142】. No customer can see another’s secrets.  
- **Rotation & Audit:** Details aren’t public, but presumably customers can update keys through UI or API. Kilo plans “short-lived token exchange and in-memory secret stores” for future enhancement【7†L152-L156】. The independent review praised Kilo’s architecture【7†L53-L60】, implying auditing and key lifecycle management meet enterprise standards.  
- **Libraries/Tools:** Not specified, but likely AWS KMS or similar underlies encryption. Kilo’s whitepaper/blog is the primary source【7†L147-L155】. 

### DeerFlow (ByteDance’s superagent framework)  
- **Configuration:** DeerFlow reads a YAML `config.yaml`. It supports substituting secrets via environment variables. The docs show:  
  ```
  models:
    - api_key: $OPENAI_API_KEY   # Reads from environment
  ```  
  and common variables like `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, etc.【23†L559-L567】.  
- **.env support:** While not explicitly documented beyond environment substitution, the `.env` file is likely used (the config says `# Uncomment GITHUB_TOKEN in .env` for GitHub skill)【23†L545-L554】.  
- **Storage:** No DB or vault: secrets live in `config.yaml` only via `$VAR` references, or in a `.env` file outside version control. The config is git-ignored (best practice【23†L589-L594】).  
- **Encryption:** None built-in. All secrets are plaintext in the caller’s environment. DeerFlow expects secure handling externally.  
- **Access Control / Audit:** DeerFlow itself does not provide access controls or auditing for secrets. It warns to never commit secrets to git【23†L589-L594】. It does not log sensitive values (though not explicitly stated, any built-in logging should mask tokens).  
- **Rotation:** Fully manual: update env/config value and restart.  
- **Libraries/Tools:** Pure Python with LangGraph; no secret-management libs. The docs emphasize best practices (ignore configs, env vars)【23†L589-L594】.

### CrewAI (CrewAI Inc. agent framework)  
- **Configuration:** CrewAI (open-source Python) instructs users to set API keys via environment (e.g. in a `.env` file). The docs explicitly say *“Never commit API keys to version control. Use environment files (.env) or your system’s secret management.”*【27†L245-L248】. By default, each LLM provider’s key is read from its environment variable (e.g. `OPENAI_API_KEY`).  
- **UI/Cloud Offering:** CrewAI offers a managed “Crew Control Plane” with a UI that likely allows entering keys. (For example, Portkey integration docs show generating a “virtual key” in a dashboard【20†L770-L778】). Internally, the control plane would store such keys, but details aren’t public. We assume standard encryption (HTTPS, DB encryption-at-rest) on the hosted UI.  
- **Storage (OSS):** In the open-source framework, secrets are **not stored** by CrewAI itself; your code simply loads env vars and passes them to SDKs.  
- **Encryption:** None built-in in code. TLS is used for API/model calls. If running self-hosted, encrypting `.env` is user responsibility. No default encryption at rest in code.  
- **Access Control / Audit:** No built-in audit for secrets. CrewAI **does** redact secrets from telemetry/logs according to docs: *“OpenAI-compatible endpoints... including secrets and environment variables, with the exception of ... if `share_crew` is enabled.”*【15†L7-L10】 (meaning secrets aren’t sent out). CrewAI’s Enterprise suite touts *“Secure by default”*, but specifics are opaque.  
- **Rotation:** Manual: update env or Vault, restart.  
- **Libraries/Tools:** CrewAI has no secret-manager integration by itself. It mentions Portkey (external service) for secure key storage【20†L770-L779】 and OAuth flows via its CLI tools【46†L437-L441】. In community forums, staff recommend simply using `.env` and treating it like any Python app【17†L26-L33】.

## 2. MoonMind Current Secret Handling  
MoonMind’s **GitHub repo** shows no specialized secret-management code. Investigation reveals:  

- **.env Usage:** The root contains `.env-template` (and `.env.vllm-template`) which list many API key placeholders: e.g. `OPENAI_API_KEY=`, `ANTHROPIC_API_KEY=`, `GOOGLE_API_KEY=`, etc.【50†L25-L33】【50†L61-L69】. The **Quick Start** instructs: `cp .env-template .env` and edit it【46†L427-L432】. These environment variables are injected into the Docker containers and read by the FastAPI backend and workers.  
- **Docker / Compose:** Secrets (like OAuth credentials for Codex/Gemini) can also be set via `./tools/auth-codex-volume.sh` scripts【46†L437-L441】, which presumably mount OAuth tokens.  
- **Log Sanitization:** The README notes “credentials are automatically sanitized from logs”【46†L454-L459】, implying the code strips known secret patterns from output. However, this is reactive and does not protect at-rest storage.  
- **Database:** MoonMind uses PostgreSQL (via Temporal) for workflow data, and likely stores user configs/tasks. The repo’s schema or code for storing API keys is not evident; if any UI allows entering secrets, they would have to be saved somewhere (likely in the DB). No migration script for `.env` is present.  
- **Encryption/Access:** No explicit encryption code is found. By default, environment variables in Docker or container secrets are in plaintext inside the container. The Postgres DB (if used for secrets) is not known to encrypt specific fields.  
- **Conclusion:** MoonMind currently *only* documents .env-based secrets. Other options (Vault, encrypted DB, etc.) are not implemented. We mark “secret handling unspecified beyond .env usage”.

## 3. Secret Management Options for MoonMind  

We compare several strategies below, focusing on **security**, **threat model**, **complexity**, and **cost**. MoonMind is a self-hosted agent orchestrator, likely written in Python/Node.

### 3.1 .env File / Environment Variables  
- **Storage:** Secrets live in a local `.env` file (or injected via system env). They are loaded on application start (FastAPI reads them).  
- **Encryption at Rest:** *None*. The file resides on disk in plaintext (often ignored by git).  
- **In Transit:** Not applicable until used (LLM API calls via HTTPS).  
- **Access Control:** Rely on OS permissions (restrict `.env` to the `moonmind` user, e.g. `chmod 600`). Docker also can mount it; any process in the container could read it.  
- **Rotation:** Manual: Update the `.env` and restart. No support for in-flight updates.  
- **Auditing:** No built-in audit. OS auditing could track file access, but not provided by app. Logs won’t contain raw values if sanitized.  
- **Threat Model:** If an attacker gains file/volume access (malicious insider or another container), they can steal secrets easily. Also vulnerable to accidental check-ins.  
- **Pros:** Very simple, no extra infra. Cheap, easy to implement (just `python-dotenv`).  
- **Cons:** Insecure for shared/production environments【8†L142-L150】. Secrets may leak to logs or backups【8†L142-L150】【39†L42-L51】. No auditing/rotation support【39†L42-L51】.  
- **Complexity:** Very low. Suitable for dev or single-user home use only.  
- **Cost:** Negligible (no extra services).  
- **Libraries:** Use `python-dotenv` or Node’s `dotenv`.  
- **Suitability:** **Poor** for MoonMind production, but OK for initial prototyping.

### 3.2 UI Input Saved to DB (Plaintext)  
- **Storage:** Users enter keys in a web dashboard; secrets stored in DB fields (varchar). Database has default at-rest encryption (database-level disk encryption) but field values are plaintext to DB.  
- **Encryption at Rest:** Only disk-level encryption (e.g. PG TDE) if enabled. Field values visible to DB admin.  
- **In Transit:** TLS for UI/API calls can be enforced.  
- **Access Control:** App code should restrict who can create/read secrets (e.g. only user or role). DB roles need permission controls.  
- **Rotation:** Requires UI to allow changing key; old keys should be deleted via UI. No automated rotation.  
- **Auditing:** Application logs could record who created/updated keys (store metadata). But value not logged.  
- **Threat Model:** If DB is compromised or backup taken, secrets are exposed. Insider threat (DB admin) sees raw keys.  
- **Pros:** Convenient for end-users; integrates with UI.  
- **Cons:** Secrets at-rest are not cryptographically protected. High risk if DB leaks.  
- **Complexity:** Moderate (requires UI forms, new DB columns).  
- **Cost:** Low (just DB).  
- **Libraries:** Standard DB ORM (SQLAlchemy/Knex).  
- **Suitability:** **Low**. Acceptable only if coupled with encryption, else not for sensitive keys. 

### 3.3 Encrypted Database Fields (using KMS-managed keys)  
- **Storage:** Secrets stored in DB encrypted. Use a data encryption key (DEK) per secret or per-user, encrypted with a master key from a KMS (AWS/GCP KMS, Azure Key Vault, or Vault).  
- **Encryption:** E.g. AES-256-GCM per field. Master key stored in KMS/Vault. DEKs can be stored in DB (encrypted blob) or fetched on demand.  
- **In Transit:** TLS between app and DB; secrets decrypted only in application memory.  
- **Rotation:** KMS supports rotating master keys. Application needs logic to re-encrypt existing secrets with new DEKs (migration).  
- **Access Control:** Only backend server (with KMS permissions) can decrypt. If DB dumped, attacker sees only ciphertext.  
- **Auditing:** KMS provides logs on key usage (AWS CloudTrail, Google Audit Logs, Vault audit logs). App can log when a secret is accessed.  
- **Threat Model:** Protects against DB compromises. If attacker gets KMS credentials, they can decrypt. Requires securing KMS credentials (e.g. instance IAM role).  
- **Pros:** Strong at-rest security, minimal exposure. Good for moderate threat models.  
- **Cons:** More complex to implement: requires KMS integration, key handling code. Partial overhead on read/write (round-trip to KMS, or double-encrypt).  
- **Complexity:** Moderate to high. Requires dev effort for encryption/decryption in code, careful key management.  
- **Cost:** Low to moderate (KMS usually has small API costs and key storage fees).  
- **Libraries:** Python: `aws-encryption-sdk`, Google `cloud-kms`, `cryptography` (with DEK management). Node: `@aws-sdk/client-kms`, `node-jose`, etc.  
- **Suitability:** **High**. Balances security and cost. Good for on-prem or cloud.

### 3.4 Cloud Secret Manager (e.g. AWS Secrets Manager, Azure Key Vault, GCP Secret Manager)  
- **Storage:** Secrets stored in managed secret-store service; not in application DB.  
- **Encryption:** Provider encrypts secrets (usually AES-256) at rest.  
- **In Transit:** TLS.  
- **Rotation:** Built-in automatic rotation (with Lambda etc.) or manual. AWS/Azure/GCP allow rotation policies.  
- **Access Control:** IAM policies restrict who/what can access each secret. Fine-grained (e.g. per secret, per role).  
- **Auditing:** Cloud audit logs record every get/rotate action.  
- **Threat Model:** Protects against DB compromise (secrets elsewhere). Must secure app’s cloud credentials/role to access these services. Mitigates key leakage but adds dependency on cloud.  
- **Pros:** Mature, secure, minimal dev work. Automatic auditing and rotation. Useful for multi-service environments.  
- **Cons:** Cost (per secret and API calls); vendor lock-in; network latency.  
- **Complexity:** Medium. Application needs cloud SDK integration and secret fetch logic.  
- **Cost:** Medium (e.g. AWS SM ~$0.40 per secret per month + request charges).  
- **Libraries:** AWS: `boto3`/`@aws-sdk`, Azure: `azure-keyvault-secrets`, GCP: `google-cloud-secret-manager`.  
- **Suitability:** **High for cloud deployments**. If MoonMind runs on AWS/Azure/GCP, this is a strong option. For pure on-prem, less so. 

### 3.5 HashiCorp Vault (self-hosted or cloud)  
- **Storage:** Vault server (infrastructure to run/maintain). Stores secrets in its database (encrypted with HSM or master key).  
- **Encryption:** AES-256-GCM by default. Keys managed by Vault (can use KMS or HSM).  
- **In Transit:** TLS.  
- **Rotation:** Vault supports dynamic credentials and periodic rotation policies. E.g. database credentials can auto-rotate.  
- **Access Control:** Vault policies/token control who can read which secrets. Supports namespaces and ACLs.  
- **Auditing:** Vault audit device can log all access (to file or syslog).  
- **Threat Model:** Very strong isolation. Even Vault admin only sees ciphertext without unseal keys. If deployed robustly (HA, HSM), highly secure.  
- **Pros:** Enterprise-grade security features (dynamic secrets, versioning, PKI). No vendor lock-in.  
- **Cons:** Operational overhead (setup HA, unseal, token management). Single point-of-failure if mismanaged.  
- **Complexity:** High. Requires installing and operating Vault cluster. Application code uses Vault API or CLI.  
- **Cost:** Medium (infrastructure + human) if self-hosted; HashiCorp Cloud Vault has fees.  
- **Libraries:** Python: `hvac`; Go: official client; Node: `node-vault`.  
- **Suitability:** **Very High** if team can manage it. Best for organizations with strict security/compliance needs.

### 3.6 Hybrid (DB + Vault/KMS + .env)  
A combination approach can mix methods. For instance: use `.env` for initial deployment and admin credentials, then migrate secrets into DB encrypted with Vault-managed keys. Or store non-sensitive config in `.env` and critical keys in Vault. This adds defense-in-depth but increases complexity.  

## 4. Recommended MoonMind Design  

We propose a **hybrid design** that supports both `.env` and secure UI, with strong protections:

1. **Bootstrapping via .env:** Continue allowing `.env` for initial secrets (e.g. database credentials, service config). These should still be kept out of VCS. Clearly document that `.env` is only for initial setup or trusted local scenarios. For real API keys, prefer the UI option.

2. **UI-based Secret Entry:** Implement web UI endpoints for managing secrets (e.g. for LLM API keys, OAuth tokens). This involves:
   - A **frontend form** for the user to enter a new secret value.
   - A **secure backend API** (FastAPI endpoint) to receive the value over HTTPS. Authentication must ensure only an authorized admin/user can do this. 
   - The backend **encrypts** the secret using the KMS/Vault and stores the ciphertext in the PostgreSQL database.
   - E.g., DB table `user_secrets` with columns `(id, name, ciphertext, kms_key_id, updated_at)`. A migration can add these fields. The code decrypts on retrieval.
   - *Example DB schema snippet:*  
     ```sql
     CREATE TABLE secrets (
       id SERIAL PRIMARY KEY,
       name TEXT UNIQUE NOT NULL,
       ciphertext BYTEA NOT NULL,
       kms_key_id TEXT NOT NULL,   -- which KMS key encrypted this
       updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
     );
     ```
   - **Access Control:** Restrict the secrets table so only the backend service (via DB user) can read `ciphertext`. The application enforces logic, never exposing raw values to frontend or logs.  

3. **Encryption-at-Rest:** Use a **Key Management Service**. For example, AWS KMS:
   - Generate a symmetric CMK (customer master key). Give the application’s role permission `Decrypt/Encrypt`.
   - On secret entry: the app calls KMS to generate a data key (DEK), encrypt secret with DEK (AES-GCM), then store DEK’s ciphertext+IV (possibly prepended) and associated KMS key ID in DB. Alternatively use Envelope Encryption via KMS APIs which do this automatically.
   - On usage: app retrieves `ciphertext` from DB, calls KMS to decrypt (passing KMS key ID and ciphertext blob) to get plaintext.
   - Ensure **in-transit encryption** (use KMS HTTPS endpoint, DB connection via TLS).  
   - *Code example (Python pseudocode):*  
     ```python
     # Store secret
     response = kms.generate_data_key(KeyId=CMK_ID, KeySpec='AES_256')
     ciphertext_blob = response['CiphertextBlob']  # encrypted DEK
     plaintext_key = response['Plaintext']         # DEK bytes
     iv = os.urandom(12)
     aes = AES.new(plaintext_key, AES.MODE_GCM, nonce=iv)
     ct, tag = aes.encrypt_and_digest(secret_value.encode())
     store_in_db(name, iv + ct + tag, CMK_ID, response['KeyId'])
     
     # Retrieve secret
     row = get_secret_from_db(name)
     iv, ct, tag = row.ciphertext[:12], row.ciphertext[12:-16], row.ciphertext[-16:]
     plaintext_dek = kms.decrypt(CiphertextBlob=row.kms_cipher_blob)['Plaintext']
     aes = AES.new(plaintext_dek, AES.MODE_GCM, nonce=iv)
     secret = aes.decrypt_and_verify(ct, tag).decode()
     ```  
   - *Note:* Many KMS SDKs handle GCM easily.  

4. **Key Management and Rotation:** Use KMS’s key rotation. If required, re-encrypt existing secrets with new keys or support multi-key decryption. AWS/GCP allow scheduled CMK rotation. Update logic to accept multiple `kms_key_id` if old keys linger.

5. **Encryption-in-Transit:** All UI/API communications use TLS. Application-to-DB uses TLS. If running in Kubernetes, use Secrets for DB password, etc., and mTLS.

6. **Audit Logging:** Record events for secret changes. For example, in the DB or app logs (not containing the secret itself) note: *“User X updated secret Y at time T”*. Use KMS audit logs (e.g. CloudTrail) for decryption operations. Ensure logs do not include plaintext (only identifiers and metadata).

7. **Backup/Restore:** Since secrets are in the DB, back them up as part of DB backups. Because they’re encrypted, backup files contain only ciphertext. For disaster recovery, ensure the KMS master key is managed (if using cloud KMS, the key stays intact in cloud; if Vault, backup Vault’s unseal keys).

8. **Migration Path:** Provide a one-time tool/script to import existing secrets:
   - **From .env:** Read each key in `.env`, encrypt via KMS, insert into DB `secrets` table. Remove the value from `.env`.  
   - **From existing DB fields:** If any plaintext keys were stored, migrate similarly.  
   This tool ensures no downtime: after migration, remove plaintext and restart services.

9. **UI Integration:** The mission control UI should allow secret entry and status:
   - A form to add/edit a named secret (key name, value). The value is never shown after entry.  
   - In the UI listing, show only masked or “set/empty” status (never display value).  
   - Use role-based checks (e.g. only admin users can manage secrets).

10. **Zero-Trust Considerations:** Treat secrets as highly sensitive. Examples: enforce short-lived tokens for Gemini/Codex where possible (OAuth). For long-lived keys (Anthropic, OpenAI), document that only encrypted storage is used.

```mermaid
flowchart LR
    A[User/Admin] --> B[MoonMind Web UI]
    B --> C[FastAPI Backend]
    C -->|Encrypt via KMS| D[(Key Management Service)]
    C -->|Store ciphertext| E[(PostgreSQL Database)]
    D -->|Decrypt on demand| C
    C -->|Use plaintext secret| F[LLM API Calls (HTTPS)]
    style E fill:#f9f,stroke:#333,stroke-width:2px
    style D fill:#ff9,stroke:#333,stroke-width:2px
```
*Diagram: MoonMind secret workflow. UI-admin provides secret → backend encrypts with KMS → ciphertext stored in DB. On use, backend decrypts via KMS for API calls.*

## 5. Implementation Details  

- **Backend Code:** Use language-specific KMS SDK. In Python, `boto3` (AWS) or `google-cloud-secret-manager` is typical. For a Node frontend service, `@aws-sdk/client-kms`. Ensure minimal privileges: e.g. allow KMS Decrypt only for needed CMKs.  
- **DB Schema (example):**  
  ```sql
  CREATE TABLE secrets (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    ciphertext BYTEA NOT NULL,
    kms_key_id TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
  );
  ```  
- **API Endpoints:**  
  - `POST /api/secrets` (admin-only): JSON `{ name, value }`. Backend encrypts `value` and upserts DB.  
  - `GET /api/secrets` (admin-only): list secret names/status (no values).  
  - `PUT /api/secrets/{name}`: update a secret (same as POST).  
  - `DELETE /api/secrets/{name}`: remove it (overwrites DB, possibly call KMS to re-encrypt empty).  
  - **In all cases**, require authentication (e.g. OAuth or admin token).  
- **Code Snippet (Python Flask/FastAPI style):**  
  ```python
  from fastapi import FastAPI, HTTPException, Depends
  import boto3, os
  from sqlalchemy import text

  kms = boto3.client('kms')
  CMK_ID = os.getenv("MOONMIND_KMS_KEY_ID")
  db = ...  # SQLAlchemy engine

  @app.post("/api/secrets")
  def set_secret(name: str, value: str, user=Depends(authenticated_admin)):
      # Encrypt plaintext value
      resp = kms.encrypt(KeyId=CMK_ID, Plaintext=value.encode())
      ciphertext = resp['CiphertextBlob']
      # Store in DB
      sql = text("INSERT INTO secrets (name, ciphertext, kms_key_id) "
                 "VALUES (:name, :ct, :kid) "
                 "ON CONFLICT(name) DO UPDATE SET ciphertext=:ct, kms_key_id=:kid, updated_at=now()")
      db.execute(sql, {"name": name, "ct": ciphertext, "kid": CMK_ID})
      return {"status": "ok"}

  @app.get("/api/model_query")
  def query_llm(params, secret_name="OPENAI_API_KEY"):
      # Fetch ciphertext from DB
      row = db.execute(text("SELECT ciphertext,kms_key_id FROM secrets WHERE name=:name"),
                       {"name": secret_name}).fetchone()
      if not row:
          raise HTTPException(400, "Secret not found")
      # Decrypt via KMS
      dec = kms.decrypt(CiphertextBlob=row[0])
      api_key = dec['Plaintext'].decode()
      # Use api_key to call LLM...
      ```
- **Migration Script:** A one-off script to read `.env`, encrypt each, and insert into DB `secrets`. After migration, clear `.env` of API keys or remove it entirely from production.

## 6. Risk Mitigation Checklist  
- **Exposure via Logs:** Verify no plaintext secrets are logged. Use log filters to mask patterns (as already noted in MoonMind).  
- **Least Privilege:** The app’s execution role should have *only* permissions `kms:Decrypt/Encrypt` on the specific key. No broad IAM rights.  
- **Infrastructure Security:** Ensure the KMS/Vault service is in a secure network (VPC endpoints if in cloud), and audit logs are retained.  
- **Rotation:** Enforce periodic key rotation policies for long-lived API keys. Use versioned secrets in the UI or let users manage rotation.  
- **Backup Security:** If DB backups contain ciphertext, back them up securely (encrypt the backup or store in a protected bucket).  
- **Admin Access:** Limit who can use the secrets API (multi-factor authentication). Monitor admin login events.  
- **Penetration Testing:** Verify that compromise of a single VM or container does not reveal secrets (because actual keys come from KMS only on request).  
- **Dependency Updates:** Keep KMS SDK and cryptography libraries up-to-date to avoid vulnerabilities.  

## 7. Sources  
- OpenClaw official docs and tutorials【8†L122-L130】【10†L231-L239】【34†L360-L368】.  
- KiloClaw blog (security architecture)【7†L147-L155】【7†L159-L164】.  
- DeerFlow configuration guide (ByteDance)【23†L557-L566】【23†L589-L594】.  
- CrewAI documentation and community posts【27†L245-L248】【17†L26-L33】.  
- MoonMind GitHub repository (code, .env-template)【46†L427-L432】【50†L61-L69】.  
- Other authoritative sources: none specifically beyond official docs and whitepapers.  

