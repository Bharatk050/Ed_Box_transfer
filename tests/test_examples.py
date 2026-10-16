import json

import pytest

import metadata_agent as agent
import reference_examples as refs


def write(folder, name, data):
    (folder / name).write_text(json.dumps(data), encoding='utf-8')


def blank(template):
    return {s.name: {'value': [] if s.multiple else None, 'explanation': '', 'sources': []} for s in template}


def test_plain_wrapped_and_partial_examples(tmp_path):
    write(tmp_path, 'interview.json', {'Footage Type': 'Interview', 'People': ['Teacher', 'Teacher']})
    write(tmp_path, 'demo.json', {'description': 'A product demonstration', 'metadata': {'Footage Type': 'Product'}})
    (tmp_path / 'starter.json.example').write_text('not loaded')
    examples, warnings = refs.load_examples(agent.load_template(), tmp_path)
    assert not warnings
    assert len(examples) == 2
    assert examples[1]['metadata']['People'] == ['Teacher']
    assert examples[0]['description'] == 'A product demonstration'


def test_single_record_download_can_be_used_directly_as_example(tmp_path):
    write(tmp_path, 'download.json', [{'Footage Type': 'B-Roll', 'Role': ['Student'], 'Grade': ['8']}])
    examples, warnings = refs.load_examples(agent.load_template(), tmp_path)
    assert not warnings
    assert examples[0]['metadata'] == {'Footage Type': 'B-Roll', 'Role': ['Student'], 'Grade': ['8']}
    write(tmp_path, 'multiple.json', [{'Grade': ['3']}, {'Grade': ['6']}])
    examples, warnings = refs.load_examples(agent.load_template(), tmp_path)
    assert len(examples) == 1
    assert any('multiple.json' in warning for warning in warnings)


@pytest.mark.parametrize('document', [[], {}, {'Unknown Field': 'x'}, {'Grade': '3'},
                                      {'Footage Type': 'Movie'}, {'Creation Date': 'yesterday'},
                                      {'Length': '00:99:00'}, {'State': 'Mars'},
                                      {'metadata': {'Grade': ['3']}, 'description': 123},
                                      {'metadata': {'Grade': ['3']}, 'field_evidence': {}}])
def test_invalid_examples_are_skipped(tmp_path, document):
    write(tmp_path, 'bad.json', document)
    examples, warnings = refs.load_examples(agent.load_template(), tmp_path)
    assert not examples
    assert len(warnings) == 1
    assert 'bad.json' in warnings[0]


def test_bad_json_empty_missing_and_size_limits(tmp_path):
    template = agent.load_template()
    assert refs.load_examples(template, tmp_path) == ([], [])
    assert refs.load_examples(template, tmp_path / 'missing') == ([], [])
    (tmp_path / 'broken.json').write_text('{')
    write(tmp_path, 'too_large.json', {'Credits': 'x' * refs.MAX_EXAMPLE_BYTES})
    write(tmp_path, 'too_long.json', {'Credits': 'x' * refs.EXAMPLE_CHAR_BUDGET})
    examples, warnings = refs.load_examples(template, tmp_path)
    assert not examples
    assert len(warnings) == 3


def test_example_selection_rank_ties_and_budget():
    examples = [{'filename': f'{i}.json', 'description': '', 'metadata': {'Footage Type': 'Interview'}}
                for i in range(7)]
    examples.append({'filename': 'z.json', 'description': 'software dashboard walkthrough',
                     'metadata': {'Footage Type': 'Screencast'}})
    evidence = agent.Evidence(texts=[('transcript.json', 'software dashboard walkthrough')])
    selected = refs.select_examples(examples, evidence)
    assert len(selected) == 5
    assert selected[0]['filename'] == 'z.json'
    assert selected[1]['filename'] == '0.json'
    no_match = refs.select_examples(examples, agent.Evidence(texts=[('a', 'unrelated')]))
    assert [e['filename'] for e in no_match] == [f'{i}.json' for i in range(5)]
    for example in examples:
        example['description'] = 'x' * 5000
    bounded = refs.select_examples(examples, evidence)
    assert len(bounded) == 2
    assert len(json.dumps(bounded, ensure_ascii=False)) <= refs.EXAMPLE_CHAR_BUDGET


@pytest.mark.parametrize('footage,text', [
    ('Interview', 'An interview with a teacher about their experience.'),
    ('Product', 'A product demonstration and showcase.'),
    ('Screencast', 'A screen capture of a software dashboard workflow.')])
def test_examples_reach_extraction_without_becoming_sources(footage, text):
    template = agent.load_template()
    example = {'filename': 'reference.json', 'description': text, 'metadata': {'Footage Type': footage}}
    evidence = agent.Evidence(texts=[('archive_name', 'current.zip'), ('transcript.json', text)],
                              reference_examples=[example])
    extractor = object.__new__(agent.AzureExtractor)
    payloads = []
    def call(specs, payload, images=()):
        payloads.append(payload)
        raw = blank(specs)
        raw['Footage Type'] = {'value': footage, 'sources': ['transcript.json'], 'explanation': text}
        return raw
    extractor.call = call
    raw = extractor.extract(template, evidence)
    assert payloads[-1]['reference_examples'] == [example]
    assert agent.validate_metadata(template, raw, evidence)['metadata']['Footage Type'] == footage
    raw['Footage Type']['sources'] = ['reference.json']
    assert agent.validate_metadata(template, raw, evidence)['metadata']['Footage Type'] is None
    evidence.texts.append(('long.json', 'x' * (agent.CHUNK_CHARS + 1)))
    payloads.clear()
    extractor.extract(template, evidence)
    assert payloads[0]['mode'] == 'chunk'
    assert payloads[-1]['mode'] == 'final'
    assert all(p['reference_examples'] == [example] for p in payloads)
    assert all(p['current_video_filename'] == {'source': 'archive_name', 'text': 'current.zip'}
               for p in payloads)


def test_examples_loaded_once_per_batch_and_recorded(tmp_path, monkeypatch):
    folder = tmp_path / 'examples'
    folder.mkdir()
    write(folder, 'demo.json', {'Footage Type': 'Product'})
    (folder / 'bad.json').write_text('{')
    template = agent.load_template()
    seen = []
    class Fake:
        def extract(self, specs, evidence):
            seen.append(evidence.reference_examples)
            # Editing midway through the batch must not change its snapshot.
            write(folder, 'demo.json', {'Footage Type': 'Interview'})
            return blank(specs)
    upload = agent.UploadedArchive('product.zip', __import__('io').BytesIO().getvalue())
    monkeypatch.setattr(agent, 'prepare_archive', lambda _: agent.Evidence(texts=[('archive_name', 'product.zip')]))
    exports, results, statuses = agent.run_batch([upload, upload], template, Fake(), examples_folder=folder)
    assert len(results) == 2
    assert all(items[0]['metadata']['Footage Type'] == 'Product' for items in seen)
    assert results[0]['reference_examples'] == ['demo.json']
    assert 'bad.json' in results[0]['warnings'][0]
    assert 'reference_examples' not in exports['metadata.csv'].decode('utf-8-sig')
    assert refs.load_examples(template, folder)[0][0]['metadata']['Footage Type'] == 'Interview'
    for name in folder.glob('*.json'):
        name.write_text('{')
    _, results, statuses = agent.run_batch([upload], template, Fake(), examples_folder=folder)
    assert statuses[0]['status'] == 'success'
    assert results[0]['reference_examples'] == []


def test_starter_is_valid_but_not_automatically_loaded(tmp_path):
    starter = refs.EXAMPLES_DIR / 'desired_output.json.example'
    (tmp_path / 'starter.json').write_bytes(starter.read_bytes())
    examples, warnings = refs.load_examples(agent.load_template(), tmp_path)
    assert len(examples) == 1
    assert not warnings


def test_streamlit_shows_invalid_example_warning(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    (tmp_path / 'broken.json').write_text('{')
    monkeypatch.setattr(refs, 'EXAMPLES_DIR', tmp_path)
    app = AppTest.from_file(str(agent.ROOT / 'app.py'), default_timeout=30).run()
    assert not app.exception
    assert any('broken.json' in w.value for w in app.warning)
    assert any('0 valid example(s)' in caption.value for caption in app.caption)


def test_commented_examples_preserve_string_content_and_remove_trailing_commas():
    document = refs.parse_commented_json('''{
      // A review note, not metadata
      "Credits": "https://example.com/a//b/*x*/",
      "Grade": ["3", /* note */],
    }''')
    assert document['Credits'] == 'https://example.com/a//b/*x*/'
    assert document['Grade'] == ['3']


def test_real_reviewed_examples_load():
    examples, warnings = refs.load_examples(agent.load_template())
    assert not warnings
    assert len(examples) >= 2
    lesson = next(e for e in examples if e['filename'] == '6thGradeLesson_IdealMetadata.json')
    assert lesson['metadata']['Footage Type'] == 'Teaching Sample'
    assert lesson['metadata']['Product'] == [{'Primary': 'Core Math', 'Secondary': 'i-Ready Classroom Mathematics'}]


def test_hierarchical_product_schema_and_exports(tmp_path):
    template = agent.load_template()
    assert 'Subject' not in [s.name for s in template]
    raw = blank(template)
    raw['Product'] = {'value': [{'Primary': 'Core Math', 'Secondary': 'i-Ready Classroom Mathematics'}],
                      'explanation': 'A product cover', 'sources': ['ocr.json']}
    evidence = agent.Evidence(texts=[('ocr.json', 'i-Ready Classroom Mathematics')])
    result = agent.validate_metadata(template, raw, evidence)
    assert json.loads(agent.format_metadata_value(result['metadata']['Product'])) == raw['Product']['value']
    raw['Product']['value'][0]['Primary'] = None
    with pytest.raises(ValueError, match='hierarchy'):
        agent.validate_metadata(template, raw, evidence)


def test_restrictions_policy_and_explicit_restrictions():
    template = agent.load_template()
    evidence = agent.Evidence(texts=[('ocr.json', 'Assessment items; internal only')])
    raw = blank(template)
    result = agent.validate_metadata(template, raw, evidence)
    assert result['metadata']['Restrictions'] == 'No'
    assert result['field_evidence']['Restrictions']['sources'] == ['metadata_policy']
    raw['Content Tags'] = {'value': ['Assessment'], 'explanation': 'Assessment footage', 'sources': ['ocr.json']}
    assert agent.validate_metadata(template, raw, evidence)['metadata']['Restrictions'] == 'Yes—Internal Only'
    raw['Content Tags']['value'] = []
    raw['Restrictions'] = {'value': 'Yes—Internal Only', 'explanation': 'Explicit internal restriction', 'sources': ['ocr.json']}
    result = agent.validate_metadata(template, raw, evidence)
    assert result['metadata']['Restrictions'] == 'Yes—Internal Only'
    assert result['field_evidence']['Restrictions']['sources'] == ['ocr.json']
