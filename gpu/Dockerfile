# Build Dockerfile.core first for a fully reproducible base from official digest.
ARG CORE_IMAGE=leam-vid:core
ARG TURBO_IMAGE=leam-vid:turbo-build
FROM ${TURBO_IMAGE} AS turbo
FROM ${CORE_IMAGE}
RUN apt-get update -qq && apt-get install -y --no-install-recommends build-essential && rm -f /etc/ssh/ssh_host_* && rm -rf /var/lib/apt/lists/*
COPY dependency-lock.txt /opt/private-video-native-dependency-lock.txt
COPY --from=turbo /artifacts/*.whl /tmp/leam-vid-wheels/
COPY turbo/python-dependencies.lock /tmp/leam-vid-wheels/python-dependencies.lock
RUN python -m pip install --no-cache-dir -c /opt/private-video-native-dependency-lock.txt -r /tmp/leam-vid-wheels/python-dependencies.lock && python -m pip install --no-deps --no-index /tmp/leam-vid-wheels/*.whl && python -m pip check && python -m pip list --format=freeze > /opt/private-video-runtime-lock.txt && rm -rf /tmp/leam-vid-wheels
COPY runtime-source/ /workspace/wan-cloud/runtime-source/
COPY apply-public-patches.py /opt/private-video/apply-public-patches.py
RUN python /opt/private-video/apply-public-patches.py /workspace/wan-cloud/runtime-source/TurboDiffusion
COPY assets/ /workspace/wan-assets/
COPY native_graphs.py /opt/private-video/native_graphs.py
COPY workflows/ /opt/private-video/examples/
RUN cp /workspace/wan-assets/private-video-nodes.py /workspace/wan-cloud/ComfyUI/custom_nodes/PrivateVideoRuntime/__init__.py && chmod 700 /workspace/wan-assets/*.sh && touch /workspace/wan-cloud/turbowan-runtime.installed
CMD ["/workspace/wan-assets/start-comfy-video.sh"]
