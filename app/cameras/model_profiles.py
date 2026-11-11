"""Stable model identities and feature layouts shared by the UI and worker."""

DEFAULT_MODEL = 'mobilenetv3'
ANALYSIS_SOURCE = 'original-v1'
COLOR_MODES = ('rgb', 'grayscale')
IMAGENET_MEAN = (.485, .456, .406)
IMAGENET_STD = (.229, .224, .225)
MODELS = {
    'mobilenetv3': dict(label='MobileNetV3', encoder='mobilenetv3-small-stride16-v1', dimensions=48, stride=16),
    'mobileone-s0': dict(label='MobileOne-S0', encoder='mobileone-s0-stage2-v1', dimensions=128, stride=8),
    'mobilenetv4-convsmall-x0.5': dict(label='MobileNetV4 ConvSmall ×0.5',
                         encoder='mobilenetv4-convsmall-x05-stride16-v1', dimensions=48, stride=16,
                         timm_model='mobilenetv4_conv_small_050.e3000_r224_in1k', feature_module='blocks.2',
                         mean=(.5, .5, .5), std=(.5, .5, .5)),
}


def validate_model(value):
    if not isinstance(value, str) or value not in MODELS:
        raise ValueError('Select a supported analysis model')
    return value


def validate_color_mode(value):
    if not isinstance(value, str) or value not in COLOR_MODES:
        raise ValueError('Select color or grayscale analysis')
    return value


def heatmap_dimensions(width, height, model_key=DEFAULT_MODEL):
    stride = MODELS[validate_model(model_key)]['stride']
    return (width + stride - 1) // stride, (height + stride - 1) // stride
