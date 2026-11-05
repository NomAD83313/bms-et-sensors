"""PatchCore-style patch memory with a small pretrained ResNet-18 backbone.

This experimental variant uses a bounded candidate pool, projected greedy
coreset, and an unweighted maximum nearest-patch distance. It does not claim
benchmark equivalence with the original WideResNet-50 PatchCore implementation.
"""
import io
import math
from pathlib import Path

import torch
from torch.nn import functional as F
from torchvision.models import resnet18, ResNet18_Weights
from torchvision.transforms import functional as TF
from PIL import Image


class PatchCoreLite:
    def __init__(self):
        torch.set_num_threads(1)
        self.network = resnet18(weights=ResNet18_Weights.DEFAULT).eval()
        # Channels-last convolutions avoid slower layout conversions on ARM CPUs.
        self.network.to(memory_format=torch.channels_last)
        generator = torch.Generator().manual_seed(42)
        self.projection = torch.randn(384, 128, generator=generator) / math.sqrt(128)
        self.memory = None
        self.threshold = None
        self.baseline_frames = 0

    @torch.inference_mode()
    def features(self, jpeg):
        image = Image.open(io.BytesIO(jpeg)).convert('RGB')
        image = TF.resize(image, [224, 224])
        x = TF.normalize(TF.to_tensor(image), [.485, .456, .406], [.229, .224, .225]).unsqueeze(0)
        net = self.network
        x = net.maxpool(net.relu(net.bn1(net.conv1(x))))
        x = net.layer1(x)
        first = net.layer2(x)
        second = net.layer3(first)
        first = F.avg_pool2d(first, 3, stride=1, padding=1)
        second = F.avg_pool2d(second, 3, stride=1, padding=1)
        second = F.interpolate(second, size=first.shape[-2:], mode='bilinear', align_corners=False)
        patches = torch.cat([first, second], dim=1).squeeze(0).permute(1, 2, 0).reshape(-1, 384)
        return patches @ self.projection

    @torch.inference_mode()
    def fit(self, samples):
        if len(samples) < 20:
            raise ValueError('At least 20 independent frames are required')
        # Hold out complete frames: calibration patches never enter the bank.
        training = [sample for i, sample in enumerate(samples) if i % 5]
        validation = [sample for i, sample in enumerate(samples) if not i % 5]
        patches = torch.cat(training)
        generator = torch.Generator().manual_seed(42)
        patches = patches[torch.randperm(len(patches), generator=generator)[:8000]]
        projection = torch.randn(128, 32, generator=generator) / math.sqrt(32)
        compact = patches @ projection
        distances = torch.full((len(patches),), float('inf'))
        selected = []
        index = 0
        for _ in range(min(1024, len(patches))):
            selected.append(index)
            distances = torch.minimum(distances, ((compact - compact[index]) ** 2).sum(dim=1))
            distances[index] = -1
            index = int(distances.argmax())
        self.memory = patches[selected].contiguous()
        scores = torch.tensor([self.score(sample)[0] for sample in validation])
        self.threshold = max(float(torch.quantile(scores, .99)), 1e-6)
        self.baseline_frames = len(samples)
        return scores.tolist()

    @torch.inference_mode()
    def score(self, patches):
        if self.memory is None:
            raise ValueError('Learn a normal view first')
        distances = torch.cdist(patches, self.memory).min(dim=1).values
        return float(distances.max()), distances.reshape(28, 28)

    def save(self, path):
        temporary = Path(str(path) + '.tmp')
        torch.save(dict(memory=self.memory, threshold=self.threshold,
                        baseline_frames=self.baseline_frames, version=1), temporary)
        temporary.replace(path)

    def load(self, path):
        model = torch.load(path, map_location='cpu', weights_only=True)
        if model.get('version') != 1 or model['memory'].shape[1] != 128:
            raise ValueError('Unsupported visual baseline')
        self.memory = model['memory']
        self.threshold = model['threshold']
        self.baseline_frames = model['baseline_frames']


def overlay(jpeg, distances, threshold):
    image = Image.open(io.BytesIO(jpeg)).convert('RGB')
    image.thumbnail((960, 960))
    heat = F.interpolate(((distances / max(threshold, 1e-6) - .8) / .7).clamp(0, 1)[None, None],
                         size=(image.height, image.width), mode='bilinear', align_corners=False)[0, 0]
    alpha = TF.to_pil_image((heat * .65).unsqueeze(0))
    red = Image.new('RGB', image.size, (255, 32, 0))
    result = Image.composite(red, image, alpha)
    output = io.BytesIO()
    result.save(output, format='JPEG', quality=85)
    return output.getvalue()
