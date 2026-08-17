# RocketChat and Portainer upgrade — vm700

Written 2026-08-17. Every version number and date below was read from the live host,
from upstream release metadata, from vendor lifecycle documentation, or from upstream
source at the exact release tag; sources are named inline so the whole thing can be
re-derived rather than trusted.

The targets are the **newest** release of each component. That is a deliberate choice
over the LTS lines, taken because each one has a verified way back — section 6 is the
part of this document that does the work.

## 1. Measured starting state

Read off vm700 (192.168.31.152) on 2026-08-17.

| Component | Declared in repo | Actually running | Gap |
|---|---|---|---|
| RocketChat | `rocket.chat:8.4.3` | 8.4.3 (`/api/info`) | in step |
| RocketChat MongoDB | `mongo:8.2.4` | 8.2.4, FCV 8.2 | in step, 8 patches behind |
| Portainer | `portainer/portainer-ce:latest` | **2.33.6** (`/api/status`) | **7 months, unmaintained** |

Portainer's container was created **2026-01-28** and had not been replaced since.
`latest` on Docker Hub had moved to 2.39.6 by 2026-08-13. The tag said "rolling" and
the install was frozen — section 3, claim C.

Capacity: `/var/lib/docker` 93 GB free of 196 GB, `/` 64 GB free, 10.4 GB RAM
available of 16 GB, `rocketchat` database 60 MB data / 22 MB on disk. Nothing here is
resource-constrained.

## 2. Targets — newest of each

| Component | From | To | Newest? | Retreat |
|---|---|---|---|---|
| RocketChat | 8.4.3 | **8.7.0** | yes (2026-08-06) | 8.5.2 LTS — pin change only |
| MongoDB | 8.2.4 | **8.3.8** | yes (2026-08-12) | 8.2.12 — supported downgrade |
| Portainer | 2.33.6 | **2.44.0** | yes (2026-07-30) | 2.39.6 LTS — restore + run |

2.45 LTS is planned for Aug 2026 and has not shipped: the GitHub releases API tops out
at 2.44.0, and Docker Hub returns 404 for `portainer-ce:2.45.0`.

### What the newest costs, stated plainly

**RocketChat 8.7.0** is supported to **2027-02-28**; 8.5.2 LTS is supported to
**2027-06-30**. Taking the newest buys ~6 months instead of ~10 and means moving again
by February. Expiry is enforced — past it, Rocket.Chat's cloud gates desktop and
mobile app access to the workspace. Both dates come from the vendor's signed
supported-versions manifest, read live off this workspace:

| Version | Expires | LTS |
|---|---|---|
| 8.4.x (current) | 2026-10-31 | no |
| 8.5.x | 2027-06-30 | **yes** |
| 8.6.x | 2027-01-31 | no |
| **8.7.0** | 2027-02-28 | no |

What 8.7.0 gives for that: phishing-resistant MFA, the server-side OAuth flow with
CSRF protection, state validation and PKCE, FIPS 140-3 images, and the SAML SSO
authentication-bypass and forged-ephemeral-message fixes that have **not** yet been
backported to an 8.5.3. On security specifically, the newest is currently ahead.

**Portainer 2.44.0** is the awkward one, and this is the one place where "newest"
buys nothing:

- Its STS maintenance window ended **Aug 2026**. There will be no 2.44.1.
- It is the **less patched** of the two candidates. 2.39.6 shipped 2026-08-12,
  thirteen days *after* 2.44.0, and carries Go 1.25.12 (CVE-2026-42505,
  CVE-2026-39822), go-git 5.19.2 (CVE-2026-71556/71557), oras-go 2.6.2
  (CVE-2026-50163), otel 1.44.0 (CVE-2026-41178), x/net 0.56.0 (CVE-2026-46600,
  CVE-2026-56852) and an SSRF allow-list. None of that exists in 2.44.0.
- Upstream's own `latest` and `lts` tags both resolve to 2.39.6; only `sts` carries
  2.44.0.

2.44.0 does bring its own security work — leftover-service-account access and a swarm
compose path traversal, both also in 2.39.6 — plus workflow detail screens, GPU
visibility and a long bug-fix list. If those are not wanted, 2.39.6 is strictly the
better pin here and the retreat in section 6 is the whole procedure.

**MongoDB 8.3.8** is inside Rocket.Chat's supported range ("8.0 and above") but
outside what it verifies ("verified with 8.2"). It is also a rapid minor release;
MongoDB notes that from 8.2 onward minor releases are published for on-premises use
"for specific use cases" rather than as the general recommendation.

## 3. Refutation analysis

Each claim below is one a reasonable upgrade plan would make. Each was attacked before
being relied on.

### A. "Bump the compose file to `rocket.chat:8.7.0` and pull." — **REFUTED**

That tag does not exist. The compose file referenced the **Docker Official Image**
`rocket.chat`, which lags the vendor badly. On 2026-08-17 `library/rocket.chat` had
8.5.1 and 8.4.4 as its newest tags and returned HTTP 404 for **8.4.5, 8.5.2, 8.6.0,
8.6.1, 8.7.0 and 8.0.8** — every release from the 2026-07-10 security-hotfix wave
onward. The vendor repository `rocketchat/rocket.chat` on the same registry has all of
them.

While the stack pointed at the official image, the patch line was unreachable: 8.4.5,
carrying two security hotfixes and the `users.createToken` permission fix, could not
have been applied at all. The pin now names `docker.io/rocketchat/rocket.chat`.

### B. "Newest release = most patched." — **REFUTED for Portainer**

2.44.0 predates 2.39.6 by thirteen days and is missing six CVE remediations, listed
above. Newest by version number is not newest by patch level when a project maintains
two streams in parallel. It survives for Rocket.Chat, where 8.7.0 genuinely is ahead
of 8.5.2 on security.

### C. "`portainer-ce:latest` keeps Portainer current." — **REFUTED**

It did the opposite, and the two halves of the failure hid each other.
`community.docker.docker_container` defaults to `pull: missing`, so once `latest`
had been resolved to a local image nothing re-resolved it and the container was never
recreated. Meanwhile the tag carried no version into the repo, so no diff, commit or
review ever showed which Portainer was installed. Result: 2.33.6 from January 2026 —
whose LTS line went end of maintenance in May 2026 — presented as auto-updating.

Fixed in both halves: `portainer_image` is pinned by digest, and the play reads
`/api/status` back and **fails** when the running version does not match
`portainer_image_version`.

### D. "Release notes say `MongoDB: 8.0`, so MongoDB must be downgraded." — **REFUTED**

The `Engine versions` block in every 8.x release note says `MongoDB: 8.0`. Read as a
requirement it says the running 8.2.4 is wrong. It is a floor: the support-prerequisites
documentation states 8.x supports "**8.0 and above (verified with 8.2)**".

Acting on the misreading would have been destructive rather than merely wasteful. The
live data files are at `featureCompatibilityVersion: 8.2` and MongoDB 8.0 refuses to
open them, so "downgrading to comply" means a dump-and-restore during a window opened
for an unrelated upgrade, for no benefit.

### E. "The role can already perform this upgrade safely." — **REFUTED**

Three defects, all fixed alongside this document:

1. **No backup.** `docker_compose_v2` with `pull: always` and no dump. The only backup
   on the host was a hand-made archive from the 8.0.1 upgrade, mentioned in a comment.
2. **A permanently failing task.** `Render .env file (if needed)` rendered
   `templates/rocketchat/.env.j2`, which git history shows was never added to this
   repo, under `ignore_errors: true`. Every run reported a failure people had learned
   to scroll past — which is how a real failure would have been received too. The
   compose file has no `env_file` and no `${...}` interpolation; nothing read it.
3. **No tag.** `portainer.yaml` had `portainer_stack`; the RocketChat import had none,
   so touching RocketChat meant running the entire ops role against a live box. It now
   has `rocketchat_stack`.

### F. "Rolling back means re-pinning the old version." — **PARTLY REFUTED**

True for Portainer, false for Rocket.Chat, and the difference is only visible in the
source. Section 6 replaces this claim entirely.

### G. "The host might not have room or memory." — **survives, negatively**

93 GB free on the docker disk, 10.4 GB RAM available, a 60 MB database. Worth checking
because this host has been OOM-killed twice by image builds (see `omniroute_image` in
`defaults/main.yaml`), but nothing here builds an image. All three pins resolve to
`linux/amd64` in their OCI index.

### H. "Caddy will keep routing to Portainer." — **survives**

Attacked because Portainer's upgrade instructions have not exposed 9000 since 2.9,
while `caddy_portainer_port` is 9000. HTTP on 9000 is not in Portainer's
deprecated-and-removed table, the listener is still there, and force-HTTPS-only is an
opt-in admin setting that is not being enabled.

### I. "8.4.3 → 8.7.0 may need intermediate hops." — **survives**

The no-skipping rule is about **major** versions: "Minor and patch updates within the
same major version (for example, 8.0.x to 8.6.x) can be applied directly." Confirmed
independently in section 6: the two releases share an identical migration set.

## 4. Procedure

Two independent slices. Portainer first — smaller blast radius, and the one already
running unmaintained code — each verified before the next starts.

### Slice 1 — Portainer 2.33.6 → 2.44.0

```bash
ssh vm700 'docker run --rm -v portainer_data:/from -v /var/lib/docker-data/rocketchat-backups:/to alpine tar czf /to/portainer_data-pre-2.44.0.tar.gz -C /from .'
```

Take that first. Portainer also writes `backups/portainer.db.bak` into the volume
before it migrates, but that is the database only; the tarball covers the volume.

```bash
ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml \
  -l ops --tags portainer_stack
```

Pins the digest, recreates the container, polls `/api/status`, asserts `2.44.0`.

### Slice 2 — RocketChat 8.4.3 → 8.7.0, MongoDB 8.2.4 → 8.3.8

```bash
ansible-playbook -i protected/inventories/inventory-main.yaml playbook-index.yaml \
  -l ops --tags rocketchat_stack
```

In order: dumps `rocketchat` to
`/var/lib/docker-data/rocketchat-backups/rocketchat-<timestamp>.archive.gz`; renders
the compose file from the pins; brings the stack up; waits up to 300 s for
Rocket.Chat to answer `/api/info`; asserts the minor line is 8.7; asserts MongoDB's
FCV is still 8.2, which is what keeps the retreat in section 6 available.

MongoDB and Rocket.Chat move in the same apply. Both containers are recreated; the
`rs0` replica set and its data directory are untouched, and `rc-mongo-init` is a no-op
against an initialised set. Expect a few minutes of chat downtime — `chat.koeff.com`
502s through Caddy until the health gate passes.

## 5. Verification

```bash
ssh vm700 'curl -s localhost:9000/api/status'                 # {"Version":"2.44.0",...}
ssh vm700 'curl -s localhost:6666/api/info | head -c 40'      # {"version":"8.7",...
ssh vm700 'docker exec rc-mongo mongosh --quiet --eval "db.version()"'                 # 8.3.8
ssh vm700 'docker exec rc-mongo mongosh --quiet --eval "rs.status().ok"'               # 1
ssh vm700 'docker exec rc-mongo mongosh --quiet rocketchat --eval "db.migrations.findOne({_id:\"control\"}).version"'   # 335
```

Then through the edge, because that is the path users take: `https://chat.koeff.com`
loads and a message sends; `https://portainer.local.koeff.com` logs in. The play's
asserts cover the containers; only these two cover Caddy.

### Result — executed 2026-08-17

Both slices applied to vm700. `--tags portainer_stack` finished `ok=7 changed=1`,
`--tags rocketchat_stack` finished `ok=19 changed=5`, neither with a failure.

| Check | Measured |
|---|---|
| Portainer `/api/status` | `2.44.0` |
| RocketChat `/api/info` | `8.7` |
| MongoDB `db.version()` | `8.3.8` |
| `rs.status().ok` | `1` |
| MongoDB FCV | `8.2` — retreat still open |
| `migrations.control.version` | `335` — unchanged, zero migrations ran |
| `chat.koeff.com` via Caddy | HTTP 200, `ssl_verify_result=0`, `/api/info` reports 8.7 |
| `portainer.local.koeff.com` via Caddy | HTTP 200, `ssl_verify_result=0`, `/api/status` reports 2.44.0 |
| Dumps on disk | `portainer_data-pre-2.44.0.tar.gz` (145 KB), `rocketchat-<ts>.archive.gz` (8.7 MB) |

The control document still reading 335 after the upgrade is the section 6 claim
confirmed against the live host rather than only against upstream source: the
8.4.3 → 8.7.0 move ran no migrations, so the retreat to 8.5.2 remains a pin change.

The synthetic WebSocket check (`curl` with `Upgrade: websocket`) returns 400, which
is what it returned against the previous stack too — see eval 017, where the same
check is recorded as not a valid test. A real handshake was not re-run.

### Idempotence

`portainer_stack` is `changed=0` on a second consecutive run.

`rocketchat_stack` is **not**, and each of the three is accounted for:

1. `Ensure target data directories exist with correct ownership` uses `recurse: true`
   over the uploads tree — pre-existing behaviour, inherited unchanged.
2. The pre-upgrade dump runs every play by design. It is bounded by
   `rocketchat_backup_keep` (10).
3. `Run RocketChat Docker Compose stack` reports `changed` because its only action
   is `{"id": "rc-mongo-init", "status": "Starting"}` — `mongo-init-replica` is a
   one-shot service with `restart: "no"`, so `compose up` restarts the exited
   container each run. Its script is idempotent.

None of the three recreates `rocketchat` or `rc-mongo`: their `Created` timestamps
were identical across four consecutive plays.

One real defect was found this way and fixed: the compose task had `pull: always`
against exact pins, which re-fetched both manifests every run and reported `changed`
without recreating anything. It is now `pull: missing`.

## 6. Retreat to LTS — checked in source, per component

The question this section answers is not "is downgrade supported" (upstream says no,
for all three) but "what actually happens if we run the older binary against the newer
data". That has three different answers here, and the plan takes the newest release of
each **because** all three are recoverable.

### RocketChat 8.7.0 → 8.5.2 LTS — a pin change

Rocket.Chat gates startup on the `migrations` collection's `control` document.
`migrateDatabase()` in `apps/meteor/server/lib/migrations.ts` looks up the DB's version
in the migration list the running binary carries, and when that lookup fails it throws
`Can't find migration version N` **outside** the try block — the server exits and the
container crash-loops. That is the real downgrade blocker, and it is absent here:

| Release | Migration files | Highest version |
|---|---|---|
| 8.4.3 | 42 | v335 |
| 8.4.5 | 42 | v335 |
| 8.5.2 | 42 | v335 |
| 8.6.1 | 42 | v335 |
| 8.7.0 | 42 | v335 |

Set difference between 8.7.0 and 8.5.2: **empty**. The live `control` document already
reads `version: 335`. So the 8.4.3 → 8.7.0 upgrade runs **zero** migrations, and an
8.5.2 binary started afterwards hits `currentVersion === version` →
`Already at target migration version`, not the throw.

Retreat: set `rocketchat_image` to `docker.io/rocketchat/rocket.chat:8.5.2` and
`rocketchat_version` to `"8.5"`, re-run `--tags rocketchat_stack`. Verify the control
document still reads 335 and `/api/info` reports 8.5.

Caveat worth keeping: migrations are not the only thing a newer server writes. Settings
registered only by 8.7.0 will sit unused in the `settings` collection, and anything an
8.7-only feature persisted (a modern-OAuth state document, a FIPS marker in workspace
statistics) is not rolled back. Those are additive; none of them gate startup. The dump
from section 4 remains the backstop, and it is the only thing that also reverses
*content* written while 8.7.0 was live.

### MongoDB 8.3.8 → 8.2.12 — supported, conditional on FCV

MongoDB permits single-version downgrades between adjacent releases — 8.3 → 8.2 is
explicitly the documented path — provided the featureCompatibilityVersion is already at
the target. Ours is **8.2**, verified on the live host:

```
$ docker exec rc-mongo mongosh --quiet --eval \
    'db.adminCommand({getParameter:1,featureCompatibilityVersion:1}).featureCompatibilityVersion.version'
8.2
```

mongod never raises FCV by itself, and neither Rocket.Chat nor this role calls
`setFeatureCompatibilityVersion`. Running the 8.3.8 binary therefore leaves FCV at 8.2
and the downgrade door open. `tasks/rocketchat.yaml` asserts this after every run, so
the door cannot be closed silently.

Retreat: set `rocketchat_mongo_image` back to `mongo:8.2.12`, re-run
`--tags rocketchat_stack`. No dump/restore, no FCV step.

**Do not run `setFeatureCompatibilityVersion` to 8.3 by hand.** That is what converts
this into a dump-and-restore: the 8.3-only features it enables — 2dsphere index
version 4, views using 8.3 expressions, collections marked validated under 8.3
semantics — must each be removed before MongoDB will downgrade the FCV again, and a
downgrade attempted with validated collections present simply fails.

### Portainer 2.44.0 → 2.39.6 LTS — restore, then run

The weakest of the three, and the reason 2.44.0 is the one target where the LTS line
would have been the better pick anyway.

There is **no "database is newer" guard** in 2.39.6. Read from
`api/datastore/migrate_data.go` and `api/datastore/migrator/migrate_ce.go` at that tag:
`NeedsMigration()` returns true (the 2.44.0 database carries a non-zero migrator count
that 2.39.6 does not recognise), `MigrateData()` writes a backup **first**, then
`Migrate()` walks its migration list, finds nothing whose version is greater than the
2.44.0 stamp, runs none, and re-stamps `SchemaVersion` back to `2.39.6`.

So 2.39.6 starts. What it does not do is undo `migrate_2_43_0`,
`migrate_2_43_sources` or `migrate_2_44_0` — those files exist only in 2.44.0, and
their data changes stay in the database under a stamp that now claims 2.39.6. Going
back up to 2.44.0 later would re-run them against already-migrated data.

Retreat, the clean way — restore rather than rely on that:

1. `docker stop portainer && docker rm portainer`
2. Restore `portainer_data-pre-2.44.0.tar.gz` over the volume. Without it, rename
   `portainer.db` aside inside the volume and copy `backups/portainer.db.bak` over it —
   Portainer wrote that automatically before it migrated.
3. Set `portainer_image` back to
   `sha256:3fa8750ac2b98ce56784ca292df1adc3ec38f0062fd572811ea4b2221beee310` and
   `portainer_image_version` to `2.39.6`.
4. `--tags portainer_stack`. 2.39.6 migrates the restored 2.33.6 database forward,
   which is a path it does support.

## 7. What changed in this repo

| File | Change |
|---|---|
| `defaults/main.yaml` | Portainer and RocketChat pin blocks, with lifecycle and retreat reasoning |
| `tasks/portainer.yaml` | digest pin, explicit pull policy, digest assert, version read-back |
| `tasks/rocketchat.yaml` | pre-upgrade dump, template instead of copy, dead `.env` task removed, health gate, version assert, FCV assert |
| `templates/rocketchat/docker-compose.yml.j2` | new; replaces the static `files/rocketchat/docker-compose.yml` |
| `files/rocketchat/docker-compose.yml` | deleted (superseded) |
| `tasks/main.yaml` | `rocketchat_stack` tag on the RocketChat import |

Covered by eval `018-main-core--rocketchat-portainer-version-pinning.json`.

## 8. Deliberately not done

- **Memory ceilings on the RocketChat stack.** `caddy` and `omniroute` carry
  `mem_limit`/`memswap_limit` because this host has been swapped to death twice; the
  RocketChat services carry none. Adding one is right, but guessing the number during a
  version upgrade risks an OOM-kill loop on a service that was previously fine. Size it
  against observed RSS on 8.7.0, separately — and note 8.7.0 adds Unified AI Search and
  a Node.js apps runtime option, both opt-in and both off here.
- **Portainer 2.45 LTS.** Not released. When it appears it supersedes both candidates.
- **Rocket.Chat 8.5.3.** Not released. Only relevant if the retreat is taken.
- **The `recurse: true` on the uploads directory.** It chowns and chmods the whole
  uploads tree on every run, which is both the first line of the idempotence list
  above and O(files) of pointless work. It predates this change and altering
  permission semantics on a live uploads tree is not something to fold into a
  version bump.
- **A real WebSocket handshake through Caddy on 8.7.0.** The synthetic curl check is
  not a valid test and was not replaced with one.

## 9. Operational notes from the run

- **The first `--tags portainer_stack` run failed twice before succeeding**, and
  neither failure was the upgrade. First, `pull: not_present` is `docker_image_pull`
  vocabulary — `docker_container` takes `missing`. Second, the pull itself died with
  `commit failed: rename .../ingest/... no such file or directory` from containerd's
  content store; a manual `docker pull` of the same digest succeeded immediately
  afterwards, and no build or CI container was running at the time. Treat that class
  of failure as transient and retry before investigating.
- **Pre-pull before the RocketChat slice.** Both images were pulled by hand with a
  retry loop before running the play, so a repeat of that containerd failure could
  not land in the middle of the chat downtime window.
- **Downtime** was two health-gate retries, roughly 20 seconds, plus container
  restart.
