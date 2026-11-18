"""Build fixed full-frame encoders and dynamic encoders for aspect-preserving ROI."""
import sys
from pathlib import Path
import torch

try:
    from .feature_encoder import FeatureEncoder
    from .model_profiles import INPUT_SIZES, MODELS, input_dimensions
except ImportError:
    from feature_encoder import FeatureEncoder
    from model_profiles import INPUT_SIZES, MODELS, input_dimensions


def export_models(directory, model_keys=None):
    if model_keys is not None and set(model_keys) - MODELS.keys():
        raise ValueError('Select supported encoder profiles for export')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(1)
    with torch.inference_mode():
        for model_key, profile in MODELS.items():
            if model_keys is not None and model_key not in model_keys:
                continue
            network = FeatureEncoder(model_key).eval()
            image = torch.zeros(1, 3, 256, 320)
            torch.onnx.export(network, image, directory / f'{profile["encoder"]}-dynamic.onnx',
                              input_names=['image'], output_names=['patches'],
                              dynamic_axes={'image': {2: 'height', 3: 'width'}, 'patches': {0: 'patch_count'}},
                              opset_version=18, dynamo=False)
            for size in INPUT_SIZES:
                width, height = input_dimensions(size)
                image = torch.zeros(1, 3, height, width)
                torch.onnx.export(network, image, directory / f'{MODELS[model_key]["encoder"]}-{size}.onnx',
                                  input_names=['image'], output_names=['patches'],
                                  opset_version=18, dynamo=False)


if __name__ == '__main__':
    export_models(sys.argv[1], sys.argv[2:] or None)
