import io
import json
import zipfile

import pytest

import metadata_agent as agent
from evidence_details import collect_image_timing, resolve_image_timing


def blank(template):
    return {s.name: {'value': [] if s.multiple else None, 'sources': [], 'explanation': ''}
            for s in template}


@pytest.mark.parametrize('timing,seconds', [
    ({'timestampSeconds': 2}, 2),
    ({'offsetMilliseconds': 3500}, 3.5),
    ({'timestamp': '00:00:04.500'}, 4.5),
    ({'FrameIndex': 48, 'Fps': 24}, 2),
])
def test_explicit_timing_formats(timing, seconds):
    data = {'images': [{'filename': 'opening.jpg', **timing}]}
    entries = collect_image_timing('manifest.json', data)
    result, warnings = resolve_image_timing(entries, ['nested.zip!/opening.jpg'])
    assert not warnings
    assert result == [{'source': 'nested.zip!/opening.jpg', 'seconds': seconds,
                       'timing_sources': ['manifest.json']}]


def test_inherited_fps_and_nested_sibling_mapping():
    timings = collect_image_timing('thumbs.zip!/folder/manifest.json', {
        'Fps': 25, 'frames': [{'ImagePath': 'opening.jpg', 'FrameIndex': 50}]})
    resolved, warnings = resolve_image_timing(timings, [
        'thumbs.zip!/folder/opening.jpg', 'other.zip!/opening.jpg'])
    assert not warnings
    assert resolved[0]['source'] == 'thumbs.zip!/folder/opening.jpg'
    assert resolved[0]['seconds'] == 2


def test_ambiguous_names_conflicts_and_missing_timing_do_not_invent_opening_frames():
    mappings = collect_image_timing('manifest.json', {
        'frames': [{'filename': 'a.jpg', 'seconds': 1},
                   {'filename': 'b.jpg', 'seconds': 1},
                   {'filename': 'b.jpg', 'seconds': 10},
                   {'filename': 'uuid.jpg', 'FrameIndex': 0}]})
    resolved, warnings = resolve_image_timing(mappings, ['one/a.jpg', 'two/a.jpg', 'b.jpg', 'uuid.jpg'])
    assert resolved == []
    assert len(warnings) == 2
    assert collect_image_timing('manifest.json', {'filename': 'frame.jpg', 'timestamp': 123}) == []
    assert collect_image_timing('manifest.json', {'filename': 'frame.jpg', 'seconds': -1}) == []


def test_archive_preparation_retains_explicit_image_timing():
    from PIL import Image
    image = io.BytesIO()
    Image.new('RGB', (20, 20)).save(image, 'JPEG')
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('manifest.json', json.dumps({'frames': [
            {'filename': 'opening.jpg', 'timestampSeconds': 1}]}))
        archive.writestr('opening.jpg', image.getvalue())
    evidence = agent.prepare_archive(agent.UploadedArchive('clip.zip', bundle.getvalue()))
    assert evidence.image_timing[0]['source'] == 'opening.jpg'
    assert evidence.image_timing[0]['seconds'] == 1


def test_late_archived_opening_image_is_reattached_and_all_batches_remain_available():
    evidence = agent.Evidence(
        texts=[('archive_name', 'Ryan_Booth_6thGradeLesson_1280x720.zip')],
        images=[(f'{i}.jpg', 'encoded') for i in range(14)],
        image_timing=[{'source': '13.jpg', 'seconds': 1, 'timing_sources': ['manifest.json']},
                      {'source': '0.jpg', 'seconds': 20, 'timing_sources': ['manifest.json']}])
    extractor = object.__new__(agent.AzureExtractor)
    calls = []
    def call(template, payload, images=()):
        calls.append((payload, images))
        raw = blank(template)
        if payload['mode'] == 'visual_review' and any(s == '13.jpg' for s, _ in images):
            raw['Credits'] = {'value': 'AnnMarie Lynch', 'sources': ['13.jpg'],
                              'explanation': 'Opening contributor, excluding title and grade'}
        if payload['mode'] == 'final':
            assert payload['credits_policy']['opening_seconds'] == 5
            assert 'names only' in payload['credits_policy']['content']
            assert [s for s, _ in images][0] == '13.jpg'
            raw['Credits'] = payload['visual_candidates'][-1]['Credits']
        return raw
    extractor.call = call
    template = agent.load_template()
    result = agent.validate_metadata(template, extractor.extract(template, evidence), evidence)
    assert result['metadata']['Credits'] == 'AnnMarie Lynch'
    assert evidence.image_review['reviewed_images'] == 14
    assert all([e['source'] for e in p['opening_images']] == ['13.jpg'] for p, _ in calls)


def test_prompt_has_one_consistent_names_only_policy():
    prompt = agent.PROMPT.read_text(encoding='utf-8')
    assert 'preserving names and accompanying video-title lines' not in prompt
    assert 'title but no contributor name does not block filename' in prompt
    assert 'Opening contributor names take precedence' in prompt
