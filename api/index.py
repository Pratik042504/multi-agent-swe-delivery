"""Vercel Serverless Function entrypoint for MAS-EDS.

Exposes the SwarmHTTPHandler for Vercel's Python runtime (@vercel/python).
Supports both BaseHTTPRequestHandler interface (class handler) and WSGI/ASGI apps.
"""

import os
import sys
from pathlib import Path

# Ensure project root is in sys.path so 'src' can be imported in Vercel lambda
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Ensure VERCEL environment flag is set
os.environ.setdefault("VERCEL", "1")

from src.server import SwarmHTTPHandler

class handler(SwarmHTTPHandler):
    """Vercel Python runtime HTTP request handler."""
    pass
