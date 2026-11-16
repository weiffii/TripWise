"""Локальная демонстрация рекомендаций; JSON в stdout, ошибки в stderr."""

import argparse
import json
import sys

from ai_ds.data import load_pois
from ai_ds.recommendation import RecommendationError, recommend


def main(argv=None):
    # В Windows перенаправленный поток может использовать cp1251.
    # StringIO в библиотечных тестах не имеет reconfigure и хранит Unicode.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='strict')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pois', required=True)
    parser.add_argument('--preferences', required=True)
    args = parser.parse_args(argv)
    try:
        result = recommend(load_pois(args.preferences), load_pois(args.pois))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except RecommendationError as error:
        print(json.dumps(error.response(), ensure_ascii=False), file=sys.stderr)
        return 1
    except (OSError, ValueError, OverflowError, RecursionError) as error:
        print(json.dumps({'schema_version': '0.1', 'error': {
            'code': 'INPUT_ERROR', 'details': [{'message': str(error)}]}}, ensure_ascii=False),
            file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
