---
name: upgrade-claude-models
description: Add new Claude model IDs to the LiteLLM proxy (koeff-ai-stack) and change which model VS Code's Claude Code extension uses by default. Use when a new Claude model ships (e.g. Sonnet 5, Opus 5) or when the model picker in VS Code doesn't show a model that LiteLLM already serves.
---

# Upgrade Claude models in koeff-ai-stack

Stack: VS Code (Claude Code ext) → `headroom.local.koeff.com` → headroom → nginx-ingest → LiteLLM → Anthropic.

## 1. Find the real model ID

Anthropic doesn't always publish new IDs in docs immediately. Most reliable source: the official **Claude desktop app** binary (`/Applications/Claude.app`) — it ships ahead of API docs.

```bash
strings /Applications/Claude.app/Contents/Resources/app.asar | grep -oE "claude-[a-z]+-[0-9][a-z0-9_.-]*" | sort -u
```

Also check the bundled CLI inside the VS Code extension (use the **highest version number** under `~/.vscode/extensions/anthropic.claude-code-*`):

```bash
D=$(ls -d ~/.vscode/extensions/anthropic.claude-code-* | sort -V | tail -1)
strings "$D/resources/native-binary/claude" | grep -oE "claude-[a-z]+-[0-9][a-z0-9_.-]*" | sort -u
```

Cross-check both lists. Don't trust a single source — verify the ID appears in at least 2 places (one of them being the real Anthropic-shipped binary, not just a docstring example like `model:g.string().describe('e.g. "claude-sonnet-5"')`).

## 2. Add the model to LiteLLM config

Edit `roles/ops-settings/templates/headroom/litellm-config.yaml.j2`, add under `model_list`:

```yaml
  - model_name: claude-<new-id>
    litellm_params:
      model: anthropic/claude-<new-id>
```

Models route directly to `anthropic/` (not `openrouter/anthropic/`) — auth is BYOK: client's bearer token flows through nginx-ingest → `x-api-key` → LiteLLM forwards via `forward_llm_provider_auth_headers: true`. No Anthropic API key stored server-side.

## 3. Deploy

Ansible `--tags ops-settings` has been unreliable (0 hosts matched in past runs). Direct path:

```bash
export SSHPASS='<see group_vars/all.yaml: ansible_become_pass / ssh pass>'
sshpass -e scp -o StrictHostKeyChecking=no \
  roles/ops-settings/templates/headroom/litellm-config.yaml.j2 \
  koeffuser@192.168.31.152:/tmp/litellm-config.yaml.j2

sshpass -e ssh -o StrictHostKeyChecking=no koeffuser@192.168.31.152 '
KEY=$(sudo grep LITELLM_MASTER_KEY /opt/koeff-ai-stack/litellm.env | cut -d= -f2)
sed "s|{{ headroom_litellm_master_key }}|$KEY|" /tmp/litellm-config.yaml.j2 | sudo tee /opt/koeff-ai-stack/litellm-config.yaml > /dev/null
rm -f /tmp/litellm-config.yaml.j2
sudo docker compose -f /opt/koeff-ai-stack/docker-compose.yml restart litellm
'
```

Never put the SSH password or master key literally in a command string — pull them from `litellm.env` / `group_vars` server-side via `sed`, and pass the SSH password through `$SSHPASS` (sshpass `-e`), not `-p`.

## 4. Verify

```bash
export SSHPASS='<pass>'
KEY=$(sshpass -e ssh -o StrictHostKeyChecking=no koeffuser@192.168.31.152 \
  'sudo grep LITELLM_MASTER_KEY /opt/koeff-ai-stack/litellm.env | cut -d= -f2')
sshpass -e ssh -o StrictHostKeyChecking=no koeffuser@192.168.31.152 \
  "curl -s -H 'Authorization: Bearer $KEY' http://localhost:4000/models" \
  | python3 -c "import sys,json; [print(m['id']) for m in json.load(sys.stdin)['data']]"
```

Confirm the new model ID is in the list, then commit + push the `.j2` change.

## Why VS Code's model picker may not show the new model

LiteLLM serving a model is independent of whether the VS Code Claude Code extension's UI picker offers it. The picker list is gated behind a **GrowthBook** remote feature flag fetched by the extension at startup — not driven by the LiteLLM `/models` response, and not simply a binary version issue. A brand-new model can exist in the bundled CLI and still not appear in the dropdown.

**Workaround — force the model directly, bypassing the picker:**

Edit `~/Library/Application Support/Code/User/settings.json`:

```json
"claudeCode.environmentVariables": [
    { "name": "ANTHROPIC_BASE_URL", "value": "https://headroom.local.koeff.com" },
    { "name": "ANTHROPIC_MODEL", "value": "claude-<new-id>" }
]
```

Restart the VS Code window. `ANTHROPIC_MODEL` overrides the picker selection at CLI startup.

Inside an interactive Claude Code session (CLI, not headless), `/model claude-<new-id>` also works — the model setter accepts arbitrary strings without validating against a known list.
