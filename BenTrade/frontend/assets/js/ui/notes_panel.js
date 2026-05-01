/**
 * BenTrade — Home Dashboard Notes Panel (v1)
 * ==========================================
 *
 * Renderer for the right-side drawer's "notes mode". Mounts a scrollable
 * newest-first note list with a pinned append footer into the drawer body.
 *
 * Public API (window.BenTradeNotesPanel):
 *   render(containerEl, { sectionId, displayName })
 *   destroy(containerEl)
 *
 * Security notes:
 *   - Note bodies are rendered with textContent, never innerHTML.
 *   - Timestamps are parsed/formatted via Date APIs only.
 */
window.BenTradeNotesPanel = (function () {
  'use strict';

  // Per-container state (keyed by the DOM node so multiple mounts don't
  // collide). We use a WeakMap so discarded containers get GC'd.
  var _states = new WeakMap();

  function _api() {
    return window.BenTradeApi && window.BenTradeApi.notes;
  }

  function _fmtTs(iso) {
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso || '');
      var y = d.getFullYear();
      var m = String(d.getMonth() + 1).padStart(2, '0');
      var day = String(d.getDate()).padStart(2, '0');
      var hh = String(d.getHours()).padStart(2, '0');
      var mm = String(d.getMinutes()).padStart(2, '0');
      return y + '-' + m + '-' + day + ' ' + hh + ':' + mm;
    } catch (_e) {
      return String(iso || '');
    }
  }

  function _buildSkeleton(container, displayName) {
    // Clear any previous content / listeners.
    container.innerHTML = '';

    var root = document.createElement('div');
    root.className = 'notes-panel';

    var header = document.createElement('header');
    header.className = 'notes-panel-header';
    var h3 = document.createElement('h3');
    h3.textContent = (displayName || 'Notes') + ' · Notes';
    header.appendChild(h3);

    var banner = document.createElement('div');
    banner.className = 'notes-panel-banner';
    banner.style.display = 'none';

    var list = document.createElement('div');
    list.className = 'notes-panel-list';
    list.setAttribute('role', 'log');
    list.setAttribute('aria-live', 'polite');

    var form = document.createElement('form');
    form.className = 'notes-panel-append';

    var textarea = document.createElement('textarea');
    textarea.placeholder = 'Add a note\u2026 (Ctrl+Enter to append)';
    textarea.maxLength = 8000;
    textarea.rows = 3;

    var submit = document.createElement('button');
    submit.type = 'submit';
    submit.disabled = true;
    submit.textContent = 'Append';

    form.appendChild(textarea);
    form.appendChild(submit);

    root.appendChild(header);
    root.appendChild(banner);
    root.appendChild(list);
    root.appendChild(form);
    container.appendChild(root);

    return {
      root: root,
      banner: banner,
      list: list,
      form: form,
      textarea: textarea,
      submit: submit,
    };
  }

  function _showError(banner, message) {
    banner.textContent = message || 'Something went wrong.';
    banner.style.display = '';
    if (banner._clearTimer) clearTimeout(banner._clearTimer);
    banner._clearTimer = setTimeout(function () {
      banner.style.display = 'none';
      banner.textContent = '';
    }, 4000);
  }

  function _renderEmpty(listEl) {
    listEl.innerHTML = '';
    var empty = document.createElement('div');
    empty.className = 'notes-panel-empty';
    empty.textContent = 'No notes yet. Add the first one below.';
    listEl.appendChild(empty);
  }

  function _makeRow(note, onDelete) {
    var row = document.createElement('div');
    row.className = 'notes-panel-row';
    row.dataset.noteId = note.note_id;

    var meta = document.createElement('div');
    meta.className = 'notes-panel-row-meta';

    var ts = document.createElement('span');
    ts.className = 'notes-panel-ts';
    ts.textContent = _fmtTs(note.created_at);

    var del = document.createElement('button');
    del.type = 'button';
    del.className = 'notes-panel-delete';
    del.setAttribute('aria-label', 'Delete note');
    del.textContent = '\u00D7';
    del.addEventListener('click', function () {
      if (!window.confirm('Delete this note?')) return;
      onDelete(note.note_id, row);
    });

    meta.appendChild(ts);
    meta.appendChild(del);

    var body = document.createElement('div');
    body.className = 'notes-panel-body';
    // SECURITY: textContent — never innerHTML for user input.
    body.textContent = String(note.body || '');

    row.appendChild(meta);
    row.appendChild(body);
    return row;
  }

  function _renderList(listEl, notes, onDelete) {
    listEl.innerHTML = '';
    if (!notes || notes.length === 0) {
      _renderEmpty(listEl);
      return;
    }
    notes.forEach(function (n) {
      listEl.appendChild(_makeRow(n, onDelete));
    });
  }

  function destroy(container) {
    if (!container) return;
    var state = _states.get(container);
    if (state && state.handlers) {
      // Remove any outstanding timers/listeners tracked on the state.
      if (state.banner && state.banner._clearTimer) {
        clearTimeout(state.banner._clearTimer);
      }
    }
    container.innerHTML = '';
    _states.delete(container);
  }

  function render(container, opts) {
    if (!container) return;
    var sectionId = String((opts && opts.sectionId) || '').trim();
    var displayName = (opts && opts.displayName) || sectionId;
    if (!sectionId) return;

    // Tear down any previous mount on this container.
    destroy(container);

    var dom = _buildSkeleton(container, displayName);
    var api = _api();
    var state = {
      sectionId: sectionId,
      displayName: displayName,
      banner: dom.banner,
      handlers: true,
    };
    _states.set(container, state);

    function onDelete(noteId, row) {
      if (!api) {
        _showError(dom.banner, 'Notes API unavailable.');
        return;
      }
      api.delete(sectionId, noteId)
        .then(function () {
          if (row && row.parentNode) row.parentNode.removeChild(row);
          if (!dom.list.querySelector('.notes-panel-row')) {
            _renderEmpty(dom.list);
          }
        })
        .catch(function (err) {
          _showError(dom.banner, 'Delete failed: ' + (err && err.message ? err.message : 'unknown error'));
        });
    }

    function doAppend() {
      if (!api) {
        _showError(dom.banner, 'Notes API unavailable.');
        return;
      }
      var body = dom.textarea.value;
      if (!body || !body.trim()) return;
      dom.submit.disabled = true;
      dom.textarea.disabled = true;
      api.append(sectionId, body)
        .then(function (resp) {
          var note = resp && resp.note;
          if (!note) throw new Error('malformed response');
          // Prepend — newest first.
          var empty = dom.list.querySelector('.notes-panel-empty');
          if (empty) dom.list.removeChild(empty);
          dom.list.insertBefore(_makeRow(note, onDelete), dom.list.firstChild);
          dom.textarea.value = '';
          dom.submit.disabled = true;
        })
        .catch(function (err) {
          _showError(dom.banner, 'Append failed: ' + (err && err.message ? err.message : 'unknown error'));
        })
        .finally(function () {
          dom.textarea.disabled = false;
          dom.textarea.focus();
        });
    }

    dom.textarea.addEventListener('input', function () {
      dom.submit.disabled = !dom.textarea.value || !dom.textarea.value.trim();
    });

    dom.textarea.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        doAppend();
      }
    });

    dom.form.addEventListener('submit', function (e) {
      e.preventDefault();
      doAppend();
    });

    // Initial fetch.
    if (!api) {
      _showError(dom.banner, 'Notes API unavailable.');
      _renderEmpty(dom.list);
      return;
    }
    // Loading placeholder
    dom.list.innerHTML = '';
    var loading = document.createElement('div');
    loading.className = 'notes-panel-empty';
    loading.textContent = 'Loading notes\u2026';
    dom.list.appendChild(loading);

    api.list(sectionId)
      .then(function (resp) {
        var notes = (resp && resp.notes) || [];
        _renderList(dom.list, notes, onDelete);
      })
      .catch(function (err) {
        _showError(dom.banner, 'Load failed: ' + (err && err.message ? err.message : 'unknown error'));
        _renderEmpty(dom.list);
      });
  }

  return {
    render: render,
    destroy: destroy,
  };
})();
