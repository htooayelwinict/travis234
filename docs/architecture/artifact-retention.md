# Durable artifact retention ownership

Durable artifact collection is an explicit, nonautomatic SDK maintenance
operation. Travis234 does not invoke it during startup, session construction,
shutdown, or in a background task, and no configuration option enables
automatic collection.

`ArtifactGarbageCollector.collect()` is the sole collection entry point. A
caller must construct the collector with the durable artifact store and session
catalog, then deliberately choose whether to execute a dry run. A dry run
reports the same deletion candidates without removing objects.

Collection shares the artifact store maintenance lock with promotion. It scans
every session manifest before deleting anything and fails closed when a
manifest or object entry cannot be validated. It removes only content-addressed
objects that are unreferenced by every successfully validated manifest, and it
syncs each changed object directory before reporting completion.

This owner is deliberately dormant in the application runtime. Retaining it as
an explicit maintenance primitive avoids silently converting session history
or lifecycle events into destructive storage policy. Any future automatic
retention policy requires a separate product design, regression tests, and an
explicit user-facing configuration and safety review.
