import json
import os
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


def run_update(base_url, retries=None, retry_interval=None, timeout=None,
               alternate_base_urls=None):
    """
    Run one full update against the given base url.
    alternate_base_urls: mirror sites that serve identical content; individual
    config downloads that fail (e.g. "Connection reset by peer") are retried
    from the next base in that list.
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
        alternate_base_urls=alternate_base_urls,
    )
    ok = bool(vpngate.run())
    end_time = datetime.now()
    running_time = (end_time - start_time).total_seconds()
    print("Script finish at: {0}\n".format(end_time))
    print("Running in {0} seconds, ok={1}\n".format(running_time, ok))
    return ok


# Where this snapshot came from, written for the workflow so the Release notes
# can say whether the batch was scraped from a mirror or from the official site.
SOURCE_ENV_FILE = os.path.join(os.path.dirname(json_file_path), "source.env")


def _count_json_rows():
    try:
        with open(json_file_path, encoding="utf-8") as json_file:
            return len(json.load(json_file))
    except Exception:
        return 0


def _write_source_env(source_type, source_url, rows, ok):
    """Publish scrape provenance as KEY=VALUE lines for GitHub Actions env."""
    os.makedirs(os.path.dirname(SOURCE_ENV_FILE), exist_ok=True)
    lines = [
        "SOURCE_TYPE={0}".format(source_type or "none"),
        "SOURCE_URL={0}".format(source_url or ""),
        "SOURCE_NODES={0}".format(rows if rows is not None else ""),
        "SOURCE_OK={0}".format("true" if ok else "false"),
    ]
    with open(SOURCE_ENV_FILE, "w", encoding="utf-8") as env_file:
        env_file.write("\n".join(lines) + "\n")


def main():
    mirror_urls = load_mirror_urls(mirror_sites_file)
    ok = False
    source_type = "none"
    source_url = ""

    if mirror_urls:
        # Stateless time-slot rotation: start at a different mirror every
        # 30 minutes so load is spread across mirrors without extra state.
        start_index = int(time.time()) // 1800 % len(mirror_urls)
        ordered = mirror_urls[start_index:] + mirror_urls[:start_index]
        print("Loaded {0} mirror sites from {1}, start at index {2}.".format(
            len(mirror_urls), mirror_sites_file, start_index))
        for base_url in ordered:
            # Success means the source responded and produced a snapshot; the
            # number of nodes is reported in the provenance but never used to
            # accept or reject a source.
            # The remaining mirrors are alternate download sources for config
            # rows that hit "Connection reset by peer" on this base.
            ok = run_update(base_url, retries=1,
                            alternate_base_urls=[
                                u for u in ordered if u != base_url])
            if ok:
                source_type = "mirror"
                source_url = base_url
                break
            print("Mirror site failed ({0}); try next source.\n".format(base_url))
    else:
        print("No mirror site list available; use official site directly.")

    if not ok:
        print("All mirror sites failed or unavailable; fall back to official site {0}.".format(
            OFFICIAL_SITE_URL))
        ok = run_update(OFFICIAL_SITE_URL,
                        alternate_base_urls=mirror_urls)
        if ok:
            source_type = "official"
            source_url = OFFICIAL_SITE_URL

    rows = _count_json_rows() if ok else 0
    _write_source_env(source_type, source_url, rows, ok)

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

    print("Update finished, ok={0} (source: {1} {2}, rows: {3}).".format(
        ok, source_type, source_url, rows))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
