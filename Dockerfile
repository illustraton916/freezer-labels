FROM python:3.11-slim

# Шрифти НЕ ставимо з apt: DejaVu тепер вендорені в labels/fonts і їх SHA-256
# перевіряється на старті (labels/fonts_check.py). apt — не система версіонування,
# а від конкретного білда шрифту залежить кожна точка переносу на наліпці.

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py products.json ./
COPY labels ./labels
COPY tools ./tools

ARG BUILD_SHA=dev
ENV BUILD_SHA=$BUILD_SHA

# 5001 — /api/* для Node-бекенду (у компоузі НЕ публікується);
# 5000 — старий веб-інтерфейс (у компоузі публікується лише на 127.0.0.1).
EXPOSE 5001 5000

# Здоров'я сервісу міряється по 5001: при ENABLE_LEGACY_UI=false порт 5000 не
# відкривається взагалі, і healthcheck по ньому валив би справний контейнер.
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5001/api/healthz', timeout=3).status == 200 else 1)"
CMD ["python", "app.py"]
