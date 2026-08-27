#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
proxy_handler.py -- Parse PROXY_URL and generate Xray-core config.json

Supported protocols:
  vless://uuid@host:port?security=tls|reality&type=ws&...#name
  vmess://base64EncodedJSON
  trojan://password@host:port?security=tls&sni=xxx&type=ws&...
  ss://method:password@host:port
  socks5://[user:pass@]host:port
  http://[user:pass@]host:port
  https://[user:pass@]host:port

Output: config.json with HTTP inbound on 127.0.0.1:8080
"""

from __future__ import annotations

import base64
import json
import os
import sys
from urllib.parse import parse_qs, unquote, urlparse

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8080
UNSUPPORTED_WITH_XRAY = ("hy2", "hysteria2", "tuic", "anytls")


def qget(params, *keys, default=""):
    lower_map = {str(k).lower(): v for k, v in params.items()}
    for key in keys:
        vals = params.get(key) or lower_map.get(str(key).lower())
        if vals and vals[0] != "":
            return vals[0]
    return default


def is_true(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def normalize_path(path):
    path = unquote(path or "/") or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path


def split_alpn(raw):
    return [x.strip() for x in str(raw).replace(";", ",").split(",") if x.strip()]


def build_stream(params, default_host=""):
    network = (qget(params, "type", "network", default="tcp") or "tcp").lower()
    if network in ("h2",):
        network = "http"
    if network in ("splithttp",):
        network = "xhttp"

    security = (qget(params, "security", default="none") or "none").lower()
    sni = qget(params, "sni", "serverName", "peer", default="")
    host = qget(params, "host", "Host", default="") or sni or default_host
    if not sni:
        sni = host or default_host
    path = normalize_path(qget(params, "path", default="/"))
    fingerprint = qget(params, "fp", "fingerprint", default="") or "chrome"
    alpn_raw = qget(params, "alpn", default="")
    allow_insecure = is_true(qget(params, "allowInsecure", "insecure", default="0"))
    if "proxyip=" in path and str(fingerprint).lower() == "random":
        print("CF worker-style path + fp=random -> using chrome fingerprint")
        fingerprint = "chrome"

    stream = {
        "network": network,
        "security": security if security in ("tls", "reality") else "none",
    }

    if network == "ws":
        ws = {"path": path}
        if host:
            ws["headers"] = {"Host": host}
        stream["wsSettings"] = ws
    elif network == "grpc":
        service = qget(params, "serviceName", "service_name", default="") or path.strip("/")
        stream["grpcSettings"] = {"serviceName": service, "multiMode": False}
    elif network == "http":
        http_settings = {"path": path}
        if host:
            http_settings["host"] = [host]
        stream["httpSettings"] = http_settings
    elif network == "httpupgrade":
        upgrade = {"path": path}
        if host:
            upgrade["host"] = host
        stream["httpupgradeSettings"] = upgrade
    elif network == "xhttp":
        xhttp = {
            "path": path,
            "mode": qget(params, "mode", default="auto") or "auto",
        }
        if host:
            xhttp["host"] = host
        stream["xhttpSettings"] = xhttp
    elif network == "tcp":
        header_type = qget(params, "headerType", default="")
        if header_type.lower() == "http":
            stream["tcpSettings"] = {
                "header": {
                    "type": "http",
                    "request": {
                        "path": [path],
                        "headers": {"Host": [host] if host else []},
                    },
                }
            }

    if security == "tls":
        tls = {
            "serverName": sni,
            "allowInsecure": allow_insecure,
            "fingerprint": fingerprint,
        }
        if alpn_raw:
            tls["alpn"] = split_alpn(alpn_raw)
        elif network == "ws":
            # WS upgrade must stay on HTTP/1.1; negotiating h2 often causes RST.
            tls["alpn"] = ["http/1.1"]
        elif network == "grpc":
            tls["alpn"] = ["h2"]
        stream["tlsSettings"] = tls
    elif security == "reality":
        public_key = qget(params, "pbk", "publicKey", default="")
        if not public_key:
            raise SystemExit("REALITY node is missing pbk/publicKey")
        stream["realitySettings"] = {
            "serverName": sni,
            "fingerprint": fingerprint,
            "publicKey": public_key,
            "shortId": qget(params, "sid", "shortId", default=""),
            "spiderX": qget(params, "spx", "spiderX", default="/") or "/",
        }

    meta = {
        "network": network,
        "security": stream["security"],
        "sni": sni,
        "host": host,
        "path": path,
        "fingerprint": fingerprint,
        "allow_insecure": allow_insecure,
    }
    return stream, meta


def parse_vless(parsed, params):
    user = {
        "id": unquote(parsed.username or ""),
        "encryption": qget(params, "encryption", default="none") or "none",
    }
    flow = qget(params, "flow", default="")
    if flow:
        user["flow"] = flow

    stream, meta = build_stream(params, default_host=parsed.hostname or "")
    outbound = {
        "tag": "proxy",
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": parsed.hostname,
                    "port": parsed.port or 443,
                    "users": [user],
                }
            ]
        },
        "streamSettings": stream,
    }
    return outbound, meta


def parse_trojan(parsed, params):
    stream, meta = build_stream(params, default_host=parsed.hostname or "")
    if stream.get("security") == "none":
        stream["security"] = "tls"
        sni = meta.get("sni") or parsed.hostname
        stream["tlsSettings"] = {
            "serverName": sni,
            "allowInsecure": meta.get("allow_insecure", False),
            "fingerprint": meta.get("fingerprint") or "chrome",
        }
        meta["security"] = "tls"
        meta["sni"] = sni

    password = unquote(parsed.username or "")
    if parsed.password:
        password = f"{password}:{unquote(parsed.password)}"

    outbound = {
        "tag": "proxy",
        "protocol": "trojan",
        "settings": {
            "servers": [
                {
                    "address": parsed.hostname,
                    "port": parsed.port or 443,
                    "password": password,
                }
            ]
        },
        "streamSettings": stream,
    }
    return outbound, meta


def parse_socks(parsed):
    server = {
        "address": parsed.hostname,
        "port": parsed.port or 1080,
    }
    if parsed.username:
        server["users"] = [
            {
                "user": unquote(parsed.username),
                "pass": unquote(parsed.password or ""),
            }
        ]
    outbound = {
        "tag": "proxy",
        "protocol": "socks",
        "settings": {"servers": [server]},
    }
    meta = {
        "network": "tcp",
        "security": "none",
        "sni": "",
        "host": parsed.hostname,
        "path": "",
        "fingerprint": "",
    }
    return outbound, meta


def parse_http(parsed, scheme):
    server = {
        "address": parsed.hostname,
        "port": parsed.port or (443 if scheme == "https" else 8080),
    }
    if parsed.username:
        server["users"] = [
            {
                "user": unquote(parsed.username),
                "pass": unquote(parsed.password or ""),
            }
        ]
    outbound = {
        "tag": "proxy",
        "protocol": "http",
        "settings": {"servers": [server]},
    }
    if scheme == "https":
        outbound["streamSettings"] = {
            "network": "tcp",
            "security": "tls",
            "tlsSettings": {"serverName": parsed.hostname},
        }
    meta = {
        "network": "tcp",
        "security": "tls" if scheme == "https" else "none",
        "sni": parsed.hostname if scheme == "https" else "",
        "host": parsed.hostname,
        "path": "",
        "fingerprint": "",
    }
    return outbound, meta


def parse_vmess(url_str):
    encoded = url_str[len("vmess://") :]
    if "#" in encoded:
        encoded = encoded.split("#", 1)[0]
    encoded = unquote(encoded)
    pad = 4 - len(encoded) % 4
    if pad != 4:
        encoded += "=" * pad
    cfg = json.loads(base64.b64decode(encoded).decode("utf-8"))

    network = (cfg.get("net") or "tcp").lower()
    if network in ("h2",):
        network = "http"
    security = (cfg.get("tls") or "none").lower()
    sni = cfg.get("sni") or cfg.get("host") or cfg.get("add") or ""
    host = cfg.get("host") or sni
    path = normalize_path(cfg.get("path") or "/")
    fingerprint = cfg.get("fp") or "chrome"
    alpn_raw = cfg.get("alpn") or ""

    params = {
        "type": [network],
        "security": [security if security else "none"],
        "sni": [sni],
        "host": [host],
        "path": [path],
        "fp": [fingerprint],
        "alpn": [alpn_raw],
        "serviceName": [cfg.get("path") or ""],
        "headerType": [cfg.get("type") or ""],
        "allowInsecure": [str(cfg.get("allowInsecure") or cfg.get("insecure") or "0")],
    }
    stream, meta = build_stream(params, default_host=cfg.get("add") or "")

    outbound = {
        "tag": "proxy",
        "protocol": "vmess",
        "settings": {
            "vnext": [
                {
                    "address": cfg.get("add", ""),
                    "port": int(cfg.get("port", 443)),
                    "users": [
                        {
                            "id": cfg.get("id", ""),
                            "alterId": int(cfg.get("aid", 0)),
                            "security": cfg.get("scy") or "auto",
                        }
                    ],
                }
            ]
        },
        "streamSettings": stream,
    }
    return outbound, meta


def parse_ss(url_str):
    raw = url_str[len("ss://") :]
    if "#" in raw:
        raw = raw.split("#", 1)[0]
    query = ""
    if "?" in raw:
        raw, query = raw.split("?", 1)
    params = parse_qs(query)

    def b64decode_text(value):
        pad = 4 - len(value) % 4
        if pad != 4:
            value += "=" * pad
        return base64.urlsafe_b64decode(value.encode("utf-8")).decode("utf-8")

    if "@" in raw:
        userinfo, serverpart = raw.split("@", 1)
        decoded = None
        try:
            decoded = b64decode_text(userinfo)
        except Exception:
            decoded = None
        if decoded and ":" in decoded:
            method, password = decoded.split(":", 1)
        else:
            method, password = unquote(userinfo).split(":", 1)
        host, port_s = serverpart.rsplit(":", 1)
        host = unquote(host)
        port = int(port_s)
    else:
        decoded = b64decode_text(raw)
        userinfo, serverpart = decoded.split("@", 1)
        method, password = userinfo.split(":", 1)
        host, port_s = serverpart.rsplit(":", 1)
        port = int(port_s)

    plugin = qget(params, "plugin", default="")
    if plugin:
        raise SystemExit(f"Xray shadowsocks plugin is not supported: {plugin}")

    outbound = {
        "tag": "proxy",
        "protocol": "shadowsocks",
        "settings": {
            "servers": [
                {
                    "address": host,
                    "port": port,
                    "method": method,
                    "password": password,
                }
            ]
        },
    }
    meta = {
        "network": "tcp",
        "security": "none",
        "sni": "",
        "host": host,
        "path": "",
        "fingerprint": "",
    }
    return outbound, meta


def summarize(outbound, meta):
    settings = outbound.get("settings") or {}
    server = "N/A"
    port = "N/A"
    if settings.get("vnext"):
        server = settings["vnext"][0].get("address", "N/A")
        port = settings["vnext"][0].get("port", "N/A")
    elif settings.get("servers"):
        server = settings["servers"][0].get("address", "N/A")
        port = settings["servers"][0].get("port", "N/A")
    return server, port


def main():
    proxy_url = os.environ.get("PROXY_URL", "").strip().strip('"').strip("'")
    if not proxy_url:
        print("PROXY_URL is empty, skipping Xray config generation.")
        sys.exit(0)

    # Allow pasting a multi-line secret; use the first URI.
    for line in proxy_url.splitlines():
        line = line.strip()
        if "://" in line:
            proxy_url = line
            break

    scheme = proxy_url.split("://", 1)[0].lower()
    print(f"Parsing proxy URI ({scheme}://***)")

    if scheme in UNSUPPORTED_WITH_XRAY:
        print(
            f"Xray-core v25.3.6 不支持 {scheme}。请改用 vless / vmess / trojan / socks5 / http 节点。"
        )
        sys.exit(1)

    if scheme == "vmess":
        outbound, meta = parse_vmess(proxy_url)
    elif scheme == "ss":
        outbound, meta = parse_ss(proxy_url)
    else:
        parsed = urlparse(proxy_url)
        params = parse_qs(parsed.query)
        if not parsed.hostname:
            print("PROXY_URL 无法解析主机名，请检查链接是否完整。")
            sys.exit(1)
        if scheme == "vless":
            outbound, meta = parse_vless(parsed, params)
        elif scheme == "trojan":
            outbound, meta = parse_trojan(parsed, params)
        elif scheme in ("socks", "socks5"):
            outbound, meta = parse_socks(parsed)
        elif scheme in ("http", "https"):
            outbound, meta = parse_http(parsed, scheme)
        else:
            print(f"Unsupported protocol: {scheme}")
            sys.exit(1)

    config = {
        "log": {"loglevel": "info"},
        "inbounds": [
            {
                "tag": "http-in",
                "listen": LISTEN_HOST,
                "port": LISTEN_PORT,
                "protocol": "http",
                "settings": {"allowTransparent": False},
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": True,
                },
            }
        ],
        "outbounds": [
            outbound,
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
    }

    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    server, port = summarize(outbound, meta)
    print("Xray config.json generated.")
    print(f"  Inbound:  http://{LISTEN_HOST}:{LISTEN_PORT}")
    print(f"  Outbound: {outbound['protocol']} -> {server}:{port}")
    print(
        "  Transport: "
        f"{meta.get('network')} + {meta.get('security')}  "
        f"sni={meta.get('sni') or '-'}  host={meta.get('host') or '-'}"
    )
    if meta.get("path"):
        print(f"  Path: {meta['path']}")
        if "proxyip=" in str(meta["path"]):
            print("  Note: CF worker-style path detected.")
    if meta.get("fingerprint"):
        print(f"  Fingerprint: {meta['fingerprint']}")
        if str(meta["fingerprint"]).lower() == "random":
            print("  Hint: 若 TLS 握手失败，把节点 fp=random 改成 fp=chrome 后再试。")


if __name__ == "__main__":
    main()
