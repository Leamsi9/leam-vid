"""Public native-image API graph builders; no state, I/O, or model execution.

The image's PrivateVideoModels selector validates before weights arrive; its
loader delegates to pinned core Comfy loaders after ready-queue preparation.
"""

def h3_graph(prompt, config):
    """Core ComfyUI graph with the matching official FL2VA Turbo 4-step settings."""

    def node(kind, **inputs):
        return {'class_type': kind, 'inputs': inputs}
    result = {'10': node('PrivateVideoModels', model='h3'), '11': node('ModelAttentionBackend', model=['10', 0], attention='comfy kitchen attention'), '12': node('PrivateH3Lora', model=['11', 0]), '16': node('MiniMaxH3ImageToVideo', clip=['10', 1], vae=['10', 2], prompt=prompt, width=config['width'], height=config['height'], length=config['frames']), '17': node('RandomNoise', noise_seed=config['seed']), '18': node('BasicGuider', model=['26', 0], conditioning=['16', 0]), '19': node('BasicScheduler', model=['26', 0], scheduler='simple', steps=4, denoise=1.0), '20': node('KSamplerSelect', sampler_name='euler'), '21': node('SamplerCustomAdvanced', noise=['17', 0], guider=['18', 0], sampler=['20', 0], sigmas=['19', 0], latent_image=['16', 1]), '22': node('VAEDecode', samples=['21', 0], vae=['10', 2]), '24': node('CreateVideo', images=['22', 0], fps=24.0, bit_depth=8, color_space='sRGB', codec='none'), '25': node('SaveVideo', video=['24', 0], filename_prefix='video/MiniMax_H3_Turbo', format='mp4', **{'format.codec': 'h264', 'format.codec.encoding': 'auto'}), '26': node('MiniMaxH3SigmaShift', model=['12', 0], shift_video=6.0, shift_audio=3.0)}
    if config['audio']:
        result['23'] = node('VAEDecodeAudio', samples=['21', 0], vae=['10', 3])
        result['24']['inputs']['audio'] = ['23', 0]
    return result


def native_fasth3_graph(prompt, config):
    """Native eight-step FastH3 with a stable pre-download model selector."""
    if config['model'] not in {'fasth3_v2', 'fasth3_trim'}:
        raise ValueError('Choose FastH3 V2 or Trim for this graph.')
    result = h3_graph(prompt, config)
    result['10']['inputs']['model'] = config['model']
    result.pop('12')
    result['19']['inputs']['steps'] = 8
    result['20']['inputs']['sampler_name'] = 'res_multistep'
    result['26']['inputs'].update(model=['11', 0], shift_video=10.0, shift_audio=3.0)
    result['25']['inputs']['filename_prefix'] = 'video/' + config['model']
    return result


def graph(prompt, config):
    """Return an API graph for the image's H3, FastH3 V2, or FastH3 Trim."""
    if config['model'] == 'h3':
        return h3_graph(prompt, config)
    return native_fasth3_graph(prompt, config)
