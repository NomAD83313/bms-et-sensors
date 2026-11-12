"""Pretrained patch encoders shared by PyTorch and exported ONNX models."""
import torch
from torch.nn import functional as F
from torchvision.models import mobilenet_v3_small, MobileNet_V3_Small_Weights

try:
    from .model_profiles import DEFAULT_MODEL, MODELS, validate_model
except ImportError:
    from model_profiles import DEFAULT_MODEL, MODELS, validate_model

class FeatureEncoder(torch.nn.Module):
    def __init__(self, model_key=DEFAULT_MODEL):
        super().__init__()
        profile = MODELS[validate_model(model_key)]
        self.dimensions = profile['dimensions']
        if model_key == 'mobilenetv3':
            self.layers = mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.DEFAULT).features[:9]
        elif 'timm_model' in profile:
            import timm
            network = timm.create_model(profile['timm_model'], pretrained=True)
            feature = next(item for item in network.feature_info if item['module'] == profile['feature_module'])
            if (feature['num_chs'] != self.dimensions or feature['reduction'] != profile['stride']
                    or tuple(network.pretrained_cfg['mean']) != profile['mean']
                    or tuple(network.pretrained_cfg['std']) != profile['std']):
                raise ValueError('Pretrained encoder layout or normalization changed')
            stage = int(profile['feature_module'].removeprefix('blocks.'))
            # Remove later stages; requesting a feature alone does not truncate timm.
            self.layers = torch.nn.Sequential(network.conv_stem, network.bn1, network.blocks[:stage + 1])
        else:
            raise ValueError('No encoder is defined for the selected model')

    def forward(self, image):
        features = F.avg_pool2d(self.layers(image), 3, stride=1, padding=1)
        return features.squeeze(0).permute(1, 2, 0).reshape(-1, self.dimensions)
