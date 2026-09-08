# VPN Gate OpenVPN UDP

Parse data from [www.vpngate.net](https://www.vpngate.net) and save it to csv file like VPN Gate Public API  ([www.vpngate.net/api/iphone/](https://www.vpngate.net/api/iphone/))

### Environment required
1. Python 3.x installed

### Installation
* Setup venv with `python -m venv venv` command
* Activate venv with `venv\Script\activate` on Windows or `source venv\bin\activate` on Linux, Unix
* Install required python library with `pip install -r requirements.txt`

### How to run?
Type `python .` command then hit Enter in root directory

### Configuration
You can change output csv location in line 7 in file `__main__.py` like this
```python
# csv output file
csv_file_path = "output/udp"
```

### Mirror site fallback (映像站)
A single update run may fail when `www.vpngate.net` is unreachable. To reduce the chance of failure every run behaves like this:

1. Read the mirror-site list persisted in `mirror_sites.json` (repo root).
2. If the list is not empty, pick a different starting mirror every 30 minutes (stateless time-slot rotation) and try each mirror **once** (no retry, fast fail) until one produces a non-empty `output/udp.csv` / `output/udp.json`.
3. Only when **all** mirrors fail does the run fall back to the official site with the original retry behaviour.
4. After every execution the mirror-site list is refreshed once from [https://www.vpngate.net/ja/sites.aspx](https://www.vpngate.net/ja/sites.aspx) (best effort; the old list is kept if the refresh fails). The file is written back only when the URL list actually changes, so CI does not create a commit on every scheduled run.

The GitHub Actions workflow (`schedule`/`push` to `master`) commits `mirror_sites.json` back automatically only when its content changed. Pushes that only touch `mirror_sites.json` do not trigger the pipeline again, and `concurrency` prevents overlapping runs.

Made with ♥️ by [Hoàng Rio](https://hoangnguyendong.dev)
