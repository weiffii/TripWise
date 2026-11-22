"""Проверки фиксированного времени и исходной версии в сравнении маршрутов."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import route_previews
from backend.accounts import COOKIE_NAME


class MoscowRoutePriorityTests(unittest.TestCase):
    def test_compare_preserves_pins_and_does_not_change_source(self):
        date = '2026-11-22'
        key = 'mandatory'
        pin = {date: {key: '12:00'}}
        request = SimpleNamespace(cookies={COOKIE_NAME: 'fixture-session'})
        user = {'id': 1}

        def plan(clock, cost):
            return {'days': [{'date': date, 'visits': [{'poi_id': key,
                'start_at': f'{date}T{clock}:00+03:00'}], 'legs': []}],
                'budget_summary': {'estimated_total': cost, 'budget_amount': 2000}}

        base = {'city': 'moscow', 'version': 1, 'preferences': {}, 'options': {},
                'plan': {**plan('12:00', 1500), 'visit_times': pin}}
        prefs = SimpleNamespace(city='moscow', model_dump=lambda **_: {'route_priority': 'cost'})
        data = route_previews.PreviewComparison(expected_version=1, route_priority='cost')
        with (patch.object(route_previews, '_previews', {}),
              patch.object(route_previews, 'engine_preferences', return_value=({'route_priority': 'cost'}, prefs)),
              patch('backend.shanghai.build_plan') as build):
            source = route_previews._put_preview(base, {}, user, request)
            args = (source['preview_id'], data, request, user, '1')
            build.return_value = (plan('11:30', 1200), {}, 'moscow-v7:test')
            with self.assertRaises(HTTPException) as error:
                route_previews.compare_preview(*args)
            self.assertEqual(error.exception.status_code, 422)
            self.assertEqual(len(route_previews._previews), 1)

            build.return_value = (plan('12:00', 1200), {}, 'moscow-v7:test')
            result = route_previews.compare_preview(*args)
            self.assertTrue(result['improved'])
            variant = route_previews.read_preview(result['preview_id'], request, user)
            self.assertEqual(variant['plan']['visit_times'], pin)
            self.assertEqual(route_previews.read_preview(source['preview_id'], request, user), source)
