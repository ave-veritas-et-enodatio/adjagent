"""The readiness-doc templates resolve where the pipeline looks for them.

``stamp_readiness_docs`` reads these two files at phase-3a of a real build,
after the whole head has run, where a resource that moved without its lookup
surfaces as a stage failure with no obvious cause. These tests fail at the
move instead.
"""

import unittest

from kb_tools import kb_pipeline


class TestReadinessTemplatesResolve(unittest.TestCase):
    """Every readiness doc has an installed template on disk."""

    def test_each_readiness_doc_has_a_template_file(self):
        for name in kb_pipeline.READINESS_DOCS:
            source = kb_pipeline.installed_template(name)
            self.assertTrue(source.is_file(), f"{name}: no template at {source}")

    def test_templates_live_under_the_installed_directory(self):
        # The directory name is load-bearing: it states that these files are
        # written into a consuming KB, not rendered into anything here.
        for name in kb_pipeline.READINESS_DOCS:
            source = kb_pipeline.installed_template(name)
            self.assertEqual(source.parent.name, kb_pipeline.INSTALLED_DIR)

    def test_templates_carry_the_project_name_field(self):
        # Substituting it is the one per-project fact stamping performs; a
        # template that lost the field would stamp a generic doc silently.
        for name in kb_pipeline.READINESS_DOCS:
            text = kb_pipeline.installed_template(name).read_text(encoding="utf-8")
            self.assertIn(kb_pipeline.PROJECT_NAME_FIELD, text, name)


if __name__ == "__main__":
    unittest.main()
