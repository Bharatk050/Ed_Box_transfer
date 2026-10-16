"""Archive evidence preparation, Azure extraction, and in-memory downloads."""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from PIL import Image
from pydantic import ConfigDict, create_model
from reference_examples import load_examples, select_examples
from product_taxonomy import (PRODUCT_PARENTS, normalize_products, product_mentions,
                              product_mention_batches)
from evidence_details import (collect_json_details, duration_seconds, supporting_excerpts,
                              collect_image_timing, resolve_image_timing)
from metadata_rules import filename_grades

ROOT = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = ROOT / 'Video Metadata(Proposed Box Metadata (MK Edit)).csv'
PROMPT = ROOT / 'prompts' / 'metadata_extraction.md'
MAX_MEMBER = 32 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
CHUNK_CHARS = 24000
IMAGE_BATCH_SIZE = 6
OPENING_SECONDS = 5
METADATA_SCHEMA_VERSION = 'role-mentioned-grades-v1'
STATES = set(('Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|District of Columbia').split('|'))


@dataclass
class TemplateField:
    name: str
    kind: str
    choices: list[str] = field(default_factory=list)

    @property
    def multiple(self):
        return 'as many' in self.kind.lower()


def load_template(path=DEFAULT_TEMPLATE):
    fields = {}
    current = None
    raw = Path(path).read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252')
    with io.StringIO(text, newline='') as handle:
        for row in csv.reader(handle):
            cells = [s.strip() for s in row[:3]]
            cells += [''] * (3 - len(cells))
            name, kind, value = cells
            if name == 'Field':
                continue
            if not any(cells):
                current = None
                continue
            if name:
                current = fields.setdefault(name, TemplateField(name, kind))
            if current and value and 'choose' in current.kind.lower():
                if value not in current.choices:
                    current.choices.append(value)
    if not fields:
        raise ValueError('The metadata template has no fields.')
    return list(fields.values())


def response_model(template):
    definitions = {}
    for index, spec in enumerate(template):
        scalar = Literal[tuple(spec.choices)] if spec.choices else str
        if spec.name == 'Product':
            scalar = create_model('ProductHierarchy', __config__=ConfigDict(extra='forbid'),
                                  Primary=(Literal[tuple(sorted(set(PRODUCT_PARENTS.values())))] | None, ...),
                                  Secondary=(scalar, ...))
        value_type = list[scalar] if spec.multiple else scalar | None
        item = create_model(f'Field{index}', __config__=ConfigDict(extra='forbid'),
                            value=(value_type, ...), explanation=(str, ...), sources=(list[str], ...))
        definitions[spec.name] = (item, ...)
    return create_model('VideoMetadata', __config__=ConfigDict(extra='forbid'), **definitions)


@dataclass
class Evidence:
    texts: list[tuple[str, str]] = field(default_factory=list)
    images: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration: tuple[float, str] | None = None
    quality: tuple[str, str] | None = None
    reference_examples: list[dict] = field(default_factory=list)
    opening_text: list[dict] = field(default_factory=list)
    image_review: dict = field(default_factory=dict)
    inventory: list[dict] = field(default_factory=list)
    json_details: list[dict] = field(default_factory=list)
    technical_facts: list[dict] = field(default_factory=list)
    product_review: dict = field(default_factory=dict)
    image_timing: list[dict] = field(default_factory=list)


class ArchiveLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadedArchive:
    name: str
    data: bytes


def prepare_archive(path):
    uploaded = isinstance(path, UploadedArchive)
    path = path if uploaded else Path(path)
    name = Path(path.name.replace('\\', '/')).name
    evidence = Evidence(texts=[('archive_name', name)])
    resolution = re.search(r'(?<!\d)(\d{3,4})x(\d{3,4})(?!\d)', Path(name).stem, re.I)
    if resolution:
        height = int(resolution[2])
        quality = {720: '720', 1080: '1080', 2160: '4K'}.get(height)
        if quality:
            evidence.quality = (quality, 'archive_name')
    images = []
    image_timings = []
    budget = [0, 0]

    def walk(archive, prefix='', depth=0):
        for member in archive.infolist():
            if member.is_dir():
                continue
            budget[1] += 1
            if budget[1] > 10000:
                raise ArchiveLimitError('Archive exceeds 10,000 entries.')
            source = prefix + member.filename
            record = {'source': source, 'status': 'pending', 'bytes': member.file_size}
            evidence.inventory.append(record)
            suffix = Path(member.filename).suffix.lower()
            if suffix not in {'.json', '.zip', '.jpg', '.jpeg', '.png'}:
                evidence.warnings.append(f'Unsupported file skipped: {source}')
                record['status'] = 'unsupported'
                continue
            budget[0] += member.file_size
            if member.file_size > MAX_MEMBER or budget[0] > MAX_TOTAL:
                raise ArchiveLimitError('Archive exceeds member or total expanded-size limit.')
            if member.flag_bits & 1:
                evidence.warnings.append(f'Encrypted member skipped: {source}')
                record['status'] = 'encrypted'
                continue
            try:
                raw = archive.read(member)
                if suffix == '.zip':
                    if depth >= 2:
                        evidence.warnings.append(f'Nesting limit reached: {source}')
                        record['status'] = 'nesting_limit'
                        continue
                    with zipfile.ZipFile(io.BytesIO(raw)) as nested:
                        walk(nested, source + '!/', depth + 1)
                    record['status'] = 'opened_nested_zip'
                elif suffix == '.json':
                    data = json.loads(raw)
                    image_timings.extend(collect_image_timing(source, data))
                    facts, technical = collect_json_details(source, data)
                    evidence.json_details.extend(facts)
                    evidence.technical_facts.extend(technical)
                    text = summarize_json(source, data, evidence)
                    if facts:
                        text += '\nExplicit JSON attributes (paths identify context):\n' + json.dumps(facts, ensure_ascii=False)
                    if text:
                        evidence.texts.append((source, text))
                    record['status'] = 'prepared_json' if text else 'no_relevant_evidence'
                else:
                    images.append((source, raw, record))
            except (ValueError, UnicodeError, zipfile.BadZipFile, AttributeError, TypeError, RuntimeError) as exc:
                if isinstance(exc, ArchiveLimitError):
                    raise
                record['status'] = 'unreadable'
                evidence.warnings.append(f'Unreadable member: {source} ({type(exc).__name__})')

    if (len(path.data) if uploaded else path.stat().st_size) > MAX_TOTAL:
        raise ValueError('Compressed archive exceeds 256 MiB.')
    with zipfile.ZipFile(io.BytesIO(path.data) if uploaded else path) as archive:
        walk(archive)
    for source, raw, record in images:
        try:
            with Image.open(io.BytesIO(raw)) as image:
                image.thumbnail((2048, 2048))
                buffer = io.BytesIO()
                image.convert('RGB').save(buffer, format='JPEG', quality=95)
                evidence.images.append((source, base64.b64encode(buffer.getvalue()).decode()))
                record['status'] = 'prepared_image'
        except (OSError, ValueError, Image.DecompressionBombError):
            evidence.warnings.append(f'Unreadable thumbnail: {source}')
            record['status'] = 'unreadable'
    evidence.image_timing, timing_warnings = resolve_image_timing(
        image_timings, [source for source, _ in evidence.images])
    evidence.warnings.extend(timing_warnings)
    resolve_technical_facts(evidence)
    if len(evidence.texts) == 1 and not evidence.images:
        raise ValueError('No usable JSON analysis or thumbnails found.')
    if not evidence.images:
        evidence.warnings.append('No usable thumbnails found; proceeding with JSON evidence.')
    evidence.image_review = {'images_found': len(images), 'readable_images': len(evidence.images),
                             'reviewed_images': 0, 'completed_batches': 0,
                             'ordering': 'archive order; timestamps are not inferred from UUID image filenames'}
    return evidence


def summarize_json(source, data, evidence):
    name = re.sub(r'[^a-z0-9]', '', source.lower())
    if 'transcript' in name and isinstance(data, list):
        texts = []
        for entry in data:
            transcription = entry.get('transcription', {})
            duration = duration_seconds(transcription.get('durationMilliseconds'), milliseconds=True)
            if duration is not None:
                evidence.technical_facts.append({'field': 'Length', 'value': duration, 'source': source, 'priority': 2})
                if evidence.duration is None or duration > evidence.duration[0]:
                    evidence.duration = (duration, source)
            # Combined text can be incomplete; detailed phrases may contain the
            # only product, grade, speaker introduction, or school mention.
            phrases = (transcription.get('combinedPhrases') or []) + (transcription.get('phrases') or [])
            texts.extend(p.get('text', '') for p in phrases)
        segments = []
        for entry in data:
            for phrase in entry.get('transcription', {}).get('phrases', []):
                if phrase.get('text'):
                    segments.append({k: phrase[k] for k in ('text', 'offsetMilliseconds', 'durationMilliseconds', 'offset', 'speaker') if k in phrase})
        return '\n'.join(dict.fromkeys(texts)) + ('\nTimed transcript segments: ' + json.dumps(segments, ensure_ascii=False) if segments else '')
    if 'ocr' in name and isinstance(data, dict):
        fps = data.get('Fps')
        if isinstance(fps, (int, float)) and fps > 0:
            seen = set()
            for result in data.get('Results', []):
                frame = result.get('FrameIndex')
                text = result.get('Ocr', {}).get('content', '').strip()
                if isinstance(frame, (int, float)) and 0 <= frame / fps <= OPENING_SECONDS and text and text not in seen:
                    seen.add(text)
                    identifier = f'{source}#frame={frame}'
                    entry = {'source': identifier, 'seconds': round(frame / fps, 3), 'text': text}
                    evidence.opening_text.append(entry)
                    evidence.texts.append((identifier, json.dumps(entry, ensure_ascii=False)))
        entries = {}
        for result in data.get('Results', []):
            text = result.get('Ocr', {}).get('content', '').strip()
            if text:
                entry = entries.setdefault(text, {'text': text, 'first_frame': result.get('FrameIndex'), 'last_frame': result.get('FrameIndex')})
                entry['last_frame'] = result.get('FrameIndex')
        return json.dumps({'fps': fps, 'ocr_observations': list(entries.values())}, ensure_ascii=False)
    if 'labels' in name and isinstance(data, dict):
        counts = Counter()
        for result in data.get('Results', []):
            for label in result.get('Label', []):
                if label.get('confidence', 0) >= .6:
                    counts[label.get('name', '')] += 1
        observations = {}
        for result in data.get('Results', []):
            for label in result.get('Label', []):
                entry = observations.setdefault(label.get('name', ''), {'first_frame': result.get('FrameIndex'), 'last_frame': result.get('FrameIndex'), 'peak_confidence': 0})
                entry['last_frame'] = result.get('FrameIndex')
                entry['peak_confidence'] = max(entry['peak_confidence'], label.get('confidence', 0))
        return json.dumps({'label_frame_counts': counts.most_common(), 'observations': observations, 'fps': data.get('Fps')})
    if 'detectedobjects' in name and isinstance(data, dict):
        counts = Counter(r.get('type', '') for r in data.get('results', []))
        observations = []
        for track in data.get('results', []):
            instances = track.get('instances', [])
            if instances:
                peak = max(instances, key=lambda instance: instance.get('confidence', 0))
                observations.append({'type': track.get('type'), 'start': instances[0].get('start'),
                                     'end': instances[-1].get('end'), 'peak_confidence': peak.get('confidence'),
                                     'peak_frame': peak.get('frame')})
        return json.dumps({'object_track_counts': counts.most_common(), 'observations': observations})
    if 'contentmoderation' in name and isinstance(data, dict):
        flagged = [r.get('FrameIndex') for r in data.get('Results', [])
                   if any(r.get('Adult', {}).get(k) for k in ['isAdultContent', 'isRacyContent', 'isGoryContent'])]
        return json.dumps({'flagged_frames': flagged, 'other_findings': data.get('TextualContentModeration', [])})
    if 'emotions' in name:
        snippets = [{'text': entry['Text'], 'time_range': entry.get('TimeRange')} for entry in data
                    if isinstance(entry, dict) and isinstance(entry.get('Text'), str)] if isinstance(data, list) else []
        return json.dumps({'transcript_snippets': snippets,
                           'note': 'Emotion predictions excluded; snippets are spoken text, not proof of depicted activity.'}, ensure_ascii=False)
    return json.dumps(data, ensure_ascii=False)


def resolve_technical_facts(evidence):
    if evidence.quality:
        evidence.technical_facts.append({'field': 'Quality', 'value': evidence.quality[0],
                                        'source': evidence.quality[1], 'priority': 1})
    for field_name, attribute in [('Length', 'duration'), ('Quality', 'quality')]:
        candidates = [fact for fact in evidence.technical_facts if fact['field'] == field_name]
        if not candidates:
            continue
        priority = max(fact['priority'] for fact in candidates)
        strongest = [fact for fact in candidates if fact['priority'] == priority]
        if len({fact['value'] for fact in strongest}) > 1:
            setattr(evidence, attribute, None)
            evidence.warnings.append(f'{field_name}: conflicting explicit video properties; left unknown.')
        else:
            best = strongest[0]
            setattr(evidence, attribute, (best['value'], best['source']))


def evidence_chunks(evidence):
    # Every segment carries its original identifier, including continuations.
    segments = []
    for source, text in evidence.texts:
        for offset in range(0, len(text), CHUNK_CHARS):
            segments.append({'source': source, 'text': text[offset:offset + CHUNK_CHARS]})
    return segments


class AzureExtractor:
    def __init__(self):
        from openai import AzureOpenAI
        names = ['AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_DEPLOYMENT']
        missing = [name for name in names if not os.getenv(name)]
        if missing:
            raise ValueError('Configure ' + ', '.join(missing) + ' in .env before processing.')
        self.deployment = os.environ['AZURE_OPENAI_DEPLOYMENT'].strip()
        endpoint = os.environ['AZURE_OPENAI_ENDPOINT'].strip().rstrip('/')
        url = urlsplit(endpoint)
        if url.scheme != 'https' or not url.hostname or url.path not in ('', '/') or url.query:
            raise ValueError('AZURE_OPENAI_ENDPOINT must be the HTTPS resource root, '
                             'for example https://YOUR-RESOURCE.openai.azure.com/ (no API path or query).')
        version = os.getenv('AZURE_OPENAI_API_VERSION', '2024-10-21').strip()
        # This date is the GA release, not a preview release.
        if version == '2024-10-21-preview':
            version = '2024-10-21'
        self.client = AzureOpenAI(azure_endpoint=endpoint,
                                 api_key=os.environ['AZURE_OPENAI_API_KEY'].strip(),
                                 api_version=version,
                                 timeout=90, max_retries=2)

    def check_connection(self):
        self.client.chat.completions.create(
            model=self.deployment, messages=[{'role': 'user', 'content': 'Reply OK.'}],
            max_completion_tokens=16)

    def call(self, template, payload, images=()):
        content = [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}]
        for source, encoded in images:
            content += [{'type': 'text', 'text': f'Thumbnail source: {source}'},
                        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + encoded, 'detail': 'high'}}]
        completion = self.client.chat.completions.parse(
            model=self.deployment,
            messages=[{'role': 'system', 'content': PROMPT.read_text(encoding='utf-8')},
                      {'role': 'user', 'content': content}],
            response_format=response_model(template))
        message = completion.choices[0].message
        if message.refusal or message.parsed is None:
            raise ValueError('Azure returned a refusal or no structured metadata.')
        return message.parsed.model_dump()

    def extract(self, template, evidence):
        specs = [vars(spec) for spec in template]
        filename = {'source': 'archive_name', 'text': next(
            (text for source, text in evidence.texts if source == 'archive_name'), '')}
        chunks = evidence_chunks(evidence)
        opening_images = [entry for entry in evidence.image_timing
                          if 0 <= entry['seconds'] <= OPENING_SECONDS]
        if len(chunks) > 64:
            raise ValueError('Evidence exceeds 64 text chunks; reduce the archive contents.')
        visual_candidates = []
        reviewed_sources = []
        evidence.image_review.update(reviewed_images=0, completed_batches=0)
        if len(evidence.images) > IMAGE_BATCH_SIZE:
            for start in range(0, len(evidence.images), IMAGE_BATCH_SIZE):
                batch = evidence.images[start:start + IMAGE_BATCH_SIZE]
                candidate = self.call(template, {
                    'mode': 'visual_review', 'template': specs,
                    'reference_examples': evidence.reference_examples,
                    'product_hierarchy': PRODUCT_PARENTS,
                    'opening_text': evidence.opening_text,
                    'opening_images': opening_images,
                    'current_video_filename': filename,
                    'image_ordering': 'Archive order only; do not infer image timestamps.',
                    'batch_number': start // IMAGE_BATCH_SIZE + 1}, batch)
                visual_candidates.append(candidate)
                reviewed_sources.extend(source for source, _ in batch)
                evidence.image_review.update(reviewed_images=len(reviewed_sources),
                                             completed_batches=len(visual_candidates))
        if sum(len(c['text']) for c in chunks) > CHUNK_CHARS:
            candidates = [self.call(template, {'mode': 'chunk', 'template': specs, 'evidence': [chunk],
                                               'reference_examples': evidence.reference_examples,
                                               'current_video_filename': filename,
                                               'product_hierarchy': PRODUCT_PARENTS})
                          for chunk in chunks]
            payload = {'mode': 'final', 'template': specs, 'candidates': candidates,
                       'evidence': [filename],
                       'original_supporting_excerpts': supporting_excerpts(evidence, candidates + visual_candidates)}
        else:
            payload = {'mode': 'final', 'template': specs, 'evidence': chunks}
        payload['known_duration_seconds'] = evidence.duration
        product_choices = next((spec.choices for spec in template if spec.name == 'Product'), [])
        mentions = product_mentions(evidence.texts, product_choices)
        mention_batches = product_mention_batches(mentions)
        payload['product_mentions'] = mentions if len(mention_batches) <= 1 else []
        payload['product_review_candidates'] = []
        if len(mention_batches) > 1:
            for number, batch in enumerate(mention_batches, 1):
                candidate = self.call(template, {
                    'mode': 'product_review', 'template': specs,
                    'product_mentions': batch, 'product_hierarchy': PRODUCT_PARENTS,
                    'current_video_filename': filename, 'opening_text': evidence.opening_text,
                    'batch_number': number})
                payload['product_review_candidates'].append({'Product': candidate['Product']})
        payload['product_evidence_coverage'] = {
            'text_sources_scanned': len(evidence.texts), 'mentions_found': len(mentions),
            'mention_batches': len(mention_batches), 'images_for_review': len(evidence.images)}
        payload['explicit_video_properties'] = evidence.technical_facts
        payload['explicit_source_quality'] = evidence.quality
        payload['reference_examples'] = evidence.reference_examples
        payload['product_hierarchy'] = PRODUCT_PARENTS
        payload['current_video_filename'] = filename
        payload['opening_text'] = evidence.opening_text
        payload['opening_images'] = opening_images
        payload['credits_policy'] = {
            'content': 'contributor names only; exclude titles, grades, and scene descriptions',
            'opening_seconds': OPENING_SECONDS,
            'precedence': ['readable opening contributor names', 'unambiguous archive filename contributor names']}
        payload['visual_candidates'] = visual_candidates
        payload['required_field_checklist'] = [spec.name for spec in template]
        # Explicit attributes must survive even when a chunk model misses them.
        payload['original_json_attributes'] = evidence.json_details
        payload['source_inventory'] = evidence.inventory
        payload['image_ordering'] = 'Archive order only; UUID filenames do not establish timestamps.'
        # Cross-check explicitly timed opening images directly, including opening
        # cards stored late in archive order. Other batches retain their sources.
        opening_sources = {entry['source'] for entry in opening_images}
        final_images = sorted(evidence.images, key=lambda image: image[0] not in opening_sources)
        result = self.call(template, payload, final_images[:IMAGE_BATCH_SIZE])
        if not visual_candidates:
            reviewed_sources = [source for source, _ in evidence.images]
            evidence.image_review.update(reviewed_images=len(reviewed_sources),
                                         completed_batches=1 if reviewed_sources else 0)
        evidence.image_review['reviewed_sources'] = reviewed_sources
        evidence.product_review = dict(payload['product_evidence_coverage'],
                                       images_reviewed=len(reviewed_sources))
        for entry in evidence.inventory:
            if entry['status'] == 'prepared_json':
                entry['status'] = 'reviewed_json'
            elif entry['status'] == 'prepared_image' and entry['source'] in reviewed_sources:
                entry['status'] = 'reviewed_image'
        return result


def validate_metadata(template, raw, evidence):
    parsed = response_model(template).model_validate(raw).model_dump()
    sources = {source for source, _ in evidence.texts + evidence.images}
    warnings = list(evidence.warnings)
    for spec in template:
        item = parsed[spec.name]
        if spec.name == 'Product':
            item['value'] = normalize_products(item['value'], spec.choices)
        elif spec.multiple:
            item['value'] = list(dict.fromkeys(item['value']))
        valid = all(s in sources or (spec.name == 'Restrictions' and s == 'metadata_policy') for s in item['sources'])
        if item['value'] and (not item['sources'] or not item['explanation'].strip() or not valid):
            warnings.append(f'{spec.name}: unsupported value cleared (missing or invalid evidence).')
            item.update(value=[] if spec.multiple else None, sources=[], explanation='Insufficient evidence.')
        if spec.name in {'Creation Date', 'Last Modified'} and item['value']:
            try:
                if date.fromisoformat(item['value']).isoformat() != item['value']:
                    raise ValueError('Not ISO date')
            except ValueError:
                item.update(value=None, sources=[], explanation='Invalid date.')
                warnings.append(f'{spec.name}: invalid date cleared.')
        if spec.name == 'State' and item['value'] and item['value'] not in STATES:
            item.update(value=None, sources=[], explanation='No supported US state.')
            warnings.append('State: invalid US state cleared.')
    grade_spec = next((spec for spec in template if spec.name == 'Grade'), None)
    if grade_spec:
        filename = next((text for source, text in evidence.texts if source == 'archive_name'), '')
        grades = filename_grades(filename, grade_spec.choices)
        if grades:
            item = parsed['Grade']
            item['value'] = list(dict.fromkeys(item['value'] + grades))
            item['sources'] = list(dict.fromkeys(item['sources'] + ['archive_name']))
            item['explanation'] = (item['explanation'] + ' Explicit current-video filename grade labels.').strip()
    for name, fact in [('Length', evidence.duration), ('Quality', evidence.quality)]:
        if name not in parsed:
            continue
        if fact:
            value, source = fact
            if name == 'Length':
                seconds = int(value)
                value = f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}'
            parsed[name] = {'value': value, 'sources': [source], 'explanation': 'Explicit source duration or resolution.'}
        else:
            parsed[name] = {'value': None, 'sources': [], 'explanation': 'No explicit technical evidence found.'}
    if 'Restrictions' in parsed:
        restricted = 'Assessment' in parsed.get('Content Tags', {}).get('value', []) or any(
            'Restrictions Apply' in tag for tag in parsed.get('Content Tags', {}).get('value', []))
        if restricted or parsed['Restrictions']['value'] is None:
            parsed['Restrictions'] = {
                'value': 'Yes—Internal Only' if restricted else 'No',
                'sources': list(dict.fromkeys(['metadata_policy'] + (
                    parsed['Content Tags']['sources'] if restricted else []))),
                'explanation': 'User metadata policy: assessment material is internal-only; otherwise default No '
                               'unless explicit source restrictions say Yes.'}
    unknown = [name for name, item in parsed.items() if not item['value']]
    return {'metadata': {name: item['value'] for name, item in parsed.items()},
            'field_evidence': parsed, 'unknown_fields': unknown, 'warnings': warnings,
            'opening_text': evidence.opening_text, 'image_review': dict(evidence.image_review),
            'product_review': dict(evidence.product_review),
            'archive_coverage': dict(Counter(entry['status'] for entry in evidence.inventory))}


def scan_archives(folder, output_folder=None):
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError('Input folder does not exist.')
    output = Path(output_folder).resolve() if output_folder else None
    return sorted(p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() == '.zip'
                  and not any(part.startswith('.test-tmp-') for part in p.relative_to(folder).parts)
                  and (output is None or not p.is_relative_to(output)))


def run_batch(paths, template, extractor, progress=lambda *_: None, examples_folder=None):
    examples, example_warnings = load_examples(template, examples_folder)
    results, statuses = [], []
    for index, path in enumerate(paths):
        path = path if isinstance(path, UploadedArchive) else Path(path).resolve()
        source_archive = f'uploaded:{path.name}' if isinstance(path, UploadedArchive) else str(path)
        progress(index, len(paths), path.name)
        try:
            evidence = prepare_archive(path)
            evidence.reference_examples = select_examples(examples, evidence)
            evidence.warnings.extend(example_warnings)
            result = validate_metadata(template, extractor.extract(template, evidence), evidence)
            result['reference_examples'] = [example['filename'] for example in evidence.reference_examples]
            result['source_archive'] = source_archive
            results.append(result)
            statuses.append({'source_archive': source_archive, 'status': 'success', 'warnings': result['warnings']})
        except Exception as exc:
            # Do not persist SDK exception messages: they may contain endpoint/config details.
            message = describe_error(exc)
            statuses.append({'source_archive': source_archive, 'status': 'failed', 'error': message})
    progress(len(paths), len(paths), 'Complete')
    return build_exports(template, results, statuses), results, statuses


def build_exports(template, results, statuses):
    """Prepare downloads in memory; never create or write result files."""
    handle = io.StringIO(newline='')
    writer = csv.DictWriter(handle, fieldnames=[s.name for s in template])
    writer.writeheader()
    for result in results:
        row = {k: format_metadata_value(v) for k, v in result['metadata'].items()}
        row = {k: "'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')) else v
               for k, v in row.items()}
        writer.writerow(row)
    return {'metadata.csv': handle.getvalue().encode('utf-8-sig'),
            'metadata.json': json.dumps([result['metadata'] for result in results], indent=2, ensure_ascii=False).encode('utf-8'),
            'batch_status.json': json.dumps(statuses, indent=2, ensure_ascii=False).encode('utf-8')}


def format_metadata_value(value):
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False) if any(isinstance(item, dict) for item in value) else '; '.join(value)
    return value


def describe_error(exc):
    """Actionable errors without exposing credentials, URLs, or request bodies."""
    from openai import APIConnectionError, APIStatusError
    if isinstance(exc, APIConnectionError):
        return ('Cannot connect to Azure OpenAI. Check network access, proxy settings, '
                'and the resource endpoint; private endpoints require the appropriate network/VPN.')
    if isinstance(exc, APIStatusError):
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get('error', body)
        code = error.get('code', '') if isinstance(error, dict) else ''
        if exc.status_code == 404:
            if code == 'DeploymentNotFound':
                return ('Azure deployment not found (404). Set AZURE_OPENAI_DEPLOYMENT to the exact '
                        'deployment name in Azure, and ensure AZURE_OPENAI_ENDPOINT belongs to that resource.')
            return ('Azure resource/API route not found (404). Check the resource endpoint, deployment name, '
                    'and API version in .env. Use 2024-10-21, not 2024-10-21-preview.')
        if exc.status_code in (401, 403):
            return ('Azure access denied. Check that the API key belongs to the configured resource '
                    'and that its network access rules allow this machine.')
        if exc.status_code == 400:
            return ('Azure rejected the request (400). Ensure the deployment supports chat completions, '
                    'image inputs, and structured outputs with the configured API version.')
        if exc.status_code == 429:
            return 'Azure rate limit or quota exceeded. Retry later or check deployment quota.'
        return f'Azure API request failed (HTTP {exc.status_code}).'
    return str(exc) if isinstance(exc, (ValueError, zipfile.BadZipFile, ArchiveLimitError)) else type(exc).__name__
