FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    TRAVIS234_NO_VENV_REEXEC=1 \
    DEBIAN_FRONTEND=noninteractive \
    HOME=/travis-home \
    TRAVIS234_CODING_AGENT_DIR=/travis-home/agent

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        nodejs \
        npm \
        ripgrep \
        sudo \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --home-dir /travis-home --shell /bin/bash travis \
    && mkdir -p /workspace /travis-home/agent /opt/travis234 \
    && chown -R travis:travis /workspace /travis-home \
    && printf 'Defaults:travis env_keep += "DEBIAN_FRONTEND"\ntravis ALL=(root) NOPASSWD: /usr/bin/apt-get, /usr/bin/apt, /usr/bin/dpkg\n' > /etc/sudoers.d/travis-packages \
    && chmod 0440 /etc/sudoers.d/travis-packages

WORKDIR /opt/travis234

COPY pyproject.toml README.md LICENSE NOTICE.md ./
COPY travis ./travis

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

USER travis
WORKDIR /workspace

ENTRYPOINT ["travis234"]
CMD ["--cwd", "/workspace"]
