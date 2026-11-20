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
    errors = []
    owner.on('pageerror', lambda error: errors.append(str(error)))
    authenticate(owner, base, 'owner@example.org', register=True)
    owner.locator('a[href="/trip/shanghai"]').click()
    owner.locator('#start-date').fill('2026-12-05')
    owner.locator('#days').select_option('2')
    owner.locator('#budget').fill('1500')
    owner.locator('#build-button').click()
    owner.wait_for_url('**/trip/shanghai?route=*', timeout=45_000)
    saved_url = owner.url
    route_id = saved_url.split('route=')[1]
    api_url = base + '/api/planned-trips/' + route_id
    expect(owner.locator('#timeline')).not_to_be_empty()
    expect(owner.locator('#route-map circle').first).to_be_attached()
    initial = owner.request.get(api_url).json()
    removed = initial['plan']['days'][0]['visit_ids'][0]
    operations.append({'op': 'remove_poi', 'poi_id': removed})
    markers = owner.locator('#route-map [data-stop] title')
    initial_map = markers.all_text_contents()
    for selector in ('[data-chat-reject]', '[data-chat-confirm]'):
        owner.locator('#chat-input').fill('Убери первое посещение')
        owner.locator('[data-chat-send]').click()
        expect(owner.locator(selector)).to_be_visible(timeout=45_000)
        assert owner.request.get(api_url).json() == initial
        assert markers.all_text_contents() == initial_map
        owner.locator(selector).click()
        expect(owner.locator('[data-chat-preview]')).to_be_hidden(timeout=45_000)
    assert markers.all_text_contents() != initial_map
    assert owner.request.get(api_url).json()['version'] == 2
    owner.goto(base + '/trips')
    owner.locator('#logout-button').click()
    owner.wait_for_url(base + '/')
    assert owner.request.get(api_url).status == 401
    authenticate(owner, base, 'owner@example.org', register=False)
    owner.locator('#planned-trips a').click()
    expect(owner.locator('#timeline')).not_to_be_empty()
    assert owner.url == saved_url
    assert removed not in owner.request.get(api_url).json()['plan']['selected_ids']
    assert not errors, errors
    other = browser.new_page()
    authenticate(other, base, 'other@example.org', register=True)
    assert other.request.get(api_url).status == 404
    assert other.request.get(api_url + '/assistant/status').status == 404
    assert other.request.get(base + '/api/planned-trips').json() == {'trips': []}


def main():
    operations = []

    def interpreter(_payload):
        return {'action': 'propose', 'operations': operations, 'clarification': None}

    with TemporaryDirectory() as path, socket.socket() as listener, sync_playwright() as playwright:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        service = ShanghaiAssistantService(interpreter=interpreter, mode='mock')
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
                finally:
                    browser.close()
            finally:
                server.should_exit = True
                thread.join(timeout=10)


if __name__ == '__main__':
    main()
