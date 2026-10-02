# Backups and recovery

Agent definitions, memories, Relay history, artifacts, and most platform state
live in PostgreSQL. Connector tokens and chat identities also need their
Kubernetes Secret values. A database dump by itself cannot restore Kai's
Discord identity. The cloud backup job packages both into one **age-encrypted**
archive before uploading it to Google Cloud Storage (GCS).

The existing `ap-pg-backup` CronJob still writes a daily local SQL dump to the
`ap-pg-backups` volume. The new `ap-cloud-backup` CronJob is a separate, opt-in
path. Its archive contains `manifest.json`, `database.sql.gz`, and
`secrets.json`. Only the encrypted `.tar.age` file goes to GCS. The backup pod
holds the public age recipient and an upload-only GCS credential. It never has
the private recovery identity.

## Set up the Cloud backups connection

1. Make a dedicated age identity on a trusted machine: `age-keygen -o
   identity.txt`. Keep a private-key copy outside the bucket and outside Git.
   `age-keygen -y identity.txt` prints its **public** recipient.
2. Choose a private GCS bucket. Give a dedicated service account
   `roles/storage.objectCreator` on that bucket only and create a JSON key for
   it. This grants object creation, not listing, reading, or deletion. The
   account and bucket are Google Cloud resources; the platform does not create
   them.
3. In **Settings → Connections → Cloud backups**, enter the bucket, object
   prefix, public recipient, and service-account JSON. Save, then **Verify
   upload**. Verification writes a small marker using the exact credential.
   Secret values are stored in Kubernetes Secret `backup-gcs`; the API returns
   only a credential-set indicator. Agents cannot grant this Secret to their
   run pods.
4. Enable `backup.cloud.enabled=true` in the Helm chart and deploy. The default
   schedule is 08:30 UTC daily. **Settings → Backups** can run the same Job
   immediately and shows its encrypted local exports, cloud upload receipts,
   and downloads. The external MCP facade lists backups, starts Jobs, and
   reads Job status as Tools; encrypted bytes are available as
   `ap://backup/<filename>` Resources. Connection changes stay in its gated
   admin menu. The API checks admin authority for every operation, even when
   a Tool is visible. Private recovery identities must not be sent as MCP
   Tool arguments.

The GCS key is a connector credential, not an age decryption key. The private
age identity is deliberately outside the connection. A copy on pai protects
against losing the laptop key file, but someone who compromises pai can then
decrypt the off-site files. Protect the root-only directory and retain a
second copy somewhere independent of pai if that threat matters.

The bucket's lifecycle controls cloud retention. After a successful upload,
the Job keeps the newest `backup.keep` encrypted local exports (14 by default)
and prunes older ones. `ap-pg-backup` separately keeps its newest 14 local SQL
dumps. Monitor the volume and bucket for failures or retention drift.

## Inspect or download

The UI's **Inspect an import** accepts an encrypted archive and private age
identity file. It checks the decrypted manifest, member names and SHA-256
checksums, then displays Secret **names** but no values. The identity is used
for that request and removed. The same inspection is available offline:

```sh
python3 scripts/backup_restore.py inspect ap-recovery-YYYYMMDDTHHMMSSZ.tar.age \
  --identity /path/to/identity.txt
```

Backups are also downloadable from Settings → Backups or the admin-only API
`GET /api/backups/file/<filename>`. To fetch a cloud object, use a separate
administrator's GCS read access, such as `gcloud storage cp gs://BUCKET/PREFIX/FILE .`.
The backup service account intentionally cannot read its own uploads.

## Restore onto a fresh cluster

Restore is an **offline administrative operation**. The UI validates imports;
it does not replace a live database from inside the running API. A restore
would destroy newer state and kill the API serving the request. Use a fresh
cluster or a deliberate maintenance window, and keep the archive and private
identity on your trusted machine.

A restore brings back everything the archive holds, including
[Judgment](judgment.md) records Kyle deleted after the backup was taken.
Re-delete them on `/apps/judgment/` after restoring.

1. Check out the Agent Platform Git revision that produced the backup, or a
   compatible newer version. Deploy its chart to a fresh cluster so PostgreSQL
   and the platform-generated credentials exist. Do not restore old K3s
   ServiceAccount tokens or Helm release Secrets.
2. Decrypt and verify the archive on the trusted machine. The resulting
   directory contains **plaintext** SQL and Secret values; it is created mode
   0700 and files mode 0600. Remove it after the restore.

   ```sh
   python3 scripts/backup_restore.py extract ap-recovery-YYYYMMDDTHHMMSSZ.tar.age \
     --identity /path/to/identity.txt --output /private/tmp/ap-restore
   gzip -t /private/tmp/ap-restore/database.sql.gz
   ```

3. Stop API, dispatcher, recorder, scheduler, app, and connector workloads so
   none writes during restore. Record their replica counts for restart. Keep
   PostgreSQL running. Port-forward `svc/ap-postgresql` to local port 5433.
   Read the **fresh cluster's** `ap-postgresql` password from its Secret into
   `PGPASSWORD` on the trusted machine; do not replace that Secret with the
   archived one. Drop and recreate only the `agentplatform` database, then
   restore the dump with `ON_ERROR_STOP`:

   ```sh
   kubectl -n agent-platform port-forward svc/ap-postgresql 5433:5432
   # In another trusted terminal, use the fresh cluster's generated password.
   # The command is saved in shell history; the password value is not.
   export PGPASSWORD="$(kubectl -n agent-platform get secret ap-postgresql \
     -o jsonpath='{.data.postgres-password}' | base64 --decode)"
   dropdb   -h 127.0.0.1 -p 5433 -U postgres --if-exists agentplatform
   createdb -h 127.0.0.1 -p 5433 -U postgres agentplatform
   gzip -dc /private/tmp/ap-restore/database.sql.gz | \
     psql -h 127.0.0.1 -p 5433 -U postgres -d agentplatform -v ON_ERROR_STOP=1
   unset PGPASSWORD
   ```

4. Restore user-managed connector credentials. The helper omits generated
   `ap-*`, `app-*`, `tool-*`, QA login, and run-JWT values: a fresh chart must
   keep its own PostgreSQL/Kafka/app passwords and internal signing keys.

   ```sh
   python3 scripts/backup_restore.py secrets-manifest \
     /private/tmp/ap-restore/secrets.json | \
     kubectl -n agent-platform apply -f -
   ```

5. Restart the workloads at their recorded replica counts. Verify agents,
   Kai's Chat Identity and its Discord connector, Relay, artifacts, and one
   end-to-end run. Check Settings → Connections; some providers may require a
   new token or verification. Remove `/private/tmp/ap-restore` from the trusted
   machine after verification.

The archive captures PostgreSQL at one consistent `pg_dump` snapshot and
Kubernetes Secrets moments later. It does not preserve in-flight Kafka events,
running pods, K3s's SQLite control-plane database, or node configuration.
Restoring the SQL dump replaces all newer database state, including agent
memories and messages created after its timestamp. For a destroyed NUC, rebuild
K3s and the chart from Git first, then follow the fresh-cluster procedure.

## Failure signals and a restore drill

Inspect `kubectl -n agent-platform get cronjob ap-cloud-backup` and its recent
Jobs. The export Job fails if `pg_dump`, encryption, Secret capture, or cloud
upload fails; it never writes a success receipt for a partial upload. In
Settings → Backups, a file marked **Local only** needs investigation. Download
one cloud copy periodically, compare its SHA-256 with the local receipt, and
run the offline inspection. A genuine restore drill needs a disposable cluster
and must verify Kai's identity and a sample artifact, not just `gzip -t`.
