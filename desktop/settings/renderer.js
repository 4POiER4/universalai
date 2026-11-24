(async () => {
  const input = document.getElementById('url');
  const errorEl = document.getElementById('error');
  const saveBtn = document.getElementById('save');

  try {
    const current = await window.settingsAPI.get();
    if (current?.serverUrl) input.value = current.serverUrl;
  } catch (_) { /* first run: leave the placeholder */ }

  async function save() {
    errorEl.textContent = '';
    saveBtn.disabled = true;
    try {
      await window.settingsAPI.save({ serverUrl: input.value });
    } catch (error) {
      errorEl.textContent = error.message || String(error);
      saveBtn.disabled = false;
    }
  }

  saveBtn.addEventListener('click', save);
  input.addEventListener('keydown', event => { if (event.key === 'Enter') save(); });
})();
