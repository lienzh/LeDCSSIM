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
import threading
import time
from pathlib import Path
from typing import List, Tuple, Optional, Union

from src.opc_client.client import OPCClient

logger = logging.getLogger(__name__)

# 默认 OPC URL(可被 set_url 覆盖)
DEFAULT_OPC_URL = "opc.tcp://localhost:9440"

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


# 支持的函数 + 参数个数
FUNC_ARITY = {
    # 锁存/逻辑
    "RS": 2, "RS_NOT": 2, "NOT": 1, "AND": 2, "OR": 2,
    # 算术 (参数: 常数 or 节点)
    "ADD": 2, "SUB": 2, "MUL": 2, "DIV": 2,
    # 取值
    "MAX": 2, "MIN": 2, "LIMIT": 3,
    # 选择: SEL(cond, a, b) — cond 真则 a, 否则 b
    "SEL": 3,
    # 一阶滞后: LAG(x, T) — y[k] = y[k-1] + dt/T*(x - y[k-1]), 时间常数 T 秒
    "LAG": 2,
}
SUPPORTED_FUNCS = tuple(FUNC_ARITY.keys())


def _parse_arg(s: str):
    """解析一个参数:常数(float)或 OPC 节点(str)"""
    s = _strip_paren_label(s)
    try:
        return float(s)
    except ValueError:
        return short_to_full(s)


def _parse_rhs(rhs_raw: str):
    """解析右边:数字 / OPC tag / 函数调用
       函数 → ('FNAME', [arg1, arg2, ...]),arg 是 float 或 str(node)
       不支持嵌套(每个参数必须是常数或节点)
    """
    rhs = rhs_raw.strip()
    m = re.match(r'^([A-Z][A-Z_]*)\s*\((.+)\)\s*$', rhs, re.DOTALL)
    if m:
        fname = m.group(1)
        if fname not in FUNC_ARITY:
            raise ParseError(f"未知函数 {fname!r}(支持: {', '.join(SUPPORTED_FUNCS)})")
        args = _split_top_commas(m.group(2))
        expected = FUNC_ARITY[fname]
        if len(args) != expected:
            raise ParseError(f"{fname} 需要 {expected} 个参数, 实际 {len(args)}")
        return (fname, [_parse_arg(a) for a in args])
    return _parse_arg(rhs)


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
        # 左边: 剥描述 → OPC 节点
        try:
            lhs_full = short_to_full(_strip_paren_label(lhs_raw))
        except ValueError as e:
            raise ParseError(f"第 {ln_no} 行左边无效: {e}")
        # 右边: 数字 / OPC 节点 / 函数 (如 RS(a, b))
        try:
            rhs_val = _parse_rhs(rhs_raw)
        except (ValueError, ParseError) as e:
            raise ParseError(f"第 {ln_no} 行右边无效: {e}")
        result.append((lhs_full, rhs_val))
    return result


# ---------- 求值 ----------

class _SkipCycle(Exception):
    """本周期数据不全, 跳过该 lhs 的写"""
    pass


def _resolve(arg, val_by_node: dict, s=None):
    """参数 → 实际值。
       str 以 $ 开头  → 中间变量 (从 s.intermediates 取)
       str 是节点      → OPC 读值
       float           → 常数直返
    """
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


def _eval_rhs(rhs, val_by_node: dict, s, dt: float):
    """计算右边表达式的当前值。
    rhs 形态:
      - str(节点):直接读
      - float/int:常数
      - (fname, [args]):函数调用
    """
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
    vals = [_resolve(a, val_by_node, s) for a in raw_args]

    # 锁存/逻辑
    if fname in ("RS", "RS_NOT"):
        set_v, reset_v = vals
        key = ("RS", tuple(raw_args))
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
    # 算术
    a, b = (float(vals[0]), float(vals[1])) if len(vals) >= 2 else (0.0, 0.0)
    if fname == "ADD": return a + b
    if fname == "SUB": return a - b
    if fname == "MUL": return a * b
    if fname == "DIV": return a / b if b != 0 else 0.0
    if fname == "MAX": return max(a, b)
    if fname == "MIN": return min(a, b)
    # 限幅
    if fname == "LIMIT":
        x, lo, hi = float(vals[0]), float(vals[1]), float(vals[2])
        return max(lo, min(hi, x))
    # 选择
    if fname == "SEL":
        return vals[1] if bool(vals[0]) else vals[2]
    # 一阶滞后 — 冷启动 y0=0, 输入阶跃后按时间常数 T 爬升
    if fname == "LAG":
        x, T = float(vals[0]), float(vals[1])
        key = ("LAG", tuple(raw_args))
        y_prev = s.lag_state.get(key, 0.0)
        y = y_prev + (dt / T) * (x - y_prev) if T > 0 else x
        s.lag_state[key] = y
        return y
    return None


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
        # 上次写入值 (用于跳过未变化的写,降低通讯负荷)
        self.last_written: dict = {}


_STATE = _State()


def get_status() -> dict:
    s = _STATE
    avg = lambda lst: (sum(lst) / len(lst)) if lst else 0.0
    avg_read = avg(s.recent_read_ms)
    avg_write = avg(s.recent_write_ms)
    avg_cycle = avg(s.recent_cycle_ms)
    dt_ms = s.dt * 1000
    load_pct = (avg_cycle / dt_ms * 100) if dt_ms > 0 else 0.0
    return {
        "running": s.running,
        "cycle_count": s.cycle_count,
        "read_count": s.read_count,
        "write_count": s.write_count,
        "last_error": s.last_error,
        "started_at": s.started_at,
        "uptime_s": (time.time() - s.started_at) if s.started_at else 0,
        "pairs_count": len(s.pairs),
        "dt": s.dt,
        "opc_url": s.opc_url,
        "last_values": s.last_values,
        "last_read": s.last_read,
        # 实时负荷指标 (最近 20 周期平均)
        "avg_read_ms": round(avg_read, 1),
        "avg_write_ms": round(avg_write, 1),
        "avg_cycle_ms": round(avg_cycle, 1),
        "dt_ms": round(dt_ms, 0),
        "load_pct": round(load_pct, 1),
    }


async def _opc_loop(pairs: List[Tuple[str, Union[str, float]]], dt: float,
                    opc_url: str):
    s = _STATE
    client = OPCClient(opc_url)
    try:
        await client.connect(retry_count=3, retry_interval=2.0)
    except Exception as e:
        s.last_error = f"OPC 连接失败: {e}"
        s.running = False
        return
    logger.info(f"OPC 已连接, 进入循环 dt={dt}s, {len(pairs)} 对")

    # 预解析:所有右边引用的 OPC 节点 (去重)
    # 中间变量 ($xxx) 跳过 — 不读 OPC
    read_set = set()
    for _, rhs in pairs:
        if isinstance(rhs, str):
            if not rhs.startswith("$"):
                read_set.add(rhs)
        elif isinstance(rhs, tuple):
            for a in rhs[1]:
                if isinstance(a, str) and not a.startswith("$"):
                    read_set.add(a)
    read_nodes = list(read_set)

    try:
        while s.running:
            cycle_t0 = time.perf_counter()
            read_ms = 0.0
            write_ms = 0.0
            try:
                # 1. 批量读 (计时)
                if read_nodes:
                    r_t0 = time.perf_counter()
                    raw = await client.read_values(read_nodes)
                    read_ms = (time.perf_counter() - r_t0) * 1000
                    val_by_node = dict(zip(read_nodes, raw))
                    # 同步到 last_read (只保留非 None)
                    s.last_read = {k: v for k, v in val_by_node.items() if v is not None}
                    s.read_count += len(read_nodes)
                else:
                    val_by_node = {}

                # 2. 计算每个 lhs 的目标值
                #    LHS 是 $xxx 中间变量 → 存 intermediates, 不进 writes
                #    LHS 是 OPC 节点 → 进 writes
                writes: dict = {}
                for lhs, rhs in pairs:
                    try:
                        v = _eval_rhs(rhs, val_by_node, s, dt)
                    except _SkipCycle:
                        continue
                    if v is None:
                        continue
                    s.last_values[lhs] = v
                    if lhs.startswith("$"):
                        s.intermediates[lhs] = v
                    else:
                        writes[lhs] = v

                # 3. 批量写 (只写变化的 + 失败的重试)
                #    - 值跟上次一样且上次成功 → 跳过
                #    - 值变化 / 上次失败 → 写
                writes_changed = {lhs: v for lhs, v in writes.items()
                                  if s.last_written.get(lhs) != v}
                if writes_changed:
                    w_t0 = time.perf_counter()
                    write_results = await client.write_values(writes_changed)
                    write_ms = (time.perf_counter() - w_t0) * 1000
                    n_ok = 0
                    for lhs, ok in write_results.items():
                        if ok:
                            s.last_written[lhs] = writes_changed[lhs]
                            n_ok += 1
                        # 失败的不进 last_written, 下周期自动重试
                    s.write_count += n_ok

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


def start(pairs: List[Tuple[str, Union[str, float]]],
          dt: float = 0.2, opc_url: Optional[str] = None) -> Tuple[bool, str]:
    s = _STATE
    if s.running:
        return False, "已在运行,请先停止"
    s.pairs = pairs
    s.dt = dt
    s.opc_url = opc_url or DEFAULT_OPC_URL
    s.cycle_count = 0
    s.read_count = 0
    s.write_count = 0
    s.last_error = None
    s.last_values = {}
    s.intermediates = {}
    s.rs_state = {}
    s.lag_state = {}
    s.started_at = time.time()
    s.running = True

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
    return True, f"已启动 {len(pairs)} 对,周期 {dt*1000:.0f}ms"


def stop() -> Tuple[bool, str]:
    s = _STATE
    if not s.running:
        return False, "未在运行"
    s.running = False
    # 等线程退出(最多 2 秒)
    if s.thread:
        s.thread.join(timeout=2.0)
    return True, f"已停止,共执行 {s.cycle_count} 周期"


# ---------- 从配对结果生成脚本骨架 ----------

def _recommend_cmd(fb_pt: dict, cmd_pts: list, threshold: float = 0.55):
    """
    为未配对反馈推荐最可能的指令 (基于 KKS 前缀 + 描述相似度)

    返回 (best_cmd_pt, score) 或 (None, 0)
    """
    from difflib import SequenceMatcher
    fb_desc = fb_pt.get("desc", "") or ""
    fb_kks = fb_pt.get("kks", "") or ""
    # 反馈描述中去掉常见反馈词,留下信号本体
    fb_core = (fb_desc.replace("位置", "").replace("反馈", "")
                       .replace("运行", "").replace("状态", "")
                       .replace("开", "").replace("关", "").strip())
    best, best_score = None, 0.0
    for c in cmd_pts:
        score = 0.0
        c_kks = c.get("kks", "") or ""
        c_desc = c.get("desc", "") or ""
        # KKS 设备根 (前 9 位: 30HAG21AA) 完全相同 = 极强信号
        if fb_kks[:12] and c_kks[:12] and fb_kks[:12] == c_kks[:12]:
            score += 0.6
        # KKS 前 5 位相同 (30HAG)
        elif fb_kks[:5] and c_kks[:5] and fb_kks[:5] == c_kks[:5]:
            score += 0.25
        # 描述相似度(去掉反馈词后)
        if fb_core and c_desc:
            c_core = (c_desc.replace("指令", "").replace("输出", "")
                            .replace("开", "").replace("关", "").strip())
            sim = SequenceMatcher(None, fb_core, c_core).ratio()
            score += sim * 0.5
        if score > best_score:
            best, best_score = c, score
    return (best, best_score) if best_score >= threshold else (None, best_score)


_FAULT_WORDS = ("故障", "告警", "报警", "异常", "失败", "失效", "损坏",
                "保护", "跳闸", "越限", "停电", "断线", "误操作")
_REMOTE_WORDS = ("远方", "远控", "就地远方", "投运允许", "允许投运",
                 "可用", "就绪", "准备好")
_RUN_WORDS = ("运行", "在运行", "运转")
_START_WORDS = ("启动", "开启", "合闸", "投入", "启泵", "开阀", "开 ", "开机",
                "启 ")  # 含 "启 X设备" 短指令
_STOP_WORDS = ("停止", "停机", "停车", "停泵", "关闭", "分闸", "停运", "切除",
               "跳闸", "跳机", "跳泵", "关 ", "关阀",
               "停 ", "停E", "停F", "停A", "停B", "停C", "停D",  # 短指令"停 X设备"
               "停#")
# 设备类型分段关键词
_VALVE_REG_WORDS = ("调节", "调整门", "调整阀", "动叶", "调门", "风门", "调节阀")
_MOTOR_WORDS = ("电机", "泵", "风机", "机组")
_VALVE_ONOFF_WORDS = ("阀门", "气动阀", "电动阀", "气动快关", "电磁阀")
# 柜间通讯关键词 (MEH/DEH/DCS 之间)
_GATEWAY_WORDS = ("MEH", "DEH", "TO CCS", "来自DPU", "至PECCS", "来自PECCS",
                  "至CCS", "至DCS", "来自DCS", "柜间", "对侧", "跨DPU")

# ====== 白名单 ======
# 全局电机 exclude (所有电机共用 — 阀门/门/油泵等配套设备不算电机本体)
_COMMON_MOTOR_EXCLUDE = [
    "动叶", "调节阀", "调门", "风门",
    "出口", "进口", "气动门", "电动门", "插板",
    "出口风门", "进口风门",
    "润滑油", "液压油", "油泵", "油箱", "油站",
    "电加热", "加热器", "冷却风", "循环冷却",
    "失速",
    "联络",   # BC 联络给煤机等桥接设备 — 非主体
]

# 电机设备 (开关量,DI=RS) - 7 类
MOTOR_DEVICES = [
    {"name": "送风机",   "include": ["送风机"],   "exclude": list(_COMMON_MOTOR_EXCLUDE)},
    {"name": "引风机",   "include": ["引风机"],   "exclude": list(_COMMON_MOTOR_EXCLUDE)},
    {"name": "一次风机", "include": ["一次风机"], "exclude": list(_COMMON_MOTOR_EXCLUDE)},
    {"name": "给煤机",   "include": ["给煤机"],   "exclude": list(_COMMON_MOTOR_EXCLUDE)},
    {"name": "磨煤机",   "include": ["磨煤机"],   "exclude": list(_COMMON_MOTOR_EXCLUDE)},
    {"name": "前置泵",   "include": ["前置泵"],   "exclude": list(_COMMON_MOTOR_EXCLUDE)},
    {"name": "凝结水泵", "include": ["凝结水泵", "凝水泵"], "exclude": list(_COMMON_MOTOR_EXCLUDE)},
]
# 阀门设备 (模拟量,AI=AQ) - 5 类
VALVE_DEVICES = [
    {"name": "除氧器主调节阀", "include": ["除氧器主调节阀", "除氧主调节阀", "除氧器主调阀", "除氧主调阀"]},
    {"name": "除氧器副调节阀", "include": ["除氧器副调节阀", "除氧副调节阀", "除氧器副调阀", "除氧副调阀"]},
    {"name": "送风机动叶",     "include": ["送风机动叶"]},
    {"name": "引风机动叶",     "include": ["引风机动叶"]},
    {"name": "一次风机动叶",   "include": ["一次风机动叶"]},
]


def _match_device(desc, specs):
    """描述匹配到的白名单设备 spec, 或 None"""
    if not desc: return None
    for spec in specs:
        if any(w in desc for w in spec["include"]):
            if not any(w in desc for w in spec.get("exclude", [])):
                return spec
    return None


def _device_instance(desc, spec):
    """从描述里提取实例标识 (A/B/3A/...) - 取 include 关键词之前的最后字母数字"""
    import re
    if not desc: return ""
    for inc_word in spec["include"]:
        idx = desc.find(inc_word)
        if idx >= 0:
            prefix = desc[:idx].rstrip()
            m = re.search(r'([A-Z#0-9]{1,4})$', prefix)
            return m.group(1) if m else ""
    return ""


def _classify_device_section(fb_pt):
    """返回反馈点所属的设备分段名"""
    desc = fb_pt.get("desc", "") or ""
    code = fb_pt["code"]
    if any(k in desc for k in _FAULT_WORDS):
        return "故障/告警"
    if any(k in desc for k in _REMOTE_WORDS):
        return "远方/允许"
    if code == "AI":
        if any(k in desc for k in _VALVE_REG_WORDS):
            return "调节机构(动叶/调门/风门)"
        return "其他模拟量(传感器/反馈)"
    # DI
    if any(k in desc for k in _RUN_WORDS):
        return "电机/泵/风机"
    if any(k in desc for k in _VALVE_ONOFF_WORDS) or "阀" in desc:
        return "开关阀(气动/电动)"
    return "其他数字量(状态/位置)"


def _classify_feedback(fb_pt, dpu, dq_by_dpu_kks, aq_by_dpu, auto_pair_cmd, all_points):
    """
    工艺规则分类反馈点 → 返回赋值决策

    返回 ('rule_name', 描述, rhs_str) — rhs_str 是要写到脚本右边的字符串
    """
    desc = fb_pt.get("desc", "") or ""
    code = fb_pt["code"]
    kks_root = (fb_pt.get("kks", "") or "")[:12]

    # 规则 1: 故障类 → 0
    if any(k in desc for k in _FAULT_WORDS):
        return ("fault", "故障类→0", "0")
    # 规则 2: 远方/允许类 → 1
    if any(k in desc for k in _REMOTE_WORDS):
        return ("remote", "远方/允许→1", "1")
    # 规则 3: DI 运行类 → RS(启动, 停止)
    if code == "DI" and any(k in desc for k in _RUN_WORDS):
        candidates = dq_by_dpu_kks.get((dpu, kks_root), [])
        start, stop = None, None
        for c in candidates:
            cd = c.get("desc", "") or ""
            # 不能同时含 启 和 停
            is_start = any(k in cd for k in _START_WORDS) and not any(k in cd for k in _STOP_WORDS)
            is_stop = any(k in cd for k in _STOP_WORDS) and not any(k in cd for k in _START_WORDS)
            if is_start and start is None:
                start = c
            elif is_stop and stop is None:
                stop = c
        if start and stop:
            return ("rs", "运行→RS(启动,停止)",
                    f"RS({_fmt_node(dpu, start)}, {_fmt_node(dpu, stop)})")
    # 规则 4: 已自动配对(同 KKS) → 直通
    cmd_name = auto_pair_cmd.get((dpu, fb_pt["name"]))
    if cmd_name:
        cmd_pt = next((c for c in all_points[dpu] if c["name"] == cmd_name), None)
        if cmd_pt:
            return ("pair", "自动配对", _fmt_node(dpu, cmd_pt))
    # 规则 5: AI 推荐(注释行 — 需人工审核)
    if code == "AI":
        cand, sc = _recommend_cmd(fb_pt, aq_by_dpu.get(dpu, []), threshold=0.55)
        if cand:
            return ("recommend", f"推荐 {sc*100:.0f}%", _fmt_node(dpu, cand))
    # 兜底: 0
    return ("default", "兜底→0", "0")


def _fmt_node(dpu, pt):
    """点对象 → 'DPU3013.AI010502(描述)' 格式"""
    short = pt["name"].replace("HW.", "").replace(".PV", "")
    desc = (pt.get("desc", "") or "").replace("(", "[").replace(")", "]")
    return f"{dpu}.{short}({desc})" if desc else f"{dpu}.{short}"


def generate_script_from_tagmap(tagmap_yaml_path: str) -> str:
    """
    生成按工艺规则自动分类的赋值脚本

    规则优先级:
      1. 故障/告警/异常类 → 反馈 = 0
      2. 远方/允许/就绪类 → 反馈 = 1
      3. DI 运行类 + 同设备启/停 DQ → 反馈 = RS(启动指令, 停止指令)
      4. 已自动配对(同 KKS AQ↔AI / DQ↔DI) → 反馈 = 指令
      5. AI 模糊推荐 → 注释行(审核后激活)
      6. 兜底 → 反馈 = 0

    `# 推荐` 注释行需用户取消行首 # 才激活
    """
    import csv as _csv
    import glob as _glob
    from src.sim_engine.io_pairing_gen import (
        load_points, pair_analog, pair_digital, is_soft, DEV,
    )
    # 优先用"简化"目录的 *_S.csv, 找不到回退老路径
    SIMPLE_DIR = Path("YQ3SIM-IO/SIMPLE/简化")
    csv_files = sorted(SIMPLE_DIR.glob("*[_-]S.csv"))
    if csv_files:
        def _dpu_of(p): return "DPU" + p.stem.replace("_S","").replace("-S","")
    else:
        csv_files = sorted(Path("YQ3SIM-IO").glob("DPU*.csv"))
        csv_files = [p for p in csv_files if "_" not in p.stem]
        def _dpu_of(p): return p.stem

    DPU_SCOPE = sorted({_dpu_of(p) for p in csv_files})
    csv_by_dpu = {_dpu_of(p): p for p in csv_files}

    # 全部点 + 描述
    desc_map = {}     # {(dpu, name): desc}
    all_points = {}   # {dpu: [points...]} - load_points 风格
    for dpu in DPU_SCOPE:
        fn = csv_by_dpu.get(dpu)
        if not fn or not fn.exists():
            continue
        for ln in fn.read_bytes().decode("gbk", errors="replace").splitlines()[2:]:
            try: r = next(_csv.reader([ln]))
            except: continue
            if len(r) > 2:
                desc_map[(dpu, r[1].strip())] = r[2].strip()
        all_points[dpu] = load_points(str(fn))

    # 配对
    pairs_a, pairs_d = [], []
    for dpu, pts in all_points.items():
        pairs_a.extend(pair_analog(pts, dpu))
        pairs_d.extend(pair_digital(pts, dpu))

    # 已配对点集合 (用于过滤未配对)
    paired = set()
    for p in pairs_a + pairs_d:
        paired.add((p["dpu"], p["cmd"]))
        paired.add((p["dpu"], p["fb"]))

    # 同 DPU 的全部指令池(用于推荐候选)
    cmds_by_dpu = {dpu: {"AQ": [], "DQ": []} for dpu in DPU_SCOPE}
    for dpu, pts in all_points.items():
        for p in pts:
            if p["code"] in ("AQ", "DQ") and not is_soft(p):
                cmds_by_dpu[dpu][p["code"]].append(p)

    # 未配对的硬件 IO (只看 AI/DI 反馈端)
    unpaired_by_dpu = {dpu: {"AI": [], "DI": []} for dpu in DPU_SCOPE}
    for dpu, pts in all_points.items():
        for p in pts:
            if p["code"] not in ("AI", "DI"): continue
            if is_soft(p): continue
            if (dpu, p["name"]) in paired: continue
            unpaired_by_dpu[dpu][p["code"]].append(p)

    # 构造输出
    lines = [
        "# 赋值脚本 — 反馈(信号名) = 指令(信号名)",
        "# 括号内为信号名称, 仅作可读性, 解析时自动忽略",
        "# 修改 / 注释 / 新增按需; 短码 'DPU3013.AI010502' 自动展开为 ns=0;s=DPU3013.HW.AI010502.PV",
        "",
    ]

    def short(name_full: str) -> str:
        return name_full.replace("HW.", "").replace(".PV", "")

    def fmt(dpu, full_name):
        desc = desc_map.get((dpu, full_name), "")
        body = f"{dpu}.{short(full_name)}"
        clean = desc.replace("(", "[").replace(")", "]")
        return f"{body}({clean})" if clean else body

    # 同 DPU 同 KKS 设备根的 DQ 池 (找 RS 启停)
    dq_by_dpu_kks = {}
    for dpu, pts in all_points.items():
        for p in pts:
            if p["code"] == "DQ" and not is_soft(p):
                kr = (p.get("kks", "") or "")[:12]
                if kr:
                    dq_by_dpu_kks.setdefault((dpu, kr), []).append(p)

    # 已自动配对查找: {(dpu, fb_name): cmd_name}
    auto_pair_cmd = {}
    for p in pairs_a + pairs_d:
        auto_pair_cmd[(p["dpu"], p["fb"])] = p["cmd"]

    # 同 DPU 全部 AQ 池(给 AI 推荐用)
    aq_by_dpu = {dpu: [p for p in pts if p["code"] == "AQ" and not is_soft(p)]
                 for dpu, pts in all_points.items()}

    # === 按 KKS 设备分组生成 ===
    # 每个设备 (DPU, KKS_root) → 组装本设备所有相关信号
    from collections import defaultdict
    OPEN_FB_WORDS = ("运行", "在运行", "开到位", "已开", "在开", "开反馈", "全开",
                     "开位", "运转", "合位", "开关合", "投运")
    CLOSE_FB_WORDS = ("关到位", "已关", "关反馈", "全关", "关位", "停止反馈", "停运",
                      "停止", "停机", "已停", "停泵", "停车", "跳位", "开关跳")
    LOCAL_WORDS = ("在就地", "就地控制", "就地操作")   # 反义远方 = 0

    # 排除"保护跳闸"等非手动操作指令 — 不能作为 RS 的 set/reset
    CMD_EXCLUDE_WORDS = ("FSSS", "MFT", "保护跳闸", "联锁跳闸", "SOE",
                          "来自DPU", "DCS送出", "RB", "RB-",
                          "保护动作", "保护输出", "事故")

    def _is_real_cmd(desc):
        return not any(k in desc for k in CMD_EXCLUDE_WORDS)

    def _is_open_cmd(desc):
        d = (desc or "").strip()
        if not _is_real_cmd(d): return False
        # 开头模式: "启A给煤机" / "开X电动门" — 首字 启/开,且第二字非空格
        if len(d) >= 2 and d[0] in ("启", "开") and d[1] != " ":
            return True
        return (any(k in d for k in _START_WORDS) and
                not any(k in d for k in _STOP_WORDS))

    def _is_close_cmd(desc):
        d = (desc or "").strip()
        if not _is_real_cmd(d): return False
        # 开头模式: "停A给煤机" / "关X电动门"
        if len(d) >= 2 and d[0] in ("停", "关") and d[1] != " ":
            return True
        return (any(k in d for k in _STOP_WORDS) and
                not any(k in d for k in _START_WORDS))

    def _should_skip_pt(pt):
        """跳过: 软点(备用等) / 空描述"""
        if is_soft(pt): return True
        desc = (pt.get("desc", "") or "").strip()
        if not desc: return True
        return False

    # === 按 KKS 设备根分组 (前 9 位) + 白名单仅过滤设备类型 ===
    # 同 KKS 下的指令和反馈自动绑;不同 KKS (如主体 vs FSSS 输出) 自动隔离
    motor_groups = defaultdict(lambda: {"DQ": [], "DI": [], "AQ": [], "AI": [], "spec": None})
    valve_groups = defaultdict(lambda: {"AQ": [], "AI": [], "DI": [], "DQ": [], "spec": None})

    for dpu, pts in all_points.items():
        for p in pts:
            if _should_skip_pt(p): continue
            desc = (p.get("desc","") or "").strip()
            if p["code"] not in ("DQ", "DI", "AQ", "AI"): continue
            kks_root = (p.get("kks","") or "")[:9]
            if not kks_root: continue   # 无 KKS 跳过

            # 白名单匹配 (描述必须含白名单关键词)
            v_spec = _match_device(desc, VALVE_DEVICES)
            if v_spec:
                key = (dpu, kks_root)
                valve_groups[key][p["code"]].append(p)
                if valve_groups[key]["spec"] is None:
                    valve_groups[key]["spec"] = v_spec
                continue
            m_spec = _match_device(desc, MOTOR_DEVICES)
            if m_spec:
                key = (dpu, kks_root)
                motor_groups[key][p["code"]].append(p)
                if motor_groups[key]["spec"] is None:
                    motor_groups[key]["spec"] = m_spec

    SECTION_ORDER = [
        ("电机设备层 (开关量, RS 触发器)", "motor"),
        ("阀门设备层 (模拟量, AI 直通)", "valve"),
        ("柜间通讯 (MEH / DEH / DCS) - 待实现", "gateway"),
        ("模型层 - 待实现", "model"),
    ]
    sections = {key: [] for _, key in SECTION_ORDER}

    stats = defaultdict(int)
    consumed_fb = set()

    def fmt_pt(dpu, pt):
        return fmt(dpu, pt["name"])

    def _device_name(pts):
        """从同设备所有点的描述中提取公共前缀作为设备名"""
        descs = [(p.get("desc","") or "").strip() for p in pts if (p.get("desc","") or "").strip()]
        if not descs: return ""
        # 取最长公共前缀
        prefix = descs[0]
        for d in descs[1:]:
            while not d.startswith(prefix):
                prefix = prefix[:-1]
                if not prefix: return descs[0][:20]
        # 截掉末尾的标点/数字/编号(如 "A引风机#1 " → "A引风机")
        prefix = prefix.rstrip(" #0123456789()（）-")
        return prefix if len(prefix) >= 2 else descs[0][:20]

    # === 段 1: 电机设备层 ===
    for key in sorted(motor_groups.keys()):
        dpu, kks_root = key
        dev = motor_groups[key]
        dis = dev["DI"]; dqs = dev["DQ"]
        if not dis: continue
        dev_name = dev["spec"]["name"] if dev.get("spec") else ""
        inst = _device_instance((dis[0].get("desc","") or ""), dev["spec"]) if dev.get("spec") else ""
        # 找开/关指令
        open_cmd = next((c for c in dqs if _is_open_cmd(c.get("desc",""))), None)
        close_cmd = next((c for c in dqs if _is_close_cmd(c.get("desc",""))), None)
        block_lines = []
        for di in dis:
            d = di.get("desc","") or ""
            lhs = fmt_pt(dpu, di)
            # 跳过常数赋值
            if any(k in d for k in _FAULT_WORDS): stats["skip_fault"]+=1; continue
            if any(k in d for k in LOCAL_WORDS): stats["skip_local"]+=1; continue
            if any(k in d for k in _REMOTE_WORDS): stats["skip_remote"]+=1; continue
            if any(k in d for k in CLOSE_FB_WORDS) and not any(k in d for k in OPEN_FB_WORDS):
                if open_cmd and close_cmd:
                    block_lines.append(f"{lhs} = RS_NOT({fmt_pt(dpu, open_cmd)}, {fmt_pt(dpu, close_cmd)})     # 关反馈 = !RS")
                    stats["rs_not"] += 1; consumed_fb.add((dpu, di["name"]))
                elif close_cmd:
                    block_lines.append(f"{lhs} = {fmt_pt(dpu, close_cmd)}     # 关反馈 = 关指令")
                    stats["single_close"] += 1; consumed_fb.add((dpu, di["name"]))
                elif open_cmd:
                    block_lines.append(f"{lhs} = NOT({fmt_pt(dpu, open_cmd)})     # 关反馈 = !开指令")
                    stats["single_close"] += 1; consumed_fb.add((dpu, di["name"]))
                else:
                    stats["skip_no_cmd"] += 1
            elif any(k in d for k in OPEN_FB_WORDS) or any(k in d for k in _RUN_WORDS):
                if open_cmd and close_cmd:
                    block_lines.append(f"{lhs} = RS({fmt_pt(dpu, open_cmd)}, {fmt_pt(dpu, close_cmd)})     # 开反馈 = RS")
                    stats["rs"] += 1; consumed_fb.add((dpu, di["name"]))
                elif open_cmd:
                    block_lines.append(f"{lhs} = {fmt_pt(dpu, open_cmd)}     # 开反馈 = 开指令")
                    stats["single_open"] += 1; consumed_fb.add((dpu, di["name"]))
                elif close_cmd:
                    block_lines.append(f"{lhs} = NOT({fmt_pt(dpu, close_cmd)})     # 开反馈 = !关指令")
                    stats["single_open"] += 1; consumed_fb.add((dpu, di["name"]))
                else:
                    stats["skip_no_cmd"] += 1
            else:
                stats["skip_other"] += 1
        if block_lines:
            title = f"{inst}{dev_name}" if inst else dev_name
            sections["motor"].append(f"# --- {title} @ {dpu} (KKS:{kks_root}) ---")
            sections["motor"].extend(block_lines)
            sections["motor"].append("")

    # === 段 2: 阀门设备层 (AI = AQ 直通) ===
    for key in sorted(valve_groups.keys()):
        dpu, kks_root = key
        dev = valve_groups[key]
        ais = dev["AI"]; aqs = dev["AQ"]
        if not ais: continue
        dev_name = dev["spec"]["name"] if dev.get("spec") else ""
        inst = _device_instance((ais[0].get("desc","") or ""), dev["spec"]) if dev.get("spec") else ""
        ai_lines = []
        for ai in ais:
            cmd_name = auto_pair_cmd.get((dpu, ai["name"]))
            if cmd_name:
                cmd_pt = next((c for c in aqs if c["name"] == cmd_name), None)
                if cmd_pt:
                    ai_lines.append(f"{fmt_pt(dpu, ai)} = {fmt_pt(dpu, cmd_pt)}     # AI 直通")
                    stats["pair"] += 1; consumed_fb.add((dpu, ai["name"]))
        # 如果没自动配对,尝试同设备下唯一 AQ
        if not ai_lines and len(aqs) == 1:
            for ai in ais:
                ai_lines.append(f"{fmt_pt(dpu, ai)} = {fmt_pt(dpu, aqs[0])}     # AI 直通(单 AQ 假设)")
                stats["pair"] += 1; consumed_fb.add((dpu, ai["name"]))
        if ai_lines:
            title = f"{inst}{dev_name}" if inst else dev_name
            sections["valve"].append(f"# --- {title} @ {dpu} (KKS:{kks_root}) ---")
            sections["valve"].extend(ai_lines)
            sections["valve"].append("")

    # === 柜间通讯段 (扫所有点,不管 is_soft 过滤,识别柜间关键词) ===
    # 柜间点通常在 _GATEWAY_WORDS 描述里,可能被 is_soft 过滤掉,要单独识别
    import csv as _csv2
    gateway_by_dpu = {}
    for dpu in DPU_SCOPE:
        fn = csv_by_dpu.get(dpu)
        if not fn or not fn.exists(): continue
        gw = []
        for ln in fn.read_bytes().decode("gbk", errors="replace").splitlines()[2:]:
            try: r = next(_csv2.reader([ln]))
            except: continue
            if len(r) < 3: continue
            name = r[1].strip()
            desc = r[2].strip()
            if not name.startswith("HW.") or not name.endswith(".PV"): continue
            if (dpu, name) in consumed_fb: continue
            if not desc or "备用" in desc: continue   # 空描述/备用 — 跳过
            if any(k in desc for k in _GATEWAY_WORDS):
                gw.append((name, desc))
        if gw:
            gateway_by_dpu[dpu] = gw

    sections["gateway"].append("# (柜间通讯 — MEH/DEH/DCS 之间通讯点,待用户细化白名单后实现)")
    sections["gateway"].append("# 目前发现的潜在柜间点 (注释 — 仅参考):")
    total_gw = 0
    for dpu, items in gateway_by_dpu.items():
        for name, desc in items[:5]:  # 每 DPU 只列 5 个示例
            short_name = name.replace("HW.", "").replace(".PV", "")
            desc_clean = desc.replace("(", "[").replace(")", "]")
            sections["gateway"].append(f"# {dpu}.{short_name}({desc_clean}) = ???")
            total_gw += 1
    stats["gateway"] = total_gw
    sections["model"].append("# (模型层 — 待实现)")

    # === 输出 ===
    for sec_label, sec_key in SECTION_ORDER:
        body = sections[sec_key]
        if not body: continue
        lines.append("")
        lines.append("# " + "═" * 64)
        lines.append(f"# 【{sec_label}】")
        lines.append("# " + "═" * 64)
        lines.extend(body)

    # 头部统计 - 白名单严格模式
    motor_active = stats['rs'] + stats['rs_not'] + stats['single_open'] + stats['single_close']
    header_stats = [
        f"# 白名单严格模式 (只处理列出的设备类型):",
        f"#   1. 电机设备层 (送风机/引风机/一次风机/给煤机/磨煤机/前置泵/凝结水泵)",
        f"#      → {motor_active} 行真闭环 (RS:{stats['rs']} + RS_NOT:{stats['rs_not']} "
        f"+ 单开:{stats['single_open']} + 单关:{stats['single_close']})",
        f"#   2. 阀门设备层 (除氧器主/副调节阀, 送风机/引风机/一次风机动叶)",
        f"#      → {stats['pair']} 行 AI 直通",
        f"#   3. 柜间通讯 (MEH/DEH/DCS) — 待实现",
        f"#   4. 模型层 — 待实现",
        f"#   ─────────────",
        f"#   不在白名单的所有点: 不入脚本 (在 _FULL.csv 已暴露, 后续按需手动加)",
        "",
    ]
    lines[3:3] = header_stats
    return "\n".join(lines)


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
