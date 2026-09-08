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
