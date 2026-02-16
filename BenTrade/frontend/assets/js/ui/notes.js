window.BenTradeNotes = (function(){
  const STORAGE_KEY = 'bentrade_notes_v1';

  function nowIso(){
    return new Date().toISOString();
  }

  function normalizeContextKey(value){
    const text = String(value || '').trim();
    return text;
  }

  function safeText(value){
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function readStore(){
    try{
      const raw = localStorage.getItem(STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : {};
      return (parsed && typeof parsed === 'object') ? parsed : {};
    }catch(_err){
      return {};
    }
  }

  function writeStore(store){
    try{
      localStorage.setItem(STORAGE_KEY, JSON.stringify(store || {}));
    }catch(_err){
    }
  }

  function readNotesForKey(contextKey){
    const key = normalizeContextKey(contextKey);
    if(!key) return [];
    const store = readStore();
    const list = store[key];
    if(!Array.isArray(list)) return [];
    return list
      .filter((item) => item && typeof item === 'object')
      .map((item) => ({
        ts: String(item.ts || nowIso()),
        text: String(item.text || '').trim(),
      }))
      .filter((item) => item.text !== '')
      .sort((a, b) => String(b.ts).localeCompare(String(a.ts)));
  }

  function writeNotesForKey(contextKey, notes){
    const key = normalizeContextKey(contextKey);
    if(!key) return;
    const store = readStore();
    store[key] = Array.isArray(notes) ? notes : [];
    writeStore(store);
  }

  async function emitLifecycleNote(contextKey, noteText){
    const key = normalizeContextKey(contextKey);
    if(!key || !noteText) return;

    const body = {
      event: 'NOTE',
      source: 'notes_component',
      payload: {
        text: String(noteText),
        context_key: key,
      },
    };

    if(key.startsWith('notes:trade:')){
      body.trade_key = key.substring('notes:trade:'.length);
    }
    if(key.startsWith('notes:idea:')){
      body.payload.idea_key = key.substring('notes:idea:'.length);
    }

    try{
      await fetch('/api/lifecycle/event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    }catch(_err){
    }
  }

  function buildTemplate(){
    return `
      <div class="bt-notes">
        <textarea class="bt-notes-input" rows="3" placeholder="Add a note..."></textarea>
        <div class="bt-notes-actions">
          <button class="btn bt-notes-add" type="button">Add Note</button>
        </div>
        <div class="bt-notes-list"></div>
      </div>
    `;
  }

  function renderList(listEl, notes){
    const rows = Array.isArray(notes) ? notes : [];
    if(!rows.length){
      listEl.innerHTML = '<div class="bt-notes-empty">No notes yet.</div>';
      return;
    }

    listEl.innerHTML = rows.map((row, idx) => {
      const ts = row?.ts ? new Date(row.ts).toLocaleString() : 'N/A';
      return `
        <div class="bt-notes-item" data-note-idx="${idx}">
          <div class="bt-notes-meta">${safeText(ts)}</div>
          <div class="bt-notes-text">${safeText(row.text)}</div>
          <button class="bt-notes-delete" type="button" data-note-delete="${idx}" aria-label="Delete note">✕</button>
        </div>
      `;
    }).join('');
  }

  function attachNotes(root, contextKeyProvider){
    if(!root) return null;

    root.innerHTML = buildTemplate();

    const input = root.querySelector('.bt-notes-input');
    const addBtn = root.querySelector('.bt-notes-add');
    const listEl = root.querySelector('.bt-notes-list');

    if(!input || !addBtn || !listEl) return null;

    const getContextKey = () => {
      try{
        const candidate = (typeof contextKeyProvider === 'function') ? contextKeyProvider() : contextKeyProvider;
        return normalizeContextKey(candidate);
      }catch(_err){
        return '';
      }
    };

    const reload = () => {
      const key = getContextKey();
      const notes = readNotesForKey(key);
      renderList(listEl, notes);
    };

    const addNote = async () => {
      const key = getContextKey();
      const text = String(input.value || '').trim();
      if(!key || !text) return;

      const notes = readNotesForKey(key);
      notes.unshift({ ts: nowIso(), text });
      writeNotesForKey(key, notes);
      input.value = '';
      renderList(listEl, notes);
      emitLifecycleNote(key, text).catch(() => {});
    };

    const deleteNoteAt = (idx) => {
      const key = getContextKey();
      if(!key) return;
      const notes = readNotesForKey(key);
      if(idx < 0 || idx >= notes.length) return;
      notes.splice(idx, 1);
      writeNotesForKey(key, notes);
      renderList(listEl, notes);
    };

    addBtn.addEventListener('click', () => addNote());
    input.addEventListener('keydown', (event) => {
      if(event.key === 'Enter' && (event.ctrlKey || event.metaKey)){
        event.preventDefault();
        addNote();
      }
    });

    listEl.addEventListener('click', (event) => {
      const button = event.target.closest('[data-note-delete]');
      if(!button) return;
      const idx = Number(button.getAttribute('data-note-delete'));
      if(!Number.isFinite(idx)) return;
      deleteNoteAt(idx);
    });

    reload();

    return {
      reload,
      add: (text) => {
        input.value = String(text || '');
        return addNote();
      },
      destroy: () => {
        root.innerHTML = '';
      },
    };
  }

  return {
    storageKey: STORAGE_KEY,
    attachNotes,
    readNotesForKey,
    writeNotesForKey,
  };
})();
