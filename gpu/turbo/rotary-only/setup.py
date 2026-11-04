from setuptools import setup, find_packages
setup(name="private-video-rotary", version="2.8.3+060c9188", packages=find_packages(),
      description="Pinned official FlashAttention rotary Python/Triton implementation",
      license="BSD-3-Clause", license_files=["LICENSE", "AUTHORS"],
      install_requires=["torch==2.9.1", "triton>=3.3.0", "einops"],
      data_files=[("share/private-video-rotary", ["PROVENANCE.json"])])
