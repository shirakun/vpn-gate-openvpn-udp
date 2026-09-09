
from pyquery import PyQuery
from urllib import request
from urllib.parse import urlparse
import re
import csv
import base64
import time
import os
from datetime import datetime, timezone
import threading
import json

ERROR_MSG = "Method: {0} throw exception: {1} at: {2}"

OFFICIAL_SITE_URL = "https://www.vpngate.net"
MIRROR_SITES_PAGE_URL = OFFICIAL_SITE_URL + "/ja/sites.aspx"

# The list page carries up to ~100 relays. Downloading every config in one
# parallel burst per row makes the source (official or mirror) reset or close
# the connections ("Connection reset by peer" / "Remote end closed"), silently
# discarding nodes: measured yields are ~25 rows at 10 concurrent downloads,
# ~93-98 at 3, and a full 98/98 at 2 on a strict mirror, while a shared CI
# egress can trigger resets even at 3. Cap config downloads with a shared
# semaphore (parsing stays threaded as before) at 2, and when a download
# fails, retry the same config from another mirror site whose content is
# identical (only the base URL differs), so a reset burst no longer
# permanently drops the affected rows.
CONFIG_DOWNLOAD_CONCURRENCY = 2
# Total download attempts allowed per config. The mirror bases are tried in
# order (the site whose list page was parsed first, then each alternate
# mirror); when no alternate is available the same URL is retried with a
# short delay. Connection resets/refusals are transient throttling and
# usually succeed from a different mirror once the burst has passed.
CONFIG_DOWNLOAD_MAX_ATTEMPTS = 6
# Seconds to wait between consecutive download attempts.
CONFIG_DOWNLOAD_RETRY_BACKOFF = 1

class VPNGateBase():
    # 网络请求参数 (与原有行为一致; 映像站快速尝试会覆盖为 1 次)
    _max_retries = 10
    _retry_interval = 10
    _timeout = 8

    def _get_url_once(self, url):
        """Fetch one URL a single time. Returns the decoded body or None."""
        timeout = self._timeout
        try:
            req = request.Request(url)
            with request.urlopen(req, timeout=timeout) as response:
                if response.headers.get_content_charset() is None:
                    encoding = 'utf-8'
                else:
                    encoding = response.headers.get_content_charset()
                return response.read().decode(encoding)
        except Exception as ex:
            print(ERROR_MSG.format(
                "_get_url", ex, datetime.now()))
            return None

    def _get_url(self, url):
        max_retries = self._max_retries
        retry_interval = self._retry_interval
        for attempt in range(max_retries):
            html = self._get_url_once(url)
            if html is not None:
                return html
            if attempt < max_retries - 1:  # 如果不是最后一次尝试
                time.sleep(retry_interval)
        return None


class VPNGateItem(VPNGateBase, threading.Thread):
    def _set_data(self, *args, **kwargs):
        """
        Set class attribute
        """
        for key, value in kwargs.items():
            self.__setattr__(key, value)
    ##
    # Fill another value
    ##

    def __fill_other_value(self, all_td, server):
        # Score
        server[2] = int(all_td.eq(9).text().replace(',', ''))
        # Ping
        server[3] = all_td.eq(3).find('b').eq(1).text().replace(' ms', '')
        # Speed
        speed_text = all_td.eq(3).find('b').eq(
            0).text().replace(' Mbps', '').replace(',', '')
        speed = float(speed_text)
        server[4] = int(speed * pow(1024, 2))
        # CountryLong
        server[5] = all_td.eq(0).text().strip()
        # CountryShort Ex image src ../images/flags/CO.png
        image_src = all_td.eq(0).find('img').eq(0).attr('src')
        server[6] = image_src[16:-4]
        # NumVpnSessions
        server[7] = all_td.eq(2).find('b').eq(
            0).text().replace(' sessions', '')
        # Uptime
        uptime_text = all_td.eq(2).find('span').eq(1).text()
        uptime_prop = uptime_text.split(' ')
        if len(uptime_prop) > 1:
            if uptime_prop[1] == 'days':
                uptime = int(uptime_prop[0]) * 24 * 60 * 60
            elif uptime_prop[1] == 'hours':
                uptime = int(uptime_prop[0]) * 60 * 60
            elif uptime_prop[1] == 'mins':
                uptime = int(uptime_prop[0]) * 60
            else:
                uptime = 0
        else:
            uptime = 0
        server[8] = uptime
        # TotalUsers
        total_user_text = all_td.eq(2).text()
        regex = r"Total\s([\d,]+)\susers"
        matches = re.finditer(regex, total_user_text)
        for match_mum, match in enumerate(matches, start=1):
            total_user = match.group(1)
            server[9] = int(total_user.replace(',', ''))
            break
        # TotalTraffic
        total_traffic_text = all_td.eq(3).find('b').eq(2).text()
        total_traffic_props = total_traffic_text.split(' ')
        if len(total_traffic_text) > 1:
            total_traffic_str = total_traffic_props[0].replace(',', '')
            if total_traffic_props[1] == 'GB':
                server[10] = int(float(total_traffic_str) * pow(1024, 3))
            elif total_traffic_props[1] == 'MB':
                server[10] = int(float(total_traffic_str) * pow(1024, 2))
            elif total_traffic_props[1] == 'KB':
                server[10] = int(float(total_traffic_str) * 1024)
            else:
                server[10] = int(total_traffic_str)
        # LogType
        server[11] = '2 Weeks'
        # Operator
        server[12] = all_td.eq(8).find('b').eq(0).text().replace("By ", "").replace(",", "") # Remove , from operator
        # Message
        message = all_td.eq(8).find('i').eq(
            1).text().replace('"', '').replace(',', ' ')
        server[13] = re.sub(r"\n", " ", message)
        return server

    def __get_openvpn_config_base64(self, item_params):
        try:
            download_path = '/common/openvpn_download.aspx?sid=%s&%s&host=%s&port=%s&hid=%s&/vpngate_%s.ovpn'
            for item in item_params:
                props = item.split('=')
                if len(props) < 2:
                    continue
                elif props[0] == 'ip':
                    ip = props[1]
                elif props[0] == 'tcp':
                    tcp_port = props[1]
                elif props[0] == 'udp':
                    udp_port = props[1]
                elif props[0] == 'sid':
                    sid = props[1]
                elif props[0] == 'hid':
                    hid = props[1]
            if tcp_port != '0':
                download_path = download_path % (
                    sid, 'tcp=1', ip, tcp_port, hid, ip + '_tcp_'+tcp_port)
            elif udp_port != '0':
                download_path = download_path % (
                    sid, 'udp=1', ip, udp_port, hid, ip + '_udp_'+udp_port)
            # Transient failures (connection reset / remote end closed) are
            # mirror throttling of the download burst. Every mirror site serves
            # identical content, so when a download fails try the same config
            # URL against the next mirror base instead of dropping the row
            # after one attempt; without alternates, retry the same base URL
            # with a short delay so the reset burst passes.
            bases = [self.__getattribute__('__base_url')]
            try:
                alternate_bases = list(
                    self.__getattribute__('__alternate_base_urls') or [])
            except AttributeError:
                alternate_bases = []
            for alternate_base in alternate_bases:
                if alternate_base and alternate_base not in bases:
                    bases.append(alternate_base)
            if len(bases) > CONFIG_DOWNLOAD_MAX_ATTEMPTS:
                # Keep total attempts bounded even with a long mirror list.
                bases = bases[:CONFIG_DOWNLOAD_MAX_ATTEMPTS]
            elif len(bases) == 1:
                # No alternate mirror available: retry the same base URL.
                bases = bases * CONFIG_DOWNLOAD_MAX_ATTEMPTS
            openvpn_config_string = None
            for attempt_index, base in enumerate(bases):
                openvpn_config_string = self._get_url_once(
                    base + download_path)
                if openvpn_config_string is not None:
                    break
                if attempt_index < len(bases) - 1:
                    print("Config download failed via {0} (attempt {1}/{2}); "
                          "trying another mirror base.".format(
                              base, attempt_index + 1, len(bases)))
                    time.sleep(CONFIG_DOWNLOAD_RETRY_BACKOFF)
            if openvpn_config_string is None:
                return None
            openvpn_config_string = re.sub(
                r"#.+?$", "", openvpn_config_string, flags=re.MULTILINE)
            openvpn_config_string = re.sub(
                r"\n+", "\n", openvpn_config_string, flags=re.MULTILINE)
            openvpn_config_string = re.sub(
                r"(\n\r|\r\n)+", r"\1", openvpn_config_string, flags=re.MULTILINE)
            openvpn_config_string = re.sub(
                r"^\n\r\n", "", openvpn_config_string, flags=re.MULTILINE)
            base64_config = base64.b64encode(
                openvpn_config_string.encode('utf-8'))
            base64_config = base64_config.decode('utf-8')
            return base64_config
        except Exception as ex:
            print(ERROR_MSG.format(
                "__get_openvpn_config_base64", ex, datetime.now()))
            return None

    def __process_item(self):
        all_td = PyQuery(self.__getattribute__('__el')).find('td')
        a_tag = all_td.eq(6).find('a[href^="do_openvpn.aspx?"]')
        if a_tag.length == 0:
            return
        href = a_tag.attr('href').replace('do_openvpn.aspx?', '')
        items = href.split('&')
        server = ['', '', '', '', '', '', '', '',
                  '', '', '', '', '', '', '', '', '', '0', '0']
        for item in items:
            props = item.split('=')
            if len(props) < 2:
                continue
            if props[0] == 'fqdn':
                server[0] = props[1].replace('.opengw.net', '')
            elif props[0] == 'ip':
                server[1] = props[1]
            elif props[0] == 'tcp':
                server[15] = props[1]
            elif props[0] == 'udp':
                server[16] = props[1]
        server = self.__fill_other_value(all_td, server)
        # OpenVPN_ConfigData_Base64 (bounded concurrency; see CONFIG_DOWNLOAD_CONCURRENCY)
        semaphore = self.__getattribute__('__download_semaphore')
        if semaphore is not None:
            semaphore.acquire()
        try:
            server[14] = self.__get_openvpn_config_base64(items)
        finally:
            if semaphore is not None:
                semaphore.release()
        # L2TP support
        a_l2tp = all_td.eq(5).find('a[href="howto_l2tp.aspx"]')
        if a_l2tp.length > 0:
            # Is L2TP Support
            server[17] = '1'
        # SSTP Support
        a_sstp = all_td.eq(7).find('a[href="howto_sstp.aspx"]')
        if a_sstp.length > 0:
            server[18] = '1'
        if server[14] is None:
            return  # openvpn_config_base64 is none skip this item
        if self.__getattribute__('__sleep_time') > 0:
            time.sleep(self.__getattribute__('__sleep_time'))
        self.lock.acquire()
        self.__getattribute__('__list_server').append(server)
        self.lock.release()

    def run(self):
        self.lock = threading.Lock()
        self.__process_item()


class VPNGate(VPNGateBase):

    def __init__(self, __base_url, __file_path, __json_file_path, __sleep_time,
                 retries=None, retry_interval=None, timeout=None,
                 alternate_base_urls=None):
        self.__base_url = __base_url
        # Mirror sites that serve identical content; used to retry config
        # downloads that hit transient failures such as "Connection reset by
        # peer" on the primary base URL.
        self.__alternate_base_urls = []
        for alternate_url in (alternate_base_urls or []):
            if (alternate_url and alternate_url != self.__base_url
                    and alternate_url not in self.__alternate_base_urls):
                self.__alternate_base_urls.append(alternate_url)
        self.__file_path = __file_path
        self.__json_file_path = __json_file_path
        self.__sleep_time = __sleep_time
        if retries is not None:
            self._max_retries = retries
        if retry_interval is not None:
            self._retry_interval = retry_interval
        if timeout is not None:
            self._timeout = timeout
        self.__list_server = [['*vpn_servers']]
        self._threads = []
        # Shared by every VPNGateItem thread to bound concurrent config downloads.
        self.__download_semaphore = threading.BoundedSemaphore(
            CONFIG_DOWNLOAD_CONCURRENCY)

    def __write_csv_file(self, __file_path):
        csv.register_dialect('myDialect', delimiter=',', lineterminator='\n')
        with open(__file_path, 'w', encoding='utf-8') as write_file:
            writer = csv.writer(write_file, dialect="myDialect")
            writer.writerows(self.__list_server)
        write_file.close()

    def __write_json_file(self, __json_file_path):
        # 提取键（key）和值（value）
        keys = self.__list_server[1]  # 第二行为键
        values = [row for row in self.__list_server[2:-1]]  # 从第三行开始为值，排除最后一行

        # 构造新的 JSON 结构
        json_data = []
        for value_row in values:
            item = dict(zip(keys, value_row))  # 将键和值组合成字典
            json_data.append(item)

        # 写入 JSON 文件
        with open(__json_file_path, 'w', encoding='utf-8') as write_file:
            json.dump(json_data, write_file, ensure_ascii=False, indent=4)


    def __process_item(self, index, el):
        t = VPNGateItem()
        t._set_data(__index=index, __el=el, __base_url=self.__base_url, __file_path=self.__file_path, __json_file_path=self.__json_file_path, __sleep_time=self.__sleep_time, __list_server=self.__list_server, _max_retries=self._max_retries, _retry_interval=self._retry_interval, _timeout=self._timeout, __download_semaphore=self.__download_semaphore, __alternate_base_urls=self.__alternate_base_urls)
        self._threads.append(t)
        t.start()

    def start_process(self, lock_file_path):
        try:
            with open(lock_file_path, 'w') as lock_file:
                lock_file.write("{0}".format(datetime.now()))
                lock_file.close()
            html = self._get_url(self.__base_url+'/en/')
            if html is None:
                print("Skip write file because cannot fetch list page: {0}".format(self.__base_url + '/en/'))
                return False
            if html is not None:
                pq = PyQuery(html)
                self.__list_server.append([
                    '#HostName', 'IP', 'Score', 'Ping', 'Speed', 'CountryLong', 'CountryShort', 'NumVpnSessions', 'Uptime', 'TotalUsers', 'TotalTraffic', 'LogType', 'Operator', 'Message', 'OpenVPN_ConfigData_Base64', 'TcpPort', 'UdpPort', 'L2TP', 'SSTP'
                ])
                openvpn_links = pq('#vg_hosts_table_id').eq(2).find('tr')
                openvpn_links.each(self.__process_item)
                # Join thread
                for t in self._threads:
                    t.join()
                if len(self.__list_server) < 2:
                    print("Skip write file because empty server list")
                    return False
                # 写入 CSV 文件为 udp.csv
                self.__write_csv_file(self.__file_path)
                # 写入 JSON 文件为 udp.json
                self.__write_json_file(self.__json_file_path)
                return True
        except Exception as ex:
            print(ERROR_MSG.format(
                "run", ex, datetime.now()))
            return False
        finally:
            if os.path.exists(lock_file_path):
                # Remove lock when complete
                os.remove(lock_file_path)

    def run(self):
        lock_file_path = 'vpngate.lock'
        if (os.path.exists(lock_file_path)):
            with open(lock_file_path, 'r') as lock_file:
                lock_time = datetime.strptime(
                    lock_file.read(), '%Y-%m-%d %H:%M:%S.%f')
                lock_file.close()
                time_from_last_lock = datetime.now() - lock_time
                if time_from_last_lock.total_seconds() > 60 * 20:
                    print("Lock file expired. Script coninue to run.\n")
                else:
                    print("Lock file found. Script currently runing.\n")
                    return False
        return self.start_process(lock_file_path)


# --- VPN Gate 映像站(镜像站)清单支持 ---

MIRROR_SITES_FILE = "mirror_sites.json"


def _normalize_mirror_url(href):
    """
    把映像站链接规整为根地址(scheme://host:port), 并剔除官网本身。
    映像站页面与官网结构一致, 只要把 base url 换掉即可整条流水线复用。
    """
    if not href or not href.startswith(("http://", "https://")):
        return None
    parsed = urlparse(href)
    base = "{0}://{1}".format(parsed.scheme, parsed.netloc)
    if base == OFFICIAL_SITE_URL or base == "http://www.vpngate.net":
        return None
    return base


def fetch_mirror_urls(sites_url=MIRROR_SITES_PAGE_URL):
    """
    从官网映像站清单页抓取映像站根地址(去重、保持页面顺序)。
    失败或解析为空时返回空列表, 由调用方决定是否保留旧清单。
    """
    fetcher = VPNGateBase()
    fetcher._max_retries = 1  # 刷新清单只快速尝试一次
    html = fetcher._get_url(sites_url)
    if not html:
        return []
    urls = []
    for item in PyQuery(html)("ul.listBigArrow a").items():
        base = _normalize_mirror_url(item.attr("href"))
        if base and base not in urls:
            urls.append(base)
    return urls


def load_mirror_urls(file_path=MIRROR_SITES_FILE):
    """
    读取上次保存的映像站清单。文件不存在或损坏时返回空列表(调用方将直接使用官网)。
    """
    try:
        with open(file_path, "r", encoding="utf-8") as mirror_file:
            data = json.load(mirror_file)
        urls = data.get("urls")
        if isinstance(urls, list):
            return [u for u in urls if isinstance(u, str) and u]
    except FileNotFoundError:
        return []
    except Exception as ex:
        print(ERROR_MSG.format("load_mirror_urls", ex, datetime.now()))
    return []


def update_mirror_urls_file(file_path=MIRROR_SITES_FILE, urls=None):
    """
    仅在映像站 URL 顺序列表发生变化时覆写清单文件, 避免每次运行都产生 git 提交。
    返回是否发生了写入。
    """
    cleaned = []
    for url in (urls or []):
        if url and url not in cleaned:
            cleaned.append(url)
    if cleaned == load_mirror_urls(file_path):
        print("Mirror site list unchanged.")
        return False
    data = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "urls": cleaned,
    }
    with open(file_path, "w", encoding="utf-8") as mirror_file:
        json.dump(data, mirror_file, ensure_ascii=False, indent=2)
    print("Mirror site list saved to {0} ({1} sites).".format(file_path, len(cleaned)))
    return True
