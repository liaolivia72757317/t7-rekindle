"""Final SCORE fields and reward-free SETTLE_UPDATE_MSG for deathmatch."""
from .kill_flow import MAX_SCORE, SCORE_UPDATE_EVENT, pack

SETTLE_COMMAND, SETTLE_UPDATE_MSG = 52, 1
GAME_RESULT_FIELD, POINT_RANK_FIELD = 9, 51
INVALID, WIN, LOSE, DRAW = 0, 1, 2, 3


def encode_final_score(*, server_time_ms, entity_rid, kills, deaths, points, result, rank):
    if any(type(value) is not int or not 0 <= value <= MAX_SCORE for value in (kills, deaths, points)):
        raise ValueError('final score values must fit a nonnegative int32')
    if type(result) is not int or result not in (INVALID, WIN, LOSE, DRAW):
        raise ValueError('invalid personal game result')
    if type(rank) is not int or not 1 <= rank <= 20:
        raise ValueError('final score rank must be in 1..20')
    fields = ((2, kills), (44, kills), (3, deaths), (1, points),
              (GAME_RESULT_FIELD, result), (POINT_RANK_FIELD, rank))
    return pack('>HQQii', SCORE_UPDATE_EVENT, server_time_ms, entity_rid, 0, len(fields)) + b''.join(
        pack('>iii', key, value, value) for key, value in fields)


def encode_settle_update(*, level, start_pattern, result):
    if type(level) is not int or not 1 <= level <= MAX_SCORE:
        raise ValueError('settlement level must be a positive int32')
    if type(start_pattern) is not int or start_pattern != 2:
        raise ValueError('settlement supports deathmatch start pattern 2')
    if type(result) is not int or result not in (INVALID, WIN, LOSE, DRAW):
        raise ValueError('invalid personal game result')
    # START_PATTERN_DATA's default arm is one byte; all three reward arrays are empty.
    return pack('>HiiiBiiii', SETTLE_UPDATE_MSG, level, 0, start_pattern, 0, 0, 0, 0, result)
