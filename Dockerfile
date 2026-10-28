FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml UPSTREAM_LICENSE.txt upstream-lock.json ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir . && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin conductor
USER 10001:10001
EXPOSE 8012
CMD ["python", "-m", "conductor_coordination.coordination_server"]
