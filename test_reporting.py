import unittest
import analyze

class ReportingTests(unittest.TestCase):
    def test_report_separates_runtime_protocols(self):
        self.assertTrue(hasattr(analyze,'build_report'), 'Mixed-runtime report support is missing')
        text=analyze.build_report({})
        self.assertIn('Native /v1/systemone',text)
        self.assertIn('Intern full-prefix adapter',text)
        self.assertIn('not directly interchangeable',text)
        self.assertNotIn('Both Intern-Decision GGUFs and Mica lack',text)

if __name__=='__main__':unittest.main()
