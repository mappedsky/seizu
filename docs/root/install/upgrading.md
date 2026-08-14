# Upgrading Seizu

Read the [changelog](https://github.com/mappedsky/seizu/blob/master/CHANGELOG.md)
from the version you run through the version you plan to deploy. Breaking
changes link to the applicable procedure in this guide. Do not skip intermediate
procedures when upgrading across more than one release.

For every production upgrade:

1. Record the running Seizu version and take recoverable backups of PostgreSQL,
   the chat checkpoint database, configuration files, and deployment secrets.
2. Read every applicable breaking-change section and identify settings that
   must be added, removed, or renamed before startup.
3. Quiesce writes and stop the web and Temporal worker processes when a
   procedure calls for an offline migration. A database backup taken while
   writes continue is not a cutover point.
4. Apply the release-specific procedure below. Seizu runs inspector-guarded
   Alembic application-schema migrations during startup; LangGraph owns the
   checkpoint schema. A release-specific data migration is never implied by
   those schema upgrades.
5. Verify schema versions, entity counts, schedules, authentication, and a
   representative report before reopening traffic.
6. Keep the pre-upgrade backups and old deployment artifacts until the
   verification window closes. Do not run an older Seizu release against a
   database written by a newer one unless its upgrade procedure explicitly says
   that downgrade is supported.

## Unreleased

### Migrating from DynamoDB to PostgreSQL

The release that removes the DynamoDB backends is a hard storage boundary. Do
not point it at an empty PostgreSQL database and start accepting traffic.

1. On the last release that supports DynamoDB, block external writes and stop
   every Temporal worker. Leave one API process reachable only by the migration
   operator, then take recoverable backups of the application table, checkpoint
   table, and any checkpoint-offload bucket.
2. Run `seizu export --config /safe/path/seizu-export.yaml` against that
   isolated API, then stop it. The export contains the durable product
   configuration supported by seed/export: spaces, reports, workflows,
   toolsets, skillsets, and their cross-references. Preserve this file with the
   database backups.
3. Provision and back up two PostgreSQL databases: one for `SQL_DATABASE_*`
   and one for `CHAT_CHECKPOINT_DATABASE_*`. Start this release with
   `CHAT_CHECKPOINT_CREATE_TABLE=true` and without any removed backend or
   DynamoDB checkpoint settings; Alembic upgrades the application schema and
   LangGraph creates the checkpoint schema.
4. Import the exported configuration with `seizu seed --force --config
   /safe/path/seizu-export.yaml` and verify report, workflow, space, toolset,
   and skillset counts before reopening traffic.

User profiles are rebuilt from the identity provider on the next authenticated
request. Query history, user-defined role history, action confirmations,
scheduled/chat run transcripts, and report version history are not represented
by the seed/export format. If those records are retention requirements, remain
on the last dual-backend release and perform a deployment-specific database
migration before upgrading. The PostgreSQL-only release deliberately refuses
stale backend settings instead of pretending those records were copied.

Existing DynamoDB LangGraph checkpoints and their offloaded objects are not
migrated. Conversation history therefore starts empty on PostgreSQL; remove the
corresponding chat-session records during a deployment-specific migration so
the sidebar cannot point at missing checkpoints. Keep the old tables and bucket
unchanged until the verification window closes.

Rollback means stopping all new writes, restoring the pre-cutover backups, and
redeploying the last release that supports DynamoDB. Never point that older
release at data written by the PostgreSQL-only release.

Startup rejects `REPORT_STORE_BACKEND`, `CHAT_CHECKPOINT_BACKEND`, and all
removed persistence-specific DynamoDB/S3 settings even when they contain the old
SQL-selecting values. This makes an incomplete cutover fail before any schema or
application write.
