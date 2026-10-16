"""Preserve contextual JSON facts and explicit source-video technical properties."""
import json
import math
import re


def key_name(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def duration_seconds(value, milliseconds=False):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = value / 1000 if milliseconds else value
        return number if math.isfinite(number) and number >= 0 else None
    if isinstance(value, str) and re.fullmatch(r'\d+:[0-5]\d:[0-5]\d(?:\.\d+)?', value):
        hours, minutes, seconds = value.split(':')
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return None


def collect_image_timing(source, data):
    """Read explicit image/time associations, never archive order or UUIDs."""
    timings = []

    def walk(node, path='$', fps=None):
        if isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f'{path}[{index}]', fps)
        elif isinstance(node, dict):
            keys = {key_name(key): value for key, value in node.items()}
            rate = keys.get('fps', keys.get('framerate', fps))
            names = [keys[key] for key in ('image', 'imagefile', 'imagefilename', 'imagepath', 'filename', 'file',
                     'filepath', 'thumbnail', 'thumbnailpath', 'thumbnailfilename')
                     if isinstance(keys.get(key), str) and
                     re.search(r'\.(?:jpg|jpeg|png)$', keys[key], re.IGNORECASE)]
            times = []
            for key in ('timestampseconds', 'timeseconds', 'seconds', 'offsetseconds',
                        'timestampmilliseconds', 'offsetmilliseconds'):
                if key in keys:
                    seconds = duration_seconds(keys[key], milliseconds='milliseconds' in key)
                    if seconds is not None:
                        times.append(seconds)
            for key in ('timestamp', 'time', 'start'):
                if isinstance(keys.get(key), str):
                    seconds = duration_seconds(keys[key])
                    if seconds is not None:
                        times.append(seconds)
            frame = keys.get('frameindex')
            if (isinstance(frame, (int, float)) and not isinstance(frame, bool) and frame >= 0
                    and isinstance(rate, (int, float)) and not isinstance(rate, bool)
                    and math.isfinite(rate) and rate > 0):
                seconds = duration_seconds(frame / rate)
                if seconds is not None:
                    times.append(seconds)
            for name in names:
                for seconds in times:
                    timings.append({'image': name, 'seconds': seconds,
                                    'timing_source': source, 'path': path})
            for key, child in node.items():
                walk(child, f'{path}.{key}', rate)
    walk(data)
    return timings


def resolve_image_timing(timings, image_sources):
    """Resolve relative paths or unique basenames to actual reviewed images."""
    resolved, warnings = {}, []
    for entry in timings:
        name = entry['image'].replace('\\', '/')
        source = entry['timing_source'].replace('\\', '/')
        sibling = source.rsplit('/', 1)[0] + '/' + name if '/' in source else name
        matches = [s for s in image_sources if s in {name, sibling}]
        if not matches:
            matches = [s for s in image_sources if s.rsplit('/', 1)[-1] == name.rsplit('/', 1)[-1]]
        if len(matches) != 1:
            warnings.append(f'Image timing could not be linked unambiguously: {name}')
            continue
        resolved.setdefault(matches[0], []).append(entry)
    result = []
    for source, entries in resolved.items():
        seconds = {round(e['seconds'], 6) for e in entries}
        if len(seconds) != 1:
            warnings.append(f'Conflicting image timestamps ignored: {source}')
            continue
        result.append({'source': source, 'seconds': entries[0]['seconds'],
                       'timing_sources': list(dict.fromkeys(e['timing_source'] for e in entries))})
    return result, list(dict.fromkeys(warnings))


def collect_json_details(source, data):
    """Inspect scalar metadata, avoiding image/frame dimensions and model scores."""
    facts, technical = [], []
    metadata_keys = {
        'contentowner', 'owner', 'credits', 'creator', 'creatorvendor', 'vendor',
        'creationdate', 'createddate', 'videocreationdate', 'lastmodified',
        'videolastmodified', 'modifieddate', 'product', 'primary', 'secondary',
        'grade', 'school', 'schoolname', 'regionschool', 'region', 'state',
        'restrictions', 'footagetype', 'title', 'description', 'copyright',
        'license', 'licence', 'usagerights', 'permission', 'permissions',
        'role', 'speakerrole', 'speakerroles', 'jobtitle',
    }
    video_containers = {'video', 'videos', 'sourcevideo', 'videometadata', 'media', 'mediainfo', 'sourcemedia'}

    def walk(node, path='$', video_context=False):
        if isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f'{path}[{index}]', video_context)
        elif isinstance(node, dict):
            keys = {key_name(key): value for key, value in node.items()}
            width = keys.get('videowidth', keys.get('width') if video_context else None)
            height = keys.get('videoheight', keys.get('height') if video_context else None)
            if isinstance(width, (int, float)) and isinstance(height, (int, float)):
                quality = {720: '720', 1080: '1080', 2160: '4K'}.get(height)
                if width > 0 and quality:
                    technical.append({'field': 'Quality', 'value': quality, 'source': source,
                                      'path': path, 'priority': 3})
                    facts.append({'source': source, 'path': path, 'value': {'video_width': width, 'video_height': height}})
            for key, child in node.items():
                normalized = key_name(key)
                location = f'{path}.{key}'
                is_video = video_context or normalized in video_containers
                if normalized in metadata_keys:
                    facts.append({'source': source, 'path': location, 'value': child})
                explicit_duration = normalized in {'videoduration', 'videodurationseconds',
                                                    'sourceduration', 'sourcedurationseconds',
                                                    'videodurationmilliseconds'}
                if explicit_duration or (video_context and normalized in {'duration', 'durationseconds', 'durationmilliseconds'}):
                    value = duration_seconds(child, 'milliseconds' in normalized)
                    if value is not None:
                        technical.append({'field': 'Length', 'value': value, 'source': source,
                                          'path': location, 'priority': 3})
                        facts.append({'source': source, 'path': location, 'value': child})
                # A frame/page/thumbnail isn't a source video, even if nested below media.
                if normalized in {'frames', 'keyframes', 'pages', 'thumbnails', 'images', 'instances',
                                  'words', 'boundingbox', 'ocr', 'shots', 'scenes', 'detections', 'faces'}:
                    is_video = False
                walk(child, location, is_video)
    walk(data)
    return facts, technical


def supporting_excerpts(evidence, candidates, budget=18000):
    """Keep original snippets backing candidates, not just model summaries."""
    sources = dict(evidence.texts)
    excerpts, seen = [], set()
    for candidate in candidates:
        for name, item in candidate.items():
            if not item.get('value'):
                continue
            value = item['value']
            values = value if isinstance(value, list) else [value]
            needles = []
            for part in values:
                if isinstance(part, dict):
                    needles.extend(str(v) for v in part.values() if v)
                else:
                    needles.append(str(part))
            for source in item.get('sources', []):
                text = sources.get(source)
                if text is None:
                    continue
                positions = [text.lower().find(needle.lower()) for needle in needles if needle]
                matches = [pos for pos in positions if pos >= 0]
                if matches:
                    windows = [(max(0, pos - 350), pos + 650) for pos in matches]
                elif len(text) <= 1200:
                    windows = [(0, len(text))]
                else:
                    # Never attach an unrelated beginning as proof of an inference.
                    continue
                for start, end in windows:
                    key = (source, start, end)
                    if key in seen:
                        continue
                    snippet = {'source': source, 'field': name, 'offset': start, 'text': text[start:end]}
                    if len(json.dumps(excerpts + [snippet], ensure_ascii=False)) <= budget:
                        excerpts.append(snippet)
                        seen.add(key)
    return excerpts
