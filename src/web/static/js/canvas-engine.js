/* ═══════════════════════════════════════════════════════════
   LeDCSsim - CanvasEngine
   Drawflow 增强引擎，提供 Simulink/SAMA 风格画布功能
   依赖: Drawflow v0.0.59 (CDN 加载)
   ═══════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ── 类别配置 ──────────────────────────────────────── */
  var CATEGORY_META = {
    signal:       { label: '信号',   color: '#10b981' },
    arithmetic:   { label: '运算',   color: '#f59e0b' },
    compare:      { label: '比较',   color: '#06b6d4' },
    dynamic:      { label: '动态',   color: '#3b82f6' },
    control:      { label: '控制',   color: '#ef4444' },
    logic:        { label: '逻辑',   color: '#8b5cf6' },
    timer:        { label: '定时',   color: '#ec4899' },
    transfer:     { label: '传输',   color: '#64748b' },
    encapsulated: { label: '封装',   color: '#7c3aed' }
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

  /** 深拷贝 (JSON 安全) */
  function deepClone(obj) {
    try { return JSON.parse(JSON.stringify(obj)); }
    catch (e) { return obj; }
  }

  /** 生成唯一 ID */
  var _uid = 0;
  function uid() { return 'ce_' + (++_uid) + '_' + Date.now().toString(36); }

  /* ═══════════════════════════════════════════════════════════
     CanvasEngine 主类
     ═══════════════════════════════════════════════════════════ */

  /**
   * @param {string} containerId  Drawflow 容器元素的 id
   * @param {object} [options]
   * @param {string} [options.grid='dots']         网格类型: 'dots'|'lines'|'none'
   * @param {string} [options.mode='config']       初始模式: 'config'|'monitor'
   * @param {number} [options.maxUndo=50]          撤销历史上限
   * @param {boolean} [options.minimap=false]      是否显示缩略图（保留，暂不实现）
   */
  function CanvasEngine(containerId, options) {
    options = options || {};

    this._containerId = containerId;
    this._container = document.getElementById(containerId);
    if (!this._container) {
      throw new Error('CanvasEngine: 找不到容器元素 #' + containerId);
    }

    // 初始化 Drawflow
    this._editor = new Drawflow(this._container);
    this._editor.reroute = true;
    this._editor.reroute_fix_curvature = true;
    this._editor.force_first_input = false;
    this._editor.start();

    // 状态
    this._mode = options.mode || 'config';
    this._gridType = options.grid || 'dots';
    this._maxUndo = options.maxUndo || 50;
    this._undoStack = [];
    this._redoStack = [];
    this._blockDefs = {};         // id -> blockDef
    this._nodeBlockMap = {};      // drawflow nodeId -> blockDef.id
    this._nodeDataMap = {};       // drawflow nodeId -> user data
    this._monitorValues = {};     // nodeId -> { output: value }
    this._suppressHistory = false;

    // 回调
    this._onNodeSelected = null;
    this._onNodeDeselected = null;
    this._onCanvasChanged = null;

    // 设置网格
    this._applyGrid();

    // 添加缩放指示器
    this._zoomIndicatorEl = document.createElement('div');
    this._zoomIndicatorEl.className = 'zoom-indicator';
    this._zoomIndicatorEl.textContent = '100%';
    this._container.style.position = 'relative';
    this._container.appendChild(this._zoomIndicatorEl);

    // 绑定事件
    this._bindEvents();

    // 初始快照
    this._pushHistory();
  }

  /* ── 内部方法 ──────────────────────────────────────── */

  CanvasEngine.prototype._applyGrid = function () {
    this._container.classList.remove('grid-dots', 'grid-lines', 'grid-none');
    this._container.classList.add('grid-' + this._gridType);
  };

  CanvasEngine.prototype._updateZoomIndicator = function () {
    var z = this._editor.zoom || 1;
    this._zoomIndicatorEl.textContent = Math.round(z * 100) + '%';
  };

  CanvasEngine.prototype._bindEvents = function () {
    var self = this;
    var editor = this._editor;

    // 节点选中/取消选中
    editor.on('nodeSelected', function (nodeId) {
      if (self._onNodeSelected) {
        self._onNodeSelected(nodeId, self.getNodeData(nodeId));
      }
    });

    editor.on('nodeUnselected', function () {
      if (self._onNodeDeselected) {
        self._onNodeDeselected();
      }
    });

    // 画布变更 → 推送历史
    var changeEvents = [
      'nodeCreated', 'nodeRemoved', 'nodeDataChanged',
      'nodeMoved', 'connectionCreated', 'connectionRemoved'
    ];

    changeEvents.forEach(function (evt) {
      editor.on(evt, function () {
        if (!self._suppressHistory) {
          self._pushHistory();
          self._redoStack = [];
        }
        if (self._onCanvasChanged) {
          self._onCanvasChanged(evt);
        }
      });
    });

    // 缩放变化
    editor.on('zoom', function () {
      self._updateZoomIndicator();
    });

    // 鼠标滚轮缩放 (Drawflow 内置支持，但需确保指示器同步)
    this._container.addEventListener('wheel', function () {
      setTimeout(function () { self._updateZoomIndicator(); }, 0);
    });
  };

  /** 推送当前状态到撤销栈 */
  CanvasEngine.prototype._pushHistory = function () {
    try {
      var snapshot = JSON.stringify(this._editor.export());
      // 避免重复压入相同快照
      if (this._undoStack.length > 0 &&
          this._undoStack[this._undoStack.length - 1] === snapshot) {
        return;
      }
      this._undoStack.push(snapshot);
      if (this._undoStack.length > this._maxUndo) {
        this._undoStack.shift();
      }
    } catch (e) {
      // 导出失败时忽略
    }
  };

  /** 恢复快照 */
  CanvasEngine.prototype._restoreSnapshot = function (snapshot) {
    this._suppressHistory = true;
    try {
      this._editor.import(JSON.parse(snapshot));
    } catch (e) {
      console.warn('CanvasEngine: 恢复快照失败', e);
    }
    this._suppressHistory = false;
    this._updateZoomIndicator();
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - 功能块管理
     ═══════════════════════════════════════════════════════════ */

  /**
   * 注册功能块定义（方便后续 addBlock 时使用）
   * @param {object} blockDef  { id, name, category, inputs, outputs, params, description }
   *   inputs/outputs: 数字或数组 [{name:'IN1'}, ...]
   *   params: [{key:'K', label:'增益', default:1.0, type:'number'}, ...]
   */
  CanvasEngine.prototype.registerBlock = function (blockDef) {
    this._blockDefs[blockDef.id] = blockDef;
  };

  /**
   * 批量注册功能块
   * @param {Array} defs
   */
  CanvasEngine.prototype.registerBlocks = function (defs) {
    var self = this;
    defs.forEach(function (d) { self.registerBlock(d); });
  };

  /**
   * 在画布上添加一个功能块
   * @param {object|string} blockDef  功能块定义对象或已注册的 id
   * @param {number} x  画布 X 坐标
   * @param {number} y  画布 Y 坐标
   * @param {object} [data]  初始参数覆盖
   * @returns {number}  Drawflow 节点 ID
   */
  CanvasEngine.prototype.addBlock = function (blockDef, x, y, data) {
    if (typeof blockDef === 'string') {
      blockDef = this._blockDefs[blockDef];
      if (!blockDef) {
        console.error('CanvasEngine.addBlock: 未找到功能块定义', blockDef);
        return null;
      }
    }

    // 计算端口数量
    var numInputs = 0;
    var numOutputs = 0;
    var inputNames = [];
    var outputNames = [];

    if (typeof blockDef.inputs === 'number') {
      numInputs = blockDef.inputs;
    } else if (Array.isArray(blockDef.inputs)) {
      numInputs = blockDef.inputs.length;
      inputNames = blockDef.inputs.map(function (p) {
        return typeof p === 'string' ? p : (p.name || '');
      });
    }

    if (typeof blockDef.outputs === 'number') {
      numOutputs = blockDef.outputs;
    } else if (Array.isArray(blockDef.outputs)) {
      numOutputs = blockDef.outputs.length;
      outputNames = blockDef.outputs.map(function (p) {
        return typeof p === 'string' ? p : (p.name || '');
      });
    }

    // 合并参数默认值和用户覆盖
    var nodeData = {};
    if (blockDef.params && Array.isArray(blockDef.params)) {
      blockDef.params.forEach(function (p) {
        nodeData[p.key] = (data && data[p.key] !== undefined) ? data[p.key] : p.default;
      });
    }
    if (data) {
      // 覆盖非参数字段
      Object.keys(data).forEach(function (k) {
        if (nodeData[k] === undefined) nodeData[k] = data[k];
      });
    }
    nodeData._blockId = blockDef.id;
    nodeData._blockName = data && data.name ? data.name : blockDef.name;

    // 生成节点 HTML
    var html = this.renderNodeHTML(blockDef, nodeData);

    // CSS class: 类别 + 功能块类型
    var cssClass = 'cat-' + (blockDef.category || 'transfer');

    // 添加到 Drawflow
    var nodeId = this._editor.addNode(
      blockDef.id,      // name (用作 CSS class)
      numInputs,
      numOutputs,
      x, y,
      cssClass,
      nodeData,
      html
    );

    // 记录映射
    this._nodeBlockMap[nodeId] = blockDef.id;
    this._nodeDataMap[nodeId] = nodeData;

    return nodeId;
  };

  /**
   * 删除当前选中的节点
   */
  CanvasEngine.prototype.removeSelected = function () {
    var sel = this._editor.node_selected;
    if (sel) {
      // sel 是 DOM 元素，提取 ID
      var idStr = sel.id; // "node-3"
      if (idStr) {
        var nodeId = parseInt(idStr.replace('node-', ''), 10);
        if (!isNaN(nodeId)) {
          this._editor.removeNodeId('node-' + nodeId);
          delete this._nodeBlockMap[nodeId];
          delete this._nodeDataMap[nodeId];
        }
      }
    }
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - 节点 HTML 渲染
     ═══════════════════════════════════════════════════════════ */

  /**
   * 生成 SAMA 风格的节点 HTML
   * @param {object} blockDef  功能块定义
   * @param {object} data      节点数据
   * @returns {string}         HTML 字符串
   */
  CanvasEngine.prototype.renderNodeHTML = function (blockDef, data) {
    data = data || {};
    var typeId = blockDef.id || '';
    var cat = blockDef.category || 'transfer';
    var catMeta = CATEGORY_META[cat] || { color: '#64748b' };
    var color = catMeta.color;

    // SAMA 图标风格：简洁方块 + 名称
    // 信号端子显示标签名
    var label = typeId;
    if ((typeId === 'input' || typeId === 'output') && data.tag) {
      label = data.tag;
    } else if (typeId === 'CON' && data.value !== undefined) {
      label = String(data.value);
    }

    var html = '<div class="sama-node" style="border-left:4px solid ' + color + ';">';
    html += '<div class="sama-label">' + esc(label) + '</div>';
    html += '<div class="node-value" data-node-value></div>';
    html += '</div>';
    return html;
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - 导入/导出
     ═══════════════════════════════════════════════════════════ */

  /**
   * 导出画布为 JSON
   * @returns {object}  包含 drawflow 数据和 meta 数据
   */
  CanvasEngine.prototype.exportJSON = function () {
    return {
      version: 1,
      drawflow: this._editor.export(),
      meta: {
        blockDefs: deepClone(this._blockDefs),
        nodeBlockMap: deepClone(this._nodeBlockMap),
        nodeDataMap: deepClone(this._nodeDataMap),
        exportTime: new Date().toISOString()
      }
    };
  };

  /**
   * 导入画布 JSON
   * @param {object} data  exportJSON 的输出，或原始 Drawflow export 格式
   */
  CanvasEngine.prototype.importJSON = function (data) {
    this._suppressHistory = true;
    try {
      if (data && data.version && data.drawflow) {
        // 完整格式
        this._editor.import(data.drawflow);
        if (data.meta) {
          if (data.meta.blockDefs) {
            var self = this;
            Object.keys(data.meta.blockDefs).forEach(function (k) {
              self._blockDefs[k] = data.meta.blockDefs[k];
            });
          }
          this._nodeBlockMap = data.meta.nodeBlockMap || {};
          this._nodeDataMap = data.meta.nodeDataMap || {};
        }
      } else if (data && data.drawflow) {
        // 原始 Drawflow 格式
        this._editor.import(data);
      } else {
        console.warn('CanvasEngine.importJSON: 无法识别的数据格式');
      }
    } catch (e) {
      console.error('CanvasEngine.importJSON: 导入失败', e);
    }
    this._suppressHistory = false;
    this._pushHistory();
    this._updateZoomIndicator();
  };

  /**
   * 清空画布
   */
  CanvasEngine.prototype.clear = function () {
    this._editor.clear();
    this._nodeBlockMap = {};
    this._nodeDataMap = {};
    this._monitorValues = {};
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - 视图控制
     ═══════════════════════════════════════════════════════════ */

  CanvasEngine.prototype.zoomIn = function () {
    this._editor.zoom_in();
    this._updateZoomIndicator();
  };

  CanvasEngine.prototype.zoomOut = function () {
    this._editor.zoom_out();
    this._updateZoomIndicator();
  };

  CanvasEngine.prototype.zoomReset = function () {
    this._editor.zoom_reset();
    this._updateZoomIndicator();
  };

  /**
   * 自动适应视图，将所有节点缩放到可见范围
   */
  CanvasEngine.prototype.fitView = function () {
    var data = this._editor.export();
    var nodes = [];
    var home = data.drawflow && data.drawflow.Home;
    if (!home || !home.data) {
      this.zoomReset();
      return;
    }

    Object.keys(home.data).forEach(function (id) {
      var n = home.data[id];
      nodes.push({ x: n.pos_x, y: n.pos_y });
    });

    if (nodes.length === 0) {
      this.zoomReset();
      return;
    }

    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodes.forEach(function (n) {
      if (n.x < minX) minX = n.x;
      if (n.y < minY) minY = n.y;
      if (n.x > maxX) maxX = n.x;
      if (n.y > maxY) maxY = n.y;
    });

    // 加边距
    var pad = 100;
    minX -= pad; minY -= pad;
    maxX += pad + 200; // 节点宽度补偿
    maxY += pad + 80;

    var w = this._container.clientWidth;
    var h = this._container.clientHeight;
    var scaleX = w / (maxX - minX);
    var scaleY = h / (maxY - minY);
    var scale = Math.min(scaleX, scaleY, 1.5);
    scale = Math.max(scale, 0.2);

    // 重置并设置
    this._editor.zoom_reset();
    // Drawflow 没有直接设置 zoom/pan 的 API，用 transform 方式
    var cx = (minX + maxX) / 2;
    var cy = (minY + maxY) / 2;
    this._editor.canvas_x = w / 2 - cx * scale;
    this._editor.canvas_y = h / 2 - cy * scale;
    this._editor.zoom = scale;

    // 应用 transform
    var precanvas = this._container.querySelector('.drawflow');
    if (precanvas) {
      precanvas.style.transform =
        'translate(' + this._editor.canvas_x + 'px, ' + this._editor.canvas_y + 'px) scale(' + scale + ')';
    }

    this._updateZoomIndicator();
  };

  /**
   * 切换网格类型
   * @param {string} [type]  'dots'|'lines'|'none'，不传则循环切换
   */
  CanvasEngine.prototype.toggleGrid = function (type) {
    if (type) {
      this._gridType = type;
    } else {
      var cycle = ['dots', 'lines', 'none'];
      var idx = cycle.indexOf(this._gridType);
      this._gridType = cycle[(idx + 1) % cycle.length];
    }
    this._applyGrid();
    return this._gridType;
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - 撤销/重做
     ═══════════════════════════════════════════════════════════ */

  CanvasEngine.prototype.undo = function () {
    if (this._undoStack.length <= 1) return false;
    var current = this._undoStack.pop();
    this._redoStack.push(current);
    var prev = this._undoStack[this._undoStack.length - 1];
    this._restoreSnapshot(prev);
    return true;
  };

  CanvasEngine.prototype.redo = function () {
    if (this._redoStack.length === 0) return false;
    var next = this._redoStack.pop();
    this._undoStack.push(next);
    this._restoreSnapshot(next);
    return true;
  };

  CanvasEngine.prototype.canUndo = function () {
    return this._undoStack.length > 1;
  };

  CanvasEngine.prototype.canRedo = function () {
    return this._redoStack.length > 0;
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - 模式切换
     ═══════════════════════════════════════════════════════════ */

  /**
   * 设置模式
   * @param {string} mode  'config' 或 'monitor'
   */
  CanvasEngine.prototype.setMode = function (mode) {
    this._mode = mode;
    var parent = this._container.closest('.canvas-layout') || this._container.parentElement;
    if (mode === 'monitor') {
      parent.classList.add('monitor-mode');
      this._editor.editor_mode = 'fixed'; // 禁止拖拽连线
    } else {
      parent.classList.remove('monitor-mode');
      this._editor.editor_mode = 'edit';
    }
    // 更新工具栏模式指示器
    this._updateToolbarMode();
  };

  CanvasEngine.prototype.getMode = function () {
    return this._mode;
  };

  /**
   * 更新监视值
   * @param {object} values  { nodeId: { output: value, ... }, ... }
   */
  CanvasEngine.prototype.updateMonitorValues = function (values) {
    var self = this;
    this._monitorValues = values || {};

    Object.keys(values).forEach(function (nodeId) {
      var nodeEl = self._container.querySelector('#node-' + nodeId);
      if (!nodeEl) return;

      var valObj = values[nodeId];
      var displayVal = '';
      if (typeof valObj === 'object' && valObj !== null) {
        displayVal = valObj.output !== undefined ? String(valObj.output) : JSON.stringify(valObj);
      } else {
        displayVal = String(valObj);
      }

      var valEl = nodeEl.querySelector('[data-node-value]');
      if (valEl) {
        valEl.textContent = displayVal;

        // 值域颜色判断
        valEl.classList.remove('val-normal', 'val-warning', 'val-alarm');
        var numVal = parseFloat(displayVal);
        if (!isNaN(numVal)) {
          if (valObj.alarm) {
            valEl.classList.add('val-alarm');
          } else if (valObj.warning) {
            valEl.classList.add('val-warning');
          } else {
            valEl.classList.add('val-normal');
          }
        }
      }
    });
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - 事件回调
     ═══════════════════════════════════════════════════════════ */

  CanvasEngine.prototype.onNodeSelected = function (callback) {
    this._onNodeSelected = callback;
  };

  CanvasEngine.prototype.onNodeDeselected = function (callback) {
    this._onNodeDeselected = callback;
  };

  CanvasEngine.prototype.onCanvasChanged = function (callback) {
    this._onCanvasChanged = callback;
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - 节点数据操作
     ═══════════════════════════════════════════════════════════ */

  /**
   * 获取节点数据
   * @param {number} nodeId
   * @returns {object|null}
   */
  CanvasEngine.prototype.getNodeData = function (nodeId) {
    try {
      var dfData = this._editor.getNodeFromId(nodeId);
      if (dfData) {
        return deepClone(dfData.data || {});
      }
    } catch (e) {}
    return this._nodeDataMap[nodeId] ? deepClone(this._nodeDataMap[nodeId]) : null;
  };

  /**
   * 设置节点数据的某个字段
   * @param {number} nodeId
   * @param {string} key
   * @param {*} value
   */
  CanvasEngine.prototype.setNodeData = function (nodeId, key, value) {
    try {
      var dfNode = this._editor.getNodeFromId(nodeId);
      if (dfNode) {
        dfNode.data[key] = value;
        this._editor.updateNodeDataFromId(nodeId, dfNode.data);
      }
    } catch (e) {
      console.warn('CanvasEngine.setNodeData 失败:', e);
    }

    if (this._nodeDataMap[nodeId]) {
      this._nodeDataMap[nodeId][key] = value;
    }
  };

  /**
   * 刷新节点显示（数据变更后调用）
   * @param {number} nodeId
   */
  CanvasEngine.prototype.updateNodeDisplay = function (nodeId) {
    var blockId = this._nodeBlockMap[nodeId];
    var blockDef = this._blockDefs[blockId];
    if (!blockDef) return;

    var data = this.getNodeData(nodeId);
    if (!data) return;

    var html = this.renderNodeHTML(blockDef, data);

    // 更新 DOM
    var nodeEl = this._container.querySelector('#node-' + nodeId + ' .drawflow_content_node');
    if (nodeEl) {
      nodeEl.innerHTML = html;
    }
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - 工具栏渲染
     ═══════════════════════════════════════════════════════════ */

  /**
   * 渲染标准工具栏到指定容器
   * @param {string} toolbarId  工具栏容器元素 id
   */
  CanvasEngine.prototype.renderToolbar = function (toolbarId) {
    var el = document.getElementById(toolbarId);
    if (!el) return;

    this._toolbarEl = el;
    el.className = (el.className || '') + ' canvas-toolbar';

    var self = this;

    // 工具按钮定义
    var buttons = [
      { icon: '\u2190', label: '撤销', id: 'tb-undo', action: function () { self.undo(); self._updateToolbarState(); } },
      { icon: '\u2192', label: '重做', id: 'tb-redo', action: function () { self.redo(); self._updateToolbarState(); } },
      'sep',
      { icon: '\u002B', label: '放大', action: function () { self.zoomIn(); } },
      { icon: '\u2212', label: '缩小', action: function () { self.zoomOut(); } },
      { icon: '\u25A3', label: '适应', action: function () { self.fitView(); } },
      { icon: '1:1',   label: '重置', action: function () { self.zoomReset(); } },
      'sep',
      { icon: '\u25A6', label: '网格', id: 'tb-grid', action: function () { self.toggleGrid(); } },
      'sep',
      { icon: '\u2716', label: '删除', action: function () { self.removeSelected(); } },
      { icon: '\u2327', label: '清空', action: function () {
        if (confirm('确定清空画布？此操作不可撤销。')) { self.clear(); }
      }},
      'spacer',
      { icon: '\u25B6', label: '配置', id: 'tb-mode-config', cls: 'active', action: function () { self.setMode('config'); } },
      { icon: '\u23FA', label: '监视', id: 'tb-mode-monitor', action: function () { self.setMode('monitor'); } },
      'sep',
      { icon: '\u2B07', label: '导出', action: function () { self._exportToFile(); } },
      { icon: '\u2B06', label: '导入', action: function () { self._importFromFile(); } }
    ];

    var fragment = document.createDocumentFragment();

    buttons.forEach(function (b) {
      if (b === 'sep') {
        var sep = document.createElement('span');
        sep.className = 'tb-sep';
        fragment.appendChild(sep);
        return;
      }
      if (b === 'spacer') {
        var sp = document.createElement('span');
        sp.className = 'tb-spacer';
        fragment.appendChild(sp);
        return;
      }

      var btn = document.createElement('button');
      btn.className = 'tb-btn' + (b.cls ? ' ' + b.cls : '');
      btn.title = b.label;
      if (b.id) btn.id = b.id;

      var iconSpan = document.createElement('span');
      iconSpan.className = 'tb-icon';
      iconSpan.textContent = b.icon;
      btn.appendChild(iconSpan);

      var labelSpan = document.createElement('span');
      labelSpan.className = 'tb-label';
      labelSpan.textContent = b.label;
      btn.appendChild(labelSpan);

      btn.addEventListener('click', b.action);
      fragment.appendChild(btn);
    });

    el.innerHTML = '';
    el.appendChild(fragment);

    this._updateToolbarState();
  };

  /** 更新工具栏按钮状态 */
  CanvasEngine.prototype._updateToolbarState = function () {
    if (!this._toolbarEl) return;

    var undoBtn = this._toolbarEl.querySelector('#tb-undo');
    var redoBtn = this._toolbarEl.querySelector('#tb-redo');

    if (undoBtn) {
      undoBtn.classList.toggle('disabled', !this.canUndo());
    }
    if (redoBtn) {
      redoBtn.classList.toggle('disabled', !this.canRedo());
    }
  };

  /** 更新工具栏模式按钮 */
  CanvasEngine.prototype._updateToolbarMode = function () {
    if (!this._toolbarEl) return;

    var configBtn = this._toolbarEl.querySelector('#tb-mode-config');
    var monitorBtn = this._toolbarEl.querySelector('#tb-mode-monitor');

    if (configBtn) {
      configBtn.classList.toggle('active', this._mode === 'config');
    }
    if (monitorBtn) {
      monitorBtn.classList.toggle('active', this._mode === 'monitor');
    }
  };

  /** 导出为文件下载 */
  CanvasEngine.prototype._exportToFile = function () {
    var json = this.exportJSON();
    var blob = new Blob([JSON.stringify(json, null, 2)], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'canvas_' + new Date().toISOString().slice(0, 10) + '.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  /** 从文件导入 */
  CanvasEngine.prototype._importFromFile = function () {
    var self = this;
    var input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.style.display = 'none';

    input.addEventListener('change', function (e) {
      var file = e.target.files[0];
      if (!file) return;

      var reader = new FileReader();
      reader.onload = function (ev) {
        try {
          var data = JSON.parse(ev.target.result);
          self.importJSON(data);
        } catch (err) {
          console.error('CanvasEngine: 导入文件解析失败', err);
          alert('导入失败: 文件格式不正确');
        }
      };
      reader.readAsText(file);
    });

    document.body.appendChild(input);
    input.click();
    document.body.removeChild(input);
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - 调色板渲染
     ═══════════════════════════════════════════════════════════ */

  /**
   * 渲染功能块调色板
   * @param {string} paletteId  调色板容器元素 id
   * @param {Array} blocks  功能块定义数组（可选，默认用已注册的）
   */
  CanvasEngine.prototype.renderPalette = function (paletteId, blocks) {
    var el = document.getElementById(paletteId);
    if (!el) return;

    el.className = (el.className || '').replace(/\bblock-palette\b/g, '') + ' block-palette';

    var self = this;
    var defs = blocks || Object.values(this._blockDefs);

    // 按类别分组
    var groups = {};
    defs.forEach(function (d) {
      var cat = d.category || 'transfer';
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push(d);
    });

    var html = '';

    // 搜索框
    html += '<div class="palette-search"><input type="text" placeholder="搜索功能块..." id="palette-search-input" /></div>';

    // 按类别渲染
    var catOrder = ['signal', 'arithmetic', 'compare', 'dynamic', 'control', 'logic', 'timer', 'transfer', 'encapsulated'];
    catOrder.forEach(function (cat) {
      if (!groups[cat]) return;
      var meta = CATEGORY_META[cat] || { label: cat, color: '#64748b' };

      html += '<div class="palette-category cat-' + cat + '" data-category="' + cat + '">';
      html += '<div class="palette-category-title">';
      html += '<span class="cat-dot" style="background:' + meta.color + '"></span>';
      html += '<span>' + esc(meta.label) + ' (' + groups[cat].length + ')</span>';
      html += '<span class="cat-arrow">\u25BC</span>';
      html += '</div>';
      html += '<div class="palette-blocks">';

      groups[cat].forEach(function (d) {
        html += '<div class="palette-block" draggable="true" data-block-id="' + esc(d.id) + '"';
        html += ' title="' + esc(d.description || d.name) + '">';
        html += '<span class="block-icon"></span>';
        html += '<span class="block-label">' + esc(d.name) + '</span>';
        html += '<span class="block-id">' + esc(d.id) + '</span>';
        html += '</div>';
      });

      html += '</div></div>';
    });

    el.innerHTML = html;

    // 绑定拖拽事件
    var paletteBlocks = el.querySelectorAll('.palette-block');
    paletteBlocks.forEach(function (block) {
      block.addEventListener('dragstart', function (e) {
        e.dataTransfer.setData('blockId', block.getAttribute('data-block-id'));
        e.dataTransfer.effectAllowed = 'copy';
      });
    });

    // 画布接收拖拽
    this._container.addEventListener('dragover', function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    });

    this._container.addEventListener('drop', function (e) {
      e.preventDefault();
      var blockId = e.dataTransfer.getData('blockId');
      if (!blockId) return;

      // 计算画布坐标
      var rect = self._container.getBoundingClientRect();
      var zoom = self._editor.zoom || 1;
      var canvasX = self._editor.canvas_x || 0;
      var canvasY = self._editor.canvas_y || 0;
      var x = (e.clientX - rect.left - canvasX) / zoom;
      var y = (e.clientY - rect.top - canvasY) / zoom;

      self.addBlock(blockId, x, y);
    });

    // 搜索过滤
    var searchInput = el.querySelector('#palette-search-input');
    if (searchInput) {
      searchInput.addEventListener('input', function () {
        var query = this.value.toLowerCase().trim();
        paletteBlocks.forEach(function (block) {
          var label = (block.querySelector('.block-label') || {}).textContent || '';
          var id = block.getAttribute('data-block-id') || '';
          var match = !query || label.toLowerCase().indexOf(query) >= 0 || id.toLowerCase().indexOf(query) >= 0;
          block.style.display = match ? '' : 'none';
        });
        // 隐藏空类别
        el.querySelectorAll('.palette-category').forEach(function (cat) {
          var visibleBlocks = cat.querySelectorAll('.palette-block:not([style*="display: none"])');
          cat.style.display = visibleBlocks.length > 0 || !query ? '' : 'none';
        });
      });
    }

    // 类别折叠
    el.querySelectorAll('.palette-category-title').forEach(function (title) {
      title.addEventListener('click', function () {
        title.parentElement.classList.toggle('collapsed');
      });
    });
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - 属性面板辅助
     ═══════════════════════════════════════════════════════════ */

  /**
   * 渲染属性面板内容（选中某节点时调用）
   * @param {string} panelId  属性面板容器 id
   * @param {number} nodeId   选中节点 ID
   */
  CanvasEngine.prototype.renderProperties = function (panelId, nodeId) {
    var el = document.getElementById(panelId);
    if (!el) return;

    if (!nodeId) {
      el.innerHTML = '<div class="props-header"><h4>属性</h4></div>' +
                     '<div class="props-body"><div class="prop-empty">选择一个功能块查看属性</div></div>';
      return;
    }

    var data = this.getNodeData(nodeId);
    var blockId = this._nodeBlockMap[nodeId];
    var blockDef = this._blockDefs[blockId];

    var html = '<div class="props-header"><h4>属性</h4></div>';
    html += '<div class="props-body">';

    // 基本信息
    html += '<div class="prop-section">';
    html += '<div class="prop-section-title">基本信息</div>';
    html += '<div class="prop-node-id">ID: ' + nodeId + '</div>';

    html += '<div class="prop-row">';
    html += '<label>名称</label>';
    html += '<input type="text" value="' + esc(data._blockName || '') + '" data-prop-key="_blockName" />';
    html += '</div>';

    if (blockDef) {
      html += '<div class="prop-row">';
      html += '<label>类型</label>';
      html += '<input type="text" value="' + esc(blockDef.id) + '" readonly />';
      html += '</div>';

      html += '<div class="prop-row">';
      html += '<label>类别</label>';
      html += '<input type="text" value="' + esc((CATEGORY_META[blockDef.category] || {}).label || blockDef.category || '') + '" readonly />';
      html += '</div>';
    }
    html += '</div>';

    // 参数
    if (blockDef && blockDef.params && blockDef.params.length > 0) {
      html += '<div class="prop-section">';
      html += '<div class="prop-section-title">参数</div>';

      blockDef.params.forEach(function (p) {
        var val = data[p.key] !== undefined ? data[p.key] : (p.default || '');
        var inputType = p.type === 'number' ? 'number' : 'text';
        html += '<div class="prop-row">';
        html += '<label>' + esc(p.label || p.key) + '</label>';
        html += '<input type="' + inputType + '" value="' + esc(String(val)) + '" data-prop-key="' + esc(p.key) + '"';
        if (p.type === 'number') {
          html += ' step="any"';
        }
        html += ' />';
        html += '</div>';
      });

      html += '</div>';
    }

    // 描述
    if (blockDef && blockDef.description) {
      html += '<div class="prop-section">';
      html += '<div class="prop-section-title">说明</div>';
      html += '<p style="font-size:12px;color:#64748b;line-height:1.5;">' + esc(blockDef.description) + '</p>';
      html += '</div>';
    }

    html += '</div>';
    el.innerHTML = html;

    // 绑定输入事件
    var self = this;
    el.querySelectorAll('[data-prop-key]').forEach(function (input) {
      input.addEventListener('change', function () {
        var key = this.getAttribute('data-prop-key');
        var val = this.value;
        if (this.type === 'number') {
          val = parseFloat(val);
          if (isNaN(val)) val = 0;
        }
        self.setNodeData(nodeId, key, val);
        self.updateNodeDisplay(nodeId);
      });
    });
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - 键盘快捷键
     ═══════════════════════════════════════════════════════════ */

  /**
   * 启用键盘快捷键
   * Delete: 删除选中节点
   * Ctrl+Z: 撤销
   * Ctrl+Y / Ctrl+Shift+Z: 重做
   */
  CanvasEngine.prototype.enableKeyboard = function () {
    var self = this;

    document.addEventListener('keydown', function (e) {
      // 忽略输入框中的按键
      var tag = e.target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      // Delete
      if (e.key === 'Delete' || e.key === 'Backspace') {
        self.removeSelected();
        e.preventDefault();
        return;
      }

      // Ctrl+Z: 撤销
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key === 'z') {
        self.undo();
        self._updateToolbarState();
        e.preventDefault();
        return;
      }

      // Ctrl+Y or Ctrl+Shift+Z: 重做
      if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.shiftKey && e.key === 'Z'))) {
        self.redo();
        self._updateToolbarState();
        e.preventDefault();
        return;
      }
    });
  };


  /* ═══════════════════════════════════════════════════════════
     公共 API - Drawflow 编辑器实例访问
     ═══════════════════════════════════════════════════════════ */

  /** 获取底层 Drawflow 编辑器实例 */
  CanvasEngine.prototype.getEditor = function () {
    return this._editor;
  };

  /** 获取类别元数据 */
  CanvasEngine.CATEGORIES = CATEGORY_META;


  /* ═══════════════════════════════════════════════════════════
     全局注册
     ═══════════════════════════════════════════════════════════ */

  window.CanvasEngine = CanvasEngine;

})();
