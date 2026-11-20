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
    const plannedResponse = await fetch('/api/planned-trips', { cache: 'no-store', credentials: 'same-origin' });
    if (plannedResponse.ok) {
      const { trips } = await plannedResponse.json();
      const list = document.querySelector('#planned-trips');
      list.replaceChildren();
      for (const trip of trips) {
        const entry = document.createElement('li');
        const link = document.createElement('a');
        link.href = '/trip/shanghai?route=' + encodeURIComponent(trip.route_id);
        link.textContent = `Шанхай · ${trip.start_date} · версия ${trip.version}`;
        entry.append(link);
        list.append(entry);
      }
    }
    const draftResponse = await fetch('/api/trip-draft', { cache: 'no-store', credentials: 'same-origin' });
    if (!draftResponse.ok) throw new Error('draft');
    const { draft } = await draftResponse.json();
    document.querySelector('#draft-card').hidden = !draft;
    document.querySelector('#empty-trips').hidden = Boolean(draft);
    if (draft) {
      const preferences = draft.preferences;
      const formatDate = value => new Date(value).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' });
      const counts = preferences.traveler_counts || { adults: preferences.travelers, children: 0, seniors: 0 };
      const amount = Number(preferences.budget_amount) * (preferences.budget_basis === 'per_person' ? preferences.travelers : 1);
      const budget = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: preferences.budget_currency || 'RUB', maximumFractionDigits: 2 }).format(amount);
      document.querySelector('#draft-details').textContent = `${formatDate(preferences.first_walk_at)} — ${formatDate(preferences.last_walk_end_at)} · Взрослые: ${counts.adults}, дети: ${counts.children}, пожилые: ${counts.seniors} · Бюджет компании: ${budget}`;
    }
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
