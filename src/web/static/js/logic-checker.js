/* ═══════════════════════════════════════════════════════════
   LeDCSsim - LogicChecker
   DCS 逻辑检查模块，检测画布中的错误、警告和信息项
   依赖: canvas-engine.js (CanvasEngine, getPortType, areTypesCompatible)
   ═══════════════════════════════════════════════════════════ */

var LogicChecker = (function () {
  'use strict';

  /* ── 常量 ──────────────────────────────────────────────── */

  var TIMER_TYPES = { TB:1, TBD:1, TD:1, THF:1, TP:1, TW:1, TWO:1, TWF:1, RT:1, CNT:1 };
  var NO_INPUT_REQUIRED = { input:1, constant:1, CON:1, ref_in:1, SG:1 };
  var NO_OUTPUT_REQUIRED = { output:1, ref_out:1 };
  var LOGIC_GATE_TYPES = { A:1, OR:1 };

  var LS_KEY = 'ce_ignored_checks';

  var _panel = null;
  var _results = [];
  var _stale = false;
  var _minimized = false;
  var _filterSeverity = 'all';
  var _engine = null;
  var _editListener = null;

  /* ── 工具函数 ────────────────────────────────────────── */

  function esc(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function loadIgnored() {
    try {
      var raw = localStorage.getItem(LS_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch (e) {
      return {};
    }
  }

  function saveIgnored(map) {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(map));
    } catch (e) { /* 静默 */ }
  }

  function ignoreKey(code, nodeId) {
    return code + ':' + nodeId;
  }

  function getNodes(engine) {
    try {
      var exp = engine.getEditor().export();
      return exp.drawflow.Home.data;
    } catch (e) {
      return {};
    }
  }

  function getBlockType(engine, nodeId) {
    return engine._nodeBlockMap[nodeId] || null;
  }

  function getNodeData(engine, nodeId) {
    return engine._nodeDataMap[nodeId] || {};
  }

  /** 收集某节点所有输入端口的连接 */
  function inputConnections(node) {
    var conns = [];
    if (!node.inputs) return conns;
    var keys = Object.keys(node.inputs);
    for (var i = 0; i < keys.length; i++) {
      var portKey = keys[i];
      var arr = node.inputs[portKey].connections || [];
      for (var j = 0; j < arr.length; j++) {
        conns.push({ portKey: portKey, conn: arr[j] });
      }
    }
    return conns;
  }

  /** 收集某节点所有输出端口的连接 */
  function outputConnections(node) {
    var conns = [];
    if (!node.outputs) return conns;
    var keys = Object.keys(node.outputs);
    for (var i = 0; i < keys.length; i++) {
      var portKey = keys[i];
      var arr = node.outputs[portKey].connections || [];
      for (var j = 0; j < arr.length; j++) {
        conns.push({ portKey: portKey, conn: arr[j] });
      }
    }
    return conns;
  }

  /** 端口键名 → 端口索引 (input_1 → 0, output_3 → 2) */
  function portIndex(portKey) {
    var m = portKey.match(/(\d+)$/);
    return m ? parseInt(m[1], 10) - 1 : 0;
  }

  /* ── 检查规则 ────────────────────────────────────────── */

  /** E1: 悬空输入 — 非信号源块的输入端口无连接 */
  function checkDanglingInputs(engine, nodes) {
    var results = [];
    var ids = Object.keys(nodes);
    for (var i = 0; i < ids.length; i++) {
      var nid = ids[i];
      var node = nodes[nid];
      var blockType = getBlockType(engine, nid);
      if (NO_INPUT_REQUIRED[blockType]) continue;
      if (!node.inputs) continue;
      var inputKeys = Object.keys(node.inputs);
      for (var j = 0; j < inputKeys.length; j++) {
        var pk = inputKeys[j];
        var conns = node.inputs[pk].connections || [];
        if (conns.length === 0) {
          results.push({
            code: 'E1',
            severity: 'error',
            message: '悬空输入: 节点 #' + nid + ' (' + (blockType || node.name) + ') 的 ' + pk + ' 未连接',
            elements: [nid],
            ignored: false
          });
        }
      }
    }
    return results;
  }

  /** E2: 类型不匹配 — 连接两端端口类型不兼容 */
  function checkTypeMismatch(engine, nodes) {
    var results = [];
    if (typeof getPortType !== 'function' || typeof areTypesCompatible !== 'function') return results;
    var ids = Object.keys(nodes);
    for (var i = 0; i < ids.length; i++) {
      var nid = ids[i];
      var node = nodes[nid];
      if (!node.inputs) continue;
      var inputKeys = Object.keys(node.inputs);
      for (var j = 0; j < inputKeys.length; j++) {
        var pk = inputKeys[j];
        var conns = node.inputs[pk].connections || [];
        for (var c = 0; c < conns.length; c++) {
          var srcNodeId = conns[c].node;
          var srcPortKey = conns[c].input; // Drawflow 存的是 output_N
          var srcBlockId = getBlockType(engine, srcNodeId);
          var dstBlockId = getBlockType(engine, nid);
          var srcType = getPortType(srcBlockId || '', 'output', portIndex(srcPortKey));
          var dstType = getPortType(dstBlockId || '', 'input', portIndex(pk));
          if (srcType && dstType && !areTypesCompatible(srcType, dstType)) {
            results.push({
              code: 'E2',
              severity: 'error',
              message: '类型不匹配: #' + srcNodeId + '.' + srcPortKey + ' (' + srcType + ') -> #' + nid + '.' + pk + ' (' + dstType + ')',
              elements: [srcNodeId, nid],
              ignored: false
            });
          }
        }
      }
    }
    return results;
  }

  /** E3: 循环依赖 — Tarjan SCC，排除含定时器的环 */
  function checkCyclicDependency(engine, nodes) {
    var results = [];
    var ids = Object.keys(nodes);
    // 建邻接表
    var adj = {};
    for (var i = 0; i < ids.length; i++) {
      adj[ids[i]] = [];
    }
    for (var i = 0; i < ids.length; i++) {
      var nid = ids[i];
      var node = nodes[nid];
      if (!node.outputs) continue;
      var outKeys = Object.keys(node.outputs);
      for (var j = 0; j < outKeys.length; j++) {
        var conns = node.outputs[outKeys[j]].connections || [];
        for (var c = 0; c < conns.length; c++) {
          adj[nid].push(conns[c].node);
        }
      }
    }

    // Tarjan SCC
    var index = 0;
    var stack = [];
    var onStack = {};
    var indices = {};
    var lowlinks = {};
    var sccs = [];

    function strongConnect(v) {
      indices[v] = index;
      lowlinks[v] = index;
      index++;
      stack.push(v);
      onStack[v] = true;
      var neighbors = adj[v] || [];
      for (var k = 0; k < neighbors.length; k++) {
        var w = neighbors[k];
        if (indices[w] === undefined) {
          strongConnect(w);
          lowlinks[v] = Math.min(lowlinks[v], lowlinks[w]);
        } else if (onStack[w]) {
          lowlinks[v] = Math.min(lowlinks[v], indices[w]);
        }
      }
      if (lowlinks[v] === indices[v]) {
        var scc = [];
        var w2;
        do {
          w2 = stack.pop();
          onStack[w2] = false;
          scc.push(w2);
        } while (w2 !== v);
        if (scc.length > 1) {
          sccs.push(scc);
        }
      }
    }

    for (var i = 0; i < ids.length; i++) {
      if (indices[ids[i]] === undefined) {
        strongConnect(ids[i]);
      }
    }

    // 过滤：排除包含定时器块的 SCC
    for (var s = 0; s < sccs.length; s++) {
      var scc = sccs[s];
      var hasTimer = false;
      for (var k = 0; k < scc.length; k++) {
        var bt = getBlockType(engine, scc[k]);
        if (bt && TIMER_TYPES[bt]) {
          hasTimer = true;
          break;
        }
      }
      if (!hasTimer) {
        var nodeNames = scc.map(function (nid) {
          return '#' + nid + '(' + (getBlockType(engine, nid) || '?') + ')';
        });
        results.push({
          code: 'E3',
          severity: 'error',
          message: '循环依赖: ' + nodeNames.join(' -> '),
          elements: scc.slice(),
          ignored: false
        });
      }
    }
    return results;
  }

  /** E4: 输出冲突 — 一个输入端口有多条连线 */
  function checkOutputConflict(engine, nodes) {
    var results = [];
    var ids = Object.keys(nodes);
    for (var i = 0; i < ids.length; i++) {
      var nid = ids[i];
      var node = nodes[nid];
      if (!node.inputs) continue;
      var inputKeys = Object.keys(node.inputs);
      for (var j = 0; j < inputKeys.length; j++) {
        var pk = inputKeys[j];
        var conns = node.inputs[pk].connections || [];
        if (conns.length > 1) {
          results.push({
            code: 'E4',
            severity: 'error',
            message: '输出冲突: 节点 #' + nid + ' (' + (getBlockType(engine, nid) || node.name) + ') 的 ' + pk + ' 有 ' + conns.length + ' 条输入连线',
            elements: [nid],
            ignored: false
          });
        }
      }
    }
    return results;
  }

  /** W1: 未使用输出 — 输出端口无连接 */
  function checkUnusedOutputs(engine, nodes) {
    var results = [];
    var ids = Object.keys(nodes);
    for (var i = 0; i < ids.length; i++) {
      var nid = ids[i];
      var node = nodes[nid];
      var blockType = getBlockType(engine, nid);
      if (NO_OUTPUT_REQUIRED[blockType]) continue;
      if (!node.outputs) continue;
      var outKeys = Object.keys(node.outputs);
      for (var j = 0; j < outKeys.length; j++) {
        var pk = outKeys[j];
        var conns = node.outputs[pk].connections || [];
        if (conns.length === 0) {
          results.push({
            code: 'W1',
            severity: 'warning',
            message: '未使用输出: 节点 #' + nid + ' (' + (blockType || node.name) + ') 的 ' + pk + ' 未连接',
            elements: [nid],
            ignored: false
          });
        }
      }
    }
    return results;
  }

  /** W4: 布尔量无去抖 — 布尔输出直接连逻辑门，中间无定时器 */
  function checkBoolNoDebounce(engine, nodes) {
    var results = [];
    var ids = Object.keys(nodes);
    for (var i = 0; i < ids.length; i++) {
      var nid = ids[i];
      var node = nodes[nid];
      if (!node.outputs) continue;
      var srcType = getBlockType(engine, nid);
      // 仅检查布尔类输出（简单判定：DI 相关或 CMP 等比较块输出布尔）
      var outKeys = Object.keys(node.outputs);
      for (var j = 0; j < outKeys.length; j++) {
        var conns = node.outputs[outKeys[j]].connections || [];
        for (var c = 0; c < conns.length; c++) {
          var dstId = conns[c].node;
          var dstType = getBlockType(engine, dstId);
          if (dstType && LOGIC_GATE_TYPES[dstType]) {
            // 检查源是否是定时器
            if (srcType && !TIMER_TYPES[srcType]) {
              // 检查源是否输出布尔（使用 getPortType 如果可用）
              var isBool = false;
              if (typeof getPortType === 'function') {
                var pt = getPortType(srcType || '', 'output', portIndex(outKeys[j]));
                if (pt === 'bool' || pt === 'digital') isBool = true;
              } else {
                // 无类型信息时，保守跳过
                continue;
              }
              if (isBool) {
                results.push({
                  code: 'W4',
                  severity: 'warning',
                  message: '布尔无去抖: #' + nid + '(' + srcType + ') 直连逻辑门 #' + dstId + '(' + dstType + ')，建议中间加定时器',
                  elements: [nid, dstId],
                  ignored: false
                });
              }
            }
          }
        }
      }
    }
    return results;
  }

  /** W5: 孤立块 — 所有端口均无连接 */
  function checkOrphanBlocks(engine, nodes) {
    var results = [];
    var ids = Object.keys(nodes);
    for (var i = 0; i < ids.length; i++) {
      var nid = ids[i];
      var node = nodes[nid];
      var hasAny = false;
      if (node.inputs) {
        var ik = Object.keys(node.inputs);
        for (var j = 0; j < ik.length && !hasAny; j++) {
          if ((node.inputs[ik[j]].connections || []).length > 0) hasAny = true;
        }
      }
      if (!hasAny && node.outputs) {
        var ok = Object.keys(node.outputs);
        for (var j = 0; j < ok.length && !hasAny; j++) {
          if ((node.outputs[ok[j]].connections || []).length > 0) hasAny = true;
        }
      }
      if (!hasAny) {
        results.push({
          code: 'W5',
          severity: 'warning',
          message: '孤立块: 节点 #' + nid + ' (' + (getBlockType(engine, nid) || node.name) + ') 无任何连接',
          elements: [nid],
          ignored: false
        });
      }
    }
    return results;
  }

  /** I1: 命名缺失 — input/output 块未设 tag */
  function checkNaming(engine, nodes) {
    var results = [];
    var ids = Object.keys(nodes);
    for (var i = 0; i < ids.length; i++) {
      var nid = ids[i];
      var blockType = getBlockType(engine, nid);
      if (blockType === 'input' || blockType === 'output') {
        var nd = getNodeData(engine, nid);
        if (!nd.tag || String(nd.tag).trim() === '') {
          results.push({
            code: 'I1',
            severity: 'info',
            message: '命名缺失: 节点 #' + nid + ' (' + blockType + ') 未设置 tag 名称',
            elements: [nid],
            ignored: false
          });
        }
      }
    }
    return results;
  }

  /** I2: 重复连接 — 同源端口到同目标端口出现多次 */
  function checkRedundantConnections(engine, nodes) {
    var results = [];
    var seen = {};
    var ids = Object.keys(nodes);
    for (var i = 0; i < ids.length; i++) {
      var nid = ids[i];
      var node = nodes[nid];
      if (!node.inputs) continue;
      var inputKeys = Object.keys(node.inputs);
      for (var j = 0; j < inputKeys.length; j++) {
        var pk = inputKeys[j];
        var conns = node.inputs[pk].connections || [];
        var portSeen = {};
        for (var c = 0; c < conns.length; c++) {
          var key = conns[c].node + ':' + conns[c].input + '->' + nid + ':' + pk;
          if (portSeen[key]) {
            results.push({
              code: 'I2',
              severity: 'info',
              message: '重复连接: #' + conns[c].node + '.' + conns[c].input + ' -> #' + nid + '.' + pk,
              elements: [conns[c].node, nid],
              ignored: false
            });
          }
          portSeen[key] = true;
        }
      }
    }
    return results;
  }

  /* ── 执行所有检查 ──────────────────────────────────────── */

  function runAllChecks(engine) {
    var nodes = getNodes(engine);
    var all = [];
    all = all.concat(checkDanglingInputs(engine, nodes));
    all = all.concat(checkTypeMismatch(engine, nodes));
    all = all.concat(checkCyclicDependency(engine, nodes));
    all = all.concat(checkOutputConflict(engine, nodes));
    all = all.concat(checkUnusedOutputs(engine, nodes));
    all = all.concat(checkBoolNoDebounce(engine, nodes));
    all = all.concat(checkOrphanBlocks(engine, nodes));
    all = all.concat(checkNaming(engine, nodes));
    all = all.concat(checkRedundantConnections(engine, nodes));

    // 应用 ignore 列表
    var ignored = loadIgnored();
    for (var i = 0; i < all.length; i++) {
      var item = all[i];
      var key = ignoreKey(item.code, item.elements[0]);
      if (ignored[key]) {
        item.ignored = true;
      }
    }

    return all;
  }

  /* ── 面板 DOM ──────────────────────────────────────────── */

  function createPanel() {
    if (_panel) return _panel;
    _panel = document.createElement('div');
    _panel.id = 'ce-check-panel';
    _panel.innerHTML =
      '<div class="check-header">' +
        '<span class="check-title">逻辑检查</span>' +
        '<span class="check-badges"></span>' +
        '<select class="check-filter">' +
          '<option value="all">全部</option>' +
          '<option value="error">错误</option>' +
          '<option value="warning">警告</option>' +
          '<option value="info">信息</option>' +
        '</select>' +
        '<button class="check-btn check-minimize" title="最小化">&#x2015;</button>' +
        '<button class="check-btn check-close" title="关闭">&times;</button>' +
      '</div>' +
      '<div class="check-results"></div>' +
      '<div class="check-footer"></div>';
    document.body.appendChild(_panel);
    injectStyles();

    // 事件绑定
    _panel.querySelector('.check-close').addEventListener('click', function () {
      _panel.style.display = 'none';
    });
    _panel.querySelector('.check-minimize').addEventListener('click', function () {
      _minimized = !_minimized;
      _panel.querySelector('.check-results').style.display = _minimized ? 'none' : '';
      _panel.querySelector('.check-footer').style.display = _minimized ? 'none' : '';
      _panel.querySelector('.check-minimize').innerHTML = _minimized ? '&#x25A1;' : '&#x2015;';
    });
    _panel.querySelector('.check-filter').addEventListener('change', function (e) {
      _filterSeverity = e.target.value;
      renderResults();
    });

    return _panel;
  }

  function injectStyles() {
    if (document.getElementById('ce-check-styles')) return;
    var style = document.createElement('style');
    style.id = 'ce-check-styles';
    style.textContent =
      '#ce-check-panel {' +
        'position:fixed; bottom:0; left:0; right:0; z-index:9999;' +
        'background:#1e293b; color:#e2e8f0; font-size:12px;' +
        'border-top:2px solid #334155; display:none;' +
        'font-family:monospace; max-height:40vh;' +
        'display:flex; flex-direction:column;' +
      '}' +
      '#ce-check-panel[style*="display: none"] { display:none!important; }' +
      '.check-header {' +
        'display:flex; align-items:center; gap:8px;' +
        'padding:4px 10px; background:#0f172a; border-bottom:1px solid #334155;' +
        'flex-shrink:0;' +
      '}' +
      '.check-title { font-weight:bold; margin-right:4px; }' +
      '.check-badges { display:flex; gap:4px; flex:1; }' +
      '.check-badge {' +
        'padding:1px 6px; border-radius:3px; font-size:11px; font-weight:bold;' +
      '}' +
      '.check-badge-error { background:#dc2626; color:#fff; }' +
      '.check-badge-warning { background:#d97706; color:#fff; }' +
      '.check-badge-info { background:#2563eb; color:#fff; }' +
      '.check-filter {' +
        'background:#334155; color:#e2e8f0; border:1px solid #475569;' +
        'padding:1px 4px; font-size:11px; border-radius:2px;' +
      '}' +
      '.check-btn {' +
        'background:none; border:none; color:#94a3b8; cursor:pointer;' +
        'font-size:14px; padding:0 4px; line-height:1;' +
      '}' +
      '.check-btn:hover { color:#fff; }' +
      '.check-results {' +
        'overflow-y:auto; flex:1; min-height:0;' +
      '}' +
      '.check-row {' +
        'display:flex; align-items:center; gap:6px;' +
        'padding:3px 10px; border-bottom:1px solid #1e293b;' +
        'background:#0f172a;' +
      '}' +
      '.check-row:nth-child(even) { background:#1a2332; }' +
      '.check-row:hover { background:#1e3a5f; }' +
      '.check-row.ignored { opacity:0.4; text-decoration:line-through; }' +
      '.check-row.stale { opacity:0.5; }' +
      '.check-icon { width:14px; text-align:center; flex-shrink:0; }' +
      '.check-icon-error { color:#ef4444; }' +
      '.check-icon-warning { color:#f59e0b; }' +
      '.check-icon-info { color:#3b82f6; }' +
      '.check-code { color:#94a3b8; width:24px; flex-shrink:0; }' +
      '.check-msg { flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }' +
      '.check-action {' +
        'background:#334155; border:1px solid #475569; color:#cbd5e1;' +
        'padding:1px 6px; font-size:10px; cursor:pointer; border-radius:2px;' +
        'flex-shrink:0;' +
      '}' +
      '.check-action:hover { background:#475569; color:#fff; }' +
      '.check-footer {' +
        'padding:3px 10px; background:#0f172a; border-top:1px solid #334155;' +
        'color:#64748b; font-size:11px; flex-shrink:0;' +
      '}' +
      '.ce-error-flash {' +
        'outline:3px dashed #ef4444 !important;' +
        'outline-offset:4px;' +
        'animation:ce-flash 0.4s ease-in-out 3;' +
      '}' +
      '@keyframes ce-flash {' +
        '0%,100% { outline-color:#ef4444; }' +
        '50% { outline-color:transparent; }' +
      '}';
    document.head.appendChild(style);
  }

  var SEVERITY_ICON = {
    error:   '<span class="check-icon check-icon-error">&#x2716;</span>',
    warning: '<span class="check-icon check-icon-warning">&#x26A0;</span>',
    info:    '<span class="check-icon check-icon-info">&#x2139;</span>'
  };

  function renderResults() {
    if (!_panel) return;
    var container = _panel.querySelector('.check-results');
    var html = '';
    var counts = { error: 0, warning: 0, info: 0 };
    var ignoredCount = 0;

    for (var i = 0; i < _results.length; i++) {
      var r = _results[i];
      counts[r.severity] = (counts[r.severity] || 0) + 1;
      if (r.ignored) ignoredCount++;
    }

    // 徽章
    var badgesEl = _panel.querySelector('.check-badges');
    badgesEl.innerHTML =
      '<span class="check-badge check-badge-error">E:' + counts.error + '</span>' +
      '<span class="check-badge check-badge-warning">W:' + counts.warning + '</span>' +
      '<span class="check-badge check-badge-info">I:' + counts.info + '</span>';

    // 过滤并渲染行
    for (var i = 0; i < _results.length; i++) {
      var r = _results[i];
      if (_filterSeverity !== 'all' && r.severity !== _filterSeverity) continue;
      var cls = 'check-row';
      if (r.ignored) cls += ' ignored';
      if (_stale) cls += ' stale';
      html += '<div class="' + cls + '" data-idx="' + i + '">';
      html += SEVERITY_ICON[r.severity] || '';
      html += '<span class="check-code">' + esc(r.code) + '</span>';
      html += '<span class="check-msg" title="' + esc(r.message) + '">' + esc(r.message) + '</span>';
      html += '<button class="check-action check-locate" data-idx="' + i + '">定位</button>';
      html += '<button class="check-action check-ignore" data-idx="' + i + '">' + (r.ignored ? '取消忽略' : '忽略') + '</button>';
      html += '</div>';
    }

    if (_results.length === 0) {
      html = '<div class="check-row" style="justify-content:center;color:#22c55e;padding:8px;">无问题</div>';
    }

    container.innerHTML = html;

    // 页脚
    var footer = _panel.querySelector('.check-footer');
    var now = new Date();
    var timeStr = now.getHours() + ':' + String(now.getMinutes()).padStart(2, '0') + ':' + String(now.getSeconds()).padStart(2, '0');
    footer.textContent = '上次检查: ' + timeStr + '  |  共 ' + _results.length + ' 项' +
      (ignoredCount > 0 ? '  |  已忽略: ' + ignoredCount : '') +
      (_stale ? '  |  (结果已过期)' : '');

    // 绑定行按钮事件
    container.querySelectorAll('.check-locate').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var idx = parseInt(btn.getAttribute('data-idx'), 10);
        var r = _results[idx];
        if (r && r.elements && r.elements.length > 0 && _engine) {
          locateNode(_engine, r.elements[0]);
        }
      });
    });

    container.querySelectorAll('.check-ignore').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var idx = parseInt(btn.getAttribute('data-idx'), 10);
        var r = _results[idx];
        if (!r) return;
        var map = loadIgnored();
        var key = ignoreKey(r.code, r.elements[0]);
        if (r.ignored) {
          delete map[key];
          r.ignored = false;
        } else {
          map[key] = true;
          r.ignored = true;
        }
        saveIgnored(map);
        renderResults();
      });
    });
  }

  function locateNode(engine, nodeId) {
    if (typeof engine.centerOnNode === 'function') {
      engine.centerOnNode(nodeId);
    }
    // 红色闪烁
    var el = document.querySelector('#node-' + nodeId);
    if (el) {
      el.classList.add('ce-error-flash');
      setTimeout(function () {
        el.classList.remove('ce-error-flash');
      }, 2400);
    }
  }

  function showPanel() {
    if (!_panel) createPanel();
    _panel.style.display = 'flex';
    _minimized = false;
    _panel.querySelector('.check-results').style.display = '';
    _panel.querySelector('.check-footer').style.display = '';
    _panel.querySelector('.check-minimize').innerHTML = '&#x2015;';
  }

  /* ── 过期检测 ─────────────────────────────────────────── */

  function setupStaleDetection(engine) {
    if (_editListener) return;
    var editor = engine.getEditor();
    if (!editor) return;
    var events = ['nodeCreated', 'nodeRemoved', 'connectionCreated', 'connectionRemoved', 'nodeMoved'];
    _editListener = function () {
      if (_results.length > 0) {
        _stale = true;
        renderResults();
      }
    };
    for (var i = 0; i < events.length; i++) {
      editor.on(events[i], _editListener);
    }
  }

  /* ── 公共 API ──────────────────────────────────────────── */

  return {
    /** 初始化：创建面板 DOM，设置过期检测 */
    init: function (engine) {
      _engine = engine;
      createPanel();
      _panel.style.display = 'none';
      setupStaleDetection(engine);
    },

    /** 执行检查并显示面板 */
    runCheck: function (engine) {
      _engine = engine || _engine;
      _stale = false;
      _results = runAllChecks(_engine);
      showPanel();
      renderResults();
      return _results;
    },

    /** 静默检查：仅 toast 汇总，返回结果 */
    runSilent: function (engine) {
      _engine = engine || _engine;
      _stale = false;
      _results = runAllChecks(_engine);
      var counts = { error: 0, warning: 0, info: 0 };
      for (var i = 0; i < _results.length; i++) {
        if (!_results[i].ignored) {
          counts[_results[i].severity]++;
        }
      }
      var total = counts.error + counts.warning + counts.info;
      if (typeof showToast === 'function') {
        if (counts.error > 0) {
          showToast('逻辑检查: ' + counts.error + ' 错误, ' + counts.warning + ' 警告, ' + counts.info + ' 信息', 'error');
        } else if (counts.warning > 0) {
          showToast('逻辑检查: ' + counts.warning + ' 警告, ' + counts.info + ' 信息', 'warning');
        } else if (total > 0) {
          showToast('逻辑检查: ' + counts.info + ' 信息项', 'info');
        } else {
          showToast('逻辑检查通过', 'success');
        }
      }
      return _results;
    },

    /** 获取面板元素 */
    getPanel: function () {
      return _panel;
    }
  };

})();
