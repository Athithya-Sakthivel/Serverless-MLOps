# ============================================================
# tests/test_model_loader.py
# ============================================================
from __future__ import annotations

from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest
from model.loader import ModelLoader
from model.registry import ModelResolution


@pytest.fixture
def mock_registry() -> MagicMock:
    registry = MagicMock()
    resolution = ModelResolution(
        model_name="test_model",
        model_version="1",
        run_id="run123",
        artifact_uri="models:/test_model/1/onnx/model.onnx",
    )
    registry.resolve.return_value = resolution
    registry.tracking_uri = "test-uri"
    registry.registry_uri = None
    return registry


def test_load_creates_session(mock_registry: MagicMock, tmp_path: Path) -> None:
    onnx_file = tmp_path / "model.onnx"
    onnx_file.write_text("fake-onnx")

    with (
        patch("model.loader.download_artifacts") as mock_download,
        patch("model.loader.ort.InferenceSession") as mock_session,
    ):
        mock_download.return_value = str(onnx_file)
        mock_session.return_value = MagicMock()

        loader = ModelLoader(mock_registry)
        session = loader.load()

        mock_download.assert_called_once_with(
            artifact_uri="models:/test_model/1/onnx/model.onnx",
            dst_path=ANY,
            tracking_uri="test-uri",
            registry_uri=None,
        )
        mock_session.assert_called_once()
        assert session is not None
        assert loader.is_ready is True
        assert loader.model_version == "1"


def test_load_uses_cached_session(mock_registry: MagicMock, tmp_path: Path) -> None:
    onnx_file = tmp_path / "model.onnx"
    onnx_file.write_text("fake-onnx")

    with (
        patch("model.loader.download_artifacts") as mock_download,
        patch("model.loader.ort.InferenceSession"),
    ):
        mock_download.return_value = str(onnx_file)
        loader = ModelLoader(mock_registry)
        loader.load()

    with patch("model.loader.download_artifacts") as mock_download_second:
        loader.load()
        mock_download_second.assert_not_called()


def test_is_ready_false_before_load(mock_registry: MagicMock) -> None:
    loader = ModelLoader(mock_registry)
    assert loader.is_ready is False


def test_model_version_property(mock_registry: MagicMock) -> None:
    loader = ModelLoader(mock_registry)
    assert loader.model_version == "1"
