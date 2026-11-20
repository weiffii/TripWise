from pathlib import Path
import os
import socket
from tempfile import TemporaryDirectory
from threading import Thread
from time import sleep
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
import uvicorn

from ai_ds.shanghai_assistant import ShanghaiAssistantService
from backend import accounts, shanghai
from backend.main import app


def authenticate(page, base, email, *, register):
    page.goto(base)
    if register:
        page.locator('#register-mode').click()
        page.locator('#confirm-password').fill('TripWise-Browser-2026')
    page.locator('#email').fill(email)
    page.locator('#password').fill('TripWise-Browser-2026')
    page.locator('#submit-button').click()
    page.wait_for_url('**/trips')


def scenario(browser, base, operations):
    owner = browser.new_page(viewport={'width': 1280, 'height': 900})
    owner.route('https://**/*', lambda route: route.abort())
    errors = []
    owner.on('pageerror', lambda error: errors.append(str(error)))
    authenticate(owner, base, 'owner@example.org', register=True)
    owner.goto(base + '/trip/new?city=shanghai')
    expect(owner.locator('#trip-fields')).to_be_enabled()
    owner.locator('#first-walk').fill('2026-12-05T09:00')
    owner.locator('#last-walk').fill('2026-12-06T19:00')
    owner.locator('#budget').fill('1500')
    owner.locator('#primary-interests input').first.check()
    owner.locator('#finish-questionnaire').click()
    owner.wait_for_url('**/trip/result?route=*', timeout=45_000)
    saved_url = owner.url
    route_id = saved_url.split('route=')[1]
    api_url = base + '/api/planned-trips/' + route_id
    expect(owner.locator('#route-stops')).not_to_be_empty()
    expect(owner.locator('#route-map circle').first).to_be_attached()
    expect(owner.locator('#route-map polyline').first).to_be_visible()
    owner.locator('.route-event-travel button').first.click()
    expect(owner.locator('#route-directions strong').first).to_contain_text('Переход')
    assert owner.locator('.route-chat').bounding_box()['x'] < owner.locator('#street-map').bounding_box()['x']
    initial = owner.request.get(api_url).json()
    removed = initial['plan']['days'][0]['visit_ids'][0]
    markers = owner.locator('#route-map [data-stop] title')
    initial_map = markers.all_text_contents()
    live = operations is None
    if not live:
        expect(owner.locator('#chat-status')).to_contain_text('Тестовый режим')
        owner.locator('#chat-text').fill('Убери музей')
        owner.locator('#edit-route button').click()
        expect(owner.locator('#chat-status')).to_have_text('В какой день?')
        assert owner.request.get(api_url).json() == initial
        operations.append({'op': 'remove_poi', 'poi_id': removed})
    for selector in (('#confirm-edit',) if live else ('#reject-edit', '#confirm-edit')):
        owner.locator('#chat-text').fill('Убери первое посещение первого дня. Остальные посещения и питание оставь.')
        owner.locator('#edit-route button').click()
        try:
            expect(owner.locator(selector)).to_be_visible(timeout=45_000)
        except AssertionError as error:
            raise AssertionError(owner.locator('#chat-status').inner_text()) from error
        assert owner.request.get(api_url).json() == initial
        assert markers.all_text_contents() == initial_map
        owner.locator(selector).click()
        expect(owner.locator('#chat-preview')).to_be_hidden(timeout=45_000)
    assert markers.all_text_contents() != initial_map
    assert owner.request.get(api_url).json()['version'] == 2
    owner.goto(base + '/trips')
    owner.locator('#logout-button').click()
    owner.wait_for_url(base + '/')
    assert owner.request.get(api_url).status == 401
    authenticate(owner, base, 'owner@example.org', register=False)
    owner.locator('#planned-trips a').click()
    expect(owner.locator('#route-stops')).not_to_be_empty()
    assert owner.url == saved_url
    assert removed not in owner.request.get(api_url).json()['plan']['selected_ids']
    assert not errors, errors
    other = browser.new_page()
    authenticate(other, base, 'other@example.org', register=True)
    assert other.request.get(api_url).status == 404
    assert other.request.get(api_url + '/assistant/status').status == 404
    assert other.request.get(base + '/api/planned-trips').json() == {'trips': []}
    other.goto(base + '/trip/new')
    expect(other.locator('#trip-fields')).to_be_enabled()
    expect(other.locator('#city option')).to_have_text(['Шанхай'])


def main():
    operations = None if os.environ.get('TRIPWISE_TEST_LIVE_LLM') == '1' else []

    def interpreter(payload):
        if payload['text'] == 'Убери музей':
            return {'action': 'needs_clarification', 'operations': [], 'clarification': 'В какой день?'}
        return {'action': 'propose', 'operations': operations, 'clarification': None}

    with TemporaryDirectory() as path, socket.socket() as listener, sync_playwright() as playwright:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        service = ShanghaiAssistantService() if operations is None else ShanghaiAssistantService(interpreter=interpreter, mode='mock')
        with patch.object(accounts, 'DB_PATH', Path(path) / 'browser.sqlite3'), patch.object(shanghai, 'assistant', return_value=service):
            server = uvicorn.Server(uvicorn.Config(app, log_level='warning'))
            thread = Thread(target=server.run, kwargs={'sockets': [listener]})
            thread.start()
            try:
                for _attempt in range(100):
                    if server.started:
                        break
                    sleep(.05)
                assert server.started
                browser = playwright.chromium.launch(channel=os.environ.get('TRIPWISE_BROWSER_CHANNEL', 'chrome'))
                try:
                    scenario(browser, f'http://127.0.0.1:{port}', operations)
                    print('Browser E2E passed:', 'live OpenRouter' if operations is None else 'mock LLM', flush=True)
                finally:
                    browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=10)


if __name__ == '__main__':
    main()
