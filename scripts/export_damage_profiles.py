"""Export zero-enhancement melee attributes; never run as part of hit settlement."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests/reference'), str(ROOT / 'tests/reference/Business/vendor')]

from Business.scripts.combat_profile_data import HERO_WEAPONS
from Business.scripts.damage_resources import extract


def render(weapons, heroes, actions):
    lines = ['"""Offline client resource data. Regenerate with scripts/export_damage_profiles.py.',
             '', 'WEAPONS: category, family, base attribute ID, slash/pierce/blunt, shield HP, buff IDs, attack/defence letters.',
             'HEROES: base attribute ID, armor, feature IDs, max HP. ACTIONS: category -> intent -> damage ID.',
             'Sources: s_weapon_cli, s_weapon_attr, s_heroscard_cli, s_hero_attr, s_battle_param_cli.',
             'Damage type, fists, charge peak, relative weight and armor class are project rules in damage_resources.',
             '"""', '']
    for name, values in (('WEAPONS', weapons), ('HEROES', heroes), ('ACTIONS', actions)):
        lines.append(name + ' = {')
        lines.extend(f'    {key}: {value!r},' for key, value in sorted(values.items()))
        lines.extend(('}', ''))
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('client_directory', type=Path, help='Original client Bin directory (read only)')
    parser.add_argument('output', type=Path, help='New Python export file')
    args = parser.parse_args()
    content = render(*extract(str(args.client_directory), HERO_WEAPONS))
    with args.output.open('x', encoding='utf-8', newline='\n') as target:
        target.write(content)


if __name__ == '__main__':
    main()
