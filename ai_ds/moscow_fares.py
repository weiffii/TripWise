"""Deterministic, explicitly synthetic Moscow trip-cost estimates (RUB).

Not quotations or verified tariffs. A taxi ride requires dispatch/boarding time,
vehicle capacity and a positive minimum fare. Public transit is a group estimate
per boarded journey; transfers on the same network leg are not double-counted.
"""
from math import ceil, isfinite

TAXI_MIN_RUB = 420
TAXI_RUB_PER_KM = 34
TAXI_RUB_PER_DRIVING_MIN = 11
TAXI_DISPATCH_MIN = 5
TAXI_BOARDING_AND_EXIT_MIN = 3
TRANSIT_ADULT_RUB = 75
TRANSIT_CHILD_RUB = 38


def estimate_fare(mode, *, distance_meters, driving_minutes, group):
    people = sum(group[k] for k in ('adult', 'child', 'senior'))
    if mode == 'walk':
        return {'rub': 0, 'additional_minutes': 0, 'basis': 'no_fare'}
    if mode == 'transit':
        # Fare is paid once for a multimodal rail/bus path, not per transfer.
        return {'rub': group['adult'] * TRANSIT_ADULT_RUB +
                group['child'] * TRANSIT_CHILD_RUB + group['senior'] * TRANSIT_ADULT_RUB,
                'additional_minutes': 0, 'basis': 'synthetic_fare_estimate'}
    if mode != 'taxi':
        raise ValueError('Unsupported mode')
    if distance_meters is None or not isinstance(distance_meters, (int, float)) or not isfinite(distance_meters) or distance_meters < 0:
        return {'rub': None, 'additional_minutes': TAXI_DISPATCH_MIN + TAXI_BOARDING_AND_EXIT_MIN,
                'basis': 'unknown_distance'}
    cars = ceil((people + group.get('pet', 0)) / 4)
    distance_km = distance_meters / 1000
    drive = max(1, driving_minutes)
    per_car = max(TAXI_MIN_RUB, TAXI_MIN_RUB * .65 + distance_km * TAXI_RUB_PER_KM + drive * TAXI_RUB_PER_DRIVING_MIN)
    return {'rub': int(ceil(per_car * cars / 10) * 10),
            'additional_minutes': TAXI_DISPATCH_MIN + TAXI_BOARDING_AND_EXIT_MIN,
            'basis': 'synthetic_fare_estimate'}
