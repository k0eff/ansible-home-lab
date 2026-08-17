# RocketChat and Portainer upgrade — vm700

Written 2026-08-17. Every version number and date below was read from the live host,
from upstream release metadata, or from vendor lifecycle documentation on that date;
sources are named inline so the whole thing can be re-derived rather than trusted.

## 1. Measured starting state

Read off vm700 (192.168.31.152) on 2026-08-17.

| Component | Declared in repo | Actually running | Gap |
|---|---|---|---|
| RocketChat | `rocket.chat:8.4.3` | 8.4.3 (`/api/info`) | in step |
| RocketChat MongoDB | `mongo:8.2.4` | 8.2.4, FCV 8.2 | in step, 8 patches behind |
| Portainer | `portainer/portainer-ce:latest` | **2.33.6** (`/api/status`) | **7 months, unmaintained** |

Portainer's container was created **2026-01-28** and had not been replaced since.
`latest` on Docker Hub had moved to 2.39.6 by 2026-08-13. The tag said "rolling" and
the install was frozen — section 3, claim C, explains how both were true at once.

Capacity, for the record: `/var/lib/docker` 93 GB free of 196 GB, `/` 64 GB free,
10.4 GB RAM available of 16 GB. The `rocketchat` database is 60 MB of data and 22 MB
on disk. Nothing here is constrained by resources.

## 2. Targets

| Component | From | To | Why this one |
|---|---|---|---|
| RocketChat | 8.4.3 | **8.5.2** | the LTS line; supported to 2027-06-30 |
| RocketChat MongoDB | 8.2.4 | **8.2.12** | newest patch inside the verified 8.2 line |
| Portainer | 2.33.6 | **2.39.6** | the LTS line; maintained to Nov 2026 |

Both targets are deliberately **not** the newest release available. That is the single
most important output of the analysis below, and it lands the same way twice, for two
products, for the same reason.

Rocket.Chat's own signed supported-versions manifest — served by the running workspace
at `http://127.0.0.1:6666/api/info`, decoded from the `supportedVersions.signed` JWT:

| Version | Expires | LTS |
|---|---|---|
| 8.4.x (current) | 2026-10-31 | no |
| **8.5.x** | **2027-06-30** | **yes** |
| 8.6.x | 2027-01-31 | no |
| 8.7.0 (newest stable) | 2027-02-28 | no |

Expiry is enforced, not advisory: past it Rocket.Chat's cloud cuts desktop and mobile
app access to the workspace. 8.5.2 buys about ten months; 8.7.0, the newest release,
buys about six.

Portainer's published lifecycle policy (docs.portainer.io/start/lifecycle):

| Release | Released | End of maintenance |
|---|---|---|
| **2.39 LTS** | Feb 2026 | **Nov 2026** |
| 2.44 STS | Jul 2026 | **Aug 2026** — i.e. now |
| 2.45 LTS | Aug 2026 | May 2027 (not yet patched) |

Take 8.7.0 instead only for something in it that is actually wanted — phishing-resistant
MFA, the modern OAuth flow with PKCE and state validation, FIPS images — and accept
re-upgrading by February 2027:

```
ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml -l ops \
  --tags rocketchat_stack \
  -e rocketchat_image=docker.io/rocketchat/rocket.chat:8.7.0 -e rocketchat_version=8.7
```

## 3. Refutation analysis

Each claim below is one a reasonable upgrade plan would make. Each was attacked before
being relied on. Six did not survive.

### A. "Bump the compose file to `rocket.chat:8.7.0` and pull." — **REFUTED**

That tag does not exist. The compose file references the **Docker Official Image**
`rocket.chat`, which lags the vendor's releases badly. On 2026-08-17, `library/rocket.chat`
had 8.5.1 and 8.4.4 as its newest tags and returned HTTP 404 for **8.4.5, 8.5.2, 8.6.0,
8.6.1, 8.7.0 and 8.0.8** — every release from the 2026-07-10 security-hotfix wave onward.
The vendor repository `rocketchat/rocket.chat` on the same registry has all of them.

Consequence beyond this upgrade: while the stack pointed at the official image, the
patch line was simply unreachable. 8.4.5 — which carries two security hotfixes and the
`users.createToken` permission fix — could not have been applied at all. The pin now
names `docker.io/rocketchat/rocket.chat`.

### B. "Newest release = best target." — **REFUTED, twice**

Rocket.Chat 8.7.0 (2026-08-07) expires 2027-02-28. Rocket.Chat 8.5.2 is LTS and expires
2027-06-30. The newest release has the *shorter* support window by four months.

Portainer 2.44.0 (2026-07-30) is STS and its end of maintenance is **Aug 2026** — it
would have been adopted already expired. It is also, concretely, the *less patched* of
the two candidates: 2.39.6 (2026-08-12) ships Go 1.25.12 (CVE-2026-42505, CVE-2026-39822),
go-git 5.19.2 (CVE-2026-71556/71557), oras-go 2.6.2 (CVE-2026-50163), otel 1.44.0
(CVE-2026-41178), golang.org/x/net 0.56.0 (CVE-2026-46600, CVE-2026-56852) and an SSRF
protection mechanism, none of which existed when 2.44.0 was cut two weeks earlier.

Upstream agrees: Docker Hub's `latest` and `lts` tags for `portainer-ce` both resolve to
2.39.6; only the `sts` tag carries 2.44.0.

### C. "`portainer-ce:latest` keeps Portainer current." — **REFUTED**

It did the opposite, and the two halves of the failure hid each other.
`community.docker.docker_container` defaults to `pull: not_present`. Once `latest` had
been resolved to a local image, nothing re-resolved it, so the January image kept being
reused and the container was never recreated. Meanwhile the tag carried no version
information into the repo, so no diff, commit or review ever showed which Portainer was
installed. Result: 2.33.6 from January 2026 — a release whose LTS line went end of
maintenance in May 2026 — presented as an auto-updating install.

The fix is both halves: `portainer_image` is pinned by digest, and the play now reads
`/api/status` back and **fails** if the running version does not match
`portainer_image_version`. Drift becomes a red play, not a discovery seven months later.

### D. "Release notes say `MongoDB: 8.0`, so MongoDB must be downgraded." — **REFUTED**

The `Engine versions` block in every 8.x release note says `MongoDB: 8.0`. Read as a
requirement, it says the running 8.2.4 is wrong. It is a floor, not a target: the
support-prerequisites documentation states Rocket.Chat 8.x supports "**8.0 and above
(verified with 8.2)**".

Acting on the misreading would have been destructive rather than merely wasteful. The
live data files are at `featureCompatibilityVersion: 8.2`; MongoDB 8.0 refuses to open
them. "Downgrading to comply" means a dump-and-restore, taken during a window opened for
an unrelated upgrade, for no benefit. The pin moves 8.2.4 → 8.2.12 instead: newest patch,
same line, no FCV change, nothing one-way.

The same reasoning forbids the opposite move. `mongo:8.3` exists and other stacks on this
host use it; adopting it here would push FCV to 8.3, outside what Rocket.Chat verifies,
and could not be undone without a restore.

### E. "The role can already perform this upgrade safely." — **REFUTED**

Three defects, all fixed in the same change as this document:

1. **No backup.** The task ran `docker_compose_v2` with `pull: always` and no dump. The
   only backup on the host was a hand-made archive from the 8.0.1 upgrade, referenced in
   a comment. Rocket.Chat migrations rewrite documents in place, so the dump is the only
   rollback that exists — see section 6.
2. **A permanently failing task.** `Render .env file (if needed)` rendered
   `templates/rocketchat/.env.j2`, which has never existed in this repo, wrapped in
   `ignore_errors: true`. Every run reported a failure everyone had learned to ignore —
   which is also how a *real* failure in that stack would have been received. The compose
   file has no `env_file` and no `${...}` interpolation; nothing read it. Task deleted.
3. **No tag.** `portainer.yaml` had `portainer_stack`; the RocketChat import had none, so
   touching RocketChat meant running the entire ops role — Caddy, LiteLLM, OmniRoute and
   the rest — against a live box. It now has `rocketchat_stack`.

### F. "Rolling back means re-pinning the old version." — **REFUTED, for both**

Neither product rolls back by changing an image reference.

Portainer bumps its bolt database schema on upgrade and "you're unable to use newer
databases on older versions" (docs.portainer.io FAQ). Rocket.Chat's migrations rewrite
the database in place and a 8.4.3 server will not read an 8.5-migrated schema. In both
cases rollback is *restore a backup*, and the backup has to exist beforehand. Section 6
is written accordingly.

### G. "The host might not have room or memory for this." — **survives, negatively**

Checked and dismissed: 93 GB free on the docker disk, 10.4 GB RAM available, a 60 MB
database. Worth stating because this host has been OOM-killed twice by image builds
(see the `omniroute_image` comment in `defaults/main.yaml`), so the question is not
idle here — but nothing in this upgrade builds an image. All three pins are pulled, and
all three resolve to `linux/amd64` in their OCI index, which is what vm700 needs.

### H. "Caddy will keep routing to Portainer after the jump." — **survives**

Attacked because Portainer's own upgrade instructions have not exposed `9000` since 2.9
and steer users to HTTPS on 9443, while `caddy_portainer_port` is 9000. Checked: HTTP on
9000 is not in Portainer's deprecated-and-removed table, the listener is still there, and
"force HTTPS only" is an opt-in admin setting that is not being enabled. Publishing 9000
is still supported and documented as an explicit `-p 9000:9000` addition. No change.

### I. "Rocket.Chat 8.4.3 → 8.5.2 may need intermediate hops." — **survives**

Checked because Rocket.Chat does forbid skipping. The rule is about **major** versions:
"Minor and patch updates within the same major version (for example, 8.0.x to 8.6.x) can
be applied directly." 8.4.3 → 8.5.2 is a single hop inside major 8.

### Where the LTS choice costs something — stated, not hidden

Rocket.Chat backports security hotfixes across every maintained line on the same day —
2026-07-10 shipped 7.10.14, 8.0.8, 8.1.7, 8.2.7, 8.3.7, 8.4.5, 8.5.2 and 8.6.1 together.
But 8.7.0 (2026-08-06) closed a SAML SSO authentication bypass and a forged-ephemeral-
message impersonation issue, and the matching 8.5.3 has not shipped yet. Until it does,
8.5.2 is behind 8.7.0 on those two specific items. This workspace does not use SAML, and
8.5.3 is the thing to watch for; if it has shipped by the time this is executed, pin it
instead of 8.5.2.

## 4. Procedure

Two independent slices. Portainer first — it is the smaller blast radius and the one
already running unmaintained code — and each is verified before the next starts.

### Slice 1 — Portainer 2.33.6 → 2.39.6

```bash
ssh vm700 'docker run --rm -v portainer_data:/from -v /var/lib/docker-data/rocketchat-backups:/to alpine tar czf /to/portainer_data-pre-2.39.6.tar.gz -C /from .'
```

Take that first. Portainer writes its own `backups/portainer.db.bak` during the upgrade,
but that is a database-only copy; the tarball covers the whole volume.

```bash
ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml \
  -l ops --tags portainer_stack
```

The play pins the digest, recreates the container, then polls `/api/status` and asserts
`Version == 2.39.6`. A mismatch fails the play.

### Slice 2 — RocketChat 8.4.3 → 8.5.2, MongoDB 8.2.4 → 8.2.12

```bash
ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml \
  -l ops --tags rocketchat_stack
```

In order, the play: dumps `rocketchat` to
`/var/lib/docker-data/rocketchat-backups/rocketchat-<timestamp>.archive.gz`; renders the
compose file from the pins; brings the stack up; waits up to 300 s for Rocket.Chat to run
its migrations and answer `/api/info`; asserts the reported minor line is 8.5.

MongoDB and Rocket.Chat move in the same apply. Both containers are recreated; the
replica set `rs0` and its data directory are untouched, and `rc-mongo-init` is a no-op
against an already-initialised set.

Expect a few minutes of chat downtime while migrations run. `chat.koeff.com` will 502
through Caddy until the health gate passes.

## 5. Verification

```bash
ssh vm700 'curl -s localhost:9000/api/status'                 # {"Version":"2.39.6",...}
ssh vm700 'curl -s localhost:6666/api/info | head -c 40'      # {"version":"8.5",...
ssh vm700 'docker ps --format "{{.Names}}\t{{.Image}}" | grep -E "rocketchat|rc-mongo|portainer"'
ssh vm700 'docker exec rc-mongo mongosh --quiet --eval "rs.status().ok"'   # 1
```

Then, through the edge rather than the loopback, because that is the path users take:
`https://chat.koeff.com` loads and a message sends; `https://portainer.local.koeff.com`
logs in. The play's asserts cover the containers; only these two cover Caddy.

## 6. Rollback

Neither product rolls back by re-pinning. Both need the artifact taken in section 4.

**Portainer** — stop and remove the container, restore the volume from
`portainer_data-pre-2.39.6.tar.gz`, revert `portainer_image` and
`portainer_image_version` in `defaults/main.yaml`, re-run `--tags portainer_stack`.
Without the tarball, the in-volume `backups/portainer.db.bak` written during the upgrade
is the fallback: rename `portainer.db` aside, copy the `.bak` over it, start the old
image.

**RocketChat** — revert `rocketchat_image` and `rocketchat_version`, re-run
`--tags rocketchat_stack` to put 8.4.3 back, then restore the dump into the downgraded
server:

```bash
ssh vm700 'docker exec -i rc-mongo mongorestore --archive --gzip --drop \
  < /var/lib/docker-data/rocketchat-backups/rocketchat-<timestamp>.archive.gz'
```

Order matters: restoring the pre-upgrade dump into an 8.5 server, or starting 8.4.3
against a migrated database, both leave a workspace that does not start. Downgrade the
image first, restore second.

## 7. What changed in this repo

| File | Change |
|---|---|
| `defaults/main.yaml` | Portainer and RocketChat pin blocks added, with the lifecycle reasoning |
| `tasks/portainer.yaml` | digest pin, explicit pull policy, digest-pinning assert, version read-back |
| `tasks/rocketchat.yaml` | pre-upgrade dump, template instead of copy, dead `.env` task removed, health gate and version assert |
| `templates/rocketchat/docker-compose.yml.j2` | new; replaces the static `files/rocketchat/docker-compose.yml` |
| `files/rocketchat/docker-compose.yml` | deleted (superseded) |
| `tasks/main.yaml` | `rocketchat_stack` tag on the RocketChat import |

Covered by eval `018-main-core--rocketchat-portainer-version-pinning.json`.

## 8. Deliberately not done

- **Memory ceilings on the RocketChat stack.** `caddy` and `omniroute` both carry
  `mem_limit`/`memswap_limit` because this host has been swapped to death twice; the
  RocketChat services carry none. Adding one is right, but guessing the number during a
  version upgrade risks an OOM-kill loop on a service that was previously fine. Size it
  against observed RSS on 8.5.2, separately.
- **Portainer 2.45 LTS.** Released Aug 2026, maintained to May 2027, no patch releases
  yet. Move after 2.45.1 or 2.45.2.
- **Rocket.Chat 8.5.3.** Not released as of 2026-08-17. It is where the 8.7.0 SAML and
  ephemeral-message fixes land for this line; pin it when it appears.
- **Applying any of this to vm700.** The repo is correct and the plan is executable;
  nothing has been run against the live host beyond read-only queries.
