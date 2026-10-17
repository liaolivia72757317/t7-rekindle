"""Offline clip timing extraction. Runtime uses the exported static table."""
from functools import lru_cache
import math
import posixpath

from .animation_resource import AnimationReader, ClipReader
from .dash_resources import readSelected
from .vfs_resource import ResourceError, canonicalPath

ANIMATION_ROOT = 'data/actors/havok/shgameexport/animations/'


def effectiveDuration(seconds, generator):
    start = generator.get('cropStartAmountLocalTime', 0.)
    end = generator.get('cropEndAmountLocalTime', 0.)
    speed = generator.get('playbackSpeed', 1.)
    if (any(type(n) not in (int, float) or not math.isfinite(n) for n in (seconds, start, end, speed))
            or min(start, end) < 0 or seconds <= start + end or speed <= 0
            or generator.get('mode', 0) != 0):
        raise ValueError('non_finite_clip')
    # enforcedDuration is the server clock being replaced, not the source clip length.
    result = math.floor((seconds - start - end) / speed * 1000 + .5)
    if not 1 <= result <= 65535:
        raise ValueError('duration_out_of_range')
    return result


@lru_cache(maxsize=1024)
def clipSeconds(directory, path):
    values = readSelected(directory, 'actor.vfs', path, lambda data: ClipReader(data).durations())
    if len(values) != 1:
        raise ValueError('ambiguous_clip')
    return values[0]


class AnimationTimings:
    def __init__(self, directory, characterPath):
        self.directory, self.aliases, self.generators = directory, {}, {}
        self.error = None
        try:
            self.read(characterPath)
        except (ValueError, KeyError, IndexError, TypeError) as error:
            self.error = error.code if isinstance(error, ResourceError) else 'invalid_character_binding'

    def read(self, characterPath):
        objects = readSelected(self.directory, 'actor.vfs', characterPath, lambda data: AnimationReader(data).objects())
        strings, = [obj for obj in objects[1:] if obj['className'] == 'hkbCharacterStringData']
        names, files = strings['animationBundleNameData'], strings['animationBundleFilenameData']
        if len(names) != len(files):
            raise ValueError('animation_bundle_count')
        for nameBundle, fileBundle in zip(names, files):
            for alias, filename in zip(nameBundle['assetNames'], fileBundle['assetNames']):
                if not filename:
                    continue
                path = canonicalPath(ANIMATION_ROOT + filename)
                if alias in self.aliases and self.aliases[alias] != path:
                    raise ValueError('duplicate_animation_alias')
                self.aliases[alias] = path
        graphPath = canonicalPath(posixpath.normpath(posixpath.dirname(characterPath) + '/'
                                 + strings['behaviorFilename'].replace('\\', '/')))
        graph = readSelected(self.directory, 'actor.vfs', graphPath, lambda data: AnimationReader(data).objects())
        graphStrings, = [obj for obj in graph[1:] if obj['className'] == 'hkbBehaviorGraphStringData']
        variables = graphStrings.get('variableNames', [])
        for obj in graph[1:]:
            if obj['className'] != 'hkbClipGenerator':
                continue
            obj = dict(obj)
            bindings = graph[obj['variableBindingSet']].get('bindings', []) if obj.get('variableBindingSet') else []
            for binding in bindings:
                member = binding.get('memberPath')
                if member in ('playbackSpeed', 'cropStartAmountLocalTime', 'cropEndAmountLocalTime', 'mode', 'animationName'):
                    obj['unresolvedTiming'] = True
                if member == 'enforcedDuration' and variables[binding.get('variableIndex', 0)] != 'BattleStateDuration':
                    obj['unresolvedTiming'] = True
            self.generators.setdefault(obj.get('animationName'), []).append(obj)

    def select(self, tableMs, aliases):
        evidence = dict(tableMs=tableMs, source='table')
        try:
            if self.error:
                raise ValueError(self.error)
            durations, paths = set(), set()
            if not aliases:
                raise ValueError('missing_animation_binding')
            for alias in aliases:
                if alias not in self.aliases or alias not in self.generators:
                    raise ValueError('missing_animation_binding')
                path = self.aliases[alias]
                for generator in self.generators[alias]:
                    if generator.get('unresolvedTiming'):
                        raise ValueError('dynamic_clip_timing')
                    durations.add(effectiveDuration(clipSeconds(self.directory, path), generator))
                    paths.add(path)
            if len(durations) != 1:
                raise ValueError('ambiguous_animation_duration')
            duration = durations.pop()
            return duration, evidence | dict(source='animation', animationMs=duration, animations=sorted(paths))
        except (ValueError, KeyError, IndexError, TypeError) as error:
            reason = error.code if isinstance(error, ResourceError) else str(error)
            return tableMs, evidence | dict(reason=reason)
