import pytest

from opticonverterbackend.app import app


def test_legacy_modules_reexport_package_apis():
    import ai_converter as legacy_ai_converter
    import convert_pdf_to_docx as legacy_pdf_converter

    from opticonverterbackend import ai_converter, convert_pdf_to_docx

    assert legacy_ai_converter.convert_file is ai_converter.convert_file
    assert legacy_pdf_converter.convert is convert_pdf_to_docx.convert


def test_media_converter_dispatches_video_and_audio(monkeypatch):
    from opticonverterbackend import ai_converter

    calls = []
    monkeypatch.setattr(
        ai_converter,
        "video_convert",
        lambda *args: calls.append(("video", args)),
    )
    monkeypatch.setattr(
        ai_converter,
        "audio_convert",
        lambda *args: calls.append(("audio", args)),
    )

    ai_converter.media_convert("input.mp4", "output.mp3", ".mp4", ".mp3", 20, 75)
    ai_converter.media_convert("input.wav", "output.mp3", ".wav", ".mp3", 20, 75)

    assert calls == [
        ("video", ("input.mp4", "output.mp3", ".mp4", ".mp3", 20, 75)),
        ("audio", ("input.wav", "output.mp3", ".wav", ".mp3", 20)),
    ]


def test_ogg_audio_conversion_uses_vorbis_quality_mode(monkeypatch):
    from opticonverterbackend.converters import audio_converter, video_converter

    commands = []
    monkeypatch.setattr(audio_converter, "tool", lambda name: name)
    monkeypatch.setattr(video_converter, "tool", lambda name: name)
    monkeypatch.setattr(audio_converter, "run", commands.append)
    monkeypatch.setattr(video_converter, "run", commands.append)

    audio_converter.audio_convert("input.wav", "output.ogg", ".wav", ".ogg", 30)
    video_converter.video_convert("input.mp4", "output.ogg", ".mp4", ".ogg", 30)

    assert len(commands) == 2
    for command in commands:
        assert command[command.index("-c:a") + 1] == "libvorbis"
        assert "-q:a" in command
        assert "-b:a" not in command


def test_health_endpoint():
    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_formats_endpoint_describes_supported_operations():
    response = app.test_client().get("/formats")

    assert response.status_code == 200
    formats = response.get_json()
    assert formats["max_upload_bytes"] == 50 * 1024 * 1024
    assert formats["operations"] == ["merge", "split", "rotate", "watermark", "metadata"]
    assert formats["conversions"]["documents"]["pairs"] == {
        ".docx": [".pdf"],
        ".pptx": [".pdf"],
        ".xlsx": [".csv", ".pdf", ".xls"],
        ".xls": [".csv", ".pdf", ".xlsx"],
        ".csv": [".pdf", ".xls", ".xlsx"],
    }


def test_document_router_rejects_unavailable_format_pairs(tmp_path):
    from opticonverterbackend.converters.common import ConversionFailure
    from opticonverterbackend.converters.router import convert_file

    source = tmp_path / "source.docx"
    source.write_bytes(b"not inspected because this pair is rejected before conversion")

    for source_name, target in [
        ("source.docx", ".xlsx"),
        ("source.docx", ".csv"),
        ("source.pptx", ".xls"),
        ("source.txt", ".xlsx"),
        ("source.txt", ".csv"),
    ]:
        with pytest.raises(ConversionFailure):
            convert_file(source, tmp_path / f"output{target}", source_name, target)


def test_cors_allows_configured_origin(monkeypatch):
    monkeypatch.setattr("opticonverterbackend.app.allowed_origins", {"https://opticonverter.com"})

    response = app.test_client().get(
        "/health",
        headers={"Origin": "https://opticonverter.com"},
    )

    assert response.headers["Access-Control-Allow-Origin"] == "https://opticonverter.com"


def test_cors_rejects_unconfigured_origin(monkeypatch):
    monkeypatch.setattr("opticonverterbackend.app.allowed_origins", {"https://opticonverter.com"})

    response = app.test_client().get(
        "/health",
        headers={"Origin": "https://attacker.example"},
    )

    assert "Access-Control-Allow-Origin" not in response.headers
