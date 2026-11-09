"""Persistent v7 experiment recipes, independent of v5 home/sequence files."""
import copy
import json
import os
from pathlib import Path
import re
from . import planner


def checked_name(name, kind='name'):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', str(name)) or str(name) in ('.', '..'):
        raise ValueError('{} may use letters, digits, _, -, . and must start with a letter/digit'.format(kind))
    return str(name)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.partial')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(data, output, indent=2, ensure_ascii=False, sort_keys=True)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    os.replace(str(temporary), str(path))


class Experiments:
    def __init__(self, path, home_store):
        self.path = Path(path)
        self.homes = home_store
        self.items = {}
        if self.path.is_file():
            self.items = json.loads(self.path.read_text(encoding='utf-8-sig'))
            if not isinstance(self.items, dict):
                raise ValueError('Invalid v7 experiment store')
            for r in self.items.values():
                planner.validate(r)

    def save(self):
        atomic_json(self.path, self.items)

    def get(self, name):
        if name not in self.items:
            raise ValueError('Unknown experiment {!r}'.format(name))
        return copy.deepcopy(self.items[name])

    def create(self, name, home, output):
        checked_name(name, 'experiment')
        checked_name(home, 'home')
        if name in self.items:
            raise ValueError('Experiment already exists: {}'.format(name))
        spec = planner.new_recipe(name, home, self.homes.get_xyz(home), output)
        planner.validate(spec)
        self.items[name] = spec
        self.save()
        return copy.deepcopy(spec)

    def change(self, name, mutate):
        spec = self.get(name)
        mutate(spec)
        planner.validate(spec)
        self.items[name] = spec
        self.save()
        return copy.deepcopy(spec)

    def _region(self, spec, home=None):
        if home is None:
            return spec['regions'][0]
        for region in spec['regions']:
            if region['home'] == home:
                return region
        raise ValueError('Unknown region/home {!r} for this experiment'.format(home))

    def add_region(self, name, home):
        checked_name(home, 'home')
        def update(s):
            if any(r['home'] == home for r in s['regions']):
                raise ValueError('Region already included')
            s['regions'].append(planner.new_region(home, self.homes.get_xyz(home)))
        return self.change(name, update)

    def delete_region(self, name, home):
        def update(s):
            s['regions'] = [r for r in s['regions'] if r['home'] != home]
        return self.change(name, update)

    def refresh_home(self, name, home):
        def update(s):
            self._region(s, home)['home_xyz_um'] = self.homes.get_xyz(home)
        return self.change(name, update)

    def set_axis(self, name, axis, step, endpoint, home=None, relative=False):
        axis = axis.lower()
        if axis not in planner.AXES:
            raise ValueError('Axis must be x, y or z')
        def update(s):
            region = self._region(s, home)
            end = planner.number(endpoint, 'end')
            if relative:
                end += region['home_xyz_um'][axis]
            region['axes'][axis] = {'step_um': abs(planner.number(step, 'step')),
                                    'end_um': end}
        return self.change(name, update)

    def set_value(self, name, key, value):
        def update(s):
            s[key] = value
        return self.change(name, update)

    def set_time(self, name, count, interval):
        def update(s):
            s['time'] = {'count': planner.positive_int(count, 'time count'),
                         'interval_s': planner.positive_or_zero(interval, 'interval')}
        return self.change(name, update)

    def add_channel(self, name, channel):
        checked_name(channel, 'channel')
        def update(s):
            if len(s['channels']) == 1 and s['channels'][0]['name'] == 'default' and not s['channels'][0]['actions'] and s['channels'][0]['exposure_ms'] is None:
                s['channels'] = []
            s['channels'].append({'name': channel, 'exposure_ms': None, 'actions': []})
        return self.change(name, update)

    def delete_channel(self, name, channel):
        def update(s):
            s['channels'] = [c for c in s['channels'] if c['name'] != channel]
        return self.change(name, update)

    @staticmethod
    def _channel(spec, channel):
        for c in spec['channels']:
            if c['name'] == channel:
                return c
        raise ValueError('Unknown channel {!r}'.format(channel))

    def exposure(self, name, ms, channel=None):
        ms = planner.number(ms, 'exposure')
        def update(s):
            if channel is None:
                for c in s['channels']:
                    c['exposure_ms'] = ms
            else:
                self._channel(s, channel)['exposure_ms'] = ms
        return self.change(name, update)

    def channel_action(self, name, channel, action):
        def update(s):
            self._channel(s, channel)['actions'].append(action)
        return self.change(name, update)

    def clear_channel_actions(self, name, channel):
        def update(s):
            self._channel(s, channel)['actions'] = []
        return self.change(name, update)

    def delete(self, name):
        self.get(name)
        del self.items[name]
        self.save()
