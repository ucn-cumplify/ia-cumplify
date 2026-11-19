"""Literal official catalogs and preserved open dimensions, without network or database."""

import pytest
from pydantic import ValidationError

from ia_cumplify.adapters.outbound.openai.llm_schema import LlmArticleClassification
from ia_cumplify.adapters.outbound.openai.llm_profile_schema import LlmCompanyProfileClassification
from ia_cumplify.adapters.outbound.openai.prompts import render_candidate_labels
from ia_cumplify.domain.classification_catalogs import CLOSED_CATALOGS

EXPECTED = {'scope': ['General', 'Ambiental', 'Seguridad y salud en el trabajo', 'Laboral', 'Calidad'], 'productive_sector': ['Todos los sectores', 'Agropecuario', 'Energía', 'Equipamiento', 'Forestal', 'Infraestructura de transporte', 'Infraestructura hidráulica', 'Infraestructura portuaria', 'Inmobiliarios', 'Instalaciones fabriles varias', 'Minería', 'Pesca y acuicultura', 'Planificación territorial e inmobiliarios en zonas', 'Saneamiento ambiental', 'Otros'], 'territorial_coverage': ['Nacional', 'Región de Arica y Parinacota', 'Región de Tarapacá', 'Región de Antofagasta', 'Region de Atacama', 'Región de Coquimbo', 'Región de Valparaiso', 'Región Metropolitana de Santiago', "Región del Libertador General Bernardo O'Higgins", 'Región del Maule', 'Región de Ñuble', 'Región del Biobio', 'Región de La Araucanía', 'Región de Los Ríos', 'Región de Los Lagos', 'Región de Aysén', 'Región de Magallanes y de la Antártica Chilena']}

def data():
    return dict(
        scope=["Ambiental"], productive_sector=["Minería"], territorial_coverage=["Nacional"],
        activity_action=["Proceso libre"], facility_installation_equipment=["Equipo libre"],
        others=["Condición libre"],
    )


def test_catalogs_match_the_official_lists_literally():
    assert CLOSED_CATALOGS == {key: tuple(values) for key, values in EXPECTED.items()}
    assert all("No especificado" not in values for values in CLOSED_CATALOGS.values())


@pytest.mark.parametrize("dimension, labels", EXPECTED.items())
def test_every_official_label_is_accepted(dimension, labels):
    for label in labels:
        value = {**data(), dimension: [label]}
        assert LlmArticleClassification.model_validate(value).model_dump() == value


@pytest.mark.parametrize("dimension", list(data()))
def test_sentinel_is_exclusive_in_every_dimension(dimension):
    assert LlmArticleClassification.model_validate({**data(), dimension: ["No especificado"]})
    with pytest.raises(ValidationError):
        LlmArticleClassification.model_validate({**data(), dimension: [data()[dimension][0], "No especificado"]})


@pytest.mark.parametrize("dimension, value", [
    ("scope", "ambiental"), ("scope", " Ambiental "),
    ("territorial_coverage", "Región de Atacama"),
    ("territorial_coverage", "Región de Valparaíso"),
    # v6 adds only the full name of the region: no abbreviation or partial name is an option.
    ("territorial_coverage", "RM"),
    ("territorial_coverage", "Región Metropolitana"),
    ("territorial_coverage", "Metropolitana"),
    ("territorial_coverage", "Santiago"),
    ("territorial_coverage", "Region Metropolitana de Santiago"),
    ("territorial_coverage", "región metropolitana de santiago"),
])
def test_closed_labels_are_not_corrected_or_extended(dimension, value):
    with pytest.raises(ValidationError):
        LlmArticleClassification.model_validate({**data(), dimension: [value]})


# The territorial catalog of classify-v5-closed-catalogs, in its order.
V5_TERRITORIAL_COVERAGE = [
    'Nacional', 'Región de Arica y Parinacota', 'Región de Tarapacá', 'Región de Antofagasta', 'Region de Atacama',
    'Región de Coquimbo', 'Región de Valparaiso', "Región del Libertador General Bernardo O'Higgins",
    'Región del Maule', 'Región de Ñuble', 'Región del Biobio', 'Región de La Araucanía', 'Región de Los Ríos',
    'Región de Los Lagos', 'Región de Aysén', 'Región de Magallanes y de la Antártica Chilena',
]


def test_v6_adds_only_the_metropolitan_region_to_the_territorial_catalog():
    territorial = list(CLOSED_CATALOGS["territorial_coverage"])
    assert len(territorial) == 17
    assert [label for label in territorial if label != "Región Metropolitana de Santiago"] == V5_TERRITORIAL_COVERAGE
    assert territorial.index("Región Metropolitana de Santiago") == territorial.index("Región de Valparaiso") + 1
    assert (len(CLOSED_CATALOGS["scope"]), len(CLOSED_CATALOGS["productive_sector"])) == (5, 15)


@pytest.mark.parametrize("labels", [
    ["Región Metropolitana de Santiago"],
    ["Región de Valparaiso", "Región Metropolitana de Santiago"],
])
def test_the_metropolitan_region_is_accepted_and_offered_to_the_model(labels):
    value = {**data(), "territorial_coverage": labels}
    assert LlmArticleClassification.model_validate(value).model_dump() == value
    line = next(line for line in render_candidate_labels().splitlines() if line.startswith("territorial_coverage: "))
    assert line.removeprefix("territorial_coverage: ").split(" | ") == list(CLOSED_CATALOGS["territorial_coverage"])


def test_open_dimensions_and_profile_remain_free_text():
    value = data()
    assert LlmArticleClassification.model_validate(value).model_dump() == value
    profile = {**value, "scope": ["Ámbito libre"], "territorial_coverage": ["Comuna libre"]}
    assert LlmCompanyProfileClassification.model_validate(profile).model_dump() == profile


def test_open_candidate_labels_are_preserved_when_provided():
    # CandidateLabels currently exposes only closed dimensions. Exercise the generic rendering path.
    class Candidates:
        def by_dimension(self):
            return (
                ("scope", ("HISTORICA",)),
                ("activity_action", ("Proceso existente",)),
                ("facility_installation_equipment", ("Equipo existente",)),
                ("others", ("Condición existente",)),
            )
    block = render_candidate_labels(Candidates())
    assert "HISTORICA" not in block
    for label in ("Proceso existente", "Equipo existente", "Condición existente"):
        assert label in block
