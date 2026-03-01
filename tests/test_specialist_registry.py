import unittest
from nova.tools.specialist_registry import save_specialist_config, get_specialist_config, list_specialists

class TestSpecialistRegistry(unittest.TestCase):

    def test_save_specialist_config(self):
        result = save_specialist_config("New Specialist", "Test Role", "Test Instructions")
        self.assertIn("saved", result.lower())

    def test_get_specialist_config(self):
        config = get_specialist_config("Researcher")
        self.assertIsNotNone(config)
        self.assertEqual(config['name'], "Researcher")

    def test_list_specialists(self):
        result = list_specialists()
        self.assertNotEqual(result, "No specialists registered.")

if __name__ == '__main__':
    unittest.main()
