"""strip_base64_images, shared by the classification, the profile and the chat: the linear passes give
exactly what the earlier single regexes gave, without their quadratic cost."""

import random
import re
from time import perf_counter

import pytest

from ia_cumplify.adapters.outbound.openai.strip_images import strip_base64_images

# The earlier implementation, as the reference for the output.
_OLD_MARKDOWN = re.compile(r"!\[[^\]]*\]\(\s*data:image\/[^)]+\)", re.IGNORECASE)
_OLD_IMG_TAG = re.compile(
    r"<img\b[^>]*?\bsrc\s*=\s*(?:[\"']\s*)?data:image\/[^>\s\"']+[^>]*>", re.IGNORECASE | re.DOTALL
)
_OLD_DATA_URI = re.compile(
    r"data:image\/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/]+=*"
    r"(?:\s+[A-Za-z0-9+/]{40,}=*)*"
    r"(?:\s+[A-Za-z0-9+/]{1,39}={1,2}(?![A-Za-z0-9+/=]))?",
    re.IGNORECASE,
)


def old_strip(text: str) -> str:
    if not text or ("base64" not in text.casefold() and "data:image" not in text.casefold()):
        return text
    text = _OLD_MARKDOWN.sub("[imagen omitida]", text)
    text = _OLD_IMG_TAG.sub("[imagen omitida]", text)
    return _OLD_DATA_URI.sub("[imagen omitida]", text)


IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"Ver ![plano]({IMAGE}) fin.", "Ver [imagen omitida] fin."),
        (f'Ver <img alt="x" src="{IMAGE}"> fin.', "Ver [imagen omitida] fin."),
        (f"Ver <IMG\nsrc = '{IMAGE}'\n/> fin.", "Ver [imagen omitida] fin."),
        (f"Ver <img src={IMAGE}> fin.", "Ver [imagen omitida] fin."),
        (f"Ver {IMAGE} fin.", "Ver [imagen omitida] fin."),
        (f"Ver ![a](https://x.cl/a.png) y ![b]({IMAGE}).", "Ver ![a](https://x.cl/a.png) y [imagen omitida]."),
        (f'<img src="https://x.cl/a.png"> y <img src="{IMAGE}">', '<img src="https://x.cl/a.png"> y [imagen omitida]'),
        (f'<img alt="<img src={IMAGE}">', "[imagen omitida]"),
        ("data:image/png;base64,QUFB\nQUFB fin", "[imagen omitida]\nQUFB fin"),
        ("Sin imágenes, solo base64 en el texto.", "Sin imágenes, solo base64 en el texto."),
    ],
)
def test_real_images(text: str, expected: str) -> None:
    assert strip_base64_images(text) == expected
    assert old_strip(text) == expected


def test_same_output_as_the_single_regexes() -> None:
    tokens = [
        "<img", "<IMG", "<İmg", "<ımg", "<imgx", "<img9", "<im", ">", " ", "\n", "\x1c", "src", "SRC", "ſrc",
        "xsrc", "=", '"', "'", "data:image/", "DATA:IMAGE/", "png", ";base64,", "AAAA", "x", "<", "\t", "![",
        "!", "[", "](", "]( ", "](data:image/", ")", "]", "(", "base64", "iVBORw0KGgo" * 4, "==", "\r\n",
    ]
    rng = random.Random(2026)
    for size in (10, 40):
        for _ in range(5000):
            text = "".join(rng.choice(tokens) for _ in range(rng.randint(0, size)))
            assert strip_base64_images(text) == old_strip(text), repr(text)


@pytest.mark.parametrize(
    "unit",
    ["<img ", "<img src=data:image/A", "![", "![](data:image/x", "src=data:image/AAA "],
)
def test_long_text_without_closing_characters_stays_linear(unit: str) -> None:
    # Ten passages' worth: the earlier "<img" regex took seconds here (about 90 ms at 6.000).
    text = (unit * 60_000)[:60_000] + " data:image"
    started = perf_counter()
    strip_base64_images(text)
    assert perf_counter() - started < 1.0
