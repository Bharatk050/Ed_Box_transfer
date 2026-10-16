"""Load curated desired outputs and choose a bounded, relevant subset."""
import json
import re
from datetime import date
from pathlib import Path
from product_taxonomy import normalize_products

EXAMPLES_DIR = Path(__file__).resolve().parent / 'examples'
MAX_EXAMPLE_BYTES = 128 * 1024
EXAMPLE_CHAR_BUDGET = 12000
MAX_EXAMPLES = 5
STOPWORDS = set('a an the and or of to in for is are with this that video content metadata'.split())


def parse_commented_json(text):
    """Accept comments and trailing commas without altering quoted strings."""
    pattern = r'("(?:\\.|[^"\\])*")|(//[^\r\n]*|/\*[\s\S]*?\*/)|(,\s*(?=[}\]]))'
    def replace(match):
        if match.group(1):
            return match.group(1)
        if match.group(2):
            return '\n' * match.group(2).count('\n') + ' '
        return match.group(0)
    clean = re.sub(pattern, replace, text)
    # Comments may separate a comma from its closing bracket.
    clean = re.sub(pattern, lambda m: m.group(1) or ('' if m.group(3) else ' '), clean)
    return json.loads(clean)


def load_examples(template, folder=None):
    folder = Path(folder) if folder is not None else EXAMPLES_DIR
    if not folder.exists():
        return [], []
    examples, warnings = [], []
    fields = {spec.name: spec for spec in template}
    try:
        files = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == '.json'),
                       key=lambda p: p.name)
    except OSError:
        return [], ['Reference examples folder could not be read. Continuing without examples.']
    for path in files:
        try:
            if path.stat().st_size > MAX_EXAMPLE_BYTES:
                raise ValueError('file exceeds 128 KiB')
            document = parse_commented_json(path.read_text(encoding='utf-8-sig'))
            # Individual metadata downloads use a one-record array. Accept that
            # form directly so users can reuse a corrected download as an example.
            if isinstance(document, list) and len(document) == 1:
                document = document[0]
            if not isinstance(document, dict):
                raise ValueError('expected one JSON object or a one-record array per file')
            if 'metadata' in document:
                if set(document) - {'metadata', 'description'}:
                    raise ValueError('wrapped examples allow only description and metadata')
                metadata, description = document['metadata'], document.get('description', '')
            else:
                metadata, description = document, ''
            if not isinstance(description, str) or not isinstance(metadata, dict) or not metadata:
                raise ValueError('metadata must be a nonempty object and description must be text')
            normalized = {}
            for name, value in metadata.items():
                if name not in fields:
                    raise ValueError(f'unknown metadata field: {name}')
                spec = fields[name]
                if name == 'Product':
                    normalized[name] = normalize_products(value, spec.choices)
                    continue
                if spec.multiple:
                    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                        raise ValueError(f'{name} must be an array of strings')
                    value = list(dict.fromkeys(value))
                    values = value
                else:
                    if value is not None and not isinstance(value, str):
                        raise ValueError(f'{name} must be a string or null')
                    value = value if value else None
                    values = [] if value is None else [value]
                if spec.choices and any(v not in spec.choices for v in values):
                    raise ValueError(f'{name} contains a choice outside the template')
                if name in {'Creation Date', 'Last Modified'} and value:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError(f'{name} must be YYYY-MM-DD')
                if name == 'Length' and value and not re.fullmatch(r'\d{2,}:[0-5]\d:[0-5]\d', value):
                    raise ValueError('Length must be hh:mm:ss')
                if name == 'State' and value and value not in STATE_NAMES:
                    raise ValueError('State must be a full US state name')
                normalized[name] = value
            example = {'filename': path.name, 'description': description, 'metadata': normalized}
            if len(json.dumps(example, ensure_ascii=False)) + 2 > EXAMPLE_CHAR_BUDGET:
                raise ValueError('example exceeds the 12,000-character request budget')
            examples.append(example)
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, (json.JSONDecodeError, UnicodeError)) else 'invalid or unreadable JSON'
            warnings.append(f'Reference example skipped: {path.name} ({reason}).')
    return examples, warnings


def tokens(text):
    return set(re.findall(r'\w+', text.lower())) - STOPWORDS


def select_examples(examples, evidence):
    current = tokens('\n'.join(text for _, text in evidence.texts).replace('_', ' '))
    def relevance(example):
        text = example['filename'] + ' ' + example['description'] + ' ' + json.dumps(list(example['metadata'].values()))
        return len(current & tokens(text.replace('_', ' ')))
    ranked = sorted(examples, key=lambda example: (-relevance(example), example['filename']))
    selected = []
    for example in ranked:
        if len(selected) >= MAX_EXAMPLES:
            break
        if len(json.dumps(selected + [example], ensure_ascii=False)) <= EXAMPLE_CHAR_BUDGET:
            selected.append(example)
    return selected


STATE_NAMES = set(('Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|'
                   'Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|'
                   'Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|'
                   'New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|'
                   'South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|'
                   'Wisconsin|Wyoming|District of Columbia').split('|'))
