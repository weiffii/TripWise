"""Собрать локальный AI/DS-пакет после проверки в изолированном каталоге."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('ai_ds', 'tests', 'examples', 'config', 'data', 'scripts', 'docs', 'reports')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/tripwise-ai-ds-2026-11-18.zip')
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        raise ValueError('Output must be new and inside the workspace')
    output.parent.mkdir(parents=True, exist_ok=True)
    secrets = [os.environ[n].encode('utf-8') for n in
               ('OPENROUTER_API_KEY', 'GEOAPIFY_API_KEY') if os.environ.get(n)]
    files = []
    for name in DIRECTORIES:
        directory = ROOT / name
        if not directory.exists():
            continue
        files.extend(p for p in directory.rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix != '.pyc')
    files.extend(p for p in [ROOT / 'AGENTS.md'] if p.exists())
    generated_report_names = {'reports/qa/clean-bundle-tests.2026-11-18.json',
                              'reports/qa/clean-bundle-tests.2026-11-18.txt',
                              'reports/qa/ai-ds-bundle.2026-11-18.json'}
    files = [p for p in files if p.relative_to(ROOT).as_posix() not in generated_report_names]
    for source in files:
        if not source.resolve().is_relative_to(ROOT):
            raise ValueError('Source resolves outside workspace')
        body = source.read_bytes()
        if any(secret in body for secret in secrets):
            raise ValueError('Credential material found in ' + source.relative_to(ROOT).as_posix())
    temporary_root = (ROOT / 'artifacts').resolve()
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ai-ds-clean-', dir=temporary_root) as tmp:
        staging = Path(tmp).resolve()
        if not staging.is_relative_to(temporary_root):
            raise ValueError('Unsafe temporary directory')
        hashes = {}
        for source in files:
            relative = source.relative_to(ROOT)
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            hashes[relative.as_posix()] = digest(source)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        for name in ('OPENROUTER_API_KEY', 'GEOAPIFY_API_KEY'):
            env.pop(name, None)
        cmd = [sys.executable, '-B', '-W', 'error::ResourceWarning',
               '-m', 'unittest', 'discover', '-s', 'tests', '-v']
        process = subprocess.run(cmd, cwd=staging, env=env, capture_output=True)
        log = process.stdout.decode('utf-8', errors='replace') + process.stderr.decode('utf-8', errors='replace')
        match = re.search(r'Ran (\d+) tests', log)
        verification = {'date': '2026-11-18', 'exit_code': process.returncode,
                        'test_count': int(match.group(1)) if match else None,
                        'passed': process.returncode == 0,
                        'environment': 'isolated directory on same Python/OS; API keys removed',
                        'live_network_requests': 0}
        reports = ROOT / 'reports/qa'
        reports.mkdir(parents=True, exist_ok=True)
        (reports / 'clean-bundle-tests.2026-11-18.txt').write_text(log, encoding='utf-8')
        (reports / 'clean-bundle-tests.2026-11-18.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
        if process.returncode:
            print(log[-6000:])
            return process.returncode
        for relative, expected in hashes.items():
            if digest(staging / relative) != expected:
                raise ValueError('Tests modified source file ' + relative)
        (staging / 'TEST_LOG.txt').write_text(log, encoding='utf-8')
        (staging / 'VERIFICATION.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
        generated_reports = staging / 'reports/qa'
        generated_reports.mkdir(parents=True, exist_ok=True)
        (generated_reports / 'clean-bundle-tests.2026-11-18.txt').write_text(log, encoding='utf-8')
        (generated_reports / 'clean-bundle-tests.2026-11-18.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
        generated = {p.relative_to(staging).as_posix(): digest(p) for p in
                     [staging / 'TEST_LOG.txt', staging / 'VERIFICATION.json',
                      generated_reports / 'clean-bundle-tests.2026-11-18.txt',
                      generated_reports / 'clean-bundle-tests.2026-11-18.json']}
        manifest = {'source_kind': 'uncommitted workspace snapshot',
                    'credentials_included': False,
                    'files': hashes, 'generated_files': generated, 'verification': verification}
        (staging / 'BUILD_MANIFEST.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(staging.rglob('*')):
                if file.is_file():
                    archive.write(file, file.relative_to(staging).as_posix())
    result = dict(verification, archive=output.relative_to(ROOT).as_posix(),
                  archive_sha256=digest(output), source_files=len(files))
    (ROOT / 'reports/qa/ai-ds-bundle.2026-11-18.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
