"""Quantitative methods (QNT): returns, risk, ratios, regressions, event studies and a strategy backtester.

The maths is Goldman Sachs' open-source gs-quant (Apache-2.0, pinned 2.2.5), only its offline parts: its data and
pricing need a Goldman Marquee login and are not used. Every function here goes through this package, so the rest of
the app never imports gs_quant directly. These are measurements; nothing here changes an engine's verdict.

Modules: series (the price, total-return and index series, with the CSE accuracy rules), metrics (single-stock
figures), rates (the CBSL Treasury-bill rate and optional FRED series), stats_tests (t-statistics), coverage (the map
of every gs-quant module and what became of it).
"""
