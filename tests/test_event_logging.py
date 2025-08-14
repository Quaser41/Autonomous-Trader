import importlib.util
from pathlib import Path

def test_event_log_single_entry(tmp_path, monkeypatch):
    module_path = Path(__file__).resolve().parents[1] / "autonomous_trader" / "utils" / "logger.py"
    spec = importlib.util.spec_from_file_location("logger", module_path)
    logger = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(logger)

    # redirect log directory to temporary path
    monkeypatch.setattr(logger, "LOG_DIR", tmp_path)

    notifier = logger.Notifier({"telegram_enabled": False})

    # first trade event
    logger.log_trade("BUY", "TEST", 1.0, 10.0)
    notifier.send("BUY TEST @ 10.00")

    # second trade event
    logger.log_trade("SELL", "TEST", 1.0, 12.0)
    notifier.send("SELL TEST @ 12.00")

    events_file = tmp_path / "events.log"
    assert events_file.exists()
    lines = events_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
