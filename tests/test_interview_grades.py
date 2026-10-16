import json

import pytest

import metadata_agent as agent


def response(template, footage, people, grades, source='transcript.json'):
    raw = {spec.name: {'value': [] if spec.multiple else None, 'sources': [], 'explanation': ''}
           for spec in template}
    for name, value in [('Footage Type', footage), ('People', people), ('Grade', grades)]:
        raw[name] = {'value': value, 'sources': [source], 'explanation': 'Current video evidence'}
    return raw


@pytest.mark.parametrize('people,grades,text', [
    (['Teacher'], ['6'], 'I teach sixth grade.'),
    (['Teacher'], ['3', '4'], 'I teach third and fourth grade.'),
    (['Coach'], ['3', '4', '5'], 'I coach teachers in grades three through five.'),
    (['Teacher', 'Coach'], ['6'], 'I am a teacher and instructional coach.'),
])
def test_adult_only_interview_grades_are_preserved(people, grades, text):
    template = agent.load_template()
    evidence = agent.Evidence(texts=[('transcript.json', text)])
    result = agent.validate_metadata(template, response(template, 'Interview', people, grades), evidence)
    assert result['metadata']['Grade'] == grades
    assert result['metadata']['People'] == people
    assert result['field_evidence']['Grade']['sources'] == ['transcript.json']
    assert 'Grade' not in result['unknown_fields']
    assert not any('Grade cleared' in warning for warning in result['warnings'])


@pytest.mark.parametrize('source,text', [
    ('archive_name', 'Teacher_6th_Grade_Interview.zip'),
    ('ocr.json#frame=0', 'Grade 6 Teacher Interview'),
])
def test_filename_or_opening_title_grades_are_preserved(source, text):
    template = agent.load_template()
    evidence = agent.Evidence(texts=[(source, text)])
    raw = response(template, 'Interview', ['Teacher'], ['6'], source)
    result = agent.validate_metadata(template, raw, evidence)
    assert result['metadata']['Grade'] == ['6']
    result['source_archive'] = 'example.zip'
    exported = json.loads(agent.build_exports(template, [result], [])['metadata.json'])[0]
    assert exported['Grade'] == ['6']
    assert set(exported) == {spec.name for spec in template}


@pytest.mark.parametrize('footage,people,grades', [
    ('Interview', ['Student'], ['3']),
    ('Interview', ['Teacher', 'Student'], ['3']),
    ('Interview', ['Coach', 'Student'], ['6']),
    ('Teaching Sample', ['Teacher', 'Student'], ['6']),
    ('Teaching Sample', ['Teacher'], ['6']),
])
def test_student_mixed_and_lesson_grades_are_preserved(footage, people, grades):
    template = agent.load_template()
    evidence = agent.Evidence(texts=[('transcript.json', 'Explicit student or lesson grade')])
    result = agent.validate_metadata(template, response(template, footage, people, grades), evidence)
    assert result['metadata']['Grade'] == grades


def test_final_validation_preserves_grade_from_long_transcript_candidate():
    template = agent.load_template()
    evidence = agent.Evidence(texts=[('archive_name', 'Teacher_Grade6_Interview.zip'),
                                    ('transcript.json', 'I teach sixth grade. ' * 1500)])
    calls = []
    extractor = object.__new__(agent.AzureExtractor)
    def call(specs, payload, images=()):
        calls.append(payload)
        return response(specs, 'Interview', ['Teacher'], ['6'])
    extractor.call = call
    raw = extractor.extract(template, evidence)
    assert calls[0]['mode'] == 'chunk'
    assert calls[-1]['mode'] == 'final'
    assert calls[-1]['candidates'][0]['Grade']['value'] == ['6']
    assert agent.validate_metadata(template, raw, evidence)['metadata']['Grade'] == ['6']


@pytest.mark.parametrize('role,people,grades,text', [
    (['Coach'], ['Student'], ['3', '4', '5'], 'An off-camera coach: I coach grades three through five.'),
    (['Teacher'], ['Teacher'], ['3', '6'], 'I used to teach third grade and now teach sixth.'),
    (['Teacher'], ['Teacher'], ['5'], 'I teach fifth grade, not sixth.'),
    (['Teacher'], ['Teacher'], [], 'I have six years of experience and work with six teachers.'),
    (['Teacher'], ['Teacher'], ['Pre-K', 'K'], 'I teach preschool and kindergarten.'),
    (['Teacher'], ['Teacher'], ['9', '10', '11', '12'], 'I teach grades nine through twelve.'),
])
def test_supported_role_grade_outputs_survive_validation(role, people, grades, text):
    # These fixtures represent the model's classified output; they verify the
    # contract and removal of the old guard, not live language-model accuracy.
    template = agent.load_template()
    raw = response(template, 'Interview', people, grades)
    raw['Role'] = {'value': role, 'sources': ['transcript.json'], 'explanation': 'Speaker self-description'}
    evidence = agent.Evidence(texts=[('transcript.json', text)])
    result = agent.validate_metadata(template, raw, evidence)
    assert result['metadata']['Role'] == role
    assert result['metadata']['People'] == people
    assert result['metadata']['Grade'] == grades


def test_grade_union_deduplicates_without_copying_people_to_role():
    template = agent.load_template()
    evidence = agent.Evidence(texts=[('archive_name', 'Teacher_Grade3_Interview.zip'),
                                    ('transcript.json', 'I also teach sixth grade.')])
    raw = response(template, 'Interview', ['Teacher'], ['3', '6', '3'])
    raw['Grade']['sources'] = ['archive_name', 'transcript.json']
    result = agent.validate_metadata(template, raw, evidence)
    assert result['metadata']['Grade'] == ['3', '6']
    assert result['metadata']['Role'] == []


def test_legacy_examples_can_omit_role(tmp_path):
    from reference_examples import load_examples
    (tmp_path / 'old.json').write_text('{"People":["Teacher"],"Grade":[]}', encoding='utf-8')
    examples, warnings = load_examples(agent.load_template(), tmp_path)
    assert not warnings
    assert 'Role' not in examples[0]['metadata']
