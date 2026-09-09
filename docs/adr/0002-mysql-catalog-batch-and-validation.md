# Mirror each released catalog into MySQL and validate its nodes per batch

Release assets alone cannot answer "which node is reachable and what does it unlock", so every update run now mirrors its scraped snapshot into MySQL `vpn_nodes` (batch_id = GitHub Actions run id, aligned with the Release tag), UPSERTs rows whose `host` already exists so they keep their id, and deletes rows left over from older batches, leaving the table with exactly the latest catalog. The new `validate` job then reads that batch from MySQL, splits it across 5 parallel shards, and for each node (including public-vpn hosts) tests OpenVPN connectivity through tun0 plus Netflix/Abema/Bahamut/ChatGPT unlock, writing one latest-result row per node into `vpn_validate_results` (UNIQUE `node_id`, FK to `vpn_nodes` ON DELETE CASCADE).

Status: accepted

Considered options:

- Separate batch-metadata table — rejected: rows carrying `batch_id` keep the snapshot self-contained, and "latest batch wins" needs no history beyond the rows themselves.
- Keep the standalone `validate-openvpn.yml` (workflow_dispatch only) — rejected in favour of merging validation into `pythonapp.yml` so it runs automatically on every schedule/push and splits work across 5 parallel matrix shards.
- Keep result rows keyed by (run_id, idx) as before — rejected: a per-node key lets results update in place, and the FK cascade removes results together with their node.

Consequences:

- MySQL write failures never block the Release upload or fail the workflow (they warn in the log); if the batch store failed, the validate job finds no nodes for its run id and skips itself green.
- Only the most recent catalog snapshot and each node's most recent result are retained; older batches and their cascaded results disappear on the next successful store.
- Deleting a node row (host vanished from a newer catalog) automatically deletes its validation result through the foreign key.
- Validation now runs up to twice per hour against live nodes, costing ~5 parallel runner-minutes per run; a shard fails the run only when it tested at least one node and every one of them failed.
