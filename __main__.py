import json
import os
import shutil
import sys
import time
from datetime import datetime

from vpngate import (
    OFFICIAL_SITE_URL,
    MIRROR_SITES_FILE,
    MIRROR_SITES_PAGE_URL,
    VPNGate,
    fetch_mirror_urls,
    load_mirror_urls,
    update_mirror_urls_file,
)

# Edit it to use with mirror site
vpngate_base_url = OFFICIAL_SITE_URL
# csv output file
csv_file_path = "output/udp.csv"
json_file_path = "output/udp.json"
sleep_time = 0

# Mirror-site list file (kept at repo root). Refreshed once per run; only written
# back when the URL list actually changes, so CI does not create a commit every run.
mirror_sites_file = MIRROR_SITES_FILE


def run_update(base_url, retries=None, retry_interval=None, timeout=None):
    """
    Run one full update against the given base url.
    Returns True only when the whole pipeline works (list page + per-row config
    downloads) and non-empty CSV/JSON files are produced.
    """
    print("Update from base url: {0} (retries={1})".format(base_url, retries))
    start_time = datetime.now()
    print("Script start at: {0}\n".format(start_time))
    vpngate = VPNGate(
        base_url,
        csv_file_path,
        json_file_path,
        sleep_time,
        retries=retries,
        retry_interval=retry_interval,
        timeout=timeout,
    )
    ok = bool(vpngate.run())
    end_time = datetime.now()
    running_time = (end_time - start_time).total_seconds()
    print("Script finish at: {0}\n".format(end_time))
    print("Running in {0} seconds, ok={1}\n".format(running_time, ok))
    return ok


# A healthy mirror serves configs for ~all rows of its list (~100). Some mirrors
# only proxy a small subset (measured ~15-18 rows even with serial downloads), so
# a run that "succeeds" with a tiny yield must not stop the rotation.
MIN_SERVER_ROWS = 30


def _count_json_rows():
    try:
        with open(json_file_path, encoding="utf-8") as json_file:
            return len(json.load(json_file))
    except Exception:
        return 0


def _snapshot_best():
    best_dir = os.path.join(os.path.dirname(json_file_path), ".best")
    os.makedirs(best_dir, exist_ok=True)
    for name in (os.path.basename(json_file_path), os.path.basename(csv_file_path)):
        source = os.path.join(os.path.dirname(json_file_path), name)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(best_dir, name))


def _restore_best():
    best_dir = os.path.join(os.path.dirname(json_file_path), ".best")
    for name in (os.path.basename(json_file_path), os.path.basename(csv_file_path)):
        source = os.path.join(best_dir, name)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(os.path.dirname(json_file_path), name))


def main():
    mirror_urls = load_mirror_urls(mirror_sites_file)
    used_mirror = False
    ok = False

    if mirror_urls:
        # Stateless time-slot rotation: start at a different mirror every
        # 30 minutes so load is spread across mirrors without extra state.
        start_index = int(time.time()) // 1800 % len(mirror_urls)
        ordered = mirror_urls[start_index:] + mirror_urls[:start_index]
        print("Loaded {0} mirror sites from {1}, start at index {2}.".format(
            len(mirror_urls), mirror_sites_file, start_index))
        best_rows = -1
        best_base_url = None
        for base_url in ordered:
            run_ok = run_update(base_url, retries=1)
            if not run_ok:
                print("Mirror site failed ({0}); try next source.\n".format(base_url))
                continue
            rows = _count_json_rows()
            print("Mirror site {0} yielded {1} rows.".format(base_url, rows))
            if rows > best_rows:
                best_rows = rows
                best_base_url = base_url
                _snapshot_best()
            if rows >= MIN_SERVER_ROWS:
                used_mirror = True
                ok = True
                print("Mirror site {0} accepted ({1} >= {2} rows).\n".format(
                    base_url, rows, MIN_SERVER_ROWS))
                break
            print("Mirror site {0} yielded only {1} rows (< {2}); try next source.\n".format(
                base_url, rows, MIN_SERVER_ROWS))
        if not ok and best_rows >= 0:
            # No mirror reached the minimum yield; keep the best of what we got.
            _restore_best()
            used_mirror = True
            ok = True
            print("No mirror reached {0} rows; kept best result: {1} rows from {2}.\n".format(
                MIN_SERVER_ROWS, best_rows, best_base_url))
    else:
        print("No mirror site list available; use official site directly.")

    if not ok:
        print("All mirror sites failed or unavailable; fall back to official site {0}.".format(
            OFFICIAL_SITE_URL))
        ok = run_update(OFFICIAL_SITE_URL)

    # After every execution, fetch the mirror-site list once from the official
    # site (best effort; keep the previous list if the fetch fails).
    try:
        refreshed = fetch_mirror_urls(MIRROR_SITES_PAGE_URL)
        if refreshed:
            update_mirror_urls_file(mirror_sites_file, refreshed)
        else:
            print("Could not refresh mirror site list from {0}; keep existing list.".format(
                MIRROR_SITES_PAGE_URL))
    except Exception as ex:
        print("Failed to refresh mirror site list: {0}".format(ex))

    print("Update finished, ok={0} (source: {1}).".format(
        ok, "mirror" if used_mirror else "official"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
