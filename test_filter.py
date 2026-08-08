# -*- coding: utf-8 -*-
"""test_filter.py — 过滤代理离线测试(T1 单测 + T2 mock 上游集成), 无需网络。"""
import base64, json, os, sys, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import filter_proxy as fp
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAIL = []

def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "|", name, detail)
    if not cond:
        FAIL.append(name)

def b64(obj):
    return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()

EMPTY_ENC = "ccswitch-anthropic-thinking-v1:" + b64({"type": "thinking", "thinking": "", "signature": "s-empty"})
OK_ENC = "ccswitch-anthropic-thinking-v1:" + b64({"type": "thinking", "thinking": "normal thinking content here", "signature": "s-ok"})

print("== T1 单测 ==")
real = "ccswitch-anthropic-thinking-v1:eyJ0eXBlIjoidGhpbmtpbmciLCJ0aGlua2luZyI6IiIsInNpZ25hdHVyZSI6IjljMzU3NjU5LTEwZDctNDQwNC1iZWMzLThmNTZhNWYyMDRmYyJ9"
check("解码真实空thinking", fp.decode_thinking(real) == "", repr(fp.decode_thinking(real)))
check("解码正常thinking", fp.decode_thinking(OK_ENC) == "normal thinking content here")
check("非ccswitch格式返回None", fp.decode_thinking("other") is None)

items = [
    {"type": "reasoning", "id": "a", "encrypted_content": EMPTY_ENC},
    {"type": "reasoning", "id": "b", "encrypted_content": OK_ENC},
    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]},
    {"type": "reasoning", "id": "c", "encrypted_content": EMPTY_ENC},
]
out, n = fp.filter_items(items, 4, False)
check("filter_items 数量", n == 2 and len(out) == 2)
check("filter_items 保留正常+消息", out[0]["id"] == "b" and out[1]["type"] == "message")
out2, n2 = fp.filter_items(items, 4, True)
check("strip_all 全删 reasoning", n2 == 3 and len(out2) == 1)

san = fp.SSESanitizer(4, False)
evs = [
    {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "msg_1"}},
    {"type": "response.output_item.added", "output_index": 1, "item": {"type": "reasoning", "id": "rs_empty", "encrypted_content": EMPTY_ENC}},
    {"type": "response.output_item.done", "output_index": 1, "item": {"type": "reasoning", "id": "rs_empty", "encrypted_content": EMPTY_ENC}},
    {"type": "response.output_item.added", "output_index": 2, "item": {"type": "reasoning", "id": "rs_ok", "encrypted_content": OK_ENC}},
    {"type": "response.output_item.done", "output_index": 2, "item": {"type": "reasoning", "id": "rs_ok", "encrypted_content": OK_ENC}},
    {"type": "response.output_text.delta", "output_index": 2, "item_id": "rs_ok", "delta": "x"},
    {"type": "response.output_item.done", "output_index": 0, "item": {"type": "message", "id": "msg_1"}},
]
kept = []
for e in evs:
    for nd in san.process(json.dumps(e, ensure_ascii=False)):
        kept.append(json.loads(nd))
check("SSE 丢弃空thinking added+done", not any(k.get("item", {}).get("id") == "rs_empty" for k in kept))
ok_added = [k for k in kept if k.get("type") == "response.output_item.added" and k.get("item", {}).get("id") == "rs_ok"]
check("SSE 正常thinking保留且序号重排", len(ok_added) == 1 and ok_added[0]["output_index"] == 1)
delta = [k for k in kept if k.get("type") == "response.output_text.delta"]
check("SSE 增量序号重排", delta and delta[0]["output_index"] == 1)

san2 = fp.SSESanitizer(4, False)
comp = {"type": "response.completed", "response": {"id": "r", "output": [
    {"type": "message", "id": "m"}, {"type": "reasoning", "id": "x", "encrypted_content": EMPTY_ENC}]}}
emits = san2.process(json.dumps(comp, ensure_ascii=False))
comp2 = json.loads(emits[0])
check("response.completed 过滤输出", len(comp2["response"]["output"]) == 1)

print("== T2 mock 上游集成 ==")
received_bodies = []

class MockUpstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        ln = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(ln)
        received_bodies.append(body)
        req = json.loads(body.decode("utf-8"))
        mode = req.get("test_mode", "sse")
        if mode == "sse":
            events = [
                {"type": "response.created", "response": {"id": "r1"}},
                {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "msg_1", "role": "assistant", "content": []}},
                {"type": "response.output_item.added", "output_index": 1, "item": {"type": "reasoning", "id": "rs_empty", "summary": [], "content": None, "encrypted_content": EMPTY_ENC}},
                {"type": "response.output_item.done", "output_index": 1, "item": {"type": "reasoning", "id": "rs_empty", "summary": [], "content": None, "encrypted_content": EMPTY_ENC}},
                {"type": "response.output_item.added", "output_index": 2, "item": {"type": "reasoning", "id": "rs_ok", "summary": [], "content": None, "encrypted_content": OK_ENC}},
                {"type": "response.output_item.done", "output_index": 2, "item": {"type": "reasoning", "id": "rs_ok", "summary": [], "content": None, "encrypted_content": OK_ENC}},
                {"type": "response.output_text.delta", "output_index": 0, "item_id": "msg_1", "delta": "hi"},
                {"type": "response.output_item.done", "output_index": 0, "item": {"type": "message", "id": "msg_1", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]}},
                {"type": "response.completed", "response": {"id": "r1", "status": "completed", "output": [
                    {"type": "message", "id": "msg_1"},
                    {"type": "reasoning", "id": "rs_empty", "encrypted_content": EMPTY_ENC},
                    {"type": "reasoning", "id": "rs_ok", "encrypted_content": OK_ENC}]}},
            ]
            payload = "".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events) + "data: [DONE]\n\n"
            data = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
        else:
            obj = {"id": "r2", "object": "response", "status": "completed", "output": [
                {"type": "message", "id": "m2", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]},
                {"type": "reasoning", "id": "rs_empty2", "encrypted_content": EMPTY_ENC},
            ]}
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")

mock = ThreadingHTTPServer(("127.0.0.1", 0), MockUpstream)
mock_port = mock.server_address[1]
threading.Thread(target=mock.serve_forever, daemon=True).start()

srv = fp.FilterServer(("127.0.0.1", 0), fp.ProxyHandler, "127.0.0.1", mock_port, 4, False)
fport = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

def send(mode):
    req_body = {
        "model": "deepseek-v4-flash-0731",
        "instructions": "sys",
        "test_mode": mode,
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "ping"}]},
            {"type": "reasoning", "id": "rs_hist_empty", "encrypted_content": EMPTY_ENC},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "pong"}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "go"}]},
        ],
        "stream": mode == "sse",
    }
    c = HTTPConnection("127.0.0.1", fport, timeout=15)
    c.request("POST", "/v1/responses", body=json.dumps(req_body, ensure_ascii=False).encode("utf-8"),
              headers={"Content-Type": "application/json", "Authorization": "Bearer PROXY_MANAGED"})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, r.getheader("Content-Type", ""), data

# 2a. 请求侧: 上游收到的 body 应无空 thinking reasoning, 且保留消息
st, ct, _ = send("sse")
recv = received_bodies[0].decode("utf-8")
check("集成: 上游收到请求且无空thinking", b"rs_hist_empty" not in received_bodies[0])
check("集成: 上游请求仍含消息", '"output_text"' in recv and '"go"' in recv)
check("集成: SSE 响应 200", st == 200 and "text/event-stream" in ct)

# 2b. 响应侧: 客户端流应无 rs_empty, 有 rs_ok(序号重排)
st2, ct2, data2 = send("sse")
text2 = data2.decode("utf-8")
check("集成: 客户端流无空thinking", "rs_empty" not in text2)
check("集成: 客户端流保留正常thinking", "rs_ok" in text2)
for line in text2.splitlines():
    if line.startswith("data:") and "rs_ok" in line and '"added"' in line:
        ev = json.loads(line[5:].strip())
        check("集成: rs_ok 序号重排为1", ev.get("output_index") == 1, "idx=%s" % ev.get("output_index"))
        break
check("集成: completed 输出已过滤", '"rs_empty"' not in text2)

# 2c. JSON 非流式响应过滤
received_bodies.clear()
st3, ct3, data3 = send("json")
t3 = data3.decode("utf-8")
check("集成: JSON 响应 200", st3 == 200)
check("集成: JSON 响应无空thinking", "rs_empty2" not in t3 and "m2" in t3)
check("集成: JSON 请求侧也过滤", received_bodies and b"rs_hist_empty" not in received_bodies[0])

# 2d. 健康检查
c = HTTPConnection("127.0.0.1", fport, timeout=5)
c.request("GET", "/healthz")
r = c.getresponse(); check("健康检查", r.status == 200 and r.read() == b"ok"); c.close()

mock.shutdown(); srv.server_close()
print("\n结果:", "全部通过" if not FAIL else "失败: %s" % FAIL)
sys.exit(1 if FAIL else 0)
