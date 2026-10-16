# The LTX-2.5 inference server, for a Linux machine with an 80 GB NVIDIA GPU
# and the NVIDIA Container Toolkit:
#   docker build -t untitled-project-ltx server
#   docker run --rm --gpus all -e HF_TOKEN -e LTX2_API_KEY -p 127.0.0.1:8000:8000 \
#       -v ltx-hf-cache:/root/.cache/huggingface untitled-project-ltx
# The gated weights (~72 GB) download into the cache volume on the first
# start; nothing is baked into the image. The base image is the one the
# rented boxes run (remote_setup.sh installs the same requirements there).
FROM pytorch/pytorch:2.9.1-cuda12.8-cudnn9-devel

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/ltx

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY ltx2_server.py encode.py audio_ceiling.py server_defaults.sh ./

# All interfaces inside the container so the published port works; publish it
# on 127.0.0.1 (or behind a firewall) and set LTX2_API_KEY beyond that.
ENV LTX2_HOST=0.0.0.0
EXPOSE 8000
# server_defaults.sh: the distilled recipe, fp8, compile, 512x288 and the
# live config's warm-up; any LTX2_* passed with -e wins.
CMD ["bash", "-c", ". ./server_defaults.sh && exec python ltx2_server.py"]
