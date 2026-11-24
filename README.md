# WearStream

A self-hosted container that keeps a complete local copy of your raw Whoop and Garmin Connect
data.

## What WearStream is

WearStream runs as one long-lived container. On a schedule it asks Whoop and Garmin Connect for your
data and writes **exactly what they returned** into a capture tree under `/data`:

```
/data/<source>/<collection>/dt=<YYYY-MM-DD>/<collection>_<ms>_<id>.<ext>        the payload
/data/<source>/<collection>/dt=<YYYY-MM-DD>/<collection>_<ms>_<id>.meta.json    its sidecar
```

The tree is append-only and is meant to be the raw ("bronze") layer of your own data lake: any tool
that reads files can build on it. Vendors restate recent days, so WearStream re-captures a trailing
window every run (8 days for Whoop, hourly; 14 days for Garmin, daily) and keeps only what changed.
It fills older history in the background, watches the tree for problems with its checks, and
reports everything through its log.

What it doesn't do: there is no web UI, no dashboard and no parsing. WearStream never interprets
the data; turning it into tables is the job of whatever reads `/data`. It talks only to Whoop and
Garmin, and serves nothing.

## Before you start

- **A Whoop developer application.** Register one in the Whoop developer dashboard with the scopes
  `read:sleep read:workout read:recovery read:cycles read:profile read:body_measurement offline`
  and a redirect URI (WearStream expects `http://localhost:8000/callback` unless you set
  `WEARSTREAM_WHOOP_REDIRECT_URI`; nothing needs to listen there). Put its client id and client
  secret in `/secrets/whoop/client_id` and `/secrets/whoop/client_secret`, one value per file.
- **Your Garmin Connect email and password.** You type them into `wearstream auth garmin`. If your
  account has no MFA and you want WearStream to sign in again on its own when a session is lost,
  you may also put them in `/secrets/garmin/email` and `/secrets/garmin/password`.
- **Three host folders**, mounted into the container:
  - `/data`, the capture tree, on a filesystem that supports **hard links** (ext4, XFS, Btrfs, ZFS;
    not most network or FUSE filesystems): captures are written atomically through them.
  - `/state`, WearStream's own state, on a **local** filesystem (it relies on file locks).
  - `/secrets`, the credentials and the tokens WearStream maintains. It must be writable, because
    Whoop rotates its refresh token on every refresh.

  The container runs as uid and gid `65532` (`nonroot`) and writes nothing outside these folders,
  so give them to that uid:

  ```sh
  sudo mkdir -p /srv/wearstream/{data,state,secrets/whoop,secrets/garmin}
  sudo chown -R 65532:65532 /srv/wearstream/{data,state,secrets}
  sudo chmod 700 /srv/wearstream/secrets /srv/wearstream/secrets/*
  ```

- **Permissions of what WearStream writes** follow `WEARSTREAM_UMASK` (default `022`, so other users
  on the host can read captures; `077` keeps them readable only by uid `65532`).

## Run it

**With Compose:** copy [`docs/deploy/compose.yaml`](docs/deploy/compose.yaml) and `.env.example`
(as `wearstream.env`) into a folder on the host, adjust the paths and settings, then:

```sh
docker compose up -d
docker compose logs -f wearstream
```

**With Ansible:** use the task in
[`docs/deploy/wearstream-task.yml`](docs/deploy/wearstream-task.yml) (the `community.docker`
collection), templating `.env.example` into the environment file it reads.

Both run a pinned release with a read-only root filesystem, no capabilities, no ports and a
60-second stop timeout, so a credential exchange in progress always finishes before the container
stops. Until you authorize a source, WearStream simply waits for you.

## Authorize

Each source needs a person once, and again only if its credential is lost. Run the commands in the
running container:

```sh
docker exec -it wearstream wearstream auth whoop
docker exec -it wearstream wearstream auth garmin
```

`auth whoop` prints a Whoop authorization link. Open it, approve, and paste back the address your
browser was sent to (that page won't load; that's expected). `auth garmin` asks for your email,
password and, if Garmin asks, the MFA code. Neither command opens a port or prints a token. The
daemon notices the new credential within a minute and resumes on its own.

To authorize before the daemon runs, use a one-off container with the same mounts and hardening:

```sh
docker run --rm -it --read-only --tmpfs /tmp --env-file wearstream.env \
  -v /srv/wearstream/data:/data -v /srv/wearstream/state:/state \
  -v /srv/wearstream/secrets:/secrets \
  ghcr.io/chainringlabs/wearstream:X.Y.Z auth whoop
```

## Check on it

- `docker exec wearstream wearstream status` shows the daemon, anything that needs you (with what
  to do about it), each source and job, backfill progress and failing checks. It exits `0` when
  nothing needs attention and `1` when something does; `--json` prints the same as JSON.
- `docker exec wearstream wearstream config` shows every setting, its value and where it came from,
  which secret files are present, and any problem.
- **The log** is the alert channel: one event per line (set `WEARSTREAM_LOG_FORMAT=json` to feed it
  to Loki or similar). Lines at `ERROR` (`check_failed`, `job_failing`, `source_waiting_for_auth`,
  `capture_paused`, …) need a person and carry an `action` saying what to do; their recovery events
  (`check_recovered`, `job_recovered`, `source_resumed`, …) close them.
- **The health check** (`wearstream healthcheck`, built into the image) turns unhealthy when the
  daemon's heartbeat stops or capture is paused, so `docker ps` and your orchestrator see it.
- On a brand-new install nothing has been captured yet: until a source's first successful run, its
  freshness checks report `suppressed` (reason `first_capture_pending`) instead of failing.

## Backfill

After a day's first successful scheduled run, WearStream walks back through history and fills every
day that has no capture, one day at a time with a pause between days (60 seconds by default) so it
stays well inside the vendors' limits. Set where it stops with `WEARSTREAM_WHOOP_BACKFILL_FROM` and
`WEARSTREAM_GARMIN_BACKFILL_FROM` (`YYYY-MM-DD`); unset, it goes back to the earliest day already in
`/data`, or the date WearStream first ran. Two years of history take about 12 hours for Whoop and
about a day and a half for Garmin. Days before your account existed come back empty, so setting the
start date to your first device saves that time. `wearstream status` shows the progress.

## Adopting an existing capture tree

A tree with the same layout, written by an earlier capture tool, can be mounted as `/data` as it is:
WearStream re-captures only what changed and backfills only days with no capture.

1. **Stop the other tool first**, before WearStream ever refreshes a credential: two processes
   refreshing one Whoop grant revoke it, and two sharing a Garmin session can invalidate it.
2. Don't copy its token files. Authorize afresh with `wearstream auth whoop` and
   `wearstream auth garmin`.
3. Start WearStream. On their first run, its checks record each collection's current payload shape
   as the baseline later runs compare with.

## Upgrading and verifying the image

Images are published as `ghcr.io/chainringlabs/wearstream:X.Y.Z`. To upgrade, change the pinned
version in your Compose file or Ansible task and redeploy; the stop timeout lets running work finish,
and the new version picks up where the old one stopped. A setting an older release used and a newer
one retired is ignored, with a `setting_retired` warning in the log.

Every image is signed and carries an SBOM and build provenance, made by the organization's shared
publishing workflow. Verify both the signer and the source repository:

```sh
gh attestation verify oci://ghcr.io/chainringlabs/wearstream:X.Y.Z \
  --repo chainringlabs/wearstream \
  --signer-workflow chainringlabs/ci/.github/workflows/docker-publish.yml

cosign verify ghcr.io/chainringlabs/wearstream:X.Y.Z \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/chainringlabs/ci/\.github/workflows/docker-publish\.yml@' \
  --certificate-github-workflow-repository chainringlabs/wearstream
```

## Configuration reference

Every setting is an environment variable. `.env.example` lists them all with their defaults; the
full reference, including the secret files and what each command reads, is the operator-interface
design in [`docs/intent/operator/operator-design.md`](docs/intent/operator/operator-design.md). The
overall design is in [`docs/high-level-design.md`](docs/high-level-design.md).

## License

MIT; see [LICENSE](LICENSE).
