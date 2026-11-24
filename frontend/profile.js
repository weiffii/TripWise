(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const requested = new URLSearchParams(location.search).get('view');
  const view = ['profile','atlas'].includes(requested) ? requested : 'trips';
  document.body.dataset.accountView = view;
  byId('account-profile').hidden = view !== 'profile';
  byId('profile-trips').hidden = view !== 'trips';
  byId('profile-atlas').hidden = view !== 'atlas';
  document.title = `${{profile:'Мой профиль',atlas:'Атлас путешествий',trips:'Мои поездки'}[view]} — TripWise`;
  for (const [key, id] of [['trips','profile-trips-tab'],['atlas','profile-atlas-tab'],['profile','profile-settings-tab']]) {
    if (view === key) byId(id).setAttribute('aria-current','page');
  }
  const input = byId('profile-display-name'), form = byId('profile-form'), save = byId('profile-save'), message = byId('profile-message');
  const dialog = byId('profile-name-dialog'), opener = byId('profile-edit-name'), cancel = byId('profile-cancel');
  opener.addEventListener('click', () => { dialog.showModal(); input.select(); });
  cancel.addEventListener('click', () => { dialog.close(); });
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => {
    if (!locked) { input.value = original; save.disabled = true; message.hidden = true; }
    opener.focus();
  });
  let original = '', loaded = false, busy = false, ownerContext = null, locked = false;
  function stopSaving(text) { locked=true; save.disabled=true; message.hidden=false; message.textContent=text; }
  function display(user) {
    const name = user.display_name || '';
    byId('profile-name').textContent = name || 'Ваше пространство';
    byId('profile-avatar').textContent = name ? [...name][0].toLocaleUpperCase('ru') : '✦';
    byId('account-email').textContent = user.email;
  }
  window.TripWiseProfile = {
    loadUser(user, context) {
      if (typeof context !== 'string' || !context) { stopSaving('Не удалось проверить аккаунт. Обновите страницу перед сохранением.'); return; }
      if (ownerContext !== null && ownerContext !== context) { stopSaving('Аккаунт изменился. Сохранение остановлено. Обновите страницу, чтобы открыть профиль текущего аккаунта.'); return; }
      if (ownerContext === null) ownerContext=context;
      display(user);
      // Повторная загрузка pageshow не стирает ещё не сохранённый ввод.
      if (!loaded || input.value === original) { original = user.display_name || ''; input.value = original; }
      loaded = true; opener.disabled = false; input.disabled = false; save.disabled = locked || busy || input.value === original;
    },
  };
  input.addEventListener('input',()=>{if(!locked)message.hidden=true; save.disabled=locked||busy||!loaded||input.value===original;});
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    if (locked || busy || !loaded || !ownerContext || !form.reportValidity()) return;
    busy=true; cancel.disabled=true; save.disabled=true; input.disabled=true; message.hidden=false; message.textContent='Сохраняем имя…';
    const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),10000);
    try {
      const response=await fetch('/api/auth/profile',{method:'PUT',credentials:'same-origin',headers:{'Content-Type':'application/json','X-TripWise-Draft-Owner':ownerContext},body:JSON.stringify({display_name:input.value}),signal:controller.signal});
      const result=await response.json();
      if (response.status===409 || response.status===428) { stopSaving(response.status===409?'Аккаунт изменился. Сохранение остановлено. Ваш ввод остался в этой вкладке. Обновите страницу, чтобы открыть профиль текущего аккаунта.':'Обновите страницу перед сохранением профиля. Ваш ввод пока остаётся в этой вкладке.'); return; }
      if (response.status===401) { stopSaving('Сеанс закончился. Войдите снова и откройте профиль заново. Ваш ввод остался в этой вкладке.'); return; }
      if (!response.ok || !result.user) throw Error(response.status===401?'Сеанс закончился. Войдите снова, чтобы сохранить изменения.':typeof result.detail==='string'?result.detail:'Не удалось сохранить имя. Проверьте ввод и повторите.');
      original=result.user.display_name||'';input.value=original;display(result.user);dialog.close();
    } catch(error) { message.textContent=error.name==='AbortError'?'Сервер не ответил вовремя. Обновите страницу, чтобы проверить сохранение.':error instanceof TypeError?'Не удалось связаться с сервером. Проверьте соединение и повторите.':error.message||'Не удалось сохранить изменения.'; }
    finally {clearTimeout(timer);busy=false;cancel.disabled=false;input.disabled=false;save.disabled=locked||input.value===original;}
  });
})();
