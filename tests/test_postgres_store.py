import os
import unittest

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

from app.backend.postgres_store import _compile_filter, parse_document_id


class PostgresFilterTests(unittest.TestCase):
    def test_not_equal_matches_missing_fields(self):
        sql, params = _compile_filter({"status": {"$ne": "cancelled"}})

        self.assertIn("COALESCE((data -> 'status'), 'null'::jsonb)", sql)
        self.assertEqual(params, {"p0": '"cancelled"'})

    def test_or_conditions_use_distinct_bound_parameters(self):
        sql, params = _compile_filter(
            {
                "$or": [
                    {"name": {"$regex": "Sam & Co", "$options": "i"}},
                    {"email": {"$regex": "Sam & Co", "$options": "i"}},
                ]
            }
        )

        self.assertIn(":p0", sql)
        self.assertIn(":p1", sql)
        self.assertEqual(params, {"p0": "%Sam & Co%", "p1": "%Sam & Co%"})

    def test_document_ids_are_validated_and_normalized(self):
        self.assertEqual(
            parse_document_id("0123456789ABCDEF01234567"),
            "0123456789abcdef01234567",
        )
        with self.assertRaises(ValueError):
            parse_document_id("not-an-object-id")


if __name__ == "__main__":
    unittest.main()
