"""safe_fetch：SSRF 防护。全部 monkeypatch DNS 与连接，不会真的出网。"""

import http.client
import io

import pytest

from app.services import safe_fetch
from app.services.safe_fetch import FetchError, UnsafeURLError

PUBLIC_IP = "93.184.216.34"


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = io.BytesIO(body)
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}

    def getheader(self, name, default=None):
        return self._headers.get(name.lower(), default)

    def read(self, size=-1):
        return self._body.read(size)


class FakeConnection:
    def __init__(self, response, log, ip):
        self._response = response
        self.log = log
        self.ip = ip

    def request(self, method, path, headers=None):
        self.log.append({"ip": self.ip, "path": path, "headers": headers or {}})

    def getresponse(self):
        return self._response

    def close(self):
        pass


@pytest.fixture
def net(monkeypatch):
    """假 DNS + 假连接：dns 映射主机 → IP，routes 映射 (host, path) → 响应。"""
    state = {"dns": {}, "routes": {}, "log": [], "dns_calls": []}

    def fake_getaddrinfo(host, port):
        state["dns_calls"].append(host)
        if host not in state["dns"]:
            raise OSError("nxdomain")
        return state["dns"][host]

    def fake_open(scheme, host, port, ip, timeout):
        # 连接阶段拿到的必须是已校验的 IP，而不是主机名
        assert ip not in state["dns"]  # 传进来的必须是 IP 而不是主机名
        response = state["routes"][host]
        if isinstance(response, list):
            response = response.pop(0)
        return FakeConnection(response, state["log"], ip)

    monkeypatch.setattr(safe_fetch, "_getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(safe_fetch, "_open_connection", fake_open)
    monkeypatch.delenv("ALLOW_PRIVATE_FETCH", raising=False)
    return state


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a",
        "http://10.0.0.5/a",
        "http://172.16.0.1/a",
        "http://192.168.1.1/a",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/a",
        "http://[::ffff:127.0.0.1]/a",
        "http://[::ffff:169.254.169.254]/a",
        "http://[fd00::1]/a",
        "http://[fe80::1]/a",
        "http://0.0.0.0/a",
        "http://224.0.0.1/a",
        "http://100.64.0.1/a",
        "http://[64:ff9b::7f00:1]/a",
    ],
)
def test_private_ip_literals_are_rejected(net, url):
    with pytest.raises(UnsafeURLError):
        safe_fetch.fetch(url)
    assert net["log"] == []  # 没有发出任何连接


def test_domain_resolving_to_private_ip_is_rejected(net):
    net["dns"]["evil.example"] = ["10.1.2.3"]
    with pytest.raises(UnsafeURLError):
        safe_fetch.fetch("https://evil.example/x")


def test_mixed_dns_answer_is_rejected(net):
    """只要有一个解析结果是内网就整体拒绝。"""
    net["dns"]["mixed.example"] = [PUBLIC_IP, "127.0.0.1"]
    with pytest.raises(UnsafeURLError):
        safe_fetch.fetch("https://mixed.example/x")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/x",
        "ftp://example.com/x",
        "data:text/plain,hi",
        "//example.com/x",
    ],
)
def test_disallowed_schemes(net, url):
    with pytest.raises(UnsafeURLError):
        safe_fetch.fetch(url)


def test_credentials_in_url_are_rejected(net):
    with pytest.raises(UnsafeURLError):
        safe_fetch.fetch("http://user:pass@example.com/x")


def test_public_url_is_fetched_with_pinned_ip(net):
    net["dns"]["cdn.example"] = [PUBLIC_IP]
    net["routes"]["cdn.example"] = FakeResponse(200, b"hello", {"Content-Type": "image/png; charset=x"})
    result = safe_fetch.fetch("https://cdn.example/a/b.png?x=1", allowed_mime={"image/png"})
    assert result.content == b"hello"
    assert result.content_type == "image/png"
    assert result.final_url == "https://cdn.example/a/b.png?x=1"
    # 连接用的是解析后的 IP，路径与查询串原样保留
    assert net["log"] == [{"ip": PUBLIC_IP, "path": "/a/b.png?x=1", "headers": net["log"][0]["headers"]}]
    assert net["dns_calls"] == ["cdn.example"]  # 只解析一次，建连阶段不再解析


def test_redirect_to_private_address_is_rejected(net):
    net["dns"]["a.example"] = [PUBLIC_IP]
    net["routes"]["a.example"] = FakeResponse(302, b"", {"Location": "http://169.254.169.254/latest/"})
    with pytest.raises(UnsafeURLError):
        safe_fetch.fetch("https://a.example/x")


def test_redirect_to_domain_resolving_private_is_rejected(net):
    net["dns"]["a.example"] = [PUBLIC_IP]
    net["dns"]["internal.example"] = ["192.168.0.9"]
    net["routes"]["a.example"] = FakeResponse(301, b"", {"Location": "https://internal.example/x"})
    with pytest.raises(UnsafeURLError):
        safe_fetch.fetch("https://a.example/x")


def test_relative_redirect_is_followed_and_limited(net):
    net["dns"]["a.example"] = [PUBLIC_IP]
    net["routes"]["a.example"] = [
        FakeResponse(302, b"", {"Location": "/b"}),
        FakeResponse(200, b"ok", {"Content-Type": "text/plain"}),
    ]
    result = safe_fetch.fetch("https://a.example/a")
    assert result.content == b"ok" and result.final_url == "https://a.example/b"

    net["routes"]["a.example"] = [FakeResponse(302, b"", {"Location": "/loop"}) for _ in range(10)]
    with pytest.raises(FetchError, match="重定向"):
        safe_fetch.fetch("https://a.example/a")
    assert len(net["log"]) == 2 + 4  # 前一次成功 2 跳 + 本次最多 1 + 3 跳


def test_oversize_body_is_aborted(net):
    net["dns"]["big.example"] = [PUBLIC_IP]
    net["routes"]["big.example"] = FakeResponse(200, b"x" * 5000)
    with pytest.raises(FetchError, match="大小"):
        safe_fetch.fetch("https://big.example/x", max_bytes=1000)


def test_oversize_declared_length_is_rejected_early(net):
    net["dns"]["big.example"] = [PUBLIC_IP]
    net["routes"]["big.example"] = FakeResponse(200, b"x", {"Content-Length": "999999"})
    with pytest.raises(FetchError, match="大小"):
        safe_fetch.fetch("https://big.example/x", max_bytes=1000)


def test_mime_allowlist_and_http_error(net):
    net["dns"]["m.example"] = [PUBLIC_IP]
    net["routes"]["m.example"] = FakeResponse(200, b"<html>", {"Content-Type": "text/html"})
    with pytest.raises(FetchError, match="MIME"):
        safe_fetch.fetch("https://m.example/x", allowed_mime={"image/png"})
    net["routes"]["m.example"] = FakeResponse(404, b"")
    with pytest.raises(FetchError, match="404"):
        safe_fetch.fetch("https://m.example/x")


def test_dns_failure(net):
    with pytest.raises(FetchError, match="解析失败"):
        safe_fetch.fetch("https://nope.example/x")


def test_allow_private_fetch_switch(net, monkeypatch):
    net["routes"]["127.0.0.1"] = FakeResponse(200, b"dev")
    with pytest.raises(UnsafeURLError):
        safe_fetch.fetch("http://127.0.0.1/x")
    monkeypatch.setenv("ALLOW_PRIVATE_FETCH", "true")
    assert safe_fetch.fetch("http://127.0.0.1/x").content == b"dev"


def test_custom_headers_not_leaked_across_hosts(net):
    net["dns"]["a.example"] = [PUBLIC_IP]
    net["dns"]["b.example"] = [PUBLIC_IP]
    net["routes"]["a.example"] = FakeResponse(302, b"", {"Location": "https://b.example/x"})
    net["routes"]["b.example"] = FakeResponse(200, b"ok")
    safe_fetch.fetch("https://a.example/x", headers={"Authorization": "Bearer t"})
    assert net["log"][0]["headers"].get("Authorization") == "Bearer t"
    assert "Authorization" not in net["log"][1]["headers"]


def test_fetch_to_file_streams_and_cleans_up(net, tmp_path):
    net["dns"]["f.example"] = [PUBLIC_IP]
    net["routes"]["f.example"] = FakeResponse(200, b"abc" * 10)
    dest = tmp_path / "out.bin"
    safe_fetch.fetch_to_file("https://f.example/x", dest)
    assert dest.read_bytes() == b"abc" * 10

    net["routes"]["f.example"] = FakeResponse(200, b"x" * 5000)
    dest2 = tmp_path / "big.bin"
    with pytest.raises(FetchError):
        safe_fetch.fetch_to_file("https://f.example/x", dest2, max_bytes=1000)
    assert not dest2.exists()


@pytest.mark.asyncio
async def test_afetch(net):
    net["dns"]["a.example"] = [PUBLIC_IP]
    net["routes"]["a.example"] = FakeResponse(200, b"async")
    assert (await safe_fetch.afetch("https://a.example/x")).content == b"async"


def test_open_connection_pins_ip_and_keeps_host(monkeypatch):
    """真实 _open_connection：socket 只连 IP，Host 头保留原主机名，且不再触发 DNS。"""
    connected = []

    class FakeSock:
        def close(self):
            pass

    def fake_create_connection(address, timeout=None):
        connected.append(address)
        return FakeSock()

    monkeypatch.setattr(safe_fetch.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(
        safe_fetch.socket, "getaddrinfo", lambda *a, **k: pytest.fail("建连阶段不应再解析 DNS")
    )
    connection = safe_fetch._open_connection("http", "cdn.example", 8080, PUBLIC_IP, 5)
    assert connected == [(PUBLIC_IP, 8080)]
    assert isinstance(connection, http.client.HTTPConnection)
    assert connection.host == "cdn.example"
    assert connection.sock is not None


class TrickleResponse(FakeResponse):
    """每次 read 慢慢吐 1 字节：单次读取都没超时，但总时间无限拉长。"""

    def read(self, size=-1):
        import time

        time.sleep(0.05)
        return b"x"


def test_overall_deadline_stops_trickling_response(net):
    net["dns"]["slow.example"] = [PUBLIC_IP]
    net["routes"]["slow.example"] = TrickleResponse(200)
    import time

    started = time.monotonic()
    with pytest.raises(FetchError, match="总时限|下载超时"):
        safe_fetch.fetch("https://slow.example/x", timeout=30, total_timeout=0.3)
    assert time.monotonic() - started < 2


def test_dns_resolution_is_bounded_by_deadline(net, monkeypatch):
    import time

    def slow_dns(host, port):
        time.sleep(1.5)
        return [PUBLIC_IP]

    monkeypatch.setattr(safe_fetch, "_getaddrinfo", slow_dns)
    started = time.monotonic()
    with pytest.raises(FetchError, match="解析超时"):
        safe_fetch.fetch("https://slowdns.example/x", total_timeout=0.2)
    assert time.monotonic() - started < 1.2


def test_socket_timeout_is_clamped_to_remaining_time(net):
    net["dns"]["a.example"] = [PUBLIC_IP]
    seen = []

    class Sock:
        def settimeout(self, value):
            seen.append(value)

    class Conn(FakeConnection):
        sock = Sock()

    response = FakeResponse(200, b"ok")
    net["routes"]["a.example"] = response
    original = safe_fetch._open_connection
    safe_fetch._open_connection = lambda scheme, host, port, ip, timeout: Conn(response, net["log"], ip)
    try:
        safe_fetch.fetch("https://a.example/x", timeout=30, total_timeout=5)
    finally:
        safe_fetch._open_connection = original
    assert seen and all(v <= 5 for v in seen)


@pytest.mark.parametrize(
    "start,target",
    [
        ("https://a.example/x", "http://a.example/x"),  # 协议降级
        ("https://a.example/x", "https://a.example:8443/x"),  # 同主机换端口
        ("https://a.example/x", "https://b.example/x"),  # 换主机
    ],
)
def test_sensitive_headers_dropped_when_origin_changes(net, start, target):
    for host in ("a.example", "b.example"):
        net["dns"][host] = [PUBLIC_IP]
    net["routes"]["a.example"] = [
        FakeResponse(302, b"", {"Location": target}),
        FakeResponse(200, b"ok"),
    ]
    net["routes"]["b.example"] = FakeResponse(200, b"ok")
    safe_fetch.fetch(
        start, headers={"Authorization": "Bearer t", "Cookie": "s=1", "Proxy-Authorization": "x"}
    )
    first, second = net["log"][0]["headers"], net["log"][1]["headers"]
    assert first["Authorization"] == "Bearer t"
    for name in ("Authorization", "Cookie", "Proxy-Authorization"):
        assert name not in second


def test_headers_kept_on_same_origin_redirect(net):
    net["dns"]["a.example"] = [PUBLIC_IP]
    net["routes"]["a.example"] = [FakeResponse(302, b"", {"Location": "/y"}), FakeResponse(200, b"ok")]
    safe_fetch.fetch("https://a.example/x", headers={"Authorization": "Bearer t"})
    assert net["log"][1]["headers"]["Authorization"] == "Bearer t"


def test_truncated_body_is_an_error_and_partial_file_removed(net, tmp_path):
    net["dns"]["t.example"] = [PUBLIC_IP]
    net["routes"]["t.example"] = FakeResponse(200, b"abc", {"Content-Length": "10"})
    with pytest.raises(FetchError, match="不完整"):
        safe_fetch.fetch("https://t.example/x")
    net["routes"]["t.example"] = FakeResponse(200, b"abc", {"Content-Length": "10"})
    dest = tmp_path / "partial.bin"
    with pytest.raises(FetchError, match="不完整"):
        safe_fetch.fetch_to_file("https://t.example/x", dest)
    assert not dest.exists()


def test_incomplete_read_exception_is_wrapped(net):
    net["dns"]["t.example"] = [PUBLIC_IP]

    class Broken(FakeResponse):
        def read(self, size=-1):
            raise http.client.IncompleteRead(b"ab", 5)

    net["routes"]["t.example"] = Broken(200)
    with pytest.raises(FetchError, match="读取失败"):
        safe_fetch.fetch("https://t.example/x")


# ── 真实本地 TCP 服务器：验证看门狗能打断阻塞中的 recv ──


@pytest.fixture
def trickle_server(monkeypatch):
    import socket
    import threading
    import time

    monkeypatch.setenv("ALLOW_PRIVATE_FETCH", "true")  # 仅此测试内放行 127.0.0.1
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    stop = threading.Event()
    mode = {"value": "body"}

    def serve():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                continue
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    def handle(conn):
        try:
            conn.recv(4096)
            if mode["value"] == "body":
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
                payload = b"x" * 100000
            else:  # 响应头慢速涓流
                payload = b"HTTP/1.1 200 OK\r\n" + b"X-Slow: a\r\n" * 5000
            for i in range(len(payload)):
                if stop.is_set():
                    break
                conn.sendall(payload[i : i + 1])
                time.sleep(0.05)
        except OSError:
            pass
        finally:
            conn.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield listener.getsockname()[1], mode
    stop.set()
    listener.close()


@pytest.mark.parametrize("mode_name", ["body", "headers"])
def test_watchdog_interrupts_blocked_read(trickle_server, mode_name):
    import time

    port, mode = trickle_server
    mode["value"] = mode_name
    started = time.monotonic()
    with pytest.raises(FetchError, match="下载超时"):
        safe_fetch.fetch(f"http://127.0.0.1:{port}/x", timeout=30, total_timeout=0.3)
    assert time.monotonic() - started < 0.8


def test_watchdog_removes_partial_file(trickle_server, tmp_path):
    port, mode = trickle_server
    dest = tmp_path / "x.bin"
    with pytest.raises(FetchError, match="下载超时"):
        safe_fetch.fetch_to_file(f"http://127.0.0.1:{port}/x", dest, timeout=30, total_timeout=0.3)
    assert not dest.exists()
