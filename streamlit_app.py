#!/usr/bin/env python3
"""streamlit.app's entry point. It holds no logic: it runs board_app.main() and nothing else.

Why a separate file at all: Community Cloud's deploy form defaults its "Main script path" to
``streamlit_app.py``, and a first-time deployer who leaves that field alone gets "no app file found"
- which reads like a broken pipeline and is one filename. So the filename exists, the page lives in
board_app.py (which is what the tests exercise, and what the tiles and the redactor are built
around), and the only rule here is that this file never grows code: two definitions of the same page
is how a hosted view and a local view start disagreeing about what "applied this week" means.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import board_app  # noqa: E402

board_app.main()
