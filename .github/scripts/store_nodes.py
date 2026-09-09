#!/usr/bin/env python3
"""Store the freshly scraped server snapshot into MySQL (table `vpn_nodes`).

Catalog batch semantics (see docs/adr/0002):
* One catalog batch per scrape run; the batch id is the GitHub Actions run id.
* Rows whose natural key (`host`, i.e. #HostName) already exists are UPDATEd in
  place, keeping their `id`, so validation results stay attached.
* Rows left over from older batches are DELETEd; their validation results are
  removed through the ON DELETE CASCADE foreign key.

The schema is bootstrapped idempotently. A legacy `vpn_validate_results`
table (pre-node_id layout, run_id/idx key) is dropped and rebuilt once.

Usage:  store_nodes.py <udp.json path>
Env:    DB_HOST, DB_USER, DB_NAME, DB_PASS   - MySQL connection
        RUN_ID                               - batch id (GitHub Actions run id)
Exit:   0 on success, 1 on failure (the workflow decides whether to block).
"""
import base64
import json
import os
import re
import sys

import pymysql

DDL_NODES = """
CREATE TABLE IF NOT EXISTS vpn_nodes (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
  batch_id BIGINT NOT NULL,
  host VARCHAR(255) NOT NULL,
  ip VARCHAR(64) NOT NULL DEFAULT '',
  score BIGINT NULL,
  ping INT NULL,
  speed BIGINT NULL,
  country_long VARCHAR(128) NOT NULL DEFAULT '',
  country_short VARCHAR(8) NOT NULL DEFAULT '',
  num_vpn_sessions BIGINT NULL,
  uptime BIGINT NULL,
  total_users BIGINT NULL,
  total_traffic BIGINT NULL,
  log_type VARCHAR(32) NOT NULL DEFAULT '',
  operator VARCHAR(255) NOT NULL DEFAULT '',
  message TEXT,
  config_base64 MEDIUMTEXT NULL,
  tcp_port INT NULL,
  udp_port INT NULL,
  l2tp CHAR(1) NOT NULL DEFAULT '0',
  sstp CHAR(1) NOT NULL DEFAULT '0',
  cfg_proto VARCHAR(8) NOT NULL DEFAULT '',
  cfg_ip VARCHAR(255) NOT NULL DEFAULT '',
  cfg_port INT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_vpn_nodes_host (host),
  KEY idx_vpn_nodes_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

DDL_RESULTS = """
CREATE TABLE IF NOT EXISTS vpn_validate_results (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
  node_id INT UNSIGNED NOT NULL,
  batch_id BIGINT NOT NULL,
  cc VARCHAR(8) NOT NULL DEFAULT '',
  host VARCHAR(255) NOT NULL DEFAULT '',
  proto VARCHAR(8) NOT NULL DEFAULT '',
  ip VARCHAR(64) NOT NULL DEFAULT '',
  port INT NULL,
  status VARCHAR(16) NOT NULL DEFAULT '',
  egress VARCHAR(64) NOT NULL DEFAULT '',
  elapsed INT NULL,
  netflix VARCHAR(128) NOT NULL DEFAULT '',
  abema VARCHAR(128) NOT NULL DEFAULT '',
  bahamut VARCHAR(128) NOT NULL DEFAULT '',
  chatgpt VARCHAR(128) NOT NULL DEFAULT '',
  tested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_vpn_validate_node (node_id),
  KEY idx_vpn_validate_batch (batch_id),
  CONSTRAINT fk_vpn_validate_node FOREIGN KEY (node_id)
    REFERENCES vpn_nodes (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

NODE_COLUMNS = [
    "batch_id", "host", "ip", "score", "ping", "speed", "country_long",
    "country_short", "num_vpn_sessions", "uptime", "total_users",
    "total_traffic", "log_type", "operator", "message", "config_base64",
    "tcp_port", "udp_port", "l2tp", "sstp", "cfg_proto", "cfg_ip", "cfg_port",
]


def to_int(value):
    """Best-effort integer conversion; empty/unparseable values become None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "N/A"):
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def to_flag(value):
    return "1" if str(value).strip().lower() in ("1", "true", "yes", "y") else "0"


def decode_config(row):
    """Return (proto, remote_ip_or_host, port) parsed from the config data."""
    b64 = row.get("OpenVPN_ConfigData_Base64") or ""
    cfg_text = ""
    if b64:
        try:
            cfg_text = base64.b64decode(b64).decode("utf-8", "replace")
        except Exception:
            cfg_text = ""
    proto = ""
    m = re.search(r"(?m)^proto\s+(\w+)", cfg_text)
    if m:
        proto = m.group(1).lower()
    cfg_ip = ""
    cfg_port = None
    m = re.search(r"(?m)^remote\s+(\S+)\s+(\d+)", cfg_text)
    if m:
        cfg_ip = m.group(1)
        cfg_port = to_int(m.group(2))
    return proto, cfg_ip, cfg_port


def ensure_schema(conn):
    """Idempotent bootstrap + one-time legacy results table rebuild."""
    cur = conn.cursor()
    cur.execute(DDL_NODES)
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = 'vpn_validate_results'"
    )
    if cur.fetchone()[0]:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'vpn_validate_results' "
            "AND column_name = 'node_id'"
        )
        if cur.fetchone()[0] == 0:
            print("Legacy vpn_validate_results detected; dropping and rebuilding it.")
            cur.execute("DROP TABLE vpn_validate_results")
    cur.execute(DDL_RESULTS)
    conn.commit()


def load_rows(json_path):
    with open(json_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    rows = []
    for idx, item in enumerate(data):
        host = (item.get("#HostName") or item.get("HostName") or "").strip()
        if not host:
            print("Skip row {0}: empty #HostName".format(idx))
            continue
        proto, cfg_ip, cfg_port = decode_config(item)
        rows.append((
            host,
            (item.get("IP") or "").strip(),
            to_int(item.get("Score")),
            to_int(item.get("Ping")),
            to_int(item.get("Speed")),
            (item.get("CountryLong") or "").strip(),
            (item.get("CountryShort") or "").strip(),
            to_int(item.get("NumVpnSessions")),
            to_int(item.get("Uptime")),
            to_int(item.get("TotalUsers")),
            to_int(item.get("TotalTraffic")),
            (item.get("LogType") or "2 Weeks").strip(),
            (item.get("Operator") or "").strip(),
            re.sub(r"\s+", " ", (item.get("Message") or "")).strip(),
            (item.get("OpenVPN_ConfigData_Base64") or "").strip(),
            to_int(item.get("TcpPort")),
            to_int(item.get("UdpPort")),
            to_flag(item.get("L2TP")),
            to_flag(item.get("SSTP")),
            proto,
            cfg_ip,
            cfg_port,
        ))
    return rows


def main():
    if len(sys.argv) != 2:
        print("Usage: store_nodes.py <udp.json path>")
        return 2
    json_path = sys.argv[1]
    required = ("DB_HOST", "DB_USER", "DB_NAME", "DB_PASS", "RUN_ID")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print("Missing environment variables: {0}".format(", ".join(missing)))
        return 1
    batch_id = int(os.environ["RUN_ID"])

    rows = load_rows(json_path)
    print("Loaded {0} server entries from {1}".format(len(rows), json_path))
    if not rows:
        print("Nothing to store; leaving previous catalog untouched.")
        return 0

    assignment = ", ".join("{0}=VALUES({0})".format(c) for c in NODE_COLUMNS)
    placeholders = ", ".join(["%s"] * len(NODE_COLUMNS))
    insert_sql = (
        "INSERT INTO vpn_nodes ({0}) VALUES ({1}) "
        "ON DUPLICATE KEY UPDATE {2}".format(
            ", ".join(NODE_COLUMNS), placeholders, assignment
        )
    )

    conn = None
    try:
        conn = pymysql.connect(
            host=os.environ["DB_HOST"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASS"],
            database=os.environ["DB_NAME"],
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=20,
        )
        ensure_schema(conn)
        cur = conn.cursor()
        values = []
        for row in rows:
            values.append((batch_id,) + row)
        cur.executemany(insert_sql, values)
        deleted = cur.execute(
            "DELETE FROM vpn_nodes WHERE batch_id <> %s", (batch_id,)
        )
        cur.execute(
            "SELECT COUNT(*) FROM vpn_nodes WHERE batch_id = %s", (batch_id,)
        )
        kept = cur.fetchone()[0]
        conn.commit()
        print("Stored batch {0}: upserted {1} rows, deleted {2} stale rows, catalog now {3} rows.".format(
            batch_id, len(values), deleted, kept))
        return 0
    except Exception as ex:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        print("DB store failed (batch {0}): {1}".format(
            os.environ.get("RUN_ID", "?"), ex))
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
