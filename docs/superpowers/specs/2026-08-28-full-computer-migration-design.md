# Full Computer Migration Design

## Goal

Move the Etsy performance employee to another Windows 10/11 computer without losing trained knowledge, training images, conversations, attachments, Excel sources, generated workbooks, approvals, audit history, or originality guards. The target computer will use a newly authenticated official OpenAI account; the current relay configuration and all old credentials stay on the source computer.

## Approved decisions

- Use a stopped, full `DataDirectory` clone rather than the portable employee ZIP.
- Rebuild the Hermes `etsy-performance-us` Profile from the repository on the target computer.
- Configure the rebuilt Profile with the official `openai-codex` provider and model `gpt-5.6-sol`.
- Require the owner to complete Hermes OAuth and `codex login` interactively on the target computer.
- Never copy Hermes `.env`, OAuth state, Codex auth files, cookies, browser identity, API keys, or the current relay URL as active target configuration.
- Keep the source computer unchanged and available for rollback until target acceptance passes.

## Approaches considered

1. **Stopped full clone — selected.** Copy the complete durable business data tree and a Git bundle after verified shutdown. This is the only approach that preserves training originals and historical Excel binaries without inventing a second media export format.
2. **Portable migration ZIP plus media copy.** This keeps the existing sanitized import path but needs separate attachment, training-image, and Excel copy logic. More moving pieces create a higher omission and conflict risk.
3. **Copy the entire application and Hermes home.** This is mechanically simple but carries machine-bound PID files, browser state, stale paths, and credentials. It is rejected.

## Migration set

The export directory has one self-contained shape:

```text
etsy-employee-full-YYYYMMDD-HHMMSS/
├── repository.bundle
├── data-full/
│   ├── app.db
│   ├── attachments/
│   ├── excel-jobs/
│   ├── training-evidence/
│   ├── trust/
│   └── other durable application data
├── migration-manifest.json
└── RESTORE-README.md
```

The export excludes transient or identity-bearing paths:

- `runtime/`, including `.start-pids.json` and migration capability tokens;
- `migration-workspace/` and previously generated `migration-packages/`;
- `browser-profile/`, because it can contain cookies, local storage, extensions, and machine identity;
- Hermes Profile directories, Codex auth data, API keys, and relay credentials.

The source `training-evidence/` directory, database, attachments, and `excel-jobs/` directory are explicitly included. These are the assets the existing portable ZIP intentionally omits.

## Source-computer export flow

`scripts/export-full-migration.ps1` will:

1. Resolve and validate the project, data, and output paths. It rejects drive roots, UNC paths, reparse points, and an output path inside the source data tree.
2. Measure the included source data and require enough free output space for the data, repository bundle, manifest, and a safety margin before stopping services.
3. Require a clean Git worktree and record the branch and commit.
4. Check the local API when available and block export while any Excel job is queued, running, cancelling, or awaiting review.
5. Stop only the verified backend and frontend processes through the existing PID/port-safe startup script.
6. Copy durable data with `robocopy`, using explicit exclusions and treating exit codes `0` through `7` as success.
7. Create and verify `repository.bundle` with `git bundle create --all` and `git bundle verify`.
8. Build an inventory containing relative paths, byte sizes, SHA-256 values, total bytes, data-category counts, source commit, and schema version.
9. Scan exported relative paths and file names for forbidden credential and runtime patterns before publishing the manifest.
10. Write a standalone `RESTORE-README.md` containing only non-secret instructions.

The script leaves source services stopped. This prevents new writes after the backup point and makes the cutover state explicit. Restarting the source remains a separate, reversible command.

## Target-computer restore flow

The target operator first clones `repository.bundle`. `scripts/restore-full-migration.ps1` then:

1. Verifies the bundle commit and every manifest hash before copying any business data.
2. Requires a new or empty, local, non-OneDrive, non-UNC, non-reparse-point target `DataDirectory`.
3. Copies `data-full` and verifies the destination inventory against the manifest.
4. Ensures no source runtime token, PID record, browser profile, or credential file appeared at the destination.
5. Runs the normal locked dependency bootstrap.
6. Rebuilds `etsy-performance-us` with `openai-codex`, `gpt-5.6-sol`, and high reasoning effort. It never overwrites an existing Profile; an existing target Profile must be backed up and moved aside first.
7. Opens the official Hermes OAuth and Codex CLI login flows. The user performs browser authorization.
8. Requires `verify-employee.ps1 -RunModelCheck -RunDoctor` to pass before starting the website.
9. Starts the backend and frontend with the selected target ports and runs API, employee, data-inventory, and page checks.

The target LAN IP is not copied because it belongs to the target network. The ports remain configurable; after startup, the guide shows how to discover the target IPv4 address. Optional LAN forwarding on port `5174` is a separate, explicit administrator action because it changes Windows networking and firewall state. Restore never enables LAN exposure silently.

## Bootstrap changes

Before this work, `scripts/bootstrap-new-machine.ps1` hardcoded the older `gpt-5.4` official-account path even though the provisioner already supported separate custom-provider setup. The migration work will:

- change the official default model to `gpt-5.6-sol`;
- make provider authentication checks use the selected provider rather than a hardcoded name;
- keep `openai-codex` as the approved migration default;
- leave separate relay/custom-provider provisioning available for future use without activating it in the full-restore workflow or putting a secret in command-line arguments, Git, the manifest, or the README;
- treat a failed real model check as a blocking failure even if the website itself returns HTTP 200.

The target described in this design uses official OAuth only. The source relay outage does not block data export, and its settings do not become target defaults.

## Safety and error handling

- No source data is deleted or overwritten.
- Export refuses to run with active jobs or an unclean repository.
- Restore refuses a non-empty destination and verifies hashes before and after copy.
- Database and files are copied only after service shutdown.
- Any hash, bundle, schema, Profile, OAuth, model, API, or inventory mismatch stops the workflow with a concrete next action.
- Failed target acceptance does not trigger cleanup. The failed target directory is retained for diagnosis, and the source remains the rollback system.
- The source computer should be retained for at least one complete business cycle after successful cutover.

## Acceptance tests

Automated checks will cover:

- safe-path rejection and forbidden-path exclusions;
- active-job export blocking;
- `robocopy` exit-code handling;
- manifest schema, path normalization, byte counts, and SHA-256 verification;
- rejection of tampered or missing files;
- rejection of non-empty restore destinations;
- bootstrap defaults for `openai-codex` and `gpt-5.6-sol`;
- documentation consistency for ports, official login, full-data scope, and excluded credentials.

Manual acceptance on the target computer requires:

1. `/api/health` returns `status=ok`.
2. `/api/employee/status` returns `online`.
3. Hermes model check returns the exact `PROFILE_READY` marker and Doctor has no core failure.
4. Source and target inventory counts match for conversations, attachments, training evidence, knowledge, and Excel jobs.
5. The latest completed historical workbook is downloadable.
6. A copied one-row image workbook produces all five Listing fields.
7. The source file remains unchanged and the output is a new workbook.

## Deliverables

- `scripts/export-full-migration.ps1`
- `scripts/restore-full-migration.ps1`
- updated `scripts/bootstrap-new-machine.ps1`
- documented optional Windows LAN forwarding on port `5174`, with explicit administrator approval
- updated root `README.md`
- updated `docs/operations/网站与数字员工部署迁移指南.md`
- automated migration and startup contract tests
- a locally generated, hash-verified full migration directory ready to copy to the target computer
