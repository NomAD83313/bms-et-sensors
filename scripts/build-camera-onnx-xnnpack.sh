#!/usr/bin/env bash
# Build the optional runtime wheel inside Dockerfile.camera-onnx-xnnpack.
set -euo pipefail
build_root="${1:-/work}"
mkdir -p "$build_root"
build_root="$(realpath "$build_root")"
cd "$build_root"
# Use the matching upstream compiler instead of building unused language generators.
case "$(uname -m)" in
  aarch64) protoc_platform=aarch_64; protoc_sha256=4016cd2ad24c6a2405332a2ef7e04b719240e40d811587c6a1c4bbe514cd668c ;;
  x86_64) protoc_platform=x86_64; protoc_sha256=9c49962391ff8b5754342509efc3885db91dfe11a358e1e98c9a71518f0ad199 ;;
  *) echo 'Unsupported native build architecture' >&2; exit 1 ;;
esac
if [[ ! -x protoc/bin/protoc ]]; then
  curl -fLsS "https://github.com/protocolbuffers/protobuf/releases/download/v33.6/protoc-33.6-linux-${protoc_platform}.zip" -o protoc.zip
  printf '%s  %s\n' "$protoc_sha256" protoc.zip | sha256sum -c -
  unzip -q protoc.zip -d protoc
fi
if [[ ! -d onnxruntime/.git ]]; then
  git clone --depth 1 --branch v1.31.1 --recursive https://github.com/microsoft/onnxruntime.git
fi
# Limit registration to inference, calibration and quantization operators used here.
cat > operators.config <<'EOF'
ai.onnx;18;Add,AveragePool,BatchNormalization,Cast,Ceil,Clip,Concat,Constant,ConstantOfShape,Conv,ConvInteger,DequantizeLinear,Div,DynamicQuantizeLinear,Equal,Expand,Flatten,Gather,Gemm,GlobalAveragePool,HardSigmoid,HardSwish,Identity,MatMul,MatMulInteger,Max,MaxPool,Min,Mul,Neg,Pad,Pow,QLinearConv,QLinearMatMul,QuantizeLinear,Range,Reciprocal,ReduceMax,ReduceMean,ReduceMin,ReduceSum,Relu,Reshape,Resize,Round,Shape,Sigmoid,Size,Slice,Split,Sqrt,Squeeze,Sub,Transpose,Unsqueeze,Where
com.microsoft;1;DynamicQuantizeMatMul,FusedConv,FusedGemm,MatMulIntegerToFloat,QGemm,QLinearAdd,QLinearAveragePool,QLinearGlobalAveragePool,QLinearMul,QLinearSigmoid,QLinearSoftmax
com.microsoft.nchwc;1;AveragePool,Conv,MaxPool,ReorderInput,ReorderOutput
EOF
cd onnxruntime
# ORT 1.31.1 unconditionally links KleidiAI in its XNNPACK CMake setup.
# Honor --no_kleidiai for the Pi 5 build (GCC 12 fails on a BF16 kernel).
python - <<'PY'
from pathlib import Path
path = Path('cmake/external/xnnpack.cmake')
original = 'if(ORT_TARGET_PROCESSOR MATCHES "^arm64.*" AND NOT CMAKE_C_COMPILER_ID STREQUAL "MSVC")'
replacement = original.replace('if(', 'if(onnxruntime_USE_KLEIDIAI AND ', 1)
source = path.read_text()
if source.count(original) == 2:
    path.write_text(source.replace(original, replacement))
elif source.count(replacement) != 2:
    raise RuntimeError('Unexpected XNNPACK CMake layout')
PY
./build.sh --config Release --use_xnnpack --build_shared_lib --build_wheel \
  --path_to_protoc_exe "$build_root/protoc/bin/protoc" \
  --skip_tests --parallel "${ONNX_BUILD_JOBS:-2}" --compile_no_warning_as_error \
  --disable_ml_ops --disable_generation_ops --no_telemetry --no_kleidiai --allow_running_as_root \
  --include_ops_by_config "$build_root/operators.config" \
  --cmake_extra_defines onnxruntime_USE_KLEIDIAI=OFF XNNPACK_ENABLE_KLEIDIAI=OFF \
  onnxruntime_BUILD_UNIT_TESTS=OFF
mkdir -p "$build_root/wheels"
cp build/Linux/Release/dist/*.whl "$build_root/wheels/"
