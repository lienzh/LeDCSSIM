# -*- coding: utf-8 -*-
"""
赋值脚本运行时

DSL 语法 (一行一对赋值):
    DPU3013.AI010502 = DPU3013.AQ010101    # 反馈 = 指令
    DPU3013.AI010503 = 50                  # 写常数
    # 注释支持

短码格式:
    DPU3013.AI010502  ↔  ns=0;s=DPU3013.HW.AI010502.PV
    DPU3013.AQ010101  ↔  ns=0;s=DPU3013.HW.AQ010101.PV
    用户也可以直接写完整 ns=0;s=... 节点

执行语义:
    - 读所有"右边引用的点" → 取值 → 写到"左边目标点"
    - 左边是 AI 点 (HW.AI....) → 自动走 HR/LR 双写(NTVDPU 硬约束)
    - 左边是 DI 点 → 普通 write_value (NTVDPU 实际不接受,日志告警)
    - 周期默认 200ms
"""
import asyncio
import logging
import re
import socket
import struct
import threading
import time
from collections import deque
from pathlib import Path
from typing import List, Tuple, Optional, Union
from urllib.parse import urlparse

from src.opc_client.client import OPCClient
from src.models.dsl_registry import MODEL_FACTORIES, get_factory_params
from src.models.steam import steam_T_from_ph
from src import project as proj


# ---------- 显式事件日志 (用户视角时间线: 启停/重连/镜像/清状态等) ----------
_EVENT_BUF = deque(maxlen=200)

def log_event(category: str, msg: str, detail: Optional[dict] = None) -> None:
    """记一条显式事件 (用户视角时间线, 不同于 _LOG_BUFFER 抓 logging 噪声).

    category: run / stop / opc / opc-err / snapshot / state / save / endpoint / error
    msg:      一句话, 带 emoji 前缀给 UI 用色
    detail:   可选字典, 给 expand 时看
    """
    _EVENT_BUF.append({
        "ts": time.time(),
        "category": category,
        "msg": msg,
        "detail": detail or {},
    })


def get_events() -> list:
    """返回最近事件 (新→旧, 跟 deque 顺序相反, UI 显示从近到远)"""
    return list(reversed(_EVENT_BUF))


# ---------- 会话日志环形缓冲 (debug 用, 不影响 stderr 输出) ----------
_LOG_BUFFER = deque(maxlen=300)

class _BufferHandler(logging.Handler):
    def emit(self, record):
        try:
            _LOG_BUFFER.append({
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "msg": self.format(record),
            })
        except Exception:
            self.handleError(record)

_buf_handler = _BufferHandler(level=logging.INFO)
_buf_handler.setFormatter(logging.Formatter("%(message)s"))
# 挂到根 logger, 抓所有模块 (asyncua / src.* / 我们自己)
_root = logging.getLogger()
if _buf_handler not in _root.handlers:
    _root.addHandler(_buf_handler)
# 同时降低 asyncua noise 到 WARNING (避免一周期 100 条 INFO)
logging.getLogger("asyncua").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# OPC 端点配置 — 由当前工程的 opc_endpoints.yaml 驱动 (mode=local|vm)
# 顶栏切换按钮调 set_endpoint_mode() 更新 + 持久化
def _endpoint_path() -> Path:
    """当前工程的 opc_endpoints.yaml (跟着 projects/<active>/ 走)"""
    return proj.paths().endpoints

_ENDPOINT_DEFAULT = {
    "mode": "local",
    "local": "opc.tcp://127.0.0.1:9440",
    "vm":    "opc.tcp://192.168.31.39:9440",
}


def _load_endpoint_config() -> dict:
    """读 opc_endpoints.yaml, 缺字段就拿默认补齐, 文件不存在写入默认"""
    import yaml as _yaml
    cfg = dict(_ENDPOINT_DEFAULT)
    if _endpoint_path().exists():
        try:
            doc = _yaml.safe_load(_endpoint_path().read_text(encoding="utf-8")) or {}
            for k in ("mode", "local", "vm"):
                if isinstance(doc.get(k), str) and doc[k].strip():
                    cfg[k] = doc[k].strip()
        except Exception as e:
            logger.warning(f"读取 {_endpoint_path()} 失败, 用默认: {e}")
    else:
        try:
            _endpoint_path().parent.mkdir(parents=True, exist_ok=True)
            _endpoint_path().write_text(
                "# OPC 端点选择 — viewer 顶栏 [本地] [VM] 切换写这里\n"
                "mode: local\n"
                f"local: {cfg['local']}\n"
                f"vm:    {cfg['vm']}\n",
                encoding="utf-8")
        except Exception as e:
            logger.warning(f"写入 {_endpoint_path()} 失败: {e}")
    if cfg["mode"] not in ("local", "vm"):
        cfg["mode"] = "local"
    return cfg


def get_endpoint_config() -> dict:
    """返回 {mode, local, vm, url} — url 是当前 mode 对应的 URL"""
    cfg = _load_endpoint_config()
    cfg["url"] = cfg[cfg["mode"]]
    return cfg


def set_endpoint_mode(mode: str, vm_url: Optional[str] = None) -> dict:
    """切模式 + 持久化. 返回新的 config (含 url 字段)"""
    import yaml as _yaml
    if mode not in ("local", "vm"):
        raise ValueError(f"mode 必须是 local | vm, 收到 {mode!r}")
    cfg = _load_endpoint_config()
    cfg["mode"] = mode
    if vm_url and vm_url.strip():
        cfg["vm"] = vm_url.strip()
    try:
        _endpoint_path().write_text(
            "# OPC 端点选择 — viewer 顶栏 [本地] [VM] 切换写这里\n"
            f"mode: {cfg['mode']}\n"
            f"local: {cfg['local']}\n"
            f"vm:    {cfg['vm']}\n",
            encoding="utf-8")
    except Exception as e:
        logger.warning(f"持久化 {_endpoint_path()} 失败: {e}")
    cfg["url"] = cfg[cfg["mode"]]
    log_event("endpoint", f"🔌 OPC 端点切到 [{'VM' if mode == 'vm' else '本地'}] {cfg['url']}",
              {"mode": mode, "url": cfg["url"]})
    # 立即探活新端点, UI 不用等下一轮 5s 轮询
    try: _probe_once_and_store()
    except Exception: pass
    return cfg


# 默认 OPC URL(start() 不传 opc_url 时, 读 endpoint 配置)
DEFAULT_OPC_URL = "opc.tcp://127.0.0.1:9440"   # 仅作 import-time 占位; 真正用的是 get_endpoint_config()


# ---------- OPC 探活 (TCP 级, 不开 OPC session) ----------
# 后台 5s 自动探一次, UI 可直接读 _PROBE_RESULT 拿最近结果, 不阻塞前端
_PROBE_RESULT = {
    "url": None, "mode": None,
    "ok": None,           # None=尚未探测 / True=通 / False=不通
    "latency_ms": None,
    "error": None,
    "ts": 0.0,            # 上次探测时间戳
}
_PROBE_LOCK = threading.Lock()
_PROBE_THREAD = None
_PROBE_INTERVAL = 5.0     # 后台探活间隔, 秒


def probe_endpoint(url: str, timeout: float = 2.0) -> dict:
    """OPC UA HELLO/ACK 协议级探活.

    为什么不用纯 TCP: Tailscale / Meta(198.18/15) 等代理接口会劫持任意
    IP 的 TCP connect, 让 socket 三次握手成功但其实没真到目标. 必须看
    应用层 (OPC HELLO → ACK) 才能确认对端真是 NTVDPU.

    返回 {ok, latency_ms, error, host, port}
    - ok=True:  端口可达 + 返回 OPC UA ACK 报文 (确认是 OPC Server)
    - ok=False: TCP 拒绝/超时, 或 TCP 通但无 OPC 响应 (被代理拦)
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9440
    except Exception as e:
        return {"ok": False, "error": f"URL 解析失败: {e}",
                "latency_ms": None, "host": None, "port": None}

    # 构造 OPC UA TCP HELLO 报文 (OPC UA Spec Part 6, sec 7.1.2.3)
    #   Header(8B): "HELF" + UInt32 totalSize
    #   Body: ProtocolVer(UInt32=0) + RecvBuf + SendBuf + MaxMsg(=0) + MaxChunk(=0)
    #         + EndpointUrl(Int32 len + UTF-8 字节)
    url_bytes = url.encode("utf-8")
    body = struct.pack("<IIIII", 0, 65536, 65536, 0, 0) \
           + struct.pack("<i", len(url_bytes)) + url_bytes
    total_size = 8 + len(body)
    hello_msg = b"HELF" + struct.pack("<I", total_size) + body

    t0 = time.time()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.sendall(hello_msg)
        # 读应答头 (8 字节: 3 字节 MsgType + 1 字节 ChunkType + UInt32 size)
        hdr = b""
        while len(hdr) < 8:
            chunk = sock.recv(8 - len(hdr))
            if not chunk:
                return {"ok": False,
                        "error": "TCP 通但服务端无 OPC 应答 (可能被代理劫持)",
                        "latency_ms": None, "host": host, "port": port}
            hdr += chunk
        msgtype = hdr[:3]
        latency = round((time.time() - t0) * 1000, 1)
        if msgtype == b"ACK":
            return {"ok": True, "error": None, "latency_ms": latency,
                    "host": host, "port": port}
        elif msgtype == b"ERR":
            return {"ok": False,
                    "error": "OPC 服务端拒绝 (ERR 响应, 协议版本或缓冲不匹配)",
                    "latency_ms": latency, "host": host, "port": port}
        else:
            return {"ok": False,
                    "error": f"非 OPC 协议应答 (got {msgtype!r})",
                    "latency_ms": latency, "host": host, "port": port}
    except socket.timeout:
        return {"ok": False,
                "error": f"超时 (>{timeout:.1f}s) — TCP 通但未返回 OPC ACK, 通常是代理劫持",
                "latency_ms": None, "host": host, "port": port}
    except ConnectionRefusedError:
        return {"ok": False, "error": "连接被拒 (端口未监听 / NTVDPU 未启)",
                "latency_ms": None, "host": host, "port": port}
    except OSError as e:
        return {"ok": False, "error": str(e),
                "latency_ms": None, "host": host, "port": port}
    finally:
        try: sock.close()
        except Exception: pass


def get_probe_status() -> dict:
    """读最近一次探活结果, 给 UI 用. 不阻塞."""
    with _PROBE_LOCK:
        return dict(_PROBE_RESULT)


def _probe_once_and_store() -> dict:
    """探活当前 endpoint, 把结果写入 _PROBE_RESULT, 返回结果."""
    cfg = get_endpoint_config()
    res = probe_endpoint(cfg["url"])
    with _PROBE_LOCK:
        _PROBE_RESULT.update({
            "url": cfg["url"],
            "mode": cfg["mode"],
            "ok": res["ok"],
            "latency_ms": res["latency_ms"],
            "error": res["error"],
            "ts": time.time(),
        })
    return dict(_PROBE_RESULT)


def _probe_loop():
    """后台探活线程 — 每 _PROBE_INTERVAL 秒探一次. 运行中也照探(很快, 无负担)."""
    while True:
        try:
            _probe_once_and_store()
        except Exception as e:
            logger.warning(f"探活异常: {e}")
        time.sleep(_PROBE_INTERVAL)


def start_probe_thread() -> None:
    """启动后台探活. 重复调用安全 (只起一次). viewer 启动时调一次即可."""
    global _PROBE_THREAD
    if _PROBE_THREAD is not None and _PROBE_THREAD.is_alive():
        return
    _PROBE_THREAD = threading.Thread(target=_probe_loop, daemon=True,
                                     name="opc-probe")
    _PROBE_THREAD.start()
    # 立即先探一次, UI 第一帧就有数据
    try: _probe_once_and_store()
    except Exception: pass

# 短码正则:  DPU + 4位数字 + . + 字母数字组合
SHORTCODE_RE = re.compile(r"^DPU\d{4}\.[A-Z][\w.]*$")   # 支持多段: DPU3013.SH0500.PRO21120.IN
VAR_RE = re.compile(r"^\$[A-Za-z_][\w]*$")              # 中间变量: $tmp / $A_total_flow
# 在表达式中识别短码引用
SHORTCODE_IN_EXPR = re.compile(r"\bDPU\d{4}\.[A-Z]+\w*\b")
# 完整节点 ID
FULL_NODE_RE = re.compile(r"^ns=\d+;s=.+$")


def short_to_full(s: str) -> str:
    """短码 → 完整 OPC 节点 / 中间变量
       DPU3013.AI010502             → ns=0;s=DPU3013.HW.AI010502.PV       (HW 单段)
       DPU3013.SH0500.PRO21120.IN   → ns=0;s=DPU3013.SH0500.PRO21120.IN   (SH 多段)
       $tmp / $A_flow               → $tmp / $A_flow                      (中间变量, 不写 OPC)
    """
    if FULL_NODE_RE.match(s):
        return s
    # 中间变量
    if s.startswith("$"):
        if not VAR_RE.match(s):
            raise ValueError(f"无效的中间变量名 {s!r} "
                             f"(应为 $ + 字母/下划线开头, 如 $tmp1)")
        return s
    if not SHORTCODE_RE.match(s):
        raise ValueError(f"无效的 OPC 引用: {s!r} "
                         f"(期望 DPU3013.AI010502 / DPU3013.SH0500.PRO21120.IN / $中间变量)")
    dpu, rest = s.split(".", 1)
    if "." in rest:
        return f"ns=0;s={s}"
    return f"ns=0;s={dpu}.HW.{rest}.PV"


def is_intermediate(s: str) -> bool:
    """是否中间变量(不参与 OPC 读写)"""
    return isinstance(s, str) and s.startswith("$")


def _summarize_pairs(pairs) -> dict:
    """统计 LHS 分类 + 函数使用 (debug 用)"""
    from collections import Counter
    lhs_type = Counter()
    fn_use = Counter()
    for lhs, rhs in pairs:
        if isinstance(lhs, str):
            if lhs.startswith("$"): lhs_type["$中间变量"] += 1
            elif ".HW." in lhs:
                m = re.search(r"\.HW\.([A-Z]+)\d+\.PV", lhs)
                lhs_type[m.group(1) if m else "HW其他"] += 1
            elif ".SH" in lhs: lhs_type["SH段"] += 1
            else: lhs_type["其他"] += 1
        # 递归数函数
        def walk(node):
            if isinstance(node, tuple):
                fn_use[node[0]] += 1
                for a in node[1]: walk(a)
        walk(rhs)
    return {"lhs_by_type": dict(lhs_type), "function_usage": dict(fn_use)}


def reinit_tracking_state() -> dict:
    """初始化"跟踪态" — 清 LAG / $var / last_written, 保留 RS 锁存和镜像文件.

    场景: 改了脚本系数 / 想让分离器温度从 0 爬变成立即跟踪当前燃水比 / 等等
    下周期所有 LAG 用 'y_prev = 当前 x' 初始化 (见 _eval_rhs 注释), 立即稳态.
    RS 锁存保留 — 它们是事件状态 (开/关命令的历史), 不该当作初值清掉.
    """
    s = _STATE
    n = {"lag": len(s.lag_state), "var": len(s.intermediates),
         "written": len(s.last_written), "ccs": len(s.ccs_state)}
    s.lag_state = {}
    s.intermediates = {}
    s.last_written = {}
    # CCS 协调模型也是积分状态, 算作"初值"清掉, 下周期按 yaml seed 起步
    s.ccs_state = {}
    logger.info(f"跟踪状态已重置: {n}")
    log_event("state", f"⏮ 重置初值 (LAG {n['lag']} / $var {n['var']} / CCS {n['ccs']} 清, RS {len(s.rs_state)} 保留)", n)
    return n


def reinit_lag_from_dcs(opc_url: Optional[str] = None) -> dict:
    """上载: 工程 → 本项目. 4 步无扰跟踪起步.

    Step 1. 检查 OPC 状态 (连不通直接给清晰错误, 不再等读 retry)
    Step 2. 读 DCS 当前值 (所有直接写 OPC 的 LHS — 含 LAG / RS / RS_NOT)
    Step 3. 无扰跟踪处理:
         - LAG (含嵌套, 走 RHS 树穿过 $var/LIMIT 等): lag_state = DCS 值
         - RS:     Q = DCS 值       (rs_state)
         - RS_NOT: Q = NOT DCS 值   (因为输出是 NOT Q, 反算 Q)
         - last_written 清空, 强制下周期重写一遍同步 DCS
    Step 4. 用户接着点【▶ 运行】, 正常读写循环跑起来,
            第 1 周期写出去 = DCS 现状 → 零跳变, 后续按各 τ 平滑过渡到算法目标.

    前置: OPC 循环已停 (避免跟运行中的 connect 抢 NTVDPU session).
    典型场景: VM 镜像还原 / CCMStudio 重下组态 / 工程 AI 被人工改过 之后.
    """
    import asyncio
    s = _STATE
    if s.running:
        return {"ok": False, "error": "OPC 循环在运行, 请先点【■ 停止】再上载"}

    # s.pairs 只有点过 ▶ 运行 才填; 首次启动直接调本接口时为空 — 从 script.txt 重新 parse
    pairs = s.pairs
    if not pairs:
        script_path = proj.paths().script
        if not script_path.exists():
            return {"ok": False,
                    "error": f"{script_path} 不存在, 先编辑器里点【💾 保存】"}
        try:
            content = script_path.read_text(encoding="utf-8")
            pairs = parse_script(content)
        except Exception as e:
            return {"ok": False, "error": f"脚本解析失败: {e}"}
        if not pairs:
            return {"ok": False, "error": "脚本为空 (全是注释?)"}

    url = opc_url or get_endpoint_config()["url"]
    import time as _time
    steps = []

    # ════════ Step 1: 检查 OPC 状态 ════════
    t0 = _time.perf_counter()
    async def _probe():
        client = OPCClient(url)
        await client.connect(retry_count=2, retry_interval=1.0)
        try: await client.disconnect()
        except Exception: pass
    try:
        asyncio.run(_probe())
    except Exception as e:
        log_event("opc-err", f"✗ 上载 Step 1 — OPC 不通 ({url}): {e}")
        return {"ok": False, "step": 1, "error": f"OPC 不通 ({url}): {e}",
                "msg": f"✗ Step 1 OPC 检查失败: {e}"}
    probe_ms = (_time.perf_counter() - t0) * 1000
    steps.append(f"✓ Step 1: OPC 通 ({url}, {probe_ms:.0f}ms)")

    # ════════ Step 2: 收集要读的 LHS + 状态 keys (LAG 含嵌套, RS 含 RS_NOT 反相) ════════
    var_defs = {lhs: rhs for lhs, rhs in pairs
                if isinstance(lhs, str) and lhs.startswith("$")}

    def _collect_lag_keys(expr, depth=0):
        """走 RHS 树收集 LAG state key, 但**不跨 $var 边界**.

        Why: 跨 $var 等于跨物理量级. 例如:
            DPU.TC = LAG($T_sep, 10)                     ← 温度 °C
            $T_sep = LIMIT(LAG($T_sep_static, 120), ...)  ← 温度
            $T_sep_static = ... + 2200 * ($M_coal_tot / ...)
            $M_coal_tot = LAG(sum_of_AI, 120)            ← 煤量 t/h

        把所有 LAG 都锚到 TC 的 DCS 值 (472°C) 是错的 —
        $M_coal_tot LAG 不应该用 472 当初值. 不跨 $var 后:
            - TC 的外层 LAG → 锚 (LHS = DCS TC) ✓
            - $T_sep 里的 LAG → 不锚 (它跟外层 LAG 量级相同, 用 track-init 就行)
            - $M_coal_tot 的 LAG → 不锚, track-init = sum_of_DCS_AI 当前读
        $M_coal_tot 不再"温度起步漂到煤量", 而是直接从合理量级起步.
        """
        if depth > 12: return []
        keys = []
        if isinstance(expr, tuple):
            fname, args = expr
            if fname == "LAG":
                keys.append(("LAG", _make_hashable(args)))
            for a in args:
                keys.extend(_collect_lag_keys(a, depth + 1))
        # 不跨 $var (原来这里会 recurse into var_defs[expr], 现已删除)
        return keys

    def _collect_rs_keys(expr, inverted=False, depth=0, seen=None):
        """走 RHS 树收集 RS state key, 支持跨 $var 和 NOT 反相。

        RS 是布尔锁存, 可以从合位/跳位反馈安全反算; 这和 LAG 不同, 不存在
        "把温度 DCS 值锚到煤量 LAG" 这种物理量级串线风险。
        """
        if seen is None:
            seen = set()
        if depth > 12:
            return []
        if isinstance(expr, str):
            if expr.startswith("$") and expr in var_defs and expr not in seen:
                seen.add(expr)
                return _collect_rs_keys(var_defs[expr], inverted, depth + 1, seen)
            return []
        if not isinstance(expr, tuple):
            return []
        fname, args = expr
        if fname == "RS":
            return [(("RS", _make_hashable(args)), inverted)]
        if fname == "RS_NOT":
            return [(("RS", _make_hashable(args)), not inverted)]
        if fname == "NOT" and args:
            return _collect_rs_keys(args[0], not inverted, depth + 1, seen)
        return []

    def _collect_ccs_pin_refs(expr, depth=0, seen=None):
        """收集 RHS 直接引用的模型输出管脚, 支持穿透 $var。

        仅处理直接传递关系, 如 AI=$SIM_MW、$SIM_MW=$YQ3.NE。
        不从 ADD/STEAM_T/LAG 等表达式反推, 避免非线性或量纲混合导致误锚定。
        """
        if seen is None:
            seen = set()
        if depth > 12:
            return []
        if isinstance(expr, str):
            if expr.startswith("$") and expr in var_defs and expr not in seen:
                seen.add(expr)
                return _collect_ccs_pin_refs(var_defs[expr], depth + 1, seen)
            return []
        if not isinstance(expr, tuple) or expr[0] != "INST_PIN":
            return []
        inst_name, pin = expr[1][0].split(".", 1)
        inst_rhs = var_defs.get(inst_name)
        if not (isinstance(inst_rhs, tuple) and inst_rhs[0] in MODEL_FACTORIES):
            return []
        spec = MODEL_FACTORIES[inst_rhs[0]]
        if pin not in spec.pins:
            return []
        key = (inst_rhs[0], _make_hashable(inst_rhs[1]))
        return [(key, pin)]

    # 对每个 OPC LHS 收集: LAG (递归但不跨 $var) + RS (支持 $var/NOT/RS_NOT 反相)
    lhs_lag_keys = {}   # {lhs_full: [lag_key, ...]}
    lhs_rs = {}         # {lhs_full: [(rs_key, inverted), ...]}  inverted=True 表示 NOT/RS_NOT
    lhs_ccs_pins = {}   # {lhs_full: [(ccs_key, pin), ...]}
    for lhs, rhs in pairs:
        if not isinstance(lhs, str) or lhs.startswith("$"):
            continue
        lag_keys = _collect_lag_keys(rhs)
        if lag_keys:
            lhs_lag_keys[lhs] = lag_keys
        rs_keys = _collect_rs_keys(rhs)
        if rs_keys:
            lhs_rs[lhs] = rs_keys
        ccs_pins = _collect_ccs_pin_refs(rhs)
        if ccs_pins:
            lhs_ccs_pins[lhs] = ccs_pins

    # 除了 LHS 锚定用的节点, 也把脚本里所有 RHS 引用的 OPC 节点一起读
    # (跟正常 OPC 循环一样收集 read_set), 让面板"读取"列在停止状态下也能看到全貌
    def _collect_rhs_opc(expr, out):
        if isinstance(expr, str):
            if expr.startswith("ns="):
                out.add(expr)
        elif isinstance(expr, tuple):
            for a in expr[1]:
                _collect_rhs_opc(a, out)
    rhs_opc = set()
    for _lhs, rhs in pairs:
        _collect_rhs_opc(rhs, rhs_opc)
    nodes_to_read = sorted(set(lhs_lag_keys.keys()) | set(lhs_rs.keys()) |
                           set(lhs_ccs_pins.keys()) | rhs_opc)
    if not nodes_to_read:
        log_event("state", "📥 上载 Step 2 — 脚本里没有 LHS=LAG/RS(...) 的直接写, 跳过")
        return {"ok": True, "synced_lag": 0, "synced_rs": 0,
                "msg": "脚本里没有 LHS=LAG/RS(...) 直接写, 无需同步"}

    steps.append(f"  收集到 {len(lhs_lag_keys)} 个 LAG-LHS + {len(lhs_rs)} 个 RS-LHS"
                 f" + {len(lhs_ccs_pins)} 个 CCS-LHS (合并去重 {len(nodes_to_read)} 个 OPC 节点)")

    # ════════ Step 3: 读 DCS 当前值 + 无扰跟踪处理 ════════
    t0 = _time.perf_counter()
    async def _read():
        client = OPCClient(url)
        await client.connect(retry_count=3, retry_interval=2.0)
        try:
            vals = await client.read_values(nodes_to_read)
            return dict(zip(nodes_to_read, vals))
        finally:
            try: await client.disconnect()
            except Exception: pass
    try:
        val_map = asyncio.run(_read())
    except Exception as e:
        log_event("error", f"✗ 上载 Step 2 — DCS 读取失败: {e}")
        return {"ok": False, "step": 2, "error": f"读 DCS 失败: {e}",
                "msg": f"✗ Step 2 读 DCS 失败: {e}"}
    read_ms = (_time.perf_counter() - t0) * 1000
    no_val = sum(1 for v in val_map.values() if v is None)
    steps.append(f"✓ Step 2: 读 {len(nodes_to_read)} 个 LHS DCS 当前值 ({read_ms:.0f}ms, {no_val} 读不到)")

    # 无扰跟踪: LAG (含嵌套) + RS (含 RS_NOT 反相)
    synced_lag = 0
    synced_rs = 0
    anchored_lag = set()
    for lhs_full, keys in lhs_lag_keys.items():
        v = val_map.get(lhs_full)
        if v is None: continue
        v_float = float(v)
        for k in keys:
            if k not in anchored_lag:
                s.lag_state[k] = v_float
                anchored_lag.add(k)
                synced_lag += 1
    anchored_rs = set()
    for lhs_full, keys in lhs_rs.items():
        v = val_map.get(lhs_full)
        if v is None: continue
        for rs_key, inverted in keys:
            if rs_key in anchored_rs:
                continue
            # NOT/RS_NOT 输出 = NOT Q, 所以从 LHS 反算 Q = NOT LHS
            q = (not bool(v)) if inverted else bool(v)
            s.rs_state[rs_key] = q
            anchored_rs.add(rs_key)
            synced_rs += 1

    synced_ccs = 0
    ccs_targets = {}  # {ccs_key: {pin: [actual, ...]}}
    for lhs_full, refs in lhs_ccs_pins.items():
        v = val_map.get(lhs_full)
        if v is None:
            continue
        try:
            v_float = float(v)
        except (TypeError, ValueError):
            continue
        for ccs_key, pin in refs:
            ccs_targets.setdefault(ccs_key, {}).setdefault(pin, []).append(v_float)
    for ccs_key, pin_vals in ccs_targets.items():
        fname = ccs_key[0]
        spec = MODEL_FACTORIES.get(fname)
        params = get_factory_params(fname)
        if spec is None or params is None:
            continue
        handle = s.ccs_state.get(ccs_key)
        if handle is None:
            handle = _CcsHandle(spec.make(params), spec.pins)
            s.ccs_state[ccs_key] = handle
        if not hasattr(handle.model, "get_state") or not hasattr(handle.model, "set_state"):
            continue
        st = handle.model.get_state()
        changed = False
        for pin, vals_for_pin in pin_vals.items():
            actual = sum(vals_for_pin) / len(vals_for_pin)
            if pin == "NE":
                st["Ne"] = actual
                changed = True
            elif pin == "HM":
                st["hm"] = actual
                changed = True
            elif pin == "PST":
                dp = params.get("steam", {}).get("dp", {})
                a = float(dp.get("a", 0.0))
                b = float(dp.get("b", 0.0))
                if abs(1.0 - a) > 1e-9:
                    # pst = pm - (a*pm + b) = (1-a)*pm - b
                    st["pm"] = (actual + b) / (1.0 - a)
                    changed = True
        if changed:
            handle.model.set_state(st)
            handle.outputs = {}
            handle.last_cycle = -1
            synced_ccs += 1

    # 清 last_written, 强制下周期重写一遍同步 DCS
    s.last_written = {}
    # 清掉没锚定的 LAG state — 否则旧 run 的残留 y_prev 可能在 LIMIT 门控下卡住, 让锚定值
    # 传不出去 (典型: DPU.TC = LAG($T_sep, 10); $T_sep = LIMIT(LAG(..., 120), 350, 480).
    # 外层锚 412, 但内层 LAG 旧残留 273 < 350, LIMIT 钳到 350, 外层 LAG 跌向 350).
    # 删掉非锚定 key 后下周期默认 y_prev=当前x (跟踪初始化), 算法链合理起步.
    cleared_lag = 0
    for k in list(s.lag_state.keys()):
        if k not in anchored_lag:
            del s.lag_state[k]; cleared_lag += 1
    # 把刚才读到的 DCS 现状直接灌进 last_read, 右侧"实时值"面板就能反映 (否则面板还是上次跑停残留)
    for node, v in val_map.items():
        if v is not None:
            try: s.last_read[node] = float(v)
            except (TypeError, ValueError):
                try: s.last_read[node] = v   # 非数值 (bool 等) 原样存
                except Exception: pass
    # 模拟一周期更新 $var + LHS 显示值 (用 sim 副本 + dt=0, 不影响真实 lag_state/rs_state)
    # 否则 panel 显示的 $M_X 等中间变量是上次跑停下来的 stale, 跟刚读的 DCS 对不上
    sim = _EvalSimState(s, cycle_count=s.cycle_count + 1)
    refreshed = 0
    for lhs, rhs in pairs:
        try:
            v = _eval_rhs(rhs, val_map, sim, dt=0.0)   # dt=0 → LAG y_prev 不前进
        except _SkipCycle:
            continue
        if v is None: continue
        s.last_values[lhs] = v
        if isinstance(lhs, str) and lhs.startswith("$"):
            sim.intermediates[lhs] = v   # ★ 关键: 下一对 RHS 用 $var 时要在 sim 里找得到
            s.intermediates[lhs] = v
        refreshed += 1
    steps.append(f"✓ Step 3: 无扰跟踪同步 — {synced_lag} 个 LAG + {synced_rs} 个 RS + {synced_ccs} 个 CCS, last_written 清空, 非锚 LAG 清 {cleared_lag} 个")
    steps.append(f"  → 刷新面板显示值: {len(val_map)} 个 DCS 节点 → last_read, 算 {refreshed} 对 LHS → last_values/$var")
    steps.append("✓ Step 4: 现在可以点【▶ 运行】, 第 1 周期写出去 = DCS 现状, 后续按 τ 平滑过渡")

    log_event("state",
              f"📥 上载: LAG {synced_lag} (含嵌套) + RS {synced_rs} (含 RS_NOT) ← 工程当前值",
              {"synced_lag": synced_lag, "synced_rs": synced_rs,
               "synced_ccs": synced_ccs,
               "no_val": no_val, "url": url, "probe_ms": int(probe_ms), "read_ms": int(read_ms)})
    return {"ok": True,
            "synced_lag": synced_lag, "synced_rs": synced_rs,
            "synced_ccs": synced_ccs,
            "no_val": no_val,
            "lhs_count": len(nodes_to_read),
            "msg": "\n".join(steps)}


def dryrun_preview(opc_url: Optional[str] = None) -> dict:
    """干运行预演: 读 DCS 现状 → 用脚本算一遍 → 对每个 OPC LHS 报告
    (算出来要写的值 vs DCS 现在的实际值 vs 差值 vs 风险等级).

    用法:
    - 状态量 (LAG/RS): 已被 upload 锚到 DCS → 算出 ≈ DCS, diff 应该 0
    - 非状态量 (SEL/直通/算术): diff 大 = 公式 bug 或量纲不一致
    - 前置: OPC 循环已停; 建议先点 上载 把 lag_state/rs_state 锚好
    """
    import asyncio
    s = _STATE
    if s.running:
        return {"ok": False, "error": "OPC 循环在运行, 请先点【■ 停止】再预演"}
    pairs = s.pairs
    if not pairs:
        sp = proj.paths().script
        if not sp.exists():
            return {"ok": False, "error": f"{sp} 不存在"}
        try:
            pairs = parse_script(sp.read_text(encoding="utf-8"))
        except ParseError as e:
            return {"ok": False, "error": f"脚本 parse 失败: {e}"}
    url = opc_url or get_endpoint_config()["url"]

    # 收集所有 OPC 节点 (LHS + RHS)
    all_opc = set()
    def _collect(expr):
        if isinstance(expr, str):
            if expr.startswith("ns="): all_opc.add(expr)
        elif isinstance(expr, tuple):
            for a in expr[1]: _collect(a)
    for lhs, rhs in pairs:
        if isinstance(lhs, str) and lhs.startswith("ns="):
            all_opc.add(lhs)
        _collect(rhs)

    # 读 DCS
    async def _read():
        client = OPCClient(url)
        await client.connect(retry_count=2, retry_interval=1.0)
        try:
            nodes = sorted(all_opc)
            vals = await client.read_values(nodes)
            return dict(zip(nodes, vals))
        finally:
            try: await client.disconnect()
            except Exception: pass
    try:
        val_map = asyncio.run(_read())
    except Exception as e:
        return {"ok": False, "error": f"OPC 读失败: {e}"}

    # 模拟一个周期 (用当前 lag_state/rs_state 副本, intermediates 从空算起)
    sim = _EvalSimState(s, cycle_count=s.cycle_count + 1)

    def _classify(rhs):
        if isinstance(rhs, tuple): return rhs[0]
        if isinstance(rhs, (int, float)): return "const"
        if isinstance(rhs, str): return "var" if rhs.startswith("$") else "direct"
        return "?"

    def _risk(diff_rel, kind):
        # 状态量: 锚定后算出 ≈ DCS, 容忍度更紧
        if kind in ("LAG", "RS", "RS_NOT"):
            if diff_rel < 0.005: return "ok"
            if diff_rel < 0.05:  return "info"
            return "warning"
        # 非状态量: 容忍度宽
        if diff_rel < 0.01: return "ok"
        if diff_rel < 0.1:  return "info"
        if diff_rel < 0.5:  return "warning"
        return "risk"

    results = []
    skipped = 0
    for lhs, rhs in pairs:
        try:
            v = _eval_rhs(rhs, val_map, sim, dt=0.2)
        except _SkipCycle:
            skipped += 1; continue
        if v is None: continue
        if isinstance(lhs, str) and lhs.startswith("$"):
            sim.intermediates[lhs] = v
            continue
        # OPC LHS
        actual = val_map.get(lhs)
        kind = _classify(rhs)
        item = {
            "lhs": lhs,
            "lhs_short": lhs.replace("ns=0;s=", "").replace(".HW.", ".").replace(".PV", ""),
            "kind": kind,
        }
        if isinstance(v, bool):
            item["computed"] = bool(v); item["computed_num"] = 1.0 if v else 0.0
        elif isinstance(v, (int, float)):
            item["computed"] = float(v); item["computed_num"] = float(v)
        else:
            item["computed"] = str(v); item["computed_num"] = None
        if isinstance(actual, bool):
            item["actual"] = bool(actual); item["actual_num"] = 1.0 if actual else 0.0
        elif isinstance(actual, (int, float)):
            item["actual"] = float(actual); item["actual_num"] = float(actual)
        elif actual is None:
            item["actual"] = None; item["actual_num"] = None
        else:
            item["actual"] = str(actual); item["actual_num"] = None
        # 计算 diff + 风险
        if item["computed_num"] is not None and item["actual_num"] is not None:
            diff = item["computed_num"] - item["actual_num"]
            item["diff"] = diff
            denom = max(abs(item["actual_num"]), 1.0)
            item["rel"] = abs(diff) / denom
            item["risk"] = _risk(item["rel"], kind)
        else:
            item["diff"] = None; item["rel"] = None
            item["risk"] = "no_actual" if item["actual"] is None else "non_numeric"
        results.append(item)

    # 排序: 风险大在前
    risk_order = {"risk": 0, "warning": 1, "info": 2, "non_numeric": 3, "no_actual": 4, "ok": 5}
    results.sort(key=lambda x: (risk_order.get(x.get("risk", "ok"), 99), -(x.get("rel") or 0)))

    summary = {"total": len(results), "skipped": skipped, "url": url, "nodes_read": len(val_map)}
    for r in ("risk", "warning", "info", "non_numeric", "no_actual", "ok"):
        summary[r] = sum(1 for x in results if x.get("risk") == r)
    return {"ok": True, "items": results, "summary": summary}


def prune_state_to_pairs(pairs: list) -> dict:
    """保存脚本时调用 — 清掉新脚本不再引用的 LAG/RS/CCS state key,
    保留还在用的状态量. 让镜像保存不带历史脚本残留的死状态.

    收集策略: 走 pairs 的 RHS 树, 看到 LAG/RS/RS_NOT/模型工厂就记下其 state_key,
    然后对 lag_state/rs_state/ccs_state 三个 dict 做白名单过滤.

    INST_PIN 的 args 是 ["$YQ3.PST"], 不会触发 LAG/RS/CCS 收集.

    返回各类被剪掉的条目数.
    """
    s = _STATE
    live: set = set()

    def _walk(expr):
        if not isinstance(expr, tuple):
            return
        fname, args = expr
        if fname == "INST_PIN":
            return
        if fname == "LAG":
            live.add(("LAG", _make_hashable(args)))
        elif fname in ("RS", "RS_NOT"):
            live.add(("RS", _make_hashable(args)))
        elif fname in MODEL_FACTORIES:
            live.add((fname, _make_hashable(args)))
        for a in args:
            _walk(a)

    for _, rhs in pairs:
        _walk(rhs)

    before = {"lag": len(s.lag_state), "rs": len(s.rs_state), "ccs": len(s.ccs_state)}
    s.lag_state = {k: v for k, v in s.lag_state.items() if k in live}
    s.rs_state  = {k: v for k, v in s.rs_state.items()  if k in live}
    s.ccs_state = {k: v for k, v in s.ccs_state.items() if k in live}
    pruned = {
        "lag": before["lag"] - len(s.lag_state),
        "rs":  before["rs"]  - len(s.rs_state),
        "ccs": before["ccs"] - len(s.ccs_state),
    }
    total = sum(pruned.values())
    if total > 0:
        log_event("state",
                  f"🧹 保存时清理过期状态 (LAG {pruned['lag']} / RS {pruned['rs']} / CCS {pruned['ccs']})",
                  pruned)
    return pruned


def reset_persistent_state() -> dict:
    """显式清空持久状态 (RS/LAG/中间变量/CCS 模型) — 用户想"从头开始"时调用"""
    s = _STATE
    n = {"rs": len(s.rs_state), "lag": len(s.lag_state),
         "var": len(s.intermediates), "ccs": len(s.ccs_state)}
    s.rs_state = {}
    s.lag_state = {}
    s.intermediates = {}
    s.ccs_state = {}
    s.last_values = {}
    s.last_read = {}
    s.last_written = {}
    try:
        _snapshot_path().unlink(missing_ok=True)
    except Exception:
        pass
    logger.info(f"持久状态已清空: {n}")
    log_event("state", f"🔥 清空状态 (RS {n['rs']} / LAG {n['lag']} / $var {n['var']} / CCS {n['ccs']})", n)
    return n


def get_debug() -> dict:
    """完整 debug 包: 状态 + 摘要 + 失败 + 写后未生效 + 日志"""
    s = _STATE
    # 写后未生效: 持续 >= 5 周期不一致 (1 秒, 避开 NTVDPU 写入延迟)
    GRACE_CYCLES = 5
    ineffective = []
    for k, streak in s.ineffective_streak.items():
        if streak < GRACE_CYCLES:
            continue
        if isinstance(k, str) and k.startswith("$"): continue
        actual = s.last_read.get(k)
        want = s.last_values.get(k)
        if actual is None or want is None: continue
        ineffective.append({"node": k, "wrote": want, "actual": actual, "streak": streak})
    ineffective.sort(key=lambda x: -x["streak"])
    return {
        "running": s.running,
        "uptime_s": (time.time() - s.started_at) if s.started_at else 0,
        "cycle_count": s.cycle_count,
        "read_count": s.read_count,
        "write_count": s.write_count,
        "last_error": s.last_error,
        "opc_url": s.opc_url,
        "dt": s.dt,
        "pairs_count": len(s.pairs),
        "pairs_summary": _summarize_pairs(s.pairs),
        # 写后未生效 (最严重的问题, 没报错但实际没生效)
        "write_ineffective": ineffective[:30],
        # 静默跳过 — RHS 节点读不到导致整对赋值跳过
        "top_skipped": sorted(
            [(lhs, s.skip_count[lhs], s.skip_cause.get(lhs, "?"))
             for lhs in s.skip_count],
            key=lambda x: -x[1])[:30],
        # 节点级 OPC 层面失败 (server 直接拒)
        "top_read_fail": sorted(s.node_read_fail.items(), key=lambda x: -x[1])[:20],
        "top_write_fail": sorted(s.node_write_fail.items(), key=lambda x: -x[1])[:20],
        "logs": list(_LOG_BUFFER)[-100:],
    }


def is_ai_target(short_or_full: str) -> bool:
    """判断目标节点是 AI(需走 HR/LR 双写)"""
    return ".HW.AI" in short_or_full or ".AI" in short_or_full.split(";")[-1]


def is_di_target(short_or_full: str) -> bool:
    return ".HW.DI" in short_or_full or ".DI" in short_or_full.split(";")[-1]


def ai_channel_base(full_node: str) -> str:
    """ns=0;s=DPU3013.HW.AI010502.PV → ns=0;s=DPU3013.HW.AI010502"""
    return full_node.rsplit(".", 1)[0]


# ---------- 脚本解析 ----------

class ParseError(Exception):
    pass


def _strip_paren_label(s: str) -> str:
    """剥离 'tag(描述)' 中的 (描述) 部分,返回 tag"""
    for op in ("(", "(", "（"):
        idx = s.find(op)
        if idx >= 0:
            return s[:idx].strip()
    return s.strip()


def _split_top_commas(s: str) -> list:
    """按顶层逗号分隔,保持括号内的逗号 (支持半角/全角括号)"""
    args = []
    depth = 0
    start = 0
    for i, ch in enumerate(s):
        if ch in "([{（":
            depth += 1
        elif ch in ")]}）":
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(s[start:i].strip())
            start = i + 1
    if start < len(s) or not s:
        args.append(s[start:].strip())
    return [a for a in args if a]


# 支持的函数 + 参数个数 (int 固定, -1 表示变长)
FUNC_ARITY = {
    # 锁存/逻辑
    "RS": 2, "RS_NOT": 2, "NOT": 1, "AND": 2, "OR": 2,
    # 算术 (参数: 常数 or 节点)
    "ADD": 2, "SUB": 2, "MUL": 2, "DIV": 2,
    "POW": 2, "SQRT": 1, "ABS": 1,
    # 取值
    "MAX": 2, "MIN": 2, "LIMIT": 3,
    # 选择: SEL(cond, a, b) — cond 真则 a, 否则 b
    "SEL": 3,
    # 一阶滞后: LAG(x, T) — y[k] = y[k-1] + dt/T*(x - y[k-1]), T 秒
    "LAG": 2,
    # 折线特性 CHAR(x, x0,y0, x1,y1, ..., xN,yN) — 变长, 至少 2 个点
    "CHAR": -1,
    # 水蒸气热力性质 — IAPWS-IF97 自动选区(亚饱和水/过热/超临界)
    #   STEAM_T(h, p) → T (°C)   入参: h kJ/kg, p MPa
    "STEAM_T": 2,
}
# 模型工厂 (src/models/dsl_registry.py 注册) — 加 660MW preset = 注册表加一条, 本文件不动
FUNC_ARITY.update({name: spec.arity for name, spec in MODEL_FACTORIES.items()})
SUPPORTED_FUNCS = tuple(FUNC_ARITY.keys())


# ---------- 表达式 parser(递归下降, 支持中缀 + 嵌套) ----------

_TOKEN_RE = re.compile(r"""
    \s+
  | (?P<NUMBER>\d+\.\d+|\d+)
  | (?P<NODE>DPU\d{4}(?:\.\w+)+)
  | (?P<INST_PIN>\$[A-Za-z_]\w*\.[A-Za-z_]\w*)
  | (?P<VAR>\$[A-Za-z_]\w*)
  | (?P<FUNC>[A-Z][A-Z_0-9]*)(?=\s*[(（])
  | (?P<LPAREN>[(（])
  | (?P<RPAREN>[)）])
  | (?P<COMMA>[,，])
  | (?P<POW>\^|\*\*)
  | (?P<OP>[+\-*/])
""", re.VERBOSE)


def _tokenize(s: str):
    """切 token. 节点/变量后紧跟 `(...)` 视为描述, 整段跳过"""
    tokens = []
    pos = 0
    while pos < len(s):
        # 描述区: 上一个是 NODE/VAR, 当前是 ( 或 ( → 吞到匹配的 )
        if tokens and tokens[-1][0] in ("NODE", "VAR") \
                and pos < len(s) and s[pos] in "(（":
            depth = 1
            end = pos + 1
            while end < len(s) and depth > 0:
                if s[end] in "(（": depth += 1
                elif s[end] in ")）": depth -= 1
                end += 1
            if depth == 0:
                pos = end
                continue
            # 括号不闭合 → fall through, 后续会按错处理
        m = _TOKEN_RE.match(s, pos)
        if not m:
            raise ParseError(f"无法解析 {s[pos:pos+12]!r}")
        pos = m.end()
        d = m.groupdict()
        for k, v in d.items():
            if v is not None:
                tokens.append((k, v))
                break
        # 全是 None 表示空白, 跳过
    return tokens


def _parse_expr(tokens, pos):
    """expr := term (('+'|'-') term)*"""
    pos, left = _parse_term(tokens, pos)
    while pos < len(tokens) and tokens[pos][0] == "OP" and tokens[pos][1] in "+-":
        op = tokens[pos][1]
        pos += 1
        pos, right = _parse_term(tokens, pos)
        left = ("ADD" if op == "+" else "SUB", [left, right])
    return pos, left


def _parse_term(tokens, pos):
    """term := factor (('*'|'/') factor)*"""
    pos, left = _parse_factor(tokens, pos)
    while pos < len(tokens) and tokens[pos][0] == "OP" and tokens[pos][1] in "*/":
        op = tokens[pos][1]
        pos += 1
        pos, right = _parse_factor(tokens, pos)
        left = ("MUL" if op == "*" else "DIV", [left, right])
    return pos, left


def _parse_factor(tokens, pos):
    """factor := unary ('^' factor)?   右结合"""
    pos, left = _parse_unary(tokens, pos)
    if pos < len(tokens) and tokens[pos][0] == "POW":
        pos += 1
        pos, right = _parse_factor(tokens, pos)
        left = ("POW", [left, right])
    return pos, left


def _parse_unary(tokens, pos):
    """unary := '-' unary | primary"""
    if pos < len(tokens) and tokens[pos][0] == "OP" and tokens[pos][1] == "-":
        pos += 1
        pos, inner = _parse_unary(tokens, pos)
        if isinstance(inner, (int, float)):
            return pos, -float(inner)
        return pos, ("SUB", [0.0, inner])
    if pos < len(tokens) and tokens[pos][0] == "OP" and tokens[pos][1] == "+":
        pos += 1
        return _parse_unary(tokens, pos)
    return _parse_primary(tokens, pos)


def _parse_primary(tokens, pos):
    """primary := NUMBER | NODE | INST_PIN | VAR | FUNC '(' args ')' | '(' expr ')'"""
    if pos >= len(tokens):
        raise ParseError("表达式意外结束")
    tk, val = tokens[pos]
    if tk == "NUMBER":
        return pos + 1, float(val)
    if tk == "NODE":
        return pos + 1, short_to_full(val)
    if tk == "INST_PIN":
        # $YQ3.PST → ("INST_PIN", ["$YQ3.PST"]); args 是 list 兼容标准 (fname, args)
        # 形态, 让所有走 RHS 树的遍历器 (_collect / _node_refs / _walk 等) 看到
        # "$YQ3.PST" 这个 args 元素时按 "$" 开头跳过 — 否则三元 tuple + 字符串迭代
        # 会拆字符成 Y/Q/3 被误判 OPC 节点
        return pos + 1, ("INST_PIN", [val])
    if tk == "VAR":
        return pos + 1, val
    if tk == "FUNC":
        fname = val
        pos += 1
        if pos >= len(tokens) or tokens[pos][0] != "LPAREN":
            raise ParseError(f"{fname} 后缺少 '('")
        pos += 1
        args = []
        if pos < len(tokens) and tokens[pos][0] != "RPAREN":
            pos, a = _parse_expr(tokens, pos)
            args.append(a)
            while pos < len(tokens) and tokens[pos][0] == "COMMA":
                pos += 1
                pos, a = _parse_expr(tokens, pos)
                args.append(a)
        if pos >= len(tokens) or tokens[pos][0] != "RPAREN":
            raise ParseError(f"{fname} 缺少 ')'")
        pos += 1
        if fname not in FUNC_ARITY:
            raise ParseError(f"未知函数 {fname!r}(支持: {', '.join(SUPPORTED_FUNCS)})")
        expected = FUNC_ARITY[fname]
        if expected >= 0 and len(args) != expected:
            raise ParseError(f"{fname} 需要 {expected} 个参数, 实际 {len(args)}")
        # 变长: CHAR(x, x0,y0, ..., xN,yN) — 至少 5 参数 (x + 2 对 xy), 总数奇数
        if expected < 0:
            if fname == "CHAR" and (len(args) < 5 or len(args) % 2 == 0):
                raise ParseError(f"CHAR 至少 5 参数 (x, x0,y0, x1,y1), 总数奇数; 实际 {len(args)}")
        return pos, (fname, args)
    if tk == "LPAREN":
        pos += 1
        pos, expr = _parse_expr(tokens, pos)
        if pos >= len(tokens) or tokens[pos][0] != "RPAREN":
            raise ParseError("缺少右括号 ')'")
        return pos + 1, expr
    raise ParseError(f"意外 token: {tk} {val!r}")


def _parse_rhs(rhs_raw: str):
    """解析右边为完整表达式。支持中缀运算符 + 函数嵌套 + 中间变量 + 节点描述。
    返回:
      - float                — 常数
      - str ('ns=0;s=...')   — OPC 节点
      - str ('$xxx')         — 中间变量
      - tuple (fname, [args]) — 函数调用 / 运算符(args 可嵌套)
    """
    tokens = _tokenize(rhs_raw)
    if not tokens:
        raise ParseError("右边为空")
    pos, result = _parse_expr(tokens, 0)
    if pos < len(tokens):
        leftover = tokens[pos]
        raise ParseError(f"表达式末尾多余 token: {leftover[1]!r}")
    return result


def parse_script(text: str) -> List[Tuple[str, Union[str, float]]]:
    """
    返回 [(lhs_full_node, rhs)]
        rhs: 字符串(另一个 OPC 节点) 或 float(常数)

    支持的语法:
        DPU3013.AI010502 = DPU3013.AQ010101
        DPU3013.AI010502(启动系统暖管反馈) = DPU3013.AQ010101(启动系统暖管指令)
        DPU3013.AI010502 = 50
        # 注释

    抛 ParseError(行号, 内容, 原因)
    """
    result = []
    for ln_no, raw in enumerate(text.splitlines(), 1):
        # 行首注释 — 整行跳过
        if raw.lstrip().startswith("#"):
            continue
        # 行尾注释 — # 必须 (1) 在括号外 (2) 前面是空白或行首
        # 防误切描述括号里的 # (如 'tag(#3机)' 或 'tag(A空预器#2)')
        cut = -1
        depth = 0
        for i, ch in enumerate(raw):
            if ch in "([（":
                depth += 1
            elif ch in ")]）":
                depth = max(0, depth - 1)
            elif ch == "#" and depth == 0:
                if i == 0 or raw[i-1].isspace():
                    cut = i; break
        # Fallback: 描述里括号未闭合(NT6000 截断) → 从右找 ' #'
        if cut < 0 and depth > 0:
            for i in range(len(raw) - 1, 0, -1):
                if raw[i] == "#" and raw[i-1].isspace():
                    cut = i; break
        stripped = (raw[:cut] if cut >= 0 else raw).strip()
        if not stripped:
            continue
        if "=" not in stripped:
            raise ParseError(
                f"第 {ln_no} 行格式错(应为 '左 = 右'): {raw!r}"
            )
        lhs_raw, rhs_raw = stripped.split("=", 1)
        if not lhs_raw.strip() or not rhs_raw.strip():
            raise ParseError(f"第 {ln_no} 行左/右不能为空: {raw!r}")
        # 右边: 数字 / OPC 节点 / 函数 / 表达式
        try:
            rhs_val = _parse_rhs(rhs_raw)
        except (ValueError, ParseError) as e:
            raise ParseError(f"第 {ln_no} 行右边无效: {e}")
        # 左边: 支持逗号分隔多目标 (A, B, C = expr 一次写 3 对)
        # 描述括号里的逗号不切 — _split_top_commas 已尊重括号深度
        lhs_parts = _split_top_commas(lhs_raw)
        for lhs_part in lhs_parts:
            try:
                lhs_full = short_to_full(_strip_paren_label(lhs_part))
            except ValueError as e:
                raise ParseError(f"第 {ln_no} 行左边无效: {e}")
            result.append((lhs_full, rhs_val))
    return result


# ---------- 求值 ----------

class _SkipCycle(Exception):
    """本周期数据不全, 跳过该 lhs 的写"""
    pass


def _resolve(arg, val_by_node: dict, s=None, dt: float = 0.2):
    """参数 → 实际值。
       tuple           → 嵌套函数, 递归求值
       str ($xxx)      → 中间变量
       str (节点)      → OPC 读值
       float           → 常数直返
    """
    if isinstance(arg, tuple):
        return _eval_rhs(arg, val_by_node, s, dt)
    if isinstance(arg, str):
        if arg.startswith("$"):
            if s is None or arg not in s.intermediates:
                raise _SkipCycle()
            return s.intermediates[arg]
        v = val_by_node.get(arg)
        if v is None:
            raise _SkipCycle()
        return v
    return arg


def _make_hashable(x):
    """把表达式树里的 list 递归转 tuple, 让 RS/LAG state 能用 args 当 key.
       不修这里 → 当 LAG/RS 的参数是嵌套表达式(ADD/MUL 等)时, raw_args 里
       带 list 直接 tuple() 还是 unhashable, 抛 'unhashable type: list'."""
    if isinstance(x, list):
        return tuple(_make_hashable(e) for e in x)
    if isinstance(x, tuple):
        return tuple(_make_hashable(e) for e in x)
    return x


class _CcsHandle:
    """模型工厂函数返回的运行时把柄 — 装到 s.intermediates['$YQ3'] 里.
    持有: 模型对象 + 管脚名 + 上次输出 + 上次积分的 cycle (整周期只 step 一次)
    """
    __slots__ = ("model", "pins", "outputs", "last_cycle")

    def __init__(self, model, pins):
        self.model = model
        self.pins = tuple(pins)
        self.outputs: dict = {}        # {pin: float}
        self.last_cycle: int = -1

    def step_if_needed(self, vals, dt: float, cycle: int) -> None:
        if self.last_cycle != cycle:
            self.last_cycle = cycle
            outs = self.model.step(*vals, dt)
            self.outputs = dict(zip(self.pins, outs))


class _EvalSimState:
    """上载/预演用的临时求值状态, 避免干运行污染真实运行状态。"""

    def __init__(self, src_state=None, cycle_count: int = 0):
        self.lag_state = dict(getattr(src_state, "lag_state", {}))
        self.rs_state = dict(getattr(src_state, "rs_state", {}))
        self.intermediates = {}
        self.ccs_state = _clone_ccs_state(getattr(src_state, "ccs_state", {}))
        self.cycle_count = cycle_count


def _clone_ccs_state(ccs_state: dict) -> dict:
    """复制 CCS 模型实例池, 供预演/上载刷新使用。

    不能直接 dict() 浅拷贝: _CcsHandle 内部模型有积分状态, 干运行 step 会污染真实状态。
    """
    cloned = {}
    for key, handle in ccs_state.items():
        try:
            fname = key[0]
            spec = MODEL_FACTORIES[fname]
            params = get_factory_params(fname)
            if params is None:
                continue
            new_handle = _CcsHandle(spec.make(params), spec.pins)
            if hasattr(handle.model, "get_state") and hasattr(new_handle.model, "set_state"):
                new_handle.model.set_state(handle.model.get_state())
            new_handle.outputs = dict(handle.outputs)
            cloned[key] = new_handle
        except Exception:
            continue
    return cloned


def _eval_rhs(rhs, val_by_node: dict, s, dt: float):
    """计算右边表达式的当前值。
    rhs 形态:
      - str(节点):直接读
      - float/int:常数
      - (fname, [args]):函数调用
      - ("INST_PIN", "$YQ3", "PST"):实例管脚访问
    """
    # 实例管脚访问 $YQ3.PST → 查 s.intermediates['$YQ3'] (必须是 _CcsHandle)
    # AST 形态: ("INST_PIN", ["$YQ3.PST"]) — args 列表里唯一元素是 "$inst.pin"
    if isinstance(rhs, tuple) and rhs[0] == "INST_PIN":
        inst_name, pin = rhs[1][0].split(".", 1)
        handle = s.intermediates.get(inst_name)
        if not isinstance(handle, _CcsHandle):
            raise _SkipCycle()
        if pin not in handle.outputs:
            raise _SkipCycle()
        return handle.outputs[pin]

    if isinstance(rhs, str):
        # 中间变量直读
        if rhs.startswith("$"):
            if rhs not in s.intermediates:
                raise _SkipCycle()
            return s.intermediates[rhs]
        v = val_by_node.get(rhs)
        if v is None:
            raise _SkipCycle()
        return v
    if isinstance(rhs, (int, float)):
        return rhs
    if not isinstance(rhs, tuple):
        return None

    fname, raw_args = rhs
    vals = [_resolve(a, val_by_node, s, dt) for a in raw_args]

    # 锁存/逻辑
    if fname in ("RS", "RS_NOT"):
        set_v, reset_v = vals
        key = ("RS", _make_hashable(raw_args))
        last = s.rs_state.get(key, False)
        if bool(set_v):     q = True
        elif bool(reset_v): q = False
        else:               q = last
        s.rs_state[key] = q
        return q if fname == "RS" else (not q)
    if fname == "NOT":
        return not bool(vals[0])
    if fname == "AND":
        return bool(vals[0]) and bool(vals[1])
    if fname == "OR":
        return bool(vals[0]) or bool(vals[1])
    # 单参算术
    if fname == "SQRT":
        x = float(vals[0])
        return x ** 0.5 if x >= 0 else 0.0
    if fname == "ABS":
        return abs(float(vals[0]))
    # 双参算术
    a, b = (float(vals[0]), float(vals[1])) if len(vals) >= 2 else (0.0, 0.0)
    if fname == "ADD": return a + b
    if fname == "SUB": return a - b
    if fname == "MUL": return a * b
    if fname == "DIV": return a / b if b != 0 else 0.0
    if fname == "POW": return a ** b
    if fname == "MAX": return max(a, b)
    if fname == "MIN": return min(a, b)
    # 限幅
    if fname == "LIMIT":
        x, lo, hi = float(vals[0]), float(vals[1]), float(vals[2])
        return max(lo, min(hi, x))
    # 选择
    if fname == "SEL":
        return vals[1] if bool(vals[0]) else vals[2]
    # 折线特性 CHAR(x, x0,y0, x1,y1, ..., xN,yN)
    # 端点外取最近值, 段内线性插值
    if fname == "CHAR":
        x = float(vals[0])
        pts = []
        for i in range(1, len(vals) - 1, 2):
            pts.append((float(vals[i]), float(vals[i + 1])))
        pts.sort(key=lambda p: p[0])
        if x <= pts[0][0]: return pts[0][1]
        if x >= pts[-1][0]: return pts[-1][1]
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]; x1, y1 = pts[i + 1]
            if x0 <= x <= x1:
                if x1 == x0: return y0
                return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        return pts[-1][1]

    # 一阶滞后 — 冷启动 y0=0, 输入阶跃后按时间常数 T 爬升
    if fname == "LAG":
        x, T = float(vals[0]), float(vals[1])
        key = ("LAG", _make_hashable(raw_args))
        # 首次跟踪初始化: 默认 y_prev=x → 第一周期输出就等于输入, 跳过 N·τ 爬升期.
        # DCS 厂家算法块的标准做法 — 防止开机时分离器温度从 0 一路爬到 380℃,
        # 触发越限报警 / 控制器误动.  后续周期照常按 (dt/T) 滞后.
        y_prev = s.lag_state.get(key, x)
        y = y_prev + (dt / T) * (x - y_prev) if T > 0 else x
        s.lag_state[key] = y
        return y

    # 模型工厂 (dsl_registry 注册) — $YQ3 = CCS_660(uB, Dfw, ut)
    # 返回 _CcsHandle 把柄, 后续 $YQ3.PST 等从把柄读管脚
    # 入参 hashable 作 key → 同一脚本里多次写 $YQ3 = CCS_660(...) 用同一份模型
    if fname in MODEL_FACTORIES:
        spec = MODEL_FACTORIES[fname]
        key = (fname, _make_hashable(raw_args))
        handle = s.ccs_state.get(key)
        if handle is None:
            params = get_factory_params(fname)
            if params is None:
                raise _SkipCycle()    # 参数 yaml 没加载成功, 跳过 (诊断面板可查错因)
            handle = _CcsHandle(spec.make(params), spec.pins)
            s.ccs_state[key] = handle
        handle.step_if_needed([float(v) for v in vals], dt, s.cycle_count)
        return handle

    # 水蒸气热力性质 — IAPWS-IF97  STEAM_T(h, p) → T(°C)
    if fname == "STEAM_T":
        h, p = float(vals[0]), float(vals[1])
        T = steam_T_from_ph(h, p)
        if T is None:
            raise _SkipCycle()   # 越界 / iapws 求解失败 → 跳过本周期, 状态面板查原因
        return T
    return None


# ---------- 状态镜像 (用户显式保存/恢复, 用于 NTVDPU 重启后手动还原) ----------

def _snapshot_path() -> Path:
    return proj.paths().snapshot

def _snapshot_bak_dir() -> Path:
    return proj.paths().snapshot_backups

_SNAPSHOT_KEEP = 30                                  # 最多保留几份历史副本


def _migrate_legacy_bak() -> None:
    """老机制留下的 state_snapshot.json.bak → 搬进新历史目录(模块加载时跑一次).
    防丢失用户已有的"上一份"备份.
    """
    if not proj.list_projects():
        return    # 还没建任何工程 (新 clone) — 跳过迁移, 别让 import 崩
    from datetime import datetime
    import shutil
    legacy = _snapshot_path().with_suffix(_snapshot_path().suffix + ".bak")
    if not legacy.exists():
        return
    try:
        _snapshot_bak_dir().mkdir(parents=True, exist_ok=True)
        ts = datetime.fromtimestamp(legacy.stat().st_mtime).strftime("%Y%m%d_%H%M%S")
        migrated = _snapshot_bak_dir() / f"snapshot_{ts}_legacy.json"
        if not migrated.exists():
            shutil.copy2(legacy, migrated)
        legacy.unlink()
        logger.info(f"老 .bak 迁入历史目录: {migrated.name}")
    except Exception as e:
        logger.warning(f"老 .bak 迁移失败 (跳过): {e}")


def list_snapshot_backups() -> list:
    """返回所有历史镜像副本, 新→旧 排序. 每项 {name, path, ts, size}"""
    if not _snapshot_bak_dir().exists():
        return []
    out = []
    for p in _snapshot_bak_dir().glob("snapshot_*.json"):
        try:
            st = p.stat()
            out.append({
                "name": p.name,
                "path": str(p),
                "ts": st.st_mtime,
                "size": st.st_size,
            })
        except OSError:
            continue
    out.sort(key=lambda x: -x["ts"])
    return out


def save_state_snapshot(force: bool = False) -> dict:
    """显式保存当前 RS/LAG/中间变量到镜像文件 (用户主动调用)
    JSON 结构清晰: 每个 RS/LAG 条目带 fname/args/value 字段, 便于查看核对
    force=False 时若内存全空会拒绝, 避免误覆盖已有镜像

    备份链:
      - 主文件 data/state_snapshot.json (📤 下载默认从这里读, 永远指最新)
      - 历史 data/snapshot_backups/snapshot_YYYYMMDD_HHMMSS.json (每次保存留一份)
      - 历史只保留最近 _SNAPSHOT_KEEP 个 (满了清最老)
    """
    import json as _json
    from datetime import datetime
    s = _STATE
    if not force and not s.rs_state and not s.lag_state and not s.intermediates:
        return {"ok": False, "error": "内存里 RS/LAG/中间变量 全为空, 拒绝保存(防覆盖). "
                                       "请先运行脚本让状态算出来, 或加 force 强制保存"}
    try:
        _snapshot_path().parent.mkdir(parents=True, exist_ok=True)
        _snapshot_bak_dir().mkdir(parents=True, exist_ok=True)
        # 老 .bak 防御性再迁一次 (module 加载时已跑过, 这里防中途外部进程造 .bak)
        _migrate_legacy_bak()
        # 写新文件前, 若旧主文件存在 → 拷一份到时间戳历史 (留底链不再单 .bak)
        if _snapshot_path().exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = _snapshot_bak_dir() / f"snapshot_{ts}.json"
            try:
                import shutil
                shutil.copy2(_snapshot_path(), bak)
            except Exception as e:
                logger.warning(f"备份旧镜像到历史失败 (继续覆盖写): {e}")
            # 清理: 只留最近 N 个
            try:
                backups = sorted(_snapshot_bak_dir().glob("snapshot_*.json"))
                for old in backups[:-_SNAPSHOT_KEEP]:
                    try: old.unlink()
                    except OSError: pass
            except Exception: pass
        data = {
            "saved_at": time.time(),
            "rs_state": [
                {"fname": k[0], "args": list(k[1]), "value": v}
                for k, v in s.rs_state.items()
            ],
            "lag_state": [
                {"fname": k[0], "args": list(k[1]), "value": v}
                for k, v in s.lag_state.items()
            ],
            # 模型实例 _CcsHandle 不进镜像 — 重启时按 yaml seed 重建即可,
            # 序列化 model + delay queue 复杂且收益小
            "intermediates": {k: v for k, v in s.intermediates.items()
                              if not isinstance(v, _CcsHandle)},
        }
        _snapshot_path().write_text(
            _json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        n = {"rs": len(s.rs_state), "lag": len(s.lag_state), "var": len(s.intermediates)}
        logger.info(f"状态镜像已保存: {n}")
        log_event("snapshot", f"📸 镜像保存 (RS {n['rs']} / LAG {n['lag']} / $var {n['var']})", n)
        return {"ok": True, "saved": n}
    except Exception as e:
        logger.warning(f"保存镜像失败: {e}")
        log_event("error", f"✗ 镜像保存失败: {e}")
        return {"ok": False, "error": str(e)}


def restore_state_snapshot(path: Optional[str] = None) -> dict:
    """从镜像文件恢复 RS/LAG/中间变量.
    path=None → 主文件 data/state_snapshot.json
    path="snapshot_20260610_153045.json" → data/snapshot_backups/ 下指定历史副本
    """
    import json as _json
    from datetime import datetime
    s = _STATE
    if path:
        # 安全: 只允许文件名 (不接受绝对路径或 ..) — 防御 path traversal
        if ("/" in path) or ("\\" in path) or (".." in path) or not path.endswith(".json"):
            return {"ok": False, "error": f"非法历史镜像名: {path!r}"}
        src = _snapshot_bak_dir() / path
        if not src.exists():
            return {"ok": False, "error": f"历史镜像不存在: {path}"}
    else:
        src = _snapshot_path()
        if not src.exists():
            return {"ok": False, "error": "没有镜像文件,先保存"}
    try:
        data = _json.loads(src.read_text(encoding="utf-8"))
        # 镜像现在只有一种格式: list of {fname, args, value}
        # _make_hashable 递归把 list → tuple, 否则嵌套表达式参数 (如
        # LAG((MUL,[...]),120) 这种 args) 直接 tuple() 还是带 list, dict key 报
        # unhashable
        def _build(raw):
            out = {}
            for it in raw or []:
                out[(it["fname"], _make_hashable(it["args"]))] = it["value"]
            return out
        s.rs_state = _build(data.get("rs_state", []))
        s.lag_state = _build(data.get("lag_state", []))
        s.intermediates = data.get("intermediates", {})
        # 清 last_written: 让下周期所有 LHS 强制重写, 把 RS 等状态对应的 OPC 写值真正发出去
        s.last_written = {}
        ts = data.get("saved_at", 0)
        when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        n = {"rs": len(s.rs_state), "lag": len(s.lag_state), "var": len(s.intermediates)}
        logger.info(f"已从镜像恢复: {n} (镜像保存于 {when})")
        log_event("snapshot", f"📤 下载镜像 (RS {n['rs']} / LAG {n['lag']} / $var {n['var']}, 保存于 {when})", n)
        return {"ok": True, "restored": n, "saved_at": when}
    except Exception as e:
        logger.warning(f"恢复失败: {e}")
        log_event("error", f"✗ 镜像恢复失败: {e}")
        return {"ok": False, "error": str(e)}


def _index_state_users(pairs):
    """反向索引: state_key (RS/LAG) → [使用这个 state 的 LHS 短码列表]
    用于镜像里显示 "这个触发器用在哪几个赋值"
    """
    out = {}
    def _walk(expr, lhs_short, depth=0):
        if depth > 5: return
        if not isinstance(expr, tuple): return
        fname, args = expr
        if fname in ("RS", "RS_NOT", "LAG"):
            real_fn = "RS" if fname in ("RS", "RS_NOT") else fname
            key = (real_fn, _make_hashable(args))
            out.setdefault(key, []).append(f"{lhs_short} = {fname}(...)")
        for a in args:
            _walk(a, lhs_short, depth + 1)
    for lhs, rhs in pairs:
        short = lhs if isinstance(lhs, str) and lhs.startswith("$") \
                else (lhs.replace("ns=0;s=", "").replace(".HW.", ".").replace(".PV", "")
                      if isinstance(lhs, str) else str(lhs))
        _walk(rhs, short)
    return out


def get_snapshot_info(with_detail: bool = False) -> dict:
    """看当前镜像信息. with_detail=True 返回完整模块值用于检查"""
    import json as _json
    from datetime import datetime
    if not _snapshot_path().exists():
        return {"exists": False}
    try:
        data = _json.loads(_snapshot_path().read_text(encoding="utf-8"))
        ts = data.get("saved_at", 0)
        rs_raw = data.get("rs_state") or []
        lag_raw = data.get("lag_state") or []
        intermediates = data.get("intermediates") or {}

        out = {
            "exists": True,
            "saved_at": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
            "age_s": int(time.time() - ts),
            "rs_count": len(rs_raw),
            "lag_count": len(lag_raw),
            "var_count": len(intermediates),
            "size_bytes": _snapshot_path().stat().st_size,
            "path": str(_snapshot_path()),
        }

        if with_detail:
            # 把 OPC 节点路径缩短显示, 便于人眼对照
            def _short(nid):
                if isinstance(nid, str):
                    return nid.replace("ns=0;s=", "").replace(".HW.", ".").replace(".PV", "")
                return str(nid)
            # 反向索引 state → [使用它的 LHS 列表]
            users_index = _index_state_users(_STATE.pairs)
            def _users_for(fname, args_full):
                # users_index key 用 _make_hashable, 这里也得用, 嵌套表达式 args 才匹配得上
                k = (fname, _make_hashable(args_full))
                return users_index.get(k, [])
            def _norm_items(raw):
                return [{
                    "fname": it.get("fname", "?"),
                    "args": [_short(a) for a in it.get("args", [])],
                    "value": it.get("value"),
                    "users": _users_for(it.get("fname", "?"), it.get("args", [])),
                } for it in raw]
            out["rs_detail"] = _norm_items(rs_raw)
            out["lag_detail"] = _norm_items(lag_raw)
            out["var_detail"] = intermediates
        return out
    except Exception as e:
        return {"exists": True, "error": str(e)}


# ---------- 后台运行状态 ----------

class _State:
    def __init__(self):
        self.running: bool = False
        self.thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.cycle_count: int = 0
        self.read_count: int = 0
        self.write_count: int = 0
        self.last_error: Optional[str] = None
        self.started_at: Optional[float] = None
        self.pairs: List[Tuple[str, Union[str, float]]] = []
        self.dt: float = 0.2
        self.opc_url: str = DEFAULT_OPC_URL
        self.last_values: dict = {}     # {full_node: latest_value} 已写入(LHS)
        self.last_read: dict = {}       # {full_node: latest_value} 读到的(RHS 源)
        # 实时通讯负荷统计 (最近 20 周期滚动平均)
        self.recent_read_ms: list = []
        self.recent_write_ms: list = []
        self.recent_cycle_ms: list = []
        # RS 触发器持久状态 {lhs_full_node: bool}
        self.rs_state: dict = {}
        # 一阶滞后(LAG)持久状态 {state_key: y_last}
        self.lag_state: dict = {}
        # 中间变量当前值 {"$tmp": value}, 每周期可覆盖
        self.intermediates: dict = {}
        # CCS 协调模型实例池 {state_key: {"inst": CcsUscOtbt, "tick": int,
        #                                "out": (pst, hm, Ne)}}
        # state_key = ("CCS", hashable(args)) — 3 个姊妹函数 CCS_PST/HM/NE 共享
        self.ccs_state: dict = {}
        # 上次写入值 (用于跳过未变化的写,降低通讯负荷)
        self.last_written: dict = {}
        # 节点级失败/成功累计 (debug 用)
        self.node_read_fail: dict = {}     # {node: 累计读失败次数}
        self.node_write_fail: dict = {}    # {node: 累计写失败次数}
        self.node_write_ok: dict = {}      # {node: 累计写成功次数}
        # SkipCycle 跟踪 — 因 RHS 读不到导致整对被跳过 (最常见的"安静失效")
        self.skip_count: dict = {}         # {lhs: 累计跳过次数}
        self.skip_cause: dict = {}         # {lhs: 第一个 None 的源节点}
        # "写后未生效" 持续不一致计数 (NTVDPU 写入有 ~1 秒延迟, 需要稳定判定)
        self.ineffective_streak: dict = {}  # {lhs: 连续不一致周期数}


_STATE = _State()
# 不自动恢复 — 用户在界面点【🔄 恢复镜像】才恢复 (避免误用陈旧状态)
# 模块加载时把老机制的 .bak 单备份迁进新历史目录 (一次性)
_migrate_legacy_bak()


def _jsonable_value_map(d: dict) -> dict:
    """把 last_values / intermediates 序列化前先把 _CcsHandle 转成 outputs dict.
    Flask jsonify 不认识自定义对象, 直接喂会 500. 前端拿到 $YQ3 → {PST,HM,NE}
    反而更直观, 一行就能在实时值面板里看到三个管脚.
    """
    return {k: (v.outputs if isinstance(v, _CcsHandle) else v)
            for k, v in d.items()}


def switch_project(name: str) -> dict:
    """切换激活工程 — 仅停止态允许. 全量重建运行内存状态 + 立即探活新工程端点."""
    global _STATE
    if _STATE.running:
        return {"ok": False, "error": "OPC 循环运行中, 先点【■ 停止】再切工程"}
    try:
        proj.set_active(name)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    _STATE = _State()      # RS/LAG/$var/模型实例全清 — 不能把 A 工程状态带进 B 工程
    log_event("project", f"📂 切换工程 → {name}")
    try:
        _probe_once_and_store()
    except Exception:
        pass
    return {"ok": True, "active": name}


def get_status() -> dict:
    s = _STATE
    avg = lambda lst: (sum(lst) / len(lst)) if lst else 0.0
    avg_read = avg(s.recent_read_ms)
    avg_write = avg(s.recent_write_ms)
    avg_cycle = avg(s.recent_cycle_ms)
    dt_ms = s.dt * 1000
    load_pct = (avg_cycle / dt_ms * 100) if dt_ms > 0 else 0.0
    # 写后未生效统计 — 必须持续 >= 5 周期 (1秒) 不一致才算
    # (NTVDPU 写入有内部刷新延迟, 立即对比会误报)
    GRACE_CYCLES = 5
    n_write_ineffective = sum(1 for n in s.ineffective_streak.values() if n >= GRACE_CYCLES)
    return {
        "running": s.running,
        "cycle_count": s.cycle_count,
        "read_count": s.read_count,
        "write_count": s.write_count,
        # 累计次数 + 涉及不同节点数 (区分: 1 个节点持续失败 vs 多个节点偶发失败)
        "write_fail_total": sum(s.node_write_fail.values()),
        "write_fail_nodes": len(s.node_write_fail),
        "read_fail_total": sum(s.node_read_fail.values()),
        "read_fail_nodes": len(s.node_read_fail),
        "write_ineffective": n_write_ineffective,
        # 当前内存里的持久状态计数 (镜像 save 前对比用)
        "memory_rs_count": len(s.rs_state),
        "memory_lag_count": len(s.lag_state),
        "memory_var_count": len(s.intermediates),
        # 静默跳过 (RHS 读不到, 整对赋值无效) — 最易被忽略
        "skipped_pairs": len(s.skip_count),
        "skipped_total": sum(s.skip_count.values()),
        "last_error": s.last_error,
        "started_at": s.started_at,
        "uptime_s": (time.time() - s.started_at) if s.started_at else 0,
        "pairs_count": len(s.pairs),
        "dt": s.dt,
        "opc_url": s.opc_url,
        "last_values": _jsonable_value_map(s.last_values),
        "last_read": s.last_read,
        "avg_read_ms": round(avg_read, 1),
        "avg_write_ms": round(avg_write, 1),
        "avg_cycle_ms": round(avg_cycle, 1),
        "dt_ms": round(dt_ms, 0),
        "load_pct": round(load_pct, 1),
    }


async def _opc_loop(initial_pairs: List[Tuple[str, Union[str, float]]], dt: float,
                    opc_url: str):
    s = _STATE
    s.pairs = initial_pairs   # 保证 swap 入口拿到同一份
    client = OPCClient(opc_url)
    try:
        await client.connect(retry_count=3, retry_interval=2.0)
    except Exception as e:
        s.last_error = f"OPC 连接失败: {e}"
        s.running = False
        return
    logger.info(f"OPC 已连接, 进入循环 dt={dt}s, {len(initial_pairs)} 对")

    # 自动重连状态: 连续 N 周期全部读不到值 → 视为连接断, 尝试重连
    consec_fail = 0
    RECONNECT_THRESHOLD = 10   # 10 周期 (~2 秒) 全失败就重连
    BACKOFF_AFTER_FAIL = 5     # 重连失败后等 5 秒再试

    # 预解析:所有需要读的 OPC 节点 (去重)
    # 只收 RHS 引用的节点 — 求值要用. LHS 不读(用户的脚本范围外不浪费 OPC 通讯)
    def _collect(expr, out):
        if isinstance(expr, str):
            if not expr.startswith("$"):
                out.add(expr)
        elif isinstance(expr, tuple):
            for a in expr[1]:
                _collect(a, out)
    def _rebuild_read_set(pairs_):
        rs = set()
        for _lhs, rhs in pairs_:
            _collect(rhs, rs)
        return list(rs)
    # 初始 read_nodes + 引用追踪 (每周期看 s.pairs 是不是被 swap 了)
    read_nodes = _rebuild_read_set(initial_pairs)
    last_pairs_ref = initial_pairs

    try:
        while s.running:
            cycle_t0 = time.perf_counter()
            read_ms = 0.0
            write_ms = 0.0
            # ★ 在线下装: 看 s.pairs 是不是被 swap 了 (引用比较, O(1))
            # 是 → 重算 read_set, 后续步骤用新 pairs. 不停 OPC 连接, 不断流.
            current_pairs = s.pairs
            if current_pairs is not last_pairs_ref:
                read_nodes = _rebuild_read_set(current_pairs)
                logger.info(f"♻ 在线下装: pairs {len(last_pairs_ref)} → "
                            f"{len(current_pairs)} 对, read_set → {len(read_nodes)} 节点")
                last_pairs_ref = current_pairs
            try:
                # 1. 批量读 (计时)
                if read_nodes:
                    r_t0 = time.perf_counter()
                    raw = await client.read_values(read_nodes)
                    read_ms = (time.perf_counter() - r_t0) * 1000
                    val_by_node = dict(zip(read_nodes, raw))
                    s.last_read = {k: v for k, v in val_by_node.items() if v is not None}
                    s.read_count += len(read_nodes)
                    # 节点级读失败统计
                    n_ok = 0
                    for n, v in val_by_node.items():
                        if v is None:
                            s.node_read_fail[n] = s.node_read_fail.get(n, 0) + 1
                        else:
                            n_ok += 1
                    # 自动重连判定: 全部读不到 → 视为连接断
                    if n_ok == 0:
                        consec_fail += 1
                    else:
                        consec_fail = 0
                else:
                    val_by_node = {}

                # OPC 自动重连 — NTVDPU 重启 / 网络抖动恢复
                if consec_fail >= RECONNECT_THRESHOLD:
                    logger.warning(f"连续 {consec_fail} 周期全部读失败, 重连 OPC")
                    log_event("opc-err", f"⚠ OPC 断连: 连续 {consec_fail} 周期读失败, 重连中",
                              {"opc_url": opc_url})
                    try: await client.disconnect()
                    except Exception: pass
                    client = OPCClient(opc_url)
                    try:
                        await client.connect(retry_count=2, retry_interval=2.0)
                        # 重连后 NTVDPU 端所有值可能被重置, 清 last_written
                        # 让所有 LHS 强制重写一次, 避免"跳过未变化"误判
                        s.last_written = {}
                        logger.info(f"OPC 重连成功, RS/LAG 状态保持, "
                                    f"last_written 清空 → 下周期所有 LHS 重写")
                        log_event("opc", "🔗 OPC 重连成功 (RS/LAG/$var 保留, last_written 清)",
                                  {"opc_url": opc_url})
                        consec_fail = 0
                    except Exception as e:
                        logger.warning(f"OPC 重连失败, 等 {BACKOFF_AFTER_FAIL}s 再试: {e}")
                        s.last_error = f"OPC 断连: {e}"
                        log_event("opc-err", f"✗ OPC 重连失败 (等 {BACKOFF_AFTER_FAIL}s 再试)",
                                  {"opc_url": opc_url, "error": str(e)})
                        await asyncio.sleep(BACKOFF_AFTER_FAIL)
                        continue   # 跳过本周期的求值/写入

                # 2. 计算每个 lhs 的目标值 (用本周期开头捕获的 current_pairs, 避免 mid-cycle swap 错乱)
                writes: dict = {}
                def _node_refs(expr):
                    if isinstance(expr, str) and not expr.startswith("$"):
                        yield expr
                    elif isinstance(expr, tuple):
                        for a in expr[1]:
                            yield from _node_refs(a)
                for lhs, rhs in current_pairs:
                    try:
                        v = _eval_rhs(rhs, val_by_node, s, dt)
                    except _SkipCycle:
                        # 整对静默失败 — 记录 lhs + 找出第一个 None 的源
                        s.skip_count[lhs] = s.skip_count.get(lhs, 0) + 1
                        if lhs not in s.skip_cause:
                            for node in _node_refs(rhs):
                                if val_by_node.get(node) is None:
                                    s.skip_cause[lhs] = node
                                    break
                            else:
                                # 全是中间变量没值
                                for a in (rhs[1] if isinstance(rhs, tuple) else []):
                                    if isinstance(a, str) and a.startswith("$") \
                                            and a not in s.intermediates:
                                        s.skip_cause[lhs] = a
                                        break
                        continue
                    if v is None:
                        continue
                    s.last_values[lhs] = v
                    if lhs.startswith("$"):
                        s.intermediates[lhs] = v
                    else:
                        # 模型实例不能写到 OPC 节点 — 用户大概率写错了
                        if isinstance(v, _CcsHandle):
                            s.skip_count[lhs] = s.skip_count.get(lhs, 0) + 1
                            s.skip_cause[lhs] = "模型实例只能绑到 $var (如 $YQ3=CCS_660(...)), 读管脚用 $YQ3.PST"
                            continue
                        writes[lhs] = v

                # 3. 批量写 (写规则: 实际值不等于目标才写, 避免被组态覆盖时永远不重试)
                def _eq(a, b):
                    """归一比较 (True/False/0/1 视为同类)"""
                    if a is None or b is None: return False
                    try:
                        na = 1 if a is True else 0 if a is False else float(a)
                        nb = 1 if b is True else 0 if b is False else float(b)
                        return abs(na - nb) < 0.01
                    except (TypeError, ValueError):
                        return str(a) == str(b)
                writes_changed = {}
                for lhs, v in writes.items():
                    actual = s.last_read.get(lhs)
                    if actual is not None and _eq(actual, v):
                        # DCS 实际值已经等于目标 → 跳过
                        continue
                    if actual is None and _eq(s.last_written.get(lhs), v):
                        # 读不回来 (如 SH 段), 退回 last_written 判定
                        continue
                    writes_changed[lhs] = v
                if writes_changed:
                    w_t0 = time.perf_counter()
                    write_results = await client.write_values(writes_changed)
                    write_ms = (time.perf_counter() - w_t0) * 1000
                    n_ok = 0
                    for lhs, ok in write_results.items():
                        if ok:
                            s.last_written[lhs] = writes_changed[lhs]
                            s.node_write_ok[lhs] = s.node_write_ok.get(lhs, 0) + 1
                            n_ok += 1
                        else:
                            s.node_write_fail[lhs] = s.node_write_fail.get(lhs, 0) + 1
                    s.write_count += n_ok

                # 4. 写后未生效持续判定 (NTVDPU ~1s 延迟, 持续 5 周期不一致才算)
                for lhs_n, want in s.last_values.items():
                    if isinstance(lhs_n, str) and lhs_n.startswith("$"):
                        continue
                    actual = s.last_read.get(lhs_n)
                    if actual is None:
                        s.ineffective_streak[lhs_n] = 0
                        continue
                    try:
                        w = 1 if want is True else 0 if want is False else float(want)
                        a = 1 if actual is True else 0 if actual is False else float(actual)
                        if abs(w - a) > 0.01:
                            s.ineffective_streak[lhs_n] = s.ineffective_streak.get(lhs_n, 0) + 1
                        else:
                            s.ineffective_streak[lhs_n] = 0
                    except (TypeError, ValueError):
                        s.ineffective_streak[lhs_n] = 0

                s.cycle_count += 1
                s.last_error = None
            except Exception as e:
                s.last_error = f"周期 #{s.cycle_count + 1} 异常: {e}"
                logger.warning(s.last_error)

            # 周期总耗时 + 滚动统计 (最近 20 周期)
            cycle_ms = (time.perf_counter() - cycle_t0) * 1000
            s.recent_read_ms.append(read_ms)
            s.recent_write_ms.append(write_ms)
            s.recent_cycle_ms.append(cycle_ms)
            if len(s.recent_cycle_ms) > 20:
                s.recent_read_ms.pop(0)
                s.recent_write_ms.pop(0)
                s.recent_cycle_ms.pop(0)

            # 节拍
            sleep_s = max(0, dt - cycle_ms / 1000)
            await asyncio.sleep(sleep_s)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
        logger.info(f"OPC 循环退出, 共 {s.cycle_count} 周期")


def swap_pairs(new_pairs: List[Tuple[str, Union[str, float]]]) -> dict:
    """在线下装 (hot swap): 原子替换 s.pairs, 不停 OPC 循环, 0 断流.

    机制:
        - _opc_loop 每周期开头都从 s.pairs 取最新引用 (`current_pairs = s.pairs`)
        - 引用变了 → 重算 read_set, 本周期就用新 pairs 算 LHS
        - GIL 保证 s.pairs = ... 是原子赋值, 不会读到半个 list

    没在跑时调用 → 等价于直接赋值 s.pairs, 下次点 ▶ 运行用它.

    状态影响:
        - 旧 LAG/RS key 在 lag_state/rs_state 里残留 (不被引用就不会更新, 无害)
        - 新 LAG/RS key 没锚定 → 按 _eval_rhs 默认 track-init (y_prev = 当前输入)
        - last_written 不动 (老 LHS 已经写过的 DCS 端值仍记着; 新 LHS 第 1 周期会强制写)
    """
    s = _STATE
    old_count = len(s.pairs)
    new_count = len(new_pairs)
    s.pairs = new_pairs   # GIL 原子赋值, _opc_loop 下个周期开头就拿到
    if s.running:
        log_event("run",
                  f"♻ 在线下装 ({old_count} → {new_count} 对, 0 断流)",
                  {"old_count": old_count, "new_count": new_count})
        return {"ok": True, "hot_swapped": True, "old_count": old_count,
                "new_count": new_count,
                "msg": f"♻ 在线下装 {old_count} → {new_count} 对, OPC 不停顿"}
    else:
        return {"ok": True, "hot_swapped": False, "old_count": old_count,
                "new_count": new_count,
                "msg": f"已替换 pairs ({old_count} → {new_count} 对, 待 ▶ 运行)"}


def start(pairs: List[Tuple[str, Union[str, float]]],
          dt: float = 0.2, opc_url: Optional[str] = None) -> Tuple[bool, str]:
    s = _STATE
    if s.running:
        return False, "已在运行,请先停止"
    s.pairs = pairs
    s.dt = dt
    # 优先用调用方指定的 URL; 否则按 endpoint 配置 (mode=local|vm) 解析
    s.opc_url = opc_url or get_endpoint_config()["url"]
    # 运行时计数清零, 但持久状态 (rs/lag/中间变量) 不清 —
    # 用户在线组态/热重启脚本时, RS 锁存等保持的量不丢
    s.cycle_count = 0
    s.read_count = 0
    s.write_count = 0
    s.last_error = None
    # 失败统计每次启动重新累计
    s.node_read_fail = {}
    s.node_write_fail = {}
    s.node_write_ok = {}
    s.skip_count = {}
    s.skip_cause = {}
    s.ineffective_streak = {}
    s.started_at = time.time()
    s.running = True
    # 持久状态: rs_state / lag_state / intermediates / last_values / last_read / last_written
    # 均保留. 改脚本后, 旧 key 残留无害 (新脚本用新 key)

    def _runner():
        try:
            s.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(s.loop)
            s.loop.run_until_complete(_opc_loop(s.pairs, s.dt, s.opc_url))
        finally:
            if s.loop:
                s.loop.close()
            s.loop = None
            s.running = False

    s.thread = threading.Thread(target=_runner, daemon=True, name="opc-runtime")
    s.thread.start()
    log_event("run", f"▶ 启动 OPC 循环 ({len(pairs)} 对, dt={dt*1000:.0f}ms)",
              {"pairs": len(pairs), "dt_ms": dt * 1000, "opc_url": s.opc_url})
    return True, f"已启动 {len(pairs)} 对,周期 {dt*1000:.0f}ms"


def stop() -> Tuple[bool, str]:
    s = _STATE
    if not s.running:
        return False, "未在运行"
    s.running = False
    log_event("stop", f"■ 停止 OPC 循环 (执行 {s.cycle_count} 周期)",
              {"cycles": s.cycle_count})
    # 等线程退出(最多 2 秒)
    if s.thread:
        s.thread.join(timeout=2.0)
    return True, f"已停止,共执行 {s.cycle_count} 周期"


# ---------- 从配对结果生成脚本骨架 ----------

def generate_script_from_tagmap(tagmap_yaml_path: str = "") -> str:
    """按工艺规则从点表生成 DSL 脚本草稿.

    规则在 drivers/*.yaml, 引擎在 src/viewer/gen/。入参 tagmap_yaml_path
    保留兼容旧 API, 当前被忽略; 生成器统一走当前工程 proj.paths()。
    """
    from .gen import generate
    return generate(proj.paths())

def _node_to_short(full_node: str) -> str:
    """ns=0;s=DPU3013.HW.AQ010101.PV → DPU3013.AQ010101"""
    if not full_node.startswith("ns="):
        return full_node
    # 取 s= 后面
    body = full_node.split(";s=", 1)[1] if ";s=" in full_node else full_node
    # body = DPU3013.HW.AQ010101.PV
    parts = body.split(".")
    if len(parts) >= 4 and parts[1] == "HW" and parts[-1] == "PV":
        return f"{parts[0]}.{parts[2]}"
    return full_node
