import os, tempfile
os.environ["KNOWBRIDGE_DATA"] = tempfile.mkdtemp(prefix="kb_test_")   # before app imports
from pathlib import Path
import pytest
from app.db import DB
from app.pipeline import run_folder
SAMPLE = Path(__file__).parent.parent / "sample_data"
@pytest.fixture(scope="session")
def kb(tmp_path_factory):
    db = DB(tmp_path_factory.mktemp("db") / "t.db"); r = run_folder(db, SAMPLE); r["db"] = db; return r
