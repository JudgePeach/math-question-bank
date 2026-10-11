"""Pure single-question figure protocol and crop tests; no app/database import."""

import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from mathbank.ocr_illustrations import materialize_ocr_illustrations, parse_single_ocr_response
from mathbank.prompts import build_single_ocr_vision_prompt


CODE = r"\begin{tikzpicture}\draw (0,0)--(1,1);\end{tikzpicture}"


def figure(slot="图1", bbox=None, **extra):
    return {"slot": slot, "bbox": bbox or {"left": 100, "top": 100, "right": 300, "bottom": 400}, **extra}


def parsed(markdown="题干[插图待补: 图1]后文", figures=None):
    return parse_single_ocr_response({"markdown": markdown, "figures": [figure()] if figures is None else figures})


def materialize(result, tmp_path, *, skip=False, draw=None, compile_tikz=None):
    image = Image.new("RGB", (1000, 800), "white")
    def compile_default(_code):
        path = tmp_path / "tikz_test.png"
        image.save(path)
        return "/static/uploads/" + path.name
    return materialize_ocr_illustrations(result, image, skip_tikz=skip,
        upload_root=tmp_path, url_prefix="static/uploads", draw=draw or (lambda *_args: CODE),
        compile_tikz=compile_tikz or compile_default)


def test_single_prompt_requests_all_named_original_figure_slots_once():
    prompt = build_single_ocr_vision_prompt()
    assert "四图选项每幅图放入各自的\\item" in prompt
    assert "{left,top,right,bottom}" in prompt
    assert "0–1000" in prompt
    assert "不能把图都放到题末" in prompt
    assert "无法可靠定位时bbox为null" in prompt


def test_disabled_drawing_automatically_inserts_source_crop_without_drawing_or_compiling(tmp_path):
    fail = lambda *_args: pytest.fail("must not draw or compile")
    result = materialize(parsed(), tmp_path, skip=True, draw=fail, compile_tikz=fail)
    item = result["illustrations"][0]
    assert result["markdown"] == f"题干![]({item['image_path']})后文"
    assert item["status"] == "cropped"
    assert result["illustrations"][0]["bbox"]["left"] == 100
    assert result["tikz_assets"] == [] and result["image_layouts"] == {}
    assert not result["warnings"]
    assert item["tikz_code"] == item["tikz_image_path"] == item["reference_image_path"] == ""
    with Image.open(tmp_path / Path(item["image_path"]).name) as crop:
        assert crop.size == (200, 240)


def test_disabled_drawing_crops_four_options_and_table_cell_from_exact_source_pixels(tmp_path):
    markdown = r"题干\begin{choices}" + "".join(
        r"\item [插图待补: 图" + str(index) + "]" for index in range(1, 5)
    ) + r"\end{choices}\begin{tabular}{cc}说明 & [插图待补: 图5]\end{tabular}后文"
    boxes = [{"left": index * 200 - 150, "top": 200, "right": index * 200, "bottom": 400}
             for index in range(1, 5)] + [{"left": 100, "top": 500, "right": 300, "bottom": 800}]
    source = Image.new("RGB", (1000, 800), "white")
    painter = ImageDraw.Draw(source)
    pixels = []
    for bbox, color in zip(boxes, ("red", "green", "blue", "black", "orange")):
        box = (bbox["left"], bbox["top"] * 800 // 1000, bbox["right"], bbox["bottom"] * 800 // 1000)
        pixels.append(box)
        painter.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=color)
    fail = lambda *_args: pytest.fail("must not draw or compile")
    result = materialize_ocr_illustrations(
        parsed(markdown, [figure("图" + str(index), bbox) for index, bbox in enumerate(boxes, 1)]),
        source, skip_tikz=True, upload_root=tmp_path, url_prefix="static/uploads", draw=fail, compile_tikz=fail,
    )
    assert len(list(tmp_path.iterdir())) == 5
    assert result["markdown"].count(r"\item ![](") == 4
    assert "插图待补" not in result["markdown"] and result["markdown"].endswith(r"\end{tabular}后文")
    paths = [item["image_path"] for item in result["illustrations"]]
    assert [result["markdown"].index(path) for path in paths] == sorted(result["markdown"].index(path) for path in paths)
    assert f"说明 & ![]({paths[-1]})" in result["markdown"]
    assert not result["tikz_assets"] and not result["warnings"]
    assert all(layout == {"align": "left", "size": "small"} for layout in result["image_layouts"].values())
    for item, box in zip(result["illustrations"], pixels):
        assert item["status"] == "cropped" and item["reference_image_path"] == item["tikz_code"] == ""
        with Image.open(tmp_path / Path(item["image_path"]).name) as crop:
            assert crop.size == source.crop(box).size
            assert crop.tobytes() == source.crop(box).tobytes()


def test_disabled_drawing_keeps_uncertain_slot_while_cropping_reliable_slot(tmp_path):
    markdown = "前文[插图待补: 图1]后文[插图待补: 图2]末尾"
    result = materialize(parsed(markdown, [figure(), figure("图2",
        {"left": 500, "top": 500, "right": 800, "bottom": 800},
        review_required=True, review_reason="图注边界不明确")]), tmp_path, skip=True,
        draw=lambda *_args: pytest.fail("must not draw"), compile_tikz=lambda *_args: pytest.fail("must not compile"))
    assert result["markdown"].startswith("前文![](")
    assert result["markdown"].endswith("后文[插图待补: 图2]末尾")
    assert [item["status"] for item in result["illustrations"]] == ["cropped", "unresolved"]
    assert len(list(tmp_path.iterdir())) == 1 and result["warnings"]


def test_crop_filename_collision_does_not_remove_existing_asset(tmp_path, monkeypatch):
    recognized = parsed()
    existing = tmp_path / "ocr_figure_collision.png"
    existing.write_bytes(b"existing question asset")
    monkeypatch.setattr("mathbank.ocr_illustrations.uuid.uuid4", lambda: SimpleNamespace(hex="collision"))
    result = materialize(recognized, tmp_path, skip=True, draw=lambda *_args: pytest.fail("must not draw"))
    assert existing.read_bytes() == b"existing question asset"
    assert result["markdown"] == recognized["markdown"]
    assert result["illustrations"][0]["status"] == "unresolved"
    assert result["warnings"]


def test_four_option_figures_keep_source_order_and_independent_small_sizes(tmp_path):
    markdown = r"题干\begin{choices}" + "".join(r"\item [插图待补: 图" + str(index) + "]" for index in range(1, 5)) + r"\end{choices}后文"
    figures = [figure("图" + str(index), {"left": index * 200 - 150, "top": 200,
               "right": index * 200, "bottom": 400}) for index in range(1, 5)]
    observed = []
    def draw(crop_path, context, instruction):
        crop = Image.open(crop_path)
        assert crop.size == (150, 160)  # The entire screenshot is never sent.
        assert context == markdown and "不要绘制其他插图" in instruction
        observed.append(Path(crop_path))
        return CODE
    def compile_one(_code):
        path = tmp_path / f"tikz_{len(observed)}.png"
        Image.new("RGB", (120, 120), "white").save(path)
        return "/static/uploads/" + path.name
    result = materialize(parsed(markdown, figures), tmp_path, draw=draw, compile_tikz=compile_one)
    assert len(observed) == len(result["tikz_assets"]) == 4
    assert result["markdown"].endswith(r"\end{choices}后文")
    assert result["markdown"].count(r"\item ![](") == 4
    assert "插图待补" not in result["markdown"]
    assert all(setting == {"align": "left", "size": "small"} for setting in result["image_layouts"].values())
    for index, asset in enumerate(result["tikz_assets"], 1):
        assert asset["image_path"] == f"/static/uploads/tikz_{index}.png"
        assert asset["reference_image_path"].endswith(observed[index - 1].name)
        assert asset["reference_image_path"] not in result["markdown"]


@pytest.mark.parametrize("failure", ["draw", "compile", "missing_render"])
def test_failed_tikz_keeps_valid_original_crop_at_original_table_position(tmp_path, failure):
    markdown = r"\begin{tabular}{cc}说明 & [插图待补: 图1] \\ \end{tabular}后文"
    def draw(*_args):
        if failure == "draw":
            return None
        return CODE
    def compile_one(_code):
        if failure == "missing_render":
            return "/static/uploads/nonexistent.png"
        raise RuntimeError("compiler diagnostic with private path")
    result = materialize(parsed(markdown), tmp_path, draw=draw, compile_tikz=compile_one)
    item = result["illustrations"][0]
    assert item["status"] == "cropped"
    assert f"说明 & ![]({item['image_path']})" in result["markdown"]
    assert result["markdown"].endswith(r"\end{tabular}后文")
    assert result["image_layouts"][item["image_path"]]["size"] == "small"
    assert result["tikz_assets"] == [] and item["tikz_code"] == ""
    assert any("TikZ重绘或编译失败" in message for message in result["warnings"])
    assert "private path" not in " ".join(result["warnings"])


@pytest.mark.parametrize("bbox", [
    {"left": -1, "top": 0, "right": 100, "bottom": 100},
    {"left": 0, "top": 0, "right": 1000, "bottom": 1000},
    {"left": False, "top": 0, "right": 100, "bottom": 100},
    {"left": float("nan"), "top": 0, "right": 100, "bottom": 100},
    [10, 20, 90, 80], None,
])
@pytest.mark.parametrize("skip", [False, True])
def test_invalid_or_ambiguous_rectangles_never_draw_or_remove_placeholder(tmp_path, bbox, skip):
    result = materialize(parsed(figures=[{"slot": "图1", "bbox": bbox}]), tmp_path,
                         skip=skip, draw=lambda *_args: pytest.fail("must not draw"))
    assert result["markdown"] == "题干[插图待补: 图1]后文"
    assert result["illustrations"][0]["status"] == "unresolved"
    assert result["warnings"] and not list(tmp_path.iterdir())


@pytest.mark.parametrize("markdown,figures", [
    ("[插图待补: 图1]与[插图待补: 图1]", [figure()]),
    ("[插图待补: 图1]", [figure(), figure()]),
    ("[插图待补: 图1]", [figure("图2")]),
    ("[插图待补: 图1]与[插图待补: 图2]", [figure(), figure("图2")]),
    ("[插图待补: 图1]", [figure(review_required=True, review_reason="坐标不清楚")]),
])
@pytest.mark.parametrize("skip", [False, True])
def test_duplicate_or_missing_bindings_preserve_all_manual_slots(tmp_path, markdown, figures, skip):
    result = materialize(parsed(markdown, figures), tmp_path, skip=skip, draw=lambda *_args: pytest.fail("must not draw"))
    assert result["markdown"] == markdown
    assert all(item["status"] == "unresolved" for item in result["illustrations"])
    assert result["warnings"]


def test_literal_example_placeholders_do_not_bind_or_block_visible_unique_slot(tmp_path):
    markdown = "`[插图待补: 图1]`是示例。正文[插图待补: 图1]后文"
    result = materialize(parsed(markdown), tmp_path)
    assert result["markdown"].startswith("`[插图待补: 图1]`是示例。正文![](")
    assert len(result["tikz_assets"]) == 1


def test_text_table_outer_math_shell_is_repaired_before_binding_figures(tmp_path):
    table = r"\begin{tabular}{cc}$x_1$ & [插图待补: 图1] \\ \end{tabular}"
    result = materialize(parsed("$$" + table + "$$"), tmp_path)
    assert result["markdown"].startswith(r"\begin{tabular}{cc}$x_1$ & ![](")
    assert result["markdown"].endswith(r"\\ \end{tabular}")
    assert len(result["tikz_assets"]) == 1


def test_valid_text_survives_invalid_figure_json_without_extra_provider_request():
    markdown = r"题干 $x_1\leqslant2$ [插图待补: 图1]"
    raw = '{"markdown":' + json.dumps(markdown, ensure_ascii=False) + ',"figures": BROKEN}'
    result = parse_single_ocr_response(raw)
    assert result["markdown"] == markdown
    assert result["warnings"] and result["illustrations"][0]["status"] == "unresolved"


@pytest.mark.parametrize("source", [r"正文 $\frac{1}{2}$ [插图待补: 图1]", r"填空 \fillin [插图待补: 图1]",
    r"\begin{choices}\item $\frac{1}{2}$\item B\end{choices} [插图待补: 图1]"])
def test_invalid_figure_json_recovers_complete_raw_latex_without_control_characters(source):
    raw = '{"markdown":"' + source + '","figures": BROKEN}'
    result = parse_single_ocr_response(raw)
    assert result["markdown"] == source
    assert "\x0c" not in result["markdown"] and "\x08" not in result["markdown"]
    assert result["warnings"]


@pytest.mark.parametrize("bad_figures", ["BROKEN", "[]"])
def test_raw_fraction_cannot_be_silently_consumed_even_when_full_json_parses(bad_figures):
    source = r"正文 $\frac{1}{2}$ [插图待补: 图1]"
    result = parse_single_ocr_response('{"markdown":"' + source + '","figures":' + bad_figures + '}')
    assert result["markdown"] == source
    assert result["warnings"]


def test_malformed_figure_json_preserves_escaped_latex_and_normal_json_newline_tab():
    source = "首行\nnext\ttext\rrest $\\frac{1}{2}$ \\fillin [插图待补: 图1]"
    raw = '{"markdown":' + json.dumps(source, ensure_ascii=False) + ',"figures": BROKEN}'
    assert parse_single_ocr_response(raw)["markdown"] == source


def test_raw_fraction_repair_does_not_change_other_valid_json_newline_tab_escapes():
    raw = r'{"markdown":"首行\nnext\ttext\rrest $\frac{1}{2}$ [插图待补: 图1]","figures": BROKEN}'
    assert parse_single_ocr_response(raw)["markdown"] == "首行\nnext\ttext\rrest $\\frac{1}{2}$ [插图待补: 图1]"


def test_recovery_rejects_an_incomplete_markdown_string():
    with pytest.raises(ValueError, match="正文不完整"):
        parse_single_ocr_response(r'{"markdown":"正文 $\frac{1}{2}')


def test_legacy_markers_are_not_reinterpreted_as_math_or_draw_whole_screenshot(tmp_path):
    result = materialize(parse_single_ocr_response("题干\n[ILLUSTRATION_BOX: 10, 20, 90, 80]"), tmp_path,
                         draw=lambda *_args: pytest.fail("must not draw"))
    assert result["markdown"] == "题干\n[插图待补]"
    assert result["warnings"] and not list(tmp_path.iterdir())


def test_literal_protocol_examples_are_preserved():
    source = "`[ILLUSTRATION_BOX: 10, 20, 90, 80]`是示例\n[插图待补: 图1]"
    assert parsed(source)["markdown"] == source


def test_twenty_figure_bound_preserves_extra_placeholders(tmp_path):
    markdown = "\n".join(f"[插图待补: 图{index}]" for index in range(1, 23))
    result = materialize(parsed(markdown, []), tmp_path, skip=True)
    assert result["markdown"] == markdown and len(result["illustrations"]) == 20
    assert any("超过20幅" in warning for warning in result["warnings"])


@pytest.mark.parametrize("command", [r"\theta", r"\times", r"\nabla", r"\rho", r"\text{值}",
    r"\tfrac{1}{2}", r"\right)", r"\neq", r"\notin", r"\therefore"])
@pytest.mark.parametrize("figures", ["BROKEN", "[]"])
def test_recovery_preserves_known_latex_commands_overlapping_json_control_escapes(command, figures):
    source = r"正文 $\frac{1}{2}+" + command + "$ [插图待补: 图1]"
    raw = '{"markdown":"' + source + '","figures":' + figures + '}'
    assert parse_single_ocr_response(raw)["markdown"] == source


def test_recovery_retains_plain_json_newlines_tabs_beside_known_latex_control_commands():
    raw = r'{"markdown":"首行\nnext\ttext\rrest $\theta+\times+\nabla+\rho$ [插图待补: 图1]","figures": BROKEN}'
    assert parse_single_ocr_response(raw)["markdown"] == "首行\nnext\ttext\rrest $\\theta+\\times+\\nabla+\\rho$ [插图待补: 图1]"


def test_recovery_keeps_unknown_words_as_json_escapes_without_guessing_custom_commands():
    raw = r'{"markdown":"正文 $1\nnext+2\ttext$ [插图待补: 图1]","figures": BROKEN}'
    assert parse_single_ocr_response(raw)["markdown"] == "正文 $1\nnext+2\ttext$ [插图待补: 图1]"


def test_escaped_custom_math_command_and_genuine_formula_line_break_are_preserved():
    source = "正文 $\\nmycustom+1\n+2$ [插图待补: 图1]"
    raw = '{"markdown":' + json.dumps(source, ensure_ascii=False) + ',"figures": BROKEN}'
    assert parse_single_ocr_response(raw)["markdown"] == source


@pytest.mark.parametrize("figures", ["[]", "BROKEN"])
def test_nrt_only_formula_is_recovered_even_when_json_controls_are_normally_allowed(figures):
    source = r"正文 $\theta+\rho+\nabla+\times$ [插图待补: 图1]"
    assert parse_single_ocr_response('{"markdown":"' + source + '","figures":' + figures + '}')["markdown"] == source
