"""Portable offline release checks. No browser, network, credentials or run captures required."""
import ast
import importlib.metadata
import io
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft7Validator
from src.environment import ROOT


def main():
    assert sys.version_info[:2] == (3, 13), 'Use Python 3.13'
    for path in (ROOT / 'src').glob('*.py'):
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    actor = json.loads((ROOT / '.actor/actor.json').read_text(encoding='utf-8'))
    for key in ('input', 'output'):
        assert (ROOT / '.actor' / actor[key]).is_file()
    input_schema = json.loads((ROOT / '.actor/input_schema.json').read_text(encoding='utf-8'))
    assert set(input_schema['properties']) == {'url', 'outputFormat', 'extractionMethod', 'proxyConfiguration', 'proxyAccessAuthorized'}
    assert input_schema['properties']['extractionMethod']['default'] == 'nuxt'
    assert input_schema['required'] == ['url']
    assert input_schema['properties']['outputFormat']['default'] == 'reference1688'
    assert input_schema['properties']['proxyConfiguration']['default'] == {'useApifyProxy': False}
    assert input_schema['properties']['proxyAccessAuthorized']['default'] is False
    dataset = json.loads((ROOT / '.actor/dataset_schema.json').read_text(encoding='utf-8'))
    fields = ['url', 'title', 'price', 'productLadderPrices', 'moq', 'weight', 'dimensions',
              'images', 'characteristics', 'variations', 'productId']
    assert list(dataset['fields']['properties']) == fields
    assert dataset['fields']['required'] == fields and dataset['fields']['additionalProperties'] is False
    assert dataset['views']['overview']['transformation']['fields'] == fields
    assert set(dataset['views']['overview']['display']['properties']) == set(fields)
    Draft7Validator.check_schema(dataset['fields'])
    kv = json.loads((ROOT / '.actor/key_value_store_schema.json').read_text(encoding='utf-8'))
    for collection in kv['collections'].values():
        if 'jsonSchema' in collection:
            Draft7Validator.check_schema(collection['jsonSchema'])
    output = json.loads((ROOT / '.actor/output_schema.json').read_text(encoding='utf-8'))
    assert output['properties']['products']['template'] == '{{links.apiDefaultDatasetUrl}}/items'
    requirements = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    pins = {'crawl4ai': '0.9.5', 'apify': '4.2.0', 'playwright': '1.64.1'}
    for package, version in pins.items():
        assert importlib.metadata.version(package) == version
        assert f'{package}=={version}' in requirements.lower()
    names = {dist.metadata['Name'].lower() for dist in importlib.metadata.distributions()}
    assert not names.intersection({'torch', 'transformers', 'ollama', 'sentence-transformers'})
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    assert 'FROM apify/actor-python:3.13' in dockerfile
    assert 'python -m playwright install --with-deps chromium' in dockerfile
    assert '--no-shell' not in dockerfile
    assert 'CMD ["python", "-m", "src.main"]' in dockerfile
    for name in ('.dockerignore', '.actorignore'):
        rules = (ROOT / name).read_text(encoding='utf-8').splitlines()
        assert '**' in rules and '!src/*.py' in rules and '!.actor/*.json' in rules
        assert not any(line in {'!storage/', '!reports/', '!output/', '!captures/', '!backups/'} for line in rules)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(
        unittest.defaultTestLoader.discover(str(ROOT / 'tests')))
    if not result.wasSuccessful():
        print(stream.getvalue())
        raise SystemExit(1)
    print(json.dumps({'validation': 'passed', 'offlineTests': result.testsRun,
        'skippedTests': len(result.skipped),
        'inputFields': list(input_schema['properties']), 'datasetFields': len(fields),
        'coreVersions': pins, 'networkRequests': 0, 'browserLaunched': False,
        'dockerBuild': 'separate verification required', 'apifyCloudRun': 'not performed'}, indent=2))


if __name__ == '__main__':
    main()
