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
        for base_url in ordered:
            ok = run_update(base_url, retries=1)
            if ok:
                used_mirror = True
                break
            print("Mirror site failed ({0}); try next source.\n".format(base_url))
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
