// Только оформление: данные аккаунта и анкеты здесь не хранятся.
(() => {
  const key = 'tripwise-theme';
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  let preferred = null;
  try { preferred = localStorage.getItem(key); } catch { /* Хранилище может быть запрещено. */ }
  if (!['light', 'dark'].includes(preferred)) preferred = null;
  function apply(theme) {
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      const dark = theme === 'dark';
      button.setAttribute('aria-pressed', String(dark));
      button.setAttribute('aria-label', dark ? 'Включить светлую тему' : 'Включить тёмную тему');
      button.innerHTML = `<span aria-hidden="true">${dark ? '☀' : '☾'}</span><span class="theme-label">${dark ? 'Светлая' : 'Тёмная'}</span>`;
    });
  }
  apply(preferred || (system.matches ? 'dark' : 'light'));
  document.addEventListener('DOMContentLoaded', () => {
    apply(document.documentElement.dataset.theme);
    document.querySelectorAll('[data-theme-toggle]').forEach(button => button.addEventListener('click', () => {
      preferred = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(key, preferred); } catch { /* Тема работает и без сохранения. */ }
      apply(preferred);
    }));
  });
  system.addEventListener('change', event => { if (!preferred) apply(event.matches ? 'dark' : 'light'); });
  window.addEventListener('storage', event => {
    if (event.key !== key) return;
    preferred = ['light', 'dark'].includes(event.newValue) ? event.newValue : null;
    apply(preferred || (system.matches ? 'dark' : 'light'));
  });
})();
