"""Главные интересы без ограничения двумя категориями: настоящие HTTP-запросы."""

from test_accounts import LiveServerTests
from test_drafts import valid_preferences


class ManyInterestsTests(LiveServerTests):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.register()[0], 201)
        self.owner = self.request('/api/trip-draft')[1]['owner_context']

    def save(self, interests, revision=0, secondary=None):
        preferences = valid_preferences()
        preferences['primary_interests'] = interests
        preferences['secondary_interests'] = secondary or []
        return self.request('/api/trip-draft', {'revision': revision, 'preferences': preferences},
                            method='PUT', headers={'X-TripWise-Draft-Owner': self.owner})

    def test_three_primary_interests_can_be_saved_and_reopened(self):
        interests = ['history', 'museums', 'nature']
        self.assertEqual(self.save(interests)[0], 200)
        loaded = self.request('/api/trip-draft')[1]['draft']['preferences']
        self.assertEqual(loaded['primary_interests'], interests)

    def test_all_eight_categories_can_be_selected(self):
        interests = ['history', 'museums', 'nature', 'cafes', 'entertainment', 'shopping', 'viewpoints', 'unusual']
        self.assertEqual(self.save(interests)[0], 200)
        loaded = self.request('/api/trip-draft')[1]['draft']['preferences']
        self.assertEqual(loaded['primary_interests'], interests)

    def test_empty_duplicates_and_unknown_categories_are_rejected(self):
        for interests in [[], ['history', 'history'], ['history', 'unknown']]:
            with self.subTest(interests=interests):
                self.assertEqual(self.save(interests)[0], 422)
        self.assertIsNone(self.request('/api/trip-draft')[1]['draft'])

    def test_primary_secondary_overlap_still_rejected(self):
        self.assertEqual(self.save(['history', 'museums', 'nature'], secondary=['nature'])[0], 422)
        self.assertIsNone(self.request('/api/trip-draft')[1]['draft'])
