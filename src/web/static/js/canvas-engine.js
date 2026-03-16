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
  };

  /* ── 开关量/逻辑块类型集合（连线虚线判断用） ── */
  var DIGITAL_TYPES = {
    AND: true, OR: true, NOT: true, XOR: true,
    SR: true, RS: true,
    TON: true, TOFF: true, TP: true, CTR: true,
    CMP: true, AC: true, AC1: true
  };

  /* ── 端口类型推断 (Phase 0) ────────────────────────── */

  /**
   * PORT_TYPE_MAP: 按功能块类别和具体块 ID 推断端口 dataType
   * 规则:
   *   logic 类 (A, OR, NOT, EOR, FFR, FFS, LCK, UCK): 全 bool
   *   compare 类 (CMP, AC, AC1, HHV, VHV, WCM): inputs=float, outputs=bool
   *   timer 类 (TB, TBD, TD, THF, TP, TW, TWO, TWF, RT, FLK, AW): inputs=bool, outputs=bool
   *   arithmetic/dynamic/control 类: 全 float
   *   signal 类 (input, output, ref_in, ref_out, constant, CON): 'any'
   *   混合端口块 (DSW: float,float,bool→float; ASW: float,float,bool→float;
   *              HLD: float,bool→float; RLH: float,bool→float)
   *   CNT: bool,bool→float
   *   未知块: 默认 float
   */
  var PORT_TYPE_MAP = {
    // logic 类 — 全 bool
    A:    { inputs: 'bool', outputs: 'bool' },
    OR:   { inputs: 'bool', outputs: 'bool' },
    NOT:  { inputs: 'bool', outputs: 'bool' },
    EOR:  { inputs: 'bool', outputs: 'bool' },
    LG:   { inputs: 'bool', outputs: 'bool' },
    FFR:  { inputs: 'bool', outputs: 'bool' },
    FFS:  { inputs: 'bool', outputs: 'bool' },
    LCK:  { inputs: 'bool', outputs: 'bool' },
    UCK:  { inputs: 'bool', outputs: 'bool' },
    // compare 类 — inputs=float, outputs=bool
    CMP:  { inputs: 'float', outputs: 'bool' },
    AC:   { inputs: 'float', outputs: 'bool' },
    AC1:  { inputs: 'float', outputs: 'bool' },
    HHV:  { inputs: 'float', outputs: 'bool' },
    VHV:  { inputs: 'float', outputs: 'bool' },
    WCM:  { inputs: 'float', outputs: 'bool' },
    // timer 类 — inputs=bool, outputs=bool
    TB:   { inputs: 'bool', outputs: 'bool' },
    TBD:  { inputs: 'bool', outputs: 'bool' },
    TD:   { inputs: 'bool', outputs: 'bool' },
    THF:  { inputs: 'bool', outputs: 'bool' },
    TP:   { inputs: 'bool', outputs: 'bool' },
    TW:   { inputs: 'bool', outputs: 'bool' },
    TWF:  { inputs: 'bool', outputs: 'bool' },
    TWO:  { inputs: 'bool', outputs: 'bool' },
    RT:   { inputs: 'bool', outputs: 'bool' },
    FLK:  { inputs: 'bool', outputs: 'bool' },
    AW:   { inputs: 'bool', outputs: 'bool' },
    // signal 类 — 'any'
    input:    { inputs: 'any', outputs: 'any' },
    output:   { inputs: 'any', outputs: 'any' },
    ref_in:   { inputs: 'any', outputs: 'any' },
    ref_out:  { inputs: 'any', outputs: 'any' },
    constant: { inputs: 'any', outputs: 'any' },
    CON:      { inputs: 'any', outputs: 'any' },
    // 混合端口块 — 按端口索引区分
    DSW:  { inputs: ['float', 'float', 'bool'], outputs: 'float' },
    ASW:  { inputs: ['float', 'float', 'bool'], outputs: 'float' },
    ASWW: { inputs: ['float', 'float', 'bool'], outputs: 'float' },
    HLD:  { inputs: ['float', 'bool'], outputs: 'float' },
    RLH:  { inputs: ['float', 'bool'], outputs: 'float' },
    CNT:  { inputs: ['bool', 'bool'], outputs: 'float' },
    // HS/LS — float 选择
    HS:   { inputs: 'float', outputs: 'float' },
    LS:   { inputs: 'float', outputs: 'float' },
    HSG:  { inputs: 'float', outputs: 'float' },
    LSG:  { inputs: 'float', outputs: 'float' },
    SEL:  { inputs: 'float', outputs: 'float' },
    NTH:  { inputs: 'float', outputs: 'float' },
    // BP1 — transfer
    BP1:  { inputs: 'float', outputs: 'float' },
    // DELAY — 一拍延迟
    DELAY: { inputs: 'any', outputs: 'any' }
  };

  /**
   * 获取端口类型
   * @param {string|number} blockIdOrNodeId  块类型 ID 或 Drawflow 节点 ID
   * @param {string} direction  'input' 或 'output'
   * @param {number} portIndex  端口索引（0-based）
   * @returns {string}  'float' | 'bool' | 'any'
   */
  function getPortType(blockIdOrNodeId, direction, portIndex) {
    var blockId = blockIdOrNodeId;
    // 如果传入的是数字（节点 ID），需要通过 engine 实例查找
    // 但此函数是静态的，所以我们假设传入的是 blockId
    var mapping = PORT_TYPE_MAP[blockId];
    if (!mapping) return 'float'; // 默认 float

    var key = direction === 'input' ? 'inputs' : 'outputs';
    var typeDef = mapping[key];
    if (Array.isArray(typeDef)) {
      return typeDef[portIndex] || typeDef[typeDef.length - 1] || 'float';
    }
    return typeDef || 'float';
  }

  /**
   * 检查两个端口类型是否兼容
   * 规则：'any' 与任何类型兼容，否则必须同类型
   */
  function areTypesCompatible(srcType, dstType) {
    if (srcType === 'any' || dstType === 'any') return true;
    return srcType === dstType;
  }

  // 暴露为全局函数供其他模块使用
  window.getPortType = getPortType;
  window.areTypesCompatible = areTypesCompatible;

  /* ── 异常值集合（监视模式 ERR 判断用） ── */
  var ERROR_VALUES = { 'null': true, 'None': true, 'NaN': true, 'Infinity': true, '-Infinity': true };

  /* ── 类别元数据默认值 ── */
  var DEFAULT_CAT_META = { color: '#64748b', label: '' };

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

  /** 安全解析数值 */
  function parseNumSafe(val, fallback) {
    var n = parseFloat(val);
    return isNaN(n) ? (fallback !== undefined ? fallback : 0) : n;
  }

  /** 获取类别元数据（带默认值） */
  function getCatMeta(category) {
    return CATEGORY_META[category] || DEFAULT_CAT_META;
  }

  /**
   * 生成参数编辑表单 HTML（_openSidePanel 和 _openParamModal 共用）
   * @param {string} blockId   块类型 ID
   * @param {object} blockDef  块定义
   * @param {object} data      节点数据
   * @param {number} nodeId    节点 ID
   * @returns {{ header: string, body: string, footer: string }}
   */
  function buildParamFormHTML(blockId, blockDef, data, nodeId) {
    var catMeta = blockDef ? getCatMeta(blockDef.category) : DEFAULT_CAT_META;
    var params = (blockDef && blockDef.params) ? blockDef.params : [];

    // 头部
    var header = '';
    header += '<div class="ce-modal-header-color" style="background:' + catMeta.color + '"></div>';
    header += '<div class="ce-modal-header-text">';
    header += '<strong>' + esc(blockDef ? blockDef.id : blockId) + '</strong>';
    header += ' <span style="color:#64748b;font-size:13px">' + esc(blockDef ? blockDef.name : '') + '</span>';
    header += '</div>';
    header += '<button class="ce-modal-close" data-close>&times;</button>';

    // 内容
    var body = '';
    if (blockId === 'comment') {
      var textVal = data.text !== undefined ? data.text : '注释';
      var fsVal = data.fontSize !== undefined ? data.fontSize : 12;
      var colVal = data.color || '#64748b';
      body += '<div class="ce-modal-field">';
      body += '<label>注释内容</label>';
      body += '<textarea data-key="text" rows="4" style="width:100%;resize:vertical;font-size:12px;font-family:inherit;padding:4px 6px;border:1px solid #d4d4d4;">' + esc(textVal) + '</textarea>';
      body += '</div>';
      body += '<div class="ce-modal-field" style="display:flex;gap:10px;">';
      body += '<div style="flex:1;"><label>字号(px)</label>';
      body += '<input type="number" value="' + esc(String(fsVal)) + '" data-key="fontSize" min="8" max="48" step="1"></div>';
      body += '<div style="flex:1;"><label>颜色</label>';
      body += '<input type="color" value="' + esc(colVal) + '" data-key="color" style="width:100%;height:28px;padding:0;border:1px solid #d4d4d4;cursor:pointer;"></div>';
      body += '</div>';
    } else if (blockId === 'input' || blockId === 'output') {
      body += '<div class="ce-modal-field">';
      body += '<label>信号标签 (tag)</label>';
      body += '<input type="text" value="' + esc(data.tag || data.name || '') + '" data-key="tag" class="ce-field-primary">';
      body += '</div>';
      if (blockId === 'input') {
        body += '<div class="ce-modal-field">';
        body += '<label>默认值</label>';
        body += '<input type="number" step="any" value="' + esc(String(data.default || 0)) + '" data-key="default">';
        body += '</div>';
      }
    }
    if (params.length > 0) {
      body += '<div class="ce-modal-section-title">参数</div>';
      params.forEach(function (p) {
        var val = data[p.key] !== undefined ? data[p.key] : (p.default !== undefined ? p.default : '');
        body += '<div class="ce-modal-field">';
        body += '<label>' + esc(p.label || p.key) + '</label>';
        body += '<input type="' + (p.type === 'number' ? 'number' : 'text') + '" ';
        body += 'value="' + esc(String(val)) + '" data-key="' + esc(p.key) + '"';
        if (p.type === 'number') body += ' step="any"';
        body += '>';
        body += '</div>';
      });
    }
    if (blockDef && blockDef.description) {
      body += '<div class="ce-modal-desc">' + esc(blockDef.description) + '</div>';
    }

    // 底部
    var footer = '';
    footer += '<span class="ce-modal-node-id">Node #' + nodeId + '</span>';
    footer += '<button class="ce-modal-btn ce-btn-cancel" data-close>取消</button>';
    footer += '<button class="ce-modal-btn ce-btn-ok" data-ok>确定</button>';

    return { header: header, body: body, footer: footer };
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
    this._editor.curvature = 0;             // 关闭默认曲线
    this._editor.reroute_curvature_start_end = 0;
    this._editor.reroute_curvature = 0;
    this._editor.start();

    // Manhattan 直角折线：覆写 Drawflow 的 createCurvature 方法
    this._patchManhattanRouting();

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
    this._savedSnapshot = null;   // 上次保存时的快照（用于脏检测）
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

    // Fix 6: 节点拖拽释放 → snap to 20px 网格
    editor.on('nodeMoved', function (nodeId) {
      try {
        var dfNode = editor.getNodeFromId(nodeId);
        if (!dfNode) return;
        var sx = Math.round(dfNode.pos_x / 20) * 20;
        var sy = Math.round(dfNode.pos_y / 20) * 20;
        if (sx !== dfNode.pos_x || sy !== dfNode.pos_y) {
          var el = self._container.querySelector('#node-' + nodeId);
          if (el) {
            el.style.left = sx + 'px';
            el.style.top = sy + 'px';
          }
          dfNode.pos_x = sx;
          dfNode.pos_y = sy;
          editor.updateConnectionNodes('node-' + nodeId);
        }
      } catch (e) {}
    });

    // Fix 4: 连线创建时分类（开关量 → 虚线）
    editor.on('connectionCreated', function (info) {
      self._classifyConnection(info);
    });

    // 缩放变化
    editor.on('zoom', function () {
      self._updateZoomIndicator();
    });

    // 鼠标滚轮缩放 (Drawflow 内置支持，但需确保指示器同步)
    this._container.addEventListener('wheel', function () {
      setTimeout(function () {
        self._updateZoomIndicator();
        self._updateGrid();
      }, 0);
    });

    // 平移后更新网格
    this._container.addEventListener('mouseup', function () {
      setTimeout(function () { self._updateGrid(); }, 0);
    });

    // 双击 ref_in → 弹出引出点选择器（特殊处理）
    this._container.addEventListener('dblclick', function (e) {
      if (self._mode !== 'config') return;
      var nodeEl = e.target.closest('.drawflow-node');
      if (!nodeEl) return;
      var nodeId = parseInt(nodeEl.id.replace('node-', ''), 10);
      if (isNaN(nodeId)) return;
      var blockId = self._nodeBlockMap[nodeId];
      if (blockId === 'ref_in') {
        e.preventDefault();
        e.stopPropagation();
        self._openRefPicker(nodeId);
      }
    });

    // 点击画布空白区域 → 关闭侧边面板
    this._container.addEventListener('click', function (e) {
      if (!e.target.closest('.drawflow-node') && !e.target.closest('.ce-side-panel')) {
        self._closeSidePanel();
      }
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

  /** 网格跟随缩放/平移 */
  CanvasEngine.prototype._updateGrid = function () {
    if (this._gridType === 'none') return;
    var zoom = this._editor.zoom || 1;
    var baseSize = 20;
    var size = baseSize * zoom;
    var cx = this._editor.canvas_x || 0;
    var cy = this._editor.canvas_y || 0;
    this._container.style.backgroundSize = size + 'px ' + size + 'px';
    this._container.style.backgroundPosition = cx + 'px ' + cy + 'px';
  };


  /* ── Manhattan 直角折线路径 ─────────────────────────── */

  /**
   * 覆写 Drawflow 内部的 createCurvature 方法，
   * 生成水平-垂直-水平（Z 形）Manhattan 路径。
   *
   * 参数: (startX, startY, endX, endY, curvature, type)
   *   type: 'open'(输出端) / 'close'(输入端) / 'other'
   * 返回: SVG path 的 d 属性字符串
   */
  CanvasEngine.prototype._patchManhattanRouting = function () {
    this._editor.createCurvature = function (startX, startY, endX, endY /*, curvature, type*/) {
      var dx = endX - startX;
      var dy = endY - startY;

      // 最小水平伸出量（确保线从端口水平出发）
      var stub = 20;

      // 正常情况：终点在起点右侧，Z 形折线
      if (dx > stub * 2) {
        var midX = startX + dx / 2;
        return 'M ' + startX + ' ' + startY +
               ' H ' + midX +
               ' V ' + endY +
               ' H ' + endX;
      }

      // 终点在起点左侧或很近 → U 形绕行
      var offsetY = Math.max(Math.abs(dy) * 0.5, 40);
      var dir = dy >= 0 ? 1 : -1;
      var bypassY = (dy === 0) ? startY - offsetY : startY + dir * offsetY;
      var sx = startX + stub;
      var ex = endX - stub;

      return 'M ' + startX + ' ' + startY +
             ' H ' + sx +
             ' V ' + bypassY +
             ' H ' + ex +
             ' V ' + endY +
             ' H ' + endX;
    };
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
  /* ── SAMA 标准图标符号映射 ─────────────────────────── */
  var SAMA_SYMBOLS = {
    // 运算
    ADD: '\u03A3', SUM: '\u03A3', SUB: '\u0394',
    MLT: '\u00D7', ML: '\u00D7', multiply: '\u00D7',
    DIV: '\u00F7',
    ABS: '|x|', POW: 'x\u207F', SQRT: '\u221A',
    AVE: 'AVG',
    G: '\u25B7', gain: '\u25B7',       // 三角形=增益
    // 比较选择
    HS: 'HS', LS: 'LS', CMP: '\u2265',
    AC: '\u2265', AC1: '\u2265',
    NTH: 'Nth', SEL: 'SEL',
    ASW: 'SW', SW: 'SW',
    // 动态
    FLT: '1/Ts', Inertia: '1/Ts',
    I: '\u222B', Integrator: '\u222B',
    LDL: 'LD/LG', LeadLag: 'LD/LG',
    SO: '2nd',
    DB: 'DB', DeadZone: 'DB',
    RL: 'R/L', RateLimiter: 'R/L',
    LIM: '\u2534\u252C', Limiter: '\u2534\u252C',
    // 控制
    PI: 'PI', PID: 'PID', PD: 'PD',
    // 逻辑
    AND: '&', OR: '\u22651', NOT: '\u00AC', XOR: '\u2295',
    SR: 'SR', RS: 'RS',
    // 定时
    TON: 'TON', TOFF: 'TOF', TP: 'TP', CTR: 'CTR',
    // 信号处理
    SH: 'S/H', RAMP: '/', GRAD: '\u2202', DELAY: 'Z\u207B\u00B9',
    SC: 'SC', BG: 'B+G', DEV: '\u0394',
    // 常量
    CON: 'K', constant: 'K',
    // 传递
    sum: '\u03A3',
  };

  /**
   * 生成 SAMA 风格紧凑节点 HTML
   * 功能块：仅显示符号
   * 信号端子：显示 tag 名
   */
  var IO_TYPES = { input: true, output: true, ref_in: true, ref_out: true, io_input: true, il_output: true };

  /**
   * SAMA 图标风格节点渲染
   *
   * 功能块: ┌──ID──┐   IO端子: ┌─▷ tag ─┐  或  ┌─ tag ◁─┐
   *         │ SYM  │          └────────┘      └────────┘
   *         └──────┘
   * 引用:   ┌╌╌tag╌╌┐ (虚线)
   *         └╌╌╌╌╌╌╌┘
   */
  CanvasEngine.prototype.renderNodeHTML = function (blockDef, data) {
    data = data || {};
    var typeId = blockDef.id || '';
    var isIO = !!IO_TYPES[typeId];
    var isRef = (typeId === 'ref_in' || typeId === 'ref_out');
    var isCon = (typeId === 'CON' || typeId === 'constant');
    var isComment = (typeId === 'comment');

    var html = '';

    if (isComment) {
      // 纯文本注释块
      var text = data.text || data.name || '注释';
      var fontSize = parseNumSafe(data.fontSize, 12);
      var color = data.color || '#64748b';
      html = '<div class="sama-node sama-comment">';
      html += '<span class="sama-comment-text" style="font-size:' + fontSize + 'px;color:' + esc(color) + '">' + esc(text) + '</span>';
      html += '</div>';

    } else if (isIO) {
      // IO 端子 / 引用点 — 单行标签块
      var tag = data.tag || data.name || '';
      var arrow = '';
      if (typeId === 'input' || typeId === 'io_input' || typeId === 'ref_in') arrow = '\u25b7 ';   // ▷
      if (typeId === 'output' || typeId === 'il_output' || typeId === 'ref_out') arrow = ' \u25c1'; // ◁

      var cls = 'sama-node sama-io-block';
      if (isRef) cls += ' sama-ref';

      html = '<div class="' + cls + '">';
      if (arrow && (typeId === 'input' || typeId === 'io_input' || typeId === 'ref_in')) {
        html += '<span class="sama-arrow">' + arrow + '</span>';
      }
      html += '<span class="sama-tag">' + esc(tag || typeId) + '</span>';
      if (arrow && (typeId === 'output' || typeId === 'il_output' || typeId === 'ref_out')) {
        html += '<span class="sama-arrow">' + arrow + '</span>';
      }
      html += '<div class="node-value" data-node-value></div>';
      html += '</div>';

    } else if (isCon) {
      // 常数块
      html = '<div class="sama-node sama-func">';
      html += '<div class="sama-sym">' + esc(String(data.value !== undefined ? data.value : 0)) + '</div>';
      html += '<div class="node-value" data-node-value></div>';
      html += '</div>';

    } else {
      // 功能块 — 紧凑黑白 SAMA 布局
      var instanceName = data._blockName || data.name || blockDef.name || typeId;

      // 端口名生成
      var numIn = typeof blockDef.inputs === 'number' ? blockDef.inputs : (Array.isArray(blockDef.inputs) ? blockDef.inputs.length : 0);
      var numOut = typeof blockDef.outputs === 'number' ? blockDef.outputs : (Array.isArray(blockDef.outputs) ? blockDef.outputs.length : 0);
      var inNames = [];
      var outNames = [];
      if (Array.isArray(blockDef.inputs)) {
        inNames = blockDef.inputs.map(function (p) { return typeof p === 'string' ? p : (p.name || ''); });
      }
      if (Array.isArray(blockDef.outputs)) {
        outNames = blockDef.outputs.map(function (p) { return typeof p === 'string' ? p : (p.name || ''); });
      }
      for (var ii = inNames.length; ii < numIn; ii++) { inNames.push(numIn === 1 ? 'IN' : 'IN' + (ii + 1)); }
      for (var oi = outNames.length; oi < numOut; oi++) { outNames.push(numOut === 1 ? 'OUT' : 'OUT' + (oi + 1)); }

      // 关键参数（最多2个，紧凑显示）
      var paramStrs = [];
      if (blockDef.params && Array.isArray(blockDef.params)) {
        blockDef.params.slice(0, 2).forEach(function (p) {
          var v = data[p.key] !== undefined ? data[p.key] : p.default;
          if (v !== undefined && v !== '') paramStrs.push(p.key + '=' + v);
        });
      }

      html = '<div class="sama-node sama-func-full">';
      html += '<div class="node-header">';
      html += '<span class="node-instance">' + esc(instanceName) + '</span>';
      html += '</div>';
      html += '<div class="node-body">';

      // 左侧端口名
      html += '<div class="port-labels port-labels-in">';
      inNames.forEach(function (n) { html += '<div class="port-label">' + esc(n) + '</div>'; });
      html += '</div>';

      // 中间内容
      html += '<div class="node-center">';
      if (paramStrs.length > 0) {
        html += '<div class="node-params">' + esc(paramStrs.join(' ')) + '</div>';
      }
      html += '<div class="node-type">' + esc(typeId) + '</div>';
      html += '</div>';

      // 右侧端口名
      html += '<div class="port-labels port-labels-out">';
      outNames.forEach(function (n) { html += '<div class="port-label">' + esc(n) + '</div>'; });
      html += '</div>';

      html += '</div>'; // node-body
      html += '<div class="node-value" data-node-value></div>';
      html += '</div>'; // sama-func-full
    }

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
    // 重新渲染所有节点 HTML（保存的旧 HTML 可能不匹配当前渲染逻辑）
    this._refreshAllNodeHTML();
    this._pushHistory();
    this._updateZoomIndicator();
    // 导入后标记为干净状态
    this.markSaved();
  };

  /**
   * 标记当前画布为"已保存"（清除脏标记）
   */
  CanvasEngine.prototype.markSaved = function () {
    this._savedSnapshot = JSON.stringify(this._editor.export());
  };

  /**
   * 画布是否有未保存修改
   * @returns {boolean}
   */
  CanvasEngine.prototype.isDirty = function () {
    if (!this._savedSnapshot) return false;
    return JSON.stringify(this._editor.export()) !== this._savedSnapshot;
  };

  /**
   * 重新渲染所有节点的 HTML（导入后刷新用）
   */
  CanvasEngine.prototype._refreshAllNodeHTML = function () {
    var self = this;
    var nodeIds = Object.keys(this._nodeBlockMap);
    nodeIds.forEach(function (nodeId) {
      self.updateNodeDisplay(parseInt(nodeId, 10));
    });
    // 节点 HTML 变化后重新计算连线路径，延迟确保 DOM 已更新
    requestAnimationFrame(function () {
      nodeIds.forEach(function (nodeId) {
        try {
          self._editor.updateConnectionNodes('node-' + nodeId);
        } catch (e) {}
      });
    });
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
   * 居中并高亮指定节点（用于跨页跳转、搜索定位、错误定位）
   * @param {number|string} nodeId  Drawflow 节点 ID
   */
  CanvasEngine.prototype.centerOnNode = function (nodeId) {
    nodeId = parseInt(nodeId, 10);
    if (isNaN(nodeId)) return;
    try {
      var dfNode = this._editor.getNodeFromId(nodeId);
      if (!dfNode) return;
      var w = this._container.clientWidth;
      var h = this._container.clientHeight;
      var zoom = this._editor.zoom || 1;
      // 居中到节点位置
      this._editor.canvas_x = w / 2 - dfNode.pos_x * zoom - 40;
      this._editor.canvas_y = h / 2 - dfNode.pos_y * zoom - 20;
      var precanvas = this._container.querySelector('.drawflow');
      if (precanvas) {
        precanvas.style.transform =
          'translate(' + this._editor.canvas_x + 'px, ' + this._editor.canvas_y + 'px) scale(' + zoom + ')';
      }
      this._updateZoomIndicator();
    } catch (e) {
      console.warn('centerOnNode failed:', e);
    }
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

    // 建立 tag → nodeId 反向映射（从 _nodeDataMap 提取）
    var tagToNodeId = {};
    Object.keys(this._nodeDataMap).forEach(function (nid) {
      var d = self._nodeDataMap[nid];
      var tag = d && (d.tag || d.name);
      if (tag) tagToNodeId[tag] = nid;
    });

    Object.keys(values).forEach(function (key) {
      // key 可能是 tag 名（如 "MWtest"）或节点 ID（如 "3"）
      var nodeId = tagToNodeId[key] || key;
      var nodeEl = self._container.querySelector('#node-' + nodeId);
      if (!nodeEl) return;

      var valObj = values[key];
      var displayVal = '';
      if (typeof valObj === 'object' && valObj !== null) {
        displayVal = valObj.output !== undefined ? String(valObj.output) : JSON.stringify(valObj);
      } else {
        displayVal = String(valObj);
      }

      // 数值格式化：小数点后最多 2 位
      var numVal = parseFloat(displayVal);
      if (isFinite(numVal)) {
        displayVal = numVal.toFixed(2);
      }

      var valEl = nodeEl.querySelector('[data-node-value]');
      if (valEl) {
        // Fix 5: NaN/Inf/null → 红色 ERR 闪烁
        valEl.classList.remove('val-normal', 'val-warning', 'val-alarm');
        if (ERROR_VALUES[displayVal] || !isFinite(numVal)) {
          valEl.textContent = 'ERR';
          valEl.classList.add('val-alarm');
        } else {
          valEl.textContent = displayVal;
          valEl.classList.add('val-normal');
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
    var catOrder = ['signal', 'arithmetic', 'compare', 'dynamic', 'control', 'logic', 'timer', 'transfer'];
    catOrder.forEach(function (cat) {
      if (!groups[cat]) return;
      var meta = getCatMeta(cat);

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
      html += '<input type="text" value="' + esc(getCatMeta(blockDef.category).label || blockDef.category || '') + '" readonly />';
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

  /**
   * 获取节点对应的功能块 ID
   * @param {number} nodeId
   * @returns {string|null}
   */
  CanvasEngine.prototype.getNodeBlockId = function (nodeId) {
    return this._nodeBlockMap[nodeId] || null;
  };

  /** 获取类别元数据 */
  CanvasEngine.CATEGORIES = CATEGORY_META;


  /* ═══════════════════════════════════════════════════════════
     浮动调试小窗口 (Inspector)
     选中节点时显示详细信息，可拖拽移动
     ═══════════════════════════════════════════════════════════ */

  /**
   * 启用浮动调试小窗口（选中节点时自动显示）
   */
  CanvasEngine.prototype.enableInspector = function () {
    // 单击选中节点 → 打开侧边面板（替代旧的浮动 Inspector 小窗）
    var self = this;
    this.onNodeSelected(function (nodeId) {
      if (self._mode === 'config') {
        self._openSidePanel(nodeId);
      }
    });
    this.onNodeDeselected(function () {
      self._closeSidePanel();
    });
  };

  /** 显示调试小窗 */
  /* ═══════════════════════════════════════════════════════════
     参数编辑弹窗（仅用于 ref_in 选择器等特殊场景）
     ═══════════════════════════════════════════════════════════ */

  /**
   * 打开参数编辑弹窗
   * @param {number} nodeId
   */
  CanvasEngine.prototype._openParamModal = function (nodeId) {
    var data = this.getNodeData(nodeId);
    var blockId = this._nodeBlockMap[nodeId];
    var blockDef = this._blockDefs[blockId];
    if (!blockDef && !data) return;

    var modal = document.getElementById('ce-param-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'ce-param-modal';
      modal.className = 'ce-modal-overlay';
      document.body.appendChild(modal);
    }

    var form = buildParamFormHTML(blockId, blockDef, data, nodeId);
    var html = '<div class="ce-modal">';
    html += '<div class="ce-modal-header">' + form.header + '</div>';
    html += '<div class="ce-modal-body">' + form.body + '</div>';
    html += '<div class="ce-modal-footer">' + form.footer + '</div>';
    html += '</div>';

    modal.innerHTML = html;
    modal.classList.add('active');

    var self = this;

    modal.querySelectorAll('[data-close]').forEach(function (btn) {
      btn.addEventListener('click', function () { self._closeParamModal(); });
    });
    modal.addEventListener('click', function (e) {
      if (e.target === modal) self._closeParamModal();
    });

    modal.querySelector('[data-ok]').addEventListener('click', function () {
      modal.querySelectorAll('[data-key]').forEach(function (input) {
        var key = input.getAttribute('data-key');
        var val = input.type === 'number' ? parseNumSafe(input.value) : input.value;
        self.setNodeData(nodeId, key, val);
      });
      self.updateNodeDisplay(nodeId);
      self._closeParamModal();
      if (self._onNodeSelected) {
        self._onNodeSelected(nodeId, self.getNodeData(nodeId));
      }
    });

    modal.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
        modal.querySelector('[data-ok]').click();
      }
      if (e.key === 'Escape') {
        self._closeParamModal();
      }
    });

    var firstInput = modal.querySelector('.ce-modal-body input');
    if (firstInput) setTimeout(function () { firstInput.focus(); firstInput.select(); }, 50);
  };

  /** 关闭参数编辑弹窗 */
  CanvasEngine.prototype._closeParamModal = function () {
    var modal = document.getElementById('ce-param-modal');
    if (modal) modal.classList.remove('active');
  };


  /* ═══════════════════════════════════════════════════════════
     Fix 3: 侧边滑出面板 — 替代双击弹窗
     ═══════════════════════════════════════════════════════════ */

  /**
   * 打开右侧参数编辑面板
   * @param {number} nodeId
   */
  CanvasEngine.prototype._openSidePanel = function (nodeId) {
    var data = this.getNodeData(nodeId);
    var blockId = this._nodeBlockMap[nodeId];
    var blockDef = this._blockDefs[blockId];
    if (!blockDef && !data) return;

    // 先清理旧的键盘监听器（防止重复打开时泄漏）
    this._closeSidePanel();

    // 获取或创建面板容器
    var panel = document.getElementById('ce-side-panel');
    if (!panel) {
      panel = document.createElement('div');
      panel.id = 'ce-side-panel';
      panel.className = 'ce-side-panel';
      var canvasArea = this._container.closest('.canvas-area');
      if (canvasArea) {
        canvasArea.appendChild(panel);
      } else {
        this._container.parentElement.appendChild(panel);
      }
    }

    var form = buildParamFormHTML(blockId, blockDef, data, nodeId);
    var html = '';
    html += '<div class="ce-side-header">' + form.header + '</div>';
    html += '<div class="ce-side-body">' + form.body + '</div>';
    // 关联页面区域（由 CrossPageNav 异步填充）
    html += '<div class="ce-nav-related" id="ce-nav-related" style="padding:6px 10px;border-top:1px solid #334155;font-size:11px;color:#94a3b8"></div>';
    html += '<div class="ce-side-footer">' + form.footer + '</div>';

    panel.innerHTML = html;

    // 展开面板
    requestAnimationFrame(function () {
      panel.classList.add('open');
    });

    var self = this;

    panel.querySelectorAll('[data-close]').forEach(function (btn) {
      btn.addEventListener('click', function () { self._closeSidePanel(); });
    });

    panel.querySelector('[data-ok]').addEventListener('click', function () {
      panel.querySelectorAll('[data-key]').forEach(function (input) {
        var key = input.getAttribute('data-key');
        var val = input.type === 'number' ? parseNumSafe(input.value) : input.value;
        self.setNodeData(nodeId, key, val);
      });
      self.updateNodeDisplay(nodeId);
      self._closeSidePanel();
      if (self._onNodeSelected) {
        self._onNodeSelected(nodeId, self.getNodeData(nodeId));
      }
    });

    // Enter 确认 / ESC 关闭
    this._sidePanelKeyHandler = function (e) {
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
        panel.querySelector('[data-ok]').click();
      }
      if (e.key === 'Escape') {
        self._closeSidePanel();
      }
    };
    document.addEventListener('keydown', this._sidePanelKeyHandler);

    var firstInput = panel.querySelector('.ce-side-body input');
    if (firstInput) setTimeout(function () { firstInput.focus(); firstInput.select(); }, 250);

    // 异步填充关联页面
    if (typeof CrossPageNav !== 'undefined' && CrossPageNav.getRelatedInfo) {
      var relDiv = panel.querySelector('#ce-nav-related');
      if (relDiv) {
        CrossPageNav.getRelatedInfo(this, nodeId).then(function (result) {
          if (!result || result.empty) {
            relDiv.innerHTML = '<div style="color:#64748b">无跨页关联</div>';
            return;
          }
          var items = result.items || [];
          var h = '<div style="margin-bottom:3px;color:#cbd5e1;font-weight:bold">关联页面</div>';
          items.forEach(function (item) {
            h += '<a href="javascript:void(0)" class="ce-nav-link" data-page="' + item.pageId + '" data-layer="' + item.layer + '" data-node="' + (item.nodeId || '') + '" style="display:block;padding:2px 0;color:#60a5fa;cursor:pointer;text-decoration:none">';
            h += item.label;
            h += '</a>';
          });
          relDiv.innerHTML = h;
          relDiv.querySelectorAll('.ce-nav-link').forEach(function (a) {
            a.addEventListener('click', function () {
              CrossPageNav.navigateToNode(a.dataset.page, a.dataset.layer, a.dataset.node || null);
            });
          });
        });
      }
    }
  };

  /** 关闭侧边面板 */
  CanvasEngine.prototype._closeSidePanel = function () {
    var panel = document.getElementById('ce-side-panel');
    if (panel) panel.classList.remove('open');
    if (this._sidePanelKeyHandler) {
      document.removeEventListener('keydown', this._sidePanelKeyHandler);
      this._sidePanelKeyHandler = null;
    }
  };


  /* ═══════════════════════════════════════════════════════════
     Fix 4: 连线分类 — 开关量虚线
     ═══════════════════════════════════════════════════════════ */

  /**
   * 判断连线源节点是否为开关量/逻辑类型，添加 CSS class
   */
  CanvasEngine.prototype._classifyConnection = function (info) {
    try {
      var outputNodeId = info.output_id;
      var blockId = this._nodeBlockMap[outputNodeId];
      if (!blockId) return;
      // 使用 getPortType 判断输出端口类型
      var outClass = info.output_class || 'output_1';
      var portIdx = parseInt((outClass.match(/\d+$/) || ['1'])[0], 10) - 1;
      var pType = getPortType(blockId, 'output', portIdx);
      if (pType === 'bool') {
        var selector = '.connection.node_out_node-' + outputNodeId;
        var conns = this._container.querySelectorAll(selector);
        conns.forEach(function (conn) {
          conn.classList.add('conn-digital');
        });
      }
    } catch (e) {}
  };


  /* ═══════════════════════════════════════════════════════════
     引入点/引出点选择器
     ═══════════════════════════════════════════════════════════ */

  /**
   * 获取画布上所有引出点（ref_out）的 tag 列表
   */
  CanvasEngine.prototype.getAllRefOuts = function () {
    var results = [];
    var self = this;
    Object.keys(this._nodeBlockMap).forEach(function (id) {
      if (self._nodeBlockMap[id] === 'ref_out') {
        var data = self._nodeDataMap[id] || {};
        var tag = data.tag || '';
        if (tag) results.push({ nodeId: id, tag: tag });
      }
    });
    return results;
  };

  /**
   * 引入点双击 → 弹出引出点列表选择器
   */
  CanvasEngine.prototype._openRefPicker = function (nodeId) {
    var self = this;
    var localRefs = this.getAllRefOuts();
    var currentData = self._nodeDataMap[nodeId] || {};
    var currentTag = currentData.tag || '';
    var currentSourcePage = currentData.source_page_id || '';

    // 复用 ce-param-modal
    var existingModal = document.getElementById('ce-param-modal');
    if (existingModal) existingModal.remove();

    var overlay = document.createElement('div');
    overlay.className = 'ce-modal-overlay active';
    overlay.id = 'ce-param-modal';

    function buildModal(localRefs, remoteGroups) {
      var html = '<div class="ce-modal">';
      html += '<div class="ce-modal-header">';
      html += '<div class="ce-modal-header-text">选择引出点</div>';
      html += '<button class="ce-modal-close" data-close>&times;</button>';
      html += '</div>';
      html += '<div class="ce-modal-body" style="max-height:400px;overflow-y:auto;">';

      var hasAny = false;

      // 当前页
      if (localRefs.length > 0) {
        hasAny = true;
        html += '<div style="padding:4px 8px;font-size:11px;color:#a8a29e;font-weight:600;">当前页</div>';
        localRefs.forEach(function (r) {
          var isCurrent = (r.tag === currentTag && !currentSourcePage);
          html += '<div class="ce-ref-item' + (isCurrent ? ' ce-ref-active' : '') + '" data-tag="' + esc(r.tag) + '" data-source="">'
            + esc(r.tag) + '</div>';
        });
      }

      // 跨页分组
      if (remoteGroups) {
        Object.keys(remoteGroups).forEach(function (groupLabel) {
          var items = remoteGroups[groupLabel];
          if (items.length > 0) {
            hasAny = true;
            html += '<div style="padding:4px 8px;font-size:11px;color:#a8a29e;font-weight:600;margin-top:6px;">' + esc(groupLabel) + '</div>';
            items.forEach(function (r) {
              var isCurrent = (r.tag === currentTag && r.page_id === currentSourcePage);
              html += '<div class="ce-ref-item' + (isCurrent ? ' ce-ref-active' : '') + '" data-tag="' + esc(r.tag) + '" data-source="' + esc(r.page_id) + '" data-layer="' + esc(r.layer) + '">'
                + '<span style="flex:1;">' + esc(r.tag) + '</span>'
                + '<span class="ce-ref-jump" data-jump-page="' + esc(r.page_id) + '" data-jump-layer="' + esc(r.layer) + '" title="跳转到引出页">&rarr;</span>'
                + '</div>';
            });
          }
        });
      }

      if (!hasAny) {
        html += '<div style="padding:12px;text-align:center;color:#a8a29e;font-size:12px;">暂无引出点，请先添加引出点并设置变量名</div>';
      }

      // 底部：如果当前已绑定跨页引用，始终显示跳转按钮
      if (currentTag && currentSourcePage) {
        html += '<div style="border-top:1px solid #334155;margin-top:8px;padding-top:8px;">';
        html += '<div class="ce-ref-item" data-action="jump" style="color:#60a5fa;">'
          + '&rarr; 跳转到 ' + esc(currentTag) + ' 所在页 (' + esc(currentSourcePage) + ')</div>';
        html += '</div>';
      }

      html += '</div></div>';
      return html;
    }

    function applySelection(tag, sourcePageId, sourceLayer) {
      // 更新引入点的 tag 和来源页
      if (self._nodeDataMap[nodeId]) {
        self._nodeDataMap[nodeId].tag = tag;
        self._nodeDataMap[nodeId].source_page_id = sourcePageId;
        self._nodeDataMap[nodeId].source_layer = sourceLayer;
      }
      var dfNode = self._editor.getNodeFromId(nodeId);
      if (dfNode) {
        dfNode.data.tag = tag;
        dfNode.data.source_page_id = sourcePageId;
        dfNode.data.source_layer = sourceLayer;
        self._editor.updateNodeDataFromId(nodeId, dfNode.data);
      }
      var nodeEl = document.getElementById('node-' + nodeId);
      if (nodeEl) {
        var labelEl = nodeEl.querySelector('.sama-label');
        if (labelEl) labelEl.textContent = tag;
      }
      self._pushHistory();
    }

    function attachEvents() {
      // 跳转箭头按钮（不选择，仅跳转）
      overlay.querySelectorAll('.ce-ref-jump').forEach(function (btn) {
        btn.addEventListener('click', function (ev) {
          ev.stopPropagation();
          var layer = btn.dataset.jumpLayer || 'IB';
          var pageId = btn.dataset.jumpPage;
          window.location.href = '/canvas/' + layer + '/' + pageId;
        });
      });

      // 点击条目 → 选择引出点
      overlay.querySelectorAll('.ce-ref-item').forEach(function (el) {
        el.addEventListener('click', function () {
          if (el.dataset.action === 'jump') {
            var layer = (self._nodeDataMap[nodeId] || {}).source_layer || 'IB';
            window.location.href = '/canvas/' + layer + '/' + currentSourcePage;
            return;
          }
          applySelection(el.dataset.tag, el.dataset.source || '', el.dataset.layer || '');
          overlay.remove();
        });
      });

      // 关闭
      overlay.querySelector('[data-close]').addEventListener('click', function () {
        overlay.remove();
      });
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) overlay.remove();
      });
    }

    // 先显示本页的，同时异步加载跨页
    overlay.innerHTML = buildModal(localRefs, null);
    document.body.appendChild(overlay);
    attachEvents();

    // 异步加载跨页引出点
    fetch('/api/pages/refs').then(function (r) { return r.json(); }).then(function (data) {
      var refs = data.refs || [];
      var localTags = {};
      localRefs.forEach(function (r) { localTags[r.tag] = true; });
      var groups = {};
      refs.forEach(function (r) {
        if (localTags[r.tag]) return;
        var label = r.layer + ' - ' + r.page_name;
        if (!groups[label]) groups[label] = [];
        groups[label].push(r);
      });
      if (Object.keys(groups).length > 0) {
        overlay.innerHTML = buildModal(localRefs, groups);
        attachEvents();
      }
    }).catch(function () {});
  };


  /* ═══════════════════════════════════════════════════════════
     方向键移动节点
     ═══════════════════════════════════════════════════════════ */

  /**
   * 移动选中的节点（单选或多选）
   * @param {number} dx  X 方向偏移量（像素）
   * @param {number} dy  Y 方向偏移量（像素）
   */
  CanvasEngine.prototype.moveSelected = function (dx, dy) {
    if (this._mode !== 'config') return false;

    var self = this;
    var nodeIds = [];

    // 优先取多选
    if (this._selectedNodes && this._selectedNodes.length > 0) {
      nodeIds = this._selectedNodes.slice();
    } else {
      // 单选
      var sel = this._editor.node_selected;
      if (sel) {
        var nid = parseInt(sel.id.replace('node-', ''), 10);
        if (!isNaN(nid)) nodeIds.push(nid);
      }
    }
    if (nodeIds.length === 0) return false;

    nodeIds.forEach(function (nid) {
      try {
        var dfNode = self._editor.getNodeFromId(nid);
        if (!dfNode) return;
        var newX = dfNode.pos_x + dx;
        var newY = dfNode.pos_y + dy;
        dfNode.pos_x = newX;
        dfNode.pos_y = newY;
        var el = self._container.querySelector('#node-' + nid);
        if (el) {
          el.style.left = newX + 'px';
          el.style.top = newY + 'px';
        }
        self._editor.updateConnectionNodes('node-' + nid);
      } catch (e) {}
    });

    // 推送历史记录
    this._pushHistory();
    this._redoStack = [];
    if (this._onCanvasChanged) this._onCanvasChanged('nodeMoved');
    return true;
  };


  /* ═══════════════════════════════════════════════════════════
     复制 & 粘贴
     ═══════════════════════════════════════════════════════════ */

  var CLIPBOARD_LS_KEY = 'ce_clipboard';

  /**
   * 复制当前选中的节点（单选或多选）到剪贴板
   * 使用 localStorage 存储，支持跨页粘贴
   */
  CanvasEngine.prototype.copySelected = function () {
    var self = this;
    var nodeIds = [];

    // 优先取多选
    if (this._selectedNodes && this._selectedNodes.length > 0) {
      nodeIds = this._selectedNodes.slice();
    } else {
      // 单选
      var sel = this._editor.node_selected;
      if (sel) {
        var nid = parseInt(sel.id.replace('node-', ''), 10);
        if (!isNaN(nid)) nodeIds.push(nid);
      }
    }
    if (nodeIds.length === 0) return false;

    // 收集节点信息
    var clipboard = [];
    var minX = Infinity, minY = Infinity;
    nodeIds.forEach(function (nid) {
      var dfNode;
      try { dfNode = self._editor.getNodeFromId(nid); } catch (e) { return; }
      if (!dfNode) return;
      if (dfNode.pos_x < minX) minX = dfNode.pos_x;
      if (dfNode.pos_y < minY) minY = dfNode.pos_y;
      var blockId = self._nodeBlockMap[nid];
      var data = deepClone(self._nodeDataMap[nid] || dfNode.data || {});
      clipboard.push({
        blockId: blockId,
        data: data,
        pos_x: dfNode.pos_x,
        pos_y: dfNode.pos_y
      });
    });

    // 归一化坐标（相对左上角）
    clipboard.forEach(function (item) {
      item.rel_x = item.pos_x - minX;
      item.rel_y = item.pos_y - minY;
    });

    // 存入 localStorage（跨页可用）
    try {
      localStorage.setItem(CLIPBOARD_LS_KEY, JSON.stringify({ items: clipboard, pasteCount: 0 }));
    } catch (e) {}
    this._clipboard = clipboard;
    this._pasteCount = 0;
    return true;
  };

  /**
   * 粘贴剪贴板中的节点（优先从 localStorage 读取，支持跨页）
   */
  CanvasEngine.prototype.pasteClipboard = function () {
    // 从 localStorage 恢复（跨页场景）
    if (!this._clipboard || this._clipboard.length === 0) {
      try {
        var stored = JSON.parse(localStorage.getItem(CLIPBOARD_LS_KEY));
        if (stored && stored.items && stored.items.length > 0) {
          this._clipboard = stored.items;
          this._pasteCount = stored.pasteCount || 0;
        }
      } catch (e) {}
    }
    if (!this._clipboard || this._clipboard.length === 0) return false;
    if (this._mode !== 'config') return false;

    this._pasteCount = (this._pasteCount || 0) + 1;
    var offset = this._pasteCount * 40;
    var self = this;
    var newIds = [];

    this._clipboard.forEach(function (item) {
      var blockDef = self._blockDefs[item.blockId];
      if (!blockDef) return;

      // 克隆数据，清除内部映射字段
      var data = deepClone(item.data);
      delete data._blockId;
      delete data._blockName;
      // 保留 tag 等用户数据，但对 IO 信号块追加 _copy 后缀避免重名
      if (data.tag && (item.blockId === 'output' || item.blockId === 'ref_out')) {
        data.tag = data.tag + '_copy';
      }
      data.name = item.data._blockName || item.data.name || blockDef.name;

      var x = item.pos_x + offset;
      var y = item.pos_y + offset;
      var nid = self.addBlock(blockDef, x, y, data);
      if (nid) newIds.push(nid);
    });

    // 更新 localStorage 中的 pasteCount
    try {
      localStorage.setItem(CLIPBOARD_LS_KEY, JSON.stringify({ items: this._clipboard, pasteCount: this._pasteCount }));
    } catch (e) {}

    // 选中新粘贴的节点
    if (newIds.length > 0) {
      this.clearSelection();
      this._selectedNodes = newIds;
      newIds.forEach(function (nid) {
        var el = self._container.querySelector('#node-' + nid);
        if (el) el.classList.add('ce-selected');
      });
    }

    return true;
  };


  /* ═══════════════════════════════════════════════════════════
     框选 & 封装
     ═══════════════════════════════════════════════════════════ */

  /**
   * 启用框选功能（Shift+拖拽）
   */
  CanvasEngine.prototype.enableBoxSelect = function () {
    var self = this;
    this._selectedNodes = [];   // 多选节点 ID 列表
    this._boxSelecting = false;

    // 创建框选矩形
    var box = document.createElement('div');
    box.className = 'ce-select-box';
    this._container.appendChild(box);
    this._selectBox = box;

    var startX, startY;

    this._container.addEventListener('mousedown', function (e) {
      if (!e.shiftKey || self._mode !== 'config') return;
      // 不在节点上才开始框选
      if (e.target.closest('.drawflow-node')) return;
      e.preventDefault();
      self._boxSelecting = true;
      var rect = self._container.getBoundingClientRect();
      startX = e.clientX - rect.left;
      startY = e.clientY - rect.top;
      box.style.left = startX + 'px';
      box.style.top = startY + 'px';
      box.style.width = '0';
      box.style.height = '0';
      box.style.display = 'block';
    });

    document.addEventListener('mousemove', function (e) {
      if (!self._boxSelecting) return;
      var rect = self._container.getBoundingClientRect();
      var cx = e.clientX - rect.left;
      var cy = e.clientY - rect.top;
      box.style.left = Math.min(startX, cx) + 'px';
      box.style.top = Math.min(startY, cy) + 'px';
      box.style.width = Math.abs(cx - startX) + 'px';
      box.style.height = Math.abs(cy - startY) + 'px';
    });

    document.addEventListener('mouseup', function (e) {
      if (!self._boxSelecting) return;
      self._boxSelecting = false;
      box.style.display = 'none';

      // 计算框选范围（相对容器）
      var bx = parseInt(box.style.left);
      var by = parseInt(box.style.top);
      var bw = parseInt(box.style.width);
      var bh = parseInt(box.style.height);
      if (bw < 5 && bh < 5) { self.clearSelection(); return; }

      var selRect = { left: bx, top: by, right: bx + bw, bottom: by + bh };
      self._selectedNodes = [];

      // 遍历所有节点
      var nodes = self._container.querySelectorAll('.drawflow-node');
      nodes.forEach(function (nodeEl) {
        var nr = nodeEl.getBoundingClientRect();
        var cr = self._container.getBoundingClientRect();
        var nl = nr.left - cr.left;
        var nt = nr.top - cr.top;
        var nRect = { left: nl, top: nt, right: nl + nr.width, bottom: nt + nr.height };

        // 相交判定
        if (nRect.left < selRect.right && nRect.right > selRect.left &&
            nRect.top < selRect.bottom && nRect.bottom > selRect.top) {
          var nodeId = parseInt(nodeEl.id.replace('node-', ''), 10);
          if (!isNaN(nodeId)) {
            self._selectedNodes.push(nodeId);
            nodeEl.classList.add('ce-selected');
          }
        }
      });

      // 触发回调
      if (self._selectedNodes.length > 0 && self._onBoxSelected) {
        self._onBoxSelected(self._selectedNodes);
      }
    });

    // 点击空白区域清除多选
    this._container.addEventListener('click', function (e) {
      if (!e.target.closest('.drawflow-node') && !e.shiftKey) {
        self.clearSelection();
      }
    });
  };

  /** 清除多选 */
  CanvasEngine.prototype.clearSelection = function () {
    this._selectedNodes = [];
    this._container.querySelectorAll('.ce-selected').forEach(function (el) {
      el.classList.remove('ce-selected');
    });
    if (this._onBoxSelected) this._onBoxSelected([]);
  };

  /** 框选回调 */
  CanvasEngine.prototype.onBoxSelected = function (callback) {
    this._onBoxSelected = callback;
  };

  /** 获取当前多选节点 */
  CanvasEngine.prototype.getSelectedNodes = function () {
    return this._selectedNodes || [];
  };



  /* ═══════════════════════════════════════════════════════════
     全局注册
     ═══════════════════════════════════════════════════════════ */

  window.CanvasEngine = CanvasEngine;

})();
