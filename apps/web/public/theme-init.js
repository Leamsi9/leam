/* Local, non-sensitive appearance only. Executes before paint under script-src self. */
(() => {
  const key = 'leam.theme.v1';
  const valid = value => ['light', 'dark', 'system'].includes(value);
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = 'system';
  try { const saved = localStorage.getItem(key); if (valid(saved)) preference = saved; } catch { /* System is usable without storage. */ }
  function apply() {
    const resolved = preference === 'system' ? (media.matches ? 'dark' : 'light') : preference;
    const root = document.documentElement;
    root.dataset.theme = resolved;
    root.dataset.themePreference = preference;
    root.style.colorScheme = resolved;
    root.style.backgroundColor = resolved === 'dark' ? '#202527' : '#f6f3ec';
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#202527' : '#f6f3ec');
    window.dispatchEvent(new Event('leam:themechange'));
  }
  window.LeamAppearance = {
    getPreference: () => preference,
    setPreference(value) {
      if (!valid(value)) return false;
      preference = value;
      let persisted = true;
      try { localStorage.setItem(key, value); } catch { persisted = false; }
      apply();
      return persisted;
    },
  };
  media.addEventListener('change', () => { if (preference === 'system') apply(); });
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) {
      preference = valid(event.newValue) ? event.newValue : 'system';
      apply();
    }
  });
  apply();
})();
