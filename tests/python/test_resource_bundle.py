from copy import deepcopy
import json

import pytest

from resource_bundle_fixture import ROOT, bundle, exporter


def test_export_matches_shared_rust_fixture_and_has_no_environment_paths():
    value = bundle()
    expected = json.loads((ROOT / 'tests/fixtures/resources/bundle-v1.json').read_text(encoding='utf-8'))
    assert value == expected
    encoded = exporter().encodeBundle(value)
    assert b'fixture-v1' in encoded and str(ROOT).encode() not in encoded
    assert value['combatProfiles']['1101']['701']['attacks']['300560']['phases'] == [180, 0, 600, 400]
    assert value['map']['navigation']['edges'][0] == [0, 1, -1, -1]


def test_export_is_deterministic_and_does_not_overwrite(tmp_path):
    module = exporter()
    value = bundle()
    before = deepcopy(value)
    assert module.encodeBundle(value) == module.encodeBundle(bundle())
    path = tmp_path / 'bundle.json'
    module.writeBundle(path, value)
    assert json.loads(path.read_text(encoding='utf-8')) == value
    with pytest.raises(FileExistsError):
        module.writeBundle(path, value)
    assert value == before


@pytest.mark.parametrize('rules,resources,client', [(0, 1, 'fixture'), (1, -1, 'fixture'), (1, True, 'fixture'),
                                                  (1, 1, 'C:/private'), (1, 1, ''), (1, 1, 'x' * 65)])
def test_invalid_metadata_is_rejected_before_reading_resources(rules, resources, client):
    with pytest.raises(ValueError):
        exporter().buildBundle(ROOT / 'missing/bin', rules, resources, client)


def test_nonfinite_values_fail_before_creating_output(tmp_path):
    value = bundle()
    value['map']['position'][0] = float('nan')
    path = tmp_path / 'bundle.json'
    with pytest.raises(ValueError):
        exporter().writeBundle(path, value)
    assert not path.exists()
