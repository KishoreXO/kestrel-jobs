FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project
COPY kestrel ./kestrel
RUN uv sync --no-dev
ENV HOST=0.0.0.0 DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["uv", "run", "--no-dev", "kestrel"]
