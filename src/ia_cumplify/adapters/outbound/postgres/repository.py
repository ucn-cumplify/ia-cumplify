from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ia_cumplify.domain.article import Article
from ia_cumplify.domain.legal_body import LegalBody

_GET_LEGAL_BODY = """
SELECT id, title, summary, "type"
FROM legal_bodies
WHERE id = %s
"""

_LIST_ARTICLES = """
SELECT id, legal_body_id, number, section, text, "order"
FROM articles
WHERE legal_body_id = %s
ORDER BY "order" ASC
"""


class PostgresLegalBodyRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def get_by_id(self, legal_body_id: str) -> LegalBody | None:
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(_GET_LEGAL_BODY, (_as_uuid(legal_body_id),))
                row = cursor.fetchone()

        if row is None:
            return None

        return LegalBody(
            id=str(row["id"]),
            title=row["title"],
            summary=row["summary"] or "",
            type=row["type"] or "",
        )

    def list_articles(self, legal_body_id: str) -> list[Article]:
        with self._pool.connection() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(_LIST_ARTICLES, (_as_uuid(legal_body_id),))
                rows = cursor.fetchall()

        return [
            Article(
                id=str(row["id"]),
                legal_body_id=str(row["legal_body_id"]),
                number=row["number"],
                section=row["section"],
                text=row["text"],
                order=row["order"],
            )
            for row in rows
        ]


def _as_uuid(value: str) -> UUID:
    return UUID(value)
