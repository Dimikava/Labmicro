"""Pure, deterministic v7 experiment planner (Python 3.8 compatible)."""
import copy
import itertools
import math
from pathlib import Path

AXES = ('x', 'y', 'z')
FORMATS = ('npy', 'tiff', 'ome-tiff')
PATTERNS = ('serpentine', 'raster')


def number(value, label='value'):
    v = float(value)
    if not math.isfinite(v):
        raise ValueError('{} must be finite'.format(label))
    return v


def positive_int(value, label):
    i = int(value)
    if i < 1 or str(i) != str(value).strip() and not isinstance(value, int):
        raise ValueError('{} must be a positive integer'.format(label))
    return i


def positive_or_zero(value, label):
    v = number(value, label)
    if v < 0:
        raise ValueError('{} must be nonnegative'.format(label))
    return v


def axis_values(start, step, end):
    """Both endpoints included; last interval may be shorter than requested step.

    ``step`` is a positive magnitude; scan direction follows end-start.
    """
    start = number(start, 'axis start')
    end = number(end, 'axis end')
    step = number(step, 'axis step')
    if step <= 0:
        raise ValueError('Axis step magnitude must be > 0 um')
    delta = end - start
    if math.isclose(start, end, rel_tol=1e-12, abs_tol=1e-10):
        return [start]
    direction = 1.0 if delta > 0 else -1.0
    distance = abs(delta)
    n = int(math.floor(distance / step + 1e-12))
    values = [start + direction * step * j for j in range(n + 1)]
    if abs(values[-1] - end) > max(1e-9, 1e-11 * distance):
        values.append(end)
    else:
        values[-1] = end
    return values


def new_region(home_name, home_xyz):
    xyz = {a: number(home_xyz[a], a) for a in AXES}
    return {
        'home': str(home_name), 'home_xyz_um': xyz,
        'axes': {a: {'end_um': xyz[a], 'step_um': 1.0} for a in AXES},
    }


def new_recipe(name, home_name, home_xyz, output_root):
    return {
        'version': 7, 'name': str(name), 'output_root': str(Path(output_root).expanduser().resolve()),
        'regions': [new_region(home_name, home_xyz)], 'pattern': 'serpentine',
        'channels': [{'name': 'default', 'exposure_ms': None, 'actions': []}],
        'dwell_ms': 0.0, 'repeats': 1, 'time': {'count': 1, 'interval_s': 0.0},
        'format': 'npy', 'return_home': False,
    }


def validate(recipe):
    if not isinstance(recipe, dict) or recipe.get('version') != 7:
        raise ValueError('Requires v7 recipe')
    if not recipe.get('name') or not isinstance(recipe.get('name'), str):
        raise ValueError('Experiment name missing')
    if not recipe.get('output_root'):
        raise ValueError('Output directory missing')
    if recipe.get('format') not in FORMATS:
        raise ValueError('Image format must be one of {}'.format(FORMATS))
    if recipe.get('pattern') not in PATTERNS:
        raise ValueError('Unknown scan pattern')
    positive_int(recipe.get('repeats', 1), 'repeats')
    positive_int(recipe['time']['count'], 'time count')
    positive_or_zero(recipe['time']['interval_s'], 'time interval')
    positive_or_zero(recipe.get('dwell_ms', 0), 'dwell')
    regions = recipe.get('regions', [])
    channels = recipe.get('channels', [])
    if not regions or not channels:
        raise ValueError('At least one region and one channel are required')
    if len({r['home'] for r in regions}) != len(regions):
        raise ValueError('Duplicate home/region name')
    if len({c['name'] for c in channels}) != len(channels):
        raise ValueError('Duplicate channel name')
    for r in regions:
        for a in AXES:
            number(r['home_xyz_um'][a], 'home ' + a)
            spec = r['axes'][a]
            axis_values(r['home_xyz_um'][a], spec['step_um'], spec['end_um'])
    for c in channels:
        if not c['name'] or any(ch.isspace() for ch in c['name']):
            raise ValueError('Channel names must be nonempty and contain no whitespace')
        if c.get('exposure_ms') is not None and number(c['exposure_ms'], 'exposure') <= 0:
            raise ValueError('Exposure must be positive')
        for action in c.get('actions', []):
            kind = action.get('type')
            if kind == 'property':
                if not all(action.get(k) is not None for k in ('device', 'property', 'value')):
                    raise ValueError('Invalid channel property action')
            elif kind == 'state':
                int(action['index'])
                if not action.get('device'):
                    raise ValueError('Missing state device')
            elif kind == 'label':
                if not action.get('device') or not action.get('label'):
                    raise ValueError('Invalid label action')
            elif kind == 'preset':
                if not action.get('group') or not action.get('preset'):
                    raise ValueError('Invalid preset action')
            else:
                raise ValueError('Unknown channel action {!r}'.format(kind))
    return recipe


def coordinate_axes(region):
    return {a: axis_values(region['home_xyz_um'][a], region['axes'][a]['step_um'],
                           region['axes'][a]['end_um']) for a in AXES}


def region_positions(region, pattern='serpentine'):
    """X-fastest scan, then Y, then Z, preserving sequential row order."""
    axes = coordinate_axes(region)
    xs, ys, zs = axes['x'], axes['y'], axes['z']
    for zi, z in enumerate(zs):
        for yi, y in enumerate(ys):
            if pattern == 'serpentine' and yi % 2 == 1:
                order = range(len(xs) - 1, -1, -1)
            else:
                order = range(len(xs))
            for xi in order:
                yield {'x_index': xi, 'y_index': yi, 'z_index': zi,
                       'xyz_um': {'x': xs[xi], 'y': y, 'z': z}}


def total_frames(recipe):
    validate(recipe)
    count = 0
    for region in recipe['regions']:
        axes = coordinate_axes(region)
        count += len(axes['x']) * len(axes['y']) * len(axes['z'])
    return count * len(recipe['channels']) * recipe['time']['count'] * recipe['repeats']


def iter_events(recipe):
    """Frozen deterministic ordering: time > region > Z > Y > X > channel > repeat."""
    validate(recipe)
    index = 0
    for ti in range(recipe['time']['count']):
        for ri, region in enumerate(recipe['regions']):
            for xyz in region_positions(region, recipe['pattern']):
                for ci, channel in enumerate(recipe['channels']):
                    for repeat in range(recipe['repeats']):
                        event = {'index': index, 'time_index': ti, 'region_index': ri,
                                 'region_home': region['home'], 'channel_index': ci,
                                 'channel_name': channel['name'], 'repeat_index': repeat}
                        event.update(xyz)
                        yield event
                        index += 1


def filename(event, ext):
    return 'P{:02d}_T{:03d}_C{:02d}_Z{:04d}_Y{:04d}_X{:04d}_Q{:02d}.{}'.format(
        event['region_index'], event['time_index'], event['channel_index'],
        event['z_index'], event['y_index'], event['x_index'], event['repeat_index'], ext)


def freeze(recipe):
    spec = copy.deepcopy(recipe)
    validate(spec)
    return spec


def summary(recipe):
    validate(recipe)
    details = []
    for region in recipe['regions']:
        axes = coordinate_axes(region)
        details.append({'home': region['home'], 'start_xyz_um': region['home_xyz_um'],
                        'axes': {a: {'positions': len(axes[a]), 'start_um': axes[a][0],
                                     'end_um': axes[a][-1], 'requested_step_um': region['axes'][a]['step_um'],
                                     'last_step_um': abs(axes[a][-1] - axes[a][-2]) if len(axes[a]) > 1 else 0.0}
                                 for a in AXES}})
    return {'total_frames': total_frames(recipe), 'regions': details,
            'channels': len(recipe['channels']), 'timepoints': recipe['time']['count'],
            'repeats': recipe['repeats'], 'pattern': recipe['pattern']}
