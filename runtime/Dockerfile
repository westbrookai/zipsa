# SKILL Runtime Docker Image - Multi-stage Build
# Base: Debian Slim for glibc compatibility
# Purpose: Runtime environment for Claude Code, Codex, Gemini CLI with MCP support
# Strategy: Builder compiles all npm packages + Node.js; runtime copies them in

#-----------------------------------------------------------
# Build Arguments - Package Versions
#-----------------------------------------------------------

# Image version (injected at publish time via --build-arg)
ARG ZIPSA_RUNTIME_VERSION="0.4.6"

# System packages (apt)
ARG CURL_VERSION="7.88.1-10+deb12u14"
ARG CA_CERTIFICATES_VERSION="20230311+deb12u1"
ARG GIT_VERSION="1:2.39.5-0+deb12u3"
ARG BUILD_ESSENTIAL_VERSION="12.9"
ARG NODEJS_VERSION="24.14.1-1nodesource1"
ARG PYTHON3_VERSION="3.11.2-1+b1"
ARG PYTHON3_PIP_VERSION="23.0.1+dfsg-1"
ARG PYTHON3_VENV_VERSION="3.11.2-1+b1"
ARG PIPX_VERSION="1.1.0-1"

# NPM packages
ARG CLAUDE_CODE_VERSION="2.1.114"
ARG CODEX_VERSION="0.121.0"
ARG GEMINI_CLI_VERSION="0.32.1"
ARG FX_VERSION="35.0.0"

#-----------------------------------------------------------
# Stage 1: Builder - Node.js + all npm packages
#-----------------------------------------------------------

FROM debian:bookworm-slim AS builder

ARG CURL_VERSION
ARG CA_CERTIFICATES_VERSION
ARG BUILD_ESSENTIAL_VERSION
ARG NODEJS_VERSION
ARG CLAUDE_CODE_VERSION
ARG CODEX_VERSION
ARG GEMINI_CLI_VERSION
ARG FX_VERSION

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Install build deps + Node.js 24.x in one layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl=${CURL_VERSION} \
        ca-certificates=${CA_CERTIFICATES_VERSION} \
        build-essential=${BUILD_ESSENTIAL_VERSION} \
        && \
    curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && \
    apt-get install -y --no-install-recommends nodejs=${NODEJS_VERSION} && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install npm packages (separate layers for cache granularity)
RUN npm install -g --omit=dev @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} && \
    npm cache clean --force

RUN npm install -g --omit=dev @openai/codex@${CODEX_VERSION} || echo "Codex installation skipped" && \
    npm cache clean --force

RUN npm install -g --omit=dev @google/gemini-cli@${GEMINI_CLI_VERSION} && \
    npm cache clean --force

RUN npm install -g fx@${FX_VERSION} && \
    npm cache clean --force

RUN claude --version && codex --version && gemini --version && fx --version

#-----------------------------------------------------------
# Stage 2: Runtime - Copy Node.js from builder, single apt layer
#-----------------------------------------------------------

FROM debian:bookworm-slim AS runtime

ARG ZIPSA_RUNTIME_VERSION
ARG CURL_VERSION
ARG CA_CERTIFICATES_VERSION
ARG GIT_VERSION
ARG PYTHON3_VERSION
ARG PYTHON3_PIP_VERSION
ARG PYTHON3_VENV_VERSION
ARG PIPX_VERSION

# Metadata
LABEL maintainer="your-email@example.com"
LABEL description="SKILL Runtime with Claude Code, Codex, and Gemini CLI (Multi-stage)"
LABEL version="${ZIPSA_RUNTIME_VERSION}"
ENV ZIPSA_RUNTIME_VERSION=${ZIPSA_RUNTIME_VERSION}

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /home/agent/workspace

# Install all runtime packages in a single layer (no nodejs - copied from builder)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl=${CURL_VERSION} \
        ca-certificates=${CA_CERTIFICATES_VERSION} \
        git=${GIT_VERSION} \
        python3=${PYTHON3_VERSION} \
        python3-pip=${PYTHON3_PIP_VERSION} \
        python3-venv=${PYTHON3_VENV_VERSION} \
        pipx=${PIPX_VERSION} \
        jq=1.6-2.1+deb12u1 \
        vim=2:9.0.1378-2+deb12u2 \
        less=590-2.1~deb12u2 \
        tree=2.1.0-1 \
        ripgrep=13.0.0-4+b2 \
        fd-find=8.6.0-3 \
        wget=1.21.3-1+deb12u1 \
        netcat-openbsd=1.219-1 \
        lsof=4.95.0-1 \
        strace=6.1-0.1 \
        htop=3.2.2-2 \
        unzip=6.0-28 \
        bat=0.22.1-4 \
        && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    ln -sf /usr/bin/pip3 /usr/bin/pip && \
    ln -sf /usr/bin/fdfind /usr/local/bin/fd && \
    ln -sf /usr/bin/batcat /usr/local/bin/bat && \
    pipx ensurepath && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy Node.js runtime from builder (avoids nodesource re-setup in runtime stage)
# /usr/bin/node is a real binary; npm/npx/corepack are symlinks so we recreate them
COPY --from=builder /usr/bin/node /usr/bin/node
COPY --from=builder /usr/lib/node_modules /usr/lib/node_modules

# Recreate all symlinks + verify all tools
RUN ln -sf /usr/lib/node_modules/npm/bin/npm-cli.js /usr/bin/npm && \
    ln -sf /usr/lib/node_modules/npm/bin/npx-cli.js /usr/bin/npx && \
    ln -sf /usr/lib/node_modules/corepack/dist/corepack.js /usr/bin/corepack && \
    ln -sf ../lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/bin/claude && \
    ln -sf ../lib/node_modules/@openai/codex/bin/codex.js /usr/bin/codex && \
    ln -sf ../lib/node_modules/@google/gemini-cli/dist/index.js /usr/bin/gemini && \
    ln -sf ../lib/node_modules/fx/index.js /usr/bin/fx && \
    node --version && npm --version && npx --version && \
    claude --version && codex --version && gemini --version && fx --version

# Install yq (YAML processor) - binary download
RUN curl -fsSL "https://github.com/mikefarah/yq/releases/latest/download/yq_linux_$(dpkg --print-architecture)" \
    -o /usr/local/bin/yq && \
    chmod +x /usr/local/bin/yq && \
    yq --version

# Create non-root user
RUN groupadd -r agent -g 1000 && \
    useradd -r -u 1000 -g agent -m -s /bin/bash agent && \
    mkdir -p /home/agent/workspace /home/agent/.claude/skills /home/agent/.local/bin && \
    chown -R agent:agent /home/agent

ENV TERM=xterm-256color
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

USER agent

ENV PATH="/home/agent/.local/bin:${PATH}"

# Install uv and configure shell
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    uv --version && uvx --version && \
    echo 'alias ll="ls -lah"' >> /home/agent/.bashrc && \
    echo 'alias claude-help="claude --help"' >> /home/agent/.bashrc && \
    echo 'export PS1="\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ "' >> /home/agent/.bashrc

WORKDIR /home/agent/workspace

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD node --version || exit 1

CMD ["claude", "--help"]
