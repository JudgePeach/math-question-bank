"""Sharing validation keeps upload safety without recompressing retained bytes."""

import io

import pytest
from PIL import Image

from mathbank.asset_security import (
    InvalidImageError,
    normalize_raster_image,
    validate_raster_image,
)


def _image_bytes(format="PNG", mode="RGB", *, size=(32, 24)):
    image = Image.new(mode, size)
    if mode == "P":
        image.info["transparency"] = 0
    output = io.BytesIO()
    image.save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize(
    "format,mode",
    [
        ("PNG", "RGB"), ("PNG", "RGBA"), ("PNG", "LA"), ("PNG", "P"),
        ("PNG", "I;16"), ("JPEG", "RGB"), ("JPEG", "L"),
        ("GIF", "P"), ("WEBP", "RGB"), ("WEBP", "RGBA"),
    ],
)
def test_retained_image_validation_accepts_upload_formats_without_changing_bytes(format, mode):
    raw = _image_bytes(format, mode)
    original = bytes(raw)

    assert validate_raster_image(raw) is None
    assert normalize_raster_image(raw).width == 32
    assert raw == original


def test_retained_image_validation_keeps_exif_rotation_decode_and_upload_encoding(monkeypatch):
    image = Image.new("RGB", (32, 24), "white")
    exif = Image.Exif()
    exif[274] = 6
    output = io.BytesIO()
    image.save(output, format="JPEG", exif=exif)
    raw = output.getvalue()
    saved = []
    real_save = Image.Image.save

    def observe_save(image, target, *args, **kwargs):
        saved.append((image.size, kwargs["format"], kwargs["optimize"]))
        return real_save(image, target, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", observe_save)
    assert validate_raster_image(raw) is None
    normalized = normalize_raster_image(raw)

    assert saved == [((24, 32), "JPEG", False), ((24, 32), "JPEG", True)]
    with Image.open(io.BytesIO(normalized.data)) as decoded:
        assert decoded.size == (24, 32)
        assert decoded.getexif().get(274) is None


@pytest.mark.parametrize("raw", [b"", b"not an image", b"<svg></svg>"])
def test_retained_image_validation_rejects_empty_and_non_raster_bytes(raw):
    with pytest.raises(InvalidImageError):
        validate_raster_image(raw)


def test_retained_image_validation_rejects_decodable_unsupported_formats():
    with pytest.raises(InvalidImageError, match="仅支持"):
        validate_raster_image(_image_bytes("TIFF"))


@pytest.mark.parametrize("format", ["PNG", "JPEG", "GIF", "WEBP"])
def test_retained_image_validation_checks_real_decoding_and_rejects_truncation(format):
    image = Image.effect_noise((128, 128), 100).convert("RGB")
    output = io.BytesIO()
    image.save(output, format=format)
    incomplete = output.getvalue()[:len(output.getvalue()) // 2]

    # A readable header alone is insufficient; decoding must really finish.
    if format != "WEBP":  # The WebP decoder rejects this already during open.
        with Image.open(io.BytesIO(incomplete)) as header:
            assert header.size == (128, 128)
    with pytest.raises(InvalidImageError):
        validate_raster_image(incomplete)


def test_retained_image_validation_preserves_pixel_limit():
    with pytest.raises(InvalidImageError, match="像素尺寸"):
        validate_raster_image(_image_bytes(), max_pixels=32 * 24 - 1)


def test_retained_image_validation_falls_back_to_optimized_encoding_at_size_boundary(monkeypatch):
    image = Image.new("RGB", (128, 128), "white")
    slow, fast = io.BytesIO(), io.BytesIO()
    image.save(slow, format="PNG", optimize=True)
    image.save(fast, format="PNG", optimize=False)
    raw = slow.getvalue()
    assert len(raw) < len(fast.getvalue())
    encodings = []
    real_save = Image.Image.save

    def observe_save(image, target, *args, **kwargs):
        encodings.append(kwargs["optimize"])
        return real_save(image, target, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", observe_save)

    assert validate_raster_image(raw, max_output_bytes=len(raw)) is None
    assert encodings == [False, True]
    encodings.clear()
    with pytest.raises(InvalidImageError, match="安全转码后的体积"):
        validate_raster_image(raw, max_output_bytes=len(raw) - 1)
    assert encodings == [False, True]


def test_retained_image_validation_does_not_retry_malformed_images(monkeypatch):
    opens = []
    real_open = Image.open

    def observe_open(*args, **kwargs):
        opens.append(True)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(Image, "open", observe_open)
    with pytest.raises(InvalidImageError):
        validate_raster_image(b"invalid image")
    assert len(opens) == 1


def test_retained_image_validation_keeps_existing_complete_first_frame_contract():
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(
        output, format="GIF", save_all=True,
        append_images=[Image.new("RGB", (32, 32), "blue")], duration=30, loop=0,
    )
    raw = output.getvalue()
    with Image.open(io.BytesIO(raw)) as source:
        assert source.n_frames == 2
        source.seek(1)
        second_frame_data = source.fp.tell()

    # Uploads validate/decode the first frame only; sharing keeps that contract.
    for candidate in (raw, raw[:second_frame_data + 2]):
        assert normalize_raster_image(candidate).width == 32
        assert validate_raster_image(candidate) is None


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
def test_normal_upload_output_still_uses_original_optimized_encoding(format):
    image = Image.new("RGB", (32, 24), "white")
    raw = io.BytesIO()
    image.save(raw, format=format)
    expected = io.BytesIO()
    kwargs = {"format": format, "optimize": True}
    if format == "JPEG":
        kwargs["quality"] = 95
    image.save(expected, **kwargs)

    assert normalize_raster_image(raw.getvalue()).data == expected.getvalue()
