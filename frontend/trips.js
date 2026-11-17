const accountEmail = document.querySelector('#account-email');
const logoutButton = document.querySelector('#logout-button');
const tripsMessage = document.querySelector('#trips-message');

function showError(text) {
  tripsMessage.textContent = text;
  tripsMessage.hidden = false;
}

// Данные пользователя берём с сервера, а не из адресной строки или localStorage.
async function loadAccount() {
  try {
    const response = await fetch('/api/auth/me', { cache: 'no-store', credentials: 'same-origin' });
    if (response.status === 401) {
      window.location.replace('/');
      return;
    }
    if (!response.ok) throw new Error('account');
    const result = await response.json();
    accountEmail.textContent = result.user.email;
  } catch {
    accountEmail.textContent = 'Аккаунт не загружен';
    showError('Не удалось связаться с сервером. Проверьте его запуск и обновите страницу.');
  }
}

logoutButton.addEventListener('click', async () => {
  logoutButton.disabled = true;
  try {
    const response = await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
    if (!response.ok) throw new Error('logout');
    window.location.replace('/');
  } catch {
    showError('Не удалось выйти. Проверьте соединение и повторите попытку.');
    logoutButton.disabled = false;
  }
});

// Повторная проверка при возврате кнопкой «Назад», включая кэш страниц браузера.
window.addEventListener('pageshow', loadAccount);
