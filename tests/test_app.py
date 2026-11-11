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
