#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
filter_proxy.py — 透明过滤代理: Codex(15722) -> 本代理 -> CC Switch 本地代理(15721) -> 上游

解决: CC Switch 把上游返回的"空/超短 thinking"写进 Codex 会话历史, 下次请求回传时
上游网关校验 thinking 长度不足而报 HTTP 400 (messages.N.content.0.thinking 长度不足)。

做法(实时、无需重启对话):
  * 请求侧: /v1/responses 的 input 数组里, 丢弃 ccswitch-anthropic-thinking-v1 格式
            且 thinking 长度过短的 reasoning 项(兜底: 历史里已有的毒记录每次都被剥掉);
  * 响应侧: SSE/JSON 输出里同样的 reasoning 项直接丢弃(空 thinking 永远不会进入 Codex)。

仅依赖 Python 标准库。用法:
  python filter_proxy.py [--listen 127.0.0.1:15722] [--upstream 127.0.0.1:15721]
                         [--min-len 4] [--strip-all] [--log filter_proxy.log]
"""
import argparse
import base64
import json
import logging
import sys
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREFIX = "ccswitch-anthropic-thinking-v1:"


# ---------------- 工具: 识别"坏" reasoning ----------------

def decode_thinking(encrypted_content):
    """解码 ccswitch thinking 块; 非 ccswitch 格式返回 None。"""
    if not encrypted_content or not encrypted_content.startswith(PREFIX):
        return None
    b64 = encrypted_content[len(PREFIX):]
    b64 += "=" * (-len(b64) % 4)
    try:
        obj = json.loads(base64.b64decode(b64).decode("utf-8", "replace"))
        if isinstance(obj, dict):
            return obj.get("thinking")
    except Exception:
        pass
    return None


def is_bad_reasoning(item, min_len, strip_all):
    if not isinstance(item, dict) or item.get("type") != "reasoning":
        return False
    enc = item.get("encrypted_content") or ""
    if not enc.startswith(PREFIX):
        return False
    if strip_all:
        return True
    th = decode_thinking(enc)
    if not isinstance(th, str):
        return False
    return len(th.strip()) < min_len


def filter_items(items, min_len, strip_all):
    """过滤 reasoning 列表, 返回 (新列表, 删除数)。递归处理 message content 内嵌项。"""
    if not isinstance(items, list):
        return items, 0
    out = []
    removed = 0
    for it in items:
        if is_bad_reasoning(it, min_len, strip_all):
            removed += 1
            continue
        if isinstance(it, dict) and it.get("type") == "message" and isinstance(it.get("content"), list):
            new_content, n = filter_items(it["content"], min_len, strip_all)
            if n:
                it["content"] = new_content
                removed += n
        out.append(it)
    return out, removed


# ---------------- SSE 事件流过滤 ----------------

class SSESanitizer:
    def __init__(self, min_len, strip_all):
        self.min_len = min_len
        self.strip_all = strip_all
        self.dropped_indexes = []   # 已丢弃项的 output_index(升序)
        self.dropped_ids = set()    # 已丢弃项的 item id

    def _adjust(self, idx):
        return idx - sum(1 for d in self.dropped_indexes if d < idx)

    def process(self, payload):
        """data: 后的 JSON 文本(不含 [DONE]); 返回要下发的 data 行列表(可为空)。"""
        try:
            ev = json.loads(payload)
        except Exception:
            return [payload]
        if not isinstance(ev, dict):
            return [payload]

        etype = ev.get("type")
        idx = ev.get("output_index")

        if etype == "response.output_item.added":
            item = ev.get("item") or {}
            if is_bad_reasoning(item, self.min_len, self.strip_all):
                if isinstance(idx, int):
                    self.dropped_indexes.append(idx)
                    self.dropped_indexes.sort()
                iid = item.get("id")
                if iid:
                    self.dropped_ids.add(iid)
                logging.info("[SSE] 丢弃空 thinking reasoning: id=%s idx=%s", iid, idx)
                return []
            if isinstance(idx, int):
                ev["output_index"] = self._adjust(idx)
            return [json.dumps(ev, ensure_ascii=False)]

        if etype == "response.output_item.done":
            item = ev.get("item") or {}
            iid = item.get("id")
            if isinstance(idx, int) and idx in self.dropped_indexes:
                return []
            if iid and iid in self.dropped_ids:
                return []
            if isinstance(idx, int):
                ev["output_index"] = self._adjust(idx)
            return [json.dumps(ev, ensure_ascii=False)]

        if etype == "response.completed":
            resp = ev.get("response")
            if isinstance(resp, dict) and isinstance(resp.get("output"), list):
                new_output, removed = filter_items(resp["output"], self.min_len, self.strip_all)
                if removed:
                    resp["output"] = new_output
                    logging.info("[SSE] response.completed 内移除 %d 条空 thinking", removed)
                    return [json.dumps(ev, ensure_ascii=False)]
            return [payload]

        # 其它带 output_index 的事件(文本增量/工具参数等)
        if isinstance(idx, int):
            if idx in self.dropped_indexes:
                return []
            ev["output_index"] = self._adjust(idx)
            return [json.dumps(ev, ensure_ascii=False)]

        return [payload]


# ---------------- HTTP 处理 ----------------

class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默默认日志
        pass

    def _send_start(self, status, resp_headers, body_len=None):
        self.send_response(status)
        for k, v in resp_headers:
            lk = k.lower()
            if lk in ("transfer-encoding", "connection", "content-length"):
                continue
            self.send_header(k, v)
        if body_len is not None:
            self.send_header("Content-Length", str(body_len))
        self.send_header("Connection", "close")
        self.end_headers()

    def _dispatch(self, method):
        if method == "GET" and self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        # ---- 请求侧过滤 ----
        dropped_req = 0
        if method == "POST" and self.path.rstrip("/").endswith("/responses") and body:
            try:
                obj = json.loads(body.decode("utf-8"))
                if isinstance(obj, dict) and isinstance(obj.get("input"), list):
                    new_input, n = filter_items(obj["input"], self.server.min_len, self.server.strip_all)
                    if n:
                        obj["input"] = new_input
                        dropped_req = n
                        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                        logging.info("[REQ] 请求历史中移除 %d 条空 thinking reasoning", n)
            except Exception as e:
                logging.warning("请求体解析失败, 原样转发: %s", e)

        # ---- 转发上游(CC Switch 15721) ----
        conn = HTTPConnection(self.server.upstream_host, self.server.upstream_port,
                              timeout=self.server.timeout)
        headers = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in ("host", "content-length", "connection", "accept-encoding", "transfer-encoding"):
                continue
            headers[k] = v
        headers["Content-Length"] = str(len(body))
        try:
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except Exception as e:
            conn.close()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            msg = ("filter_proxy 无法连接上游 CC Switch %s:%s: %s"
                   % (self.server.upstream_host, self.server.upstream_port, e))
            data = msg.encode("utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            logging.error("上游连接失败: %s", e)
            return

        status = resp.status
        resp_headers = resp.getheaders()
        ctype = resp.getheader("Content-Type", "")

        if status != 200:
            data = resp.read()
            conn.close()
            self._send_start(status, resp_headers, len(data))
            if data:
                self.wfile.write(data)
            logging.info("%s %s -> %s (%d bytes), 请求侧过滤 %d 条",
                         method, self.path, status, len(data), dropped_req)
            return

        if "text/event-stream" in ctype.lower():
            self._send_start(status, resp_headers, None)
            sanitizer = SSESanitizer(self.server.min_len, self.server.strip_all)
            dropped_sse = 0
            try:
                for line in resp:
                    text = line.decode("utf-8", "replace")
                    if text.startswith("data:"):
                        payload = text[5:].lstrip()
                        stripped = payload.strip()
                        if stripped and stripped != "[DONE]":
                            emits = sanitizer.process(stripped)
                            if not emits:
                                dropped_sse += 1
                                continue
                            for nd in emits:
                                out = "data: " + nd
                                out += "\r\n" if line.endswith(b"\r\n") else "\n"
                                self.wfile.write(out.encode("utf-8"))
                        else:
                            self.wfile.write(line)
                    else:
                        self.wfile.write(line)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                conn.close()
            logging.info("%s %s SSE 完成: 响应侧丢弃 %d 条事件, 请求侧过滤 %d 条",
                         method, self.path, dropped_sse, dropped_req)
        else:
            data = resp.read()
            conn.close()
            if "json" in ctype.lower() and data:
                try:
                    obj = json.loads(data.decode("utf-8"))
                    if isinstance(obj, dict) and isinstance(obj.get("output"), list):
                        new_output, n = filter_items(obj["output"], self.server.min_len,
                                                     self.server.strip_all)
                        if n:
                            obj["output"] = new_output
                            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                            logging.info("[JSON] 响应中移除 %d 条空 thinking", n)
                except Exception:
                    pass
            self._send_start(status, resp_headers, len(data))
            if data:
                self.wfile.write(data)
            logging.info("%s %s -> %s (%d bytes), 请求侧过滤 %d 条",
                         method, self.path, status, len(data), dropped_req)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = \
        lambda self: self._dispatch(self.command)


class FilterServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, upstream_host, upstream_port, min_len, strip_all,
                 timeout=300):
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.min_len = min_len
        self.strip_all = strip_all
        self.timeout = timeout
        super().__init__(addr, handler)


def parse_listen(s):
    host, _, port = s.rpartition(":")
    return host or "127.0.0.1", int(port)


def main():
    ap = argparse.ArgumentParser(description="Codex -> CC Switch 空 thinking 过滤代理")
    ap.add_argument("--listen", default="127.0.0.1:15722")
    ap.add_argument("--upstream", default="127.0.0.1:15721")
    ap.add_argument("--min-len", type=int, default=4, help="thinking 最小长度阈值")
    ap.add_argument("--strip-all", action="store_true", help="丢弃所有 ccswitch thinking(不只看长短)")
    ap.add_argument("--log", default="filter_proxy.log")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(args.log, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )

    host, port = parse_listen(args.listen)
    uhost, uport = parse_listen(args.upstream)
    srv = FilterServer((host, port), ProxyHandler, uhost, uport, args.min_len,
                       args.strip_all, timeout=args.timeout)
    logging.info("过滤代理已启动: 监听 %s:%s, 上游 %s:%s, min_len=%d, strip_all=%s",
                 host, port, uhost, uport, args.min_len, args.strip_all)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
