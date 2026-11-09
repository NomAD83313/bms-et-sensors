"""Fused MobileOne-S0 stages 0–2, using Apple's published pretrained weights.

Architecture/checkpoint: https://github.com/apple-aiml-research/ml-mobileone
The checkpoint license is retained in licenses/mobileone-LICENSE.txt.
"""
import torch

WEIGHTS_URL = 'https://docs-assets.developer.apple.com/ml-research/datasets/mobileone/mobileone_s0.pth.tar'
WEIGHTS_SHA256 = '74080c64f108a15d9c73129f5ae8f8315981c8dc31e111e74b3ae36c7aea2033'


class FusedBlock(torch.nn.Module):
    def __init__(self, inputs, outputs, kernel, stride=1, groups=1):
        super().__init__()
        self.reparam_conv = torch.nn.Conv2d(inputs, outputs, kernel, stride=stride,
                                            padding=kernel // 2, groups=groups, bias=True)

    def forward(self, image):
        return torch.relu(self.reparam_conv(image))


def mobileone_layers():
    """Stop before stage3/4 and classification; stage2 is 128 channels at stride8."""
    network = torch.nn.Module()
    network.stage0 = FusedBlock(3, 48, 3, stride=2)
    inputs = 48
    for name, outputs, count in (('stage1', 48, 2), ('stage2', 128, 8)):
        blocks = []
        for index in range(count):
            blocks += [FusedBlock(inputs, inputs, 3, stride=2 if index == 0 else 1, groups=inputs),
                       FusedBlock(inputs, outputs, 1)]
            inputs = outputs
        setattr(network, name, torch.nn.Sequential(*blocks))
    weights = torch.hub.load_state_dict_from_url(
        WEIGHTS_URL, file_name='mobileone_s0-' + WEIGHTS_SHA256[:8] + '.pth',
        map_location='cpu', check_hash=True, weights_only=True)
    selected = {name: value for name, value in weights.items()
                if name.startswith(('stage0.', 'stage1.', 'stage2.'))}
    network.load_state_dict(selected, strict=True)
    return torch.nn.Sequential(network.stage0, network.stage1, network.stage2)
