"""image_generator 테스트."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from xp.config import XAIConfig
from xp.image_generator import (
    ImageGenerator,
    add_watermark,
    fit_aspect_ratio,
)


@pytest.fixture
def xai_config():
    return XAIConfig()


@pytest.fixture
def tmp_output(tmp_path):
    return tmp_path / "images"


class TestImageGenerator:
    @patch("xp.image_generator.ImageGenerator._download_image")
    @patch("xp.image_generator._post_json")
    @patch("xp.config.XAIConfig.get_access_token", return_value="fake-token")
    def test_generate_url_mode(
        self, mock_get_key, mock_post, mock_download, xai_config, tmp_output
    ):
        """URL 모드에서 이미지 생성 및 다운로드를 테스트합니다."""
        mock_post.return_value = {
            "data": [{"url": "https://example.com/image.png", "b64_json": None}]
        }

        def fake_download(url, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake-png-data")

        mock_download.side_effect = fake_download

        gen = ImageGenerator(xai_config)
        results = gen.generate(
            prompt="A futuristic city",
            output_dir=tmp_output,
            filename="city.png",
        )

        assert len(results) == 1
        assert results[0].local_path.name == "city.png"
        assert results[0].prompt == "A futuristic city"
        mock_download.assert_called_once()

    @patch("xp.image_generator._post_json")
    @patch("xp.config.XAIConfig.get_access_token", return_value="fake-token")
    def test_generate_b64_mode(self, mock_get_key, mock_post, xai_config, tmp_output):
        """base64 모드에서 이미지 생성을 테스트합니다."""
        fake_b64 = base64.b64encode(b"fake-png-data").decode()

        mock_post.return_value = {
            "data": [{"url": None, "b64_json": fake_b64}]
        }

        gen = ImageGenerator(xai_config)
        results = gen.generate(
            prompt="A robot",
            output_dir=tmp_output,
            filename="robot.png",
        )

        assert len(results) == 1
        assert results[0].local_path.exists()
        assert results[0].local_path.read_bytes() == b"fake-png-data"

    @patch("xp.image_generator.ImageGenerator._download_image")
    @patch("xp.image_generator._post_json")
    @patch("xp.config.XAIConfig.get_access_token", return_value="fake-token")
    def test_generate_multiple(
        self, mock_get_key, mock_post, mock_download, xai_config, tmp_output
    ):
        """여러 이미지 생성 시 파일명에 넘버링을 테스트합니다."""
        mock_post.return_value = {
            "data": [
                {"url": f"https://example.com/img_{i}.png", "b64_json": None}
                for i in range(3)
            ]
        }

        def fake_download(url, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"data")

        mock_download.side_effect = fake_download

        gen = ImageGenerator(xai_config)
        results = gen.generate(
            prompt="Test",
            output_dir=tmp_output,
            filename="img.png",
            n=3,
        )

        assert len(results) == 3
        names = [r.local_path.name for r in results]
        assert names == ["img_1.png", "img_2.png", "img_3.png"]


class TestWatermark:
    def _solid_png(self, path, color):
        from PIL import Image

        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (400, 200), color).save(path)

    def test_watermark_changes_bottom_right(self, tmp_path):
        """워터마크가 우측 하단 픽셀을 실제로 바꾼다."""
        from PIL import Image

        img_path = tmp_path / "w.png"
        self._solid_png(img_path, (255, 255, 255))  # 흰 배경
        before = Image.open(img_path).convert("RGB").copy()

        add_watermark(img_path, "@돈버는코드")

        after = Image.open(img_path).convert("RGB")
        assert after.size == before.size
        # 우측 하단 영역에 흰색이 아닌(=글자가 그려진) 픽셀이 있어야 한다.
        region = after.crop((250, 140, 400, 200))
        assert any(px != (255, 255, 255) for px in region.getdata())

    def test_watermark_empty_text_noop(self, tmp_path):
        """빈 워터마크는 이미지를 변경하지 않는다."""
        from PIL import Image

        img_path = tmp_path / "w.png"
        self._solid_png(img_path, (30, 30, 30))
        before = list(Image.open(img_path).convert("RGB").getdata())

        add_watermark(img_path, "")

        after = list(Image.open(img_path).convert("RGB").getdata())
        assert before == after

    def test_watermark_contrasts_on_dark_bg(self, tmp_path):
        """어두운 배경에서는 밝은 글자가 그려진다."""
        from PIL import Image

        img_path = tmp_path / "w.png"
        self._solid_png(img_path, (10, 10, 10))  # 어두운 배경
        add_watermark(img_path, "@돈버는코드")

        region = Image.open(img_path).convert("RGB").crop((250, 140, 400, 200))
        # 밝은(대비) 픽셀이 존재해야 한다.
        assert any(sum(px) > 400 for px in region.getdata())


class TestAspectRatio:
    def _png(self, path, size):
        from PIL import Image

        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, (120, 120, 120)).save(path)

    def test_portrait_cropped_to_16_9(self, tmp_path):
        from PIL import Image

        p = tmp_path / "a.png"
        self._png(p, (864, 1152))  # 3:4 세로
        fit_aspect_ratio(p, 16 / 9)

        w, h = Image.open(p).size
        assert w == 864  # 가로 유지, 세로만 크롭
        assert abs(w / h - 16 / 9) < 0.02

    def test_wide_cropped_to_16_9(self, tmp_path):
        from PIL import Image

        p = tmp_path / "a.png"
        self._png(p, (2000, 1000))  # 2:1, 너무 넓음
        fit_aspect_ratio(p, 16 / 9)

        w, h = Image.open(p).size
        assert h == 1000  # 세로 유지, 가로만 크롭
        assert abs(w / h - 16 / 9) < 0.02

    def test_already_correct_noop(self, tmp_path):
        from PIL import Image

        p = tmp_path / "a.png"
        self._png(p, (1280, 720))  # 이미 16:9
        fit_aspect_ratio(p, 16 / 9)
        assert Image.open(p).size == (1280, 720)
