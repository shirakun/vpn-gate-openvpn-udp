#!/usr/bin/env python3
"""Store one validation shard's results into MySQL (table `vpn_validate_results`).

Reads the 14-column TSV written by validate_shard.sh and UPSERTs one row per
node on the natural key node_id (UNIQUE uq_vpn_validate_node). A node's row is
replaced in place, so each node keeps only its latest result; nodes removed
from vpn_nodes lose their results through the ON DELETE CASCADE foreign key.

Result persistence is best-effort by design (see docs/adr/0002): this script
never makes the workflow fail. DB errors are printed as warnings and exit
code is still 0, so shard verdicts come solely from the live validation step.

Usage: store_results.py [results.tsv path]   (default: /tmp/results.tsv)
Env:   DB_HOST, DB_USER, DB_NAME, DB_PASS   - MySQL connection
       RUN_ID                               - batch id (GitHub Actions run id)
Exit:  0 always (unless invoked incorrectly), so the workflow stays green.
"""
import os
import sys

import pymysql

TSV_COLUMNS = [
    "idx", "cc", "host", "proto", "ip", "port", "status", "egress", "elapsed",
    "netflix", "abema", "bahamut", "chatgpt", "node_id",
]
# order must match the vpn_validate_results DDL in store_nodes.py
RESULT_COLUMNS = [
    "batch_id", "node_id", "cc", "host", "proto", "ip", "port", "status",
    "egress", "elapsed", "netflix", "abema", "bahamut", "chatgpt",
]


def clean(value):
    """'-' means "not measured" in the TSV; store it as an empty string."""
    return "" if value == "-" else value


def to_int(value):
    value = clean(str(value)).strip()
    if not value:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_tsv(path):
    """Yield one dict per usable TSV row."""
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) < len(TSV_COLUMNS):
                print("skip malformed TSV line {0}: {1} fields".format(
                    line_no, len(fields)))
                continue
            row = dict(zip(TSV_COLUMNS, fields))
            if not row.get("node_id", "").strip():
                print("skip TSV line {0}: empty node_id".format(line_no))
                continue
            yield row


def main():
    tsv_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/results.tsv"
    required = ("DB_HOST", "DB_USER", "DB_NAME", "DB_PASS", "RUN_ID")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print("Missing environment variables: {0}".format(", ".join(missing)))
        return 0
    batch_id = int(os.environ["RUN_ID"])

    if not os.path.exists(tsv_path):
        print("No results file {0}; nothing to store (validation likely skipped).".format(tsv_path))
        return 0

    rows = list(parse_tsv(tsv_path))
    print("Parsed {0} result rows from {1}".format(len(rows), tsv_path))
    if not rows:
        print("No result rows to store.")
        return 0

    update_cols = [c for c in RESULT_COLUMNS if c not in ("batch_id", "node_id")]
    assignment = ", ".join("{0}=VALUES({0})".format(c) for c in update_cols)
    placeholders = ", ".join(["%s"] * len(RESULT_COLUMNS))
    upsert_sql = (
        "INSERT INTO vpn_validate_results ({0}) VALUES ({1}) "
        "ON DUPLICATE KEY UPDATE {2}".format(
            ", ".join(RESULT_COLUMNS), placeholders, assignment
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
        cur = conn.cursor()
        values = []
        for row in rows:
            values.append((
                batch_id,
                to_int(row["node_id"]),
                clean(row["cc"]).strip(),
                clean(row["host"]).strip(),
                clean(row["proto"]).strip(),
                clean(row["ip"]).strip(),
                to_int(row["port"]),
                clean(row["status"]).strip(),
                clean(row["egress"]).strip(),
                to_int(row["elapsed"]),
                clean(row["netflix"]).strip(),
                clean(row["abema"]).strip(),
                clean(row["bahamut"]).strip(),
                clean(row["chatgpt"]).strip(),
            ))
        cur.executemany(upsert_sql, values)
        conn.commit()
        print("Stored batch {0}: upserted {1} result rows.".format(batch_id, len(values)))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary and values:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write("**MySQL 结果写入: {0} 行 (batch={1})**\n\n".format(len(values), batch_id))
        return 0
    except Exception as ex:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        # Best-effort by design: warn loudly but never fail the workflow.
        print("::warning::store_results failed (batch {0}): {1}".format(batch_id, ex))
        return 0
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())