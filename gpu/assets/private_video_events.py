"""Numeric progress and fixed stage names on a private inherited pipe."""
import json
import os

LAST_STAGE = None

STAGES = {'preparing', 'downloading', 'loading', 'sampling', 'decoding', 'saving'}

def emit(kind, **values):
    global LAST_STAGE
    if kind == 'stage' and values.get('stage') in STAGES:
        LAST_STAGE = values['stage']
    descriptor = os.environ.get('VIDEO_EVENTS_FD')
    if not descriptor:
        return
    if kind == 'stage' and values.get('stage') in STAGES:
        event = {'kind': kind, 'stage': values['stage']}
    elif kind == 'progress':
        event = {'kind': kind, 'value': int(values['value']), 'max': int(values['max'])}
    elif kind == 'error' and values.get('code') in {'access', 'disk', 'runtime', 'generation'}:
        event = {'kind': kind, 'code': values['code']}
    else:
        return
    os.write(int(descriptor), (json.dumps(event) + '\n').encode())


def record_error(error, model=None):
    """Bounded technical failure metadata, without messages, inputs or locals."""
    from pathlib import Path
    import re
    import traceback
    allowed = {'fasth3_v2', 'fasth3_trim', 'turbowan', 'h3'}
    label = model if model in allowed else 'runtime'
    try:
        folder = Path('/workspace/wan-cloud/runtime-diagnostics')
        folder.mkdir(mode=0o700, exist_ok=True)
        frames = []
        for frame in traceback.extract_tb(error.__traceback__)[-8:]:
            filename = Path(frame.filename).name
            if not re.fullmatch(r'[A-Za-z0-9_.<>-]{1,120}', filename): filename = 'source'
            function = frame.name if re.fullmatch(r'[A-Za-z0-9_.<>-]{1,120}', frame.name) else 'function'
            frames.append({'file': filename, 'function': function, 'line': frame.lineno})
        data = {'model': label, 'stage': LAST_STAGE, 'exception_type': type(error).__name__, 'frames': frames}
        causes, seen = [], {id(error)}
        cause = error.__cause__ or error.__context__
        while cause is not None and id(cause) not in seen and len(causes) < 6:
            seen.add(id(cause))
            details = {'exception_type': type(cause).__name__, 'frames': []}
            for frame in traceback.extract_tb(cause.__traceback__)[-8:]:
                filename = Path(frame.filename).name
                if not re.fullmatch(r'[A-Za-z0-9_.<>-]{1,120}', filename): filename = 'source'
                function = frame.name if re.fullmatch(r'[A-Za-z0-9_.<>-]{1,120}', frame.name) else 'function'
                details['frames'].append({'file': filename, 'function': function, 'line': frame.lineno})
            missing = getattr(cause, 'name', None)
            if isinstance(missing, str) and re.fullmatch(r'[A-Za-z0-9_.]{1,120}', missing): details['missing_module'] = missing
            causes.append(details)
            cause = cause.__cause__ or cause.__context__
        if causes: data['causes'] = causes
        missing = getattr(error, 'name', None)
        if isinstance(missing, str) and re.fullmatch(r'[A-Za-z0-9_.]{1,120}', missing): data['missing_module'] = missing
        temporary = folder / (label + '.new')
        with temporary.open('w') as handle:
            os.chmod(temporary, 0o600)
            json.dump(data, handle)
        temporary.replace(folder / (label + '.json'))
    except Exception:
        pass
