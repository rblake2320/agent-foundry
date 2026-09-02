# Sandbox image for running an agentkit agent under NVIDIA OpenShell.
# OpenShell replaces CMD/ENTRYPOINT with its supervisor; pass the agent command after `--` on `openshell sandbox create`.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends iproute2 git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1500 sandbox \
    && install -d -o sandbox -g sandbox /sandbox

COPY --chown=sandbox:sandbox . /sandbox
RUN pip install --no-cache-dir -r /sandbox/requirements.txt

# OpenShell requires a 'sandbox' user and group in every sandbox image; the supervisor drops to it before running the agent.
USER sandbox
WORKDIR /sandbox
ENV PYTHONUNBUFFERED=1
