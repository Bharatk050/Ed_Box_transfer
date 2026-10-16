import metadata_agent as agent
import pytest
from product_taxonomy import normalize_products, transcript_product_mentions


def choices():
    return next(s.choices for s in agent.load_template() if s.name == 'Product')


def test_names_variants_boundaries_and_hierarchy():
    mentions = transcript_product_mentions([
        ('transcript.json', 'We use I Ready Classroom Mathematics and BRIGANCE. Ready Math helps too.'),
        ('reference.json', 'Magnetic Literacy'),
    ], choices())
    names = [m['candidate'] for m in mentions]
    assert set(names) == {'i-Ready Classroom Mathematics', 'BRIGANCE', 'Ready Math'}
    assert normalize_products(names, choices()) == [
        {'Primary': 'Core Math' if n == 'i-Ready Classroom Mathematics' else None, 'Secondary': n}
        for n in names]
    assert transcript_product_mentions([('transcript.json', 'CAmera Stiles readiness math')], choices()) == []


def test_repeated_negated_and_unrelated_mentions_retain_context():
    text = 'We do not use i-Ready. An unrelated advertisement says i-Ready.'
    mentions = transcript_product_mentions([('transcript.json', text)], choices())
    assert len(mentions) == 2
    assert all(m['text'] == text for m in mentions)
    assert normalize_products(['i-Ready', 'i-Ready'], choices()) == [
        {'Primary': None, 'Secondary': 'i-Ready'}]


def test_long_transcript_mentions_survive_empty_chunk_candidates():
    template = agent.load_template()
    evidence = agent.Evidence(texts=[('archive_name', 'current.zip'),
        ('transcript.json', 'x ' * agent.CHUNK_CHARS + 'We use i-Ready Classroom Mathematics.')])
    extractor = object.__new__(agent.AzureExtractor)
    payloads = []
    def call(specs, payload, images=()):
        payloads.append(payload)
        return {s.name: {'value': [] if s.multiple else None, 'sources': [], 'explanation': ''}
                for s in specs}
    extractor.call = call
    raw = extractor.extract(template, evidence)
    mention = payloads[-1]['product_mentions'][0]
    assert mention['candidate'] == 'i-Ready Classroom Mathematics'
    assert mention['source'] == 'transcript.json'
    assert 'We use' in mention['text']
    assert raw['Product']['value'] == []


def test_generic_activity_does_not_nominate_product():
    assert transcript_product_mentions([
        ('transcript.json', 'She uses a laptop for reading and math assessments.')], choices()) == []


@pytest.mark.parametrize('product', choices())
def test_every_catalog_product_in_an_independent_video(product):
    mentions = transcript_product_mentions([
        ('transcript.json', f'In this video we demonstrate {product} with students.')], choices())
    assert [m['candidate'] for m in mentions] == [product]
    assert normalize_products([product], choices()) == [
        {'Primary': 'Core Math' if product == 'i-Ready Classroom Mathematics' else None,
         'Secondary': product}]


@pytest.mark.parametrize('source,text', [
    ('speechservices.json', 'We use BRIGANCE.'),
    ('captions.json', '{"text": "We use BRIGANCE."}'),
    ('subtitles.json', 'We use BRIGANCE.'),
    ('emotions.json', '{"transcript_snippets": [{"text": "We use BRIGANCE."}]}'),
])
def test_speech_sources_beyond_transcript_filename(source, text):
    mentions = transcript_product_mentions([(source, text)], choices())
    assert [m['candidate'] for m in mentions] == ['BRIGANCE']
    assert mentions[0]['source'] == source


def test_sequential_videos_do_not_share_product_mentions():
    template = agent.load_template()
    extractor = object.__new__(agent.AzureExtractor)
    payloads = []
    def call(specs, payload, images=()):
        payloads.append(payload)
        return {s.name: {'value': [] if s.multiple else None, 'sources': [], 'explanation': ''}
                for s in specs}
    extractor.call = call
    for name, speech in [('first.zip', 'We use BRIGANCE.'),
                         ('second.zip', 'We use Magnetic Literacy.'),
                         ('third.zip', 'A student uses a computer.')]:
        extractor.extract(template, agent.Evidence(texts=[
            ('archive_name', name), ('transcript.json', speech)]))
    assert [[m['candidate'] for m in p['product_mentions']] for p in payloads] == [
        ['BRIGANCE'], ['Magnetic Literacy'], []]
