# ========== 构建阶段：安装依赖到 .venv ==========
FROM python:3.12-slim AS builder

WORKDIR /app

# 安装 uv（仅构建阶段使用，不进入最终镜像）
RUN pip install --no-cache-dir uv

# 先复制依赖声明，利用 Docker 层缓存
COPY pyproject.toml uv.lock ./
# 复制源码以便 uv sync 安装项目
COPY app.py edit_archive_xml.py update_archives_with_xml.py ./
COPY templates/ ./templates/
COPY static/ ./static/

# 锁定依赖安装，不装开发包
RUN uv sync --frozen --no-dev

# ========== 运行阶段：最小镜像 ==========
FROM python:3.12-slim AS runtime

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --shell /bin/bash --create-home app

WORKDIR /app

# 复制虚拟环境与源码（路径一致，.venv 内脚本可正常执行）
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app.py /app/edit_archive_xml.py /app/update_archives_with_xml.py ./
COPY --from=builder /app/templates /app/templates
COPY --from=builder /app/static /app/static

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN chown -R app:app /app
USER app

EXPOSE 8000

# 环境变量：ALLOWED_BASE_PATHS（逗号分隔）、SESSION_SECRET
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
