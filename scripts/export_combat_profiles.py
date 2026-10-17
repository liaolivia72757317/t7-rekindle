"""One-time, read-only client inspection; emit server-owned combat configuration."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests/reference'), str(ROOT / 'tests/reference/Business/vendor')]

from Business.scripts import combat_resources as combat, hero_resources
from Business.scripts.animation_timing import AnimationTimings
from Business.scripts.dash_resources import readSelected, rows

DIRECTIONS = {300560: 'Up', 300550: 'Right', 300570: 'Down', 300540: 'Left'}
KEY_MSG_PATH = 'data/table/s_res_key_msg_cli.bin'


def attackDirections(weaponData, keyData):
    columns = [set() for _ in range(35)]
    for row in rows(keyData, 318):
        count = struct.unpack_from('<H', row, 4)[0]
        if count > 8:
            raise ValueError('key-message condition count exceeds record')
        conditions = struct.unpack_from('<8I', row, 6)
        # Bots are on foot: KEY_COND_RIDING (11) is not an available input route.
        if 11 in conditions[:count]:
            continue
        for column, messages in enumerate(columns):
            message = struct.unpack_from('<I', row, 38 + column * 8)[0]
            if message in DIRECTIONS:
                messages.add(message)
    result = {}
    for row in rows(weaponData, 2829):
        weaponId = struct.unpack_from('<I', row)[0]
        # Native weapon input lookup uses this one-based message translation index.
        column = struct.unpack_from('<H', row, 0x808)[0]
        result[weaponId] = set(columns[column - 1]) if 1 <= column <= 35 else set()
    return result


def applyDirections(profile, allowed):
    result = deepcopy(profile)
    if not result['shield']:
        if not allowed:
            raise ValueError('melee weapon has no on-foot attack input mapping')
        for message, attack in result['attacks'].items():
            attack['enabled'] = int(message) in allowed
    return result


def hurtTiming(directory):
    data = readSelected(directory, 'data1.vfs', combat.STATE_PATH, lambda data: data)
    values = {struct.unpack_from('<i', row, 0x1e3e)[0]
              for row in rows(data, 10950) if struct.unpack_from('<I', row)[0] == 35}
    if len(values) != 1 or not 0 < next(iter(values)) <= 65535:
        raise ValueError('expected a positive hurt-state table duration')
    return values.pop()


def applyTimings(profile, animation, hurtMs):
    result, evidence, windows = deepcopy(profile), {}, {}
    if result['shield']:
        return result

    def choose(name, tableMs, aliases=None):
        value, source = animation.select(tableMs, aliases if aliases is not None else [name])
        evidence[name] = (tableMs, source.get('animationMs'), source.get('reason', ''))
        return value

    def clipContact(name, value, duration):
        end = value['contactEndMs']
        if end > duration:
            value['contactEndMs'] = duration
            windows[name] = (end, duration)

    for intent, direction in DIRECTIONS.items():
        attack = result['attacks'][str(intent)]
        oldProcess = attack['phases'][2]
        for index, phase in ((0, 'Prepare'), (2, 'Process'), (3, 'Finish')):
            attack['phases'][index] = choose('Attack' + direction + phase, attack['phases'][index])
        for field, phase in (('parried', 'Parryed'), ('rebound', 'Fend')):
            attack[field] = choose('Attack' + direction + phase, attack[field])
        # Preserve the existing 0/0-table policy: collision covers the whole process phase.
        if attack['contactStartMs'] == 0 and attack['contactEndMs'] == oldProcess:
            attack['contactEndMs'] = attack['phases'][2]
        clipContact('Attack' + direction + 'Process', attack, attack['phases'][2])
    result['changeMs'] = choose('ChangeWeapon', result['changeMs'])
    for field, prefix in (('prepareMs', 'BlockPrepare'), ('successMs', 'BlockSuccess')):
        aliases = sorted(alias for alias in animation.generators if alias and alias.startswith(prefix))
        result['guard'][field] = choose(prefix, result['guard'][field], aliases)
    aliases = sorted(alias for alias in animation.generators if alias and alias.startswith('Hurt'))
    result['hurtMs'] = choose('Hurt', hurtMs, aliases)
    dash = result['dashAttack']
    if dash:
        dash['durationMs'] = choose('DashAttack', dash['durationMs'])
        dash['reboundMs'] = choose('DashAttackParryed', dash['reboundMs'])
        dash['durationSource'] = 'animation' if evidence['DashAttack'][1] is not None else 'table'
        dash['animationPath'] = animation.aliases.get('DashAttack', '')
        clipContact('DashAttack', dash, dash['durationMs'])
    result['timingSources'] = evidence
    if windows:
        result['windowAdjustments'] = windows
    if not combat.validProfile(result):
        raise ValueError('invalid exported combat profile')
    return result


def export(directory):
    catalogue = hero_resources.loadCatalogue(directory)
    allowed = attackDirections(
        readSelected(directory, 'data1.vfs', hero_resources.WEAPON_PATH, lambda data: data),
        readSelected(directory, 'data1.vfs', KEY_MSG_PATH, lambda data: data))
    animations, templates, references = {}, [], {}
    hurtMs = hurtTiming(directory)
    for heroId, hero in sorted(catalogue.heroes.items()):
        references[heroId] = {}
        ids = {hero_resources.FIST_ID, *(tid for group in hero.groups for tid in group if tid)}
        for weaponId in sorted(ids):
            profile = combat.weaponProfile(directory, weaponId, hero.gender)
            profile = applyDirections(profile, allowed[weaponId])
            if not profile['shield']:
                path = profile['movementAnimation']['characterPath']
                if path not in animations:
                    animations[path] = AnimationTimings(directory, path)
                profile = applyTimings(profile, animations[path], hurtMs)
            profile = deepcopy(profile)
            profile.pop('weaponId')
            for key in ('movementAnimation', 'dashAttack'):
                profile[key].pop('weaponId', None)
            if profile not in templates:
                templates.append(profile)
            references[heroId][weaponId] = templates.index(profile)
    return references, templates


def render(references, templates):
    lines = ['"""Generated by scripts/export_combat_profiles.py; no runtime client reads.',
             'timingSources: action -> (tableMs, animationMs or None, fallback reason).',
             '"""', '', 'HERO_WEAPONS = {']
    for heroId, value in sorted(references.items()):
        lines.append(f'    {heroId}: {value!r},')
    lines.extend(['}', '', 'PROFILES = ('])
    for index, profile in enumerate(templates):
        lines.append(f'    {{  # {index}')
        for key, value in profile.items():
            if key in ('attacks', 'timingSources'):
                lines.append(f'        {key!r}: {{')
                for name, entry in value.items():
                    lines.append(f'            {name!r}: {entry!r},')
                lines.append('        },')
            else:
                lines.append(f'        {key!r}: {value!r},')
        lines.append('    },')
    return '\n'.join([*lines, ')', ''])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-directory', type=Path, required=True, help='selected absolute Bin directory')
    parser.add_argument('--output', type=Path, required=True, help='new .py file; existing files are not overwritten')
    args = parser.parse_args()
    if not args.client_directory.is_absolute():
        parser.error('--client-directory must be absolute')
    references, templates = export(str(args.client_directory))
    with args.output.open('x', encoding='utf-8', newline='\n') as output:
        output.write(render(references, templates))
    sources = Counter('animation' if item[1] is not None else 'table:' + item[2]
                      for profile in templates for item in profile.get('timingSources', {}).values())
    print(f'heroes={len(references)} profiles={sum(map(len, references.values()))} templates={len(templates)}')
    print(dict(sources))


if __name__ == '__main__':
    main()
