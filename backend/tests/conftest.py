"""Test configuration: point everything at a throwaway SQLite DB and known
secrets BEFORE the app package (and its Settings) get imported.
"""
import os
import tempfile

_TMP_DB = os.path.join(tempfile.gettempdir(), "attendance_test.db")
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)

os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DB}")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("BIOMETRIC_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("MIN_VERIFY_SCORE", "0.40")
