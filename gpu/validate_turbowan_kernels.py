"""No CUDA launches: check ABI loading and the official pybind schemas only."""
import importlib.metadata
import json
import re
import torch
import turbo_diffusion_ops as kernels

assert torch.__version__.split("+")[0] == "2.9.1", torch.__version__
assert torch.version.cuda == "13.0", torch.version.cuda
expected = {"quant_cuda": 3, "gemm_cuda": 5, "rms_norm_cuda": 4, "layer_norm_cuda": 5}
result = {"torch": torch.__version__, "cuda": torch.version.cuda,
          "cxx11_abi": torch._C._GLIBCXX_USE_CXX11_ABI,
          "gpu_visible": torch.cuda.is_available(),
          "wheel_version": importlib.metadata.version("turbo-diffusion-kernels"),
          "module": kernels.__file__, "bindings": {}}
for name, count in expected.items():
    fn = getattr(kernels, name)
    assert callable(fn)
    assert "torch.Tensor" in fn.__doc__, (name, fn.__doc__)
    assert len(re.findall(r"arg[0-9]+:", fn.__doc__)) == count, (name, fn.__doc__)
    # A string cannot pass pybind's tensor conversion, so CUDA kernels never run.
    try:
        fn(*(["invalid schema probe"] * count))
    except TypeError as error:
        assert "incompatible function arguments" in str(error), str(error)
    else:
        raise AssertionError(f"{name} accepted a non-Tensor argument")
    result["bindings"][name] = fn.__doc__
print(json.dumps(result, indent=2))
