# SPDX-License-Identifier: Apache-2.0
"""Collector recipes vendored from the Computable GPU Index (Apache-2.0).

Source: https://github.com/getcomputable/gpu-index at commit 698f2e0 (2026-09-08).
Copyright 2026 Computable. Each file keeps its original header. Changes made here:
import paths rewritten to this package, the SKU catalog path pointed at the copy beside
catalog.py, and lambda.py renamed lambda_.py (a Python keyword). No parsing logic was
changed; the recipes and their live-captured fixtures (tests/fixtures/computable/) are
exactly as published, so a divergence from upstream is visible in a diff.

Why vendor rather than depend: TCI's collectors must be pinned, reviewable and
reproducible from this repository alone, and the upstream package is not published to
PyPI. TCI identifies itself with its own User-Agent on every request made through
these recipes (see tci.collectors.computable_sources).
"""
