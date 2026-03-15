/**
 * cross-page-nav.js — 跨页导航模块
 * 提供 Ctrl+K 搜索面板、导航历史、跨页引用查询
 */
(function () {
  'use strict';

  /* ========== 状态 ========== */
  let _engine = null;
  let _debounceTimer = null;
  const DEBOUNCE_MS = 200;
  const RECENT_KEY = 'ce_recent_pages';
  const RECENT_MAX = 5;
  const HISTORY_KEY = 'ce_nav_history';
  const navHistory = { stack: [], index: -1, MAX: 50 };

  /* ========== 工具函数 ========== */

  function loadRecent() {
    try {
      return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    } catch { return []; }
  }

  function saveRecent(list) {
    localStorage.setItem(RECENT_KEY, JSON.stringify(list));
  }

  function recordCurrentPage() {
    if (typeof PAGE_ID === 'undefined' || typeof PAGE_LAYER === 'undefined') return;
    const pageName = document.title || PAGE_ID;
    const recent = loadRecent();
    // 去重，保留最新
    const filtered = recent.filter(r => !(r.pageId === PAGE_ID && r.layer === PAGE_LAYER));
    filtered.unshift({ pageId: PAGE_ID, pageName: pageName, layer: PAGE_LAYER, timestamp: Date.now() });
    saveRecent(filtered.slice(0, RECENT_MAX));
  }

  function loadHistory() {
    try {
      const data = JSON.parse(sessionStorage.getItem(HISTORY_KEY));
      if (data && Array.isArray(data.stack)) {
        navHistory.stack = data.stack;
        navHistory.index = data.index;
      }
    } catch { /* ignore */ }
  }

  function saveHistory() {
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify({
      stack: navHistory.stack,
      index: navHistory.index
    }));
  }

  function pushHistory(pageId, layer, nodeId) {
    // 截断 forward 部分
    if (navHistory.index < navHistory.stack.length - 1) {
      navHistory.stack = navHistory.stack.slice(0, navHistory.index + 1);
    }
    navHistory.stack.push({ pageId, layer, nodeId, timestamp: Date.now() });
    if (navHistory.stack.length > navHistory.MAX) {
      navHistory.stack.shift();
    }
    navHistory.index = navHistory.stack.length - 1;
    saveHistory();
  }

  function goBack() {
    if (navHistory.index <= 0) return;
    navHistory.index--;
    saveHistory();
    const entry = navHistory.stack[navHistory.index];
    navigateToNode(entry.pageId, entry.layer, entry.nodeId, true);
  }

  function goForward() {
    if (navHistory.index >= navHistory.stack.length - 1) return;
    navHistory.index++;
    saveHistory();
    const entry = navHistory.stack[navHistory.index];
    navigateToNode(entry.pageId, entry.layer, entry.nodeId, true);
  }

  /* ========== 高亮闪烁 ========== */

  function flashNode(nodeId) {
    const editor = _engine && _engine.getEditor();
    if (!editor) return;
    const nodeEl = document.querySelector('#node-' + nodeId);
    if (!nodeEl) return;
    nodeEl.style.transition = 'box-shadow 0.3s';
    nodeEl.style.boxShadow = '0 0 12px 4px #facc15';
    setTimeout(() => {
      nodeEl.style.boxShadow = '';
    }, 3000);
  }

  function centerAndFlash(nodeId) {
    if (_engine && typeof _engine.centerOnNode === 'function') {
      _engine.centerOnNode(nodeId);
    }
    flashNode(nodeId);
  }

  /* ========== 搜索面板 DOM ========== */

  let overlayEl = null;
  let inputEl = null;
  let resultsEl = null;
  let activeIdx = -1;
  let currentResults = [];

  function createOverlay() {
    if (overlayEl) return;

    overlayEl = document.createElement('div');
    overlayEl.id = 'ce-search-overlay';
    Object.assign(overlayEl.style, {
      display: 'none',
      position: 'fixed',
      inset: '0',
      background: 'rgba(0,0,0,0.55)',
      zIndex: '2000',
      justifyContent: 'center',
      alignItems: 'flex-start',
      paddingTop: '15vh'
    });

    const panel = document.createElement('div');
    Object.assign(panel.style, {
      width: '500px',
      maxWidth: '90vw',
      background: '#1e293b',
      borderRadius: '6px',
      boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
      overflow: 'hidden',
      fontFamily: 'monospace'
    });

    inputEl = document.createElement('input');
    inputEl.type = 'text';
    inputEl.placeholder = 'Search blocks, tags, pages...';
    Object.assign(inputEl.style, {
      width: '100%',
      boxSizing: 'border-box',
      padding: '10px 14px',
      background: '#0f172a',
      border: 'none',
      borderBottom: '1px solid #334155',
      color: '#f1f5f9',
      fontSize: '14px',
      outline: 'none'
    });

    resultsEl = document.createElement('div');
    Object.assign(resultsEl.style, {
      maxHeight: '360px',
      overflowY: 'auto'
    });

    panel.appendChild(inputEl);
    panel.appendChild(resultsEl);
    overlayEl.appendChild(panel);
    document.body.appendChild(overlayEl);

    // 事件
    overlayEl.addEventListener('mousedown', (e) => {
      if (e.target === overlayEl) closeSearch();
    });

    inputEl.addEventListener('input', () => {
      clearTimeout(_debounceTimer);
      _debounceTimer = setTimeout(() => doSearch(inputEl.value.trim()), DEBOUNCE_MS);
    });

    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { closeSearch(); e.preventDefault(); return; }
      if (e.key === 'ArrowDown') { e.preventDefault(); moveSelection(1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); moveSelection(-1); return; }
      if (e.key === 'Enter') { e.preventDefault(); selectCurrent(); return; }
    });
  }

  function openSearch() {
    createOverlay();
    overlayEl.style.display = 'flex';
    inputEl.value = '';
    activeIdx = -1;
    currentResults = [];
    // 初始显示最近页面
    showRecent();
    setTimeout(() => inputEl.focus(), 50);
  }

  function closeSearch() {
    if (overlayEl) overlayEl.style.display = 'none';
  }

  function showRecent() {
    const recent = loadRecent();
    if (!recent.length) {
      resultsEl.innerHTML = '<div style="padding:12px 14px;color:#94a3b8;font-size:13px;">No recent pages</div>';
      currentResults = [];
      return;
    }
    currentResults = recent.map(r => ({
      type: 'page',
      page_id: r.pageId,
      page_name: r.pageName,
      layer: r.layer,
      node_id: null,
      tag: '',
      label: r.pageName
    }));
    renderResults(currentResults, 'Recent');
  }

  async function doSearch(query) {
    if (!query) { showRecent(); return; }
    try {
      const resp = await (typeof api === 'function'
        ? api('/api/search?q=' + encodeURIComponent(query))
        : fetch('/api/search?q=' + encodeURIComponent(query)).then(r => r.json()));
      currentResults = (resp && resp.results) || [];
      renderResults(currentResults, 'Results');
    } catch (err) {
      resultsEl.innerHTML = '<div style="padding:12px 14px;color:#f87171;font-size:13px;">Search error</div>';
      currentResults = [];
    }
  }

  function renderResults(items, heading) {
    activeIdx = -1;
    if (!items.length) {
      resultsEl.innerHTML = '<div style="padding:12px 14px;color:#94a3b8;font-size:13px;">No results</div>';
      return;
    }
    let html = '';
    if (heading) {
      html += '<div style="padding:6px 14px;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">' + heading + '</div>';
    }
    items.forEach((item, i) => {
      const tag = item.tag ? (' <span style="color:#94a3b8;">[' + escHtml(item.tag) + ']</span>') : '';
      const loc = escHtml((item.layer || '').toUpperCase() + ' / ' + (item.page_name || item.page_id));
      const label = escHtml(item.label || item.tag || item.page_name || item.page_id);
      html += '<div class="ce-sr" data-idx="' + i + '" style="padding:7px 14px;cursor:pointer;color:#e2e8f0;font-size:13px;display:flex;justify-content:space-between;align-items:center;">'
        + '<span>' + label + tag + '</span>'
        + '<span style="color:#64748b;font-size:11px;">' + loc + '</span>'
        + '</div>';
    });
    resultsEl.innerHTML = html;

    resultsEl.querySelectorAll('.ce-sr').forEach(el => {
      el.addEventListener('mouseenter', () => {
        setActive(parseInt(el.dataset.idx));
      });
      el.addEventListener('click', () => {
        setActive(parseInt(el.dataset.idx));
        selectCurrent();
      });
    });
  }

  function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  }

  function setActive(idx) {
    activeIdx = idx;
    resultsEl.querySelectorAll('.ce-sr').forEach((el, i) => {
      el.style.background = i === idx ? '#334155' : 'transparent';
    });
  }

  function moveSelection(dir) {
    if (!currentResults.length) return;
    let next = activeIdx + dir;
    if (next < 0) next = currentResults.length - 1;
    if (next >= currentResults.length) next = 0;
    setActive(next);
    // scroll into view
    const el = resultsEl.querySelector('[data-idx="' + next + '"]');
    if (el) el.scrollIntoView({ block: 'nearest' });
  }

  function selectCurrent() {
    if (activeIdx < 0 || activeIdx >= currentResults.length) return;
    const item = currentResults[activeIdx];
    closeSearch();
    navigateToNode(item.page_id, item.layer, item.node_id);
  }

  /* ========== 导航核心 ========== */

  function navigateToNode(pageId, layer, nodeId, skipHistory) {
    if (!skipHistory) {
      pushHistory(pageId, layer, nodeId);
    }

    const samePage = (typeof PAGE_ID !== 'undefined') && PAGE_ID === pageId && PAGE_LAYER === layer;

    if (samePage) {
      if (nodeId) centerAndFlash(nodeId);
      return;
    }

    // 跨页导航 — 检查脏状态
    if (_engine && typeof _engine.isDirty === 'function' && _engine.isDirty()) {
      if (!confirm('Current page has unsaved changes. Leave without saving?')) return;
    }

    let url = '/canvas/' + encodeURIComponent(layer) + '/' + encodeURIComponent(pageId);
    if (nodeId) url += '?highlight=' + encodeURIComponent(nodeId);
    window.location = url;
  }

  /* ========== ?highlight= 处理 ========== */

  function handleHighlightParam() {
    const params = new URLSearchParams(window.location.search);
    const nodeId = params.get('highlight');
    if (!nodeId) return;
    // 清除 URL 参数（不刷新）
    const cleanUrl = window.location.pathname;
    window.history.replaceState(null, '', cleanUrl);
    // 延迟等待画布加载完毕
    setTimeout(() => centerAndFlash(nodeId), 500);
  }

  /* ========== 跨页引用查询 ========== */

  async function getRelatedInfo(engine, nodeId) {
    const empty = { items: [], empty: true };
    if (!engine || !nodeId) return empty;

    const nodeData = engine._nodeDataMap && engine._nodeDataMap[String(nodeId)];
    const blockType = engine._nodeBlockMap && engine._nodeBlockMap[String(nodeId)];
    if (!nodeData) return empty;

    const items = [];

    // ref_in: 显示来源页面
    if (blockType === 'ref_in' && nodeData.source_page_id) {
      items.push({
        label: 'Source: ' + (nodeData.tag || nodeData.source_page_id),
        pageId: nodeData.source_page_id,
        layer: nodeData.source_layer || PAGE_LAYER,
        nodeId: null
      });
    }

    // ref_out: 搜索引用此 tag 的 ref_in
    if (blockType === 'ref_out' && nodeData.tag) {
      try {
        const resp = await (typeof api === 'function'
          ? api('/api/search?q=' + encodeURIComponent(nodeData.tag))
          : fetch('/api/search?q=' + encodeURIComponent(nodeData.tag)).then(r => r.json()));
        const refs = (resp && resp.results) || [];
        refs.forEach(r => {
          // 排除自身
          if (r.page_id === PAGE_ID && r.node_id === String(nodeId)) return;
          items.push({
            label: (r.label || r.tag) + ' (' + (r.layer || '').toUpperCase() + '/' + (r.page_name || r.page_id) + ')',
            pageId: r.page_id,
            layer: r.layer,
            nodeId: r.node_id
          });
        });
      } catch { /* ignore */ }
    }

    // 其他块：如果有 tag，搜索关联
    if (blockType !== 'ref_in' && blockType !== 'ref_out' && nodeData.tag) {
      try {
        const resp = await (typeof api === 'function'
          ? api('/api/search?q=' + encodeURIComponent(nodeData.tag))
          : fetch('/api/search?q=' + encodeURIComponent(nodeData.tag)).then(r => r.json()));
        const refs = (resp && resp.results) || [];
        refs.forEach(r => {
          if (r.page_id === PAGE_ID && r.node_id === String(nodeId)) return;
          items.push({
            label: (r.label || r.tag) + ' (' + (r.layer || '').toUpperCase() + '/' + (r.page_name || r.page_id) + ')',
            pageId: r.page_id,
            layer: r.layer,
            nodeId: r.node_id
          });
        });
      } catch { /* ignore */ }
    }

    return { items, empty: items.length === 0 };
  }

  /* ========== 初始化 ========== */

  function init(engine) {
    _engine = engine;
    loadHistory();
    recordCurrentPage();
    handleHighlightParam();

    // Ctrl+K
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        openSearch();
      }
      // Alt+Left / Alt+Right
      if (e.altKey && e.key === 'ArrowLeft') {
        e.preventDefault();
        goBack();
      }
      if (e.altKey && e.key === 'ArrowRight') {
        e.preventDefault();
        goForward();
      }
    });
  }

  /* ========== 导出 ========== */

  window.CrossPageNav = {
    init: init,
    navigateToNode: navigateToNode,
    openSearch: openSearch,
    getRelatedInfo: getRelatedInfo
  };

})();
