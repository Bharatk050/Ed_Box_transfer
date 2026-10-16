import io
import json
import zipfile

import pytest

import metadata_agent as agent
from product_taxonomy import product_mentions, product_mention_batches


def blank(template):
    return {s.name: {'value': [] if s.multiple else None, 'sources': [], 'explanation': ''}
            for s in template}


def choices():
    return next(s.choices for s in agent.load_template() if s.name == 'Product')


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as bundle:
        for name, contents in files.items():
            bundle.writestr(name, contents)
    return buffer.getvalue()


@pytest.mark.parametrize('source,text', [
    ('ocr.json#frame=2400', 'i-Ready Classroom Mathematics'),
    ('analysis.json', '{"curriculum": "BRIGANCE"}'),
    ('archive_name', 'Magnetic_Reading_Foundations_Clip.zip'),
    ('nested.zip!/assets.json', '{"product": "Ready Reading"}'),
    ('labels.json', 'Magnetic Literacy'),
])
def test_product_search_is_not_limited_to_speech(source, text):
    mentions = product_mentions([(source, text)], choices())
    assert len(mentions) == 1
    assert mentions[0]['source'] == source
    assert mentions[0]['text'] == text


def test_nested_and_late_product_evidence_survives_empty_chunk_results():
    nested = zipped({'details.json': json.dumps({
        'description': 'x ' * agent.CHUNK_CHARS + 'We demonstrate BRIGANCE.'})})
    evidence = agent.prepare_archive(agent.UploadedArchive('clip.zip', zipped({
        'nested.zip': nested, 'unreadable.json': '{', 'notes.txt': 'unsupported'})))
    payloads = []
    extractor = object.__new__(agent.AzureExtractor)
    def call(template, payload, images=()):
        payloads.append(payload)
        return blank(template)
    extractor.call = call
    raw = extractor.extract(agent.load_template(), evidence)
    assert payloads[-1]['product_mentions'][0]['source'] == 'nested.zip!/details.json'
    assert 'We demonstrate BRIGANCE' in payloads[-1]['product_mentions'][0]['text']
    result = agent.validate_metadata(agent.load_template(), raw, evidence)
    assert result['archive_coverage']['unreadable'] == 1
    assert result['archive_coverage']['unsupported'] == 1
    assert len(result['warnings']) == 3  # unreadable, unsupported, no images


def test_oversized_mention_set_reviews_every_excerpt_and_preserves_sources():
    evidence = agent.Evidence(texts=[('archive_name', 'current.zip'),
        ('ocr.json', ('x ' * 600 + 'BRIGANCE\n') * 32)])
    expected = product_mentions(evidence.texts, choices())
    batches = product_mention_batches(expected)
    assert len(batches) > 1
    assert [item for batch in batches for item in batch] == expected
    assert all(len(json.dumps(batch, ensure_ascii=False)) <= 12000 for batch in batches)
    payloads = []
    extractor = object.__new__(agent.AzureExtractor)
    def call(template, payload, images=()):
        payloads.append(payload)
        raw = blank(template)
        if payload['mode'] == 'product_review':
            raw['Product'] = {'value': [{'Primary': None, 'Secondary': 'BRIGANCE'}],
                              'sources': ['ocr.json'], 'explanation': 'Product named in supplied OCR'}
        return raw
    extractor.call = call
    extractor.extract(agent.load_template(), evidence)
    reviews = [p for p in payloads if p['mode'] == 'product_review']
    assert [m for p in reviews for m in p['product_mentions']] == expected
    assert len(payloads[-1]['product_review_candidates']) == len(batches)
    assert all(c['Product']['sources'] == ['ocr.json'] for c in payloads[-1]['product_review_candidates'])
    assert evidence.product_review['mentions_found'] == 32


def test_later_image_product_reaches_final_without_text_mentions():
    evidence = agent.Evidence(texts=[('archive_name', 'clip.zip')],
        images=[(f'thumbs.zip!/{i}.jpg', 'encoded') for i in range(14)])
    template = agent.load_template()
    extractor = object.__new__(agent.AzureExtractor)
    def call(specs, payload, images=()):
        raw = blank(specs)
        if any(source == 'thumbs.zip!/13.jpg' for source, _ in images):
            raw['Product'] = {'value': [{'Primary': 'Core Math',
                                        'Secondary': 'i-Ready Classroom Mathematics'}],
                              'sources': ['thumbs.zip!/13.jpg'], 'explanation': 'Readable cover'}
        if payload['mode'] == 'final':
            assert payload['product_mentions'] == []
            raw['Product'] = payload['visual_candidates'][-1]['Product']
        return raw
    extractor.call = call
    result = agent.validate_metadata(template, extractor.extract(template, evidence), evidence)
    assert result['metadata']['Product'] == [
        {'Primary': 'Core Math', 'Secondary': 'i-Ready Classroom Mathematics'}]
    assert result['product_review']['images_reviewed'] == 14
    public = json.loads(agent.build_exports(template, [result], [])['metadata.json'])
    assert 'product_review' not in public[0]


def test_generic_and_equipment_brands_do_not_match_catalog_products():
    assert product_mentions([('ocr.json', 'JVC Dell Intel 26 learning math laptop')], choices()) == []


def test_negation_conflict_and_multiple_names_are_retained_for_semantic_review():
    text = 'We do not use i-Ready. The screen shows BRIGANCE and Ready Math.'
    mentions = product_mentions([('analysis.json', text)], choices())
    assert {m['candidate'] for m in mentions} == {'i-Ready', 'BRIGANCE', 'Ready Math'}
    assert all(m['text'] == text for m in mentions)
