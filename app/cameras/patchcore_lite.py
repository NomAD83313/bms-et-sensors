"""PatchCore-style patch memory with a small pretrained ResNet-18 backbone.

This experimental variant uses a bounded candidate pool, projected greedy
coreset, and an unweighted maximum nearest-patch distance. It does not claim
benchmark equivalence with the original WideResNet-50 PatchCore implementation.
"""
import io
import math
import os
from pathlib import Path

import torch
from torch.nn import functional as F
from torchvision.transforms import functional as TF
from PIL import Image

try:
    from .anomaly_control import validate_input_size, input_dimensions, DEFAULT_INPUT_SIZE
    from .feature_encoder import FeatureEncoder, ENCODER_ID
except ImportError:
    from anomaly_control import validate_input_size, input_dimensions, DEFAULT_INPUT_SIZE
    from feature_encoder import FeatureEncoder, ENCODER_ID


class PatchCoreLite:
    def __init__(self, input_size=DEFAULT_INPUT_SIZE, threads=None, feature_engine=None, distance_engine=None):
        requested_threads = os.environ.get('CAMERA_ANOMALY_THREADS', '1') if threads is None else threads
        self.inference_threads = int(requested_threads)
        if isinstance(requested_threads, bool) or self.inference_threads not in (1, 2, 3, 4) or float(requested_threads) != self.inference_threads:
            raise ValueError('Analysis CPU threads must be between 1 and 4')
        torch.set_num_threads(self.inference_threads)
        self.feature_engine = feature_engine or os.environ.get('CAMERA_ANOMALY_FEATURE_ENGINE', 'torch')
        self.distance_engine = distance_engine or os.environ.get('CAMERA_ANOMALY_DISTANCE_ENGINE', 'torch')
        if self.feature_engine not in ('torch', 'onnx') or self.distance_engine not in ('torch', 'faiss'):
            raise ValueError('Unsupported inference engine')
        self.network = (FeatureEncoder().eval().to(memory_format=torch.channels_last)
                        if self.feature_engine == 'torch' else None)
        self.onnx_provider = None
        self._session = None
        self._session_size = None
        self._index = None
        self._indexed_memory = None
        if self.feature_engine == 'onnx':
            import onnxruntime as ort
            ort.disable_telemetry_events()
            self._ort = ort
            requested = os.environ.get('CAMERA_ANOMALY_ONNX_PROVIDER', 'auto')
            available = ort.get_available_providers()
            if requested not in ('auto', 'cpu', 'xnnpack'):
                raise ValueError('Unsupported ONNX provider')
            self.onnx_provider = ('XnnpackExecutionProvider' if requested == 'xnnpack' or
                (requested == 'auto' and 'XnnpackExecutionProvider' in available) else 'CPUExecutionProvider')
            if self.onnx_provider not in available:
                raise ValueError('Requested ONNX provider is unavailable')
        if self.distance_engine == 'faiss':
            import faiss
            self._faiss = faiss
            faiss.omp_set_num_threads(self.inference_threads)
        self.set_input_size(input_size)

    def set_input_size(self, input_size):
        self.input_size = validate_input_size(input_size)
        self.memory = None
        self.threshold = None
        self.baseline_frames = 0
        self._session = None
        self._index = None
        self._indexed_memory = None

    @torch.inference_mode()
    def features(self, jpeg):
        image = Image.open(io.BytesIO(jpeg)).convert('RGB')
        width, height = input_dimensions(self.input_size)
        image = TF.resize(image, [height, width])
        x = TF.normalize(TF.to_tensor(image), [.485, .456, .406], [.229, .224, .225]).unsqueeze(0)
        if self.feature_engine == 'torch':
            return self.network(x)
        if self._session is None or self._session_size != self.input_size:
            directory = Path(os.environ.get('CAMERA_ANOMALY_MODEL_DIR', '/opt/bms-camera/encoders'))
            options = self._ort.SessionOptions()
            options.intra_op_num_threads = 1 if self.onnx_provider == 'XnnpackExecutionProvider' else self.inference_threads
            options.inter_op_num_threads = 1
            options.add_session_config_entry('session.intra_op.allow_spinning', '0')
            provider = ((self.onnx_provider, {'intra_op_num_threads': str(self.inference_threads)})
                        if self.onnx_provider == 'XnnpackExecutionProvider' else self.onnx_provider)
            self._session = self._ort.InferenceSession(
                str(directory / f'{ENCODER_ID}-{self.input_size}.onnx'), options, providers=[provider])
            if self.onnx_provider not in self._session.get_providers():
                self._session = None
                raise RuntimeError('Requested ONNX provider did not initialize')
            self._session_size = self.input_size
        return torch.from_numpy(self._session.run(None, {'image': x.numpy()})[0])

    @torch.inference_mode()
    def fit(self, samples):
        if len(samples) < 20:
            raise ValueError('At least 20 independent frames are required')
        # Hold out complete frames: calibration patches never enter the bank.
        training = [sample for i, sample in enumerate(samples) if i % 5]
        validation = [sample for i, sample in enumerate(samples) if not i % 5]
        generator = torch.Generator().manual_seed(42)
        # Select the same global sample without concatenating full-resolution frames.
        indices = torch.randperm(sum(len(frame) for frame in training), generator=generator)[:8000]
        patches = torch.empty((len(indices), training[0].shape[1]), dtype=training[0].dtype)
        offset = 0
        for frame in training:
            selected = (indices >= offset) & (indices < offset + len(frame))
            patches[selected] = frame[indices[selected] - offset]
            offset += len(frame)
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
        if self.distance_engine == 'faiss':
            import numpy as np
            if self._index is None or self._indexed_memory is not self.memory:
                self._index = self._faiss.IndexFlatL2(128)
                self._index.add(self.memory.contiguous().numpy())
                self._indexed_memory = self.memory
            squared, _ = self._index.search(patches.contiguous().numpy(), 1)
            # FAISS returns squared L2; keep the existing Euclidean score scale.
            distances = torch.from_numpy(np.sqrt(np.maximum(squared[:, 0], 0)))
        else:
            distances = torch.cat([torch.cdist(part, self.memory).min(dim=1).values
                                   for part in patches.split(1024)])
        width, height = input_dimensions(self.input_size)
        return float(distances.max()), distances.reshape(height // 8, width // 8)

    def save(self, path):
        temporary = Path(str(path) + '.tmp')
        torch.save(dict(memory=self.memory, threshold=self.threshold,
                        baseline_frames=self.baseline_frames, input_size=self.input_size,
                        encoder=ENCODER_ID, version=3), temporary)
        temporary.replace(path)

    def load(self, path):
        model = torch.load(path, map_location='cpu', weights_only=True)
        if (model.get('version') != 3 or model.get('encoder') != ENCODER_ID
                or model['memory'].ndim != 2 or model['memory'].shape[1] != 128):
            raise ValueError('Unsupported visual baseline')
        if model.get('input_size') != self.input_size:
            raise ValueError('Baseline input size changed; learn a normal view again')
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
