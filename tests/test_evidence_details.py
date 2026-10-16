import io
import json
import zipfile

from PIL import Image

import metadata_agent as agent
from evidence_details import collect_json_details, supporting_excerpts


def archive(files, name='example_1920x1080.zip'):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as bundle:
        for filename, contents in files.items():
            bundle.writestr(filename, contents)
    return agent.UploadedArchive(name, buffer.getvalue())


def blank(template):
    return {spec.name: {'value': [] if spec.multiple else None, 'sources': [], 'explanation': ''}
            for spec in template}


def test_explicit_video_properties_win_and_attributes_survive():
    media = {'video': {'width': 1280, 'height': 720, 'duration': '00:01:20',
                       'Content Owner': 'Example owner', 'Creation Date': '2026-01-15',
                       'school': 'Example MS'}}
    transcript = [{'transcription': {'durationMilliseconds': 100000,
                                    'combinedPhrases': [{'text': 'A lesson'}],
                                    'phrases': [{'text': 'Welcome', 'offsetMilliseconds': 1200}]}}]
    evidence = agent.prepare_archive(archive({'media.json': json.dumps(media),
                                             'transcript.json': json.dumps(transcript)}))
    assert evidence.quality == ('720', 'media.json')
    assert evidence.duration == (80, 'media.json')
    texts = dict(evidence.texts)
    assert 'Content Owner' in texts['media.json']
    assert '2026-01-15' in texts['media.json']
    assert 'offsetMilliseconds' in texts['transcript.json']
    assert any(fact['path'] == '$.video.school' for fact in evidence.json_details)


def test_thumbnail_dimensions_and_unlabeled_numbers_are_not_video_properties():
    data = {'video': {'frames': [{'width': 1280, 'height': 720, 'duration': 10}]},
            'pages': [{'width': 1920, 'height': 1080}], 'duration': 100, 'width': 1920, 'height': 1080}
    _, technical = collect_json_details('ocr.json', data)
    assert technical == []


def test_equally_explicit_technical_conflicts_remain_unknown():
    data = [{'video': {'width': 1920, 'height': 1080, 'durationSeconds': 20}},
            {'video': {'width': 1280, 'height': 720, 'durationSeconds': 25}}]
    evidence = agent.prepare_archive(archive({'properties.json': json.dumps(data)}))
    assert evidence.duration is None
    assert evidence.quality is None
    assert len(evidence.warnings) >= 2


def test_emotion_files_supply_speech_not_predictions():
    evidence = agent.Evidence()
    text = agent.summarize_json('emotions.json', [
        {'Text': 'I use i-Ready', 'TimeRange': {'Start': '00:01:00', 'End': '00:01:02'},
         'DominantEmotion': {'Type': 'Sad', 'Probability': 99}}], evidence)
    assert 'I use i-Ready' in text
    assert '00:01:00' in text
    assert 'DominantEmotion' not in text
    assert 'Sad' not in text


def test_inventory_accounts_for_every_nested_member():
    image = io.BytesIO()
    Image.new('RGB', (20, 20)).save(image, 'JPEG')
    nested = archive({'frame.jpg': image.getvalue(), 'bad.jpg': b'broken'}).data
    evidence = agent.prepare_archive(archive({
        'nested.zip': nested, 'bad.json': b'{', 'notes.txt': b'unsupported',
        'metadata.json': b'{"description":"A useful description"}'}))
    assert len(evidence.inventory) == 6
    counts = {status: sum(entry['status'] == status for entry in evidence.inventory)
              for status in ['opened_nested_zip', 'prepared_image', 'prepared_json', 'unreadable', 'unsupported']}
    assert counts == {'opened_nested_zip': 1, 'prepared_image': 1, 'prepared_json': 1,
                      'unreadable': 2, 'unsupported': 1}
    assert not any(entry['status'] == 'pending' for entry in evidence.inventory)


def test_long_reconciliation_preserves_original_late_metadata_context():
    template = agent.load_template()
    text = 'x' * (agent.CHUNK_CHARS + 100) + '\nThe featured teacher is Ryan Booth at Example MS.'
    evidence = agent.Evidence(texts=[('archive_name', 'lesson.zip'), ('transcript.json', text)])
    extractor = object.__new__(agent.AzureExtractor)
    calls = []
    def call(specs, payload, images=()):
        calls.append(payload)
        raw = blank(specs)
        if payload['mode'] == 'final' or 'Ryan Booth' in payload['evidence'][0]['text']:
            raw['Credits'] = {'value': 'Ryan Booth', 'sources': ['transcript.json'], 'explanation': 'Featured teacher'}
        return raw
    extractor.call = call
    extractor.extract(template, evidence)
    final = calls[-1]
    assert final['mode'] == 'final'
    assert final['required_field_checklist'] == [spec.name for spec in template]
    assert any('Ryan Booth at Example MS' in excerpt['text'] for excerpt in final['original_supporting_excerpts'])
    assert all(excerpt['source'] == 'transcript.json' for excerpt in final['original_supporting_excerpts'])
    assert all(excerpt['offset'] > 0 for excerpt in final['original_supporting_excerpts'])


def test_excerpts_are_bounded_and_do_not_invent_context():
    template = agent.load_template()
    candidate = blank(template)
    candidate['Grade'] = {'value': ['3'], 'sources': ['missing'], 'explanation': 'guess'}
    evidence = agent.Evidence(texts=[('transcript.json', 'a' * 30000)])
    assert supporting_excerpts(evidence, [candidate]) == []
    candidate['Credits'] = {'value': 'a', 'sources': ['transcript.json'], 'explanation': ''}
    excerpts = supporting_excerpts(evidence, [candidate], budget=1200)
    assert len(json.dumps(excerpts, ensure_ascii=False)) <= 1200


def test_successful_review_updates_coverage_without_exposing_sources():
    template = agent.load_template()
    evidence = agent.prepare_archive(archive({'metadata.json': b'{"description":"A lesson"}'}))
    extractor = object.__new__(agent.AzureExtractor)
    extractor.call = lambda specs, payload, images=(): blank(specs)
    raw = extractor.extract(template, evidence)
    result = agent.validate_metadata(template, raw, evidence)
    assert result['archive_coverage'] == {'reviewed_json': 1}
    result['source_archive'] = 'example.zip'
    downloads = agent.build_exports(template, [result], [])
    public = json.loads(downloads['metadata.json'])[0]
    assert set(public) == {spec.name for spec in template}
    assert 'archive_coverage' not in public


def test_label_and_object_temporal_context_is_preserved():
    evidence = agent.Evidence()
    labels = agent.summarize_json('labels.computervision.json', {'Fps': 24, 'Results': [
        {'FrameIndex': 48, 'Label': [{'name': 'classroom', 'confidence': .9}]},
        {'FrameIndex': 96, 'Label': [{'name': 'classroom', 'confidence': .95}]}]}, evidence)
    assert json.loads(labels)['observations']['classroom']['last_frame'] == 96
    objects = agent.summarize_json('detectedobjects.json', {'results': [
        {'type': 'Laptop', 'instances': [{'start': '00:00:02', 'end': '00:00:03', 'frame': 48, 'confidence': .9}]}]}, evidence)
    assert json.loads(objects)['observations'][0]['start'] == '00:00:02'


def test_detailed_speech_is_not_lost_when_combined_text_exists():
    evidence = agent.Evidence()
    text = agent.summarize_json('transcript.json', [{'transcription': {
        'combinedPhrases': [{'text': 'Welcome to our classroom.'}],
        'phrases': [{'text': 'I teach sixth grade using i-Ready at Example MS.',
                     'speaker': 1, 'offsetMilliseconds': 1000}]
    }}], evidence)
    assert 'Welcome to our classroom.' in text
    assert 'I teach sixth grade using i-Ready at Example MS.' in text
    assert 'speaker' in text


def test_original_attributes_reach_final_even_when_chunks_miss_them():
    evidence = agent.prepare_archive(archive({'metadata.json': json.dumps({
        'description': 'x ' * agent.CHUNK_CHARS,
        'video': {'school': 'Example MS', 'Content Owner': 'Example owner',
                  'Creation Date': '2026-01-15'}})}))
    payloads = []
    extractor = object.__new__(agent.AzureExtractor)
    def call(specs, payload, images=()):
        payloads.append(payload)
        return blank(specs)
    extractor.call = call
    extractor.extract(agent.load_template(), evidence)
    final = payloads[-1]
    assert any(fact['value'] == 'Example MS' for fact in final['original_json_attributes'])
    assert any(fact['value'] == 'Example owner' for fact in final['original_json_attributes'])
    assert final['source_inventory'][0]['source'] == 'metadata.json'
