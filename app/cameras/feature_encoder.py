"""Single-layer ResNet encoder shared by PyTorch and exported ONNX models."""
import torch
from torch.nn import functional as F
from torchvision.models import resnet18, ResNet18_Weights

ENCODER_ID = 'resnet18-layer2-v1'


class FeatureEncoder(torch.nn.Module):
    def __init__(self):
        super().__init__()
        network = resnet18(weights=ResNet18_Weights.DEFAULT).eval()
        self.layers = torch.nn.Sequential(network.conv1, network.bn1, network.relu,
                                         network.maxpool, network.layer1, network.layer2)

    def forward(self, image):
        features = F.avg_pool2d(self.layers(image), 3, stride=1, padding=1)
        return features.squeeze(0).permute(1, 2, 0).reshape(-1, 128)
