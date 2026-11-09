"""Official normative catalogs. Original spelling is intentional."""

from typing import Literal, get_args

UNSPECIFIED = "No especificado"

Scope = Literal[
    "General",
    "Ambiental",
    "Seguridad y salud en el trabajo",
    "Laboral",
    "Calidad",
]

ProductiveSector = Literal[
    "Todos los sectores",
    "Agropecuario",
    "Energía",
    "Equipamiento",
    "Forestal",
    "Infraestructura de transporte",
    "Infraestructura hidráulica",
    "Infraestructura portuaria",
    "Inmobiliarios",
    "Instalaciones fabriles varias",
    "Minería",
    "Pesca y acuicultura",
    "Planificación territorial e inmobiliarios en zonas",
    "Saneamiento ambiental",
    "Otros",
]

TerritorialCoverage = Literal[
    "Nacional",
    "Región de Arica y Parinacota",
    "Región de Tarapacá",
    "Región de Antofagasta",
    "Region de Atacama",
    "Región de Coquimbo",
    "Región de Valparaiso",
    "Región del Libertador General Bernardo O'Higgins",
    "Región del Maule",
    "Región de Ñuble",
    "Región del Biobio",
    "Región de La Araucanía",
    "Región de Los Ríos",
    "Región de Los Lagos",
    "Región de Aysén",
    "Región de Magallanes y de la Antártica Chilena",
]

CLOSED_CATALOGS: dict[str, tuple[str, ...]] = {
    "scope": get_args(Scope),
    "productive_sector": get_args(ProductiveSector),
    "territorial_coverage": get_args(TerritorialCoverage),
}
