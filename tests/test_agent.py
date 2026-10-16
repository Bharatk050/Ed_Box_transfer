import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

import metadata_agent as agent


def blank(template):
    return {s.name: {'value': [] if s.multiple else None, 'explanation': '', 'sources': []} for s in template}


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def picture():
    buffer = io.BytesIO()
    Image.new('RGB', (20, 20)).save(buffer, format='JPEG')
    return buffer.getvalue()


def test_template_and_schema():
    template = agent.load_template()
    assert len(template) == 16
    assert next(s for s in template if s.name == 'Quality').choices == ['720', '1080', '4K', 'Stringout']
    assert next(s for s in template if s.name == 'Content Tags').multiple
    assert 'Creation Date' in [s.name for s in template]
    assert agent.response_model(template).model_validate(blank(template))
    invalid = blank(template)
    invalid['Quality']['value'] = 'HD'
    with pytest.raises(ValueError):
        agent.response_model(template).model_validate(invalid)


def test_supplied_archive():
    evidence = agent.prepare_archive(next(agent.ROOT.glob('*.zip')))
    assert evidence.duration[0] == pytest.approx(917.056)
    assert evidence.quality == ('1080', 'archive_name')
    assert len(evidence.images) == 36
    assert evidence.image_review['images_found'] == 36
    assert evidence.opening_text[0]['seconds'] == 0
    assert evidence.opening_text[0]['text'] == 'AnnMarie Lynch\nGrade 3 Students\nINTERVIEWS'
    assert evidence.opening_text[0]['source'] == 'ocr.json#frame=0'
    text = '\n'.join(t for _, t in evidence.texts)
    assert 'Grade 3 Students' in text
    assert 'INTERVIEWS' in text
    assert 'i-Ready' in text
    template = agent.load_template()
    result = agent.validate_metadata(template, blank(template), evidence)
    assert result['metadata']['Length'] == '00:15:17'
    assert result['metadata']['Quality'] == '1080'
    assert result['metadata']['Restrictions'] == 'No'


def test_nested_images_and_bad_json(tmp_path):
    files = {f'{i}.jpg': picture() for i in range(10)}
    archive = tmp_path / 'test.zip'
    archive.write_bytes(zip_bytes({'folder/thumbs.zip': zip_bytes(files), 'broken.json': '{',
                                   'badocr.json': '[]', 'notes.txt': 'skip'}))
    evidence = agent.prepare_archive(archive)
    assert len(evidence.images) == 10
    assert evidence.images[0][0].endswith('!/0.jpg')
    assert evidence.images[-1][0].endswith('!/9.jpg')
    assert any('broken.json' in w for w in evidence.warnings)
    assert any('notes.txt' in w for w in evidence.warnings)


def test_limits_propagate_from_nested_zip(tmp_path, monkeypatch):
    archive = tmp_path / 'large.zip'
    archive.write_bytes(zip_bytes({'nested.zip': zip_bytes({'data.json': '"' + 'x' * 1000 + '"'})}))
    monkeypatch.setattr(agent, 'MAX_TOTAL', 2000)
    with pytest.raises(agent.ArchiveLimitError):
        agent.prepare_archive(archive)


def test_validation_clears_unsupported_values():
    template = agent.load_template()
    raw = blank(template)
    raw['Content Owner'] = {'value': 'Someone', 'sources': ['invented'], 'explanation': 'guess'}
    raw['State'] = {'value': 'Mars', 'sources': ['archive_name'], 'explanation': 'guess'}
    raw['Creation Date'] = {'value': 'yesterday', 'sources': ['archive_name'], 'explanation': 'date'}
    result = agent.validate_metadata(template, raw, agent.Evidence(texts=[('archive_name', 'test.zip')]))
    assert all(result['metadata'][key] is None for key in ['Content Owner', 'State', 'Creation Date'])
    assert len(result['warnings']) == 3


def test_chunks_keep_source_and_use_all_text():
    text = 'a' * (agent.CHUNK_CHARS * 2 + 10)
    chunks = agent.evidence_chunks(agent.Evidence(texts=[('transcript.json', text)]))
    assert ''.join(c['text'] for c in chunks) == text
    assert all(c['source'] == 'transcript.json' for c in chunks)


def test_chunk_reconciliation_and_images():
    template = agent.load_template()
    extractor = object.__new__(agent.AzureExtractor)
    calls = []
    def call(specs, payload, images=()):
        calls.append((payload, images))
        return blank(specs)
    extractor.call = call
    evidence = agent.Evidence(texts=[('archive_name', 'sample.zip'),
                                    ('transcript.json', 'x' * (agent.CHUNK_CHARS + 1))],
                              images=[('thumbnail.jpg', 'encoded')])
    extractor.extract(template, evidence)
    assert calls[-1][0]['mode'] == 'final'
    assert len(calls[-1][0]['candidates']) == 3
    assert calls[-1][1] == evidence.images


def test_batch_success_failure_exports(tmp_path):
    good = tmp_path / 'good.zip'
    good.write_bytes(zip_bytes({'ocr.json': json.dumps({'Results': [{'Ocr': {'content': 'Grade 3 Students'}}]})}))
    bad = tmp_path / 'bad.zip'
    bad.write_text('not a zip')
    template = agent.load_template()
    class Fake:
        def extract(self, specs, evidence):
            raw = blank(specs)
            raw['Grade'] = {'value': ['3'], 'explanation': 'Grade 3 Students', 'sources': ['ocr.json']}
            return raw
    exports, results, statuses = agent.run_batch([good, bad], template, Fake())
    assert [s['status'] for s in statuses] == ['success', 'failed']
    assert results[0]['metadata']['Grade'] == ['3']
    assert len(json.loads(exports['metadata.json'])) == 1
    assert 'Source Archive' not in exports['metadata.csv'].decode('utf-8-sig')
    assert json.loads(exports['metadata.json'])[0] == results[0]['metadata']
    assert set(json.loads(exports['metadata.json'])[0]) == {spec.name for spec in template}
    assert len(agent.scan_archives(tmp_path, tmp_path / 'output')) == 2
    assert not (tmp_path / 'output').exists()


def test_azure_request_and_refusal():
    template = agent.load_template()
    requests = []
    parsed = agent.response_model(template).model_validate(blank(template))
    message = SimpleNamespace(parsed=parsed, refusal=None)
    def parse(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])
    extractor = object.__new__(agent.AzureExtractor)
    extractor.deployment = 'test-deployment'
    extractor.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    extractor.call(template, {'evidence': []}, [('sample.jpg', 'encoded')])
    assert requests[0]['model'] == 'test-deployment'
    assert requests[0]['messages'][1]['content'][-1]['type'] == 'image_url'
    message.refusal = 'refused'
    with pytest.raises(ValueError, match='refusal'):
        extractor.call(template, {})


def test_prompt_file_edits_reach_the_next_azure_request(tmp_path, monkeypatch):
    template = agent.load_template()
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('First prompt version: extract Role and Grade.', encoding='utf-8')
    monkeypatch.setattr(agent, 'PROMPT', prompt)
    requests = []
    parsed = agent.response_model(template).model_validate(blank(template))
    def parse(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))])
    extractor = object.__new__(agent.AzureExtractor)
    extractor.deployment = 'test-deployment'
    extractor.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    extractor.call(template, {'mode': 'final'})
    prompt.write_text('Revised prompt: retain teacher interview grades and identify speaker Role.', encoding='utf-8')
    extractor.call(template, {'mode': 'final'})
    assert requests[0]['messages'][0] == {'role': 'system', 'content': 'First prompt version: extract Role and Grade.'}
    assert requests[1]['messages'][0] == {'role': 'system', 'content': prompt.read_text(encoding='utf-8')}
    assert requests[0]['messages'][0] != requests[1]['messages'][0]


def test_api_failure_is_recorded(tmp_path):
    archive = tmp_path / 'sample.zip'
    archive.write_bytes(zip_bytes({'ocr.json': '{"Results": [{"Ocr": {"content": "Interview"}}]}'}))
    class Failed:
        def extract(self, *_):
            raise TimeoutError('secret endpoint details')
    _, _, statuses = agent.run_batch([archive], agent.load_template(), Failed())
    assert statuses[0]['error'] == 'TimeoutError'


def test_streamlit_scan_and_processing_rerun(tmp_path, monkeypatch):
    template = agent.load_template()
    calls = []
    class Fake:
        def extract(self, specs, evidence):
            calls.append(1)
            raw = blank(specs)
            raw['Footage Type'] = {'value': 'Interview', 'sources': ['ocr.json'], 'explanation': 'INTERVIEWS'}
            return raw
    monkeypatch.setattr(agent, 'AzureExtractor', Fake)
    app = AppTest.from_file(str(agent.ROOT / 'app.py'), default_timeout=30).run()
    assert not app.exception
    app.radio[0].set_value('Scan local folder').run()
    sample = next(agent.ROOT.glob('*.zip'))
    (tmp_path / sample.name).write_bytes(sample.read_bytes())
    app.sidebar.text_input[0].set_value(str(tmp_path)).run()
    next(b for b in app.button if b.label == 'Scan folder').click().run()
    assert app.multiselect[0].value
    next(b for b in app.button if b.label == 'Process selected ZIPs').click().run(timeout=30)
    assert not app.exception
    assert calls == [1]
    assert app.session_state['last_run'][2][0]['status'] == 'success'
    app.run()
    assert calls == [1]


def test_uploaded_zip_processing(tmp_path):
    template = agent.load_template()
    upload = agent.UploadedArchive('video_1920x1080.zip', zip_bytes({
        'ocr.json': '{"Results": [{"Ocr": {"content": "Grade 3 Students INTERVIEWS"}}]}',
        'thumbs.zip': zip_bytes({'frame.jpg': picture()})}))
    evidence = agent.prepare_archive(upload)
    assert evidence.quality == ('1080', 'archive_name')
    assert len(evidence.images) == 1
    class Fake:
        def extract(self, specs, evidence):
            return blank(specs)
    exports, results, statuses = agent.run_batch([upload], template, Fake())
    assert statuses[0]['status'] == 'success'
    assert results[0]['source_archive'] == 'uploaded:video_1920x1080.zip'
    assert exports['metadata.csv']
    assert not (tmp_path / 'out').exists()


def test_azure_version_correction_and_endpoint_validation(monkeypatch):
    import openai
    settings = {}
    monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', ' https://example.openai.azure.com/ ')
    monkeypatch.setenv('AZURE_OPENAI_DEPLOYMENT', ' example-deployment ')
    monkeypatch.setenv('AZURE_OPENAI_API_KEY', 'secret')
    monkeypatch.setenv('AZURE_OPENAI_API_VERSION', '2024-10-21-preview')
    def client(**kwargs):
        settings.update(kwargs)
        return object()
    monkeypatch.setattr(openai, 'AzureOpenAI', client)
    extractor = agent.AzureExtractor()
    assert settings['api_version'] == '2024-10-21'
    assert settings['azure_endpoint'] == 'https://example.openai.azure.com'
    assert extractor.deployment == 'example-deployment'
    monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://example.openai.azure.com/openai/deployments/foo')
    with pytest.raises(ValueError, match='resource root'):
        agent.AzureExtractor()


def test_azure_404_errors_are_actionable_and_sanitized():
    import httpx
    from openai import NotFoundError
    response = httpx.Response(404, request=httpx.Request('POST', 'https://example.openai.azure.com'))
    exc = NotFoundError('secret details', response=response, body={'error': {'code': 'DeploymentNotFound'}})
    assert 'exact deployment name' in agent.describe_error(exc)
    assert 'secret' not in agent.describe_error(exc)
    generic = NotFoundError('secret details', response=response, body={'code': '404'})
    assert '2024-10-21' in agent.describe_error(generic)


def test_all_nested_images_reviewed_and_reconciled():
    template = agent.load_template()
    evidence = agent.Evidence(
        texts=[('archive_name', 'current.zip'), ('ocr.json#frame=0', 'Opening credit')],
        images=[(f'thumbs.zip!/{i}.jpg', 'encoded') for i in range(14)],
        opening_text=[{'source': 'ocr.json#frame=0', 'seconds': 0, 'text': 'Opening credit'}])
    calls = []
    extractor = object.__new__(agent.AzureExtractor)
    def call(specs, payload, images=()):
        calls.append((payload, images))
        raw = blank(specs)
        raw['Credits'] = {'value': 'Opening credit', 'explanation': 'Opening card',
                          'sources': ['ocr.json#frame=0']}
        if payload['mode'] == 'final':
            raw['People'] = {'value': ['Student'], 'explanation': 'Featured in final batch',
                             'sources': ['thumbs.zip!/13.jpg']}
        return raw
    extractor.call = call
    raw = extractor.extract(template, evidence)
    visual_calls = [(payload, images) for payload, images in calls if payload['mode'] == 'visual_review']
    assert [len(images) for _, images in visual_calls] == [6, 6, 2]
    assert [source for _, images in visual_calls for source, _ in images] == [source for source, _ in evidence.images]
    assert all(payload['opening_text'] == evidence.opening_text for payload, _ in calls)
    assert all(payload['current_video_filename'] == {'source': 'archive_name', 'text': 'current.zip'}
               for payload, _ in calls)
    assert len(calls[-1][0]['visual_candidates']) == 3
    assert len(calls[-1][1]) == 6
    assert evidence.image_review['reviewed_images'] == 14
    result = agent.validate_metadata(template, raw, evidence)
    assert result['metadata']['People'] == ['Student']
    assert result['metadata']['Credits'] == 'Opening credit'


def test_opening_ocr_uses_timestamps_not_json_order():
    evidence = agent.Evidence()
    agent.summarize_json('ocr.json', {'Fps': 24, 'Results': [
        {'FrameIndex': 240, 'Ocr': {'content': 'Late scene'}},
        {'FrameIndex': 48, 'Ocr': {'content': 'Opening title'}},
        {'FrameIndex': 24, 'Ocr': {'content': 'Opening title'}},
    ]}, evidence)
    assert len(evidence.opening_text) == 1
    assert evidence.opening_text[0]['text'] == 'Opening title'
    assert evidence.opening_text[0]['seconds'] == 2


def test_failed_visual_batch_does_not_claim_full_review():
    template = agent.load_template()
    evidence = agent.Evidence(texts=[('archive_name', 'test.zip')],
                              images=[(f'{i}.jpg', 'x') for i in range(8)])
    extractor = object.__new__(agent.AzureExtractor)
    def call(specs, payload, images=()):
        if payload.get('batch_number') == 2:
            raise TimeoutError('failed batch')
        return blank(specs)
    extractor.call = call
    with pytest.raises(TimeoutError):
        extractor.extract(template, evidence)
    assert evidence.image_review['reviewed_images'] == 6
