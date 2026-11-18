"""LaTeX in Word deliverables becomes native, editable Office Math equations."""

import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from dikdik import docextract, documents, omml

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def text_of(xml: str) -> str:
    return "".join(re.findall(r"<m:t[^>]*>([^<]*)</m:t>", xml))


def well_formed(xml: str) -> None:
    ElementTree.fromstring(f'<root xmlns:m="{omml.M}" xmlns:w="{W}">{xml}</root>')


class OmmlTests(unittest.TestCase):
    def convert(self, tex: str) -> str:
        parts = omml.to_omml(tex)
        for part in parts:
            well_formed(part)
        return "".join(parts)

    def test_structures_become_office_math_elements(self):
        cases = {
            r"\frac{a}{b}": "<m:f>", r"x^2": "<m:sSup>", r"x_i": "<m:sSub>", r"x_i^2": "<m:sSubSup>",
            r"\sqrt{x}": "<m:rad>", r"\sqrt[3]{x}": "<m:deg><m:r>", r"\sum_{i=1}^{n} i": "<m:nary>",
            r"\int_0^1 f": 'm:val="∫"', r"\vec{F}": "<m:acc>", r"\bar{x}": "<m:bar>",
            r"\lim_{x \to 0} f": "<m:limLow>", r"\begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}": "<m:m>",
            r"\binom{n}{k}": 'm:val="noBar"',
        }
        for tex, element in cases.items():
            with self.subTest(tex=tex):
                self.assertIn(element, self.convert(tex))

    def test_symbols_text_and_chemistry(self):
        self.assertEqual(text_of(self.convert(r"\alpha + \beta \leq \gamma \rightarrow \infty")), "α+β≤γ→∞")
        chemistry = self.convert(r"6\mathrm{CO_2} \rightarrow \mathrm{C_6H_{12}O_6}")
        self.assertIn("<m:sSub>", chemistry)
        self.assertIn('<m:sty m:val="p"/>', chemistry)  # upright chemical symbols
        self.assertEqual(text_of(self.convert(r"\left( \frac{a}{b} \right)")), "(ab)")
        self.assertEqual(text_of(self.convert(r"\left.\frac{dy}{dx}\right|_{x=0}")), "dydx|x=0")
        self.assertEqual(text_of(self.convert(r"\mathbb{R}")), "ℝ")
        self.assertIn("unknowncommand", text_of(self.convert(r"\unknowncommand x")))  # kept, not dropped

    def test_aligned_lines_become_separate_equations(self):
        self.assertEqual(len(omml.to_omml(r"\begin{aligned} a &= b \\ c &= d \end{aligned}")), 2)
        self.assertEqual(len(omml.to_omml(r"\begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}")), 1)


class WordEquationTests(unittest.TestCase):
    def test_markdown_maths_becomes_word_equations(self):
        markdown = ("# Lesson\n\nFor $ax^2 + bx + c = 0$ the roots are\n\n$$x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$$\n\n"
                    "\\[\n\\int_0^1 x^2 \\, dx = \\frac{1}{3}\n\\]\n\n\\begin{align}\na &= b \\\\\nc &= d\n\\end{align}\n\n"
                    "```math\nE = mc^2\n```\n\nIt costs 5 dollars and $6\\mathrm{CO_2}$ is a formula.\n")
        with tempfile.TemporaryDirectory() as temp:
            result = documents.create(Path(temp), {"path": "lesson.docx", "content": markdown})
            self.assertEqual(result["equations"], 4)
            with zipfile.ZipFile(Path(temp) / "lesson.docx") as archive:
                document = archive.read("word/document.xml").decode()
            ElementTree.fromstring(document)  # the whole document stays valid XML
            self.assertEqual(document.count("<m:oMathPara>"), 5)  # two aligned lines + three display equations
            self.assertGreaterEqual(document.count("<m:oMath>"), 7)
            self.assertNotIn("\\frac", document)
            text = docextract.document_text(Path(temp) / "lesson.docx")
            self.assertIn("ax2+bx+c=0", text)  # equations read back as text
            self.assertIn("5 dollars", text)


if __name__ == "__main__":
    unittest.main()
