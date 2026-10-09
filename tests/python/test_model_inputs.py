from copy import deepcopy
import math
import struct

import pytest

from Business.scripts import controls
from test_runtime_movement import make_runtime_flow


def keyReport(category, *, crouch=0, jump=0, position=(math.nan, math.inf, -math.inf)):
    return struct.pack(">Hi6Bfff", 52, category, 1, 1, 1, 1, crouch, jump, *position)


@pytest.mark.parametrize("category,field", [(2, "crouched"), (3, "jumpPressed")])
def test_local_action_report_tracks_press_and_release_without_movement_echo(category, field):
    flow = make_runtime_flow()
    original = {"position": [10., 20., 30.], "mask": 1, "heading": 90, "tick": 12,
                "moveStartedAt": 20, "crouched": False, "jumpPressed": False}
    flow.session["ground"] = deepcopy(original)
    for pressed in (1, 1, 0, 0):
        body = keyReport(category, crouch=pressed if category == 2 else 0,
                         jump=pressed if category == 3 else 0)
        assert controls.message(flow, 2, 52, body)
        assert flow.session["ground"] == {**original, field: bool(pressed)}
    assert flow.result["send"] == []
    assert flow.result["timers"] == []


@pytest.mark.parametrize("category,otherField", [(2, "jumpPressed"), (3, "crouched")])
def test_special_key_category_preserves_the_other_action(category, otherField):
    flow = make_runtime_flow()
    controls.groundState(flow)[otherField] = True
    assert controls.message(flow, 2, 52, keyReport(category))
    assert flow.session["ground"][otherField] is True
    assert flow.session["ground"]["position"] is None


@pytest.mark.parametrize("category", [2, 3])
def test_special_key_category_obeys_preparation_lock(category):
    flow = make_runtime_flow(playing=False)
    before = deepcopy(flow.state)
    assert controls.message(flow, 2, 52, keyReport(category, crouch=1, jump=1))
    assert flow.state == before
    assert flow.result["send"] == []
    assert flow.result["timers"] == []


@pytest.mark.parametrize("category", [2, 3])
@pytest.mark.parametrize("invalid", ["short", "long", "flag"])
def test_malformed_special_key_report_does_not_mutate_state(category, invalid):
    flow = make_runtime_flow()
    before = deepcopy(flow.state)
    body = keyReport(category)
    if invalid == "short":
        body = body[:-1]
    elif invalid == "long":
        body += b"\0"
    else:
        body = body[:10] + b"\2" + body[11:]
    with pytest.raises(ValueError):
        controls.message(flow, 2, 52, body)
    assert flow.state == before


def test_unknown_input_category_is_not_treated_as_an_animation_request():
    flow = make_runtime_flow()
    before = deepcopy(flow.state)
    assert not controls.message(flow, 2, 52, keyReport(4))
    assert flow.state == before
