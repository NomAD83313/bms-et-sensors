"""PatchCore-style patch memory with a small pretrained ResNet-18 backbone.

This experimental variant uses a bounded candidate pool, projected greedy
coreset, and an unweighted maximum nearest-patch distance. It does not claim
benchmark equivalence with the original WideResNet-50 PatchCore implementation.
"""
import io
import math
import os
from pathlib import Path
import time

import torch
from torch.nn import functional as F
from torchvision.transforms import functional as TF
from PIL import Image

try:
    from .anomaly_control import validate_input_size, input_dimensions, DEFAULT_INPUT_SIZE
    from .feature_encoder import FeatureEncoder
    from .model_profiles import DEFAULT_MODEL, MODELS, validate_model, heatmap_dimensions, ANALYSIS_SOURCE
    from .roi import validate_roi, roi_bounds, roi_input_dimensions, ROI_PREPROCESSING, ROIGeometryChanged
except ImportError:
    from anomaly_control import validate_input_size, input_dimensions, DEFAULT_INPUT_SIZE
    from feature_encoder import FeatureEncoder
    from model_profiles import DEFAULT_MODEL, MODELS, validate_model, heatmap_dimensions, ANALYSIS_SOURCE
    from roi import validate_roi, roi_bounds, roi_input_dimensions, ROI_PREPROCESSING, ROIGeometryChanged


class PatchCoreLite:
    def __init__(self, input_size=DEFAULT_INPUT_SIZE, threads=None, feature_engine=None,
                 distance_engine=None, model_key=DEFAULT_MODEL, jpeg_draft=None, top_k=None,
                 coreset_size=None):
        requested_threads = os.environ.get('CAMERA_ANOMALY_THREADS', '1') if threads is None else threads
        self.inference_threads = int(requested_threads)
        if isinstance(requested_threads, bool) or self.inference_threads not in (1, 2, 3, 4) or float(requested_threads) != self.inference_threads:
            raise ValueError('Analysis CPU threads must be between 1 and 4')
        torch.set_num_threads(self.inference_threads)
        self.feature_engine = feature_engine or os.environ.get('CAMERA_ANOMALY_FEATURE_ENGINE', 'torch')
        self.distance_engine = distance_engine or os.environ.get('CAMERA_ANOMALY_DISTANCE_ENGINE', 'torch')
        if self.feature_engine not in ('torch', 'onnx') or self.distance_engine not in ('torch', 'faiss'):
            raise ValueError('Unsupported inference engine')
        requested_draft = os.environ.get('CAMERA_ANOMALY_JPEG_DRAFT', '0') if jpeg_draft is None else jpeg_draft
        if isinstance(requested_draft, bool):
            self.jpeg_draft = requested_draft
        elif str(requested_draft) in ('0', '1'):
            self.jpeg_draft = str(requested_draft) == '1'
        else:
            raise ValueError('JPEG draft must be 0 or 1')
        requested_top_k = os.environ.get('CAMERA_ANOMALY_TOP_K', '1') if top_k is None else top_k
        if isinstance(requested_top_k, bool):
            raise ValueError('Top-K score aggregation must be an integer between 1 and 20')
        try:
            self.top_k = int(requested_top_k)
            if self.top_k < 1 or self.top_k > 20 or float(requested_top_k) != self.top_k:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('Top-K score aggregation must be an integer between 1 and 20')
        requested_coreset = os.environ.get('CAMERA_ANOMALY_CORESET_SIZE', '1024') if coreset_size is None else coreset_size
        if isinstance(requested_coreset, bool):
            raise ValueError('Coreset size must be an integer between 256 and 4096')
        try:
            self.coreset_size = int(requested_coreset)
            if (self.coreset_size < 256 or self.coreset_size > 4096
                    or float(requested_coreset) != self.coreset_size):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('Coreset size must be an integer between 256 and 4096')
        self._requested_feature_engine = self.feature_engine
        self.network = None
        self.onnx_provider = None
        self._session = None
        self._session_size = None
        self._index = None
        self._indexed_memory = None
        self.roi = None
        self.preparation_metrics = {}
        if self.distance_engine == 'faiss':
            import faiss
            self._faiss = faiss
            faiss.omp_set_num_threads(self.inference_threads)
        self.set_input_size(input_size)
        self.set_model(model_key)

    def set_model(self, model_key):
        model_key = validate_model(model_key)
        if getattr(self, 'model_key', None) == model_key:
            return
        feature_engine = self._requested_feature_engine
        if feature_engine == 'onnx':
            import onnxruntime as ort
            ort.disable_telemetry_events()
            self._ort = ort
            requested = os.environ.get('CAMERA_ANOMALY_ONNX_PROVIDER', 'auto')
            if requested not in ('auto', 'cpu', 'xnnpack'):
                raise ValueError('Unsupported ONNX provider')
            self.onnx_provider = ('XnnpackExecutionProvider' if requested == 'xnnpack' or
                (requested == 'auto' and 'XnnpackExecutionProvider' in ort.get_available_providers()) else 'CPUExecutionProvider')
            if self.onnx_provider not in ort.get_available_providers():
                raise ValueError('Requested ONNX provider is unavailable')
        profile = MODELS[model_key]
        self.feature_engine = feature_engine
        if feature_engine == 'torch':
            self.onnx_provider = None
        self.model_key, self.encoder_id = model_key, profile['encoder']
        self.feature_dimensions = profile['dimensions']
        self.network = (FeatureEncoder(model_key).eval().to(memory_format=torch.channels_last)
                        if self.feature_engine == 'torch' else None)
        self.set_input_size(self.input_size)

    def set_input_size(self, input_size):
        self.input_size = validate_input_size(input_size)
        self.learning_revision = None
        self.memory = None
        self.threshold = None
        self.baseline_frames = 0
        self._session = None
        self._session_size = None
        self._index = None
        self._indexed_memory = None
        self.source_size = None
        self.effective_input_size = input_dimensions(self.input_size) if self.roi is None else None
        self._roi_geometry = None

    def set_roi(self, roi):
        roi = validate_roi(roi)
        if roi == self.roi:
            return
        self.roi = roi
        self.set_input_size(self.input_size)

    def inspect_geometry(self, jpeg):
        """Inspect a JPEG header and reject a mixed-geometry ROI learning bank."""
        with Image.open(io.BytesIO(jpeg)) as image:
            source_size = image.size
        try:
            dimensions = roi_input_dimensions(self.input_size, *source_size, self.roi)
        except ValueError as error:
            if self.roi is not None and self._roi_geometry is not None:
                self.set_input_size(self.input_size)
                self.source_size = source_size
                raise ROIGeometryChanged('ROI source geometry changed; learn the normal view again') from error
            raise
        bounds = roi_bounds(*source_size, self.roi)
        geometry = (source_size, dimensions, bounds)
        if self.roi is not None and self._roi_geometry is not None and geometry != self._roi_geometry:
            self.set_input_size(self.input_size)
            self.source_size, self.effective_input_size = source_size, dimensions
            self._roi_geometry = geometry
            raise ROIGeometryChanged('ROI source geometry changed; learn the normal view again')
        self.source_size, self.effective_input_size = source_size, dimensions
        if self.roi is not None:
            self._roi_geometry = geometry
        return bounds

    def _image(self, jpeg):
        total_started = time.perf_counter()
        geometry_started = total_started
        bounds = self.inspect_geometry(jpeg)
        geometry_ms = (time.perf_counter() - geometry_started) * 1000
        decode_started = time.perf_counter()
        image = Image.open(io.BytesIO(jpeg))
        source_width, source_height = image.size
        if self.jpeg_draft:
            width, height = self.effective_input_size
            if self.roi is None:
                draft_size = width, height
            else:
                draft_size = (math.ceil(width / self.roi['width']),
                              math.ceil(height / self.roi['height']))
            image.draft('RGB', draft_size)
        decoded_width, decoded_height = image.size
        image = image.convert('RGB')
        decode_ms = (time.perf_counter() - decode_started) * 1000
        transform_started = time.perf_counter()
        if self.roi is not None:
            scaled = (round(bounds[0] * decoded_width / source_width),
                      round(bounds[1] * decoded_height / source_height),
                      round(bounds[2] * decoded_width / source_width),
                      round(bounds[3] * decoded_height / source_height))
            image = image.crop(scaled)
        width, height = self.effective_input_size
        image = TF.resize(image, [height, width])
        transform_ms = (time.perf_counter() - transform_started) * 1000
        tensor_started = time.perf_counter()
        profile = MODELS[self.model_key]
        tensor = TF.normalize(TF.to_tensor(image), profile.get('mean', (.485, .456, .406)),
                              profile.get('std', (.229, .224, .225))).unsqueeze(0)
        tensor_ms = (time.perf_counter() - tensor_started) * 1000
        self.preparation_metrics = dict(
            geometry_ms=geometry_ms, decode_ms=decode_ms, transform_ms=transform_ms,
            tensor_ms=tensor_ms, total_ms=(time.perf_counter() - total_started) * 1000,
            decoded_width=decoded_width, decoded_height=decoded_height)
        return tensor

    @torch.inference_mode()
    def features(self, jpeg):
        x = self._image(jpeg)
        if self.network is not None:
            return self.network(x)
        session_size = self.effective_input_size if self.roi is not None else self.input_size
        if self._session is None or self._session_size != session_size:
            directory = Path(os.environ.get('CAMERA_ANOMALY_MODEL_DIR', '/opt/bms-camera/encoders'))
            options = self._ort.SessionOptions()
            options.intra_op_num_threads = 1 if self.onnx_provider == 'XnnpackExecutionProvider' else self.inference_threads
            options.inter_op_num_threads = 1
            options.add_session_config_entry('session.intra_op.allow_spinning', '0')
            if self.roi is not None:
                width, height = self.effective_input_size
                options.add_free_dimension_override_by_name('height', height)
                options.add_free_dimension_override_by_name('width', width)
            provider = ((self.onnx_provider, {'intra_op_num_threads': str(self.inference_threads)})
                        if self.onnx_provider == 'XnnpackExecutionProvider' else self.onnx_provider)
            suffix = 'dynamic' if self.roi is not None else self.input_size
            path = directory / f'{self.encoder_id}-{suffix}.onnx'
            self._session = self._ort.InferenceSession(str(path), options, providers=[provider])
            if self.onnx_provider not in self._session.get_providers():
                self._session = None
                raise RuntimeError('Requested ONNX provider did not initialize')
            self._session_size = session_size
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
        self._fit_candidates(patches, generator)
        scores = torch.tensor([self.score(sample)[0] for sample in validation])
        self.threshold = max(float(torch.quantile(scores, .99)), 1e-6)
        self.baseline_frames = len(samples)
        return scores.tolist()

    @torch.inference_mode()
    def fit_sessions(self, groups, progress=None):
        """Stream saved JPEGs with equal candidate budgets and held-out frames per session."""
        if not 1 <= len(groups) <= 8 or any(not 20 <= len(group) <= 120 for group in groups):
            raise ValueError('Learn one to eight sessions with 20 to 120 frames each')
        generator = torch.Generator().manual_seed(42)
        candidates = []
        completed, total = 0, sum(map(len, groups))
        for group_index, paths in enumerate(groups):
            training = [path for index, path in enumerate(paths) if index % 5]
            budget = 8000 // len(groups) + int(group_index < 8000 % len(groups))
            for index, path in enumerate(training):
                features = self.features(path.read_bytes())
                count = min(len(features), budget // len(training) + int(index < budget % len(training)))
                candidates.append(features[torch.randperm(len(features), generator=generator)[:count]])
                completed += 1
                if progress:
                    progress(completed, total)
        self._fit_candidates(torch.cat(candidates), generator)
        del candidates
        calibration = []
        for paths in groups:
            held_out = []
            for index, path in enumerate(paths):
                if index % 5:
                    continue
                held_out.append(self.score(self.features(path.read_bytes()))[0])
                completed += 1
                if progress:
                    progress(completed, total)
            scores = torch.tensor(held_out)
            calibration.append(dict(calibration_frames=len(scores),
                                    calibration_threshold=max(float(torch.quantile(scores, .99)), 1e-6)))
        self.threshold = max(item['calibration_threshold'] for item in calibration)
        self.baseline_frames = sum(map(len, groups))
        return calibration

    def _fit_candidates(self, patches, generator):
        projection = torch.randn(self.feature_dimensions, 32, generator=generator) / math.sqrt(32)
        compact = patches @ projection
        distances = torch.full((len(patches),), float('inf'))
        selected = []
        index = 0
        for _ in range(min(self.coreset_size, len(patches))):
            selected.append(index)
            distances = torch.minimum(distances, ((compact - compact[index]) ** 2).sum(dim=1))
            distances[index] = -1
            index = int(distances.argmax())
        self.memory = patches[selected].contiguous()

    @torch.inference_mode()
    def score(self, patches):
        if self.memory is None:
            raise ValueError('Learn a normal view first')
        if self.distance_engine == 'faiss':
            import numpy as np
            if self._index is None or self._indexed_memory is not self.memory:
                self._index = self._faiss.IndexFlatL2(self.feature_dimensions)
                self._index.add(self.memory.contiguous().numpy())
                self._indexed_memory = self.memory
            squared, _ = self._index.search(patches.contiguous().numpy(), 1)
            # FAISS returns squared L2; keep the existing Euclidean score scale.
            distances = torch.from_numpy(np.sqrt(np.maximum(squared[:, 0], 0)))
        else:
            distances = torch.cat([torch.cdist(part, self.memory).min(dim=1).values
                                   for part in patches.split(1024)])
        width, height = self.effective_input_size or input_dimensions(self.input_size)
        heat_width, heat_height = heatmap_dimensions(width, height, self.model_key)
        count = min(self.top_k, len(distances))
        score = float(torch.topk(distances, count).values.mean())
        return score, distances.reshape(heat_height, heat_width)

    def save(self, path):
        temporary = Path(str(path) + '.tmp')
        if self.source_size is None or self.effective_input_size is None:
            raise ValueError('Source geometry is required before saving a baseline')
        torch.save(dict(version=6, analysis_source=ANALYSIS_SOURCE,
                        learning_revision=self.learning_revision,
                        encoder=self.encoder_id, input_size=self.input_size,
                        roi=self.roi, roi_preprocessing=ROI_PREPROCESSING,
                        source_size=self.source_size, effective_input_size=self.effective_input_size,
                        jpeg_draft=self.jpeg_draft, score_top_k=self.top_k,
                        coreset_size=len(self.memory), memory=self.memory,
                        threshold=self.threshold, baseline_frames=self.baseline_frames), temporary)
        temporary.replace(path)

    def load(self, path):
        model = torch.load(path, map_location='cpu', weights_only=True)
        required = {'version', 'analysis_source', 'encoder', 'input_size', 'roi',
                    'roi_preprocessing', 'source_size', 'effective_input_size',
                    'jpeg_draft', 'score_top_k', 'coreset_size', 'memory',
                    'threshold', 'baseline_frames'}
        if (not isinstance(model, dict) or not required.issubset(model)
                or model['version'] != 6 or model['analysis_source'] != ANALYSIS_SOURCE
                or model.get('encoder') != self.encoder_id
                or model['memory'].ndim != 2 or model['memory'].shape[1] != self.feature_dimensions):
            raise ValueError('Unsupported visual baseline')
        if model.get('input_size') != self.input_size:
            raise ValueError('Baseline input size changed; learn a normal view again')
        saved_draft = model['jpeg_draft']
        if not isinstance(saved_draft, bool) or saved_draft != self.jpeg_draft:
            raise ValueError('JPEG preprocessing changed; learn the normal view again')
        saved_top_k = model['score_top_k']
        if not isinstance(saved_top_k, int) or isinstance(saved_top_k, bool) or saved_top_k != self.top_k:
            raise ValueError('Score aggregation changed; learn the normal view again')
        if (model['roi_preprocessing'] != ROI_PREPROCESSING
                or validate_roi(model['roi']) != self.roi):
            raise ValueError('Baseline ROI changed; learn a normal view again')
        source_size, dimensions = model['source_size'], model['effective_input_size']
        if (not isinstance(source_size, (list, tuple)) or len(source_size) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in source_size)
                or not isinstance(dimensions, (list, tuple)) or len(dimensions) != 2):
            raise ValueError('Invalid baseline geometry')
        expected = roi_input_dimensions(self.input_size, *source_size, self.roi)
        if tuple(dimensions) != expected:
            raise ValueError('Invalid model-input geometry')
        geometry = (tuple(source_size), expected, roi_bounds(*source_size, self.roi))
        if self._roi_geometry is not None and geometry != self._roi_geometry:
            raise ValueError('Baseline source geometry changed; learn the normal view again')
        if (isinstance(model['coreset_size'], bool) or not isinstance(model['coreset_size'], int)
                or model['coreset_size'] != len(model['memory'])):
            raise ValueError('Invalid baseline coreset')
        self.memory = model['memory']
        self.learning_revision = model.get('learning_revision')
        self.threshold = model['threshold']
        self.baseline_frames = model['baseline_frames']
        self.source_size, self.effective_input_size, _ = geometry
        self._roi_geometry = geometry


def overlay(jpeg, distances, threshold, roi=None):
    image = Image.open(io.BytesIO(jpeg)).convert('RGB')
    roi = validate_roi(roi)
    bounds = roi_bounds(image.width, image.height, roi)
    region = image.crop(bounds) if roi is not None else image
    heat = F.interpolate(((distances / max(threshold, 1e-6) - .8) / .7).clamp(0, 1)[None, None],
                         size=(region.height, region.width), mode='bilinear', align_corners=False)[0, 0]
    alpha = TF.to_pil_image((heat * .65).unsqueeze(0))
    red = Image.new('RGB', region.size, (255, 32, 0))
    result = Image.composite(red, region, alpha)
    if roi is not None:
        image.paste(result, bounds)
        result = image
    output = io.BytesIO()
    result.save(output, format='JPEG', quality=85)
    return output.getvalue()
