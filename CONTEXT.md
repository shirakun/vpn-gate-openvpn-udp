# VPN Gate OpenVPN UDP Context

This project periodically scrapes the VPN Gate service, which publishes OpenVPN/UDP server entries, and publishes a VPN Gate-compatible public data file (CSV/JSON) for clients that cannot reach the official VPN Gate endpoints directly.

## Language

**官网 (Official site)**:
The primary VPN Gate web service at `www.vpngate.net`; the source of truth for server entries and the mirror-site directory page.
_Avoid_: main site, VPN Gate homepage

**映像站 (Mirror site)**:
A full copy of the VPN Gate site (list pages and per-server OpenVPN config downloads) hosted outside the official domain, used when the official site is unreachable.
_Avoid_: replica, proxy, CDN node

**更新运行 (Update run)**:
One execution that produces a fresh server list output; it tries mirror sites first and falls back to the official site.
_Avoid_: refresh cycle, scrape job

**映像站清单 (Mirror-site list)**:
The persisted ordered list of known mirror sites that an update run consults before the official site; refreshed after every run.
_Avoid_: mirror cache, site pool

**源候选顺序 (Source candidate order)**:
The order in which an update run tries sources: one mirror at a time in rotation order, then the official site as the final fallback.
_Avoid_: failover chain, source priority

**服务器条目 (Server entry)**:
One row describing a single VPN Gate server (host, ports, score, location, OpenVPN config data, protocol support).
_Avoid_: server record, vpn row

**数据文件 (Data file)**:
The published artifacts (`output/udp.csv` and `output/udp.json`) that mirror the shape of the VPN Gate Public API.
_Avoid_: dataset, export

**清单回写 (List write-back)**:
Persisting a changed mirror-site list into the repository so later runs can start from the newest mirror set; deliberately not done when the list is unchanged.
_Avoid_: mirror update commit, back-sync

**目录批次 (Catalog batch)**:
The set of server entries stored into MySQL (`vpn_nodes`) by one update run, identified by the run's GitHub Actions id so it lines up with the Release tag.
_Avoid_: snapshot set, release set

**节点 (Node)**:
One server entry as stored in MySQL `vpn_nodes`, uniquely identified by its `host`; validation results attach to it through `node_id`.
_Avoid_: db row, catalog record

**批次替换 (Batch replacement)**:
The store step replacing the catalog: nodes of the current run are UPSERTed by `host` and rows from all older batches are deleted, so `vpn_nodes` always holds only the latest batch.
_Avoid_: upsert sweep, catalog refresh

**节点验证 (Node validation)**:
The automated check that connects through each node's OpenVPN config (route-nopull) to confirm tun0 egress, then runs in-tunnel Netflix/Abema/Bahamut/ChatGPT unlock probes.
_Avoid_: connectivity audit, unlock scan

**验证分片 (Validation shard)**:
One of the five parallel runners that split the current catalog batch by row index and validate only its own slice of nodes.
_Avoid_: worker, partition

**验证结果 (Validation result)**:
The stored outcome for one node (`vpn_validate_results`, unique per `node_id`); only the latest result per node is kept and deleting the node deletes its result.
_Avoid_: test record, unlock report
