"""Версія сервісу рендерера.

Контракт рендерера (render/print/healthz) має жити довше за все інше в системі,
тож версія — властивість самого сервісу, а не гілки збірки. BUILD_SHA додається
з env при збірці образу.
"""
import os

SERVICE_VERSION = "0.2.0"          # 0.2: пакунок (pack_weight_g/pack_price) і штрих-код EAN-13
BUILD_SHA = os.environ.get("BUILD_SHA", "dev")
