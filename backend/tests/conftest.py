"""Test configuration: point everything at throwaway storage and known secrets
BEFORE the app package (and its Settings) get imported.
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

# The developer .env points at the LIVE verification tenant, and pydantic-settings
# reads it. Any call a test forgets to stub therefore left this machine and asked
# the production service about a real student — which is how a test came to
# depend on what that tenant happened to hold. Force the base URL somewhere that
# cannot answer, so an unstubbed call fails loudly and locally instead.
os.environ["BIOMETRIC_BASE_URL"] = "http://127.0.0.1:9"   # discard port: always refused
os.environ["BIOMETRIC_API_KEY"] = "test-key"
os.environ["BIOMETRIC_RETRIES"] = "0"                     # no backoff sleeps in tests
os.environ["ENVIRONMENT"] = "test"
