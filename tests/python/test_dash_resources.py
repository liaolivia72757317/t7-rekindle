import math

import pytest

from Business.scripts import dash_resources as resources
from Business.scripts.animation_resource import animationDurationMs
from Business.scripts.vfs_resource import ResourceError
from dash_fixture import animation, parameters, states, weapons


@pytest.fixture
def source(monkeypatch, tmp_path):
    data = {resources.WEAPON_PATH: weapons(), resources.PARAM_PATH: parameters(),
            resources.STATE_PATH: states(), resources.DASH_ANIMATIONS[1030411]: animation()}
    calls = []

    def read(directory, archive, path, parser):
        calls.append(path)
        if path not in data:
            raise ResourceError('entry_missing', archive, path, 'fixture missing')
        try:
            return parser(data[path])
        except ValueError as error:
            raise ResourceError('content_mismatch', archive, path, str(error)) from error

    resources.loadDashAttack.cache_clear()
    monkeypatch.setattr(resources, 'readResource', read)
    yield str(tmp_path / 'Bin'), data, calls
    resources.loadDashAttack.cache_clear()


def load(source, weaponId=1030411):
    return resources.loadDashAttack(source[0], weaponId)


@pytest.mark.parametrize('category', [32, 119])
def test_dash_selects_native_action_type_13_not_war_skill_32(source, category):
    source[1][resources.WEAPON_PATH] = weapons((1030411, category))
    dash = parameters(category=category, actionType=13, duration='1233')[140:]
    warSkill = parameters(category=category, actionType=32, duration='2033')[140:]
    source[1][resources.PARAM_PATH] = parameters(rows=[warSkill, dash])
    result = load(source)
    assert result.profile.enabled and result.profile.durationMs == 1233
    assert not result.diagnostics


def test_war_skill_row_does_not_enable_a_missing_dash(source):
    source[1][resources.PARAM_PATH] = parameters(actionType=32)
    result = load(source)
    assert not result.profile.enabled
    assert any('key=(6,32,13,2)' in message for _, message in result.diagnostics)


def test_current_weapon_uses_table_without_loading_animation(source):
    result = load(source)
    value = result.profile
    assert value.enabled and value.durationMs == 2033
    assert (value.moveMs, value.speedMilli, value.contactStartMs, value.contactEndMs, value.reboundMs) == (967, 2585, 0, 1133, 933)
    assert value.durationSource == 'table'
    assert resources.DASH_ANIMATIONS[1030411] not in source[2]
    assert not result.diagnostics


@pytest.mark.parametrize('weaponId,category,duration', [(200, 2, 1400), (300, 37, 1933)])
def test_duration_selection_uses_weapon_record_not_id_arithmetic(source, weaponId, category, duration):
    source[1][resources.WEAPON_PATH] = weapons((weaponId, category))
    source[1][resources.PARAM_PATH] = parameters(category=category, duration=str(duration))
    value = load(source, weaponId).profile
    assert value.enabled and value.weaponCategory == category and value.durationMs == duration


@pytest.mark.parametrize('change', ['missing', 'empty', 'invalid', 'duplicate', 'out-of-range'])
def test_duration_errors_fall_back_to_animation_with_diagnostics(source, change):
    source[1][resources.PARAM_PATH] = parameters(
        duration={'empty': '', 'invalid': 'bad', 'out-of-range': '65536'}.get(change, '2033'),
        omit=('[持续时间]过程',) if change == 'missing' else (),
        extra=(('[持续时间]过程', '1900'),) if change == 'duplicate' else ())
    result = load(source)
    assert result.profile.enabled and result.profile.durationMs == 1333
    assert result.profile.durationSource == 'animation'
    assert [level for level, _ in result.diagnostics] == ['ERROR', 'WARN']
    assert '[持续时间]过程' in result.diagnostics[0][1]
    assert '1333' in result.diagnostics[1][1]


@pytest.mark.parametrize('change', ['missing-file', 'bad-format', 'duplicate-row'])
def test_unusable_parameter_table_attempts_fallback_but_disables_missing_other_fields(source, change):
    if change == 'missing-file':
        del source[1][resources.PARAM_PATH]
    elif change == 'bad-format':
        source[1][resources.PARAM_PATH] = b'bad'
    else:
        row = parameters()[140:]
        source[1][resources.PARAM_PATH] = parameters(rows=[row, row])
    result = load(source)
    assert not result.profile.enabled
    assert resources.DASH_ANIMATIONS[1030411] in source[2]
    assert any('disabled' in message for _, message in result.diagnostics)


@pytest.mark.parametrize('field', ['[移动时间]', '[移动初速度]', '[碰撞时间]开始', '[碰撞时间]结束'])
def test_other_missing_fields_disable_dash_without_borrowing_defaults(source, field):
    source[1][resources.PARAM_PATH] = parameters(omit=(field,))
    result = load(source)
    assert not result.profile.enabled
    assert any(field in message for _, message in result.diagnostics)


@pytest.mark.parametrize('field,value', [('[移动时间]', '2034'), ('[移动初速度]', '-1'),
                                        ('[碰撞时间]开始', '1134'), ('[碰撞时间]结束', '65536')])
def test_invalid_required_parameters_disable_dash_and_report_resource_error(source, field, value):
    source[1][resources.PARAM_PATH] = parameters(omit=(field,), extra=((field, value),))
    result = load(source)
    assert not result.profile.enabled
    assert resources.validProfile(result.asDict()['dashAttack'])
    assert any('[T7R_RESOURCE:content_mismatch]' in message for _, message in result.diagnostics)


def test_duplicate_move_parameter_does_not_borrow_another_value(source):
    source[1][resources.PARAM_PATH] = parameters(extra=(('[移动时间]', '500'),))
    result = load(source)
    assert not result.profile.enabled
    assert any('duplicate' in message and '[移动时间]' in message for _, message in result.diagnostics)


@pytest.mark.parametrize('entries', [((1030411, 32), (1030411, 32)), ((1030411, 65535),)])
def test_duplicate_or_invalid_weapon_category_disables_dash(source, entries):
    source[1][resources.WEAPON_PATH] = weapons(*entries)
    result = load(source)
    assert not result.profile.enabled and resources.validProfile(result.asDict()['dashAttack'])
    assert any('weaponCategory' in message for _, message in result.diagnostics)


def test_missing_rebound_disables_dash(source):
    del source[1][resources.STATE_PATH]
    assert not load(source).profile.enabled


@pytest.mark.parametrize('duration', [0, -11003, 65536])
def test_rebound_requires_literal_positive_table_duration(source, duration):
    source[1][resources.STATE_PATH] = states(duration)
    result = load(source)
    assert not result.profile.enabled
    assert any('reboundMs' in message for _, message in result.diagnostics)


def test_failed_animation_fallback_disables_dash(source):
    source[1][resources.PARAM_PATH] = parameters(duration='')
    del source[1][resources.DASH_ANIMATIONS[1030411]]
    result = load(source)
    assert not result.profile.enabled
    assert any('animation' in message for _, message in result.diagnostics)


def test_fallback_does_not_clip_windows_to_short_animation(source):
    source[1][resources.PARAM_PATH] = parameters(duration='')
    source[1][resources.DASH_ANIMATIONS[1030411]] = animation(.5)
    assert not load(source).profile.enabled


def test_unknown_weapon_does_not_borrow_default_category_or_animation(source):
    source[1][resources.WEAPON_PATH] = weapons((200, 99))
    assert not load(source, 200).profile.enabled


def test_zero_movement_and_nonzero_contact_start_are_valid(source):
    source[1][resources.PARAM_PATH] = parameters(duration='767',
        omit=('[移动时间]', '[移动初速度]', '[碰撞时间]开始', '[碰撞时间]结束'),
        extra=(('[移动时间]', '0'), ('[移动初速度]', '0'), ('[碰撞时间]开始', '267'), ('[碰撞时间]结束', '450')))
    value = load(source).profile
    assert value.enabled and (value.moveMs, value.speedMilli, value.contactStartMs, value.contactEndMs) == (0, 0, 267, 450)


def test_profile_cache_is_immutable_and_export_is_detached(source):
    result = load(source)
    exported = result.asDict()
    exported['dashAttack']['durationMs'] = 10
    assert load(source) is result and result.profile.durationMs == 2033


def test_animation_reader_rounds_duration_without_constant_fallback():
    assert animationDurationMs(animation(1.6)) == 1600


@pytest.mark.parametrize('seconds', [0, -1, math.inf, math.nan, 66])
def test_animation_reader_rejects_unusable_duration(seconds):
    with pytest.raises(ValueError):
        animationDurationMs(animation(seconds))


@pytest.mark.parametrize('data', [b'', b'bad', animation()[:-1], animation() + b'\0'])
def test_animation_reader_rejects_malformed_tagfiles(data):
    with pytest.raises(ValueError):
        animationDurationMs(data)
