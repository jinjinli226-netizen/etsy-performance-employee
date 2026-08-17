from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook
from PIL import Image

from app.training import etsy


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        url: str = "https://i.etsystatic.com/123/main.jpg",
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}
        self._body = body

    def iter_bytes(self, chunk_size: int = 65_536):
        for index in range(0, len(self._body), chunk_size):
            yield self._body[index : index + chunk_size]


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.requested: list[str] = []

    def get(self, url: str, *, follow_redirects: bool = False):
        assert follow_redirects is False
        self.requested.append(url)
        return self.responses.pop(0)


def image_bytes(*, fmt: str = "PNG", size: tuple[int, int] = (4, 3)) -> bytes:
    stream = BytesIO()
    mode = "RGB" if fmt == "JPEG" else "RGBA"
    color = (12, 34, 56) if mode == "RGB" else (12, 34, 56, 128)
    Image.new(mode, size, color).save(stream, format=fmt)
    return stream.getvalue()


def test_strict_etsy_url_normalization() -> None:
    assert etsy.normalize_shop_url("https://www.etsy.com/shop/StageWear/") == "https://www.etsy.com/shop/StageWear"
    assert etsy.normalize_shop_url("https://etsy.com/shop/StageWear") == "https://www.etsy.com/shop/StageWear"
    assert etsy.normalize_listing_url("https://www.etsy.com/listing/123456/a-title/") == (
        "https://www.etsy.com/listing/123456",
        "123456",
    )

    for unsafe in (
        "http://www.etsy.com/shop/StageWear",
        "https://www.etsy.com.evil/shop/StageWear",
        "https://user@www.etsy.com/shop/StageWear",
        "https://www.etsy.com:444/shop/StageWear",
        "https://www.etsy.com/shop/StageWear?ref=x",
        "https://www.etsy.com/shop/StageWear#about",
    ):
        with pytest.raises(ValueError):
            etsy.normalize_shop_url(unsafe)


def test_extract_shop_urls_reads_cells_and_hyperlinks_without_changing_workbook(tmp_path: Path) -> None:
    path = tmp_path / "shops.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "Primary https://www.etsy.com/shop/FirstShop and duplicate https://etsy.com/shop/FirstShop"
    sheet["A2"] = "linked"
    sheet["A2"].hyperlink = "https://www.etsy.com/shop/SecondShop"
    sheet["A3"] = "https://example.com/shop/Nope"
    workbook.save(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    assert etsy.extract_shop_urls(path) == [
        "https://www.etsy.com/shop/FirstShop",
        "https://www.etsy.com/shop/SecondShop",
    ]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_extract_listing_urls_preserves_page_order_and_deduplicates() -> None:
    html = """
      <a href="/listing/222/second">second</a>
      <a href="https://www.etsy.com/listing/111/first">first</a>
      <a href="https://www.etsy.com/listing/222/duplicate">duplicate</a>
      <a href="https://evil.example/listing/333/nope">nope</a>
    """

    assert etsy.extract_listing_urls(html) == [
        "https://www.etsy.com/listing/222",
        "https://www.etsy.com/listing/111",
    ]


def test_snapshot_and_main_image_prefer_product_jsonld_then_og_fallback() -> None:
    fetched_at = datetime(2026, 8, 17, tzinfo=UTC)
    html = """
      <html><head>
      <title>Blue Stage Costume - Etsy</title>
      <meta name="description" content="A dramatic blue costume.">
      <meta property="og:image" content="https://i.etsystatic.com/123/fallback.jpg">
      <script type="application/ld+json">
      {"@type":"Product","name":"Blue Stage Costume","description":"Seller description", "image":["https://i.etsystatic.com/123/main.jpg","https://i.etsystatic.com/123/second.jpg"],"material":"Polyester","category":"Costumes"}
      </script></head><body><a href="/search?q=dance">dance costume</a></body></html>
    """

    snapshot = etsy.extract_listing_snapshot(
        html,
        "https://www.etsy.com/listing/123456",
        fetched_at,
    )

    assert snapshot.listing_id == "123456"
    assert snapshot.title == "Blue Stage Costume"
    assert snapshot.description == "Seller description"
    assert snapshot.text_facts["materials"] == ["Polyester"]
    assert snapshot.text_facts["product_family"] == ["Costumes"]
    assert etsy.select_main_image_url(html, snapshot.canonical_url) == "https://i.etsystatic.com/123/main.jpg"

    without_jsonld_image = html.replace(
        '"image":["https://i.etsystatic.com/123/main.jpg","https://i.etsystatic.com/123/second.jpg"],',
        "",
    )
    assert etsy.select_main_image_url(without_jsonld_image, snapshot.canonical_url) == "https://i.etsystatic.com/123/fallback.jpg"


def test_download_normalizes_first_image_and_hashes_normalized_bytes(tmp_path: Path) -> None:
    body = image_bytes()
    headers = {"content-type": "image/png", "content-length": str(len(body))}
    first = etsy.download_main_image(
        "https://i.etsystatic.com/123/main.png",
        tmp_path,
        FakeClient([FakeResponse(url="https://i.etsystatic.com/123/main.png", headers=headers, body=body)]),
    )
    second = etsy.download_main_image(
        "https://i.etsystatic.com/123/main.png",
        tmp_path,
        FakeClient([FakeResponse(url="https://i.etsystatic.com/123/main.png", headers=headers, body=body)]),
    )

    assert first.sha256 == second.sha256 == hashlib.sha256(first.path.read_bytes()).hexdigest()
    assert first.path == second.path
    with Image.open(first.path) as normalized:
        assert normalized.format == "JPEG"
        assert normalized.mode == "RGB"
        assert normalized.size == (4, 3)
        assert not normalized.getexif()


def test_download_rejects_cross_host_redirect_mime_signature_and_pixel_limit(tmp_path: Path, monkeypatch) -> None:
    redirect = FakeClient(
        [
            FakeResponse(
                status_code=302,
                headers={"location": "https://evil.example/tracker.png"},
                url="https://i.etsystatic.com/123/main.png",
            )
        ]
    )
    with pytest.raises(etsy.ImageEvidenceError, match="redirect"):
        etsy.download_main_image("https://i.etsystatic.com/123/main.png", tmp_path, redirect)

    bad_signature = FakeClient(
        [
            FakeResponse(
                headers={"content-type": "image/png", "content-length": "12"},
                body=b"not an image",
            )
        ]
    )
    with pytest.raises(etsy.ImageEvidenceError, match="decode"):
        etsy.download_main_image("https://i.etsystatic.com/123/main.png", tmp_path, bad_signature)

    jpeg = image_bytes(fmt="JPEG")
    mismatch = FakeClient(
        [
            FakeResponse(
                headers={"content-type": "image/png", "content-length": str(len(jpeg))},
                body=jpeg,
            )
        ]
    )
    with pytest.raises(etsy.ImageEvidenceError, match="type"):
        etsy.download_main_image("https://i.etsystatic.com/123/main.png", tmp_path, mismatch)

    monkeypatch.setattr(etsy, "MAX_IMAGE_PIXELS", 1)
    oversized_pixels = FakeClient(
        [
            FakeResponse(
                headers={"content-type": "image/png", "content-length": str(len(image_bytes()))},
                body=image_bytes(),
            )
        ]
    )
    with pytest.raises(etsy.ImageEvidenceError, match="pixel"):
        etsy.download_main_image("https://i.etsystatic.com/123/main.png", tmp_path, oversized_pixels)
