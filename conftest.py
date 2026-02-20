"""
conftest.py — pytest configuration for AtmoTrack.

Ensures the project root is on sys.path so that pytest can always import
AtmoTrack modules regardless of the working directory it is invoked from.
"""
import sys
from pathlib import Path

# Insert the project root at the front of sys.path
sys.path.insert(0, str(Path(__file__).parent))
