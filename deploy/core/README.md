# Core Node Deployment

5G Core node (`10.91.2.4`): Open5GS, deployed unmodified via
`docker_open5gs`, pinned as the `vendor/open5gs` submodule -- see
`UPSTREAM_COMMIT.txt` for the exact commit. No source patches; only the
YAML configs in `deploy/core/config/` are locally authored.

## Setup

```bash
git submodule update --init vendor/open5gs   # if not already done via --recursive clone
cp deploy/core/config/*.yaml vendor/open5gs/config/   # or ~/core/config/ on the live node
```

Bring the stack up per `docker_open5gs`'s own instructions (its
`docker-compose.yml`, not this repo's `observability/docker-compose.yml`,
which is a separate monitoring stack).

## Slice management

`change_subscriber_slice.sh` migrates a subscriber between S-NSSAI slices
(used to move an attacker UE onto a throttled slice as an alternative to
PRB-ratio mitigation):

```bash
./deploy/core/change_subscriber_slice.sh <IMSI> <SST>
# Example: ./deploy/core/change_subscriber_slice.sh 001013310000103 2   (throttle)
#          ./deploy/core/change_subscriber_slice.sh 001013310000103 1   (restore)
```

## Mitigation fallback: SSH AMF subscriber barring

Used for signaling-storm attacks, where the primary E2SM-RC PRB throttle
(see `deploy/ric/README.md`) is a control-plane no-op:

```bash
# Block UE
ssh telmat@10.91.2.4 "sudo open5gs-dbctl subscriber_status <IMSI> 1 1"
# Restore
ssh telmat@10.91.2.4 "sudo open5gs-dbctl subscriber_status <IMSI> 0 0"
```

## Config patching

`patch_core.sh` overwrites the SMF/NSSF configs on the Core node with a
clean copy (adds SST=2 slice support) and restarts the affected Open5GS
services. Requires passwordless sudo (NOPASSWD) configured for the SSH user
on the Core node -- see the script's inline comments.
