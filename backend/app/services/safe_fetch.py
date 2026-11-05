"""SSRF 防护的服务端取数：凡是 URL 可能来自用户或第三方响应，一律走这里，不要直接 requests.get。

防护点：
- 只允许 http / https，拒绝带账号密码的 URL；
- 先自己解析 DNS，只要有一个解析结果落在内网 / 回环 / 链路本地（含 169.254.169.254 元数据）/
  组播 / 保留 / IPv6 ULA / IPv4 映射等非公网地址，整个请求拒绝；
- 防 DNS 重绑定：校验通过后直接用「解析得到的 IP」建 TCP 连接，HTTPS 的 SNI 与证书校验、
  HTTP 的 Host 头仍然使用原主机名——连接阶段不会再做第二次 DNS 解析；
- 每一跳重定向都重新走上面的全部校验，最多 3 跳；
- 超时、响应体大小上限（边读边截断）、可选 MIME 白名单；
- 开发环境可用 ALLOW_PRIVATE_FETCH=true 放行 localhost / 内网（默认关闭，生产严禁开启）。

取舍：这里用标准库 http.client 手工建连，不依赖 requests / httpx 的连接池，因此不支持代理、
HTTP/2 与连接复用；对「下载一个素材文件」的场景足够，换来的是连接目标完全可控。
"""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import os
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional
from urllib.parse import urljoin, urlsplit

DEFAULT_TIMEOUT = 30.0  # 单次连接 / 单次读取超时
DEFAULT_TOTAL_TIMEOUT = 120.0  # 整个取数（含 DNS、重定向、读完 body）的总时限下限
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
MAX_REDIRECTS = 3
# 常见场景的大小上限与 MIME 白名单，调用方按需取用
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_AUDIO_BYTES = 50 * 1024 * 1024
MAX_VIDEO_BYTES = 512 * 1024 * 1024
MAX_MODEL_BYTES = 256 * 1024 * 1024
VIDEO_MIME = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-matroska",
    "video/x-msvideo",
    "application/octet-stream",
    "binary/octet-stream",
}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_CHUNK = 64 * 1024
_DNS_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="safe-fetch-dns")


class UnsafeURLError(ValueError):
    """URL 不被允许（协议、主机或解析出的 IP 不安全）。"""


class FetchError(RuntimeError):
    """取数失败：状态码、超时、过大、MIME 不符等。"""


@dataclass
class FetchResult:
    content: bytes
    content_type: str
    final_url: str
    status: int


def allow_private_fetch() -> bool:
    """读取 ALLOW_PRIVATE_FETCH（运行时直读 env，便于测试 monkeypatch）。仅开发环境使用。"""
    return os.getenv("ALLOW_PRIVATE_FETCH", "false").strip().lower() in ("1", "true", "yes")


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """非公网地址一律视为不安全。"""
    if isinstance(ip, ipaddress.IPv6Address):
        # ::ffff:a.b.c.d（IPv4 映射）、6to4（2002::/16）、NAT64（64:ff9b::/96）内嵌的 IPv4 同样检查
        embedded: list[ipaddress.IPv4Address] = []
        if ip.ipv4_mapped is not None:
            embedded.append(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            embedded.append(ip.sixtofour)
        if ip in ipaddress.ip_network("64:ff9b::/96"):
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if any(is_blocked_ip(item) for item in embedded):
            return True
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _getaddrinfo(host: str, port: int) -> list[str]:
    """DNS 解析 → IP 字符串列表。单独成函数，方便测试 monkeypatch。"""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)
    return ips


def _validate_url(url: str) -> tuple[str, str, int, str]:
    """返回 (scheme, host, port, path_with_query)；不安全时抛 UnsafeURLError。"""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsafeURLError("只允许 http / https 链接")
    if parts.username or parts.password:
        raise UnsafeURLError("链接不能包含账号密码")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("链接缺少主机名")
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as error:
        raise UnsafeURLError("链接端口不合法") from error
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return scheme, host, port, path


def resolve_safe_ips(
    host: str, port: int, *, allow_private: Optional[bool] = None, deadline: Optional[float] = None
) -> list[str]:
    """解析主机并校验：只要有一个结果不是公网地址就整体拒绝（防混合应答绕过）。"""
    if allow_private is None:
        allow_private = allow_private_fetch()
    try:
        literal = ipaddress.ip_address(host)
        ips = [str(literal)]
    except ValueError:
        try:
            if deadline is None:
                ips = _getaddrinfo(host, port)
            else:
                # getaddrinfo 本身不可中断：放线程里跑，等待时间受总时限约束
                future = _DNS_POOL.submit(_getaddrinfo, host, port)
                ips = future.result(timeout=max(0.0, deadline - time.monotonic()))
        except FutureTimeout as error:
            raise FetchError(f"域名解析超时: {host}") from error
        except OSError as error:
            raise FetchError(f"域名解析失败: {host}") from error
    if not ips:
        raise FetchError(f"域名解析失败: {host}")
    if not allow_private:
        for ip in ips:
            if is_blocked_ip(ipaddress.ip_address(ip)):
                raise UnsafeURLError("链接指向内网或保留地址，已拒绝")
    return ips


def _open_connection(
    scheme: str, host: str, port: int, ip: str, timeout: float
) -> http.client.HTTPConnection:
    """用已校验的 IP 建 TCP 连接；HTTPS 的 SNI / 证书校验与 Host 头仍用原主机名。"""
    sock = socket.create_connection((ip, port), timeout=timeout)
    try:
        if scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            # 必须用 HTTPSConnection：它的默认端口是 443，Host 头才是 "host" 而不是 "host:443"。
            # 签名链接（火山 TOS / S3 预签名）把 Host 算进签名，多出 ":443" 会直接 403。
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(host, port, timeout=timeout)
        else:
            connection = http.client.HTTPConnection(host, port, timeout=timeout)
        connection.sock = sock  # 已有 sock 时 http.client 不会再自行解析 DNS 建连
    except BaseException:
        sock.close()
        raise
    return connection


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise FetchError("取数超过总时限")
    return left


class _Budget:
    """总时限看门狗：到点后从另一个线程 shutdown + close 当前 socket，
    让正阻塞在 recv（含读响应头、慢速涓流）的读取立刻返回错误，而不是等下一次读之间的检查。"""

    def __init__(self, timeout: float, total_timeout: float):
        self.timeout = timeout
        self.deadline = time.monotonic() + total_timeout
        self.expired = threading.Event()
        self._connection: Optional[http.client.HTTPConnection] = None
        self._lock = threading.Lock()
        self._timer = threading.Timer(total_timeout, self._kill)
        self._timer.daemon = True

    def start(self) -> None:
        self._timer.start()

    def cancel(self) -> None:
        self._timer.cancel()

    def attach(self, connection) -> None:
        with self._lock:
            self._connection = connection
        if self.expired.is_set():
            self._kill()

    def _kill(self) -> None:
        self.expired.set()
        with self._lock:
            connection = self._connection
        sock = getattr(connection, "sock", None)
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def error(self, fallback: str, cause: Optional[BaseException] = None) -> FetchError:
        return FetchError("下载超时" if self.expired.is_set() else fallback)

    def __call__(self) -> None:
        """每次读取前调用：检查总时限，并把 socket 超时收紧为 min(单次超时, 剩余时间)。"""
        if self.expired.is_set():
            raise FetchError("下载超时")
        connection = self._connection
        sock = getattr(connection, "sock", None)
        left = _remaining(self.deadline)
        if sock is not None:
            sock.settimeout(min(self.timeout, left))


@contextmanager
def _open_response(
    url: str,
    *,
    timeout: float,
    total_timeout: float,
    max_redirects: int,
    allow_private: Optional[bool],
    headers: Optional[dict[str, str]],
) -> Iterator[tuple[http.client.HTTPResponse, str, _Budget]]:
    """逐跳校验并跟随重定向，产出 (response, final_url, budget)；整个过程受看门狗总时限约束。"""
    budget = _Budget(timeout, total_timeout)
    budget.start()
    try:
        with _open_response_inner(
            url,
            budget=budget,
            timeout=timeout,
            total_timeout=total_timeout,
            max_redirects=max_redirects,
            allow_private=allow_private,
            headers=headers,
        ) as result:
            yield result
    finally:
        budget.cancel()


@contextmanager
def _open_response_inner(
    url: str,
    *,
    budget: _Budget,
    timeout: float,
    total_timeout: float,
    max_redirects: int,
    allow_private: Optional[bool],
    headers: Optional[dict[str, str]],
) -> Iterator[tuple[http.client.HTTPResponse, str, _Budget]]:
    deadline = budget.deadline
    current = url
    origin: Optional[tuple[str, str, int]] = None
    for _hop in range(max_redirects + 1):
        scheme, host, port, path = _validate_url(current)
        if origin is None:
            origin = (scheme, host, port)
        ips = resolve_safe_ips(host, port, allow_private=allow_private, deadline=deadline)
        connection = None
        last_error: Optional[Exception] = None
        for ip in ips:
            try:
                connection = _open_connection(scheme, host, port, ip, min(timeout, _remaining(deadline)))
                break
            except OSError as error:
                last_error = error
        if connection is None:
            raise budget.error(f"无法连接: {host}") from last_error
        budget.attach(connection)
        try:
            send_headers = {"Accept-Encoding": "identity", "User-Agent": "FishStudio-safe-fetch"}
            # 自定义头（Authorization / Cookie 等）只在 协议、主机、端口 都不变时才发送，
            # 因此 https→http 降级、换端口、换主机都不会带上凭据
            if headers and (scheme, host, port) == origin:
                send_headers.update(headers)
            connection.request("GET", path, headers=send_headers)
            response = connection.getresponse()
        except (OSError, ValueError, http.client.HTTPException) as error:
            connection.close()
            raise budget.error(f"请求失败: {error}") from error
        if response.status in _REDIRECT_STATUSES:
            location = response.getheader("Location")
            connection.close()
            if not location:
                raise FetchError("重定向缺少 Location")
            current = urljoin(current, location)
            continue

        try:
            yield response, current, budget
        finally:
            connection.close()
        return
    raise FetchError(f"重定向超过 {max_redirects} 次")


def _check_response(
    response: http.client.HTTPResponse, max_bytes: int, allowed_mime: Optional[set[str]]
) -> tuple[str, Optional[int]]:
    """校验状态码 / MIME / 声明长度 → (content_type, 声明的 Content-Length 或 None)。"""
    if not 200 <= response.status < 300:
        raise FetchError(f"HTTP {response.status}")
    content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
    if allowed_mime is not None and content_type and content_type not in allowed_mime:
        raise FetchError(f"MIME 不允许: {content_type}")
    declared_raw = response.getheader("Content-Length")
    declared = int(declared_raw) if declared_raw and declared_raw.isdigit() else None
    if declared is not None and declared > max_bytes:
        raise FetchError(f"内容超过大小限制 {max_bytes} 字节")
    return content_type, declared


def _read_chunks(
    response, arm: Callable[[], None], max_bytes: int, declared: Optional[int]
) -> Iterator[bytes]:
    """逐块读取：每次读之前检查总时限；读完核对 Content-Length（防提前断开被当成功）。"""
    total = 0
    try:
        while True:
            arm()
            chunk = response.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise FetchError(f"内容超过大小限制 {max_bytes} 字节")
            yield chunk
    except (
        OSError,
        ValueError,
        http.client.HTTPException,
    ) as error:  # 含 IncompleteRead、socket 超时、被看门狗关闭
        raise (
            arm.error(f"读取失败: {error}") if isinstance(arm, _Budget) else FetchError(f"读取失败: {error}")
        ) from error
    if isinstance(arm, _Budget) and arm.expired.is_set():
        raise FetchError("下载超时")  # 看门狗关闭 socket 后可能表现为“干净的 EOF”
    if declared is not None and total != declared:
        raise FetchError(f"内容不完整: 声明 {declared} 字节，实际收到 {total} 字节")


def _total_timeout(timeout: float, total_timeout: Optional[float]) -> float:
    return total_timeout if total_timeout is not None else max(timeout, DEFAULT_TOTAL_TIMEOUT)


def fetch(
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = DEFAULT_TIMEOUT,
    total_timeout: Optional[float] = None,
    allowed_mime: Optional[set[str]] = None,
    max_redirects: int = MAX_REDIRECTS,
    allow_private: Optional[bool] = None,
    headers: Optional[dict[str, str]] = None,
) -> FetchResult:
    """GET 一个外部 URL 并把内容读进内存。

    timeout 是单次连接 / 读取超时；total_timeout 是整个过程（DNS、重定向、读完 body）的总时限，
    默认 max(timeout, 120s)。超过 max_bytes 立即中断；声明了 Content-Length 但收到的字节数不符视为失败。
    不安全的 URL 抛 UnsafeURLError；其余失败抛 FetchError。
    """
    with _open_response(
        url,
        timeout=timeout,
        total_timeout=_total_timeout(timeout, total_timeout),
        max_redirects=max_redirects,
        allow_private=allow_private,
        headers=headers,
    ) as (response, final_url, arm):
        content_type, declared = _check_response(response, max_bytes, allowed_mime)
        content = b"".join(_read_chunks(response, arm, max_bytes, declared))
        return FetchResult(content, content_type, final_url, response.status)


def fetch_to_file(
    url: str,
    dest: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = DEFAULT_TIMEOUT,
    total_timeout: Optional[float] = None,
    allowed_mime: Optional[set[str]] = None,
    max_redirects: int = MAX_REDIRECTS,
    allow_private: Optional[bool] = None,
    headers: Optional[dict[str, str]] = None,
) -> FetchResult:
    """同 fetch，但流式写入文件（大视频不占内存）；任何失败（含超限、截断、超时）都删除半成品。"""
    with _open_response(
        url,
        timeout=timeout,
        total_timeout=_total_timeout(timeout, total_timeout),
        max_redirects=max_redirects,
        allow_private=allow_private,
        headers=headers,
    ) as (response, final_url, arm):
        content_type, declared = _check_response(response, max_bytes, allowed_mime)
        try:
            with dest.open("wb") as output:
                for chunk in _read_chunks(response, arm, max_bytes, declared):
                    output.write(chunk)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        return FetchResult(b"", content_type, final_url, response.status)


async def afetch(url: str, **kwargs) -> FetchResult:
    """fetch 的异步封装（放线程里跑，不阻塞事件循环）。"""
    return await asyncio.to_thread(fetch, url, **kwargs)
