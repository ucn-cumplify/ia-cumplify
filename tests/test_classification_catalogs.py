"""Literal official catalogs and preserved open dimensions, without network or database."""

import pytest
from pydantic import ValidationError

from ia_cumplify.adapters.outbound.openai.llm_schema import LlmArticleClassification
from ia_cumplify.adapters.outbound.openai.llm_profile_schema import LlmCompanyProfileClassification
from ia_cumplify.adapters.outbound.openai.prompts import render_candidate_labels
from ia_cumplify.domain.classification_catalogs import CLOSED_CATALOGS

EXPECTED = {'scope': ['General', 'Ambiental', 'Seguridad y salud en el trabajo', 'Laboral', 'Calidad'], 'productive_sector': ['Todos los sectores', 'Agropecuario', 'Energía', 'Equipamiento', 'Forestal', 'Infraestructura de transporte', 'Infraestructura hidráulica', 'Infraestructura portuaria', 'Inmobiliarios', 'Instalaciones fabriles varias', 'Minería', 'Pesca y acuicultura', 'Planificación territorial e inmobiliarios en zonas', 'Saneamiento ambiental', 'Otros'], 'territorial_coverage': ['Nacional', 'Región de Arica y Parinacota', 'Región de Tarapacá', 'Región de Antofagasta', 'Region de Atacama', 'Región de Coquimbo', 'Región de Valparaiso', "Región del Libertador General Bernardo O'Higgins", 'Región del Maule', 'Región de Ñuble', 'Región del Biobio', 'Región de La Araucanía', 'Región de Los Ríos', 'Región de Los Lagos', 'Región de Aysén', 'Región de Magallanes y de la Antártica Chilena']}

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
    ("territorial_coverage", "Región Metropolitana de Santiago"),
])
def test_closed_labels_are_not_corrected_or_extended(dimension, value):
    with pytest.raises(ValidationError):
        LlmArticleClassification.model_validate({**data(), dimension: [value]})


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
