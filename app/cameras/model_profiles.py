"""Stable model identities, input sizes and feature layouts shared by the UI and worker."""

INPUT_SIZES = ('768x432', '1024x576', '1280x720', '1536x864', '1920x1080')
DEFAULT_INPUT_SIZE = INPUT_SIZES[0]
DEFAULT_MODEL = 'mobilenetv3'
ANALYSIS_SOURCE = 'original-v1'
COLOR_MODES = ('rgb', 'grayscale')
IMAGENET_MEAN = (.485, .456, .406)
IMAGENET_STD = (.229, .224, .225)
MODELS = {
    'mobilenetv3': dict(label='MobileNetV3', encoder='mobilenetv3-small-stride16-v1', dimensions=48, stride=16),
    'mobilenetv4-convsmall-x0.5': dict(label='MobileNetV4 ConvSmall ×0.5',
                         encoder='mobilenetv4-convsmall-x05-stride16-v1', dimensions=48, stride=16,
                         timm_model='mobilenetv4_conv_small_050.e3000_r224_in1k', feature_module='blocks.2',
                         mean=(.5, .5, .5), std=(.5, .5, .5)),
}


def validate_input_size(value):
    if not isinstance(value, str) or value not in INPUT_SIZES:
        raise ValueError('Select a supported 16:9 model input')
    return value


def input_dimensions(value):
    return tuple(map(int, validate_input_size(value).split('x')))


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


def geometry_fields(width, height, model_key=DEFAULT_MODEL):
    """Status fields describing the model input and its heatmap for a given input size."""
    heat_width, heat_height = heatmap_dimensions(width, height, model_key)
    return dict(effective_input_width=width, effective_input_height=height,
                heatmap_width=heat_width, heatmap_height=heat_height)
