/* ═══════════════════════════════════════════════════════════
   LeDCSsim - ConnRecommend
   IO 连线推荐模块：拖线时浮动推荐面板 + 端口高亮
   依赖: canvas-engine.js (CanvasEngine 实例)
   ═══════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ── 开关量块类型（与 canvas-engine.js 保持一致） ── */
  var DIGITAL_BLOCK_TYPES = {
    AND: true, OR: true, NOT: true, XOR: true,
    SR: true, RS: true,
    TON: true, TOFF: true, TP: true, CTR: true,
    CMP: true, AC: true, AC1: true
  };

  /* ── DCS 常见连线模式（源块类型 → 目标块类型集合） ── */
  var DCS_PATTERNS = {
    CMP:   { A: true, OR: true, NOT: true, AND: true },
    AC:    { A: true, OR: true, NOT: true, AND: true },
    AC1:   { A: true, OR: true, NOT: true, AND: true },
    PI:    { LIM: true, RL: true, AM: true },
    PIF:   { LIM: true, RL: true, AM: true },
    PIV:   { LIM: true, RL: true, AM: true },
    input: { FLT: true, SC: true, G: true }
  };

  /* ── 工具函数 ──────────────────────────────────────── */

  /** 转义 HTML */
  function esc(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /**
   * 获取端口类型 — 委托给全局 getPortType (canvas-engine.js Phase 0)
   */
  function localGetPortType(engine, nodeId, direction, portIndex) {
    var blockId = engine._nodeBlockMap[nodeId];
    if (!blockId) return 'float';
    if (typeof window.getPortType === 'function') {
      return window.getPortType(blockId, direction, portIndex);
    }
    return DIGITAL_BLOCK_TYPES[blockId] ? 'bool' : 'float';
  }

  /** 类型兼容检查 — 委托给全局 areTypesCompatible */
  function localAreTypesCompatible(srcType, dstType) {
    if (typeof window.areTypesCompatible === 'function') {
      return window.areTypesCompatible(srcType, dstType);
    }
    return srcType === dstType;
  }

  /** 提取 tag 前缀（下划线或连字符前的部分） */
  function extractTagPrefix(tag) {
    if (!tag) return '';
    var m = tag.match(/^([^_\-]+)/);
    return m ? m[1] : tag;
  }

  /** 获取节点端口数量 */
  function getPortCount(dfNode, direction) {
    var ports = direction === 'output' ? dfNode.outputs : dfNode.inputs;
    return ports ? Object.keys(ports).length : 0;
  }

  /** 获取端口已有连接数 */
  function getPortConnections(dfNode, portKey) {
    var parts = portKey.split('_');
    var direction = parts[0]; // 'input' or 'output'
    var portMap = direction === 'output' ? dfNode.outputs : dfNode.inputs;
    if (portMap && portMap[portKey]) {
      return portMap[portKey].connections || [];
    }
    return [];
  }

  /** 获取端口名称（从 blockDef 定义中获取） */
  function getPortName(engine, nodeId, direction, portIndex) {
    var blockId = engine._nodeBlockMap[nodeId];
    if (!blockId) return direction + '_' + (portIndex + 1);
    var blockDef = engine._blockDefs[blockId];
    if (!blockDef) return direction + '_' + (portIndex + 1);

    var portsDef = direction === 'output' ? blockDef.outputs : blockDef.inputs;
    if (Array.isArray(portsDef) && portsDef[portIndex]) {
      var p = portsDef[portIndex];
      return typeof p === 'string' ? p : (p.name || direction + '_' + (portIndex + 1));
    }
    return direction + '_' + (portIndex + 1);
  }

  /* ── 拖拽状态 ──────────────────────────────────────── */
  var _engine = null;
  var _dragSourceInfo = null;
  var _panelEl = null;
  var _candidates = [];
  var _panelVisible = false;

  /* ── 推荐引擎（Step 3.2） ─────────────────────────── */

  /**
   * 查找连线候选端口
   * @param {CanvasEngine} engine
   * @param {number} sourceNodeId
   * @param {string} sourcePortKey   e.g. 'output_1'
   * @param {string} sourceDirection 'output' 或 'input'
   * @returns {Array} Candidate[]
   */
  function findCandidates(engine, sourceNodeId, sourcePortKey, sourceDirection) {
    var editor = engine.getEditor();
    var drawflowData = editor.export();
    var moduleData = drawflowData.drawflow.Home.data;

    var targetDirection = sourceDirection === 'output' ? 'input' : 'output';
    var srcPortIndex = parseInt((sourcePortKey.match(/\d+$/) || ['1'])[0], 10) - 1;
    var srcType = localGetPortType(engine, sourceNodeId, sourceDirection, srcPortIndex);
    var srcBlockId = engine._nodeBlockMap[sourceNodeId] || '';
    var srcData = engine._nodeDataMap[sourceNodeId] || {};
    var srcTagPrefix = extractTagPrefix(srcData.tag || srcData.name || '');

    var candidates = [];

    Object.keys(moduleData).forEach(function (nid) {
      var node = moduleData[nid];
      var nodeId = parseInt(nid, 10);

      // 不连接自身
      if (nodeId === sourceNodeId) return;

      var targetBlockId = engine._nodeBlockMap[nodeId] || '';
      var targetData = engine._nodeDataMap[nodeId] || {};
      var targetPorts = targetDirection === 'input' ? node.inputs : node.outputs;

      if (!targetPorts) return;

      Object.keys(targetPorts).forEach(function (portKey) {
        var portIndex = parseInt((portKey.match(/\d+$/) || ['1'])[0], 10) - 1;
        var dstType = localGetPortType(engine, nodeId, targetDirection, portIndex);

        // 类型兼容检查（input/output 信号块视为通用，兼容任意类型）
        var srcIsSignal = srcBlockId === 'input' || srcBlockId === 'output';
        var dstIsSignal = targetBlockId === 'input' || targetBlockId === 'output';
        if (!srcIsSignal && !dstIsSignal && !localAreTypesCompatible(srcType, dstType)) return;

        var conns = targetPorts[portKey].connections || [];
        var occupied = conns.length > 0;
        var score = 0;

        // 未连接加分
        if (!occupied) score += 2;

        // tag 前缀匹配加分
        var targetTagPrefix = extractTagPrefix(targetData.tag || targetData.name || '');
        if (srcTagPrefix && targetTagPrefix && srcTagPrefix === targetTagPrefix) {
          score += 4;
        }

        // DCS 常见模式加分
        var patternSrcId = sourceDirection === 'output' ? srcBlockId : targetBlockId;
        var patternDstId = sourceDirection === 'output' ? targetBlockId : srcBlockId;
        if (DCS_PATTERNS[patternSrcId] && DCS_PATTERNS[patternSrcId][patternDstId]) {
          score += 3;
        }

        var instanceName = targetData.tag || targetData.name || targetData._blockName || '';
        var portName = getPortName(engine, nodeId, targetDirection, portIndex);

        candidates.push({
          nodeId: nodeId,
          portKey: portKey,
          blockType: targetBlockId,
          portType: dstType,
          instanceName: instanceName,
          tag: targetData.tag || '',
          score: score,
          occupied: occupied,
          portName: portName
        });
      });
    });

    // 排序：未占用按分数降序在前，占用的在后
    candidates.sort(function (a, b) {
      if (a.occupied !== b.occupied) return a.occupied ? 1 : -1;
      return b.score - a.score;
    });

    // 最多返回 10 个
    return candidates.slice(0, 10);
  }

  /* ── 浮动推荐面板（Step 3.3） ─────────────────────── */

  /** 创建面板 DOM（懒加载） */
  function ensurePanel() {
    if (_panelEl) return _panelEl;

    _panelEl = document.createElement('div');
    _panelEl.id = 'ce-rec-panel';
    _panelEl.style.cssText = [
      'position:absolute',
      'z-index:1000',
      'width:240px',
      'background:#1e293b',
      'border:1px solid #475569',
      'border-radius:4px',
      'color:#e2e8f0',
      'font-size:12px',
      'display:none',
      'box-shadow:0 4px 12px rgba(0,0,0,0.4)',
      'overflow:hidden'
    ].join(';');

    _panelEl.innerHTML = [
      '<div style="padding:4px 6px;border-bottom:1px solid #475569">',
      '  <input id="ce-rec-filter" type="text" placeholder="筛选..." ',
      '    style="width:100%;box-sizing:border-box;background:#0f172a;border:1px solid #334155;',
      '    color:#e2e8f0;padding:3px 6px;font-size:12px;border-radius:2px;outline:none">',
      '</div>',
      '<ul id="ce-rec-list" style="list-style:none;margin:0;padding:0;max-height:260px;overflow-y:auto"></ul>'
    ].join('\n');

    // 插入到 canvas-area 容器中
    var canvasArea = document.querySelector('.canvas-area');
    if (canvasArea) {
      canvasArea.appendChild(_panelEl);
    } else {
      document.body.appendChild(_panelEl);
    }

    // 筛选事件
    var filterInput = _panelEl.querySelector('#ce-rec-filter');
    filterInput.addEventListener('input', function () {
      filterList(filterInput.value.trim().toLowerCase());
    });

    // 阻止面板内部事件冒泡到 drawflow
    _panelEl.addEventListener('mousedown', function (e) { e.stopPropagation(); });

    return _panelEl;
  }

  /** 渲染候选列表 */
  function renderList(candidates) {
    _candidates = candidates;
    var listEl = _panelEl.querySelector('#ce-rec-list');
    listEl.innerHTML = '';

    if (candidates.length === 0) {
      listEl.innerHTML = '<li style="padding:6px 8px;color:#64748b">无推荐连接</li>';
      return;
    }

    candidates.forEach(function (c, idx) {
      var li = document.createElement('li');
      li.className = 'ce-rec-item';
      li.dataset.idx = idx;
      li.style.cssText = [
        'padding:4px 8px',
        'cursor:pointer',
        'border-bottom:1px solid #334155',
        'display:flex',
        'align-items:center',
        'gap:4px'
      ].join(';');

      if (c.occupied) {
        li.style.color = '#64748b';
        li.style.fontStyle = 'italic';
      }

      // 类型标识
      var badge = c.portType === 'bool'
        ? '<span style="background:#475569;padding:0 3px;border-radius:2px;font-size:10px;margin-right:2px">B</span>'
        : '<span style="background:#334155;padding:0 3px;border-radius:2px;font-size:10px;margin-right:2px">F</span>';

      var label = esc(c.blockType) + '.' + esc(c.portName);
      var instance = c.instanceName ? ' <span style="color:#94a3b8;font-size:11px">' + esc(c.instanceName) + '</span>' : '';
      var occTag = c.occupied ? ' <span style="color:#ef4444;font-size:10px">[占]</span>' : '';

      li.innerHTML = badge + '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + label + instance + occTag + '</span>';

      // 鼠标悬停高亮目标端口
      li.addEventListener('mouseenter', function () {
        highlightTarget(c.nodeId, c.portKey, true);
      });
      li.addEventListener('mouseleave', function () {
        highlightTarget(c.nodeId, c.portKey, false);
      });

      // 点击创建连接
      li.addEventListener('click', function (e) {
        e.stopPropagation();
        selectCandidate(c);
      });

      listEl.appendChild(li);
    });
  }

  /** 按关键词筛选列表 */
  function filterList(keyword) {
    var items = _panelEl.querySelectorAll('.ce-rec-item');
    items.forEach(function (li) {
      var idx = parseInt(li.dataset.idx, 10);
      var c = _candidates[idx];
      if (!keyword) {
        li.style.display = '';
        return;
      }
      var text = (c.blockType + ' ' + c.portName + ' ' + c.instanceName + ' ' + c.tag).toLowerCase();
      li.style.display = text.indexOf(keyword) >= 0 ? '' : 'none';
    });
  }

  /** 显示面板 */
  function showPanel(x, y, candidates) {
    var panel = ensurePanel();
    renderList(candidates);
    panel.style.left = x + 'px';
    panel.style.top = y + 'px';
    panel.style.display = 'block';
    _panelVisible = true;

    // 清空筛选
    var filterInput = panel.querySelector('#ce-rec-filter');
    if (filterInput) {
      filterInput.value = '';
    }

    // 高亮所有兼容端口
    highlightAllCompatible(candidates);
  }

  /** 隐藏面板 */
  function hidePanel() {
    if (_panelEl) {
      _panelEl.style.display = 'none';
    }
    _panelVisible = false;
    _dragSourceInfo = null;
    _candidates = [];
    clearAllHighlights();
  }

  /* ── 端口高亮（Step 3.4） ──────────────────────────── */

  /** 注入高亮样式（只执行一次） */
  var _stylesInjected = false;
  function injectStyles() {
    if (_stylesInjected) return;
    _stylesInjected = true;

    var style = document.createElement('style');
    style.textContent = [
      '.ce-port-compatible { box-shadow: 0 0 6px 2px #22c55e !important; }',
      '.ce-port-occupied { opacity: 0.4 !important; box-shadow: 0 0 4px 1px #64748b !important; }',
      '.ce-port-hover { transform: scale(1.5) !important; box-shadow: 0 0 8px 3px #3b82f6 !important; }'
    ].join('\n');
    document.head.appendChild(style);
  }

  /** 高亮所有兼容端口 */
  function highlightAllCompatible(candidates) {
    clearAllHighlights();
    candidates.forEach(function (c) {
      var portEl = findPortElement(c.nodeId, c.portKey);
      if (!portEl) return;
      if (c.occupied) {
        portEl.classList.add('ce-port-occupied');
      } else {
        portEl.classList.add('ce-port-compatible');
      }
    });
  }

  /** 高亮/取消高亮单个目标端口 */
  function highlightTarget(nodeId, portKey, on) {
    var portEl = findPortElement(nodeId, portKey);
    if (!portEl) return;
    if (on) {
      portEl.classList.add('ce-port-hover');
    } else {
      portEl.classList.remove('ce-port-hover');
    }
  }

  /** 清除所有高亮 */
  function clearAllHighlights() {
    document.querySelectorAll('.ce-port-compatible, .ce-port-occupied, .ce-port-hover').forEach(function (el) {
      el.classList.remove('ce-port-compatible', 'ce-port-occupied', 'ce-port-hover');
    });
  }

  /** 查找端口 DOM 元素 */
  function findPortElement(nodeId, portKey) {
    var nodeEl = document.getElementById('node-' + nodeId);
    if (!nodeEl) return null;

    // portKey: 'input_1' or 'output_2' etc.
    var parts = portKey.split('_');
    var direction = parts[0]; // 'input' or 'output'
    var index = parseInt(parts[1], 10) - 1;

    var container = nodeEl.querySelector('.' + direction + 's');
    if (!container) return null;

    var ports = container.querySelectorAll('.' + direction);
    return ports[index] || null;
  }

  /* ── 选择候选项（Step 3.5） ───────────────────────── */

  function selectCandidate(candidate) {
    if (!_engine || !_dragSourceInfo) return;

    var editor = _engine.getEditor();
    var srcInfo = _dragSourceInfo;

    // 占用确认
    if (candidate.occupied) {
      if (!confirm('该端口已有连接，是否替换？')) return;

      // 移除已有连接
      var dfData = editor.export();
      var moduleData = dfData.drawflow.Home.data;
      var targetNode = moduleData[candidate.nodeId];
      if (targetNode) {
        var direction = candidate.portKey.split('_')[0];
        var portMap = direction === 'input' ? targetNode.inputs : targetNode.outputs;
        if (portMap && portMap[candidate.portKey]) {
          var conns = portMap[candidate.portKey].connections;
          if (conns && conns.length > 0) {
            conns.forEach(function (conn) {
              try {
                if (direction === 'input') {
                  editor.removeSingleConnection(conn.node, candidate.nodeId, conn[direction], candidate.portKey);
                } else {
                  editor.removeSingleConnection(candidate.nodeId, conn.node, candidate.portKey, conn[direction]);
                }
              } catch (e) {
                console.warn('ConnRecommend: 移除旧连接失败', e);
              }
            });
          }
        }
      }
    }

    // 创建新连接
    try {
      var outNodeId, inNodeId, outPortKey, inPortKey;
      if (srcInfo.direction === 'output') {
        outNodeId = srcInfo.nodeId;
        outPortKey = srcInfo.portKey;
        inNodeId = candidate.nodeId;
        inPortKey = candidate.portKey;
      } else {
        outNodeId = candidate.nodeId;
        outPortKey = candidate.portKey;
        inNodeId = srcInfo.nodeId;
        inPortKey = srcInfo.portKey;
      }
      editor.addConnection(outNodeId, inNodeId, outPortKey, inPortKey);
    } catch (e) {
      console.error('ConnRecommend: 创建连接失败', e);
      if (typeof showToast === 'function') showToast('连接创建失败', 'error');
    }

    hidePanel();
  }

  /* ── 拖拽拦截（Step 3.1） ──────────────────────────── */

  function init(engine) {
    _engine = engine;
    injectStyles();

    var container = engine.getEditor().container;

    // 捕获阶段监听端口 mousedown
    container.addEventListener('mousedown', onPortMouseDown, true);

    // 连接创建 → 清除状态
    engine.getEditor().on('connectionCreated', function () {
      hidePanel();
    });

    // Esc 关闭面板
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && _panelVisible) {
        hidePanel();
      }
    });
  }

  function onPortMouseDown(e) {
    var portEl = e.target.closest('.output, .input');
    if (!portEl) return;

    var nodeEl = portEl.closest('.drawflow-node');
    if (!nodeEl) return;

    var nodeId = parseInt(nodeEl.id.replace('node-', ''), 10);
    if (isNaN(nodeId)) return;

    // 判断方向
    var direction = portEl.classList.contains('output') ? 'output' : 'input';

    // 确定端口索引
    var portsContainer = portEl.parentElement;
    var siblings = portsContainer.querySelectorAll('.' + direction);
    var portIndex = 0;
    for (var i = 0; i < siblings.length; i++) {
      if (siblings[i] === portEl) { portIndex = i; break; }
    }

    var portKey = direction + '_' + (portIndex + 1);

    _dragSourceInfo = {
      nodeId: nodeId,
      portKey: portKey,
      direction: direction,
      startX: e.clientX,
      startY: e.clientY,
      startTime: Date.now()
    };

    // 绑定 mousemove 和 mouseup
    document.addEventListener('mousemove', onDragMove);
    document.addEventListener('mouseup', onDragEnd);
  }

  function onDragMove(e) {
    if (!_dragSourceInfo) return;

    var dx = e.clientX - _dragSourceInfo.startX;
    var dy = e.clientY - _dragSourceInfo.startY;
    var dist = Math.sqrt(dx * dx + dy * dy);
    var elapsed = Date.now() - _dragSourceInfo.startTime;

    // 距离 > 20px 且时间 > 300ms → 显示推荐面板
    if (dist > 20 && elapsed > 300 && !_panelVisible) {
      var candidates = findCandidates(
        _engine,
        _dragSourceInfo.nodeId,
        _dragSourceInfo.portKey,
        _dragSourceInfo.direction
      );

      if (candidates.length > 0) {
        // 计算面板位置：相对于 canvas-area 容器
        var canvasArea = document.querySelector('.canvas-area');
        var rect = canvasArea ? canvasArea.getBoundingClientRect() : { left: 0, top: 0 };
        var panelX = e.clientX - rect.left + 20;
        var panelY = e.clientY - rect.top + 20;
        showPanel(panelX, panelY, candidates);
      }
    }

    // 面板跟随鼠标（如果已显示）
    if (_panelVisible && _panelEl) {
      var canvasArea2 = document.querySelector('.canvas-area');
      var rect2 = canvasArea2 ? canvasArea2.getBoundingClientRect() : { left: 0, top: 0 };
      _panelEl.style.left = (e.clientX - rect2.left + 20) + 'px';
      _panelEl.style.top = (e.clientY - rect2.top + 20) + 'px';
    }
  }

  function onDragEnd() {
    document.removeEventListener('mousemove', onDragMove);
    document.removeEventListener('mouseup', onDragEnd);

    // 如果面板不可见，直接清除状态
    if (!_panelVisible) {
      _dragSourceInfo = null;
    }
    // 如果面板可见，保留状态等用户选择或按 Esc
  }

  /* ── 公共 API ──────────────────────────────────────── */

  window.ConnRecommend = {
    init: init,
    findCandidates: findCandidates,
    hidePanel: hidePanel
  };

})();
