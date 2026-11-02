FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /workspace
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
       fonts-noto-cjk fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
COPY server/requirements.lock /tmp/wenshu-requirements.lock
RUN pip install --no-cache-dir -r /tmp/wenshu-requirements.lock
COPY deploy/server/fonts/ /usr/local/share/fonts/wenshu/
RUN fc-cache -f
USER 1000:1000
