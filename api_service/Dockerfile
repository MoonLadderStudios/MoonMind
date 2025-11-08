ARG CODEX_CLI_VERSION=0.6.0
ARG SPEC_KIT_VERSION=0.4.0

FROM node:20-bookworm AS tooling-builder

ARG CODEX_CLI_VERSION
ARG SPEC_KIT_VERSION

# Install build prerequisites required for global npm CLI installs so future
# stages can compile any optional native extensions.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        build-essential && \
    rm -rf /var/lib/apt/lists/*

# Keep npm non-interactive inside Docker builds to avoid analytics prompts.
RUN npm config set fund false \
    && npm config set update-notifier false

# Install Codex and Spec Kit CLIs globally so binaries can be copied to the
# runtime image without shipping npm tooling alongside the Python stack.
RUN case "${CODEX_CLI_VERSION}" in
        (""|*[!0-9A-Za-z._-]*) echo "Invalid CODEX_CLI_VERSION: ${CODEX_CLI_VERSION}" >&2; exit 1;;
    esac \
    && case "${SPEC_KIT_VERSION}" in \
        (""|*[!0-9A-Za-z._-]*) echo "Invalid SPEC_KIT_VERSION: ${SPEC_KIT_VERSION}" >&2; exit 1;;
    esac \
    && npm install -g \
        @githubnext/codex-cli@"${CODEX_CLI_VERSION}" \
        @githubnext/spec-kit@"${SPEC_KIT_VERSION}" \
    && npm cache clean --force

# Fail the build early if the Codex or Spec Kit CLI binaries are missing or
# broken. This helps catch upstream npm publish issues before the runtime image
# is produced.
RUN codex --version \
    && speckit --version

FROM python:3.12-slim-bookworm

ARG CODEX_CLI_VERSION
ARG SPEC_KIT_VERSION

ENV CODEX_CLI_VERSION=${CODEX_CLI_VERSION} \
    SPEC_KIT_VERSION=${SPEC_KIT_VERSION} \
    PATH="/usr/local/bin:${PATH}"

WORKDIR /app

# Copy Codex and Spec Kit artifacts from the tooling builder so the runtime
# image retains only the compiled CLIs and minimal Node runtime needed to
# execute them.
COPY --from=tooling-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=tooling-builder /usr/local/bin/codex /usr/local/bin/codex
COPY --from=tooling-builder /usr/local/bin/speckit /usr/local/bin/speckit
COPY --from=tooling-builder /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN mkdir -p /usr/share/licenses/codex-cli /usr/share/licenses/spec-kit

COPY --from=tooling-builder /usr/local/lib/node_modules/@githubnext/codex-cli/LICENSE /usr/share/licenses/codex-cli/LICENSE
COPY --from=tooling-builder /usr/local/lib/node_modules/@githubnext/spec-kit/LICENSE /usr/share/licenses/spec-kit/LICENSE

# Install system packages required by the Python service along with the
# Node.js runtime libraries that the bundled Codex and Spec Kit binaries rely on.
RUN apt-get update && \
    apt-get install -y \
        build-essential \
        pkg-config \
        libcairo2-dev \
        libffi-dev \
        tesseract-ocr \
        tesseract-ocr-eng \
        git \
        curl \
        nodejs && \
    rm -rf /var/lib/apt/lists/*

# Ensure the non-root ``app`` user exists for Celery workers and the bundled
# CLIs remain executable from its PATH.
RUN useradd --create-home --home-dir /home/app --shell /bin/bash app && \
    chmod 0755 /usr/local/bin/codex /usr/local/bin/speckit

# Verify the non-root ``app`` user can resolve and execute the bundled CLIs.
RUN runuser -u app -- codex --version \
    && runuser -u app -- speckit --version

RUN pip install watchfiles

# Copy pyproject.toml and README.md first for better Docker layer caching
COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md

# Create empty directories to satisfy pip install
RUN mkdir -p /app/moonmind /app/api_service
RUN touch /app/moonmind/__init__.py /app/api_service/__init__.py

# Install dependencies first (this layer will be cached as long as pyproject.toml doesn't change)
RUN pip install -e .
RUN pip install readmeai~=0.6.3 --no-deps

# Copy source code AFTER installing dependencies
# This ensures that code changes don't invalidate the dependency cache
COPY moonmind /app/moonmind/
COPY api_service /app/api_service/

RUN chmod +x /app/api_service/entrypoint.sh && \
    sed -i 's/\r$//' /app/api_service/entrypoint.sh

EXPOSE 5000

CMD ["/app/api_service/entrypoint.sh"]
