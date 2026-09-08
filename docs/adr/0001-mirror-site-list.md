# Persist mirror-site list in the repository and rotate sources by time slot

Update runs were failing whenever `www.vpngate.net` was unreachable, so we persist an ordered mirror-site list in `mirror_sites.json` at the repo root, refreshed once per run from the official directory page and committed back only when the URL list actually changes; each run then tries one mirror at a time (stateless 30-minute time-slot rotation, single attempt each) and falls back to the official site with its original retry behaviour only after every mirror fails.

Status: accepted

Considered options:

- Remember the last successful mirror in the repo (stateful) — rejected because it adds an extra write/commit on every run and a mirror that worked once is not more trustworthy than the stateless rotation.
- Try mirrors but treat any reachable list page as success — rejected because a mirror with empty/broken per-row downloads would silently produce an empty data file; a run counts as successful only when non-empty CSV/JSON are written.
- Always write `mirror_sites.json` after each refresh — rejected because `fetched_at` churns every 30 minutes and would create a commit (and a workflow re-trigger) on every run; we only write when the URL list changes.

Consequences:

- A run is only as fresh as the last reachable source; the list can grow stale while the official directory page is down, which is acceptable because an unreachable source is still the best available guess.
- The commit-back push is made with `GITHUB_TOKEN`, which does not re-trigger workflows, and `on: push` also ignores commits that only touch `mirror_sites.json` as a second guard against loops.
