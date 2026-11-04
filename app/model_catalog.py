"""Supported local video runtimes; no user prompts or model execution state."""
DEFAULT_MODEL = 'fasth3_v2'
MODELS = [
    {'id': 'fasth3_v2', 'name': 'FastH3 V2 NVFP4', 'label': 'FastH3 V2 · 8 steps',
     'durations': [5, 10], 'sizes': [[832, 480], [1344, 768]], 'default_width': 832, 'default_height': 480,
     'audio': True, 'sampler_node': '21', 'steps': 8, 'fps': 24, 'runtime': 'comfy',
     'note': 'Official native Comfy model, eight sampling steps.'},
    {'id': 'fasth3_trim', 'name': 'FastH3 Trim NVFP4', 'label': 'FastH3 Trim · 8 steps',
     'durations': [5, 10], 'sizes': [[832, 480], [1344, 768]], 'default_width': 832, 'default_height': 480,
     'audio': True, 'sampler_node': '21', 'steps': 8, 'fps': 24, 'runtime': 'comfy',
     'note': 'Smaller native Comfy model, sharing H3 text and audio/video decoders.'},
    {'id': 'turbowan', 'name': 'TurboWan 2.1 1.3B TurboDiffusion', 'label': 'TurboWan 2.1 1.3B · 4 steps',
     'durations': [5], 'sizes': [[832, 480]], 'default_width': 832, 'default_height': 480,
     'audio': False, 'sampler_node': '30', 'steps': 4, 'fps': 16,
     'note': 'Fast silent 480p drafts, four sampling steps.'},
    {'id': 'h3', 'name': 'MiniMax H3 Turbo', 'label': 'H3 Turbo · comparison · 4 steps',
     'durations': [2, 3, 4, 5, 8, 10, 15], 'sizes': [[1344, 768]], 'default_width': 1344, 'default_height': 768,
     'audio': True, 'sampler_node': '21', 'steps': 4, 'fps': 24,
     'note': 'Retained comparison model: four steps at native 768p.'},
]
MODEL_BY_ID = {model['id']: model for model in MODELS}

# The image and launcher use the same supported-model subset.
import os
_selected = tuple(filter(None, (value.strip() for value in os.environ.get('LEAM_VID_MODELS', ','.join(MODEL_BY_ID)).split(','))))
if not _selected or len(set(_selected)) != len(_selected) or any(value not in MODEL_BY_ID for value in _selected):
    raise ValueError('LEAM_VID_MODELS contains an unsupported or duplicate model.')
MODELS = [MODEL_BY_ID[value] for value in _selected]
MODEL_BY_ID = {model['id']: model for model in MODELS}
if DEFAULT_MODEL not in MODEL_BY_ID: DEFAULT_MODEL = MODELS[0]['id']
