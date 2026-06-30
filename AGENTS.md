# Agent Instructions

## Security — No Secrets in This Repository

This is a **public repository**. Never store secrets, credentials, or sensitive values here.

Secrets and sensitive configuration belong exclusively in the protected repository:
**[k0eff/ansible-home-lab-protected](https://github.com/k0eff/ansible-home-lab-protected)**
which is checked out locally at `./protected/`.

### What must NOT be committed here

- Passwords, API keys, tokens, bearer credentials
- SSH private keys or passphrases
- Database connection strings containing credentials
- Cloud provider credentials (AWS, GCP, Azure, Cloudflare, etc.)
- Ansible vault passwords or unencrypted vault files
- Any value from `protected/inventories/group_vars/`

### Where secrets go instead

All sensitive values are stored in `protected/inventories/group_vars/all.yaml`
(and other files under `protected/`), which live in the private repo above.

Templates in `roles/*/templates/` may reference secret variables (e.g. `{{ litellm_db_password }}`),
but the variable values themselves must never appear in this repo.

### If you are unsure

When in doubt, put the value in the `protected/` repo and reference it as a variable.

## Evaluation System

Every implementation task requires an eval JSON in `evals/`.

**Format:** `NNN-{branch-slug}-{category}--{description}.json`
- `branch-slug`: `git branch --show-current | sed 's|/|-|g'`
- `NNN`: sequential per branch — find highest matching in `evals/`, increment by 1
- **Create with `status: "pending"` BEFORE coding. Set `status: "pass"` after validation.**
- Include "eval" or eval filename in commit message.
- Update existing eval if same file already covered. Supersede: set old `status: "disabled"`.

Full documentation: [koeff-gitroot-main/EVALS.md](https://github.com/k0eff/koeff-gitroot-main/blob/main/EVALS.md)
