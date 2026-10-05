# Retired Living Table platform App

The Living Table was removed from Agent Platform Apps on 2026-10-05 at Kyle's
request. The separate `claude-ttrpg` repository was not changed. The platform
no longer deploys `ap-app-ttrpg`, lists it as an App, or offers its `ttrpg`
custom tool. `ttrpg-gm` is disabled rather than deleted, preserving its
configuration and history. Historical `#ttrpg-table` Relay messages remain.

The world data was **not deleted**. The `ap-ttrpg-world` PVC is retained by
`legacyTtrpgWorld.keep` in the pai Helm values and has a Helm keep annotation.
No running workload mounts it. Do not remove this PVC while it is the only
on-cluster copy of the world.

An independent, age-encrypted snapshot was saved on Kyle's laptop at
`/Users/kp/Backups/agent-platform/ap-ttrpg-world-retired-20261005.tar.gz.age`.
It is 45 MiB and was verified by decrypting and listing its 225 tar entries.
SHA-256: `430fbb5230bf4d83cc8fc7a4a9698eb4d60c1700c5c513ce0382b4f108ea9f96`.
It uses the existing Agent Platform recovery identity in
`/Users/kp/Backups/agent-platform/recovery-key/identity.txt`. The backup and
private identity stay outside Git. To restore into an empty directory:

```sh
mkdir -m 700 /private/tmp/ttrpg-world-restore
age -d -i /Users/kp/Backups/agent-platform/recovery-key/identity.txt \
  /Users/kp/Backups/agent-platform/ap-ttrpg-world-retired-20261005.tar.gz.age \
  | tar -xzf - -C /private/tmp/ttrpg-world-restore
```

The archive contains the world as it was at retirement. Any future work to
host the engine should start from the `claude-ttrpg` repository and explicitly
decide whether to restore this snapshot or reuse the retained PVC.
