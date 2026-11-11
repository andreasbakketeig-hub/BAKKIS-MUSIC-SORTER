import secrets
from pathlib import Path
from bakkis_sorter.logger import SQLiteLogHandler, get_logger, setup_logging
from bakkis_sorter.storage import EncryptedStorage


def test_sqlite_log_handler(tmp_path: Path):
    store = EncryptedStorage(db_path=tmp_path / "bakkis.db", master_key=secrets.token_bytes(32))
    logger = get_logger("test_module")
    logger.setLevel(10)
    logger.addHandler(SQLiteLogHandler(level=10, storage=store))

    logger.info("Test informational message")
    try:
        raise ValueError("Simulated calculation error")
    except ValueError:
        logger.error("Failed executing job", exc_info=True)

    logs = store.get_logs(min_level="INFO")
    assert len(logs) >= 2
    err_log = next(l for l in logs if l["level"] == "ERROR")
    assert err_log["message"] == "Failed executing job"
    assert err_log["exception_type"] == "ValueError"
    assert "Simulated calculation error" in err_log["traceback"]

    store.clear_logs()
    assert len(store.get_logs()) == 0
    store.close()
