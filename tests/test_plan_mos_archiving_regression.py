from pathlib import Path


def test_archive_note_column_helper_is_not_present():
    main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
    source = main_path.read_text(encoding="utf-8")

    assert "ensure_archive_note_column" not in source
    assert "Archive Note" not in source
    assert "To Archive" not in source
