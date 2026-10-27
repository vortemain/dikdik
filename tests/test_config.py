"""Config validation fails fast on invalid environment variables."""

import unittest

from dikdik.config import validate_config


class ConfigValidationTests(unittest.TestCase):
    def test_valid_config_no_errors(self):
        self.assertEqual(validate_config({}), [])

    def test_numeric_out_of_range(self):
        errors = validate_config({"DIKDIK_PORT": "99999"})
        self.assertEqual(len(errors), 1)
        self.assertIn("DIKDIK_PORT", errors[0])

    def test_numeric_not_a_number(self):
        errors = validate_config({"DIKDIK_PORT": "abc"})
        self.assertEqual(len(errors), 1)
        self.assertIn("must be a number", errors[0])

    def test_valid_numeric(self):
        self.assertEqual(validate_config({"DIKDIK_PORT": "8787"}), [])

    def test_event_retention_range(self):
        errors = validate_config({"DIKDIK_EVENT_RETENTION_SECONDS": "-1"})
        self.assertEqual(len(errors), 1)
        self.assertEqual(validate_config({"DIKDIK_EVENT_RETENTION_SECONDS": "3600"}), [])
        errors = validate_config({"DIKDIK_EVENT_RETENTION_SECONDS": "99999999999"})
        self.assertEqual(len(errors), 1)

    def test_boolean_invalid(self):
        errors = validate_config({"DIKDIK_DORMANT": "yes"})
        self.assertEqual(len(errors), 1)

    def test_boolean_valid(self):
        self.assertEqual(validate_config({"DIKDIK_DORMANT": "1"}), [])
        self.assertEqual(validate_config({"DIKDIK_DORMANT": "true"}), [])
        self.assertEqual(validate_config({"DIKDIK_DORMANT": "false"}), [])

    def test_empty_values_ignored(self):
        self.assertEqual(validate_config({"DIKDIK_PORT": ""}), [])

    def test_multiple_errors(self):
        errors = validate_config({"DIKDIK_PORT": "abc", "DIKDIK_DORMANT": "maybe"})
        self.assertEqual(len(errors), 2)


if __name__ == "__main__":
    unittest.main()