import os
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("KNOWBRIDGE_DATA", ROOT / "data"))
DB_PATH = Path(os.getenv("KNOWBRIDGE_DB", DATA / "knowbridge.db"))
STALE_DAYS = int(os.getenv("KNOWBRIDGE_STALE_DAYS", "30"))   # age vs newest document in the corpus
CONFLICT_WINDOW_DAYS = 3                                     # differing statuses this close in time = conflict
VERSION = "1.0.0"
MAX_UPLOAD_MB = int(os.getenv("KNOWBRIDGE_MAX_UPLOAD_MB", "200"))  # streamed to disk; raise/lower to match your host limits
SAMPLE_DIR = ROOT / "sample_data"
