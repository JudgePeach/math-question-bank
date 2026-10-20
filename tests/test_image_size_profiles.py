"""Guard physical image limits shared by browser, PDF and Word roles."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from mathbank.image_layout import (
    BODY_IMAGE_SIZE_CM,
    COMPACT_CHOICE_IMAGE_SIZE_CM,
    CSS_PX_PER_CM,
    DETACHED_FIGURE_SIZE_CM,
    SIZE_CM,
    TABLE_IMAGE_MAX_HEIGHT_CM,
)

ROOT = Path(__file__).resolve().parents[1]


def _browser_limits():
    node = shutil.which("node")
    assert node, "Node.js is required for the executable image sizing regression"
    source = (ROOT / "static/js/api.js").read_text()
    start = source.index("window.ImageLayoutTools = {")
    end = source.index("\n        const FigureLayoutState", start)
    script = """global.window = {};
const cssVariables = {};
global.document = { documentElement: { style: {
    setProperty(name, value) { cssVariables[name] = value; }
} } };
""" + source[start:end] + r"""
const tools = window.ImageLayoutTools;
const pixels = {};
for (const profile of Object.keys(tools.sizeProfilesCm)) {
    pixels[profile] = {};
    for (const size of Object.keys(tools.sizeProfilesCm[profile])) {
        pixels[profile][size] = tools.sizeDimensions(profile, size);
    }
}
console.log(JSON.stringify({
    profiles: tools.sizeProfilesCm, limits: tools.limitsCm, pixels, cssVariables,
    pxPerCm: tools.pxPerCm,
    contextual: [tools.sizeDimensions('body', 'auto'), tools.sizeDimensions('detached', 'auto')],
    invalid: [tools.sizeDimensions('missing', 'large'), tools.sizeDimensions('body', '__proto__'), tools.sizeDimensions('__proto__', 'constructor')],
    frozen: Object.isFrozen(tools.sizeProfilesCm.body.large)
}));
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_frontend_and_export_profiles_share_physical_limits():
    browser = _browser_limits()
    expected = {"body": BODY_IMAGE_SIZE_CM, "detached": DETACHED_FIGURE_SIZE_CM}
    assert browser["profiles"] == {
        profile: {size: list(bounds) for size, bounds in values.items()}
        for profile, values in expected.items()
    }
    assert browser["pxPerCm"] == pytest.approx(CSS_PX_PER_CM)
    for profile, values in expected.items():
        for size, (width, height) in values.items():
            assert browser["pixels"][profile][size] == pytest.approx({
                "maxWidth": width * CSS_PX_PER_CM,
                "maxHeight": height * CSS_PX_PER_CM,
            })
    assert browser["limits"] == {
        "tableMaxHeight": TABLE_IMAGE_MAX_HEIGHT_CM,
        "compactChoice": list(COMPACT_CHOICE_IMAGE_SIZE_CM),
    }
    assert browser["frozen"] is True


def test_image_roles_preserve_compatibility_and_contextual_auto():
    assert SIZE_CM is BODY_IMAGE_SIZE_CM
    assert BODY_IMAGE_SIZE_CM["large"] != DETACHED_FIGURE_SIZE_CM["large"]
    browser = _browser_limits()
    assert browser["contextual"] == [None, None]
    assert browser["invalid"] == [None, None, None]


def test_body_css_variables_are_published_from_the_shared_profiles():
    variables = _browser_limits()["cssVariables"]
    expected = {
        f"--mb-image-body-{size}-{axis}": f"{value:g}cm"
        for size, dimensions in BODY_IMAGE_SIZE_CM.items()
        for axis, value in zip(("width", "height"), dimensions)
    }
    expected["--mb-image-table-max-height"] = f"{TABLE_IMAGE_MAX_HEIGHT_CM:g}cm"
    assert variables == expected
    assert not any("auto" in name for name in variables)
