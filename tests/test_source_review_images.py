"""Actual-pixel evidence, task ownership and bounded failure isolation."""

import base64
from copy import deepcopy
import hashlib
import json

from PIL import Image
import pytest

from mathbank import source_review_images as evidence


@pytest.fixture
def assets(tmp_path):
    roots = {"uploads_dir": tmp_path / "uploads", "test_uploads_dir": tmp_path / "test_uploads"}
    for root in roots.values():
        (root / "tmp").mkdir(parents=True)

    def create(name="formula.png", *, testing=False, color="white", size=(32, 18), image_format=None):
        root = roots["test_uploads_dir" if testing else "uploads_dir"]
        path = root / "tmp" / name
        Image.new("RGB", size, color).save(path, format=image_format)
        return f"/static/{'test_uploads' if testing else 'uploads'}/tmp/{name}", path

    return roots, create


def item(identifier="q1", content="无图题", answer="", **extra):
    return {"id": identifier, "output": {"content": content, "answer_markdown": answer}, **extra}


def prepare(assets, items, allowed):
    return evidence.prepare_candidate_images(items, allowed_paths=allowed, **assets[0])


def test_no_image_does_not_read_hidden_reference_images(assets, monkeypatch):
    url, _ = assets[1]()
    def no_read(*args):
        raise AssertionError("Unreferenced assets must not be read")
    monkeypatch.setattr(evidence, "_read_image", no_read)
    result = prepare(assets, [item(referenced_images=[url])], [url])
    assert result["per_item"] == {"q1": {"images": [], "complete": True, "reasons": []}}
    assert result["messages"] == []


@pytest.mark.parametrize("name,media_type", [("formula.png", "image/png"), ("plot.jpg", "image/jpeg"),
                                            ("plot.jpeg", "image/jpeg"), ("plot.webp", "image/webp"),
                                            ("plot.gif", "image/gif")])
def test_actual_image_bytes_hash_and_type_are_bound(assets, name, media_type):
    url, path = assets[1](name)
    items = [item(content=f"条件 ![公式]({url}) 成立。")]
    before = deepcopy(items)
    result = prepare(assets, items, [url])
    image = result["per_item"]["q1"]["images"][0]
    assert items == before
    assert result["per_item"]["q1"]["complete"]
    assert image["path"] == url and image["width"] == 32 and image["height"] == 18
    assert image["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert image["media_type"] == media_type
    data_url = result["messages"][1]["image_url"]["url"]
    assert data_url.startswith(f"data:{media_type};base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == path.read_bytes()


@pytest.mark.parametrize("kind,uniform", [("white", True), ("black", True), ("transparent", True),
                                         ("invisible_pixel", True), ("white_alpha", True),
                                         ("single_pixel", False)])
def test_uniform_metadata_uses_exact_pixels_on_white_and_does_not_reject_image(assets, kind, uniform):
    url, path = assets[1]()
    picture = Image.new("RGBA", (32, 18), (255, 255, 255, 255))
    if kind == "black":
        picture = Image.new("RGBA", (32, 18), (0, 0, 0, 255))
    elif kind in {"transparent", "invisible_pixel"}:
        picture = Image.new("RGBA", (32, 18), (0, 0, 0, 0))
        if kind == "invisible_pixel":
            picture.putpixel((1, 1), (120, 3, 45, 0))
    elif kind == "white_alpha":
        picture.putpixel((1, 1), (255, 255, 255, 22))
    elif kind == "single_pixel":
        picture.putpixel((1, 1), (254, 255, 255, 255))
    picture.save(path)
    result = prepare(assets, [item(content=f"![图]({url})")], [url])
    assert result["per_item"]["q1"]["complete"]
    assert result["per_item"]["q1"]["images"][0]["is_uniform"] is uniform
    assert len(result["messages"]) == 2


def test_same_image_is_sent_once_with_every_item_field_and_occurrence(assets):
    url, _ = assets[1](testing=True)
    items = [item(content=f"![公式]({url}) 再次 ![公式](<{url}> \"标题\")", answer=rf"答案 \includegraphics[width=1cm]{{{url}}}"),
             item("q2", rf"\includegraphics* {{{url}}}")]
    result = prepare(assets, items, [url])
    assert all(value["complete"] for value in result["per_item"].values())
    assert len(result["messages"]) == 2
    assert len(result["per_item"]["q1"]["images"]) == 1
    expected = [{"id": "q1", "field": "content", "occurrence": 1},
                {"id": "q1", "field": "content", "occurrence": 2},
                {"id": "q1", "field": "answer_markdown", "occurrence": 1},
                {"id": "q2", "field": "content", "occurrence": 1}]
    label = json.loads(result["messages"][0]["text"].split("：", 1)[1])
    assert label["bindings"] == expected
    assert result["per_item"]["q1"]["images"][0]["bindings"] == expected[:3]
    assert result["per_item"]["q2"]["images"][0]["bindings"] == expected[3:]


@pytest.mark.parametrize("reference", [
    "https://example.com/a.png", "file:///tmp/a.png", "data:image/png;base64,AAAA",
    "/static/uploads/library.png", "/static/uploads/tmp/missing.png",
    "/static/uploads/tmp/../secret.png", "/static/uploads/tmp/a.svg",
    "/static/uploads/tmp/a.png?secret=1", "/static/uploads/tmp/a.png#fragment",
])
def test_unsafe_missing_and_non_task_images_do_not_discard_valid_other_items(assets, reference):
    good, _ = assets[1]()
    result = prepare(assets, [item(content=f"![图]({reference})"), item("q2", f"![图]({good})")], [reference, good])
    assert not result["per_item"]["q1"]["complete"]
    assert result["per_item"]["q1"]["reasons"]
    assert result["per_item"]["q2"]["complete"]
    assert len(result["messages"]) == 2
    assert reference not in result["messages"][0]["text"]


def test_allowed_paths_does_not_implicitly_authorize_other_task_or_alias(assets):
    url, _ = assets[1]()
    for allowed in [[], [url.lstrip("/")], ["/static/uploads/tmp/other.png"]]:
        result = prepare(assets, [item(content=f"![图]({url})")], allowed)
        assert not result["per_item"]["q1"]["complete"]
        assert result["messages"] == []


def test_symlink_even_inside_root_is_rejected(assets):
    url, path = assets[1]()
    link = path.with_name("link.png")
    link.symlink_to(path)
    linked = url.replace("formula.png", "link.png")
    result = prepare(assets, [item(content=f"![图]({linked})")], [linked])
    assert not result["per_item"]["q1"]["complete"] and not result["messages"]


def test_symlink_directory_cannot_supply_another_tasks_asset(assets):
    _, path = assets[1]()
    other_task = path.parent / "other_task"
    other_task.mkdir()
    path.rename(other_task / path.name)
    (path.parent / "current_task").symlink_to(other_task, target_is_directory=True)
    url = "/static/uploads/tmp/current_task/formula.png"
    result = prepare(assets, [item(content=f"![图]({url})")], [url])
    assert not result["per_item"]["q1"]["complete"] and not result["messages"]


@pytest.mark.parametrize("kind", ["corrupt", "truncated", "wrong_format", "animated", "pixel_limit", "byte_limit"])
def test_invalid_raster_evidence_cannot_be_certified(assets, monkeypatch, kind):
    url, path = assets[1]()
    if kind == "corrupt":
        path.write_bytes(b"\x89PNG\r\n\x1a\nThis is not PNG")
    elif kind == "truncated":
        path.write_bytes(path.read_bytes()[:-15])
    elif kind == "wrong_format":
        Image.new("RGB", (32, 18)).save(path, format="JPEG")
    elif kind == "animated":
        Image.new("RGB", (32, 18), "white").save(path, save_all=True,
                                                append_images=[Image.new("RGB", (32, 18), "black")])
    elif kind == "pixel_limit":
        monkeypatch.setattr(evidence, "MAX_IMAGE_PIXELS", 575)
    else:
        monkeypatch.setattr(evidence, "MAX_IMAGE_BYTES", path.stat().st_size - 1)
    result = prepare(assets, [item(content=f"![图]({url})")], [url])
    assert not result["per_item"]["q1"]["complete"]
    assert not result["messages"]


def test_item_limit_does_not_take_other_item_budget_and_repeated_refs_do_not_count_twice(assets):
    urls = [assets[1](f"p{index}.png")[0] for index in range(evidence.MAX_ITEM_IMAGES + 1)]
    result = prepare(assets, [item(content=" ".join(f"![图]({url})" for url in urls)),
                              item("q2", f"![图]({urls[0]}) " * 10)], urls)
    assert not result["per_item"]["q1"]["complete"]
    assert result["per_item"]["q2"]["complete"]
    assert len(result["messages"]) == 2
    assert len(result["per_item"]["q2"]["images"][0]["bindings"]) == 10


def test_excessive_duplicate_bindings_are_bounded_and_explicit(assets):
    url, _ = assets[1]()
    result = prepare(assets, [item(content=f"![图]({url}) " * 1000)], [url])
    assert not result["per_item"]["q1"]["complete"]
    assert result["messages"] == []


def test_missing_platform_open_flags_remain_supported(assets, monkeypatch):
    url, _ = assets[1]()
    monkeypatch.delattr(evidence.os, "O_NOFOLLOW", raising=False)
    monkeypatch.delattr(evidence.os, "O_BINARY", raising=False)
    result = prepare(assets, [item(content=f"![图]({url})")], [url])
    assert result["per_item"]["q1"]["complete"]


def test_descriptor_is_closed_when_wrapping_the_stream_fails(assets, monkeypatch):
    url, _ = assets[1]()
    descriptors = []
    original_open = evidence.os.open
    def capture_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor
    def broken_fdopen(*args, **kwargs):
        raise OSError("cannot wrap descriptor")
    monkeypatch.setattr(evidence.os, "open", capture_open)
    monkeypatch.setattr(evidence.os, "fdopen", broken_fdopen)
    result = prepare(assets, [item(content=f"![图]({url})")], [url])
    assert not result["per_item"]["q1"]["complete"]
    with pytest.raises(OSError):
        evidence.os.fstat(descriptors[0])


@pytest.mark.parametrize("limit", ["count", "bytes", "pixels", "labels"])
def test_batch_limit_is_explicit_and_local_to_affected_item(assets, monkeypatch, limit):
    first, path = assets[1]()
    second, _ = assets[1]("other.png")
    first_result = prepare(assets, [item(content=f"![图]({first})")], [first])
    limits = {"count": ("MAX_BATCH_IMAGES", 1), "bytes": ("MAX_BATCH_BYTES", path.stat().st_size),
              "pixels": ("MAX_BATCH_PIXELS", 32 * 18),
              "labels": ("MAX_BATCH_LABEL_CHARS", len(first_result["messages"][0]["text"]))}
    monkeypatch.setattr(evidence, *limits[limit])
    result = prepare(assets, [item(content=f"![图]({first})"), item("q2", f"![图]({second})")], [first, second])
    assert result["per_item"]["q1"]["complete"]
    assert not result["per_item"]["q2"]["complete"]
    assert len(result["messages"]) == 2


def test_changed_bytes_rebinding_or_deletion_invalidates_snapshot(assets):
    url, path = assets[1]()
    original = [item(content=f"![图]({url})")]
    before = prepare(assets, original, [url])
    assert prepare(assets, original, [url])["fingerprint"] == before["fingerprint"]
    moved = prepare(assets, [item(answer=f"![图]({url})")], [url])
    assert moved["fingerprint"] != before["fingerprint"]
    Image.new("RGB", (32, 18), "black").save(path)
    assert prepare(assets, original, [url])["fingerprint"] != before["fingerprint"]
    path.unlink()
    missing = prepare(assets, original, [url])
    assert missing["fingerprint"] != before["fingerprint"] and not missing["per_item"]["q1"]["complete"]


def test_replacement_during_read_is_rejected(assets, monkeypatch):
    url, path = assets[1]()
    original_resolve = evidence.resolve_upload_asset
    calls = 0
    def replace_after_read(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            Image.new("RGB", (33, 18), "black").save(path)
        return original_resolve(*args, **kwargs)
    monkeypatch.setattr(evidence, "resolve_upload_asset", replace_after_read)
    result = prepare(assets, [item(content=f"![图]({url})")], [url])
    assert not result["per_item"]["q1"]["complete"] and result["messages"] == []
    assert "变化" in result["per_item"]["q1"]["reasons"][0]


@pytest.mark.parametrize("text", ["![图][ref]", "![图](", r"\includegraphics{", '<img src="image.png">'])
def test_unreadable_image_reference_never_claims_complete(assets, text):
    assert not prepare(assets, [item(content=text)], [])["per_item"]["q1"]["complete"]


def test_literal_code_and_escaped_markdown_do_not_supply_image_evidence(assets):
    url, _ = assets[1]()
    text = f"`![图]({url})`\n```tex\n\\includegraphics{{{url}}}\n```\n\\![图]({url})"
    result = prepare(assets, [item(content=text)], [url])
    assert result["per_item"]["q1"]["complete"] and not result["messages"]


@pytest.mark.parametrize("wrapper", [
    r"\verb|BODY|", r"\verb*+BODY+", r"\Verb|BODY|", r"\Verb[formatcom=\color{red}]|BODY|",
    r"\lstinline|BODY|", r"\lstinline[language=tex]{BODY}", r"\detokenize{BODY}",
    r"\mintinline{latex}{BODY}", r"\mintinline[breaklines]{latex}|BODY|",
    r"\mintinline[breaklines] {tex} {BODY}",
    r"\url{BODY}", r"\url|BODY|", r"\path{BODY}", r"\path|BODY|", "<!-- BODY -->",
    "`BODY`", "```tex\nBODY\n```", "~~~\nBODY\n~~~",
])
def test_literal_macros_and_comments_hide_images_and_diagnostic_markers_at_original_offsets(assets, wrapper):
    url, _ = assets[1]()
    literal = rf"![公式]({url}) \includegraphics{{{url}}} [公式结构待核对]"
    text = wrapper.replace("BODY", literal) + "\n可见 $x$ [公式结构待核对]"
    masked = evidence._visible_text(text)
    assert len(masked) == len(text)
    assert [index for index, char in enumerate(masked) if char == "\n"] == [
        index for index, char in enumerate(text) if char == "\n"]
    assert masked.index("[公式结构待核对]") == text.rindex("[公式结构待核对]")
    assert masked.count("[公式结构待核对]") == 1
    result = prepare(assets, [item(content=text)], [url])
    assert result["per_item"]["q1"]["complete"] and not result["messages"]


def test_literal_processing_does_not_escape_quoted_boundaries_or_hide_unknown_macros(assets):
    url, _ = assets[1]()
    text = "`\\detokenize{` 后面 " + rf"\unknownmacro{{![图]({url}) [公式结构待核对]}}"
    masked = evidence._visible_text(text)
    assert masked.count("[公式结构待核对]") == 1
    result = prepare(assets, [item(content=text)], [url])
    assert result["per_item"]["q1"]["complete"] and len(result["messages"]) == 2


@pytest.mark.parametrize("literal", ["<!-- ![x](hidden.png) [公式结构待核对]",
                                     r"\detokenize{![x](hidden.png) [公式结构待核对]",
                                     r"\mintinline{latex}{![x](hidden.png) [公式结构待核对]",
                                     r"\verb|![x](hidden.png) [公式结构待核对]"])
def test_unclosed_literals_do_not_expose_cleanup_markers_or_hidden_image_paths(assets, literal):
    assert "[公式结构待核对]" not in evidence._visible_text(literal)
    result = prepare(assets, [item(content=literal)], [])
    assert result["per_item"]["q1"]["complete"] and not result["messages"]


@pytest.mark.parametrize("environment", ["verbatim", "verbatim*", "Verbatim", "Verbatim*", "lstlisting", "minted"])
@pytest.mark.parametrize("closed", [False, True])
def test_tex_literal_environments_never_supply_images_or_cleanup_markers(assets, environment, closed):
    url, _ = assets[1]()
    diagnostic = f"[公式待核对]\n![MathType 公式待核对]({url})"
    suffix = (f"\n\\end{{{environment}}}\n可见 [公式结构待核对] ![实际图片]({url})" if closed else "")
    options = "[breaklines]{latex}" if environment == "minted" else ""
    text = (f"\\begin{{{environment}}}{options}\n" + diagnostic
            + f"\n\\includegraphics{{{url}}}\n<!-- 尚未闭合注释 `任意代码" + suffix)
    masked = evidence._visible_text(text)
    assert len(masked) == len(text)
    assert [i for i, char in enumerate(masked) if char == "\n"] == [i for i, char in enumerate(text) if char == "\n"]
    assert "[公式待核对]" not in masked and "[MathType 公式待核对]" not in masked
    if closed:
        assert masked.index("[公式结构待核对]") == text.index("[公式结构待核对]")
    result = prepare(assets, [item(content=text)], [url])
    assert result["per_item"]["q1"]["complete"]
    assert len(result["messages"]) == (2 if closed else 0)
    if closed:
        assert result["per_item"]["q1"]["images"][0]["bindings"] == [
            {"id": "q1", "field": "content", "occurrence": 1}]


@pytest.mark.parametrize("wrapper", [r"\mintinline{latex}{BODY}", r"\mintinline[breaklines]{latex}|BODY|"])
def test_mintinline_mathtype_preview_is_only_literal_text(assets, wrapper):
    url, _ = assets[1]()
    text = wrapper.replace("BODY", f"[公式待核对]\n![MathType 公式待核对]({url})")
    # Delimiter-form inline verbatim does not continue across a physical line.
    if "|BODY|" in wrapper:
        text = text.replace("\n", " ")
    masked = evidence._visible_text(text)
    assert "[公式待核对]" not in masked and "[MathType 公式待核对]" not in masked
    result = prepare(assets, [item(content=text)], [url])
    assert result["per_item"]["q1"]["complete"] and not result["messages"]


def test_unknown_environment_does_not_hide_actual_image(assets):
    url, _ = assets[1]()
    text = rf"\begin{{myenvironment}} ![图]({url}) [公式结构待核对] \end{{myenvironment}}"
    assert "[公式结构待核对]" in evidence._visible_text(text)
    result = prepare(assets, [item(content=text)], [url])
    assert result["per_item"]["q1"]["complete"] and len(result["messages"]) == 2


def test_nontext_output_is_incomplete_but_other_item_survives(assets):
    broken = item()
    broken["output"]["content"] = {"image": "hidden.png"}
    result = prepare(assets, [broken, item("q2")], [])
    assert not result["per_item"]["q1"]["complete"] and result["per_item"]["q2"]["complete"]


@pytest.mark.parametrize("items", [[item(), item()], [item(None)], [item(123)], [item({})]])
def test_ambiguous_item_ids_are_programming_errors(assets, items):
    with pytest.raises(ValueError, match="unique"):
        prepare(assets, items, [])


@pytest.mark.parametrize("count", [7, 17, 32])
def test_many_small_formula_images_remain_one_complete_original_pixel_set(assets, count):
    files = [assets[1](f"formula_{index}.png", size=(80 + index, 24)) for index in range(count)]
    urls = [url for url, _ in files]
    result = prepare(assets, [item(content="，".join(f"![公式]({url})" for url in urls))], urls)
    candidate = result["per_item"]["q1"]
    assert candidate["complete"] and not candidate["reasons"]
    assert len(candidate["images"]) == count
    assert len(result["messages"]) == count * 2
    assert sum(len(part.get("text", "")) for part in result["messages"]) <= evidence.MAX_ITEM_LABEL_CHARS
    for index, (image, (_, path)) in enumerate(zip(candidate["images"], files)):
        assert (image["width"], image["height"]) == (80 + index, 24)
        assert image["bindings"] == [{"id": "q1", "field": "content", "occurrence": index + 1}]
        supplied = result["messages"][index * 2 + 1]["image_url"]["url"]
        assert base64.b64decode(supplied.split(",", 1)[1]) == path.read_bytes()


@pytest.mark.parametrize("resource", ["bytes", "pixels", "labels"])
def test_item_resource_overflow_is_atomic_and_does_not_consume_peer_budget(assets, monkeypatch, resource):
    first, first_path = assets[1]("a.png")
    second, _ = assets[1]("b.png")
    single = prepare(assets, [item(content=f"![图]({first})")], [first])
    limits = {"bytes": ("MAX_ITEM_BYTES", first_path.stat().st_size),
              "pixels": ("MAX_ITEM_PIXELS", 32 * 18),
              "labels": ("MAX_ITEM_LABEL_CHARS", len(single["messages"][0]["text"]))}
    monkeypatch.setattr(evidence, *limits[resource])
    # q1 would individually consume both paths, but must supply neither. q2
    # can use the same first path once without inheriting q1's failed budget.
    result = prepare(assets, [item(content=f"![图]({first}) ![图]({second})"),
                              item("q2", f"![图]({first})")], [first, second])
    assert not result["per_item"]["q1"]["complete"]
    assert result["per_item"]["q1"]["images"] == []
    assert evidence._REASONS["item_resources"] in result["per_item"]["q1"]["reasons"]
    assert result["per_item"]["q2"]["complete"] and len(result["messages"]) == 2
    label = json.loads(result["messages"][0]["text"].split("：", 1)[1])
    assert label["bindings"] == [{"id": "q2", "field": "content", "occurrence": 1}]
    assert second not in result["messages"][0]["text"]


def test_small_compressed_bytes_do_not_hide_large_total_decoded_pixels(assets):
    files = [assets[1](f"large{index}.png", size=(2000, 2000)) for index in range(3)]
    urls = [url for url, _ in files]
    assert sum(path.stat().st_size for _, path in files) < evidence.MAX_ITEM_BYTES
    result = prepare(assets, [item(content=" ".join(f"![图]({url})" for url in urls))], urls)
    assert not result["per_item"]["q1"]["complete"]
    assert result["messages"] == [] and result["per_item"]["q1"]["images"] == []
    assert evidence._REASONS["item_resources"] in result["per_item"]["q1"]["reasons"]


def test_shared_image_deduplicates_batch_resources_but_retains_every_binding(assets, monkeypatch):
    url, path = assets[1]()
    monkeypatch.setattr(evidence, "MAX_ITEM_BYTES", path.stat().st_size)
    monkeypatch.setattr(evidence, "MAX_ITEM_PIXELS", 32 * 18)
    monkeypatch.setattr(evidence, "MAX_BATCH_BYTES", path.stat().st_size)
    monkeypatch.setattr(evidence, "MAX_BATCH_PIXELS", 32 * 18)
    reads = []
    original = evidence._read_image
    def capture(*args):
        reads.append(args[0])
        return original(*args)
    monkeypatch.setattr(evidence, "_read_image", capture)
    result = prepare(assets, [item(content=f"![图]({url}) " * 5),
                              item("q2", answer=f"![图]({url})")], [url])
    assert all(value["complete"] for value in result["per_item"].values())
    assert reads == [url] and len(result["messages"]) == 2
    label = json.loads(result["messages"][0]["text"].split("：", 1)[1])
    assert len(label["bindings"]) == 6


def test_batch_overflow_keeps_whole_item_unsent_and_preserves_shared_peer(assets, monkeypatch):
    first, _ = assets[1]("first.png")
    second, _ = assets[1]("second.png")
    third, _ = assets[1]("third.png")
    monkeypatch.setattr(evidence, "MAX_BATCH_PIXELS", 2 * 32 * 18)
    result = prepare(assets, [item(content=f"![图]({first})"),
                              item("q2", f"![图]({second}) ![图]({third})"),
                              item("q3", f"![图]({second})")], [first, second, third])
    assert result["per_item"]["q1"]["complete"] and result["per_item"]["q3"]["complete"]
    assert not result["per_item"]["q2"]["complete"] and result["per_item"]["q2"]["images"] == []
    assert len(result["messages"]) == 4
    assert "q2" not in "".join(part.get("text", "") for part in result["messages"])


def test_missing_one_of_many_small_images_never_yields_partial_question_evidence(assets):
    urls = [assets[1](f"formula{index}.png")[0] for index in range(20)]
    missing = "/static/uploads/tmp/formula_missing.png"
    result = prepare(assets, [item(content=" ".join(f"![公式]({url})" for url in [*urls, missing])),
                              item("q2", f"![图]({urls[0]})")], [*urls, missing])
    assert not result["per_item"]["q1"]["complete"] and not result["per_item"]["q1"]["images"]
    assert result["per_item"]["q2"]["complete"] and len(result["messages"]) == 2


def test_repeated_bindings_consume_label_budget_even_without_extra_pixels(assets, monkeypatch):
    url, _ = assets[1]()
    single = prepare(assets, [item(content=f"![图]({url})")], [url])
    monkeypatch.setattr(evidence, "MAX_ITEM_LABEL_CHARS", len(single["messages"][0]["text"]))
    result = prepare(assets, [item(content=f"![图]({url}) " * 10)], [url])
    assert not result["per_item"]["q1"]["complete"] and not result["messages"]
    assert evidence._REASONS["item_resources"] in result["per_item"]["q1"]["reasons"]
