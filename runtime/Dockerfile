# Zipsa Runtime Docker Image - Multi-stage Build
# Base: Debian Slim for glibc compatibility
# Purpose: Runtime environment for Claude Code, Codex, Gemini CLI with MCP support
# Strategy: Builder compiles all npm packages + Node.js; runtime copies them in

#-----------------------------------------------------------
# Build Arguments - Package Versions
#-----------------------------------------------------------

# Image version (injected at publish time via --build-arg)
ARG ZIPSA_RUNTIME_VERSION="0.4.8"

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

# Debug / utility packages (apt)
ARG JQ_VERSION="1.6-2.1+deb12u1"
ARG VIM_VERSION="2:9.0.1378-2+deb12u2"
ARG LESS_VERSION="590-2.1~deb12u2"
ARG TREE_VERSION="2.1.0-1"
ARG RIPGREP_VERSION="13.0.0-4+b2"
ARG FD_FIND_VERSION="8.6.0-3"
ARG WGET_VERSION="1.21.3-1+deb12u1"
ARG NETCAT_OPENBSD_VERSION="1.219-1"
ARG LSOF_VERSION="4.95.0-1"
ARG STRACE_VERSION="6.1-0.1"
ARG HTOP_VERSION="3.2.2-2"
ARG UNZIP_VERSION="6.0-28"
ARG BAT_VERSION="0.22.1-4"

# NPM packages
ARG CLAUDE_CODE_VERSION="2.1.114"
ARG CODEX_VERSION="0.121.0"
ARG GEMINI_CLI_VERSION="0.32.1"
ARG FX_VERSION="35.0.0"

# Standalone binaries
ARG YQ_VERSION="4.53.2"
ARG UV_VERSION="0.11.15"

# Go toolchain (for .go skill phases run via `go run`)
ARG GO_VERSION="1.26.4"
# Per-arch tarball checksums (https://go.dev/dl/?mode=json)
ARG GO_SHA256_AMD64="1153d3d50e0ac764b447adfe05c2bcf08e889d42a02e0fe0259bd47f6733ad7f"
ARG GO_SHA256_ARM64="ef758ae7c6cf9267c9c0ef080b8965f453d89ab2d25d9eb22de4405925238768"

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

RUN npm install -g --omit=dev @openai/codex@${CODEX_VERSION} && \
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
ARG JQ_VERSION
ARG VIM_VERSION
ARG LESS_VERSION
ARG TREE_VERSION
ARG RIPGREP_VERSION
ARG FD_FIND_VERSION
ARG WGET_VERSION
ARG NETCAT_OPENBSD_VERSION
ARG LSOF_VERSION
ARG STRACE_VERSION
ARG HTOP_VERSION
ARG UNZIP_VERSION
ARG BAT_VERSION
ARG CLAUDE_CODE_VERSION
ARG CODEX_VERSION
ARG GEMINI_CLI_VERSION
ARG FX_VERSION
ARG YQ_VERSION
ARG UV_VERSION
ARG GO_VERSION
ARG GO_SHA256_AMD64
ARG GO_SHA256_ARM64

# OCI image metadata (https://github.com/opencontainers/image-spec/blob/main/annotations.md)
LABEL org.opencontainers.image.title="Zipsa Runtime"
LABEL org.opencontainers.image.description="Runtime environment for Claude Code, Codex, and Gemini CLI with MCP support"
LABEL org.opencontainers.image.version="${ZIPSA_RUNTIME_VERSION}"
LABEL org.opencontainers.image.vendor="WestbrookAI"
LABEL org.opencontainers.image.authors="choonsik@westbrookai.com"
LABEL org.opencontainers.image.source="https://github.com/westbrookai/zipsa"
# Legacy LABELs preserved for backward compatibility with publish workflow validation
LABEL version="${ZIPSA_RUNTIME_VERSION}"

ENV ZIPSA_RUNTIME_VERSION=${ZIPSA_RUNTIME_VERSION}
ENV CLAUDE_CODE_VERSION=${CLAUDE_CODE_VERSION}
ENV CODEX_VERSION=${CODEX_VERSION}
ENV GEMINI_CLI_VERSION=${GEMINI_CLI_VERSION}

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
        jq=${JQ_VERSION} \
        vim=${VIM_VERSION} \
        less=${LESS_VERSION} \
        tree=${TREE_VERSION} \
        ripgrep=${RIPGREP_VERSION} \
        fd-find=${FD_FIND_VERSION} \
        wget=${WGET_VERSION} \
        netcat-openbsd=${NETCAT_OPENBSD_VERSION} \
        lsof=${LSOF_VERSION} \
        strace=${STRACE_VERSION} \
        htop=${HTOP_VERSION} \
        unzip=${UNZIP_VERSION} \
        bat=${BAT_VERSION} \
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

# Install yq (YAML processor) - pinned binary download
RUN curl -fsSL "https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_$(dpkg --print-architecture)" \
    -o /usr/local/bin/yq && \
    chmod +x /usr/local/bin/yq && \
    yq --version

# Install Go toolchain (pinned, checksum-verified) for `.go` skill
# phases, which `zipsa exec` dispatches via `go run`. Trim the docs/
# tests/api trees (unused for compilation) to claw back ~30MB.
RUN ARCH="$(dpkg --print-architecture)" && \
    case "${ARCH}" in \
        amd64) GO_SHA256="${GO_SHA256_AMD64}" ;; \
        arm64) GO_SHA256="${GO_SHA256_ARM64}" ;; \
        *) echo "unsupported architecture for Go: ${ARCH}" >&2; exit 1 ;; \
    esac && \
    curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${ARCH}.tar.gz" -o /tmp/go.tar.gz && \
    echo "${GO_SHA256}  /tmp/go.tar.gz" | sha256sum -c - && \
    tar -C /usr/local -xzf /tmp/go.tar.gz && \
    rm /tmp/go.tar.gz && \
    rm -rf /usr/local/go/test /usr/local/go/api /usr/local/go/doc && \
    ln -sf /usr/local/go/bin/go /usr/local/bin/go && \
    ln -sf /usr/local/go/bin/gofmt /usr/local/bin/gofmt && \
    go version

# Pin Go to the bundled toolchain — never auto-download a different one
# (a skill's go.mod could otherwise trigger a network fetch at run time).
ENV GOTOOLCHAIN=local

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

# Install uv (pinned version) and configure shell
RUN curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh && \
    uv --version && uvx --version && \
    printf '%s\n' 'alias ll="ls -lah"' >> /home/agent/.bashrc && \
    printf '%s\n' 'alias claude-help="claude --help"' >> /home/agent/.bashrc && \
    printf '%s\n' 'export PS1="\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ "' >> /home/agent/.bashrc

WORKDIR /home/agent/workspace

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD node --version || exit 1

CMD ["bash"]
