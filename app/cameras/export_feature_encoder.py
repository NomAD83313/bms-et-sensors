"""Build fixed-shape ONNX encoders so XNNPACK can prepare convolution weights."""
import sys
from pathlib import Path
import torch

try:
    from .anomaly_control import INPUT_SIZES, input_dimensions
    from .feature_encoder import FeatureEncoder, ENCODER_ID
except ImportError:
    from anomaly_control import INPUT_SIZES, input_dimensions
    from feature_encoder import FeatureEncoder, ENCODER_ID


def export_models(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(1)
    network = FeatureEncoder().eval()
    with torch.inference_mode():
        for size in INPUT_SIZES:
            width, height = input_dimensions(size)
            image = torch.zeros(1, 3, height, width)
            torch.onnx.export(network, image, directory / f'{ENCODER_ID}-{size}.onnx',
                              input_names=['image'], output_names=['patches'],
                              opset_version=18, dynamo=False)


if __name__ == '__main__':
    export_models(sys.argv[1])
