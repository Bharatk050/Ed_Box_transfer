import pytest

import metadata_agent as agent
from metadata_rules import filename_grades


@pytest.mark.parametrize('filename,expected', [
    ('3rdGradeStudents_Interview_1920x1080.zip', ['3']),
    ('Ryan_Booth_6thGradeLesson_1280x720.zip', ['6']),
    ('Teacher_Grade3_Grade_6_Interview.zip', ['3', '6']),
    ('Grade12_Interview.zip', ['12']),
    ('Grades3-5_Interview.zip', ['3', '4', '5']),
    ('Preschool_Kindergarten.zip', ['Pre-K', 'K']),
    ('Girl_on_Computer_1920x1080.zip', []),
    ('Grade30_Room6_20261017.zip', []),
])
def test_explicit_filename_grades(filename, expected):
    choices = next(s.choices for s in agent.load_template() if s.name == 'Grade')
    assert filename_grades(filename, choices) == expected


def test_filename_grade_fallback_unions_current_evidence_only():
    template = agent.load_template()
    raw = {s.name: {'value': [] if s.multiple else None, 'explanation': '', 'sources': []}
           for s in template}
    raw['Grade'] = {'value': ['6'], 'explanation': 'Explicit spoken grade.', 'sources': ['transcript.json']}
    evidence = agent.Evidence(texts=[('archive_name', 'Teacher_Grade3_Interview.zip'),
                                    ('transcript.json', 'I now teach sixth grade.')],
                              reference_examples=[{'filename': 'Grade8.zip', 'metadata': {'Grade': ['8']}}])
    result = agent.validate_metadata(template, raw, evidence)
    assert result['metadata']['Grade'] == ['6', '3']
    assert result['field_evidence']['Grade']['sources'] == ['transcript.json', 'archive_name']
    assert result['metadata']['Role'] == []


def test_assessment_restriction_retains_current_evidence_sources():
    template = agent.load_template()
    raw = {s.name: {'value': [] if s.multiple else None, 'explanation': '', 'sources': []}
           for s in template}
    raw['Content Tags'] = {'value': ['Assessment'], 'explanation': 'Visible assessment material.',
                           'sources': ['frame.jpg']}
    raw['Restrictions'] = {'value': 'No', 'explanation': 'Existing metadata.', 'sources': ['metadata.json']}
    evidence = agent.Evidence(texts=[('metadata.json', 'Restrictions: No')], images=[('frame.jpg', '')])
    result = agent.validate_metadata(template, raw, evidence)
    assert result['metadata']['Restrictions'] == 'Yes\u2014Internal Only'
    assert result['field_evidence']['Restrictions']['sources'] == ['metadata_policy', 'frame.jpg']
