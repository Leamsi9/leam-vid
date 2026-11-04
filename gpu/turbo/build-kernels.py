"""Build official, unmodified TurboDiffusion CUDA source for RTX 5090.
Only architecture selection and NVCC concurrency differ from upstream setup.py.
The Python inference source is installed independently, without upstream's optional
flash-attn dependencies or any alternate Torch runtime.
"""
from pathlib import Path
import site
from setuptools import setup
from torch.utils.cpp_extension import BuildExtension, CUDAExtension
import ast

upstream = ast.parse(Path("setup.py").read_text())
flags = next(ast.literal_eval(node.value) for node in upstream.body
             if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "nvcc_flags" for t in node.targets))
ops = Path.cwd() / "turbodiffusion" / "ops"
setup(
    name="turbo-diffusion-kernels",
    version="1.0.0+torch2.9.1cu130sm120a",
    description="Official TurboDiffusion e3d61360 kernels for Torch 2.9.1 CUDA 13 RTX 5090",
    license="Apache-2.0",
    license_files=["LICENSE", "turbodiffusion/ops/cutlass/LICENSE.txt"],
    py_modules=[],
    packages=[],
    install_requires=["torch==2.9.1"],
    ext_modules=[CUDAExtension(
        name="turbo_diffusion_ops",
        sources=[str(ops / name) for name in ["bindings.cpp", "quant/quant.cu", "norm/rmsnorm.cu", "norm/layernorm.cu", "gemm/gemm.cu"]],
        extra_compile_args={"cxx": ["-O3", "-std=c++17"], "nvcc": flags + ["-DEXECMODE=0", "-gencode", "arch=compute_120a,code=sm_120a", "--threads", "2"]},
        include_dirs=[ops / "cutlass/include", ops / "cutlass/tools/util/include", ops] + [path for root in site.getsitepackages() for path in Path(root).glob("nvidia/*/include")],
        library_dirs=["/usr/local/cuda/lib64/stubs"],
        libraries=["cuda"],
    )],
    cmdclass={"build_ext": BuildExtension},
)
