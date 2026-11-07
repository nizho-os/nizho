# Archon D-Bus development contract

System bus name: `org.nizho.Archon1`.
Object path: `/org/nizho/Archon1`.
Manager interface: `org.nizho.Archon1.Manager`.
No method accepts a shell command, executable, URL, path, plan contents or caller
identity. The compatibility inventory interface is also available on the object.

## Types

- Package `(ssssssbtts)`: full Flatpak ref, name, summary, version/commit,
  repository, backend, installed, download bytes, installed bytes, trust policy.
  A zero size means unavailable/zero, not a guaranteed zero-byte transfer.
- Plan `(stss)`: opaque ID, expiry Unix seconds, backend, resolved-plan JSON.
- Transaction `(ssssts)`: ID, state, package, operation, created Unix seconds,
  result/failure detail.

Plan and transaction responses contain a single struct output argument, rather
than flattened fields. Integration tests cover this wire boundary. Native plan
JSON has schema_version 1 and is opaque review data to clients; CommitTransaction
accepts only its ID. Clients must not reconstruct or supply a plan for commit.

## Methods

| Method | In | Out | Scope |
| --- | --- | --- | --- |
| GetCapabilities | none | a(ss) | Backend support and scope |
| SearchPackages | ssuu | a(ssssssbtts) | Query, AppStream category or empty, offset, limit |
| GetPackageDetails | ss | s | Full application ref, configured repository; JSON metadata |
| GetInstalledPackages | none | a(ssssssbtts) | System applications |
| GetAvailableUpdates | none | a(ssssssbtts) | System Flatpak applications; candidate versions |
| GetRepositories | none | s | JSON array, configured native remotes |
| PlanTransaction | sss | (stss) | install/remove/update, full ref, repository |
| CommitTransaction | s | s | Plan ID; returns transaction ID after durable consumption |
| GetTransactionStatus | s | (ssssts) | Caller UID's transaction |
| CancelTransaction | s | none | Caller-bound pending/running transaction |
| GetTransactionHistory | uu | a(ssssts) | Caller UID's offset and limit |

Search limits are 1–100; offsets are bounded. The UI requests 50 rows. Queries
are at most 128 bytes and reject control characters. Category IDs are bounded
ASCII alphanumeric strings. Application refs must be full
`app/NAME/ARCH/BRANCH`; the service does not guess package identity or source.
Repository identifiers are bounded and cannot be options, paths or URLs.

Details currently provide native metadata, sizes, required runtime and optional
AppStream description/license/homepage. Package signatures remain enforced by
libflatpak; `signature-required` describes configured policy and is not a claim
that every catalogue entry has already been downloaded and verified.

## Signals

- PackageCatalogueChanged()
- TransactionProgress(s ID, u percent, s detail)
- TransactionCompleted(s ID)
- TransactionFailed(s ID, s detail)

Progress is the native operation's progress, not an invented overall percentage.
Cancellation is forwarded to Gio.Cancellable inside libflatpak. Completion can
race a cancellation request; native success remains succeeded. A failed/cancelled
multi-operation transaction can have completed earlier native operations.
Clients must inspect inventory/history and create a fresh plan after interruption.

System package image transactions and DeploymentStaged/DeploymentActivated
signals are unsupported; they are not synthesized from application results.
System deployment status comes from GetSystemStatus on the compatibility
`org.nizho.Archon1` interface and the existing Nizho publisher.

## Errors and recovery

D-Bus error names include InvalidArgs for malformed/expired/consumed IDs and
invalid bounds, AccessDenied for ownership/authorization restrictions,
NotSupported for unavailable compatibility image operations, and Failed for
backend/IO/conflict/quota errors. More granular backend error codes remain release
work; a backend failure string is not a successful empty catalogue.

Plans expire after 300 seconds. UID and unique sender are obtained from the bus.
Commit checks ownership, sender, state, clock, polkit, clock again, then persists
running before spawning the native backend. Native re-resolution and signature
checks happen before application. No stale plan is implicitly upgraded to a new
plan. Server restarts mark pending/running records interrupted, preserving history
but preventing replay. A new plan is required.

Examples in an installed guest:

```sh
busctl introspect org.nizho.Archon1 /org/nizho/Archon1
busctl call org.nizho.Archon1 /org/nizho/Archon1 org.nizho.Archon1.Manager GetCapabilities
busctl call org.nizho.Archon1 /org/nizho/Archon1 org.nizho.Archon1.Manager SearchPackages ssuu Calculator '' 0 50
```
