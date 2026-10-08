"""strip_base64_images, shared by the classification, the profile, the applicability reasons and the chat:
the linear passes give exactly what single regexes of the same rules give, without their quadratic cost.
Images become "[imagen omitida]"; a data URI of any other type, such as a PDF attachment, "[archivo
omitido]"."""

import random
import re
from time import perf_counter

import pytest

from ia_cumplify.adapters.outbound.openai.strip_images import strip_base64_images

# The earlier single regexes (quadratic), with any MIME type in place of image, as the reference for
# the output.
_TYPE = r"([a-z][a-z0-9.+-]*)"
_OLD_MARKDOWN = re.compile(rf"!\[[^\]]*\]\(\s*data:{_TYPE}\/[^)]+\)", re.IGNORECASE)
_OLD_IMG_TAG = re.compile(
    rf"<img\b[^>]*?\bsrc\s*=\s*(?:[\"']\s*)?data:{_TYPE}\/[^>\s\"']+[^>]*>", re.IGNORECASE | re.DOTALL
)
_OLD_DATA_URI = re.compile(
    rf"data:{_TYPE}\/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/]+=*"
    r"(?:\s+[A-Za-z0-9+/]{40,}=*)*"
    r"(?:\s+[A-Za-z0-9+/]{1,39}={1,2}(?![A-Za-z0-9+/=]))?",
    re.IGNORECASE,
)


def _old_placeholder(match: re.Match[str]) -> str:
    return "[imagen omitida]" if match.group(1).lower() == "image" else "[archivo omitido]"


def old_strip(text: str) -> str:
    if not text or "data:" not in text.casefold():
        return text
    text = _OLD_MARKDOWN.sub(_old_placeholder, text)
    text = _OLD_IMG_TAG.sub(_old_placeholder, text)
    return _OLD_DATA_URI.sub(_old_placeholder, text)


IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
PDF = "data:application/pdf;base64,JVBERi0xLjQKJcfsj6IKNSAwIG9iago8PC9MZW5ndGggNiAwIFIvRmlsdGVyIC9GbGF0ZURlY29kZT4+"


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The backend writes a BCN attachment as a Markdown image with the type the BCN sends.
        (f"Ver ![anexo.pdf]({PDF}) fin.", "Ver [archivo omitido] fin."),
        # A "]" in the file name ends the Markdown pass early; the leftover pass still takes the data URI.
        (f"Ver ![anexo [1].pdf]({PDF}) fin.", "Ver ![anexo [1].pdf]([archivo omitido]) fin."),
        (f"Ver {PDF} fin.", "Ver [archivo omitido] fin."),
        (f'Ver <img src="{PDF}"> fin.', "Ver [archivo omitido] fin."),
        (f"Ver ![a]({PDF}) y ![b]({IMAGE}).", "Ver [archivo omitido] y [imagen omitida]."),
        ("DATA:Application/PDF;base64,QUFB fin", "[archivo omitido] fin"),
        # Without base64, a data URI that is not a Markdown or <img> target stays.
        ("Ver data:text/plain,hola fin.", "Ver data:text/plain,hola fin."),
    ],
)
def test_other_data_uris_become_a_file_placeholder(text: str, expected: str) -> None:
    assert strip_base64_images(text) == expected
    assert old_strip(text) == expected


def test_same_output_as_the_single_regexes() -> None:
    tokens = [
        "<img", "<IMG", "<İmg", "<ımg", "<imgx", "<img9", "<im", ">", " ", "\n", "\x1c", "src", "SRC", "ſrc",
        "xsrc", "=", '"', "'", "data:image/", "DATA:IMAGE/", "png", ";base64,", "AAAA", "x", "<", "\t", "![",
        "!", "[", "](", "]( ", "](data:image/", ")", "]", "(", "base64", "iVBORw0KGgo" * 4, "==", "\r\n",
        "data:application/", "](data:application/", "data:x-world/", "DATA:", "data:", "/", "pdf",
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
