# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
RUN useradd --create-home --uid 10001 jobmatch \
    && mkdir /work \
    && chown jobmatch /work
COPY --from=build /dist/*.whl /tmp/
RUN pip install /tmp/*.whl && rm /tmp/*.whl
USER jobmatch
WORKDIR /work
HEALTHCHECK --interval=60s --timeout=10s --retries=3 CMD ["jobmatch", "--help"]
ENTRYPOINT ["jobmatch"]
CMD ["skills"]
