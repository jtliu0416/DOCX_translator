FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN set -eux; \
    npm config set fetch-timeout 45000; \
    npm config set fetch-retries 5; \
    npm config set fetch-retry-mintimeout 20000; \
    npm config set fetch-retry-maxtimeout 120000; \
    ok=0; \
    for registry in \
      https://registry.npmmirror.com \
      https://registry.npmjs.org; do \
      npm config set registry "$registry"; \
      if npm ci; then ok=1; break; fi; \
    done; \
    [ "$ok" -eq 1 ]
COPY frontend/ ./
RUN npm run build

FROM mcr.microsoft.com/dotnet/sdk:8.0 AS dotnet-builder
WORKDIR /app/backend/scripts/Doctrans.DocxProc
COPY backend/scripts/Doctrans.DocxProc/ ./
RUN set -eux; \
    timeout 60s dotnet restore \
      -r linux-x64 \
      --source https://nuget.cdn.azure.cn/v3/index.json \
      --source https://mirrors.cloud.tencent.com/nuget/v3/index.json \
      --source https://api.nuget.org/v3/index.json \
      --ignore-failed-sources \
    || timeout 60s dotnet restore \
      -r linux-x64 \
      --source https://mirrors.cloud.tencent.com/nuget/v3/index.json \
      --source https://api.nuget.org/v3/index.json \
      --ignore-failed-sources \
    || timeout 60s dotnet restore \
      -r linux-x64 \
      --source https://api.nuget.org/v3/index.json
RUN dotnet publish -c Release -r linux-x64 --self-contained true --no-restore /p:PublishSingleFile=true /p:PublishTrimmed=false -o /out/docxproc

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app/backend

RUN set -eux; \
    sources="/etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources"; \
    apt_opts="-o Acquire::Retries=1 -o Acquire::http::Timeout=10 -o Acquire::https::Timeout=10"; \
    ok=0; \
    for mirror in \
      https://mirrors.aliyun.com \
      https://mirrors.tuna.tsinghua.edu.cn \
      https://mirrors.ustc.edu.cn \
      https://deb.debian.org; do \
      sed -ri "s#https?://deb.debian.org#$mirror#g; s#https?://security.debian.org#$mirror/debian-security#g" $sources 2>/dev/null || true; \
      if apt-get $apt_opts update; then ok=1; break; fi; \
    done; \
    [ "$ok" -eq 1 ]; \
    apt-get $apt_opts install -y --no-install-recommends libicu-dev; \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/requirements.txt
RUN set -eux; \
    ok=0; \
    for index_url in \
      https://mirrors.aliyun.com/pypi/simple/ \
      https://pypi.tuna.tsinghua.edu.cn/simple \
      https://mirrors.ustc.edu.cn/pypi/simple \
      https://pypi.org/simple; do \
      if timeout 180s pip install --no-cache-dir --retries 2 --timeout 25 -i "$index_url" -r /tmp/requirements.txt; then ok=1; break; fi; \
    done; \
    [ "$ok" -eq 1 ]

COPY backend/ /app/backend/
COPY --from=frontend-builder /app/frontend/dist /app/backend/frontend/dist
COPY --from=dotnet-builder /out/docxproc /app/backend/scripts/Doctrans.DocxProc/publish
RUN chmod +x /app/backend/scripts/Doctrans.DocxProc/publish/Doctrans.DocxProc

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
